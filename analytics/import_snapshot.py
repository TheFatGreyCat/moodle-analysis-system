#!/usr/bin/env python3
"""Import a local Moodle course snapshot export into a local DuckDB database."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
from typing import Any


DATASET_FILES = ("course.json", "gradebook.json", "submission-logs.json")
SUPPORTED_SCHEMA_VERSION = 1

SCHEMA_DDL = (
    """
    CREATE TABLE IF NOT EXISTS snapshots (
        snapshot_id VARCHAR NOT NULL,
        schema_version INTEGER NOT NULL,
        course_id BIGINT NOT NULL,
        observed_at BIGINT NOT NULL,
        log_from BIGINT NOT NULL,
        log_to BIGINT NOT NULL,
        standard_log_available BOOLEAN NOT NULL,
        PRIMARY KEY (snapshot_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS courses (
        snapshot_id VARCHAR NOT NULL,
        course_id BIGINT NOT NULL,
        name VARCHAR NOT NULL,
        short_name VARCHAR NOT NULL,
        format VARCHAR NOT NULL,
        start_at BIGINT NOT NULL,
        end_at BIGINT,
        PRIMARY KEY (snapshot_id, course_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sections (
        snapshot_id VARCHAR NOT NULL,
        section_id BIGINT NOT NULL,
        number BIGINT NOT NULL,
        name VARCHAR NOT NULL,
        PRIMARY KEY (snapshot_id, section_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS activities (
        snapshot_id VARCHAR NOT NULL,
        course_module_id BIGINT NOT NULL,
        instance_id BIGINT NOT NULL,
        section_id BIGINT NOT NULL,
        module VARCHAR NOT NULL,
        name VARCHAR NOT NULL,
        PRIMARY KEY (snapshot_id, course_module_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS learners (
        snapshot_id VARCHAR NOT NULL,
        learner_id BIGINT NOT NULL,
        PRIMARY KEY (snapshot_id, learner_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS grade_items (
        snapshot_id VARCHAR NOT NULL,
        grade_item_id BIGINT NOT NULL,
        course_module_id BIGINT,
        category_id BIGINT,
        category_total_for BIGINT,
        item_type VARCHAR NOT NULL,
        item_number BIGINT,
        name VARCHAR NOT NULL,
        grade_type BIGINT NOT NULL,
        min_grade DOUBLE NOT NULL,
        max_grade DOUBLE NOT NULL,
        scale VARCHAR,
        hidden BOOLEAN NOT NULL,
        needs_update BOOLEAN NOT NULL,
        PRIMARY KEY (snapshot_id, grade_item_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS grades (
        snapshot_id VARCHAR NOT NULL,
        grade_item_id BIGINT NOT NULL,
        learner_id BIGINT NOT NULL,
        final_grade DOUBLE,
        updated_at BIGINT,
        PRIMARY KEY (snapshot_id, grade_item_id, learner_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        snapshot_id VARCHAR NOT NULL,
        event_id BIGINT NOT NULL,
        course_module_id BIGINT NOT NULL,
        learner_id BIGINT NOT NULL,
        event_name VARCHAR NOT NULL,
        occurred_at BIGINT NOT NULL,
        object_table VARCHAR,
        object_id BIGINT,
        attempt_id BIGINT,
        PRIMARY KEY (snapshot_id, event_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS question_steps (
        snapshot_id VARCHAR NOT NULL,
        course_module_id BIGINT NOT NULL,
        learner_id BIGINT NOT NULL,
        attempt_id BIGINT NOT NULL,
        slot BIGINT NOT NULL,
        question_attempt_id BIGINT NOT NULL,
        question_id BIGINT NOT NULL,
        step_index BIGINT NOT NULL,
        occurred_at BIGINT NOT NULL,
        state VARCHAR NOT NULL,
        PRIMARY KEY (snapshot_id, question_attempt_id, step_index)
    )
    """,
)


class SnapshotValidationError(ValueError):
    """Raised when an export directory or database path fails validation."""


@dataclass(frozen=True)
class ValidatedSnapshot:
    snapshot_id: str
    snapshot_row: tuple[Any, ...]
    course_row: tuple[Any, ...]
    section_rows: list[tuple[Any, ...]]
    activity_rows: list[tuple[Any, ...]]
    learner_rows: list[tuple[Any, ...]]
    grade_item_rows: list[tuple[Any, ...]]
    grade_rows: list[tuple[Any, ...]]
    event_rows: list[tuple[Any, ...]]
    question_step_rows: list[tuple[Any, ...]]


def _require_mapping(value: Any, label: str, required_keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SnapshotValidationError(f"{label} must be a JSON object.")
    keys = set(value.keys())
    if keys != required_keys:
        raise SnapshotValidationError(f"{label} fields do not match the v1 schema.")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise SnapshotValidationError(f"{label} must be a JSON array.")
    return value


def _require_int(
    value: Any,
    label: str,
    *,
    minimum: int | None = None,
    nullable: bool = False,
) -> int | None:
    if value is None:
        if nullable:
            return None
        raise SnapshotValidationError(f"{label} must not be null.")
    if isinstance(value, bool) or not isinstance(value, int):
        raise SnapshotValidationError(f"{label} must be an integer.")
    if minimum is not None and value < minimum:
        raise SnapshotValidationError(f"{label} is below minimum {minimum}.")
    return value


def _require_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise SnapshotValidationError(f"{label} must be a boolean.")
    return value


def _require_float(value: Any, label: str, *, nullable: bool = False) -> float | None:
    if value is None:
        if nullable:
            return None
        raise SnapshotValidationError(f"{label} must not be null.")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SnapshotValidationError(f"{label} must be a finite number.")
    number = float(value)
    if not math.isfinite(number):
        raise SnapshotValidationError(f"{label} must be a finite number.")
    return number


def _require_str(
    value: Any,
    label: str,
    *,
    nullable: bool = False,
    allow_empty: bool = True,
) -> str | None:
    if value is None:
        if nullable:
            return None
        raise SnapshotValidationError(f"{label} must not be null.")
    if not isinstance(value, str):
        raise SnapshotValidationError(f"{label} must be a string.")
    if not allow_empty and value == "":
        raise SnapshotValidationError(f"{label} must not be empty.")
    return value


def _require_scale(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise SnapshotValidationError(f"{label} must be null or an array of strings.")
    items: list[str] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, str):
            raise SnapshotValidationError(f"{label}[{index}] must be a string.")
        items.append(entry)
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


def _check_no_symlink_chain(path: Path, label: str) -> None:
    current = Path(path.anchor) if path.is_absolute() else Path(".")
    parts = path.parts[1:] if path.is_absolute() else path.parts
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise SnapshotValidationError(f"{label} must not traverse symbolic links.")


def _validate_input_directory(export_dir: Path) -> dict[str, Any]:
    raw_path = Path(export_dir)
    if raw_path.is_symlink():
        raise SnapshotValidationError("Export directory must not be a symbolic link.")
    abs_dir = raw_path if raw_path.is_absolute() else (Path.cwd() / raw_path)
    _check_no_symlink_chain(abs_dir, "Export directory")
    try:
        dir_stat = os.lstat(abs_dir)
    except OSError as exc:
        raise SnapshotValidationError("Export directory does not exist or is not accessible.") from exc
    if not stat.S_ISDIR(dir_stat.st_mode):
        raise SnapshotValidationError("Export path must be a regular directory.")

    datasets: dict[str, Any] = {}
    open_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    for filename in DATASET_FILES:
        file_path = abs_dir / filename
        try:
            st = os.lstat(file_path)
        except OSError as exc:
            raise SnapshotValidationError(f"Missing required export file {filename}.") from exc
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise SnapshotValidationError(f"Export file {filename} must be a regular file.")
        try:
            fd = os.open(file_path, open_flags)
        except OSError as exc:
            raise SnapshotValidationError(f"Could not open export file {filename}.") from exc
        try:
            opened_stat = os.fstat(fd)
            if not stat.S_ISREG(opened_stat.st_mode) or (
                opened_stat.st_ino != st.st_ino or opened_stat.st_dev != st.st_dev
            ):
                raise SnapshotValidationError(f"Export file {filename} changed during read.")
            with os.fdopen(fd, "rb") as handle:
                raw_bytes = handle.read()
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        try:
            datasets[filename] = json.loads(
                raw_bytes.decode("utf-8"),
                parse_constant=lambda _const: (_ for _ in ()).throw(
                    SnapshotValidationError(f"Non-standard JSON constant in {filename}.")
                ),
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SnapshotValidationError(f"Invalid JSON in {filename}.") from exc
    return datasets


def _validate_database_target(db_path: Path) -> Path:
    raw_db = Path(db_path)
    if raw_db.is_symlink():
        raise SnapshotValidationError("Database path must not be a symbolic link.")
    abs_db = raw_db if raw_db.is_absolute() else (Path.cwd() / raw_db)
    parent = abs_db.parent
    _check_no_symlink_chain(parent, "Database directory")
    try:
        parent_stat = os.lstat(parent)
    except OSError as exc:
        raise SnapshotValidationError("Database parent directory does not exist.") from exc
    if not stat.S_ISDIR(parent_stat.st_mode):
        raise SnapshotValidationError("Database parent path must be a directory.")
    if stat.S_IMODE(parent_stat.st_mode) & 0o077:
        raise SnapshotValidationError("Database parent directory must be private (mode 0700).")

    for candidate in (abs_db, Path(f"{abs_db}.wal")):
        try:
            st = os.lstat(candidate)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise SnapshotValidationError("Could not inspect database target path.") from exc
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise SnapshotValidationError("Database path must be a regular file.")
        if stat.S_IMODE(st.st_mode) & 0o077:
            raise SnapshotValidationError("Existing database file must be private (no group/world permissions).")

    return abs_db


def _canonical_snapshot_id(datasets: dict[str, Any]) -> str:
    canonical_payload = {name: datasets[name] for name in DATASET_FILES}
    canonical_bytes = json.dumps(
        canonical_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


def validate_datasets(datasets: dict[str, Any]) -> ValidatedSnapshot:
    course_doc = _require_mapping(
        datasets.get("course.json"),
        "course.json",
        {"schema_version", "course_id", "observed_at", "course", "sections", "activities", "learner_ids"},
    )
    gradebook_doc = _require_mapping(
        datasets.get("gradebook.json"),
        "gradebook.json",
        {"schema_version", "course_id", "observed_at", "items", "grades"},
    )
    logs_doc = _require_mapping(
        datasets.get("submission-logs.json"),
        "submission-logs.json",
        {
            "schema_version",
            "course_id",
            "observed_at",
            "from",
            "to",
            "standard_log_available",
            "events",
            "question_steps",
        },
    )

    schema_version = _require_int(course_doc["schema_version"], "course.json.schema_version", minimum=1)
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise SnapshotValidationError("Unsupported schema_version; expected 1.")
    course_id = _require_int(course_doc["course_id"], "course.json.course_id", minimum=1)
    observed_at = _require_int(course_doc["observed_at"], "course.json.observed_at", minimum=0)

    for doc_name, doc in (("gradebook.json", gradebook_doc), ("submission-logs.json", logs_doc)):
        if _require_int(doc["schema_version"], f"{doc_name}.schema_version", minimum=1) != schema_version:
            raise SnapshotValidationError(f"Mismatched schema_version in {doc_name}.")
        if _require_int(doc["course_id"], f"{doc_name}.course_id", minimum=1) != course_id:
            raise SnapshotValidationError(f"Mismatched course_id in {doc_name}.")
        if _require_int(doc["observed_at"], f"{doc_name}.observed_at", minimum=0) != observed_at:
            raise SnapshotValidationError(f"Mismatched observed_at in {doc_name}.")

    snapshot_id = _canonical_snapshot_id(datasets)

    course_meta = _require_mapping(
        course_doc["course"],
        "course.json.course",
        {"name", "short_name", "format", "start_at", "end_at"},
    )
    course_row = (
        snapshot_id,
        course_id,
        _require_str(course_meta["name"], "course.name", allow_empty=False),
        _require_str(course_meta["short_name"], "course.short_name", allow_empty=False),
        _require_str(course_meta["format"], "course.format", allow_empty=False),
        _require_int(course_meta["start_at"], "course.start_at", minimum=0),
        _require_int(course_meta["end_at"], "course.end_at", minimum=0, nullable=True),
    )

    section_ids: set[int] = set()
    section_rows: list[tuple[Any, ...]] = []
    for idx, raw_section in enumerate(_require_list(course_doc["sections"], "course.json.sections")):
        sec = _require_mapping(raw_section, f"sections[{idx}]", {"section_id", "number", "name"})
        section_id = _require_int(sec["section_id"], f"sections[{idx}].section_id", minimum=1)
        assert section_id is not None
        if section_id in section_ids:
            raise SnapshotValidationError("Duplicate section_id in course.json.")
        section_ids.add(section_id)
        section_rows.append(
            (
                snapshot_id,
                section_id,
                _require_int(sec["number"], f"sections[{idx}].number", minimum=0),
                _require_str(sec["name"], f"sections[{idx}].name"),
            )
        )

    activity_ids: set[int] = set()
    activity_rows: list[tuple[Any, ...]] = []
    for idx, raw_activity in enumerate(_require_list(course_doc["activities"], "course.json.activities")):
        act = _require_mapping(
            raw_activity,
            f"activities[{idx}]",
            {"course_module_id", "instance_id", "section_id", "module", "name"},
        )
        cm_id = _require_int(act["course_module_id"], f"activities[{idx}].course_module_id", minimum=1)
        instance_id = _require_int(act["instance_id"], f"activities[{idx}].instance_id", minimum=1)
        sec_id = _require_int(act["section_id"], f"activities[{idx}].section_id", minimum=1)
        assert cm_id is not None and instance_id is not None and sec_id is not None
        if cm_id in activity_ids:
            raise SnapshotValidationError("Duplicate course_module_id in course.json.")
        if sec_id not in section_ids:
            raise SnapshotValidationError("Activity references an unknown section_id.")
        activity_ids.add(cm_id)
        activity_rows.append(
            (
                snapshot_id,
                cm_id,
                instance_id,
                sec_id,
                _require_str(act["module"], f"activities[{idx}].module", allow_empty=False),
                _require_str(act["name"], f"activities[{idx}].name"),
            )
        )

    learner_ids: set[int] = set()
    learner_rows: list[tuple[Any, ...]] = []
    for idx, raw_learner in enumerate(_require_list(course_doc["learner_ids"], "course.json.learner_ids")):
        learner_id = _require_int(raw_learner, f"learner_ids[{idx}]", minimum=1)
        assert learner_id is not None
        if learner_id in learner_ids:
            raise SnapshotValidationError("Duplicate learner_id in course.json.")
        learner_ids.add(learner_id)
        learner_rows.append((snapshot_id, learner_id))

    grade_item_ids: set[int] = set()
    grade_item_rows: list[tuple[Any, ...]] = []
    for idx, raw_item in enumerate(_require_list(gradebook_doc["items"], "gradebook.json.items")):
        item = _require_mapping(
            raw_item,
            f"items[{idx}]",
            {
                "grade_item_id",
                "course_module_id",
                "category_id",
                "category_total_for",
                "item_type",
                "item_number",
                "name",
                "grade_type",
                "min_grade",
                "max_grade",
                "scale",
                "hidden",
                "needs_update",
            },
        )
        grade_item_id = _require_int(item["grade_item_id"], f"items[{idx}].grade_item_id", minimum=1)
        assert grade_item_id is not None
        if grade_item_id in grade_item_ids:
            raise SnapshotValidationError("Duplicate grade_item_id in gradebook.json.")
        cm_id = _require_int(
            item["course_module_id"], f"items[{idx}].course_module_id", minimum=1, nullable=True
        )
        if cm_id is not None and cm_id not in activity_ids:
            raise SnapshotValidationError("Grade item references an unknown course_module_id.")
        grade_item_ids.add(grade_item_id)
        grade_item_rows.append(
            (
                snapshot_id,
                grade_item_id,
                cm_id,
                _require_int(item["category_id"], f"items[{idx}].category_id", minimum=1, nullable=True),
                _require_int(
                    item["category_total_for"], f"items[{idx}].category_total_for", minimum=1, nullable=True
                ),
                _require_str(item["item_type"], f"items[{idx}].item_type", allow_empty=False),
                _require_int(item["item_number"], f"items[{idx}].item_number", minimum=0, nullable=True),
                _require_str(item["name"], f"items[{idx}].name"),
                _require_int(item["grade_type"], f"items[{idx}].grade_type", minimum=0),
                _require_float(item["min_grade"], f"items[{idx}].min_grade"),
                _require_float(item["max_grade"], f"items[{idx}].max_grade"),
                _require_scale(item["scale"], f"items[{idx}].scale"),
                _require_bool(item["hidden"], f"items[{idx}].hidden"),
                _require_bool(item["needs_update"], f"items[{idx}].needs_update"),
            )
        )

    seen_grades: set[tuple[int, int]] = set()
    grade_rows: list[tuple[Any, ...]] = []
    for idx, raw_grade in enumerate(_require_list(gradebook_doc["grades"], "gradebook.json.grades")):
        grade = _require_mapping(
            raw_grade,
            f"grades[{idx}]",
            {"grade_item_id", "learner_id", "final_grade", "updated_at"},
        )
        grade_item_id = _require_int(grade["grade_item_id"], f"grades[{idx}].grade_item_id", minimum=1)
        learner_id = _require_int(grade["learner_id"], f"grades[{idx}].learner_id", minimum=1)
        assert grade_item_id is not None and learner_id is not None
        if grade_item_id not in grade_item_ids:
            raise SnapshotValidationError("Grade row references an unknown grade_item_id.")
        if learner_id not in learner_ids:
            raise SnapshotValidationError("Grade row references an unknown learner_id.")
        pair = (grade_item_id, learner_id)
        if pair in seen_grades:
            raise SnapshotValidationError("Duplicate (grade_item_id, learner_id) in gradebook.json.")
        seen_grades.add(pair)
        grade_rows.append(
            (
                snapshot_id,
                grade_item_id,
                learner_id,
                _require_float(grade["final_grade"], f"grades[{idx}].final_grade", nullable=True),
                _require_int(grade["updated_at"], f"grades[{idx}].updated_at", minimum=0, nullable=True),
            )
        )

    log_from = _require_int(logs_doc["from"], "submission-logs.json.from", minimum=0)
    log_to = _require_int(logs_doc["to"], "submission-logs.json.to", minimum=1)
    assert log_from is not None and log_to is not None
    if log_from >= log_to:
        raise SnapshotValidationError("submission-logs.json from must be strictly less than to.")
    standard_log_available = _require_bool(
        logs_doc["standard_log_available"], "submission-logs.json.standard_log_available"
    )

    raw_events = _require_list(logs_doc["events"], "submission-logs.json.events")
    if not standard_log_available and raw_events:
        raise SnapshotValidationError("events must be empty when standard_log_available is false.")

    seen_events: set[int] = set()
    event_rows: list[tuple[Any, ...]] = []
    for idx, raw_event in enumerate(raw_events):
        event = _require_mapping(
            raw_event,
            f"events[{idx}]",
            {
                "event_id",
                "course_module_id",
                "learner_id",
                "event_name",
                "occurred_at",
                "object_table",
                "object_id",
                "attempt_id",
            },
        )
        event_id = _require_int(event["event_id"], f"events[{idx}].event_id", minimum=1)
        cm_id = _require_int(event["course_module_id"], f"events[{idx}].course_module_id", minimum=1)
        learner_id = _require_int(event["learner_id"], f"events[{idx}].learner_id", minimum=1)
        occurred_at = _require_int(event["occurred_at"], f"events[{idx}].occurred_at", minimum=0)
        assert event_id is not None and cm_id is not None and learner_id is not None and occurred_at is not None
        if event_id in seen_events:
            raise SnapshotValidationError("Duplicate event_id in submission-logs.json.")
        if cm_id not in activity_ids:
            raise SnapshotValidationError("Event references an unknown course_module_id.")
        if learner_id not in learner_ids:
            raise SnapshotValidationError("Event references an unknown learner_id.")
        if not (log_from <= occurred_at < log_to):
            raise SnapshotValidationError("Event occurred_at is outside [from, to).")
        seen_events.add(event_id)
        event_rows.append(
            (
                snapshot_id,
                event_id,
                cm_id,
                learner_id,
                _require_str(event["event_name"], f"events[{idx}].event_name", allow_empty=False),
                occurred_at,
                _require_str(event["object_table"], f"events[{idx}].object_table", nullable=True),
                _require_int(event["object_id"], f"events[{idx}].object_id", minimum=1, nullable=True),
                _require_int(event["attempt_id"], f"events[{idx}].attempt_id", minimum=1, nullable=True),
            )
        )

    seen_steps: set[tuple[int, int]] = set()
    question_step_rows: list[tuple[Any, ...]] = []
    for idx, raw_step in enumerate(
        _require_list(logs_doc["question_steps"], "submission-logs.json.question_steps")
    ):
        step = _require_mapping(
            raw_step,
            f"question_steps[{idx}]",
            {
                "course_module_id",
                "learner_id",
                "attempt_id",
                "slot",
                "question_attempt_id",
                "question_id",
                "step_index",
                "occurred_at",
                "state",
            },
        )
        cm_id = _require_int(step["course_module_id"], f"question_steps[{idx}].course_module_id", minimum=1)
        learner_id = _require_int(step["learner_id"], f"question_steps[{idx}].learner_id", minimum=1)
        attempt_id = _require_int(step["attempt_id"], f"question_steps[{idx}].attempt_id", minimum=1)
        slot = _require_int(step["slot"], f"question_steps[{idx}].slot", minimum=1)
        qa_id = _require_int(
            step["question_attempt_id"], f"question_steps[{idx}].question_attempt_id", minimum=1
        )
        question_id = _require_int(step["question_id"], f"question_steps[{idx}].question_id", minimum=1)
        step_index = _require_int(step["step_index"], f"question_steps[{idx}].step_index", minimum=0)
        occurred_at = _require_int(step["occurred_at"], f"question_steps[{idx}].occurred_at", minimum=0)
        assert (
            cm_id is not None
            and learner_id is not None
            and attempt_id is not None
            and slot is not None
            and qa_id is not None
            and question_id is not None
            and step_index is not None
            and occurred_at is not None
        )
        if cm_id not in activity_ids:
            raise SnapshotValidationError("Question step references an unknown course_module_id.")
        if learner_id not in learner_ids:
            raise SnapshotValidationError("Question step references an unknown learner_id.")
        if not (log_from <= occurred_at < log_to):
            raise SnapshotValidationError("Question step occurred_at is outside [from, to).")
        step_key = (qa_id, step_index)
        if step_key in seen_steps:
            raise SnapshotValidationError("Duplicate question step in submission-logs.json.")
        seen_steps.add(step_key)
        question_step_rows.append(
            (
                snapshot_id,
                cm_id,
                learner_id,
                attempt_id,
                slot,
                qa_id,
                question_id,
                step_index,
                occurred_at,
                _require_str(step["state"], f"question_steps[{idx}].state", allow_empty=False),
            )
        )

    snapshot_row = (
        snapshot_id,
        schema_version,
        course_id,
        observed_at,
        log_from,
        log_to,
        standard_log_available,
    )

    return ValidatedSnapshot(
        snapshot_id=snapshot_id,
        snapshot_row=snapshot_row,
        course_row=course_row,
        section_rows=section_rows,
        activity_rows=activity_rows,
        learner_rows=learner_rows,
        grade_item_rows=grade_item_rows,
        grade_rows=grade_rows,
        event_rows=event_rows,
        question_step_rows=question_step_rows,
    )


def _write_to_duckdb(db_path: Path, snapshot: ValidatedSnapshot) -> None:
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise RuntimeError("The duckdb Python package (duckdb==1.5.4) is required.") from exc

    previous_umask = os.umask(0o077)
    con = None
    in_transaction = False
    try:
        con = duckdb.connect(str(db_path))
        for ddl in SCHEMA_DDL:
            con.execute(ddl)

        con.execute("BEGIN TRANSACTION")
        in_transaction = True

        existing = con.execute(
            "SELECT 1 FROM snapshots WHERE snapshot_id = ?",
            [snapshot.snapshot_id],
        ).fetchone()
        if existing is not None:
            con.execute("ROLLBACK")
            in_transaction = False
            return

        con.execute(
            "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
            snapshot.snapshot_row,
        )
        con.execute(
            "INSERT INTO courses VALUES (?, ?, ?, ?, ?, ?, ?)",
            snapshot.course_row,
        )
        if snapshot.section_rows:
            con.executemany(
                "INSERT INTO sections VALUES (?, ?, ?, ?)",
                snapshot.section_rows,
            )
        if snapshot.activity_rows:
            con.executemany(
                "INSERT INTO activities VALUES (?, ?, ?, ?, ?, ?)",
                snapshot.activity_rows,
            )
        if snapshot.learner_rows:
            con.executemany(
                "INSERT INTO learners VALUES (?, ?)",
                snapshot.learner_rows,
            )
        if snapshot.grade_item_rows:
            con.executemany(
                "INSERT INTO grade_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                snapshot.grade_item_rows,
            )
        if snapshot.grade_rows:
            con.executemany(
                "INSERT INTO grades VALUES (?, ?, ?, ?, ?)",
                snapshot.grade_rows,
            )
        if snapshot.event_rows:
            con.executemany(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                snapshot.event_rows,
            )
        if snapshot.question_step_rows:
            con.executemany(
                "INSERT INTO question_steps VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                snapshot.question_step_rows,
            )

        con.execute("COMMIT")
        in_transaction = False
    except Exception:
        if con is not None and in_transaction:
            try:
                con.execute("ROLLBACK")
            except Exception:
                pass
        raise
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
        for candidate in (db_path, Path(f"{db_path}.wal")):
            try:
                st = os.lstat(candidate)
                if stat.S_ISREG(st.st_mode):
                    os.chmod(candidate, 0o600)
            except OSError:
                pass
        os.umask(previous_umask)


def import_snapshot(export_dir: Path, db_path: Path) -> str:
    datasets = _validate_input_directory(export_dir)
    validated = validate_datasets(datasets)
    target_db = _validate_database_target(db_path)
    _write_to_duckdb(target_db, validated)
    return validated.snapshot_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m analytics.import_snapshot",
        description="Validate and import a local Moodle course snapshot export into DuckDB.",
    )
    parser.add_argument(
        "export_dir",
        type=Path,
        help="Path to the local directory containing course.json, gradebook.json, and submission-logs.json.",
    )
    parser.add_argument(
        "database",
        type=Path,
        help="Path to the local DuckDB database file in a private (0700) directory.",
    )
    args = parser.parse_args(argv)

    try:
        snapshot_id = import_snapshot(args.export_dir, args.database)
    except (SnapshotValidationError, RuntimeError) as err:
        sys.stderr.write(f"ERROR: {err}\n")
        return 1
    except Exception:
        sys.stderr.write("ERROR: Snapshot import failed due to an unexpected internal error.\n")
        return 1

    sys.stdout.write(f"{snapshot_id}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
