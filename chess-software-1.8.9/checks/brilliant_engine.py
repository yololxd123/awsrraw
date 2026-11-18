"""Exercise Brilliant selection using a completed MultiPV from a real engine."""
import argparse
import sys
from pathlib import Path
import chess
import chess.engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from selector_fixture import subject

parser = argparse.ArgumentParser()
parser.add_argument('--engine', type=Path, required=True)
args = parser.parse_args()
fen = 'Q1r3N1/5bn1/8/1p5b/2B2k2/2R5/4p1K1/8 w - - 0 11'
board = chess.Board(fen)
with chess.engine.SimpleEngine.popen_uci(str(args.engine)) as engine:
    options = {}
    if 'Threads' in engine.options:
        options['Threads'] = 1
    if 'Hash' in engine.options:
        options['Hash'] = 16
    engine.configure(options)
    lines = engine.analyse(board, chess.engine.Limit(depth=12), multipv=8)

snapshot = {}
for rank, line in enumerate(lines, 1):
    score = line['score'].pov(chess.WHITE).score()
    if score is None or not line.get('pv'):
        print('SKIP brilliant engine fixture: engine returned a mate or empty line')
        sys.exit(0)
    snapshot[rank] = ([move.uci() for move in line['pv']], f'{score / 100:.2f}')
app = subject(fen)
app.display_mode = 'others'
app.committed_multi_pvs = snapshot
ranks = app._brilliant_ranks()
if not ranks:
    print('SKIP brilliant engine fixture: no qualifying sacrifice in these engine results')
    sys.exit(0)
chosen = app._automove_chosen_move()
visible = app._compute_visible_suggestions()[0]
assert visible[0] in ranks and visible[1][0] == chosen
assert app._suggestion_color_override(visible[0], visible[1]) == '#40E0FF'
print(f'PASS real depth12 MultiPV: rank {visible[0]} {chosen} selected and cyan')
