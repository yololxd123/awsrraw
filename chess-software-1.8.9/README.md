# Chess Suite — source and maintenance bundle 1.8.9

Native Python/Tk desktop app plus compatible extension 1.8.3. This release fixes
range targeting and combines it with suggestion styles without extra engine searches.
Start with `AGENTS.md`; the feature map and handoff remain the navigation entry points.
See `docs/ACCURACY_FIX.md` for the cause, settings, limits and future-AI directions.

## Install / run

Keep your `settings.json`, engine binaries, weights and credentials. Close the app,
copy **all** files from `python/` into your existing Python folder, and run
`python main.py` there with your existing environment. The new `version.py` must be
copied too. No new runtime dependency. <!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:ab6ef30f73b1a951b05afa04d5418a6736d3f9e922b92bae5a82b4a270073afe -->

This mouse update also requires replacing and reloading the extension files;
see `docs/MOUSE_UPDATE.md`.

<!-- /coderabbit-ai-generated:v1 --> This package does not contain an
engine, opening book, personal settings or provider credentials.

For future AI work, share the **whole bundle**, including `AGENTS.md`, `docs/`,
`tools/`, `tests/`, `checks/` and `extension/`. Preserve those folders in your source
checkout; copying only the installed Python files loses the developer aids.

## Find a bug's owner

```sh
python tools/navigate.py feature
python tools/navigate.py feature streamproof
python tools/navigate.py find accuracy
python tools/navigate.py show gui_overlay.ChessOverlay._choose_accuracy_candidate
python tools/navigate.py callers gui_overlay.ChessOverlay._compute_visible_suggestions
python tools/check.py
```

Commands parse the **current** source without importing the GUI or contacting a
service. Results show paths/line ranges; `find`/`callers` return 20 results and
`show` returns 80 source lines by default. Use `--limit` (maximum 300) and `show
--offset` to read more. No generated full index needs to be kept in sync.
`tools/package.py` builds a verified source ZIP for the next handoff.

## Read only the relevant guide

| Need | Guide |
|---|---|
| Feature owners, execution path, extension boundary | [Architecture](docs/ARCHITECTURE.md) |
| Current depth/accuracy/opening/streamproof contracts | [Behavior](docs/BEHAVIOR.md) |
| Checks, dependencies, platform limitations | [Validation](docs/VALIDATION.md) |
| Reproducible bug report / future AI prompt | [Handoff](docs/HANDOFF.md) |

`python tools/check.py --feature <name>` selects mapped checks, including core.
Some groups need app dependencies, a display, Node or an engine path. Requirements
are explicit in the validation guide; a missing prerequisite is not a pass.
