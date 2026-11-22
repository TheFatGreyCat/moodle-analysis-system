<?php
/** Read course metadata, Gradebook and submission history in the CLI operator's Moodle scope. */

namespace local_quizexport;

use context_course;
use context_module;
use mod_quiz\access_manager;
use mod_quiz\quiz_attempt;

final class course_export {
    private const EVENTS = [
        'mod_quiz' => [
            'attempt_started', 'attempt_viewed', 'attempt_updated', 'attempt_autosaved', 'attempt_summary_viewed',
            'attempt_submitted', 'attempt_reviewed', 'attempt_regraded', 'attempt_abandoned', 'attempt_becameoverdue',
            'attempt_reopened', 'attempt_question_restarted', 'attempt_manual_grading_completed', 'question_manually_graded',
        ],
        'mod_assign' => [
            'assessable_submitted', 'submission_status_updated', 'submission_viewed', 'submission_graded',
            'submission_locked', 'submission_unlocked', 'workflow_state_updated',
        ],
        'assignsubmission_file' => ['submission_created', 'submission_updated'],
        'assignsubmission_onlinetext' => ['submission_created', 'submission_updated'],
    ];

    private \stdClass $course;
    /** @var array<int, \cm_info> Visible, existing activities. */
    private array $modules = [];
    /** @var array<int, true> Gradable active users in the operator's course group scope. */
    private array $learners = [];
    /** @var array<int, \assign> Activities with unrevealed blind marking. */
    private array $blindassignments = [];
    private array $catalog;

    public function __construct(int $courseid) {
        global $CFG;
        require_once($CFG->dirroot . '/course/lib.php');
        require_once($CFG->dirroot . '/mod/assign/locallib.php');
        require_once($CFG->dirroot . '/mod/quiz/locallib.php');
        require_once($CFG->libdir . '/gradelib.php');

        $this->course = get_course($courseid);
        $context = context_course::instance($courseid);
        foreach (['local/quizexport:exportcourse', 'moodle/course:viewparticipants', 'moodle/grade:viewall',
                  'moodle/grade:viewhidden', 'report/log:view'] as $capability) {
            require_capability($capability, $context);
        }
        if ($courseid === SITEID || !can_access_course($this->course, null, '', true)) {
            throw new \moodle_exception('courseexportunavailable', 'local_quizexport');
        }
        $this->load_learners($context);
        $this->catalog = $this->load_catalog();
    }

    private function load_learners(context_course $context): void {
        global $CFG, $DB;
        if (empty($CFG->gradebookroles)) {
            return;
        }
        [$enrolled, $params] = get_enrolled_sql($context, '', 0, true);
        [$roles, $roleparams] = $DB->get_in_or_equal(explode(',', $CFG->gradebookroles), SQL_PARAMS_NAMED, 'graderole');
        [$contexts, $contextparams] = $DB->get_in_or_equal($context->get_parent_context_ids(true), SQL_PARAMS_NAMED, 'gradectx');
        $sql = "SELECT u.id FROM {user} u JOIN ($enrolled) e ON e.id = u.id
                 WHERE u.deleted = 0 AND u.suspended = 0 AND EXISTS (
                       SELECT 1 FROM {role_assignments} r
                        WHERE r.userid = u.id AND r.roleid $roles AND r.contextid $contexts)
                 ORDER BY u.id";
        foreach ($DB->get_fieldset_sql($sql, $params + $roleparams + $contextparams) as $id) {
            if (groups_user_groups_visible($this->course, $id)) {
                $this->learners[(int) $id] = true;
            }
        }
    }

    private function load_catalog(): array {
        $modinfo = get_fast_modinfo($this->course);
        $sections = $activities = [];
        foreach ($modinfo->get_section_info_all() as $section) {
            if (!$section->uservisible) {
                continue;
            }
            $sections[] = ['section_id' => (int) $section->id, 'number' => (int) $section->section,
                'name' => get_section_name($this->course, $section)];
            foreach ($modinfo->sections[$section->section] ?? [] as $cmid) {
                $cm = $modinfo->get_cm($cmid);
                if (!$cm->uservisible || $cm->deletioninprogress) {
                    continue;
                }
                $this->modules[$cmid] = $cm;
                if ($cm->modname === 'assign') {
                    $assign = new \assign(context_module::instance($cmid), $cm, $this->course);
                    if ($assign->is_blind_marking()) {
                        $this->blindassignments[$cmid] = $assign;
                    }
                }
                $activities[] = ['course_module_id' => (int) $cmid, 'instance_id' => (int) $cm->instance,
                    'section_id' => (int) $section->id, 'module' => $cm->modname, 'name' => $cm->name];
            }
        }
        return [
            'course' => ['name' => $this->course->fullname, 'short_name' => $this->course->shortname,
                'format' => $this->course->format, 'start_at' => (int) $this->course->startdate,
                'end_at' => $this->course->enddate ? (int) $this->course->enddate : null],
            'sections' => $sections, 'activities' => $activities, 'learner_ids' => array_keys($this->learners),
        ];
    }

