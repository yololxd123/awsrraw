import math
import re
import threading
import requests

class CoachMode:
    """Move classification scoring and accuracy tracking."""

    CLASSES = {
        "brilliant":   {"label": "Brilliant",   "icon": "!!", "color": "#26c2a3", "weight": 10},
        "great_find":  {"label": "Great Find",   "icon": "!",  "color": "#5b8baf", "weight": 9},
        "best":        {"label": "Best",         "icon": "★", "color": "#81b64c", "weight": 8},
        "excellent":   {"label": "Excellent",    "icon": "✓", "color": "#81b64c", "weight": 7},
        "good":        {"label": "Good",         "icon": "✓", "color": "#95b776", "weight": 6},
        "book":        {"label": "Book",         "icon": "▤", "color": "#a88865", "weight": 5},
        "forced":      {"label": "Forced",       "icon": "=",  "color": "#8b8987", "weight": 4},
        "inaccuracy":  {"label": "Inaccuracy",   "icon": "?!", "color": "#f7c631", "weight": 3},
        "mistake":     {"label": "Mistake",      "icon": "?",  "color": "#ffa459", "weight": 2},
        "missed_win":  {"label": "Missed Win",   "icon": "??", "color": "#ff7769", "weight": 1},
        "blunder":     {"label": "Blunder",      "icon": "??", "color": "#fa412d", "weight": 0},
    }

    TIPS = {
        "brilliant":  ["A sacrifice that works. You gave up material and the position still favours you."],
        "great_find": ["Only move that holds. Anything else loses ground."],
        "best":       ["Engine's top choice."],
        "excellent":  ["Practically the best move."],
        "good":       ["Solid. Keeps your position healthy."],
        "book":       ["Known theory."],
        "forced":     ["No real choice here."],
        "inaccuracy": ["Slightly loose. There was a cleaner option."],
        "mistake":    ["This costs real ground. Look for the tactic you missed."],
        "missed_win": ["You had a winning line and let it slip."],
        "blunder":    ["Serious error. Check what your opponent threatens now."],
    }

    def __init__(self):
        self.enabled = False
        self.show_tips = True
        self.suggest_better = True
        self.coach_both = False
        self.show_running_accuracy = True
        self.tts_enabled = False
        self._positions = {}
        self._tally = {}
        self._opp_tally = {}
        self._acc_samples = []
        self._graded = set()
        self._last_report = None

    def reset(self):
        self._positions.clear()
        self._tally.clear()
        self._opp_tally.clear()
        self._acc_samples.clear()
        self._graded.clear()
        self._last_report = None

    def note_position(self, fen, best_move=None, eval_cp=None, second_cp=None,
                      legal_count=None, in_book=False):
        if not fen:
            return
        prev = self._positions.get(fen, {})
        self._positions[fen] = {
            "best_move": best_move if best_move is not None else prev.get("best_move"),
            "eval_cp": eval_cp if eval_cp is not None else prev.get("eval_cp"),
            "second_cp": second_cp if second_cp is not None else prev.get("second_cp"),
            "legal_count": legal_count if legal_count is not None else prev.get("legal_count"),
            "in_book": in_book if in_book else prev.get("in_book", False),
        }
        if len(self._positions) > 400:
            oldest = next(iter(self._positions))
            del self._positions[oldest]

    def classify(self, before, after_eval, move, material_delta=None):
        """Classify a move. before = note_position dict, after_eval = cp after move."""
        if not before or before.get("eval_cp") is None or after_eval is None:
            return None
        best_cp = before["eval_cp"]
        played_cp = -after_eval
        loss = best_cp - played_cp
        if before.get("legal_count") == 1:
            return "forced"
        if before.get("in_book"):
            return "book"
        is_best = move and before.get("best_move") and move == before["best_move"]
        if is_best and material_delta is not None and material_delta <= -200 and played_cp >= 50:
            return "brilliant"
        if is_best and before.get("second_cp") is not None and best_cp - before["second_cp"] >= 200:
            return "great_find"
        if is_best:
            return "best"
        if best_cp >= 300 and played_cp < 100:
            return "missed_win"
        if loss <= 10:
            return "excellent"
        if loss <= 50:
            return "good"
        if loss <= 100:
            return "inaccuracy"
        if loss <= 250:
            return "mistake"
        return "blunder"

    @staticmethod
    def _win_percent(cp):
        c = max(-1000, min(1000, cp or 0))
        return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * c)) - 1)

    @staticmethod
    def _move_accuracy(wp_before, wp_after):
        drop = max(0, wp_before - wp_after)
        acc = 103.1668 * math.exp(-0.04354 * drop) - 3.1669
        return max(0, min(100, acc))

    def report(self, fen_before, move, eval_after_cp, material_delta=None, is_ours=True):
        if not self.enabled:
            return None
        before = self._positions.get(fen_before)
        cls = self.classify(before, eval_after_cp, move, material_delta)
        if not cls:
            return None
        meta = self.CLASSES[cls]
        seed = sum(ord(c) for c in (move or ""))
        loss_cp = None
        if before and before.get("eval_cp") is not None and eval_after_cp is not None:
            loss_cp = max(0, round(before["eval_cp"] - (-eval_after_cp)))
        sample = None
        if before and before.get("eval_cp") is not None and eval_after_cp is not None:
            sample = self._move_accuracy(self._win_percent(before["eval_cp"]),
                                          self._win_percent(-eval_after_cp))
        stamp = f"{(fen_before or '').split(' ')[:2]}|{move}"
        already = stamp in self._graded
        if not already:
            self._graded.add(stamp)
        report = {
            "cls": cls, "label": meta["label"], "icon": meta["icon"], "color": meta["color"],
            "move": move, "loss_cp": loss_cp,
            "tip": self._pick_tip(cls, seed),
            "show_better": self.suggest_better and cls not in ("best", "brilliant") and bool(before and before.get("best_move")),
            "best_move": before.get("best_move") if before else None,
            "is_ours": is_ours, "counted": not already,
            "move_accuracy": round(sample * 10) / 10 if sample else None,
        }
        if not already:
            if is_ours:
                self._tally[cls] = self._tally.get(cls, 0) + 1
                if sample is not None:
                    self._acc_samples.append(sample)
            else:
                self._opp_tally[cls] = self._opp_tally.get(cls, 0) + 1
        self._last_report = report
        return report

    def _pick_tip(self, cls, seed):
        tips = self.TIPS.get(cls, [])
        if not tips or not self.show_tips:
            return ""
        return tips[abs(seed) % len(tips)]

    def accuracy(self):
        if self._acc_samples:
            return round(sum(self._acc_samples) / len(self._acc_samples))
        return self._accuracy_of(self._tally)

    def opponent_accuracy(self):
        return self._accuracy_of(self._opp_tally)

    def _accuracy_of(self, tally):
        entries = list(tally.items())
        if not entries:
            return None
        total = scored = 0
        for cls, n in entries:
            w = self.CLASSES.get(cls, {}).get("weight", 5)
            scored += w * n
            total += 10 * n
        return round((scored / total) * 100) if total else None

    def speak(self, report):
        """TTS via pyttsx3 (if available)."""
        if not report:
            return
        try:
            import pyttsx3
            engine = pyttsx3.init()
            text = report["label"]
            if self.show_tips and report.get("tip"):
                text += f". {report['tip']}"
            engine.say(text)
            engine.runAndWait()
        except Exception:
            pass


# ============================================================================
# PHASE 5 — EXTENSION INTERNALS PROTOCOL
# ============================================================================
# New WebSocket protocol messages for extension internals:
#   → { action: "execute_move", uci: "e2e4" }
#   → { action: "set_privacy", hidden: true }
#   → { action: "panic" }
#   → { action: "get_clocks" }
#   → { action: "get_exploits" }
#   ← { type: "clocks", self_ms, opp_ms }
#   ← { type: "move_played", ok: true }
#   ← { type: "site_exploits", features: [...] }
#   ← { type: "variant_detected", variant: "chess960" }
# ============================================================================


