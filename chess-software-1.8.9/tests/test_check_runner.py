"""The selector must run required groups and report unavailable prerequisites."""
import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import check as checker


class CheckRunnerTests(unittest.TestCase):
    def invoke(self, args, results=None):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), patch.object(checker, 'run', side_effect=results or (lambda command: 0)) as run:
            status = checker.main(args)
            commands = [call.args[0] for call in run.call_args_list]
        return status, commands

    def test_default_is_core_without_optional_runtime(self):
        result, commands = self.invoke([])
        self.assertEqual(result, 0)
        self.assertIn('check-map', commands[0])
        self.assertIn('unittest', commands[1])
        self.assertEqual(len(commands), 2)

    def test_accuracy_feature_keeps_core_and_both_policy_scripts(self):
        result, commands = self.invoke(['--feature', 'accuracy'])
        self.assertEqual(result, 0)
        self.assertEqual([command[1] for command in commands[2:]],
                         ['checks/openings.py', 'checks/strength.py', 'checks/modes_automove.py'])

    def test_failure_stops_dependent_checks(self):
        result, commands = self.invoke(['--feature', 'accuracy'], results=[0, 7])
        self.assertEqual(result, 7)
        self.assertEqual(len(commands), 2)

    def test_missing_engine_and_node_are_blocked(self):
        result, _ = self.invoke(['--group', 'engine'])
        self.assertEqual(result, 2)
        with patch.object(checker.shutil, 'which', return_value=None):
            result, _ = self.invoke(['--group', 'js'])
        self.assertEqual(result, 2)


if __name__ == '__main__':
    unittest.main()
