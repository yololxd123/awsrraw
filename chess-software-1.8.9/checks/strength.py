"""Production selector strength/strict regression scenarios."""
from selector_fixture import subject
from recommendation_policy import move_accuracy
for color in ['w','b']:
 s=subject();s.current_fen=s.current_fen.replace(' w ',f' {color} ').rsplit(' ',1)[0]+' 11'
 s.player_color=s.current_player_color=color;s._suggestions_fen=s.current_fen
 moves=['g1f3','b1c3','f1c4'] if color=='w' else ['g8f6','b8c6','f8c5']
 s.committed_multi_pvs={i+1:([m],v) for i,(m,v) in enumerate(zip(moves,['0.00','-0.15','-0.40']))}
 assert s._target_accuracy_candidate()[0]==3
 s.recommendation_strength_first=True
 assert s._target_accuracy_candidate()[0]==1
 assert 'advisory' in s.recommendation_accuracy_status
 assert s._compute_visible_suggestions()[0][1][0]==moves[0]
 assert s._automove_chosen_move()==moves[0]
 s._line_memory.expected=lambda *args:moves[1]
 s._accuracy_selection_cache=None
 assert s._target_accuracy_candidate()[0]==1
 s.committed_multi_pvs[2]=([moves[1]],'0.00')
 assert s._target_accuracy_candidate()[0]==2
 s.recommendation_strength_first=False
 assert s._target_accuracy_candidate()[0]==3
print('PASS strength/strict switch invalidates cache, both colors, rejects scored loss for continuity, equal-score continuation, renderer/AutoMove agree')
s=subject();s.recommendation_strength_first=True;s.current_fen=s.current_fen.rsplit(' ',1)[0]+' 11';s._suggestions_fen=s.current_fen
s.committed_multi_pvs={1:(['g1f3'],'FM3'),2:(['b1c3'],'0.00')}
assert s._target_accuracy_candidate()[0]==1
s._suggestions_fen=None;assert not s._compute_visible_suggestions()
print('PASS forced-mate priority and no early publication preserved')
print('Estimate for 0.40-pawn loss from equal:',round(move_accuracy(0,-40),2))


# Exact reported settings; all five display modes share the target and consumers.
for color in ['w', 'b']:
 s=subject();s.current_fen=s.current_fen.replace(' w ',f' {color} ').rsplit(' ',1)[0]+' 11'
 s.player_color=s.current_player_color=s.current_playing_as=color;s._suggestions_fen=s.current_fen
 moves=['g1f3','b1c3','f1c4','d2d3'] if color=='w' else ['g8f6','b8c6','f8c5','d7d6']
 s.target_recommendation_accuracy_min=50;s.target_recommendation_accuracy_max=85;s.target_recommendation_accuracy_priority=75
 s.aggressive_blunder_threshold=1.0
 continuation=[moves[1],'b8c6','g1f3','g8f6'] if color=='w' else [moves[1],'b1c3','g8f6','g1f3']
 s.committed_multi_pvs={1:([moves[0]],'0.00'),2:(continuation,'-0.70'),
                        3:([moves[2]],'-0.72'),4:([moves[3]],'-1.50')}
 s.current_score=-.72 if color=='w' else .72
 for mode,rank in [('pv',2),('bestmove',2),('multiPV',3),('others',2),('advPV',2)]:
  s.display_mode=mode
  selected=s._target_accuracy_candidate()
  assert selected[0]==rank,(color,mode,selected)
  assert 50<=s.recommendation_accuracy_estimate<=85
  assert abs(s.recommendation_accuracy_estimate-75)<2
  assert s._compute_visible_suggestions()[0][0]==rank
  assert s._mode_candidates(mode)[0][0]==rank
  assert s._automove_chosen_move()==moves[rank-1]
 # Late live score telemetry must not change this completed result on repaint.
 s.display_mode='multiPV';assert s._target_accuracy_candidate()[0]==3
 saved_score=s.current_score;s.current_score=0
 assert s._target_accuracy_candidate()[0]==3
 s.current_score=saved_score
 # User safety still overrides the percentage, and changing it refreshes cache.
 s.aggressive_blunder_threshold=.1
 assert s._target_accuracy_candidate()[0]==1 and 'unavailable' in s.recommendation_accuracy_status
 s.aggressive_blunder_filter_enabled=False;s.display_mode='pv'
 assert s._target_accuracy_candidate()[0]==2
 s.recommendation_strength_first=True
 assert s._target_accuracy_candidate()[0]==1
 # Policy disabled: style still works, without the accuracy override.
 s.recommendation_accuracy_enabled=False;s.recommendation_strength_first=False;s.display_mode='multiPV';s.aggressive_blunder_threshold=1.0
 assert s._compute_visible_suggestions()[0][0]==3
 # Opponent turn is isolated from human range/style targeting.
 s.current_player_color='b' if color=='w' else 'w'
 assert s._compute_visible_suggestions()[0][0]==1
print('PASS 50–85/75 across five modes and both colors, distinct aggressive/defensive picks, cache, logs/arrows/AutoMove, safety and policy/turn isolation')

s=subject();s.current_fen=s.current_fen.rsplit(' ',1)[0]+' 11';s._suggestions_fen=s.current_fen
s.target_recommendation_accuracy_min=50;s.target_recommendation_accuracy_max=85;s.target_recommendation_accuracy_priority=75
s.committed_multi_pvs={1:(['g1f3'],'0.00'),2:(['b1c3'],'-0.15')}
assert s._target_accuracy_candidate()[0]==2
assert 'unavailable' in s.recommendation_accuracy_status
# A single legal/scored candidate still publishes an honest estimate/status.
s.committed_multi_pvs={1:(['g1f3'],'0.00')}
assert s._compute_visible_suggestions()[0][0]==1
assert 'unavailable' in s.recommendation_accuracy_status
# Draw constraints keep their original reference rather than inflating accuracy.
s.committed_multi_pvs={1:(['g1f3'],'0.90'),2:(['b1c3'],'0.50')}
s.anti_draw_enabled=True
s._candidate_is_draw=lambda entry:entry[0]==1
assert s._target_accuracy_candidate()[0]==2
assert abs(s.recommendation_accuracy_estimate-move_accuracy(90,50))<1e-9
print('PASS range unavailable and single-root statuses, draw filter and original accuracy reference')
