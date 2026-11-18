#!/usr/bin/env python3
"""Build a source ZIP from explicit public file types; excludes local settings/caches."""
import argparse
import ast
from pathlib import Path
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def version(root):
    tree = ast.parse((root / 'python/version.py').read_text(encoding='utf-8'))
    values = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == 'APP_VERSION' for target in node.targets)]
    if len(values) != 1 or not isinstance(values[0], str) or not all(part.isdigit() for part in values[0].split('.')):
        raise ValueError('Expected one numeric dotted APP_VERSION string')
    return values[0]


def public_files(root):
    paths = [root / name for name in ['README.md', 'AGENTS.md', 'INSTALL.txt']]
    if (root / '.gitignore').is_file():
        paths.append(root / '.gitignore')
    for directory, patterns in {
        'python': ['*.py', 'READ_ME.txt', 'settings.example.json'],
        'extension': ['*.js', '*.html', 'manifest.json', 'INSTALL.txt'],
        'tools': ['*.py'], 'tests': ['*.py'], 'checks': ['*.py', '*.js'],
        'docs': ['*.md', 'features.json'],
    }.items():
        for pattern in patterns:
            paths.extend((root / directory).glob(pattern))
    for path in sorted(set(paths)):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Missing regular source file: {path.relative_to(root)}')
        yield path


def build(root, output):
    output = Path(output).resolve()
    prefix = f'chess-software-{version(root)}'
    entries = {f'{prefix}/{p.relative_to(root).as_posix()}': p.read_bytes() for p in public_files(root)}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, suffix='.zip', delete=False) as stream:
            temporary = Path(stream.name)
        with zipfile.ZipFile(temporary, 'w') as archive:
            for name, data in entries.items():
                info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data)
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None or set(archive.namelist()) != set(entries):
                raise ValueError('ZIP integrity mismatch')
            for name, data in entries.items():
                if archive.read(name) != data:
                    raise ValueError(f'ZIP content mismatch: {name}')
        temporary.replace(output)
        return len(entries)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path, help='Destination ZIP path')
    args = parser.parse_args()
    print(f'PASS packaged {build(ROOT, args.output)} exact source entries: {args.output}')


if __name__ == '__main__':
    main()
