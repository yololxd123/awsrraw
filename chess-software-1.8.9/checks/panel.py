"""Native panel smoke/settings/timing check; no provider/engine needed."""
import sys,tkinter as tk,time,threading
from environment import isolated_app
from pathlib import Path
from unittest.mock import patch
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
scratch = tempfile.TemporaryDirectory(prefix='chess-panel-check-')
from gui_overlay import ChessOverlay
root=tk.Tk();errors=[]
root.report_callback_exception=lambda *args:errors.append(args)
with isolated_app(ChessOverlay, Path(scratch.name)):
 app=ChessOverlay(root)
 root.withdraw()
 panel=app.control_panel
 assert not app.recommendation_strength_first and not panel.strength_first_var.get()
 assert panel.root.title()=="Chess Suite · 1.8.9"
 panel.root.deiconify();panel.root.lift();root.update()
 panel.root.geometry('1120x820+0+0');root.update()
 for width in [840,1120,1600]:
  panel.root.geometry(f'{width}x820+0+0');root.update()
  for tab,_ in panel.nav_tabs:
   panel.select_tab(tab);root.update()
   assert panel.content_canvas.winfo_width() <= width
   for btn in panel.nav_buttons.values():
    assert btn.winfo_rootx()+btn.winfo_width()<=width+3,(width,tab,btn.text)
   visible=[c for c in panel.section_frames.values() if c.winfo_manager()]
   assert len(visible)==(len(panel.section_frames) if tab=='all' else 1)
   for card in visible:
    assert card.winfo_x()+card.winfo_width()<=panel.content_canvas.winfo_width()+2,(width,tab,card.winfo_width())
 print('PASS real Tk panel: all 12 tabs at 840/1120/1600 pixels, navigation and cards fit')
 assert not errors,errors
 panel.nav_buttons['humanizer'].invoke();root.update()
 assert panel.active_tab=='humanizer'
 from modern_widgets import ToggleSwitch
 def descendants(w):
  for child in w.winfo_children():
   yield child
   yield from descendants(child)
 switch=next(w for w in descendants(panel.section_frames['humanizer']) if isinstance(w,ToggleSwitch))
 before=app.humanizer.enabled;switch.invoke();root.update();assert app.humanizer.enabled != before
 switch.invoke();root.update();assert app.humanizer.enabled == before
 print('PASS sidebar navigation and native switch callback')
 panel.root.geometry('840x820+0+0');panel.select_tab('quality');root.update()
 panel.context_player_var.set(1500);panel.context_opponent_var.set(1700);app.update_context_preferences();panel.draw_threshold_var.set(.2);panel.draw_advantage_var.set(.45)
 app.update_recommendation_policy()
 panel.accuracy_pool_var.set(6);app.update_accuracy_search_candidates()
 panel.strength_first_var.set(False);app.toggle_recommendation_strength_first();assert not app.recommendation_strength_first
 data=app._collect_settings()
 assert data['recommendation_strength_first'] is False
 app.recommendation_strength_first=True
 assert data['accuracy_candidate_limit']==6
 app.accuracy_candidate_limit=8
 assert data['manual_player_rating']==1500 and data['manual_opponent_rating']==1700 and data['anti_draw_threshold']==.2 and data['anti_draw_min_advantage']==.45
 app.manual_player_rating=0;app.anti_draw_threshold=0;app.anti_draw_min_advantage=.01
 app._apply_settings_data(data)
 assert app.accuracy_candidate_limit==6
 assert not app.recommendation_strength_first
 assert app.manual_player_rating==1500 and app.anti_draw_threshold==.2 and app.anti_draw_min_advantage==.45
 print('PASS real Tk policy controls and settings round-trip')
 # Old implicit strength-first settings migrate once, while new opt-ins persist.
 legacy=dict(data);legacy.pop('recommendation_policy_version',None)
 legacy['recommendation_strength_first']=True
 app._apply_settings_data(legacy)
 assert not app.recommendation_strength_first
 opted_in=dict(data,recommendation_strength_first=True)
 app._apply_settings_data(opted_in)
 assert app.recommendation_strength_first
 app._sync_config_ui()
 assert panel.strength_first_var.get()
 with patch.object(app,'analyse_position') as search:
  # A current position must not cause range/priority/mode edits to search again.
  app.current_fen='rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'
  panel._on_range_slider_change(50,85)
  assert not app.recommendation_strength_first and not panel.strength_first_var.get()
  panel.strength_first_var.set(True);app.toggle_recommendation_strength_first()
  panel._on_priority_slider_change(75)
  assert not app.recommendation_strength_first and not panel.strength_first_var.get()
  app.set_display_mode('multiPV')
  panel.range_slider.set_range(50,85)
  app.update_recommendation_accuracy_range()
  search.assert_not_called()
 data=app._collect_settings()
 assert data['recommendation_policy_version']==2
 assert (data['recommendation_accuracy_min'],data['recommendation_accuracy_max'],data['recommendation_accuracy_priority'])==(50,85,75)
 print('PASS legacy policy migration, explicit strength opt-in, sliders restore range targeting, no engine restart on policy/mode edits')

 panel.strength_first_var.set(True);app.toggle_recommendation_strength_first()
 panel.root.geometry('1120x820+0+0');panel.select_tab('engine');root.update()
 panel.select_tab('quality');app._apply_game_context({'self_rating':1600,'opponent_rating':1800,'self_ms':125000,'opp_ms':118000,'speed':'blitz','initial_seconds':180,'increment_seconds':2},app._bridge_epoch);root.update()
 panel.select_tab('logs');panel.content_canvas.yview_moveto(0);root.update()
 panel.depth_timing_button.invoke();assert app.depth_timer.enabled
 ticket=app.depth_timer.begin('8/8/8/8/8/8/3K4/7k w - - 0 1',12,'Stockfish',8)
 app.depth_timer.active['start']-=.24
 app.depth_timer.finish(ticket,'8/8/8/8/8/8/3K4/7k w - - 0 1',12,True)
 app._refresh_depth_timing();root.update()
 assert 'Completed: 1' in panel.depth_timing_summary.cget('text')
 assert 'Ready' in panel.depth_timing_log.get('1.0','end')
 panel.depth_timing_button.invoke();assert not app.depth_timer.enabled
 print('PASS native depth timing button and rendered summary/history')

 # A worker asks Tk for geometry at execution time, including after a move.
 root.deiconify();root.geometry('650x650+70+90');root.update()
 app._board_geometry={'ok':True,'left':900,'top':900,'width':200,'height':200}
 result=[]
 worker=threading.Thread(target=lambda: result.append(app._get_board_geometry(1500)))
 worker.start();deadline=time.monotonic()+2
 while worker.is_alive() and time.monotonic()<deadline:
  root.update();time.sleep(.01)
 worker.join(timeout=.1)
 assert not worker.is_alive() and result and result[0] == app._canvas_board_geometry()
 assert result[0]['host']=='canvas'
 root.geometry('650x650+190+130');root.update()
 result=[];worker=threading.Thread(target=lambda: result.append(app._get_board_geometry(1500)))
 worker.start();deadline=time.monotonic()+2
 while worker.is_alive() and time.monotonic()<deadline:
  root.update();time.sleep(.01)
 worker.join(timeout=.1)
 assert not worker.is_alive() and result and result[0] == app._canvas_board_geometry()
 print('PASS worker gets current Tk canvas coordinates after moving visualizer')

 # Show an already encoded preview and verify Tk continues servicing events.
 frame=[[(10,20,30)]*240 for _ in range(240)]
 app._screen_preview_frame=frame
 app._screen_preview_ppm=app._frame_to_ppm_bytes(frame,240,240)
 app._screen_preview_grid=None
 app.open_screen_preview();root.update()
 assert app._screen_preview_photo is not None
 handled=[];root.after(10,lambda:handled.append(True))
 deadline=time.monotonic()+1
 while not handled and time.monotonic()<deadline:
  root.update();time.sleep(.01)
 assert handled
 assert app._screen_preview_after_id is not None
 app.close_screen_preview()
 assert app._screen_preview_after_id is None
 app.open_screen_preview();root.update()
 assert app._screen_preview_photo is not None
 app.close_screen_preview()
 print('PASS preview displays prepared image and Tk services events')
 app.should_run=False
 app._settings_writer.close()
 root.destroy()
scratch.cleanup()
