#!/usr/bin/env python3
import json
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.parse import urlencode


CONTAINER = "moodle-analysis-web"
ENDPOINT = "http://127.0.0.1/webservice/rest/server.php"
FUNCTION = "local_quizexport_get_attempts"
BASE = Path(__file__).resolve().parent


def execute(*args, body=None, uid=None):
    run = ["docker", "exec", "-i"]
    if uid is not None:
        run += ["--user", uid]
    result = subprocess.run([*run, CONTAINER, *args], input=body, text=True, capture_output=True)
    if result.returncode:
        detail = result.stderr.strip() or next((line for line in result.stdout.splitlines() if line.startswith('!!!')), '')
        raise RuntimeError(f"{args[0]} exited {result.returncode}: {detail}")
    return result.stdout


def request(token, quiz, learner):
    payload = urlencode({"wstoken": token, "wsfunction": FUNCTION, "moodlewsrestformat": "json",
                         "quizid": quiz, "userid": learner})
    return json.loads(execute("curl", "--silent", "--show-error", "--max-time", "15",
                              "--header", "Host: 127.0.0.1:18080", "--data-binary", "@-", ENDPOINT, body=payload))


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def save_snapshot(path, response):
    with path.open("x", encoding="utf-8") as output:
        json.dump(response, output, indent=2, ensure_ascii=False)
        output.write("\n")


def main():
    subprocess.run(["docker", "cp", str(BASE / "fixture.php"), f"{CONTAINER}:/tmp/quizexport-fixture.php"], check=True)
    fixture = json.loads(execute("php", "/tmp/quizexport-fixture.php", uid="www-data"))
    read = fixture["tokens"]["reader"]
    quiz = fixture["quiz"]
    learner = fixture["learner"]
    before = request(read, quiz, learner)
    require("attempts" in before, f"REST export did not return attempts ({before.get('errorcode', 'unknown')})")
    require(before["course_id"] == fixture["course"] and before["quiz_id"] == quiz and
            before["learner_id"] == learner, "Export must identify the requested course, quiz, and learner")
    first, second, third = before["attempts"]
    require([a["attempt_id"] for a in (first, second, third)] == fixture["attempts"], "Attempts were lost or reordered")
    require([a["attempt_number"] for a in (first, second, third)] == [1, 2, 3], "Attempt number must not use Gradebook")
    require([a["state"] for a in (first, second, third)] == ["finished", "finished", "inprogress"], "Attempt states differ")
    require(third["submitted_at"] is None and third["mark"] is None, "In-progress attempt is not a submitted grade")
    require(first["mark"] is None and second["mark"] == 3 and first["max_mark"] == 4,
            "Unmarked attempt or raw quiz mark is incorrect")
    questions = {q["slot"]: q for q in first["questions"]}
    require(list(questions) == [1, 2, 3, 4], "Question slots differ from Moodle fixture")
    for attempt, selected in zip((first, second, third), fixture["selected_questions"], strict=True):
        require(selected in fixture["random_candidates"], "Fixture did not assign a random candidate")
        require([q["question_id"] for q in attempt["questions"]] == [*fixture["fixed_questions"], selected],
                "Export must include the actual selected question and omit unassigned candidates")
    require(questions[1]["mark"] == 0 and questions[1]["response_state"] == "answered", "Wrong answer must preserve zero")
    require(questions[2]["response_state"] == "blank", "Assigned blank question was not distinguished")
    require(questions[3]["state"] == "needsgrading" and questions[3]["mark"] is None, "Manual grade is not available before T0")
    require(questions[4]["mark"] == 0.8 and questions[4]["max_mark"] == 1, "Partial mark must not be rounded for display")
    repeat = request(read, quiz, learner)
    require(repeat["attempts"] == before["attempts"], "Repeated export changed or duplicated records")
    for token, requestedquiz, requesteduser in [
        (read, fixture["outside_quiz"], learner), (read, quiz, fixture["other"]),
        (fixture["tokens"]["restricted"], quiz, learner), ("invalid", quiz, learner),
    ]:
        denied = request(token, requestedquiz, requesteduser)
        require("errorcode" in denied and "attempts" not in denied, "Unauthorized REST request returned data")
    print("PASS: REST export, raw marks, random question identity, repeat read and four scope/token denials")

    exports = Path(tempfile.mkdtemp(prefix="exports-", dir=BASE.parent / ".sandbox"))
    before_path = exports / "before-t0.json"
    save_snapshot(before_path, before)
    cutoff = before["observed_at"]
    while time.time() < cutoff + 1:
        time.sleep(0.05)
    execute("php", "/tmp/quizexport-fixture.php", "grade", str(first["attempt_id"]), uid="www-data")
    after = request(read, quiz, learner)
    changed = after["attempts"][0]
    graded = changed["questions"][2]
    require(after["observed_at"] > cutoff and graded["last_action_at"] > cutoff, "Manual grade must be later than T0")
    require(graded["mark"] == 0.375 and graded["state"].startswith("man") and changed["mark"] == 1.175,
            "After manual grading, the question and attempt must expose updated raw marks")
    require(json.loads(before_path.read_text(encoding="utf-8")) == before, "The saved T0 snapshot changed")
    save_snapshot(exports / "after-t0.json", after)
    print(f"PASS: manual grade excluded from T0 and included in later snapshot; exports: {exports.relative_to(BASE.parent)}")


if __name__ == "__main__":
    main()
