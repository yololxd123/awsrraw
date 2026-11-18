import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import package


class PackageTests(unittest.TestCase):
    def test_reproducible_source_only_archive_excludes_local_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for folder in ['python', 'extension', 'tools', 'tests', 'checks', 'docs']:
                (root / folder).mkdir()
            for name in ['README.md', 'AGENTS.md', 'INSTALL.txt', '.gitignore']:
                (root / name).write_text('fixture')
            (root / 'python/version.py').write_text('APP_VERSION = "1.2.3"\n')
            for name in ['settings.json', 'coach_settings.json', 'private.txt', 'engine.exe', 'book.bin']:
                (root / 'python' / name).write_text('local only')
            (root / 'python/__pycache__').mkdir()
            (root / 'python/__pycache__/cache.pyc').write_bytes(b'cache')
            output = root / 'source.zip'
            self.assertEqual(package.build(root, output), 5)
            first = hashlib.sha256(output.read_bytes()).hexdigest()
            package.build(root, output)
            self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), first)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.read('chess-software-1.2.3/python/version.py'),
                                 b'APP_VERSION = "1.2.3"\n')
                self.assertFalse(any('settings' in name or '__pycache__' in name for name in archive.namelist()))

    def test_missing_required_source_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, 'Missing regular source file'):
                list(package.public_files(root))


if __name__ == '__main__':
    unittest.main()
