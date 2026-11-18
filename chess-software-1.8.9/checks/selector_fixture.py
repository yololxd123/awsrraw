"""Synthetic committed-result fixture; never starts an engine or GUI."""
import sys
from types import SimpleNamespace as NS
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from gui_overlay import ChessOverlay
from adaptive_policy import LineMemory
from depth_timer import DepthTimer
import chess

def subject(fen=None):
 s=ChessOverlay.__new__(ChessOverlay)
 s.__dict__.update(current_fen=fen or 'rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2',player_color='w',current_player_color='w',current_playing_as='w',perspective='white',
 recommendation_accuracy_enabled=True,recommendation_strength_first=False,target_recommendation_accuracy_min=70,target_recommendation_accuracy_max=90,target_recommendation_accuracy_priority=80,
 aggressive_blunder_filter_enabled=True,aggressive_blunder_threshold=.5,anti_draw_enabled=False,anti_draw_threshold=.12,anti_draw_min_advantage=.35,
 _bridge_epoch='test',_line_memory=LineMemory(),_adaptive_history={'w':[],'b':[]},_suggestions_fen=None,engines=[],primary_engine_index=0,
 display_mode='pv',multi_pv_count=3,accuracy_candidate_limit=8,analysis_depth=12,book_manager=NS(cloud_enabled=False),pv_line=[],current_score=0,
 opening_book_enabled=False,_style_fallback={},_flag_mode_active=lambda:False,skill_profiles={},comparison_player_elo=2000,game_context={},depth_timer=DepthTimer())
 s.committed_multi_pvs={1:(['g1f3','b8c6'],'0.00'),2:(['b1c3','b8c6'],'-0.15'),3:(['f1c4','b8c6'],'-0.40')};s.multi_pvs={}
 s._suggestions_fen=s.current_fen
 return s
