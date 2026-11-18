# Accuracy range and suggestion modes — 1.8.9

## Easy directions

1. Close the app. Copy every file from the new `python/` folder into your installed
   Python folder, keeping your existing settings, engines, weights and credentials.
   Run `main.py` normally. No new runtime dependency or extension update is needed.
2. Enable recommendation policy. Set range **50–85%**, priority **75%**. Editing
   either slider turns strength first off. Older saved strength-first defaults
   migrate off once; a new explicit opt-in remains saved.
3. Select Strategic, Aggressive, Defensive, Others or Compare as usual. The range
   applies first; other modes choose among estimates near the available priority.
4. Read the status in Recommendation accuracy & draw protection. A range miss
   means the available safe scored alternatives could not satisfy the setting.
   Opening moves 1–10, validated books, winning mates and draw protection retain
   their exceptions. Strength first, when deliberately enabled, is advisory.
5. Your explicit loss cap still applies. From an equal evaluation, .50 pawns gives
   roughly81.3%, while .70 gives roughly74.9%. If you want to permit moves around75%,
   you may raise the loss cap to .75–1.00 pawns; this allows weaker moves. The
   required loss varies with the position, and analyzed roots may still miss it.
6. Keep the default eight candidates for the same search workload. If the status
   repeatedly says unavailable, increasing candidates toward16 may expose more
   alternatives, with additional engine work. Changing only the range, priority,
   strength override or style reuses completed results without another search.

These are local per-move estimates against the current engine/depth. They cannot
promise a website's final game-review accuracy. In four real depth12/eight-root
sample positions, all available roots were above85%, even after the selector fix.
The app reports that limit and chooses the closest safe estimate.

## What was wrong

- Strength first defaulted ON and treated the range as advisory, routinely choosing
  an estimate of100%. This also survived in previously saved settings.
- Strict selection imposed an additional hard .50-pawn ceiling after the user's
  safety filter. Even raising or disabling the visible cap could not remove it.
- Accuracy selection replaced style ordering, and style logs applied another
  ranking that could disagree with the headline arrow/AutoMove.
- Range/priority/style callbacks unnecessarily requested analysis again.

## Changed contracts and compatibility

The former contract in `BEHAVIOR.md` at1.8.8 explicitly defaulted strength first ON
and bounded strict selection by .50 pawns. To honor the requested range,1.8.9
intentionally changes these guarantees: range targeting is the default, old policy
settings migrate once with `recommendation_policy_version: 2`, and the hidden cap
is removed. Explicit user safety limits are retained. Repeated weaker choices can
reduce playing strength. New explicit strength-first opt-ins remain available.

Range selection chooses the closest estimate to priority among safe in-range
roots. Other styles can prefer a candidate whose distance from priority is at most
two percentage points worse than the closest available estimate. If no root is in
range, style cannot worsen the closest range miss. Existing style heuristics still
use evaluation swing and PV length; Compare is grade ordering, not calibrated Elo.

Single-search depth, candidate breadth, current-position readiness, original score
reference, mate/opening/book/draw protection and shared renderer/AutoMove selection
are retained. There is no shallow candidate injection or extra refinement search.

## Directions for the next AI

Start with `AGENTS.md`; run `python tools/navigate.py feature accuracy`.

| Area | Owner |
|---|---|
| Safe roots, calibration, opening/strength exceptions, style callback, cache | `python/gui_overlay.py`: `_choose_accuracy_candidate`, `_target_accuracy_candidate` |
| Range/priority shortlist, closest safe fallback | `python/adaptive_policy.py`: `choose_candidate` |
| Existing mode preferences and final shared suggestions | `python/gui_overlay.py`: `_apply_mode_ordering_filter`, `_compute_visible_suggestions`, `_mode_candidates` |
| Slider actions, no-search refresh | `python/control_panel.py`: `_on_range_slider_change`, `_on_priority_slider_change`; overlay `_refresh_accuracy_selection` |
| One-time policy migration and opt-in persistence | overlay `_apply_settings_data`, `_collect_settings`; `python/settings.example.json` |
| Production regression evidence | `checks/strength.py`, `checks/openings.py`, `checks/panel.py`, `checks/engine_integration.py` |

Do not silently relax the user's safety cap to force a match. Do not restore a
second hidden loss cap, use history debt to force catch-up mistakes, bypass depth
readiness, or re-rank the logs independently. Live score telemetry must not change
an already selected completed result on repaint. Mode, policy and new completed
search changes can refresh it. Ask for FEN/PGN and whether the reported percentage
is local or from a site before claiming reproduction of an external review score.

## Verification

Use `docs/VALIDATION.md` for prerequisites and commands. This change requires core
plus selection, real Tk/settings, and the real-engine group. These checks cover
both old protection guarantees and the new targeting behavior separately.

A before/after microbenchmark on the same four Stockfish17 depth12/eight-root
snapshots (two positions plus mirrored Black positions, five modes, both policy
settings,60uncached selections per combination) measured median selection plus
visible suggestions at0.316ms before and0.317ms after. Snapshot searches took
0.51–0.63seconds. This is a local selector comparison, not a full-game/hardware
performance guarantee. The integration check verifies unchanged search generation
when applying all five modes to the same completed result.