    public function catalog(): array {
        return $this->catalog;
    }

    public function gradebook(): array {
        $moduleids = [];
        foreach ($this->modules as $cm) {
            $moduleids[$cm->modname][$cm->instance] = (int) $cm->id;
        }
        $items = $grades = [];
        $records = \grade_item::fetch_all(['courseid' => $this->course->id]) ?: [];
        ksort($records, SORT_NUMERIC);
        foreach ($records as $item) {
            $cmid = $moduleids[$item->itemmodule][$item->iteminstance] ?? null;
            if ($item->itemtype === 'mod' && ($cmid === null || !$this->can_read_activity($this->modules[$cmid]))) {
                continue;
            }
            $items[] = ['grade_item_id' => (int) $item->id, 'course_module_id' => $cmid,
                'category_id' => $item->categoryid === null ? null : (int) $item->categoryid,
                'category_total_for' => $item->itemtype === 'category' ? (int) $item->iteminstance : null,
                'item_type' => $item->itemtype, 'item_number' => $item->itemnumber === null ? null : (int) $item->itemnumber,
                'name' => $item->get_name(), 'grade_type' => (int) $item->gradetype,
                'min_grade' => (float) $item->grademin, 'max_grade' => (float) $item->grademax,
                'scale' => $item->load_scale()?->scale_items, 'hidden' => (bool) $item->is_hidden(),
                'needs_update' => (bool) $item->needsupdate];
            $ids = $cmid === null ? array_keys($this->learners) : $this->allowed_learners($this->modules[$cmid]);
            if (!$ids) {
                continue;
            }
            $entries = \grade_grade::fetch_users_grades($item, $ids, false);
            foreach ($ids as $id) {
                $grade = $entries[$id] ?? null;
                $final = $grade?->finalgrade;
                $grades[] = ['grade_item_id' => (int) $item->id, 'learner_id' => $id,
                    'final_grade' => $final === null ? null : (float) $final,
                    'updated_at' => empty($grade->timemodified) ? null : (int) $grade->timemodified];
            }
        }
        return ['items' => $items, 'grades' => $grades];
    }

    private function can_read_activity(\cm_info $cm): bool {
        $context = context_module::instance($cm->id);
        return match ($cm->modname) {
            'quiz' => has_capability('mod/quiz:viewreports', $context),
            'assign' => (!isset($this->blindassignments[$cm->id]) || has_capability('mod/assign:viewblinddetails', $context))
                && (has_capability('mod/assign:viewgrades', $context) || has_capability('mod/assign:grade', $context)),
            default => true,
        };
    }

    private function allowed_learners(\cm_info $cm): array {
        return array_values(array_filter(array_keys($this->learners),
            fn(int $id): bool => groups_user_groups_visible($this->course, $id, $cm)));
    }

    public function submission_logs(int $from, int $to): array {
        $scope = $steps = [];
        foreach ($this->modules as $cm) {
            if (!in_array($cm->modname, ['quiz', 'assign'], true) || !$this->can_read_activity($cm)) {
                continue;
            }
            $ids = $this->allowed_learners($cm);
            $scope[$cm->id] = array_fill_keys($ids, true);
            if ($cm->modname === 'quiz' && $ids) {
                foreach ($this->quiz_steps($cm, $ids, $from, $to) as $row) {
                    $steps[] = $row;
                }
            }
        }
        $reader = get_log_manager()->get_readers(\core\log\sql_reader::class)['logstore_standard'] ?? null;
        return ['from' => $from, 'to' => $to, 'standard_log_available' => $reader !== null,
            'events' => $reader && $scope ? $this->standard_events($reader, $scope, $from, $to) : [],
            'question_steps' => $steps];
    }

