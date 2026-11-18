"""Bounded recommendation selection. Never starts engine searches."""
import math


def choose_candidate(candidates, history, low, high, priority, continuation=None,
                     order_candidates=None):
    """Target the closest safe estimate, then apply style near that priority.

    The caller owns mate/draw/user loss limits. There is no second hidden pawn
    cap. Styles may choose within two percentage points of the nearest available
    priority estimate, always inside the range when any candidate matches it.
    History remains for older integrations; it never creates catch-up mistakes.
    """
    if not candidates:
        return None, 'Waiting for candidates'
    low, high = sorted((float(low), float(high)))
    desired = max(low, min(high, float(priority)))
    in_range = [c for c in candidates if low <= c[3] <= high]
    if in_range:
        eligible = in_range
        status = 'In range; accuracy target takes priority'
    else:
        def distance(candidate):
            return max(low-candidate[3], 0.0, candidate[3]-high)
        closest = min(distance(c) for c in candidates)
        eligible = [c for c in candidates if distance(c) <= closest + 1e-9]
        status = ('Range unavailable within current safe candidates; '
                  'using the closest estimate (no extra search)')
    nearest = min(abs(c[3]-desired) for c in eligible)
    if order_candidates is not None:
        shortlist = [c for c in eligible if abs(c[3]-desired) <= nearest + 2.0 + 1e-9]
        selected = order_candidates(shortlist)[0]
        status += '; style applied near priority'
    else:
        selected = min(eligible, key=lambda c: (abs(c[3]-desired),
                       c[1][0][0] != continuation, -c[2], c[0]))
    return selected, status


class LineMemory:
    """Tracks legal PV follow-ups across actual moves, never guessed move themes."""
    def __init__(self):
        self.lines = {}
        self.recorded = set()
        self.roots = {}
        self.active = {}

    @staticmethod
    def key(fen):
        import chess
        return ' '.join(chess.Board(fen).fen(en_passant='fen').split()[:4])

    def expected(self, fen, color):
        try:
            import chess
            key=self.key(fen)
            move=self.lines.get((key,color))
            active=self.active.get(color,{})
            if not move and active.get('position')==key:
                move=active.get('move')
            return move if move and chess.Move.from_uci(move) in chess.Board(fen).legal_moves else None
        except (ImportError, ValueError):
            return None

    def advance(self, previous, move, current):
        """Carry a proposed follow-up across the actual played move/reply.

        A different opponent reply may leave the same follow-up sensible; it is
        still eligible only if legal and freshly evaluated near the best move.
        """
        try:
            import chess
            old=self.key(previous)
            new=self.key(current)
            board=chess.Board(previous)
            actual=chess.Move.from_uci(move)
            if actual not in board.legal_moves:
                self.active.clear()
                return
            mover='w' if board.turn else 'b'
            board.push(actual)
            if self.key(board.fen())!=new:
                self.active.clear()
                return
            for color in list(self.active):
                state=self.active[color]
                if color!=mover:
                    if state.get('reply_position')==old:
                        state['position']=new
                    else:
                        self.active.pop(color,None)
            self.active.pop(mover,None)
            line=self.roots.get((old,mover),[])
            if len(line)>2 and line[0]==move:
                self.active[mover]={'reply_position':new,'move':line[2]}
        except (ImportError,ValueError,TypeError):
            self.active.clear()

    def remember(self, fen, moves, color):
        # Record once per root. Repaints must not rewrite a previous plan.
        try:
            import chess
            key = (self.key(fen), color)
            if key in self.recorded or not moves:
                return
            self.recorded.add(key)
            self.roots[key]=list(moves[:8])
            board = chess.Board(fen)
            for index, text in enumerate(moves[:8]):
                move = chess.Move.from_uci(text)
                if move not in board.legal_moves:
                    break
                if index >= 2 and ('w' if board.turn else 'b') == color:
                    self.lines[(self.key(board.fen()), color)] = text
                board.push(move)
            if len(self.recorded) > 128:
                self.recorded = {key}
                self.lines.clear()
                self.roots={key:list(moves[:8])}
        except (ImportError, ValueError, TypeError):
            return


def clean_context(value):
    """Small allowlist for page data. Missing/invalid values remain unknown."""
    if not isinstance(value, dict):
        value = {}
    result = {}
    for name, maximum in [('self_rating',4000),('opponent_rating',4000),
                          ('self_ms',86400000),('opp_ms',86400000),
                          ('initial_seconds',86400),('increment_seconds',3600)]:
        v = value.get(name)
        if isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and 0 <= v <= maximum:
            result[name] = int(v)
        else:
            result[name] = None
    for name in ('site','game_id','speed','variant'):
        v=value.get(name)
        result[name]=v[:100] if isinstance(v,str) else None
    result['rated']=value.get('rated') if isinstance(value.get('rated'),bool) else None
    return result
