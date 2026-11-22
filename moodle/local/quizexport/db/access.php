<?php
/** Quiz export capability; no role receives it by default. */

defined('MOODLE_INTERNAL') || die();

$capabilities = [
    'local/quizexport:export' => [
        'riskbitmask' => RISK_PERSONAL,
        'captype' => 'read',
        'contextlevel' => CONTEXT_MODULE,
        'archetypes' => [],
    ],
    'local/quizexport:exportcourse' => [
        'riskbitmask' => RISK_PERSONAL,
        'captype' => 'read',
        'contextlevel' => CONTEXT_COURSE,
        'archetypes' => [],
    ],
];
