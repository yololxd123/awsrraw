# Short handoff for the next fix

Share the complete current source bundle, then a short request like:

> Read AGENTS.md. Problem: [what happened]. Expected: [what should happen].
> Relevant feature: [accuracy / openings / analysis / arrows / streamproof / ui /
> settings / bridge / automove / timing]. Reproduce: [steps and affected FEN/PGN].
> Installed desktop/extension versions: [...]. Settings relevant to this issue: [...].
> Latest check commands/results: [...]. Preserve unrelated behavior.

Include a FEN or PGN for a move-selection issue, the Analysis log's completed depth
and selected estimate/status, recommendation mode, range, candidate count, and UCI
strength setting. For window issues include OS, streamproof state and whether the
window was hidden/restored. Include actual errors and expected results; omit tokens,
passwords and unrelated settings.

## Efficient exploration

1. `python tools/navigate.py feature <name>` returns owners and checks.
2. `python tools/navigate.py show <qualified.symbol>` prints at most80 lines.
3. Follow the named caller or `rg` a specific setting/message. Read the matching
   behavior contract rather than re-reading old conversations or every release.
4. Make the smallest complete change across producers and consumers. Report what
   changed, exact checks/results, and any live/platform verification still needed.

Keep only current source plus the relevant evidence in the AI's working context.
Archive old releases separately. These aids reduce repeated discovery; they do not
make a measured promise about token charges or how long an unknown fix will take.

## Accuracy fix follow-up (1.8.9)

Read `ACCURACY_FIX.md` first for causes, changed contracts, settings and exact
owners. Do not add a hidden pawn cap or re-rank logs independently of the final
suggestion. A range miss can mean all analyzed alternatives are too strong;
record the raw scores and status before changing engine breadth or depth.
