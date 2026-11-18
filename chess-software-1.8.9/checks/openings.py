"""Production selector opening/book regression scenarios; no display required."""
from types import SimpleNamespace as NS
from selector_fixture import subject
s=subject();assert s._target_accuracy_candidate()[0]==1 and 'Opening protection' in s.recommendation_accuracy_status
for mode in ['pv','bestmove','multiPV','others','advPV']:
 s.display_mode=mode;assert s._compute_visible_suggestions()[0][1][0]=='g1f3',mode
s=subject(s.current_fen.rsplit(' ',1)[0]+' 10');assert s._opening_protected() and s._target_accuracy_candidate()[0]==1
s=subject(s.current_fen.rsplit(' ',1)[0]+' 11');assert not s._opening_protected() and s._target_accuracy_candidate()[0]==3
print('PASS opening moves 1–10 ignore the range, all suggestion modes, strict range resumes move11')
s=subject();s.opening_book_enabled=True;s.opening_book=NS(lookup=lambda f:[('f1c4',1000),('h2h5',999),('b1c3',50),('g1f3',10)])
book=s._opening_book_lines();assert [x[1][0] for x in book]==['b1c3','g1f3'],book
assert s._target_accuracy_candidate()[0]==2
visible=s._compute_visible_suggestions();assert visible[0][1][0]=='b1c3' and visible[0][0]==2
assert len({e[1][0] for e in visible})==len(visible)
s._predict_opponent_move();assert s.opponent_move=='b8c6'
s.aggressive_blunder_threshold=.1;assert s._target_accuracy_candidate()[0]==1
s.aggressive_blunder_threshold=.5;s.committed_multi_pvs[2]=(['b1c3'],'-2.00');assert s._target_accuracy_candidate()[0]==1
print('PASS weighted book choice restricted to scored roots within .25/tighter user bound, unsafe/absent roots rejected, no duplicate arrows, coherent reply')
s=subject();s.book_manager.cloud_enabled=True;s._cloud_lines_fen=s.current_fen;s._cloud_lines_result=[{'uci':'b1c3','pct':70},{'uci':'f1c4','pct':99}]
assert s._target_accuracy_candidate()[0]==2 and 'book' in s.recommendation_accuracy_status
s=subject();s.book_manager.cloud_enabled=True
assert s._target_accuracy_candidate()[0]==1
s._cloud_lines_fen=s.current_fen;s._cloud_lines_result=[{'uci':'b1c3','pct':99}]
assert s._target_accuracy_candidate()[0]==1 and s._compute_visible_suggestions()[0][1][0]=='g1f3'
s._validated_book_cache=None;s._accuracy_selection_cache=None
assert s._target_accuracy_candidate()[0]==2
print('PASS cached cloud can guide completed results, late arrivals cannot change an already displayed move')
s=subject();s._suggestions_fen=None;s.opening_book_enabled=True;s.opening_book=NS(lookup=lambda f:[('b1c3',100)])
assert not s._opening_book_lines() and not s._compute_visible_suggestions()
s._suggestions_fen=s.current_fen;s.committed_multi_pvs={1:(['g1f3'],'FM3'),2:(['b1c3'],'0.00')}
assert not s._opening_book_lines() and s._target_accuracy_candidate()[0]==1
s=subject();s.recommendation_accuracy_enabled=False;s.opening_book_enabled=True;s.opening_book=NS(lookup=lambda f:[('b1c3',100)])
assert s._compute_visible_suggestions()[0][1][0]=='b1c3'
s=subject();s.current_fen=s.current_fen.replace(' w ',' b ');s.current_player_color='b';s.player_color='b'
s.committed_multi_pvs={1:(['g8f6'],'0.00'),2:(['b8c6'],'-0.15'),3:(['f8c5'],'-0.40')}
s._suggestions_fen=s.current_fen;s.opening_book_enabled=True;s.opening_book=NS(lookup=lambda f:[('b8c6',100)])
assert s._target_accuracy_candidate()[0]==2 and s._compute_visible_suggestions()[0][1][0]=='b8c6'
s.current_player_color='w';assert not s._opening_book_lines()
print('PASS full depth gate, forced mate priority, accuracy-off book selection, Black POV and opponent-turn isolation')
s=subject();s._line_memory.expected=lambda fen,color:'b1c3'
assert s._target_accuracy_candidate()[0]==2 and 'continuing' in s.recommendation_accuracy_status
s.committed_multi_pvs[2]=(['b1c3'],'-0.30');assert s._target_accuracy_candidate()[0]==1
s=subject();s.opening_book_enabled=True;s.opening_book=NS(lookup=lambda f:[('b1c3',100)])
s.committed_multi_pvs[2]=(['b1c3'],'-0.25');assert s._target_accuracy_candidate()[0]==2
s.committed_multi_pvs[2]=(['b1c3'],'-0.251');assert s._target_accuracy_candidate()[0]==1
s=subject();s.committed_multi_pvs={1:(['g1f3'],'0.40'),2:(['b1c3'],'0.20'),3:(['f1c4'],'0.00')}
s.anti_draw_enabled=True;s.opening_book_enabled=True;s.opening_book=NS(lookup=lambda f:[('f1c4',1000),('b1c3',100)])
assert [line[1][0] for line in s._opening_book_lines()]==['b1c3'] and s._target_accuracy_candidate()[0]==2
s=subject();s.opening_book_enabled=True;s.opening_book=NS(lookup=lambda f:[('b1c3',100)])
assert s._target_accuracy_candidate()[0]==2
s.opening_book=NS(lookup=lambda f:[('g1f3',100)]);assert s._target_accuracy_candidate()[0]==1
print('PASS opening continuity/release, precise .25 boundary, draw rules and book replacement invalidation')

s=subject();s.engines=[{'search_generation':1}];s.book_manager.cloud_enabled=True
assert s._target_accuracy_candidate()[0]==1
s._cloud_lines_fen=s.current_fen;s._cloud_lines_result=[{'uci':'b1c3','pct':99}]
assert s._target_accuracy_candidate()[0]==1
s.engines[0]['search_generation']=2
assert s._target_accuracy_candidate()[0]==2
print('PASS new completed search generation refreshes book snapshot, same-result repaint remains frozen')
