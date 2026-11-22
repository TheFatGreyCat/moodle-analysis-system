# Local DuckDB Snapshot Importer

Validate and load a local Moodle v1 course export (`course.json`, `gradebook.json`, `submission-logs.json`) into a local DuckDB database without modifying Moodle or making any network calls.

## Setup and Commands

Install the pinned DuckDB runtime in a local virtual environment:

```bash
python3 -m venv .venv
.venv/bin/pip install -r analytics/requirements.txt
```

Prepare a private directory (`0700`) for the local DuckDB file and import a snapshot export directory produced by `local/quizexport/cli/export_course.php`:

```bash
mkdir -m 0700 -p /path/to/private-analytics
python3 -m analytics.import_snapshot /var/www/moodledata/quizexport/course-101-deadbeef /path/to/private-analytics/course.duckdb
```

On success, the command prints the deterministic 64-character hexadecimal `snapshot_id` (SHA-256 over the canonical parsed JSON of the three v1 datasets).

Run the acceptance test suite:

```bash
python3 -m unittest discover -s analytics -p 'test_*.py'
```

## Submission and Gradebook Report

Use the snapshot ID printed by the importer. Run from the repository root in the same environment:

```bash
python3 -m analytics.report_snapshot /path/to/private-analytics/course.duckdb \
  --snapshot-id "$SNAPSHOT_ID" --pass-percent 60
```

The CLI opens DuckDB read-only and prints aggregate JSON to stdout. It does not export a new file, include learner IDs, or contact Moodle, Ollama, or any external service. Keep redirected output in a private directory; activity names and small-group aggregates can still be sensitive.

- **Observed submission share:** distinct learners with `attempt_submitted` (Quiz) or `assessable_submitted` (Assignment) divided by the size of the snapshot's exported cohort. Repeat attempts count once. This is evidence from the retained log window `[from, to)`, not the proportion of learners eligible for a specific activity and not proof that anyone without an event failed to submit. Without Standard Log, the observed count and share are `null`; an empty cohort also has no share.
- **Gradebook pass rate:** evaluated separately for each numeric Quiz/Assignment grade item using its current stored grade. The threshold is `min_grade + (max_grade - min_grade) * pass_percent / 100`, inclusive; this is an analyst-supplied threshold, not Moodle's grade-to-pass setting. The denominator is learners with a non-null grade; zero is a real grade. Stale items, unsupported scales/ranges and items with no grades return `null` with a `pass_status`. Rates are fractions from `0` to `1`.
- Gradebook metrics use the snapshot's current stored grades, not grades at the end of the log window or grades from the first attempt. Every query is scoped to the explicit snapshot ID.

## DuckDB Schema and Table Grains

Every table is keyed and scoped by `snapshot_id` so multiple point-in-time exports can coexist in the same database without overwriting prior observations:

| Table | Primary Key / Grain | Purpose |
| --- | --- | --- |
| `snapshots` | `(snapshot_id)` | One row per imported snapshot. Stores `schema_version` (`1`), `course_id`, `observed_at`, `log_from`, `log_to` (`[log_from, log_to)`), and `standard_log_available`. |
| `courses` | `(snapshot_id, course_id)` | One row per snapshot for the exported course catalog metadata (`name`, `short_name`, `format`, `start_at`, nullable `end_at`). |
| `sections` | `(snapshot_id, section_id)` | One row per visible course section in the snapshot (`number`, `name`). |
| `activities` | `(snapshot_id, course_module_id)` | One row per visible course module (`instance_id`, `section_id`, `module`, `name`), joining to `sections` on `(snapshot_id, section_id)`. |
| `learners` | `(snapshot_id, learner_id)` | One row per active gradable learner ID in the operator's group scope for the snapshot. |
| `grade_items` | `(snapshot_id, grade_item_id)` | One row per exported Gradebook item (`course_module_id` is nullable for course/category/manual totals; `min_grade`, `max_grade`, JSON `scale`, `hidden`, `needs_update`). |
| `grades` | `(snapshot_id, grade_item_id, learner_id)` | Up to one row per `(grade_item_id, learner_id)` in the snapshot. Sparse grade rows are allowed, and `0.0` is preserved separately from `NULL`. |
| `events` | `(snapshot_id, event_id)` | One row per Moodle Standard Log event within `[log_from, log_to)` (`course_module_id`, `learner_id`, `event_name`, `occurred_at`, `object_table`, `object_id`, nullable `attempt_id`). |
| `question_steps` | `(snapshot_id, question_attempt_id, step_index)` | One row per Question Engine step within `[log_from, log_to)` (`course_module_id`, `learner_id`, `attempt_id`, `slot`, `question_id`, `occurred_at`, `state`). |

Note: `events.attempt_id` is nullable and v1 course exports do not contain a standalone `attempts` dataset, so `attempt_id` is stored as a typed integer attribute without an invented foreign-key table.

## Validation, Idempotency, and History

- **Validation before write**: All three files are read and validated in memory before opening or mutating DuckDB. Shared metadata (`schema_version = 1`, `course_id`, `observed_at`), strict field types (rejecting booleans as integers/floats), primary-key uniqueness, within-snapshot join references (`activities.section_id`, non-null `grade_items.course_module_id`, `grades.grade_item_id`, `grades.learner_id`, and `events`/`question_steps` activity and learner references), `[from, to)` timestamp bounds, and `standard_log_available = false => events = []` are enforced prior to any write.
- **Idempotent replay**: Re-importing the exact same logical dataset produces the same `snapshot_id` and exits `0` as a no-op inside the transaction.
- **Non-destructive history**: Importing a later export (for example, after manual grading or regrading) produces a distinct `snapshot_id` and appends new snapshot-scoped rows, preserving earlier grades and log windows for longitudinal comparison.

## Privacy, Security, and Operational Assumptions

- **Local files and restrictive permissions**: Input datasets must be regular local files (symbolic links are rejected). The destination database directory must be private (`0700`, no group/world permission bits), and any existing database or `.wal` file must be a regular private file (`0600`). Process `umask(0077)` is enforced while opening/writing the database and WAL, and pre-existing database files are never deleted on validation or runtime failure.
- **No payload SQL or data leaks**: All inserts use parameterized queries in a single transaction, no DuckDB network extensions are loaded, and validation errors never echo course names or learner data.
- **Single Moodle source per database**: Because v1 exports do not include a Moodle site identifier, all numeric IDs (`course_id`, `course_module_id`, `learner_id`, `grade_item_id`, `event_id`, `question_attempt_id`) are scoped to a single Moodle instance. Use one DuckDB database file per Moodle source site.
- **Single-writer model**: DuckDB enforces single-writer file locking; run one `analytics.import_snapshot` writer process against a database file at a time.
