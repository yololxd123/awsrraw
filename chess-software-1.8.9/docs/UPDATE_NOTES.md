<!-- coderabbit-ai-generated:v1 -->

# Update notes

## Suggestions

<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:4fb202490876f39e761a53ed6b00a77eb58832ab8ba84333ac792580dc68bd52 -->

<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:7f65374040e4c6969f6e08173947b4597f3641067c1904a2565b302e72fc9d2c -->

The display offers Best Move, Offensive, Defensive, Brilliant and Tactician. Brilliant checks legal captures of the moved piece and a bounded material exchange, even if the engine's continuation declines the offer. A qualifying near-best sacrifice is cyan, including the selected forced-mate sacrifice. It can override opening, book and accuracy preferences; mate priority, the configured loss cap and draw protection still apply. Only moves in the completed engine candidate set are checked. The status reports when none qualify or python-chess is missing. The classification is a heuristic and does not guarantee a sound tactic or match another site's brilliant classification. Tactician marks a line with two forcing moves as black for attack or dark blue when the starting position was in check. Older saved Others and Compare modes now map to Brilliant and Tactician.

<!-- /coderabbit-ai-generated:v1 -->

<!-- /coderabbit-ai-generated:v1 -->

## AutoMove

<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:86ee76969b26ee2c3aedd349835f387fe988f9ff542af23bcd57a22df23a83da -->

The native mouse executor consumes the generated drag plan, including pickup and release offsets, and interpolates between its points. It requests the visualizer's current rectangular canvas from Tk before a move, with extension geometry or calibration as fallback when the canvas is unavailable. Normal and complex thinking limits and event delay settings are enforced; path speed scales event delays. The operating system still receives synthesized input, and Windows behavior has not been tested here. No detection or human-likeness percentage is claimed.

The detector preview captures a smaller frame, encodes it on a worker, and refreshes about six times per second. FEN side-to-move updates only after the placement guard accepts the position, preventing an ignored piece-lift preview from changing the active turn. Offensive and Defensive colors have greater contrast.

<!-- /coderabbit-ai-generated:v1 -->

## Reference accuracy is not implemented

<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:e2f527541ba00c755964e519eddde6687ed3a56720d34f445cbb0db84bd3f29a -->

The selector still measures a local per-move estimate against the current engine's strongest scored root. It does not estimate Elo-specific rapid, blitz or bullet accuracy. In a Stockfish 17.1 test the same position and depth produced identical four MultiPV scores at UCI Elo 1600 and 2600. A calibrated reference requires either rated-game data or an explicitly accepted experimental definition of an Elo reference. Site review percentages remain outside this selector's guarantees.

<!-- /coderabbit-ai-generated:v1 -->

## Install

Copy the files in `python/` into the existing installed Python folder, keeping your own settings and engines, then launch `main.py`. This source ZIP excludes local settings, credentials, engines and cached bytecode.
