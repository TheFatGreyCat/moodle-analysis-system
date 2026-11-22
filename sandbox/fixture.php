<?php
// CLI fixture for the isolated Moodle sandbox only.
define('CLI_SCRIPT', true);

use core\session\manager;
use core_external\util;
use mod_quiz\quiz_attempt;

require '/var/www/html/config.php';
require_once($CFG->dirroot . '/lib/testing/generator/lib.php');
require_once($CFG->dirroot . '/mod/quiz/locallib.php');
require_once($CFG->dirroot . '/webservice/lib.php');
require_once($CFG->dirroot . '/mod/assign/locallib.php');

if ($CFG->wwwroot !== 'http://127.0.0.1:18080' || $CFG->dbhost !== 'moodle-analysis-db') {
    throw new RuntimeException('Refusing to seed a site other than the quiz export sandbox.');
}

manager::set_user(get_admin());
$fixture = $DB->get_record('course', ['shortname' => 'quiz-export-fixture']);
if (($argv[1] ?? '') === 'grade') {
    $id = filter_var($argv[2] ?? '', FILTER_VALIDATE_INT, ['options' => ['min_range' => 1]]);
    if (!$fixture || $id === false) {
        throw new RuntimeException('Expected the fixture and a valid attempt ID.');
    }
    $attempt = quiz_attempt::create($id);
    if ((int) $attempt->get_courseid() !== (int) $fixture->id) {
        throw new RuntimeException('The attempt is outside the fixture course.');
    }
    $question = $attempt->get_question_attempt(3);
    $attempt->process_submitted_actions(time(), false, [
        'slots' => '3',
        $question->get_control_field_name('sequencecheck') => (string) $question->get_sequence_check_count(),
        $question->get_behaviour_field_name('comment') => 'Synthetic manual grade',
        $question->get_behaviour_field_name('comment') . ':itemid' => file_get_unused_draft_itemid(),
        $question->get_behaviour_field_name('commentformat') => FORMAT_PLAIN,
        $question->get_behaviour_field_name('mark') => '0.375',
        $question->get_behaviour_field_name('maxmark') => '1',
    ]);
    exit;
}

