"""Check mode classification, colors and the actual drag consumer with mocks."""
import sys
import threading
import chess
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from automove import AutoMoveSimulator, plan_screen_points
from gui_overlay import ChessOverlay
from selector_fixture import subject
from suggestion_modes import line_features

fen = 'r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4'
sequence = ['c4f7', 'e8f7', 'f3g5']
assert {'sacrifice', 'offensive_tactic'} <= line_features(fen, sequence)
assert 'offensive_tactic' not in line_features(fen, ['c4f7', 'e8e7', 'f3g5'])
assert not line_features(fen, ['x9y8'])
print('PASS legal continuation detects forcing sacrifice; illegal continuation rejected')

declined_fen = '5rk1/5ppp/8/8/8/4Q3/8/4R1K1 w - - 0 11'
declined = ['e3e8', 'g7g6', 'e8d7']
assert 'sacrifice' in line_features(declined_fen, declined)
assert 'sacrifice' not in line_features('4k3/3p4/4r3/8/6B1/8/8/4K3 w - - 0 11', ['g4e6', 'd7e6'])
assert 'sacrifice' not in line_features('3k4/3p4/8/8/6B1/8/8/3RK3 w - - 0 11', ['g4e6', 'd8c8'])
print('PASS declined sacrifice detected; material-winning trade and pinned capture excluded')

sample=subject(declined_fen)
sample.display_mode='others';sample.committed_multi_pvs={1:(['e3e5'],'4.38'),2:(declined,'4.26')}
assert sample._target_accuracy_candidate()[0] == 2
assert sample._automove_chosen_move() == 'e3e8'
assert sample._suggestion_color_override(2,declined)=='#40E0FF'
sample.recommendation_accuracy_enabled=False
assert sample._automove_chosen_move()=='e3e8'
sample.aggressive_blunder_threshold=.05
assert sample._automove_chosen_move()=='e3e5' and sample._suggestion_color_override(2,declined) is None
assert 'no qualifying sacrifice among 2 analyzed moves' in sample.recommendation_accuracy_status
print('PASS declined sacrifice selected and colored with accuracy on/off; cap still enforced')

sample=subject(declined_fen);sample.display_mode='others'
sample.committed_multi_pvs={1:(declined,'FM2'),2:(['e3e5'],'FM3')}
assert sample._automove_chosen_move()=='e3e8' and sample._suggestion_color_override(1,declined)=='#40E0FF'
sample.committed_multi_pvs={1:(['e3e5'],'FM2'),2:(declined,'FM3')}
assert sample._automove_chosen_move()=='e3e5' and sample._suggestion_color_override(2,declined) is None
print('PASS mate priority with cyan annotation only for the selected mating sacrifice')

sample=subject(declined_fen);sample.display_mode='others';sample.recommendation_accuracy_enabled=False
sample.committed_multi_pvs={1:(['e3e5'],'4.38'),2:(declined,'4.26')}
with patch('gui_overlay.chess_available',return_value=False):
 assert sample._automove_chosen_move()=='e3e5'
 assert 'requires python-chess' in sample.recommendation_accuracy_status
print('PASS missing chess dependency is reported and falls back to strongest safe move')

s = subject(fen)
s.display_mode = 'others'
s.target_recommendation_accuracy_min = 50
s.target_recommendation_accuracy_max = 85
s.target_recommendation_accuracy_priority = 75
s.committed_multi_pvs = {1: (['d2d3'], '0.00'), 2: (sequence, '-0.10')}
assert s._target_accuracy_candidate()[0] == 2
assert s._automove_chosen_move() == 'c4f7'
assert 'brilliant heuristic' in s.recommendation_accuracy_status
s.aggressive_blunder_threshold = .05
assert s._target_accuracy_candidate()[0] == 1
print('PASS brilliant mode overrides opening/range only within user safety cap; AutoMove agrees')

s = subject()
s.arrow_scale = 1
class Canvas:
 def winfo_width(self): return 800
 def winfo_height(self): return 800
 def create_line(self, *args, **kwargs): self.fill = kwargs['fill']; return 1
s._arrow_cv = Canvas()
for mode, first, second in [('bestmove','#FF5050','#800020'),('multiPV','#1E90FF','#001B42'),('others','#1E90FF','#32CD32')]:
 s.display_mode=mode
 s.draw_move('e2','e4',1); assert s.arrow_canvas.fill==first
 s.draw_move('e2','e4',2); assert s.arrow_canvas.fill==second
 s.draw_move('e2','e4',1,color_override='#40E0FF'); assert s.arrow_canvas.fill=='#40E0FF'
print('PASS mode palettes and explicit annotation color')

s.automove = AutoMoveSimulator()
s.automove.path_speed=1
s._square_screen_coords=lambda square,geom: {'e2':(450,650),'e4':(450,450)}[square]
events=[]
s._os_mouse_move=lambda x,y:events.append(('move',x,y))
s._os_mouse_button=lambda flag:events.append(('button',flag))
s._glide_sigmadrift=lambda plan,valid=lambda:True:None
plan=[{'t':0,'sa':.15,'ox':.1,'oy':.1,'d':1}, {'t':1,'ea':-.1,'ox':-.08,'oy':-.08,'d':1,'up':True}]
with patch('gui_overlay.time.sleep'), patch('gui_overlay.ctypes.windll',create=True):
 ChessOverlay._real_mouse_drag(s,'e2e4',plan,{'scale':1,'width':800,'height':800})
