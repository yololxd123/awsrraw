"""PGN helpers recovered from the supplied original eval_window.py.

Retains its grading rules, score encoding, colors, and boolean chess colors.
The only grading extension is an explicit unknown result when evaluations
are unavailable, preserving the earlier missing-data fix.
"""
import math

ANALYZER_CLS_ORDER = ("brilliant", "best", "excellent", "good",
                      "inaccuracy", "miss", "mistake", "blunder", "book", "unknown")
ANALYZER_CLS_COLORS = {
    "unknown":    "#9a92c9",
    "book":       "#8b8fa3",
    "brilliant":  "#58a6ff",
    "best":       "#2ecc71",
    "excellent":  "#55d98d",
    "good":       "#9fdc9f",
    "inaccuracy": "#f5c542",
    "miss":       "#f08c3a",
    "mistake":    "#f5a623",
    "blunder":    "#f87171",
}
ANALYZER_MATE = 100000
ANALYZER_PIECE_VALUES = {1: 1, 2: 3, 3: 3, 4: 5, 5: 9, 6: 0}  # P N B R Q K
_ANALYZER_GLYPHS = {
    "K": "\u2654", "Q": "\u2655", "R": "\u2656", "B": "\u2657",
    "N": "\u2658", "P": "\u2659",
    "k": "\u265a", "q": "\u265b", "r": "\u265c", "b": "\u265d",
    "n": "\u265e", "p": "\u265f",
}


def _win_prob(cp):
    """Logistic win probability (lichess/chess.com-style), cp in centipawns."""
    if cp is None:
        return 0.5
    return 1.0 / (1.0 + math.exp(-0.00368208 * cp))


def _score_to_cp(parts):
    """Parse 'info ... score cp X' / 'score mate N' from a UCI line.

    Returns a centipawn value from the SIDE-TO-MOVE point of view (positive =
    good for the side to move), or None.  Mate scores map to a huge cp value."""
    try:
        i = [p.lower() for p in parts].index("score")
    except ValueError:
        return None
    if i + 1 >= len(parts):
        return None
    kind = parts[i + 1].lower()
    try:
        if kind == "cp":
            return int(parts[i + 2])
        if kind == "mate":
            n = int(parts[i + 2])
            mag = ANALYZER_MATE - 20 * abs(n)
            return mag if n > 0 else -mag
    except (ValueError, IndexError):
        return None
    return None


def _move_accuracy(drop_pp):
    """The widely-used lichess-style per-move accuracy formula (0..100)."""
    return max(0.0, min(100.0, 103.1668 * math.exp(-0.04354 * max(0.0, drop_pp)) - 3.1669))


def _fmt_eval(cp):
    if cp is None:
        return ""
    if abs(cp) >= ANALYZER_MATE - 10000:
        n = max(1, (ANALYZER_MATE - abs(cp)) // 20)
        return ("+M%d" % n) if cp > 0 else ("-M%d" % n)
    return ("+%0.2f" % (cp / 100.0)) if cp >= 0 else ("%0.2f" % (cp / 100.0))


def _material_for(board, color):
    return sum(ANALYZER_PIECE_VALUES[p.piece_type]
               for sq, p in board.piece_map().items() if p.color == color)


def _classify_move(win_before, win_after, is_best, played_cp, mat_delta, is_book):
    """chess.com-style classification from the win% lost versus the best move.

    win_before / win_after are the mover's win probabilities (0..1) before and
    after the played move.  drop is in percentage points."""
    if is_book:
        return "book"
    if win_before is None or win_after is None or played_cp is None:
        return "unknown"
    drop = max(0.0, (win_before - win_after) * 100.0)
    if (mat_delta is not None and mat_delta <= -1.0
            and played_cp is not None and played_cp >= 0.30
            and drop <= 8.0):
        return "brilliant"
    if is_best:
        return "best"
    if win_before >= 0.85 and drop >= 5.0:
        return "miss" if drop < 20.0 else "blunder"
    if drop <= 2.0:
        return "excellent"
    if drop <= 5.0:
        return "good"
    if drop <= 10.0:
        return "inaccuracy"
    if drop <= 20.0:
        return "mistake"
    return "blunder"