$run = bin2hex(random_bytes(5));
$generator = new testing_data_generator();
$quizzes = $generator->get_plugin_generator('mod_quiz');
$questions = $generator->get_plugin_generator('core_question');
$wholecourse = ($argv[1] ?? '') === 'course-export';
$from = time();
$course = $wholecourse ? $generator->create_course([
    'shortname' => 'course-export-' . $run, 'fullname' => 'Synthetic Course Export', 'groupmode' => SEPARATEGROUPS,
]) : ($fixture ?: $generator->create_course(['shortname' => 'quiz-export-fixture', 'fullname' => 'Synthetic Python']));
$outside = $generator->create_course(['shortname' => 'quiz-export-outside-' . $run]);
$makeuser = static fn(string $name) => $generator->create_user([
    'username' => 'quiz-export-' . $name . '-' . $run,
    'firstname' => 'Synthetic',
    'lastname' => $name,
    'email' => $name . '-' . $run . '@example.invalid',
]);
$learner = $makeuser('learner');
$other = $makeuser('othergroup');
$reader = $makeuser('reader');
$restricted = $makeuser('restricted');
$nograde = $wholecourse ? $makeuser('nograde') : null;
$role = $DB->get_field('role', 'id', ['shortname' => 'quiz_export_reader']);
if (!$role) {
    $role = create_role('Quiz Export Reader', 'quiz_export_reader', 'Read synthetic quiz results');
    foreach (['moodle/course:view', 'mod/quiz:view', 'mod/quiz:viewreports'] as $capability) {
        assign_capability($capability, CAP_ALLOW, $role, context_system::instance()->id);
    }
}
$protocolrole = $DB->get_field('role', 'id', ['shortname' => 'quiz_export_rest']);
if (!$protocolrole) {
    $protocolrole = create_role('Quiz Export REST', 'quiz_export_rest', 'Use REST without site-wide data rights');
    assign_capability('webservice/rest:use', CAP_ALLOW, $protocolrole, context_system::instance()->id);
}
foreach ([$reader, $restricted] as $account) {
    role_assign($protocolrole, $account->id, context_system::instance()->id);
    $generator->enrol_user($account->id, $course->id, $role);
}
foreach ([$learner, $other] as $account) {
    $generator->enrol_user($account->id, $course->id, 'student');
}
$group = $generator->create_group(['courseid' => $course->id]);
$othergroup = $generator->create_group(['courseid' => $course->id]);
foreach ([$learner, $reader, $restricted] as $account) {
    groups_add_member($group->id, $account->id);
}
groups_add_member($othergroup->id, $other->id);
if ($wholecourse) {
    $generator->enrol_user($nograde->id, $course->id, 'student');
    groups_add_member($group->id, $nograde->id);
}
$quiz = $quizzes->create_instance([
    'course' => $course->id, 'name' => 'Prerequisites', 'groupmode' => SEPARATEGROUPS,
    'grade' => 100, 'grademethod' => QUIZ_GRADEHIGHEST, 'shuffleanswers' => 0,
]);
$outsidequiz = $quizzes->create_instance(['course' => $outside->id, 'name' => 'Outside scope']);
$fixed = $questions->create_question_category(['contextid' => context_course::instance($course->id)->id]);
$random = $questions->create_question_category(['contextid' => context_course::instance($course->id)->id]);
$makequestion = static function (string $type, int $category, string $name): stdClass {
    $empty = ['text' => '', 'format' => FORMAT_HTML];
    $form = (object) [
        'category' => $category, 'name' => $name, 'defaultmark' => 1,
        'questiontext' => ['text' => $name, 'format' => FORMAT_HTML], 'generalfeedback' => $empty,
        'penalty' => 0, 'status' => \core_question\local\bank\question_version_status::QUESTION_STATUS_READY,
    ];
    if ($type === 'shortanswer') {
        $form->usecase = 0;
        $form->answer = ['frog', 'toad', '*'];
        $form->fraction = [1, 0.8, 0];
        $form->feedback = [$empty, $empty, $empty];
    } else {
        $form->responseformat = 'editor';
        $form->responserequired = 1;
        $form->responsefieldlines = 10;
        $form->attachments = 0;
        $form->attachmentsrequired = 0;
        $form->maxbytes = 0;
        $form->filetypeslist = '';
        $form->graderinfo = $empty;
        $form->responsetemplate = $empty;
    }
    return question_bank::get_qtype($type)->save_question((object) ['qtype' => $type], $form);
};
$auto = $makequestion('shortanswer', $fixed->id, 'Auto');
$blank = $makequestion('shortanswer', $fixed->id, 'Blank');
$essay = $makequestion('essay', $fixed->id, 'Manual');
$choicea = $makequestion('shortanswer', $random->id, 'Random A');
$choiceb = $makequestion('shortanswer', $random->id, 'Random B');
foreach ([$auto, $blank, $essay] as $question) {
    quiz_add_quiz_question($question->id, $quiz, 0, 1);
}
quiz_add_random_questions($quiz, 0, $random->id, 1);
\mod_quiz\quiz_settings::create($quiz->id)->get_grade_calculator()->recompute_quiz_sumgrades();
$submit = static function (int $id, array $responses) use ($questions): void {
    $attempt = quiz_attempt::create($id);
    $postdata = ['slots' => implode(',', array_keys($responses))];
    foreach ($responses as $slot => $response) {
        $postdata += $questions->get_simulated_post_data_for_question_attempt(
            $attempt->get_question_attempt($slot), $response, false
        );
    }
    $attempt->process_submitted_actions(time(), false, $postdata);
    $attempt->process_finish(time(), false);
};
manager::set_user($learner);
$first = $quizzes->create_attempt($quiz->id, $learner->id);
$submit($first->id, [1 => 'wrong', 3 => 'Synthetic answer', 4 => 'toad']);
$second = $quizzes->create_attempt($quiz->id, $learner->id);
$submit($second->id, [1 => 'frog', 2 => 'frog', 4 => 'frog']);
$third = $quizzes->create_attempt($quiz->id, $learner->id);
manager::set_user($other);
$quizzes->create_attempt($quiz->id, $other->id);
manager::set_user(get_admin());
if ($wholecourse) {
    $section = $generator->create_course_section(['course' => $course->id, 'section' => 1]);
    course_update_section($course, $section, ['name' => 'Practice']);
    $assignment = $generator->create_module('assign', [
        'course' => $course->id, 'section' => $section->section, 'name' => 'Practice submission',
        'groupmode' => SEPARATEGROUPS, 'grade' => 100, 'assignsubmission_onlinetext_enabled' => 1,
    ]);
    $hidden = $generator->create_module('assign', ['course' => $course->id, 'name' => 'Hidden assignment', 'visible' => 0]);
    $assign = new assign(context_module::instance($assignment->cmid), get_fast_modinfo($course)->get_cm($assignment->cmid), $course);
    $submission = $assign->get_user_submission($learner->id, true);
    manager::set_user($learner);
    \mod_assign\event\assessable_submitted::create_from_submission($assign, $submission, false)->trigger();
    manager::set_user(get_admin());
    $assign->save_grade($learner->id, (object) ['grade' => 0, 'attemptnumber' => -1]);
    grade_regrade_final_grades($course->id);
    get_log_manager()->get_readers();
    $operatorrole = create_role('Synthetic course exporter', 'quiz_course_export_' . $run, 'Restricted to the fixture course');
    $context = context_course::instance($course->id);
    foreach (['moodle/course:viewparticipants', 'moodle/grade:viewall', 'moodle/grade:viewhidden',
              'report/log:view', 'mod/assign:view', 'mod/assign:grade'] as $capability) {
        assign_capability($capability, CAP_ALLOW, $operatorrole, $context->id);
    }
    if (get_capability_info('local/quizexport:exportcourse')) {
        assign_capability('local/quizexport:exportcourse', CAP_ALLOW, $operatorrole, $context->id);
    }
    role_assign($operatorrole, $reader->id, $context->id);
    echo json_encode([
        'course' => (int) $course->id, 'quiz' => (int) $quiz->id, 'assignment' => (int) $assignment->id,
        'hidden_assignment' => (int) $hidden->id, 'learner' => (int) $learner->id, 'no_grade' => (int) $nograde->id,
        'other' => (int) $other->id, 'reader' => (int) $reader->id, 'restricted' => (int) $restricted->id,
        'attempts' => [(int) $first->id, (int) $second->id, (int) $third->id], 'from' => $from,
    ], JSON_THROW_ON_ERROR);
    exit;
}

