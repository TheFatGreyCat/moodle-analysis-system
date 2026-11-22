#!/usr/bin/env python3
import json
from pathlib import Path
import re
import subprocess
import tarfile
from io import BytesIO
import time

from verify import BASE, CONTAINER, execute, require


COMMAND = "/var/www/html/local/quizexport/cli/export_course.php"
FILES = {"course.json", "gradebook.json", "submission-logs.json"}


def run_export(fixture, *flags, operator=None):
    return subprocess.run(
        ["docker", "exec", "--user", "www-data", CONTAINER, "php", COMMAND,
         f"--courseid={fixture['course']}", f"--as-user={operator or fixture['reader']}", *flags],
        text=True, capture_output=True,
    )


def read_export(result):
    require(result.returncode == 0, f"CLI export failed: {result.stdout.strip()} {result.stderr.strip()}")
    directory = result.stdout.strip()
    require(re.fullmatch(r"/var/www/moodledata/quizexport/course-\d+-[a-f0-9]+", directory), "Unexpected output path")
    mode = int(execute("stat", "-c", "%a", directory).strip(), 8)
    require(mode & 0o777 == 0o700, "Export directory must be private")
    archive = subprocess.check_output(["docker", "exec", CONTAINER, "tar", "-C", directory, "-cf", "-", "."])
    with tarfile.open(fileobj=BytesIO(archive)) as files:
        members = {Path(member.name).name: member for member in files if member.isfile()}
        require(members.keys() == FILES, "Export must consist of exactly three datasets")
        require(all(member.mode == 0o600 for member in members.values()), "Export files must be private")
        raw = {name: files.extractfile(member).read() for name, member in members.items()}
    for payload in raw.values():
        require(all(value not in payload for value in (b"@example.invalid", b"Synthetic answer", b"wstoken", b'"ip"', b'"other"')),
                "Private fields leaked to the export")
    return directory, {name: json.loads(payload) for name, payload in raw.items()}


def main():
    subprocess.run(["docker", "cp", str(BASE / "fixture.php"), f"{CONTAINER}:/tmp/quizexport-fixture.php"], check=True)
    fixture = json.loads(execute("php", "/tmp/quizexport-fixture.php", "course-export", uid="www-data"))
    start, end = fixture["from"], int(time.time()) + 1
    flags = (f"--from={start}", f"--to={end}")
    directory, datasets = read_export(run_export(fixture, *flags))
    course, grades, logs = (datasets[name] for name in ("course.json", "gradebook.json", "submission-logs.json"))
    require(course["course_id"] == fixture["course"] and course["course"]["name"] == "Synthetic Course Export", "Wrong course")
    require(set(course["learner_ids"]) == {fixture["learner"], fixture["no_grade"]}, "Roster crossed separate groups or included staff")
    activities = {(row["module"], row["instance_id"]): row["course_module_id"] for row in course["activities"]}
    quizcm = activities["quiz", fixture["quiz"]]
    assigncm = activities["assign", fixture["assignment"]]
    require(("assign", fixture["hidden_assignment"]) not in activities, "Hidden activity exposed")
    require(any(section["name"] == "Practice" for section in course["sections"]), "Course section not exported")
    items = {row["grade_item_id"]: row for row in grades["items"]}
    mapped = {(items[row["grade_item_id"]]["course_module_id"], row["learner_id"]): row["final_grade"]
              for row in grades["grades"] if items[row["grade_item_id"]]["course_module_id"] is not None}
    require(mapped[quizcm, fixture["learner"]] == 75, "Gradebook must contain Moodle's scaled/aggregated quiz grade")
    require(mapped[assigncm, fixture["learner"]] == 0 and mapped[assigncm, fixture["no_grade"]] is None,
            "Assignment zero and missing grade were conflated")
    require(any(item["item_type"] == "course" for item in items.values()), "Course total not present")
    require(all(row["learner_id"] != fixture["other"] for row in grades["grades"]), "Gradebook crossed groups")
    require(logs["standard_log_available"] and {event["course_module_id"] for event in logs["events"]} >= {quizcm, assigncm},
            "Expected quiz and assignment Standard Log events")
    require(any(event["event_name"].endswith("attempt_submitted") for event in logs["events"]) and
            any(event["event_name"].endswith("submission_graded") for event in logs["events"]), "Submission/grading events missing")
    for field in ("events", "question_steps"):
        require(all(row["learner_id"] in course["learner_ids"] and start <= row["occurred_at"] < end for row in logs[field]),
                f"{field} crossed the time or group filter")
    steps = [row for row in logs["question_steps"] if row["attempt_id"] == fixture["attempts"][0] and row["slot"] == 3]
    require({row["state"] for row in steps} >= {"todo", "needsgrading"}, "Question Engine history collapsed to the final state")
    repeat_directory, repeat = read_export(run_export(fixture, *flags))
    require(repeat_directory != directory and repeat["submission-logs.json"]["events"] == logs["events"] and
            repeat["submission-logs.json"]["question_steps"] == logs["question_steps"], "Re-read changed record keys or overwrote output")
    empty_directory, empty = read_export(run_export(fixture, "--from=1", "--to=2"))
    require(empty_directory != directory and not empty["submission-logs.json"]["events"] and
            not empty["submission-logs.json"]["question_steps"], "Time window did not exclude history")
    denied = run_export(fixture, *flags, operator=fixture["restricted"])
    require(denied.returncode != 0 and "/var/www/moodledata/quizexport/course-" not in denied.stdout, "Unauthorized export succeeded")
    print(f"PASS: course, Gradebook and submission logs; private, non-overwriting snapshot: {directory}")


if __name__ == "__main__":
    main()
