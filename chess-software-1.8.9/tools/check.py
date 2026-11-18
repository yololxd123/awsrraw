#!/usr/bin/env python3
"""Small, explicit check groups; use the app's Python interpreter."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
GROUPS = {
    'core': [['-m', 'unittest', 'discover', '-s', 'tests', '-v']],
    'selection': [['checks/openings.py'], ['checks/strength.py'], ['checks/modes_automove.py']],
    'streamproof': [['checks/streamproof.py']],
    'ui': [['checks/panel.py']],
    'js': [],
    'engine': [],
}


def run(command):
    print('+ ' + ' '.join(command), flush=True)
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument('--feature', choices=list(json.loads((ROOT / 'docs/features.json').read_text())))
    choice.add_argument('--group', choices=list(GROUPS))
    choice.add_argument('--all', action='store_true')
    parser.add_argument('--engine', type=Path, help='Stockfish-compatible executable for the engine group')
    args = parser.parse_args(argv)
    engine = args.engine.expanduser().resolve() if args.engine else None
    if sys.flags.optimize:
        parser.error('Run without -O: portable regression scripts use assertions')
    groups = list(GROUPS) if args.all else ['core']
    if args.group:
        groups = ['core'] if args.group == 'core' else ['core', args.group]
    if args.feature:
        groups = json.loads((ROOT / 'docs/features.json').read_text())[args.feature]['checks']
    env_optimize = os.environ.get('PYTHONOPTIMIZE')
    if env_optimize and env_optimize != '0':
        parser.error('Unset PYTHONOPTIMIZE so child assertions remain enabled')
    try:
        files = sorted(p for folder in ['python', 'tools', 'tests', 'checks']
                       for p in (ROOT / folder).rglob('*.py'))
        for path in files:
            compile(path.read_text(encoding='utf-8-sig'), str(path.relative_to(ROOT)), 'exec')
        print(f'PASS syntax: {len(files)} Python files (no app imports)', flush=True)
    except (OSError, SyntaxError) as exc:
        print(f'FAIL syntax: {exc}', file=sys.stderr)
        return 1
    result = run([sys.executable, 'tools/navigate.py', 'check-map'])
    if result:
        return result
    for group in dict.fromkeys(groups):
        if group == 'engine':
            if engine is None or not engine.is_file():
                print('BLOCKED engine: pass --engine PATH to a local Stockfish-compatible executable', file=sys.stderr)
                return 2
            commands = [[sys.executable, 'checks/engine_integration.py', '--engine', str(engine)],
                        [sys.executable, 'checks/brilliant_engine.py', '--engine', str(engine)]]
        elif group == 'js':
            node = shutil.which('node')
            if node is None:
                print('BLOCKED js: Node.js required for extension syntax checks', file=sys.stderr)
                return 2
            commands = [[node, '--check', str(p.relative_to(ROOT))] for p in sorted((ROOT / 'extension').glob('*.js'))]
            commands.append([node, 'checks/promotion.js'])
        else:
            commands = [[sys.executable] + command for command in GROUPS[group]]
        for command in commands:
            result = run(command)
            if result:
                print(f'FAIL/BLOCKED {group}: command exited {result}; see output above', file=sys.stderr)
                return result
        print(f'PASS group: {group}', flush=True)
    print('Selected checks passed. Platform/live-service checks beyond these groups remain separate.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
