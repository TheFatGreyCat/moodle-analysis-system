"""Report observed submissions and Gradebook pass rates for one local snapshot."""

import argparse
import json
import re
import sys
from pathlib import Path

import duckdb

from analytics.import_snapshot import SnapshotValidationError, _validate_database_target


SUBMISSIONS = r"""
WITH submitters AS (
    SELECT course_module_id, module, count(DISTINCT learner_id) AS total
    FROM (
        SELECT course_module_id, learner_id,
               CASE event_name
                   WHEN '\mod_quiz\event\attempt_submitted' THEN 'quiz'
                   WHEN '\mod_assign\event\assessable_submitted' THEN 'assign'
               END AS module
        FROM events WHERE snapshot_id = ?
    ) WHERE module IS NOT NULL
    GROUP BY course_module_id, module
)
SELECT a.course_module_id, a.module, a.name,
       CASE WHEN ? THEN coalesce(s.total, 0) END AS observed_submitters,
       CASE WHEN ? THEN coalesce(s.total, 0) / nullif(?, 0) END AS observed_submission_share
FROM activities AS a
LEFT JOIN submitters AS s USING (course_module_id, module)
WHERE a.snapshot_id = ? AND a.module IN ('quiz', 'assign')
ORDER BY a.course_module_id
"""

GRADE_RATES = """
WITH totals AS (
    SELECT i.grade_item_id, i.course_module_id, i.name, i.hidden, i.needs_update,
           i.grade_type, i.min_grade, i.max_grade, count(g.final_grade) AS graded_learners,
           count(*) FILTER (WHERE g.final_grade >= i.min_grade + (i.max_grade - i.min_grade) * ? / 100)
               AS passing
    FROM grade_items AS i
    JOIN activities AS a USING (snapshot_id, course_module_id)
    LEFT JOIN grades AS g USING (snapshot_id, grade_item_id)
    WHERE i.snapshot_id = ? AND i.item_type = 'mod' AND a.module IN ('quiz', 'assign')
    GROUP BY ALL
), statuses AS (
    SELECT *, CASE
        WHEN needs_update THEN 'needs_update'
        WHEN grade_type <> 1 OR max_grade <= min_grade THEN 'not_numeric'
        WHEN graded_learners = 0 THEN 'no_grades'
        ELSE 'available' END AS pass_status
    FROM totals
)
SELECT grade_item_id, course_module_id, name, hidden, graded_learners, pass_status,
       CASE WHEN pass_status = 'available' THEN passing END AS passed_learners,
       CASE WHEN pass_status = 'available' THEN passing / nullif(graded_learners, 0) END AS pass_rate
FROM statuses
ORDER BY grade_item_id
"""


def query_rows(connection, sql, parameters):
    result = connection.execute(sql, parameters)
    columns = [column[0] for column in result.description]
    return [dict(zip(columns, row)) for row in result.fetchall()]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--snapshot-id", required=True, help="The 64-character ID printed by the importer")
    parser.add_argument("--pass-percent", required=True, type=float, help="Threshold on the numeric grade range, 0–100")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{64}", args.snapshot_id):
        parser.error("--snapshot-id must be a lowercase SHA-256 ID")
    if not 0 <= args.pass_percent <= 100:
        parser.error("--pass-percent must be between 0 and 100")

    snapshot_id = args.snapshot_id
    try:
        target = _validate_database_target(args.database)
        if not target.is_file():
            raise SnapshotValidationError("Database file does not exist.")
        with duckdb.connect(str(target), read_only=True, config={"enable_external_access": "false"}) as db:
            snapshot = db.execute(
                "SELECT course_id, observed_at, log_from, log_to, standard_log_available FROM snapshots WHERE snapshot_id = ?",
                [snapshot_id],
            ).fetchone()
            if snapshot is None:
                raise SnapshotValidationError("Snapshot not found.")
            course_id, observed_at, log_from, log_to, logs_available = snapshot
            cohort_size = db.execute("SELECT count(*) FROM learners WHERE snapshot_id = ?", [snapshot_id]).fetchone()[0]
            report = {
                "snapshot_id": snapshot_id,
                "course_id": course_id,
                "observed_at": observed_at,
                "cohort_size": cohort_size,
                "log_window": {"from": log_from, "to": log_to, "standard_log_available": logs_available},
                "pass_percent": args.pass_percent,
                "activities": query_rows(db, SUBMISSIONS, [snapshot_id, logs_available, logs_available, cohort_size, snapshot_id]),
                "grade_items": query_rows(db, GRADE_RATES, [args.pass_percent, snapshot_id]),
            }
    except SnapshotValidationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except (duckdb.Error, OSError):
        print("ERROR: Could not read the local snapshot database.", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
