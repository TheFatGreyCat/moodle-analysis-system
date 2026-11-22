<?php
/** Export a course to three private JSON files under Moodle dataroot. */

define('CLI_SCRIPT', true);

require __DIR__ . '/../../../config.php';
require_once($CFG->libdir . '/clilib.php');

[$options, $unknown] = cli_get_params([
    'courseid' => null, 'as-user' => null, 'from' => null, 'to' => null, 'help' => false,
]);
if ($unknown) {
    cli_error('Unknown CLI options. Use --help.');
}
if ($options['help']) {
    cli_writeln('Usage: php local/quizexport/cli/export_course.php --courseid=ID --as-user=ID --from=UTC_UNIX [--to=UTC_UNIX]');
    cli_writeln('The log interval is [from,to); to defaults to the current Unix second. The three JSON files stay in dataroot.');
    exit(0);
}

function local_quizexport_cli_int($value, string $option, int $minimum = 1): int {
    $parsed = filter_var($value, FILTER_VALIDATE_INT, ['options' => ['min_range' => $minimum]]);
    if ($parsed === false) {
        cli_error("--$option must be an integer >= $minimum.");
    }
    return $parsed;
}

/** Stage the dataset files privately and publish the directory only when all writers succeed. */
function local_quizexport_cli_write(string $root, int $courseid, array $datasets): string {
    if ((!is_dir($root) && !mkdir($root, 0700) && !is_dir($root)) || is_link($root)
            || (fileperms($root) & 0777) !== 0700) {
        throw new RuntimeException('The local export directory must have permissions 0700.');
    }
    $suffix = bin2hex(random_bytes(12));
    $stage = $root . '/.staging-' . $suffix;
    $output = $root . '/course-' . $courseid . '-' . $suffix;
    if (!mkdir($stage, 0700)) {
        throw new RuntimeException('Could not create the local staging directory.');
    }
    try {
        foreach ($datasets as $name => $dataset) {
            $json = json_encode($dataset, JSON_THROW_ON_ERROR | JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT) . "\n";
            if (file_put_contents($stage . '/' . $name, $json) !== strlen($json)) {
                throw new RuntimeException('Could not write the complete local export.');
            }
        }
        if (!rename($stage, $output)) {
            throw new RuntimeException('Could not publish the local export.');
        }
    } finally {
        if (is_dir($stage)) {
            foreach (array_keys($datasets) as $name) {
                $file = $stage . '/' . $name;
                if (is_file($file)) {
                    unlink($file);
                }
            }
            rmdir($stage);
        }
    }
    return $output;
}

$courseid = local_quizexport_cli_int($options['courseid'], 'courseid');
$userid = local_quizexport_cli_int($options['as-user'], 'as-user');
$from = local_quizexport_cli_int($options['from'], 'from', 0);
$to = $options['to'] === null ? time() : local_quizexport_cli_int($options['to'], 'to');
if ($from >= $to) {
    cli_error('--from must precede --to.');
}

try {
    $user = core_user::get_user($userid, '*', MUST_EXIST);
    core_user::require_active_user($user, true, true);
    if (isguestuser($user) || is_siteadmin($user)) {
        throw new RuntimeException('Select a non-admin Moodle account with explicit course export permissions.');
    }
    \core\session\manager::set_user($user);
    $export = new \local_quizexport\course_export($courseid);
    $catalog = $export->catalog();
    $grades = $export->gradebook();
    $logs = $export->submission_logs($from, $to);
    $metadata = ['schema_version' => 1, 'course_id' => $courseid, 'observed_at' => time()];
    $mask = umask(0077);
    try {
        $directory = local_quizexport_cli_write($CFG->dataroot . '/quizexport', $courseid, [
            'course.json' => $metadata + $catalog,
            'gradebook.json' => $metadata + $grades,
            'submission-logs.json' => $metadata + $logs,
        ]);
    } finally {
        umask($mask);
    }
    cli_writeln($directory);
} catch (Exception $error) {
    cli_error($error->getMessage());
}
