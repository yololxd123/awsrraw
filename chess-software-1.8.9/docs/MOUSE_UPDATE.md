<!-- coderabbit-ai-generated:v1 -->

# Mouse input update

Install all Python files, and replace and reload the extension files in chrome://extensions. Keep personal settings and engines. Restart the desktop app after updating.

Promotion now requests a visible chooser rectangle from the extension and clicks it through Windows. Replies are request-matched and the chooser is checked again after cursor travel; hidden, obscured, missing or stale targets cause no click. Legacy extension execute_move/click_promotion commands now return native_input_required. No synthetic DOM input fallback remains.

Fast speed now scales travel rather than pickup/release pauses. Travel has a distance-dependent minimum (about 203 ms for a two-square move), plus separate button dwell. This intentionally changes earlier timing behavior: configured event bounds describe generated plan intervals, not actual Windows input spacing. Timing that falls outside the generated bounds is redistributed inside them instead of piling up at an endpoint. Cursor trajectories are interpolated with bounded cubic segments and scheduled against an absolute clock; approach and drag share this executor. Automatic post-release drift is removed. <!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:db3c7153a23a89a1e97772735d71afe5f7f0055d3ed0f4a3fbabfe190834ed95 -->

The approach no longer saturates at the same 80 ms model floor across different distances. Movement uses absolute virtual-desktop SendInput, including negative monitor origins, instead of SetCursorPos or acceleration-sensitive relative deltas. Windows still identifies injected input locally. Failed promotion selection pauses AutoMove rather than retrying the drag while the chooser is unresolved.

<!-- /coderabbit-ai-generated:v1 -->

These are input correctness and motion changes, not a guarantee of human behavior or a site's detection outcome. Native Windows, browser event properties and live-site promotion still require a real-machine check.
