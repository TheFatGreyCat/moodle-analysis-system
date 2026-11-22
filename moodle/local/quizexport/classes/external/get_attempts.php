<?php
/** Read-only quiz attempt export. */

namespace local_quizexport\external;

use context_module;
use core_external\external_api;
use core_external\external_function_parameters;
use core_external\external_multiple_structure;
use core_external\external_single_structure;
use core_external\external_value;
use mod_quiz\access_manager;
use mod_quiz\quiz_attempt;

final class get_attempts extends external_api {
    public static function execute_parameters(): external_function_parameters {
        return new external_function_parameters([
            'quizid' => new external_value(PARAM_INT, 'Quiz instance ID, not course module ID', VALUE_REQUIRED, null, NULL_NOT_ALLOWED),
            'userid' => new external_value(PARAM_INT, 'Learner ID', VALUE_REQUIRED, null, NULL_NOT_ALLOWED),
        ]);
    }

    public static function execute(int $quizid, int $userid): array {
        global $CFG;
        ['quizid' => $quizid, 'userid' => $userid] = self::validate_parameters(self::execute_parameters(), [
            'quizid' => $quizid,
            'userid' => $userid,
        ]);
        if ($quizid < 1 || $userid < 1) {
            throw new \invalid_parameter_exception('Quiz and learner IDs must be positive.');
        }

        require_once($CFG->dirroot . '/mod/quiz/locallib.php');
        [$course, $cm] = get_course_and_cm_from_instance($quizid, 'quiz');
        $context = context_module::instance($cm->id);
        self::validate_context($context);
        require_capability('local/quizexport:export', $context);
        require_capability('mod/quiz:viewreports', $context);
        if (!groups_user_groups_visible($course, $userid, $cm)) {
            throw new \moodle_exception('learnernotvisible', 'local_quizexport');
        }

        $quiz = access_manager::load_quiz_and_settings($quizid);
        $attempts = [];
        foreach (quiz_get_user_attempts($quizid, $userid, 'all') as $record) {
            $attempts[] = self::export_attempt(new quiz_attempt($record, $quiz, $cm, $course));
        }
        return [
            'schema_version' => 1,
            'course_id' => $course->id,
            'quiz_id' => $quizid,
            'learner_id' => $userid,
            'observed_at' => time(),
            'attempts' => $attempts,
        ];
    }

    private static function export_attempt(quiz_attempt $attempt): array {
        $record = $attempt->get_attempt();
        $questions = [];
        $maxmark = 0.0;
        $slots = $attempt->get_slots();
        sort($slots, SORT_NUMERIC);
        foreach ($slots as $slot) {
            if ($attempt->is_real_question($slot)) {
                $question = self::export_question($attempt->get_question_attempt($slot), $slot);
                $questions[] = $question;
                $maxmark += $question['max_mark'];
            }
        }
        return [
            'attempt_id' => $record->id,
            'attempt_number' => $record->attempt,
            'state' => $record->state,
            'started_at' => $record->timestart,
            'submitted_at' => $record->state === quiz_attempt::FINISHED ? $record->timefinish : null,
            'mark' => $record->state === quiz_attempt::FINISHED ? $record->sumgrades : null,
            'max_mark' => $maxmark,
            'questions' => $questions,
        ];
    }

    private static function export_question(\question_attempt $attempt, int $slot): array {
        $definition = $attempt->get_question(false);
        $summary = $attempt->get_response_summary();
        $answered = $summary !== null && $summary !== '';
        if (!$answered && $definition instanceof \question_with_responses) {
            $answered = $definition->is_gradable_response($attempt->get_last_qt_data());
        }
        return [
            'slot' => $slot,
            'question_id' => $attempt->get_question_id(),
            'state' => (string) $attempt->get_state(),
            'response_state' => $answered ? 'answered' : ($definition instanceof \question_with_responses ? 'blank' : 'unknown'),
            'mark' => $attempt->get_mark(),
            'max_mark' => $attempt->get_max_mark(),
            'last_action_at' => $attempt->get_last_action_time(),
        ];
    }

    public static function execute_returns(): external_single_structure {
        $question = new external_single_structure([
            'slot' => new external_value(PARAM_INT, 'Slot in this attempt'),
            'question_id' => new external_value(PARAM_INT, 'Actual question ID, including random selections'),
            'state' => new external_value(PARAM_ALPHA, 'Moodle Question Engine state'),
            'response_state' => new external_value(PARAM_ALPHA, 'answered, blank, or unknown'),
            'mark' => new external_value(PARAM_FLOAT, 'Raw mark, null if not available'),
            'max_mark' => new external_value(PARAM_FLOAT, 'Maximum mark for this slot'),
            'last_action_at' => new external_value(PARAM_INT, 'UTC Unix seconds of the latest question step'),
        ]);
        $attempt = new external_single_structure([
            'attempt_id' => new external_value(PARAM_INT, 'Moodle quiz attempt ID'),
            'attempt_number' => new external_value(PARAM_INT, 'Sequence of learner attempts in this quiz'),
            'state' => new external_value(PARAM_ALPHA, 'Moodle quiz attempt state'),
            'started_at' => new external_value(PARAM_INT, 'UTC Unix seconds at start'),
            'submitted_at' => new external_value(PARAM_INT, 'UTC Unix seconds at submission, or null'),
            'mark' => new external_value(PARAM_FLOAT, 'Raw total mark of the attempt when available'),
            'max_mark' => new external_value(PARAM_FLOAT, 'Maximum total mark on the same scale'),
            'questions' => new external_multiple_structure($question),
        ]);
        return new external_single_structure([
            'schema_version' => new external_value(PARAM_INT, 'Export schema version'),
            'course_id' => new external_value(PARAM_INT, 'Moodle course ID'),
            'quiz_id' => new external_value(PARAM_INT, 'Moodle quiz instance ID'),
            'learner_id' => new external_value(PARAM_INT, 'Moodle learner ID'),
            'observed_at' => new external_value(PARAM_INT, 'UTC Unix seconds when the export completed'),
            'attempts' => new external_multiple_structure($attempt),
        ]);
    }
}
