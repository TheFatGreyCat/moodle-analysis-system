<?php
/** External function declaration for the quiz exporter. */

defined('MOODLE_INTERNAL') || die();

$functions = [
    'local_quizexport_get_attempts' => [
        'classname' => 'local_quizexport\\external\\get_attempts',
        'description' => 'Read a learner’s quiz attempts and question marks.',
        'type' => 'read',
        'capabilities' => 'local/quizexport:export,mod/quiz:viewreports',
    ],
];
