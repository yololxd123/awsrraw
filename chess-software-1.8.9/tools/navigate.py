#!/usr/bin/env python3
"""Bounded, import-free navigation of the current source (standard library only)."""
import argparse
import ast
from dataclasses import dataclass
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Symbol:
    key: str
    path: str
    name: str
    start: int
    end: int
    doc: str
    node: ast.AST


def index(root=ROOT):
    """Parse source without executing it. Rebuilt each run, so line refs stay current."""
    symbols = []
    for path in sorted((root / 'python').glob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
        relative = path.relative_to(root).as_posix()
        def visit(node, parents):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    names = parents + [child.name]
                    start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                    symbols.append(Symbol(path.stem + '.' + '.'.join(names), relative,
                                          child.name, start, child.end_lineno,
                                          ast.get_docstring(child) or '', child))
                    visit(child, names)
                else:
                    visit(child, parents)
        visit(tree, [])
    return symbols


def resolve(symbols, query):
    exact = [s for s in symbols if s.key == query]
    matches = exact or [s for s in symbols if s.name == query or s.key.endswith('.' + query)]
    if len(matches) != 1:
        examples = ', '.join(s.key for s in matches[:8])
        raise ValueError(f"{'Ambiguous' if matches else 'Unknown'} symbol: {query}" +
                         (f' ({examples})' if examples else ''))
    return matches[0]


def features(root=ROOT):
    return json.loads((root / 'docs/features.json').read_text(encoding='utf-8'))


def check_features(symbols, root=ROOT):
    for name, feature in features(root).items():
        for anchor in feature['symbols']:
            resolve(symbols, anchor)
        for path in feature['files']:
            if not (root / path).is_file():
                raise ValueError(f'{name}: missing file {path}')
    return True


def label(symbol):
    return f'{symbol.path}:{symbol.start}-{symbol.end}  {symbol.key}'


def callers(symbols, target):
    """Lexical call-site candidates, not a resolved dynamic Python call graph."""
    seen = set()
    for owner in symbols:
        if not isinstance(owner.node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # Nested functions have their own owner; do not count their calls twice.
        def walk(node):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                yield child
                yield from walk(child)
        for node in walk(owner.node):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
            key = (owner.path, node.lineno, owner.key)
            if name == target.name and key not in seen:
                seen.add(key)
                yield key


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('summary', help='Small module/size overview')
    commands.add_parser('check-map', help='Verify feature anchors against current code')
    feature = commands.add_parser('feature', help='Feature owners and matching check command')
    feature.add_argument('name', nargs='?')
    find = commands.add_parser('find', help='Find symbols by name, path, or docstring')
    find.add_argument('query')
    find.add_argument('--limit', type=int, default=20)
    show = commands.add_parser('show', help='Print one symbol, with bounded source output')
    show.add_argument('symbol')
    show.add_argument('--offset', type=int, default=0, help='Lines from the start of the symbol')
    show.add_argument('--limit', type=int, default=80)
    calls = commands.add_parser('callers', help='Lexical call candidates; callbacks may be absent')
    calls.add_argument('symbol')
    calls.add_argument('--limit', type=int, default=20)
    args = parser.parse_args(argv)
    if hasattr(args, 'limit') and not 1 <= args.limit <= 300:
        parser.error('--limit must be 1..300')
    if hasattr(args, 'offset') and args.offset < 0:
        parser.error('--offset must be nonnegative')
    try:
        symbols = index()
        if args.command == 'check-map':
            check_features(symbols)
            print(f'PASS {len(features())} feature maps; source anchors and files exist')
        elif args.command == 'summary':
            for path in sorted((ROOT / 'python').glob('*.py')):
                count = sum(s.path == 'python/' + path.name for s in symbols)
                print(f'python/{path.name}: {len(path.read_text(encoding="utf-8-sig").splitlines())} lines, {count} symbols')
            print('Extension: extension/manifest.json -> background.js + content scripts; see feature bridge')
        elif args.command == 'feature':
            catalog = features()
            if not args.name:
                for name, item in catalog.items():
                    print(f'{name}: {item["summary"]}')
            else:
                if args.name not in catalog:
                    raise ValueError('Unknown feature; run feature without a name for the list')
                item = catalog[args.name]
                print(f'{args.name}: {item["summary"]}\n{item["contract"]}')
                for anchor in item['symbols']:
                    print(label(resolve(symbols, anchor)))
                for path in item['files']:
                    print(path)
                print(f'Checks: python tools/check.py --feature {args.name}')
                print('See docs/BEHAVIOR.md and docs/VALIDATION.md for boundaries and runtime requirements.')
        elif args.command == 'find':
            query = args.query.casefold()
            matches = [s for s in symbols if query in (s.key + ' ' + s.path + ' ' + s.doc).casefold()]
            matches.sort(key=lambda s: (s.name.casefold() != query, query not in s.key.casefold(), s.path, s.start))
            for item in matches[:args.limit]:
                print(label(item))
            print(f'{len(matches)} matches; showing {min(args.limit, len(matches))}')
        elif args.command == 'show':
            item = resolve(symbols, args.symbol)
            print(label(item))
            lines = (ROOT / item.path).read_text(encoding='utf-8-sig').splitlines()
            start = item.start - 1 + args.offset
            if start >= item.end:
                raise ValueError('Offset is beyond the end of the symbol')
            end = min(item.end, start + args.limit)
            for i in range(start, end):
                print(f'{i+1:5}: {lines[i]}')
            if end < item.end:
                print(f'Continues: show {item.key} --offset {end-item.start+1} --limit {args.limit}')
        elif args.command == 'callers':
            target = resolve(symbols, args.symbol)
            rows = sorted(callers(symbols, target))
            print('Function-body lexical candidates only; module calls, dynamic dispatch, callback references and JS are not resolved.')
            for path, line, owner in rows[:args.limit]:
                print(f'{path}:{line}  {owner}')
            print(f'{len(rows)} call sites; showing {min(args.limit, len(rows))}')
    except (ValueError, OSError, SyntaxError) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
