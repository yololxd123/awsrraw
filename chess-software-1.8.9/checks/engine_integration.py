"""Optional real-engine depth/book/selection gate. Requires a Stockfish-compatible UCI binary and display."""
import sys,time,struct,tkinter as tk
from pathlib import Path
from environment import isolated_app
import argparse,tempfile
parser=argparse.ArgumentParser()
parser.add_argument('--engine',required=True,type=Path)
args=parser.parse_args()
engine_path=args.engine.expanduser().resolve()
if not engine_path.is_file():parser.error('Engine executable does not exist')
scratch=tempfile.TemporaryDirectory(prefix='chess-engine-check-')
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'python'))
from gui_overlay import ChessOverlay
from book_manager import PolyglotBook
import chess,chess.polyglot
start=chess.STARTING_FEN;b=chess.Board();b.push_uci('e2e4');black=b.fen()
rows=[]
for fen,moves in [(start,[('g2g4',65000),('e2e4',1000),('d2d4',500),('g1f3',250),('c2c4',125)]),(black,[('f7f6',65000),('e7e5',1000),('c7c5',500),('e7e6',250),('c7c6',125)])]:
 for uci,weight in moves:
  m=chess.Move.from_uci(uci);raw=m.to_square | (m.from_square<<6)
  rows.append((chess.polyglot.zobrist_hash(chess.Board(fen)),raw,weight,0))
path=Path(scratch.name) / 'opening.bin';path.write_bytes(b''.join(struct.pack('>QHHI',*row) for row in sorted(rows)))
b=chess.Board()
for move in ['e2e4','e7e5','g1f3','b8c6']:b.push_uci(move)
unknown=b.fen().rsplit(' ',1)[0]+' 11'
root=tk.Tk();errors=[];root.report_callback_exception=lambda *e:errors.append(e)
with isolated_app(ChessOverlay, Path(scratch.name)):
 app=ChessOverlay(root);root.withdraw();app.analysis_depth=12;app.engine_threads=1;app.engine_hash=32;app.multi_pv_count=3;app.accuracy_candidate_limit=8;app.recommendation_accuracy_enabled=True;app.recommendation_strength_first=True;app.anti_draw_enabled=False;app.dynamic_depth_enabled=False
 app.target_recommendation_accuracy_min=70;app.target_recommendation_accuracy_max=90;app.target_recommendation_accuracy_priority=80
 app.engine_paths=[str(engine_path)];app.book_manager.cloud_enabled=False;app.book_manager.tb_enabled=False
 app.opening_book=PolyglotBook(str(path));app.opening_book_enabled=True;app.current_fen=start;app.current_player_color='w';app.player_color='w';app._bridge_epoch='fixture';app.depth_timer.toggle();app.start_engine();e=app.engines[0]
 for index,fen in enumerate([start,black,unknown]):
  app.player_color=fen.split()[1];app.current_player_color=app.player_color
  before=e.get('search_generation',0)
  if index:app.analyse_position(fen,app.engine_elo)
  deadline=time.monotonic()+30
  while time.monotonic()<deadline:
   root.update()
   if not app._suggestions_are_ready():assert not app._compute_visible_suggestions()
   if e['session'].state=='idle' and e.get('active_fen')==fen:break
   time.sleep(.005)
  assert e['session'].state=='idle' and e['_committed_depth']>=12 and app._suggestions_are_ready()
  if index:assert e['search_generation']==before+1
  else:assert e['search_generation']==1
  visible=app._compute_visible_suggestions();choice=visible[0][1][0]
  assert all(chess.Move.from_uci(line[1][0]) in chess.Board(fen).legal_moves for line in visible)
  remembered=app._line_memory.roots[(app._line_memory.key(fen),app.player_color)]
  assert remembered[0]==choice and len(remembered)>2
  selected=app._target_accuracy_candidate();assert selected[1][0][0]==choice==app._automove_chosen_move()
  app._predict_opponent_move();assert app.opponent_move==(visible[0][1][1] if len(visible[0][1])>1 else None)
  values={rank:float(data[1]) for rank,data in app.committed_multi_pvs.items() if 'M' not in data[1]}
  gap=max(values.values())-values[visible[0][0]]
  if index<2:
   assert choice in dict(app.opening_book.lookup(fen)) and choice not in ['g2g4','f7f6']
   assert gap<=.25+1e-9 and 'Validated local book' in app.recommendation_accuracy_status
  else:
   assert abs(gap)<=1e-9 and 'Strength first' in app.recommendation_accuracy_status
  print('PASS real depth12',app.player_color,choice,'gap',round(gap,2),'one search; arrow/selection/reply agree;',app.recommendation_accuracy_status,flush=True)
 # Existing guarantee above still holds with the explicit strength-first opt-in.
 # Reuse the same real depth12/eight-root result in strict mode, across all modes.
 generation=e['search_generation'];raw=repr(app.committed_multi_pvs)
 app.recommendation_strength_first=False
 app.target_recommendation_accuracy_min=50;app.target_recommendation_accuracy_max=85;app.target_recommendation_accuracy_priority=75
 start_select=time.perf_counter()
 for mode in ['pv','bestmove','multiPV','others','advPV']:
  app.display_mode=mode
  visible=app._compute_visible_suggestions();selected=app._target_accuracy_candidate()
  assert visible[0][0]==selected[0]
  assert app._automove_chosen_move()==selected[1][0][0]
  assert app._mode_candidates(mode)[0][0]==selected[0]
  safe=app._draw_safe_lines(app._blunder_safe_lines([(r,d[0],d[1]) for r,d in app._visible_multi_pvs_raw()]))
  estimates=[app._accuracy_from_loss(max(values.values())*100,float(line[2])*100) for line in safe]
  if any(50<=estimate<=85 for estimate in estimates):
   assert 50<=app.recommendation_accuracy_estimate<=85
  else:assert 'unavailable' in app.recommendation_accuracy_status
  assert e['search_generation']==generation and repr(app.committed_multi_pvs)==raw
 print('PASS real strict 50–85/75, all modes without another search; five-mode selection ms',round((time.perf_counter()-start_select)*1000,2),flush=True)
 assert app.depth_timer.snapshot()['count']==3
 assert not errors,errors
 app.should_run=False
 for e in app.engines:app._retire_engine(e)
 app.opening_book.close();app._settings_writer.close();root.destroy()
scratch.cleanup()
