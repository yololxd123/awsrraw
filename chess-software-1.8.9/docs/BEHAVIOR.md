# Current contracts — 1.8.9

<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:b3b624727126ec66daefb54b1c6b585bab0c5549f90811f9e71041535e93361d -->

This describes the current implementation, not a promise of playing strength or a
site's final grading. Recheck the source for the affected feature before changing it.

Mouse execution and native promotion contracts are described in `MOUSE_UPDATE.md`.

<!-- /coderabbit-ai-generated:v1 -->

## Analysis / result publication

- Only current-position results that actually reach the requested effective depth
  and attainable candidate count are committed for suggestions. The engine search
  lifecycle includes stop acknowledgement and readiness before a new search.
- Live telemetry is separate from the committed arrow snapshot. Superseded results
  must not replace the current board's suggestions.
- Cache identity includes search/depth/options context. A shallow result cannot
  satisfy a deeper requested result. In-check positions may have fewer legal roots;
  do not wait for impossible MultiPV counts.
- Accuracy candidate breadth defaults to8, configurable4–16 (at least displayed
  count); it is one search, with no repeated accuracy expansion pass. Depth is not Elo.

## Recommendation / accuracy

- Renderer and AutoMove consume `_compute_visible_suggestions`, not independent
  raw-best fallbacks. Selection uses committed scored candidates.
- With recommendation policy enabled, **range targeting defaults ON**. Old
  strength-first settings migrate once using `recommendation_policy_version: 2`.
  A subsequent explicit strength-first opt-in persists; it keeps the strongest
  surviving safe score and makes the range advisory. Editing range/priority
  returns to range targeting and reuses the completed result.
<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:28f88365acc7704c078709b9614c560eeb1bdeb7aa3e0f6be67d21caac8be6a9 -->

- After opening/book exceptions, range targeting chooses the estimate closest
  to priority inside the range. If no safe analyzed root matches, it reports
  unavailable and chooses the closest safe estimate without another search.
  Brilliant mode may override the range, opening and book for a near-best
  apparent sacrifice. Mate priority, the explicit user loss cap and draw rules
  still apply; turning the loss cap off permits larger evaluation losses.

<!-- /coderabbit-ai-generated:v1 -->
<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:362946cd6a35b0559f357cc1dde2a04c85417f7f6af8d0c8c69d8e1611fb62a8 -->

<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:96183175a911beafa574899b225e85234dc59282739a12e2094e81e5656fb0bf -->

- Best Move, Offensive and Defensive apply styles to in-range roots within two
  percentage points of the nearest available priority estimate. Brilliant may
  override the range for an apparent sacrifice, including offers declined in the
  principal variation. It checks legal captures and a bounded material exchange,
  then applies mate priority, the user loss cap, draw rules and a 0.25-pawn gap
  to the best scored root. A selected forced-mate sacrifice can also be cyan.
  Tactician favors continuations with at least two forcing moves. These heuristics
  do not guarantee tactical soundness or match a site's brilliant classification.
- Brilliant examines only the completed MultiPV candidates; it does not start
  another search. Its status reports when none qualify or when python-chess is
  unavailable. Renderer and AutoMove use the same selected move and annotation.
- The five display buttons are Best Move, Offensive, Defensive, Brilliant and
  Tactician. Legacy mode values are retained for settings compatibility, so
  former Others/Compare selections now load as Brilliant/Tactician.

<!-- /coderabbit-ai-generated:v1 -->

<!-- /coderabbit-ai-generated:v1 -->
- The selector cache includes display mode, comparison Elo
  and clock-pressure activation. Mode edits re-rank without restarting analysis.
  Logs, renderer and AutoMove use the same final headline selection.
- Repeated deliberate concessions can weaken play. This is neither Elo
  calibration nor a guarantee of an external site's game accuracy.
- Winning mate takes priority. Safety/draw constraints can remove raw-best choices.
  Estimates refer to the strongest original numeric root, not renormalized filtered
  candidates. The estimate is not a guaranteed external game-review accuracy.
- Preserve the chosen move during repaint/late book arrival for the same completed
  result; position, policy, relevant book or new search generation changes refresh it.

## Openings / continuity

- Fullmove numbers1–10 are protected from accuracy targeting, even without theory
  data. This is a policy window, not proof that the position is in a book.
- Prefer a validated local book hit, then cached cloud explorer hints. A hint must
  be a currently scored completed root within0.25 pawns of strongest numeric root,
  and obey tighter user safety/draw constraints. No unscreened synthetic book arrow.
- No book hit: during opening protection, a remembered legal continuation within
  0.20 pawns may win; otherwise strongest safe move. No book is bundled.
- Beyond move10, validated book hits still bypass accuracy targeting; other moves
  follow the selected range or strength-first policy. A PV continuation is not a semantic
  long-term strategy guarantee. Humanizer affects opponent prediction only.

## Arrows / privacy

- Arrow window stays click-through, non-activating and a tool window after sync.
- F5 streamproof ON and OFF applies the shared Windows capture-affinity helper to
  GUI, visualizer and separate arrow window. Arrow creation/remap/rebuild inherits
  current state. Successful redraw state is cached; failed protection is retried.
- Streamproof keeps windows locally visible while requesting capture exclusion.
  This relies on Windows/capture support; Linux native-call mocks cannot prove
  actual screenshots/recordings exclude the windows.

## Timing / settings

- Depth timing is optional and primary-engine only. Average/fastest/slowest include
  completed target-depth searches; incomplete and cached results are separate.
  New match resets counts, retaining the toggle. Stale tickets cannot finish a new
  search or contaminate a reset match. History is bounded to50 rows.
- Settings load after defaults; save uses immutable snapshots and atomic replace.
  Add a control to default, collection/application and UI synchronization paths.
- The compatible extension remains1.8.3; changing the desktop version does not
  require changing the browser manifest.