down=events.index(('button',2));up=events.index(('button',4))
assert events[down-1][1:] != (450,650)
assert events[up-1][1:] != (450,450)
assert len([x for x in events if x==('button',2)])==1
assert len([x for x in events if x==('button',4)])==1
print('PASS actual drag consumer uses plan offsets and releases once')

events.clear()
long_plan = [{'t':0,'d':1}, {'t':1,'d':160,'up':True}]
with patch('gui_overlay.time.sleep'), patch('gui_overlay.ctypes.windll', create=True):
 ChessOverlay._real_mouse_drag(s, 'e2e4', long_plan, {'left':0,'top':0,'scale':1,'width':800,'height':800})
down=events.index(('button',2));up=events.index(('button',4))
assert len([event for event in events[down:up] if event[0]=='move']) >= 10
assert all(0 <= event[1] < 800 and 0 <= event[2] < 800 for event in events if event[0]=='move')
print('PASS drag interpolates timed points within board bounds')

class Visualizer:
 def winfo_exists(self): return True
 def winfo_ismapped(self): return True
 def winfo_width(self): return 806
 def winfo_height(self): return 646
 def winfo_rootx(self): return -900
 def winfo_rooty(self): return 200
s = subject()
s.canvas = Visualizer()
g = s._canvas_board_geometry()
assert (g['left'], g['top'], g['width'], g['height'], g['scale']) == (-897,203,800,640,1)
assert s._square_screen_coords('e2',g) == (-447,723)
s.perspective = 'black'
assert s._square_screen_coords('e2',s._canvas_board_geometry()) == (-547,323)
s._board_geometry={'ok':True,'left':10,'top':10,'width':200,'height':200}
assert s._get_board_geometry()['host']=='canvas'
print('PASS current rectangular visualizer geometry overrides extension and handles orientation/negative origins')

s = subject()
s.automove = SimpleNamespace(enabled=True, thinking_time_ms=lambda fen:0, generate_plan=lambda move:[{'t':0}])
s._automove_epoch=1;s._automove_busy=True
s._automove_chosen_move=lambda:'g1f3'
s._is_human_turn=lambda:True
called=[]
def changed_geometry(wait_ms):
 s.current_fen='changed'
 return {'ok':True,'left':0,'top':0,'width':800,'height':800}
s._get_board_geometry=changed_geometry
s._real_mouse_drag=lambda *args:called.append(args)
original=s.current_fen
worker=threading.Thread(target=s._automove_worker,args=(original,'g1f3',1))
worker.start();worker.join(timeout=2)
assert not worker.is_alive() and not called
print('PASS changed position while retrieving geometry cannot trigger a drag')

s = subject()
s.previous_fen = s._last_real_fen = s.current_fen
original = s.current_fen
preview = original.replace('/4P3/', '/8/').replace(' w ', ' b ')
assert s._placement_delta(original, preview) == (1, 0)
s.apply_analysis_request(preview, 1600)
assert s.current_fen == original and s.current_player_color == 'w' and s._is_human_turn()
print('PASS discarded drag-preview FEN cannot change active turn')
s._sync_player_to_bottom_safe=lambda *args: None
s.last_analyzed_fen=None
searched=[]
s.analyse_position=lambda fen,elo:searched.append(fen)
board=chess.Board(original)
board.push_uci('g1f3')
accepted=board.fen(en_passant='fen')
s.apply_analysis_request(accepted,1600)
assert s.current_fen==accepted and s.current_player_color=='b' and not s._is_human_turn()
assert searched==[accepted]
print('PASS accepted move updates FEN turn and starts analysis')

s = subject()
s._canvas_board_geometry_cache = {'ok':True, 'left':0,'top':0,'width':960,'height':640}
s._board_geometry = None
s._screen_preview_win = object()
s._screen_preview_frame = s._screen_preview_grid = s._screen_preview_geom = None
s._screen_preview_ppm = None
s._screen_lock = threading.Lock()
class OneFrame:
 stopped = False
 def is_set(self): return self.stopped
 def wait(self, seconds): self.stopped = True
s._screen_detector_stop = OneFrame()
calls=[]
s._capture_board_region=lambda geom,max_dimension=None: (calls.append(('capture', max_dimension)) or [[(1,2,3)]])
s._screen_grid_from_frame=lambda frame,geom: None
s._frame_to_ppm_bytes=lambda frame,w,h: (calls.append(('encode', w, h, threading.current_thread() is threading.main_thread())) or b'ppm')
thread = threading.Thread(target=s._run_screen_preview_capture)
thread.start();thread.join(timeout=2)
assert not thread.is_alive()
assert calls == [('capture',240),('encode',240,240,False)] and s._screen_preview_ppm == b'ppm'
print('PASS preview captures a bounded image and encodes outside Tk thread')