set_config('enablewebservices', 1);
set_config('webserviceprotocols', 'rest');
$webservices = new webservice();
$serviceid = $webservices->add_external_service((object) [
    'name' => 'Synthetic quiz export ' . $run, 'shortname' => 'quizexport_' . $run,
    'enabled' => 1, 'restrictedusers' => 1,
]);
$webservices->add_external_function_to_service('local_quizexport_get_attempts', $serviceid);
$service = $webservices->get_external_service_by_shortname('quizexport_' . $run, MUST_EXIST);
$tokens = [];
foreach (['reader' => $reader, 'restricted' => $restricted] as $key => $account) {
    $webservices->add_ws_authorised_user((object) ['externalserviceid' => $serviceid, 'userid' => $account->id]);
    $tokens[$key] = util::generate_token(EXTERNAL_TOKEN_PERMANENT, $service, $account->id, context_system::instance(), time() + 3600);
}
if (get_capability_info('local/quizexport:export')) {
    $exportrole = $DB->get_field('role', 'id', ['shortname' => 'quiz_export_allowed']);
    if (!$exportrole) {
        $exportrole = create_role('Allow Quiz Export', 'quiz_export_allowed', 'Export approved quiz data');
        assign_capability('local/quizexport:export', CAP_ALLOW, $exportrole, context_system::instance()->id);
    }
    $cm = get_coursemodule_from_instance('quiz', $quiz->id, $course->id, false, MUST_EXIST);
    role_assign($exportrole, $reader->id, context_module::instance($cm->id)->id);
}

echo json_encode([
    'course' => (int) $course->id, 'quiz' => (int) $quiz->id, 'outside_quiz' => (int) $outsidequiz->id,
    'learner' => (int) $learner->id, 'other' => (int) $other->id,
    'attempts' => [(int) $first->id, (int) $second->id, (int) $third->id],
    'fixed_questions' => [(int) $auto->id, (int) $blank->id, (int) $essay->id],
    'random_candidates' => [(int) $choicea->id, (int) $choiceb->id],
    'selected_questions' => array_map(static function (stdClass $attempt): int {
        return (int) quiz_attempt::create($attempt->id)->get_question_attempt(4)->get_question_id();
    }, [$first, $second, $third]),
    'tokens' => $tokens,
], JSON_THROW_ON_ERROR);
