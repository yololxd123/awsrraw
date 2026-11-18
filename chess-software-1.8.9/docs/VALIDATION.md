# Validation commands and limits

Use the same Python interpreter as the app. All paths are relative to the bundle
root; tools also work when invoked by absolute path from another directory.

## Fast default (standard library only)

```sh
python tools/check.py
```

Parses every Python file without importing/running the app, checks all feature
anchors, then runs unit tests for navigation, timer isolation, strict selection,
UCI stop/configure/ready ordering, option validation and atomic settings snapshots.
No display, engine, provider credential or package installation needed for this
layer. It does **not** verify every branch of the application.

## Affected features / optional groups

```sh
python tools/check.py --feature accuracy
python tools/check.py --feature streamproof
python tools/check.py --feature bridge
python tools/check.py --feature analysis --engine /path/to/stockfish
python tools/check.py --all --engine /path/to/stockfish
```

| Group | Requirements | What it exercises |
|---|---|---|
| `core` | Standard library | Default checks above |
| `selection` | Existing desktop imports plus `chess`; no display | Production selector, opening/book score boundaries, both sides, 50–85/75 across five modes, strength/range, cache, mate/readiness, logs/render/AutoMove agreement |
| `streamproof` | Existing app imports, Tk and a display | Real Tk window lifecycle, **mocked Win32** affinity/click-through; enabled startup, both toggle states, restore/rebuild, retry |
| `ui` | Existing app imports, Tk and display | Native sidebar/cards at840/1120/1600, callbacks/settings migration and roundtrip, no-search policy edits, timer display; no screenshots generated |
| `js` | Node.js on PATH | Extension JavaScript syntax only |
| `engine` | Existing app imports, `chess`, Tk/display, local Stockfish-compatible UCI executable via `--engine` | Real depth12/eight candidates, temporary synthetic Polyglot, one-search completion, legal roots, selected move consistency and timer |

`--group <name>` selects one group plus core. `--all` needs **all** listed
prerequisites. A missing dependency/display/engine is a failure or blocked result,
never a silently skipped pass. Do not use `-O` or `PYTHONOPTIMIZE`; application
regression scripts use assertions and the runner rejects optimized mode.

On headless Linux, use an existing Xvfb installation, for example:

```sh
xvfb-run -a -s '-screen 0 1680x1000x24' python tools/check.py --group ui
```

The panel width check needs a screen at least1600 pixels wide. On Windows use a
normal desktop session. Existing app imports include Tk, requests, websockets and
keyboard; selection/engine checks also require the already-supported `chess`
module. No new runtime package is introduced by these tools. The ported checks use
temporary settings/books and suppress screen detector/global hotkey/socket startup.
They do not need customer data or provider credentials. GUI checks create local Tk
windows; run them outside an active app session.

## Choose coverage by the changed boundary

Feature mapping is a starting point, not a complete impact detector. Run additional
affected feature groups when a patch crosses ownership boundaries. For example,
settings + recommendation edits need both settings/UI and selection checks. Engine
parser/depth/cache changes need the real engine group and targeted completion,
under-depth/node-limit/stale-position/check-evasion cases relevant to the change.
The shipped engine smoke is not exhaustive coverage of those cases.

When adding a feature, add meaningful checks and update `docs/features.json`. A
valid symbol map proves names exist, not that current behavior/docs are correct.
Avoid rerunning equivalent completed checks unless their relevant inputs changed.

## Checks requiring the actual platform / service

- Windows capture: streamproof ON/OFF, arrows + visualizer + GUI in actual screen
  capture, including restore/rebuild. Linux/mocked API tests cannot establish this.
- Browser bridge: actual content script startup, FEN/orientation/context ordering,
  reconnect/stale snapshots on intended sites. Node syntax is insufficient.
- Engine performance/playing strength: real engine/hardware and representative
  FENs/PGNs. Depth and local accuracy do not establish Elo or remote-site labels.
- Native Windows sizing/transparency/input behavior remains a separate smoke check.

## Package a release

```sh
python tools/package.py --output /path/to/chess-software.zip
```

Reads `python/version.py` without importing the app. Uses explicit source patterns,
CRC/exact-byte verification and stable ZIP timestamps; excludes local settings,
engines, books and bytecode. Add new asset types/subdirectories to `public_files`
when introducing them. This packaging step does not run or replace validation.

## Recorded baseline for this bundle

Verified in the delivery sandbox with Python3.14.4, Node24.21.0, Linux/Xvfb and
Stockfish19 universal. All selected local groups passed; see `RELEASE_NOTES.md`.
This is historical evidence, not proof of installed dependencies on another
machine. No live website/provider or Windows capture result is claimed.
