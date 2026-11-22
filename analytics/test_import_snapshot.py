#!/usr/bin/env python3
"""Acceptance tests for the local DuckDB snapshot importer."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

try:
    import duckdb
except ModuleNotFoundError:  # pragma: no cover - local sandbox without DuckDB installed
    duckdb = None


ROOT = Path(__file__).resolve().parent.parent


def make_baseline_datasets(
    *,
    course_id: int = 101,
    observed_at: int = 1700001000,
    log_from: int = 1700000000,
    log_to: int = 1700000900,
    quiz_grade: float | None = 75.0,
    assign_grade: float | None = 0.0,
    standard_log_available: bool = True,
) -> dict[str, dict]:
    course = {
        "schema_version": 1,
        "course_id": course_id,
        "observed_at": observed_at,
        "course": {
            "name": "Synthetic Course Export",
            "short_name": "synth-101",
            "format": "topics",
            "start_at": 1699900000,
            "end_at": None,
        },
        "sections": [
            {"section_id": 10, "number": 0, "name": "General"},
            {"section_id": 11, "number": 1, "name": "Practice"},
        ],
        "activities": [
            {
                "course_module_id": 201,
                "instance_id": 301,
                "section_id": 10,
                "module": "quiz",
                "name": "Prerequisites",
            },
            {
                "course_module_id": 202,
                "instance_id": 302,
                "section_id": 11,
                "module": "assign",
                "name": "Practice submission",
            },
        ],
        "learner_ids": [501, 502],
    }

    gradebook = {
        "schema_version": 1,
        "course_id": course_id,
        "observed_at": observed_at,
        "items": [
            {
                "grade_item_id": 400,
                "course_module_id": None,
                "category_id": None,
                "category_total_for": 900,
                "item_type": "course",
                "item_number": None,
                "name": "Course total",
                "grade_type": 1,
                "min_grade": 0.0,
                "max_grade": 100.0,
                "scale": None,
                "hidden": False,
                "needs_update": False,
            },
            {
                "grade_item_id": 401,
                "course_module_id": 201,
                "category_id": 900,
                "category_total_for": None,
                "item_type": "mod",
                "item_number": 0,
                "name": "Prerequisites",
                "grade_type": 1,
                "min_grade": 0.0,
                "max_grade": 100.0,
                "scale": None,
                "hidden": False,
                "needs_update": False,
            },
            {
                "grade_item_id": 402,
                "course_module_id": 202,
                "category_id": 900,
                "category_total_for": None,
                "item_type": "mod",
                "item_number": 0,
                "name": "Practice submission",
                "grade_type": 1,
                "min_grade": 0.0,
                "max_grade": 100.0,
                "scale": ["Unsatisfactory", "Satisfactory"],
                "hidden": False,
                "needs_update": False,
            },
        ],
        "grades": [
            {
                "grade_item_id": 401,
                "learner_id": 501,
                "final_grade": quiz_grade,
                "updated_at": 1700000500,
            },
            {
                "grade_item_id": 402,
                "learner_id": 501,
                "final_grade": assign_grade,
                "updated_at": 1700000600,
            },
            {
                "grade_item_id": 402,
                "learner_id": 502,
                "final_grade": None,
                "updated_at": None,
            },
        ],
    }

    events = (
        [
            {
                "event_id": 7001,
                "course_module_id": 201,
                "learner_id": 501,
                "event_name": r"\mod_quiz\event\attempt_submitted",
                "occurred_at": log_from,
                "object_table": "quiz_attempts",
                "object_id": 8001,
                "attempt_id": 8001,
            },
            {
                "event_id": 7002,
                "course_module_id": 202,
                "learner_id": 501,
                "event_name": r"\mod_assign\event\submission_graded",
                "occurred_at": log_to - 1,
                "object_table": "assign_grades",
                "object_id": 9001,
                "attempt_id": None,
            },
        ]
        if standard_log_available
        else []
    )

    logs = {
        "schema_version": 1,
        "course_id": course_id,
        "observed_at": observed_at,
        "from": log_from,
        "to": log_to,
        "standard_log_available": standard_log_available,
        "events": events,
        "question_steps": [
            {
                "course_module_id": 201,
                "learner_id": 501,
                "attempt_id": 8001,
                "slot": 3,
                "question_attempt_id": 8103,
                "question_id": 8203,
                "step_index": 0,
                "occurred_at": log_from,
                "state": "todo",
            },
            {
                "course_module_id": 201,
                "learner_id": 501,
                "attempt_id": 8001,
                "slot": 3,
                "question_attempt_id": 8103,
                "question_id": 8203,
                "step_index": 1,
                "occurred_at": log_to - 1,
                "state": "needsgrading",
            },
        ],
    }

    return {
        "course.json": course,
        "gradebook.json": gradebook,
        "submission-logs.json": logs,
    }


class SnapshotImportAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        if duckdb is None:
            self.fail("duckdb package is required to run analytics acceptance tests")
        self.temp_root = Path(tempfile.mkdtemp(prefix="moodle-analytics-test-"))
        os.chmod(self.temp_root, 0o700)
        self.db_dir = self.temp_root / "db"
        self.db_dir.mkdir(mode=0o700)
        self.db_path = self.db_dir / "analytics.duckdb"

    def tearDown(self) -> None:
        for root, dirs, files in os.walk(self.temp_root, topdown=False):
            root_path = Path(root)
            for name in files:
                file_path = root_path / name
                try:
                    file_path.unlink()
                except FileNotFoundError:
                    pass
            for name in dirs:
                dir_path = root_path / name
                try:
                    if dir_path.is_symlink():
                        dir_path.unlink()
                    else:
                        dir_path.rmdir()
                except FileNotFoundError:
                    pass
        try:
            self.temp_root.rmdir()
        except FileNotFoundError:
            pass

    def write_export(self, name: str, datasets: dict[str, dict], *, indent: int | None = 2) -> Path:
        export_dir = self.temp_root / name
        export_dir.mkdir(mode=0o700, exist_ok=True)
        os.chmod(export_dir, 0o700)
        for filename, payload in datasets.items():
            target = export_dir / filename
            target.write_text(
                json.dumps(payload, indent=indent, sort_keys=(indent is None)) + "\n",
                encoding="utf-8",
            )
            os.chmod(target, 0o600)
        return export_dir

    def run_cli(self, export_dir: Path, db_path: Path | None = None) -> subprocess.CompletedProcess[str]:
        target_db = self.db_path if db_path is None else db_path
        return subprocess.run(
            [sys.executable, "-m", "analytics.import_snapshot", str(export_dir), str(target_db)],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )

    def connect_ro(self, db_path: Path | None = None):
        target_db = self.db_path if db_path is None else db_path
        return duckdb.connect(str(target_db), read_only=True)

    def table_counts(self, db_path: Path | None = None) -> dict[str, int]:
        tables = (
            "snapshots",
            "courses",
            "sections",
            "activities",
            "learners",
            "grade_items",
            "grades",
            "events",
            "question_steps",
        )
        with self.connect_ro(db_path) as con:
            return {
                table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in tables
            }

    def test_import_preserves_zero_vs_null_sparse_grades_and_cross_table_joins(self) -> None:
        export_dir = self.write_export("snap-initial", make_baseline_datasets())
        result = self.run_cli(export_dir)
        self.assertEqual(result.returncode, 0, msg=f"CLI failed: {result.stderr}")
        snapshot_id = result.stdout.strip()
        self.assertEqual(len(snapshot_id), 64)

        mode = stat.S_IMODE(self.db_path.stat().st_mode)
        self.assertEqual(mode & 0o077, 0, "Database file must not be group/world accessible")

        expected_counts = {
            "snapshots": 1,
            "courses": 1,
            "sections": 2,
            "activities": 2,
            "learners": 2,
            "grade_items": 3,
            "grades": 3,
            "events": 2,
            "question_steps": 2,
        }
        self.assertEqual(self.table_counts(), expected_counts)

        with self.connect_ro() as con:
            snap_row = con.execute(
                """
                SELECT snapshot_id, schema_version, course_id, observed_at,
                       log_from, log_to, standard_log_available
                FROM snapshots
                """
            ).fetchone()
            self.assertEqual(
                snap_row,
                (snapshot_id, 1, 101, 1700001000, 1700000000, 1700000900, True),
            )

            grade_rows = con.execute(
                """
                SELECT a.module, g.learner_id, g.final_grade, g.final_grade IS NULL AS is_null
                FROM grades g
                JOIN grade_items gi
                  ON gi.snapshot_id = g.snapshot_id AND gi.grade_item_id = g.grade_item_id
                LEFT JOIN activities a
                  ON a.snapshot_id = gi.snapshot_id AND a.course_module_id = gi.course_module_id
                JOIN learners l
                  ON l.snapshot_id = g.snapshot_id AND l.learner_id = g.learner_id
                WHERE g.snapshot_id = ?
                ORDER BY a.module, g.learner_id
                """,
                [snapshot_id],
            ).fetchall()
            self.assertEqual(
                grade_rows,
                [
                    ("assign", 501, 0.0, False),
                    ("assign", 502, None, True),
                    ("quiz", 501, 75.0, False),
                ],
            )

            course_total_items = con.execute(
                """
                SELECT gi.grade_item_id, gi.item_type, gi.course_module_id, COUNT(g.learner_id)
                FROM grade_items gi
                LEFT JOIN grades g
                  ON g.snapshot_id = gi.snapshot_id AND g.grade_item_id = gi.grade_item_id
                WHERE gi.snapshot_id = ? AND gi.item_type = 'course'
                GROUP BY gi.grade_item_id, gi.item_type, gi.course_module_id
                """,
                [snapshot_id],
            ).fetchall()
            self.assertEqual(course_total_items, [(400, "course", None, 0)])

            joined_steps = con.execute(
                """
                SELECT s.name, a.module, qs.learner_id, qs.attempt_id, qs.slot, qs.step_index, qs.state
                FROM question_steps qs
                JOIN activities a
                  ON a.snapshot_id = qs.snapshot_id AND a.course_module_id = qs.course_module_id
                JOIN sections s
                  ON s.snapshot_id = a.snapshot_id AND s.section_id = a.section_id
                JOIN learners l
                  ON l.snapshot_id = qs.snapshot_id AND l.learner_id = qs.learner_id
                WHERE qs.snapshot_id = ?
                ORDER BY qs.step_index
                """,
                [snapshot_id],
            ).fetchall()
            self.assertEqual(
                joined_steps,
                [
                    ("General", "quiz", 501, 8001, 3, 0, "todo"),
                    ("General", "quiz", 501, 8001, 3, 1, "needsgrading"),
                ],
            )

            event_attempts = con.execute(
                """
                SELECT event_id, attempt_id
                FROM events
                WHERE snapshot_id = ?
                ORDER BY event_id
                """,
                [snapshot_id],
            ).fetchall()
            self.assertEqual(event_attempts, [(7001, 8001), (7002, None)])

    def test_redo_question_at_same_slot_allows_multiple_question_attempt_ids(self) -> None:
        datasets = make_baseline_datasets(observed_at=1700001500)
        datasets["submission-logs.json"]["question_steps"].append(
            {
                "course_module_id": 201,
                "learner_id": 501,
                "attempt_id": 8001,
                "slot": 3,
                "question_attempt_id": 8199,
                "question_id": 8299,
                "step_index": 0,
                "occurred_at": 1700000100,
                "state": "todo",
            }
        )
        export_dir = self.write_export("snap-redo-slot", datasets)
        result = self.run_cli(export_dir)
        self.assertEqual(result.returncode, 0, msg=f"CLI failed: {result.stderr}")
        snapshot_id = result.stdout.strip()

        with self.connect_ro() as con:
            redo_rows = con.execute(
                """
                SELECT question_attempt_id, question_id, attempt_id, slot, step_index, state
                FROM question_steps
                WHERE snapshot_id = ? AND attempt_id = 8001 AND slot = 3 AND step_index = 0
                ORDER BY question_attempt_id
                """,
                [snapshot_id],
            ).fetchall()
            self.assertEqual(
                redo_rows,
                [
                    (8103, 8203, 8001, 3, 0, "todo"),
                    (8199, 8299, 8001, 3, 0, "todo"),
                ],
            )

    def test_reimport_is_idempotent_and_later_snapshot_keeps_historical_grades(self) -> None:
        datasets_t0 = make_baseline_datasets(observed_at=1700001000, quiz_grade=75.0, assign_grade=0.0)
        dir_t0 = self.write_export("snap-t0", datasets_t0, indent=2)
        first = self.run_cli(dir_t0)
        self.assertEqual(first.returncode, 0, msg=first.stderr)
        snap_t0 = first.stdout.strip()
        counts_after_first = self.table_counts()

        # Re-importing the exact same logical dataset (even with different JSON whitespace) is a no-op.
        dir_t0_compact = self.write_export("snap-t0-compact", datasets_t0, indent=None)
        replay = self.run_cli(dir_t0_compact)
        self.assertEqual(replay.returncode, 0, msg=replay.stderr)
        self.assertEqual(replay.stdout.strip(), snap_t0)
        self.assertEqual(self.table_counts(), counts_after_first)

        # A genuinely later snapshot updates grades and adds a manual grading step without overwriting T0.
        datasets_t1 = make_baseline_datasets(observed_at=1700002000, quiz_grade=84.375, assign_grade=90.0)
        datasets_t1["submission-logs.json"]["question_steps"].append(
            {
                "course_module_id": 201,
                "learner_id": 501,
                "attempt_id": 8001,
                "slot": 3,
                "question_attempt_id": 8103,
                "question_id": 8203,
                "step_index": 2,
                "occurred_at": 1700000850,
                "state": "mangrpartial",
            }
        )
        dir_t1 = self.write_export("snap-t1", datasets_t1)
        second = self.run_cli(dir_t1)
        self.assertEqual(second.returncode, 0, msg=second.stderr)
        snap_t1 = second.stdout.strip()
        self.assertNotEqual(snap_t0, snap_t1)

        with self.connect_ro() as con:
            history = con.execute(
                """
                SELECT s.observed_at, g.grade_item_id, g.final_grade
                FROM grades g
                JOIN snapshots s ON s.snapshot_id = g.snapshot_id
                WHERE g.learner_id = 501
                ORDER BY s.observed_at, g.grade_item_id
                """
            ).fetchall()
            self.assertEqual(
                history,
                [
                    (1700001000, 401, 75.0),
                    (1700001000, 402, 0.0),
                    (1700002000, 401, 84.375),
                    (1700002000, 402, 90.0),
                ],
            )

    def test_standard_log_unavailable_allows_question_steps_but_rejects_events(self) -> None:
        datasets_ok = make_baseline_datasets(standard_log_available=False)
        dir_ok = self.write_export("snap-no-stdlog", datasets_ok)
        res_ok = self.run_cli(dir_ok)
        self.assertEqual(res_ok.returncode, 0, msg=res_ok.stderr)

        with self.connect_ro() as con:
            row = con.execute(
                "SELECT standard_log_available, (SELECT COUNT(*) FROM events), (SELECT COUNT(*) FROM question_steps) FROM snapshots"
            ).fetchone()
            self.assertEqual(row, (False, 0, 2))

        counts_before = self.table_counts()
        datasets_bad = make_baseline_datasets(standard_log_available=False, observed_at=1700003000)
        datasets_bad["submission-logs.json"]["events"] = [
            {
                "event_id": 7001,
                "course_module_id": 201,
                "learner_id": 501,
                "event_name": r"\mod_quiz\event\attempt_submitted",
                "occurred_at": 1700000100,
                "object_table": "quiz_attempts",
                "object_id": 8001,
                "attempt_id": 8001,
            }
        ]
        dir_bad = self.write_export("snap-no-stdlog-with-events", datasets_bad)
        res_bad = self.run_cli(dir_bad)
        self.assertNotEqual(res_bad.returncode, 0)
        self.assertEqual(self.table_counts(), counts_before)

    def test_validation_rejects_invalid_payloads_and_leaves_existing_database_intact(self) -> None:
        baseline = make_baseline_datasets()
        good_dir = self.write_export("snap-valid", baseline)
        good_res = self.run_cli(good_dir)
        self.assertEqual(good_res.returncode, 0, msg=good_res.stderr)
        counts_before = self.table_counts()

        mutations: list[tuple[str, callable]] = [
            (
                "course-id-mismatch",
                lambda d: d["gradebook.json"].__setitem__("course_id", 999),
            ),
            (
                "observed-at-mismatch",
                lambda d: d["submission-logs.json"].__setitem__("observed_at", 1700009999),
            ),
            (
                "schema-version-mismatch",
                lambda d: d["course.json"].__setitem__("schema_version", 2),
            ),
            (
                "bool-as-int-in-id",
                lambda d: d["course.json"].__setitem__("course_id", True),
            ),
            (
                "bool-as-float-in-grade",
                lambda d: d["gradebook.json"]["grades"][0].__setitem__("final_grade", True),
            ),
            (
                "int-as-bool-in-hidden",
                lambda d: d["gradebook.json"]["items"][0].__setitem__("hidden", 0),
            ),
            (
                "inverted-log-bounds",
                lambda d: (
                    d["submission-logs.json"].__setitem__("from", 1700000900),
                    d["submission-logs.json"].__setitem__("to", 1700000900),
                ),
            ),
            (
                "event-at-upper-bound",
                lambda d: d["submission-logs.json"]["events"][0].__setitem__("occurred_at", 1700000900),
            ),
            (
                "step-before-lower-bound",
                lambda d: d["submission-logs.json"]["question_steps"][0].__setitem__("occurred_at", 1699999999),
            ),
            (
                "duplicate-learner",
                lambda d: d["course.json"].__setitem__("learner_ids", [501, 501]),
            ),
            (
                "duplicate-grade-pair",
                lambda d: d["gradebook.json"]["grades"].append(
                    deepcopy(d["gradebook.json"]["grades"][0])
                ),
            ),
            (
                "duplicate-question-step",
                lambda d: d["submission-logs.json"]["question_steps"].append(
                    deepcopy(d["submission-logs.json"]["question_steps"][0])
                ),
            ),
            (
                "orphan-activity-section",
                lambda d: d["course.json"]["activities"][0].__setitem__("section_id", 999),
            ),
            (
                "orphan-grade-item-module",
                lambda d: d["gradebook.json"]["items"][1].__setitem__("course_module_id", 999),
            ),
            (
                "orphan-grade-learner",
                lambda d: d["gradebook.json"]["grades"][0].__setitem__("learner_id", 999),
            ),
            (
                "orphan-grade-item-ref",
                lambda d: d["gradebook.json"]["grades"][0].__setitem__("grade_item_id", 999),
            ),
            (
                "orphan-event-learner",
                lambda d: d["submission-logs.json"]["events"][0].__setitem__("learner_id", 999),
            ),
            (
                "orphan-step-module",
                lambda d: d["submission-logs.json"]["question_steps"][0].__setitem__("course_module_id", 999),
            ),
        ]

        for label, mutate in mutations:
            with self.subTest(case=label):
                candidate = deepcopy(baseline)
                for payload in candidate.values():
                    payload["observed_at"] = 1700005000
                mutate(candidate)
                bad_dir = self.write_export(f"bad-{label}", candidate)
                res = self.run_cli(bad_dir)
                self.assertNotEqual(res.returncode, 0, msg=f"Expected failure for {label}")
                self.assertNotIn("Synthetic Course Export", res.stderr)
                self.assertEqual(self.table_counts(), counts_before)

    def test_filesystem_safety_rejects_symlinks_and_non_private_targets_without_deleting_db(self) -> None:
        baseline = make_baseline_datasets()
        valid_dir = self.write_export("snap-fs-valid", baseline)
        self.assertEqual(self.run_cli(valid_dir).returncode, 0)
        counts_before = self.table_counts()

        # Symlinked input file must be rejected.
        symlink_input_dir = self.write_export("snap-symlink-file", make_baseline_datasets(observed_at=1700006000))
        real_course = self.temp_root / "outside-course.json"
        real_course.write_text((symlink_input_dir / "course.json").read_text(encoding="utf-8"), encoding="utf-8")
        (symlink_input_dir / "course.json").unlink()
        (symlink_input_dir / "course.json").symlink_to(real_course)
        res_sym_in = self.run_cli(symlink_input_dir)
        self.assertNotEqual(res_sym_in.returncode, 0)
        self.assertEqual(self.table_counts(), counts_before)

        # Pre-existing symlink database path must be rejected and not deleted.
        symlink_db = self.db_dir / "symlink.duckdb"
        symlink_db.symlink_to(self.db_path)
        res_sym_db = self.run_cli(valid_dir, db_path=symlink_db)
        self.assertNotEqual(res_sym_db.returncode, 0)
        self.assertTrue(symlink_db.is_symlink())
        self.assertTrue(self.db_path.is_file())

        # Non-private destination directory must be rejected.
        shared_dir = self.temp_root / "shared-db-dir"
        shared_dir.mkdir(mode=0o755)
        os.chmod(shared_dir, 0o755)
        shared_db = shared_dir / "analytics.duckdb"
        res_shared_dir = self.run_cli(valid_dir, db_path=shared_db)
        self.assertNotEqual(res_shared_dir.returncode, 0)
        self.assertFalse(shared_db.exists())

        # Pre-existing non-private database file must be rejected and preserved.
        os.chmod(self.db_path, 0o644)
        try:
            res_open_db = self.run_cli(valid_dir)
            self.assertNotEqual(res_open_db.returncode, 0)
            self.assertTrue(self.db_path.is_file())
        finally:
            os.chmod(self.db_path, 0o600)

        self.assertEqual(self.table_counts(), counts_before)


if __name__ == "__main__":
    unittest.main()
