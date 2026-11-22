import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from analytics.import_snapshot import import_snapshot
from test_import_snapshot import make_baseline_datasets


class ReportSnapshotTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.database = self.root / "course.duckdb"

    def load(self, datasets):
        for name, dataset in datasets.items():
            (self.root / name).write_text(json.dumps(dataset), encoding="utf-8")
        return import_snapshot(self.root, self.database)

    def report(self, snapshot, threshold="75", database=None):
        return subprocess.run(
            [sys.executable, "-m", "analytics.report_snapshot", str(database or self.database),
             "--snapshot-id", snapshot, "--pass-percent", threshold],
            capture_output=True, text=True, check=False,
        )

    def test_submission_and_grade_rates_use_the_selected_snapshot_and_separate_denominators(self):
        datasets = make_baseline_datasets()
        grades = datasets["gradebook.json"]
        grades["grades"].append(dict(grades["grades"][0], learner_id=502, final_grade=50))
        grades["items"].append(dict(grades["items"][1], grade_item_id=403, item_number=1, needs_update=True))
        grades["grades"].append(dict(grades["grades"][0], grade_item_id=403, final_grade=100))
        events = datasets["submission-logs.json"]["events"]
        events.extend([
            dict(events[0], event_id=7003, object_id=8002, attempt_id=8002),
            dict(events[1], event_id=7004, event_name=r"\mod_assign\event\assessable_submitted"),
        ])
        snapshot = self.load(datasets)
        other = self.load(make_baseline_datasets(observed_at=1700002000, quiz_grade=100))
        result = self.report(snapshot)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertNotEqual(snapshot, other)
        self.assertEqual(report["snapshot_id"], snapshot)
        self.assertEqual(report["cohort_size"], 2)
        self.assertEqual(report["log_window"], {"from": 1700000000, "to": 1700000900, "standard_log_available": True})
        activities = {row["course_module_id"]: row for row in report["activities"]}
        for cmid in (201, 202):
            self.assertEqual(activities[cmid]["observed_submitters"], 1)
            self.assertEqual(activities[cmid]["observed_submission_share"], 0.5)
        items = {row["grade_item_id"]: row for row in report["grade_items"]}
        self.assertEqual(set(items), {401, 402, 403})
        self.assertEqual((items[401]["graded_learners"], items[401]["passed_learners"], items[401]["pass_rate"]), (2, 1, 0.5))
        self.assertEqual((items[402]["graded_learners"], items[402]["passed_learners"], items[402]["pass_rate"]), (1, 0, 0.0))
        self.assertEqual(items[403]["pass_status"], "needs_update")
        self.assertIsNone(items[403]["pass_rate"])

    def test_unavailable_log_and_missing_grades_are_not_zero_rates(self):
        datasets = make_baseline_datasets(standard_log_available=False, quiz_grade=None, assign_grade=None)
        snapshot = self.load(datasets)
        result = self.report(snapshot)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        for activity in report["activities"]:
            self.assertIsNone(activity["observed_submitters"])
            self.assertIsNone(activity["observed_submission_share"])
        for item in report["grade_items"]:
            self.assertEqual(item["graded_learners"], 0)
            self.assertIsNone(item["pass_rate"])
        for requested, threshold in (("0" * 64, "75"), (snapshot, "nan"), (snapshot, "101")):
            with self.subTest(snapshot=requested, threshold=threshold):
                rejected = self.report(requested, threshold)
                self.assertNotEqual(rejected.returncode, 0)
                self.assertEqual(rejected.stdout, "")
        missing = self.root / "missing.duckdb"
        rejected = self.report(snapshot, database=missing)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertFalse(missing.exists())
