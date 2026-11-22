"""Evaluate a local first-attempt quiz baseline on a later course cohort."""

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import re
import sys

import duckdb

from analytics.import_snapshot import SnapshotValidationError, _validate_database_target


PRIOR_GRADES = """
SELECT a.instance_id, i.grade_item_id, i.min_grade, i.max_grade
FROM activities AS a
JOIN grade_items AS i USING (snapshot_id, course_module_id)
WHERE a.snapshot_id = ? AND a.module = 'quiz' AND a.instance_id = ANY(?)
  AND i.item_type = 'mod' AND i.item_number = 0
  AND i.grade_type = 1 AND NOT i.needs_update AND i.max_grade > i.min_grade
"""


def require(condition, message):
    if not condition:
        raise SnapshotValidationError(message)


def positive_int(value):
    return type(value) is int and 0 < value < 2**63


def numeric(value):
    return type(value) in (int, float) and -sys.float_info.max <= value <= sys.float_info.max


def reject_json_constant(_):
    raise ValueError("Non-standard JSON constant.")


def read_json(path, label):
    require(path.is_file() and not path.is_symlink(), f"Expected a local {label} file.")
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_json_constant)
    except (ValueError, OSError, RecursionError) as error:
        raise SnapshotValidationError(f"Could not read {label} JSON.") from error


def read_cohort(db, spec):
    require(isinstance(spec, dict), "Expected a cohort configuration.")
    snapshot = spec.get("snapshot_id")
    cutoff = spec.get("cutoff")
    target = spec.get("target_quiz_id")
    priors = spec.get("prior_quiz_ids")
    require(isinstance(snapshot, str) and re.fullmatch(r"[0-9a-f]{64}", snapshot), "Invalid snapshot ID.")
    require(positive_int(cutoff) and positive_int(target), "Expected a positive cutoff and target quiz ID.")
    require(isinstance(priors, list) and priors and all(map(positive_int, priors))
            and len(set(priors)) == len(priors) and target not in priors,
            "Expected distinct prerequisite quiz IDs excluding the target.")
    found = db.execute("SELECT course_id, observed_at FROM snapshots WHERE snapshot_id = ?", [snapshot]).fetchone()
    require(found is not None, "Snapshot not found.")
    course, observed = found
    require(observed < cutoff, "Feature snapshot must precede its cutoff.")
    activities = db.execute("SELECT instance_id FROM activities WHERE snapshot_id = ? AND module = 'quiz'",
                            [snapshot]).fetchall()
    require({target, *priors}.issubset({row[0] for row in activities}), "Configured quiz is not in the feature snapshot.")
    ids = {row[0] for row in db.execute("SELECT learner_id FROM learners WHERE snapshot_id = ?", [snapshot]).fetchall()}
    items = db.execute(PRIOR_GRADES, [snapshot, priors]).fetchall()
    require(len({row[0] for row in items}) == len(items), "A prerequisite quiz has ambiguous grade items.")
    ranges = {item: (low, high) for _, item, low, high in items}
    history = {learner: [] for learner in ids}
    for learner, item, grade in db.execute(
        "SELECT learner_id, grade_item_id, final_grade FROM grades WHERE snapshot_id = ? AND grade_item_id = ANY(?)",
        [snapshot, list(ranges)],
    ).fetchall():
        low, high = ranges[item]
        if grade is not None and low <= grade <= high:
            history[learner].append((grade - low) / (high - low))
    features = {learner: (sum(grades) / len(grades) - 0.5 if grades else 0.0, len(grades) / len(priors))
                for learner, grades in history.items()}
    return course, cutoff, target, features


def first_attempt_label(attempts, cutoff, observed, pass_percent):
    require(isinstance(attempts, list) and all(isinstance(row, dict) and positive_int(row.get("attempt_number"))
                                              for row in attempts), "Invalid quiz attempt list.")
    first = [attempt for attempt in attempts if attempt["attempt_number"] == 1]
    require(len(first) <= 1, "Multiple first attempts in a quiz export.")
    if not first:
        return None, "no_first_attempt"
    attempt = first[0]
    started = attempt.get("started_at")
    require(positive_int(started) and started <= observed, "Invalid attempt start time.")
    require(started >= cutoff, "A target first attempt started before its cutoff.")
    state = attempt.get("state")
    require(state in ("finished", "inprogress", "overdue", "abandoned"), "Invalid quiz attempt state.")
    if state != "finished":
        return None, "unfinished"
    submitted = attempt.get("submitted_at")
    require(positive_int(submitted) and started <= submitted <= observed, "Invalid first-attempt submission time.")
    require("mark" in attempt and "max_mark" in attempt, "Missing first-attempt score fields.")
    mark, maximum = attempt["mark"], attempt["max_mark"]
    require(numeric(maximum) and maximum >= 0, "Invalid first-attempt maximum mark.")
    if mark is None:
        return None, "ungraded"
    require(numeric(mark), "Invalid first-attempt mark.")
    if maximum == 0:
        return None, "unscored"
    return int(mark >= maximum * (pass_percent / 100)), None


