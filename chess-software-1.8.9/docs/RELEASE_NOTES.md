# 1.8.9 — accuracy targeting and suggestion styles

- Range targeting now defaults on; old strength-first defaults migrate once.
  Explicit strength-first opt-ins remain available and persistent.
- Removed the hidden .50-pawn strict-selection ceiling. The user's visible loss
  cap, draw rules, winning mates and opening/book protection still apply.
- Targets nearest available priority; styles choose within two percentage points
  of that nearest estimate, without leaving an available range or worsening a miss.
- Style logs, arrows and AutoMove share the final suggestion. Mode changes refresh
  the selector cache; late live scores cannot replace the same completed pick.
- Range/priority/style edits reuse the completed search. Default breadth/depth and
  one-search publication remain unchanged. No additional runtime dependency.
- See `ACCURACY_FIX.md` for installation, limitations and future-AI directions.

## Verification

Core tests, production selection scenarios, native Tk/settings controls and real
Stockfish17 depth12/eight-root checks passed in the delivery sandbox. Both colors,
all five modes, protection exceptions, safety on/off, nearest-safe fallback,
cache refresh, policy migration and shared consumers are covered.
Before/after snapshot selection medians:0.316ms /0.317ms. Live website grading and
native Windows capture/input remain unverified; no percentage guarantee is made.

# 1.8.8 — maintenance and AI navigation

## Added

- Root `AGENTS.md` and short README that route future work to the affected feature.
- Ten feature maps, current contracts, architecture/extension flow and handoff guide.
- Standard-library `tools/navigate.py`: live AST symbol locations, bounded function
  source, feature owners and lexical caller candidates. No GUI/provider import.
- `tools/check.py`: explicit core/feature groups, with missing prerequisites reported.
- Portable tests and application checks instead of relying on task-local scripts.
- `tools/package.py`: reproducible source ZIP with CRC/exact-byte verification and
  explicit public source patterns; user settings, binaries and caches excluded.
- One desktop version declaration, `python/version.py` (copy it during installation).
- Compatible extension1.8.3 included unchanged to provide complete source context.

## Scope

Analysis, recommendation, opening, timing and streamproof implementation bytes are
unchanged from1.8.7. The sole existing runtime file edit is control_panel.py reading
its two version labels from version.py. This improves discovery/verification; it
is not an engine-speed or playing-strength update. No measured token-saving promise.

## Verification in this release

-19 standard-library unit tests: navigation, runner selection/failure reporting,
 timer tickets, strict-policy bounds/context, UCI ordering/options, atomic settings,
 packaging inclusion/privacy and deterministic ZIP output.
-10 feature maps checked against current symbols and source files; all Python parsed.
-Production selector/opening/strength scenarios and real Tk/mocked-Win32 streamproof
 lifecycle passed. Actual native capture output remains unverified on Linux.
-Native panel tests: all12tabs at840/1120/1600, callbacks/settings/timing and1.8.8 label.
-Five JavaScript syntax checks passed with Node24.21.0.
-Real Stockfish19 depth12/eight candidates: validated book choices, strongest safe
 post-opening fallback, legal roots, one search per position, consistent renderer/
 AutoMove/reply/PV memory and completed timing.

No live website/provider or actual Windows rendering/capture result is claimed.
