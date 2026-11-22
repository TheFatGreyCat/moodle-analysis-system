import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from analytics.import_snapshot import import_snapshot
from test_import_snapshot import make_baseline_datasets


class PredictionBaselineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.db = self.root / "course.duckdb"
        self.manifest = self.root / "experiment.json"
        self.train = self.cohort("train", 101, 501, 1_700_010_000)
        self.test = self.cohort("test", 102, 601, 1_700_020_000)

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value), encoding="utf-8")

    def cohort(self, name, course, first, cutoff):
        data = make_baseline_datasets(course_id=course, observed_at=cutoff - 1, standard_log_available=False)
        catalog, grades, logs = (data[n] for n in ("course.json", "gradebook.json", "submission-logs.json"))
        catalog["learner_ids"] = list(range(first, first + 7))
        catalog["activities"].append(dict(catalog["activities"][0], instance_id=999, course_module_id=999))
        logs["question_steps"] = []
        grades["grades"] = [dict(grade_item_id=401, learner_id=first + i, final_grade=grade, updated_at=None)
                            for i, grade in enumerate([100, 95, 0, 5, None, 80, 50])]
        grades["items"].append(dict(grades["items"][1], grade_item_id=499, course_module_id=999))
        grades["grades"].extend(dict(g, grade_item_id=499, final_grade=100) for g in grades["grades"][:])
        for filename, payload in data.items():
            self.write(filename, payload)
        snapshot = import_snapshot(self.root, self.db)
        labels = []
        for i, mark in enumerate([8, 10, 0, 1, 3, None]):
            attempt = dict(attempt_id=first + i, attempt_number=1, state="finished", started_at=cutoff,
                           submitted_at=cutoff + 1, mark=mark, max_mark=10, questions=[])
            retry = dict(attempt, attempt_id=first + i + 1000, attempt_number=2, mark=10)
            labels.append(dict(schema_version=1, course_id=course, quiz_id=999, learner_id=first + i,
                               observed_at=cutoff + 2, attempts=[retry, attempt]))
        self.write(f"{name}.json", labels)
        return dict(snapshot_id=snapshot, cutoff=cutoff, target_quiz_id=999, prior_quiz_ids=[301], labels=f"{name}.json")

    def run_cli(self):
        self.write(self.manifest.name, dict(train=self.train, test=self.test, pass_percent=60))
        return subprocess.run([sys.executable, "-m", "analytics.predict_baseline", str(self.db), str(self.manifest)],
                              capture_output=True, text=True, check=False)

    def test_first_attempt_and_unlabeled_coverage_are_preserved(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["train"]["labeled"], 5)
        self.assertEqual(report["test"]["passed"], 2)
        self.assertEqual(report["test"]["cohort_size"], 7)
        self.assertEqual(report["test"]["unlabeled"], {"missing_export": 1, "ungraded": 1})
        self.assertEqual(report["test_by_history"]["no_history"]["labeled"], 1)
        self.assertLess(report["test_metrics"]["logistic"]["brier"], report["test_metrics"]["prevalence"]["brier"])

    def test_rejects_timing_and_target_leakage(self):
        original = self.test.copy()
        for mutation in ({"cutoff": self.test["cutoff"] - 1}, {"prior_quiz_ids": [999]}):
            with self.subTest(mutation=mutation):
                self.test = original | mutation
                result = self.run_cli()
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
        self.test = original
        labels = json.loads((self.root / self.train["labels"]).read_text())
        labels[0]["observed_at"] = self.test["cutoff"]
        self.write(self.train["labels"], labels)
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn("before the test cutoff", result.stderr)

    def test_rejects_overlapping_learners_and_pre_cutoff_attempts(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        labels = json.loads((self.root / self.test["labels"]).read_text())
        labels[0]["attempts"][1]["started_at"] = self.test["cutoff"] - 1
        self.write(self.test["labels"], labels)
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn("started before", result.stderr)
        self.test = self.cohort("overlap", 103, 501, 1_700_030_000)
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn("disjoint", result.stderr)

    def test_rejects_symlinks_non_standard_json_and_path_escape(self):
        real_labels = self.root / self.test["labels"]
        link_labels = self.root / "symlink-test.json"
        link_labels.symlink_to(real_labels)
        original = self.test.copy()
        for mutation in ({"labels": "symlink-test.json"}, {"labels": "../outside.json"}):
            with self.subTest(mutation=mutation):
                self.test = original | mutation
                result = self.run_cli()
                self.assertEqual(result.returncode, 1)
        self.test = original
        real_labels.write_text('[{"schema_version": 1, "extra": NaN}]', encoding="utf-8")
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn("JSON", result.stderr)

