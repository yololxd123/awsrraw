# Start here

This is the current source bundle: desktop `python/`, browser `extension/`.
Run commands from this directory; scripts also work via absolute paths.

1. Read `README.md`. Locate the affected feature with `python tools/navigate.py feature`.
2. Use `feature <name>`, then `show <qualified.symbol>` to read bounded source.
   Use `find <term>` if the feature is unknown. Avoid dumping `gui_overlay.py`.
3. Read the relevant section of `docs/BEHAVIOR.md` before changing behavior.
   Current user instructions can intentionally change it; disclose the change.
4. Trace consumers with `callers <symbol>` and `rg` for callback references.
   The caller list is lexical, not a complete dynamic call graph.
5. Run `python tools/check.py` and the affected feature's checks. See
   `docs/VALIDATION.md` for requirements, limits and additional checks by boundary.

## Maintainability

- Keep independent new logic in small modules, following `depth_timer.py`,
  `engine_session.py`, `adaptive_policy.py` and `recommendation_policy.py`.
  `ChessOverlay` still owns shared UI state; inspect consumers before extraction.
- Update `docs/features.json` when moving/renaming mapped functions. `check-map`
  detects broken anchors; it does not prove descriptions are still correct.
- New settings need default, collect/apply, UI variable/callback and sync changes.
- Preserve one committed recommendation for renderer and AutoMove. Live depth is
  telemetry, not permission to publish an incomplete result.
- `checks/` are portable application regression scripts; `tests/` use only the
  standard library. Neither is imported by application startup.
- Keep user settings, credentials, engines and generated caches out of delivery ZIPs.

See `docs/ARCHITECTURE.md` for ownership and `docs/VALIDATION.md` for verification.
