"""Engine-referenced recommendation estimates; no engine configuration writes."""
import math


def win_percent(cp):
    # Stable logistic; avoid overflow on mate sentinels/extreme engine output.
    exponent = max(-700.0, min(700.0, -0.00368208 * float(cp)))
    return 100.0 / (1.0 + math.exp(exponent))


def move_accuracy(best_cp, candidate_cp):
    loss = max(0.0, win_percent(best_cp) - win_percent(candidate_cp))
    return max(0.0, min(100.0, 103.1668 * math.exp(-0.04354 * loss) - 3.1669))


def immediate_draw(fen, move):
    """Only provable immediate draws; FEN alone cannot establish repetition."""
    try:
        import chess
        board = chess.Board(fen)
        candidate = chess.Move.from_uci(move)
        if candidate not in board.legal_moves:
            return False
        board.push(candidate)
        return board.is_stalemate() or board.is_insufficient_material() or board.is_seventyfive_moves()
    except (ImportError, ValueError, TypeError):
        return False