    private function quiz_steps(\cm_info $cm, array $ids, int $from, int $to): \Generator {
        $quiz = access_manager::load_quiz_and_settings($cm->instance);
        foreach ($ids as $id) {
            foreach (quiz_get_user_attempts($cm->instance, $id, 'all') as $record) {
                if ($record->timestart >= $to) {
                    continue;
                }
                $attempt = new quiz_attempt($record, $quiz, $cm, $this->course);
                $slots = $attempt->get_slots();
                sort($slots, SORT_NUMERIC);
                foreach ($slots as $slot) {
                    if (!$attempt->is_real_question($slot)) {
                        continue;
                    }
                    foreach ($attempt->all_question_attempts_originally_in_slot($slot) as $qa) {
                        foreach ($qa->get_step_iterator() as $index => $step) {
                            $time = (int) $step->get_timecreated();
                            if ($time >= $from && $time < $to) {
                                yield ['course_module_id' => (int) $cm->id, 'learner_id' => $id,
                                    'attempt_id' => (int) $record->id, 'slot' => (int) $slot,
                                    'question_attempt_id' => (int) $qa->get_database_id(), 'question_id' => (int) $qa->get_question_id(),
                                    'step_index' => $index, 'occurred_at' => $time, 'state' => (string) $step->get_state()];
                            }
                        }
                    }
                }
            }
        }
    }

    private function standard_events(\core\log\sql_reader $reader, array $scope, int $from, int $to): array {
        global $DB;
        $names = [];
        foreach (self::EVENTS as $component => $events) {
            foreach ($events as $event) {
                $names[] = "\\$component\\event\\$event";
            }
        }
        [$modules, $moduleparams] = $DB->get_in_or_equal(array_keys($scope), SQL_PARAMS_NAMED, 'cm');
        [$events, $eventparams] = $DB->get_in_or_equal($names, SQL_PARAMS_NAMED, 'event');
        $filter = "courseid = :course AND contextlevel = :contextlevel AND contextinstanceid $modules
                   AND anonymous = 0 AND eventname $events AND timecreated >= :starttime AND timecreated < :endtime";
        $params = $moduleparams + $eventparams + ['course' => $this->course->id, 'contextlevel' => CONTEXT_MODULE,
            'starttime' => $from, 'endtime' => $to];
        $result = [];
        $records = $reader->get_events_select_iterator($filter, $params, 'timecreated ASC, id ASC', 0, 0);
        try {
            foreach ($records as $id => $event) {
                if (!$event) {
                    continue;
                }
                $data = $event->get_data();
                $cmid = (int) $data['contextinstanceid'];
                $learner = $this->event_learner($data);
                if (!isset($scope[$cmid][$learner])) {
                    continue;
                }
                $result[] = ['event_id' => (int) $id, 'course_module_id' => $cmid, 'learner_id' => $learner,
                    'event_name' => $data['eventname'], 'occurred_at' => (int) $data['timecreated'],
                    'object_table' => $data['objecttable'], 'object_id' => $data['objectid'] === null ? null : (int) $data['objectid'],
                    'attempt_id' => $data['objecttable'] === 'quiz_attempts' ? (int) $data['objectid']
                        : (isset($data['other']['attemptid']) && $data['component'] === 'mod_quiz' ? (int) $data['other']['attemptid'] : null)];
            }
        } finally {
            $records->close();
        }
        return $result;
    }

    private function event_learner(array $data): int {
        if (!empty($data['relateduserid'])) {
            return (int) $data['relateduserid'];
        }
        if ($data['eventname'] === '\mod_quiz\event\question_manually_graded') {
            global $DB;
            $quizid = $this->modules[$data['contextinstanceid']]->instance;
            return (int) $DB->get_field('quiz_attempts', 'userid', ['id' => (int) ($data['other']['attemptid'] ?? 0), 'quiz' => $quizid]);
        }
        // Assignment submission events may omit relateduserid when the learner is the actor.
        if (in_array($data['component'], ['mod_assign', 'assignsubmission_file', 'assignsubmission_onlinetext'], true)
                && $data['objecttable'] === 'assign_submission') {
            global $DB;
            $assignment = $this->modules[$data['contextinstanceid']]->instance;
            return (int) $DB->get_field('assign_submission', 'userid', ['id' => (int) $data['objectid'], 'assignment' => $assignment]);
        }
        return 0;
    }
}