def load_examples(path, cohort, pass_percent, before=None):
    course, cutoff, target, features = cohort
    data = read_json(path, "quiz outcomes")
    require(isinstance(data, list), "Expected an array of quiz export responses.")
    seen = set()
    examples = []
    missing = Counter()
    for row in data:
        require(isinstance(row, dict) and type(row.get("schema_version")) is int and row["schema_version"] == 1,
                "Invalid quiz export schema.")
        learner, observed = row.get("learner_id"), row.get("observed_at")
        require(all(positive_int(row.get(key)) for key in ("course_id", "quiz_id", "learner_id", "observed_at"))
                and row["course_id"] == course and row["quiz_id"] == target and learner in features and learner not in seen,
                "Quiz export is duplicated or outside its configured cohort.")
        require(observed >= cutoff, "Quiz outcome must be observed at or after its cutoff.")
        require(before is None or observed < before, "Training outcome must be observed before the test cutoff.")
        seen.add(learner)
        label, reason = first_attempt_label(row.get("attempts"), cutoff, observed, pass_percent)
        if reason:
            missing[reason] += 1
        else:
            examples.append((features[learner], label))
    if len(seen) < len(features):
        missing["missing_export"] = len(features) - len(seen)
    return examples, {"cohort_size": len(features), "labeled": len(examples),
                      "passed": sum(y for _, y in examples), "unlabeled": dict(sorted(missing.items()))}


def fit_logistic(examples):
    # Features are bounded; fixed-step regularized gradient descent is deterministic.
    weights = [0.0, 0.0, 0.0]
    for _ in range(600):
        bias, mean, coverage = weights
        grad_bias, grad_mean, grad_coverage = 0.0, 0.0, 0.0
        for (score, fraction), label in examples:
            error = sigmoid(bias + mean * score + coverage * fraction) - label
            grad_bias += error
            grad_mean += error * score
            grad_coverage += error * fraction
        count = len(examples)
        weights = [bias - (grad_bias / count), mean - (grad_mean / count + 0.01 * mean),
                   coverage - (grad_coverage / count + 0.01 * coverage)]
    return weights


def sigmoid(logit):
    return 1 / (1 + math.exp(-logit))


def metrics(outcomes):
    if not outcomes:
        return {"brier": None, "accuracy_at_half": None}
    return {"brier": sum((probability - label) ** 2 for probability, label in outcomes) / len(outcomes),
            "accuracy_at_half": sum((probability >= 0.5) == label for probability, label in outcomes) / len(outcomes)}


def outcome_path(manifest, relative):
    path = Path(relative)
    require(not path.is_absolute() and ".." not in path.parts, "Outcome paths must stay relative to the manifest.")
    current = manifest.parent
    for component in path.parts:
        current /= component
        require(not current.is_symlink(), "Outcome paths must not traverse symbolic links.")
    return current


def evaluate(db, manifest_path):
    manifest = read_json(manifest_path, "experiment manifest")
    require(isinstance(manifest, dict), "Expected an experiment configuration.")
    threshold = manifest.get("pass_percent")
    require(numeric(threshold) and 0 < threshold <= 100, "Pass percentage must be greater than 0 and at most 100.")
    train_spec, test_spec = manifest.get("train"), manifest.get("test")
    train = read_cohort(db, train_spec)
    test = read_cohort(db, test_spec)
    require(train[0] != test[0] and train[1] < test[1], "Expected different courses and strictly ordered cutoffs.")
    require(train[3].keys().isdisjoint(test[3]), "Training and test learners must be disjoint.")
    require(len(train_spec["prior_quiz_ids"]) == len(test_spec["prior_quiz_ids"]),
            "Both cohorts must define the same number of prerequisite quizzes.")
    paths = [spec.get("labels") for spec in (train_spec, test_spec)]
    require(all(isinstance(path, str) and path for path in paths), "Expected local quiz outcome file paths.")
    train_data, train_summary = load_examples(outcome_path(manifest_path, paths[0]), train, threshold, before=test[1])
    test_data, test_summary = load_examples(outcome_path(manifest_path, paths[1]), test, threshold)
    require({label for _, label in train_data} == {0, 1}, "Training needs labeled passes and fails.")
    require(test_data, "Evaluation needs at least one labeled first attempt.")
    base = train_summary["passed"] / len(train_data)
    bias, mean, coverage = fit_logistic(train_data)
    by_history = {"with_history": [], "no_history": []}
    totals = []
    for (score, fraction), label in test_data:
        outcome = sigmoid(bias + mean * score + coverage * fraction), label
        totals.append(outcome)
        by_history["with_history" if fraction > 0 else "no_history"].append(outcome)
    return {
        "pass_percent": threshold,
        "train": train_summary,
        "test": test_summary,
        "train_prevalence": base,
        "test_metrics": {"logistic": metrics(totals), "prevalence": metrics([(base, label) for _, label in totals])},
        "test_by_history": {group: {"labeled": len(rows), **metrics(rows)} for group, rows in by_history.items()},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args(argv)
    try:
        path = _validate_database_target(args.database)
        require(path.is_file(), "Database file does not exist.")
        with duckdb.connect(str(path), read_only=True, config={"enable_external_access": "false"}) as db:
            result = evaluate(db, args.manifest)
    except SnapshotValidationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except (duckdb.Error, OSError, ValueError):
        print("ERROR: Could not read the local experiment inputs.", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
