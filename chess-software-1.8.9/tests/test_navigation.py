"""Verify navigation against source fixtures rather than importing the app."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import navigate


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / 'python').mkdir()
        (self.root / 'python/demo.py').write_text('''raise RuntimeError("must never execute")
class First:
    @staticmethod
    def run():
        return target()
    async def stream(self):
        def inner():
            return target()
        return inner()
class Second:
    def run(self):
        return 0

def target():
    return 1
''')
        self.symbols = navigate.index(self.root)

    def test_no_execution_and_nested_async_decorators(self):
        item = navigate.resolve(self.symbols, 'First.run')
        self.assertEqual(item.key, 'demo.First.run')
        self.assertEqual(item.start, 3)  # decorator included
        self.assertEqual(navigate.resolve(self.symbols, 'stream').key, 'demo.First.stream')
        self.assertEqual(navigate.resolve(self.symbols, 'inner').key, 'demo.First.stream.inner')

    def test_ambiguity_and_missing_are_errors(self):
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            navigate.resolve(self.symbols, 'run')
        with self.assertRaisesRegex(ValueError, 'Unknown'):
            navigate.resolve(self.symbols, 'absent')

    def test_callers_assign_nested_calls_to_correct_owner_once(self):
        rows = list(navigate.callers(self.symbols, navigate.resolve(self.symbols, 'target')))
        self.assertEqual([(line, owner) for _, line, owner in rows],
                         [(5, 'demo.First.run'), (8, 'demo.First.stream.inner')])

    def test_show_is_bounded_and_offset_continues(self):
        with patch.object(navigate, 'ROOT', self.root), patch.object(navigate, 'index', return_value=self.symbols):
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                navigate.main(['show', 'demo.First', '--limit', '3'])
            text = buffer.getvalue()
            self.assertIn('Continues: show demo.First --offset 3', text)
            self.assertNotIn('return target()', text)
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                navigate.main(['show', 'demo.First', '--offset', '3', '--limit', '1'])
            self.assertIn('5:         return target()', buffer.getvalue())

    def test_real_feature_map_stays_valid(self):
        self.assertTrue(navigate.check_features(navigate.index()))

    def test_bad_source_is_reported(self):
        (self.root / 'python/broken.py').write_text('def broken(')
        with self.assertRaises(SyntaxError):
            navigate.index(self.root)


if __name__ == '__main__':
    unittest.main()