fake_user=MagicMock()
fake_user.GetSystemMetrics.side_effect=lambda code:{76:0,77:0,78:1920,79:1080}[code]
fake_user.GetDC.return_value=1
gdi=MagicMock()
gdi.CreateCompatibleDC.return_value=2
gdi.CreateCompatibleBitmap.return_value=3
gdi.SelectObject.return_value=4
gdi.StretchBlt.return_value=1
gdi.GetDIBits.return_value=160
with patch('gui_overlay.user32',fake_user), patch('gui_overlay.ctypes.windll',SimpleNamespace(gdi32=gdi),create=True), patch('gui_overlay._np',None):
 frame=ChessOverlay._capture_board_region(s, {'left':0,'top':0,'width':960,'height':640},max_dimension=240)
assert len(frame)==160 and len(frame[0])==240
assert gdi.CreateCompatibleBitmap.call_args.args == (1,240,160)
assert gdi.StretchBlt.called
names=[call[0] for call in gdi.method_calls]
assert [i for i,name in enumerate(names) if name=='SelectObject'][1] < names.index('GetDIBits')
print('PASS screen capture downscales before reading pixels and preserves aspect ratio')

am=AutoMoveSimulator();am.enabled=True;am.think_min_ms=2000;am.think_max_ms=2500;am.complex_enabled=False
for _ in range(300): assert 2000 <= am.thinking_time_ms(fen) <= 2500
am.event_delay_min_ms=60;am.event_delay_max_ms=90
for step in am.generate_plan('e2e4'): assert 60 <= step['d'] <= 90
print('PASS normal think and mouse event bounds')

# Production cancellation must release a held button and stop on its drop point.
s=subject();s.automove=AutoMoveSimulator();s.automove.enabled=True
s._automove_epoch=1
s._square_screen_coords=lambda sq,g:(100,100) if sq=='e2' else (100,300)
s._glide_sigmadrift=lambda *args:None
buttons=[]
s._os_mouse_button=buttons.append
s._follow_mouse_path=lambda *args:(_ for _ in ()).throw(InterruptedError())
with patch('gui_overlay.time.sleep'),patch('gui_overlay.ctypes.windll',create=True):
 try: s._real_mouse_drag('e2e4',[{'t':0,'d':10},{'t':1,'d':10,'up':True}],{'width':800,'height':800})
 except InterruptedError: pass
 else: raise AssertionError('expected cancellation')
assert buttons==[2,4]

s=subject();s.automove=AutoMoveSimulator();s.automove.enabled=True;s._automove_epoch=8
s._get_board_geometry=lambda **kw:{'ok':True,'left':100,'top':200,'width':800,'height':800,'scale':1}
s._promotion_target=lambda piece:(.5,.25,.1,.1)
plans=[];buttons=[]
s._glide_sigmadrift=lambda plan,valid:plans.append(plan)
s._os_mouse_button=buttons.append
with patch('gui_overlay.time.sleep'),patch('gui_overlay.ctypes.windll',create=True):
 assert s._native_promotion('n',8)
assert buttons==[2,4] and (plans[0][-1]['x'],plans[0][-1]['y'])==(500,400)
buttons.clear()
targets=iter([(.5,.25,.1,.1),None]);s._promotion_target=lambda piece:next(targets)
with patch('gui_overlay.time.sleep'),patch('gui_overlay.time.monotonic',side_effect=[0,0,5]),patch('gui_overlay.ctypes.windll',create=True):
 assert not s._native_promotion('q',8)
assert buttons==[]
pending={'id':'current','event':threading.Event(),'response':None};s._promotion_pending=pending
s._accept_promotion_geometry({'request_id':'old','ok':True})
assert not pending['event'].is_set()
s._accept_promotion_geometry({'request_id':'current','ok':True})
assert pending['event'].is_set()
print('PASS cancelled drag releases button; native promotion uses visible geometry, rejects vanished and stale replies')

# Absolute input preserves negative-monitor origins and bypasses relative acceleration.
s=subject();user=MagicMock();user.GetSystemMetrics.side_effect=lambda k:{76:-1920,77:0,78:3840,79:1080}[k]
user.SendInput.return_value=1
sent=[]
def send(count,event,size):
 sent.append((event._obj.mi.dx,event._obj.mi.dy,event._obj.mi.dwFlags));return 1
user.SendInput.side_effect=send
with patch('gui_overlay.ctypes.windll',SimpleNamespace(user32=user),create=True):
 s._os_mouse_move(-1920,0)
 s._os_mouse_move(1919,1079)
assert sent[0][2]==0xC001 and sent[0][0]<20 and sent[1][0]>65500
print('PASS absolute virtual-desktop native input covers negative origins and both edges')

s=subject();moves=[];s._os_mouse_move=lambda x,y:moves.append((x,y))
with patch('gui_overlay.time.sleep'),patch('gui_overlay.time.perf_counter',side_effect=[0,0]+[1]*100):
 s._follow_mouse_path([(0,0,0),(100,100,40)],.04)
assert moves==[(0,0),(100,100)]
print('PASS scheduler stalls skip missed samples instead of catch-up bursts')
