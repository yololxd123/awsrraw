"""Conservative annotations based on legal engine continuations.

Sacrifices are material-loss opportunities, not another site's classifications.
"""
from functools import lru_cache

try:
    import chess
except ImportError:
    chess = None

_VALUES = {1: 1, 2: 3, 3: 3, 4: 5, 5: 9}


def chess_available():
    return chess is not None


def _material(board, side):
    return sum(value * (len(board.pieces(kind, side)) - len(board.pieces(kind, not side)))
               for kind, value in _VALUES.items())


def _exchange_value(board, square, side, remaining):
    """Approximate best material result if either player may stop exchanging."""
    current = _material(board, side)
    if remaining <= 0:
        return current
    maximizing = board.turn == side
    best = current
    for move in board.generate_legal_captures(to_mask=chess.BB_SQUARES[square]):
        board.push(move)
        value = _exchange_value(board, square, side, remaining - 1)
        board.pop()
        best = max(best, value) if maximizing else min(best, value)
    return best


def _offered_sacrifice(board, move):
    """Find a legal capture of the moved piece that wins material in exchanges.

    The opponent need not accept it in the engine's principal variation. This
    is a material-only heuristic; the engine score separately constrains quality.
    """
    piece = board.piece_at(move.from_square)
    if piece is None or piece.piece_type not in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
        return False
    side = board.turn
    initial = _material(board, side)
    board.push(move)
    try:
        for reply in board.generate_legal_captures(to_mask=chess.BB_SQUARES[move.to_square]):
            board.push(reply)
            try:
                # The opponent has accepted the offer. Both sides may then
                # choose to continue or stop exchanging on that square.
                if _exchange_value(board, move.to_square, side, 6) <= initial - 1:
                    return True
            finally:
                board.pop()
        return False
    finally:
        board.pop()


@lru_cache(maxsize=512)
def _line_features(fen, moves):
    try:
        if chess is None:
            return frozenset()
        board = chess.Board(fen)
        side = board.turn
        was_in_check = board.is_check()
        first_move = chess.Move.from_uci(moves[0])
        if first_move not in board.legal_moves:
            return frozenset()
        sacrifice = _offered_sacrifice(board, first_move)
        forcing = 0
        first_forcing = False
        for index, text in enumerate(moves):
            move = chess.Move.from_uci(text)
            if move not in board.legal_moves:
                break
            capture = board.is_capture(move)
            mover = board.turn
            board.push(move)
            if index == 0:
                first_forcing = capture or board.is_check()
            if mover == side and (capture or board.is_check()):
                forcing += 1
        features = set()
        if first_forcing:
            features.add('forcing')
        if sacrifice:
            features.add('sacrifice')
        if forcing >= 2:
            features.add('defensive_tactic' if was_in_check else 'offensive_tactic')
        return frozenset(features)
    except (ValueError, IndexError, TypeError, AttributeError):
        return frozenset()


def line_features(fen, moves):
    try:
        return set(_line_features(fen, tuple(moves[:6])))
    except (TypeError, ValueError):
        return set()
