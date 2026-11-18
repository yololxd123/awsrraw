import random
import math
import time

class Humanizer:
    """Opponent prediction only in the GUI's current integration.

    Never writes engine settings. AutoMove has its own timing controls and plays
    the visible headline suggestion. Think-time helpers remain for API compatibility
    but are not called by AutoMove or the engine search.
    """

    SKILL_PRESETS = {
        "beginner":     {"blunder_chance": 0.18, "rank2": 0.30, "rank3": 0.15, "mean_ms": 2600, "std_ms": 1400},
        "intermediate": {"blunder_chance": 0.08, "rank2": 0.22, "rank3": 0.08, "mean_ms": 3200, "std_ms": 1800},
        "advanced":     {"blunder_chance": 0.03, "rank2": 0.15, "rank3": 0.04, "mean_ms": 3800, "std_ms": 2000},
        "master":       {"blunder_chance": 0.01, "rank2": 0.08, "rank3": 0.02, "mean_ms": 4500, "std_ms": 2400},
    }

    def __init__(self):
        self.enabled = True
        self.mean_ms = 3200
        self.std_ms = 1800
        self.min_ms = 400
        self.max_ms = 15000
        self.time_pressure_cutoff_ms = 30000
        self.time_pressure_factor = 0.35
        self.preset = "intermediate"
        self.blunder_chance = 0.08
        self.rank2_chance = 0.22
        self.rank3_chance = 0.08
        self.rank_decay = 0.4
        self.rank_pool_size = 3
        self.blunder_cooldown_moves = 4
        self._moves_since_blunder = 999
        self.opening_instant_plies = 10
        self.eval_swing_threshold = 1.5
        self._last_eval = None
        self._stats = {"moves_played": 0, "blunders": 0, "_think_sum": 0}
        # True once the user has touched any humanizer setting in the panel.
        # While False the runtime falls back to an ELO-derived skill preset;
        # once the user customises, their configuration is always respected.
        self._configured = False

    def apply_preset(self, name):
        p = self.SKILL_PRESETS.get(name)
        if not p:
            return
        self.preset = name
        self.blunder_chance = p["blunder_chance"]
        self.rank2_chance = p["rank2"]
        self.rank3_chance = p["rank3"]
        self.mean_ms = p["mean_ms"]
        self.std_ms = p["std_ms"]

    def _gauss(self, mean, std):
        u = v = 0
        while u == 0:
            u = random.random()
        while v == 0:
            v = random.random()
        return mean + std * math.sqrt(-2 * math.log(u)) * math.cos(2 * math.pi * v)

    def _clamp(self, t):
        return max(self.min_ms, min(self.max_ms, round(t)))

    def compute_think_time(self, in_book=False, ply=None, only_move=False,
                           eval_cp=None, opponent_just_blundered=False, clock_ms=None):
        if not self.enabled:
            return 0
        if in_book or (ply is not None and ply <= self.opening_instant_plies and ply % 2 == 0):
            return self._clamp(self._gauss(700, 350))
        if only_move:
            return self._clamp(self._gauss(900, 400))
        t = self._gauss(self.mean_ms, self.std_ms)
        if eval_cp is not None and self._last_eval is not None:
            swing = abs(eval_cp - self._last_eval) / 100
            if swing >= self.eval_swing_threshold:
                t *= 1.4 + min(swing / 10, 0.6)
        if opponent_just_blundered:
            t *= 1.25
        if clock_ms is not None and clock_ms < self.time_pressure_cutoff_ms:
            t *= self.time_pressure_factor
        result = self._clamp(t)
        self._stats["_think_sum"] += result
        return result

    def note_eval(self, eval_cp):
        self._last_eval = eval_cp

    def rank_weights(self, pool_size=None):
        count = max(1, int(pool_size or self.rank_pool_size or 1))
        if count < 2:
            return []
        r2 = max(0, self.rank2_chance)
        r3 = max(0, self.rank3_chance)
        decay = max(0, min(1, self.rank_decay))
        out = []
        for rank in range(2, count + 1):
            if rank == 2:
                out.append(r2)
            elif rank == 3:
                out.append(r3)
            else:
                out.append(out[-1] * decay)
        return out

    def distribution(self, pool_size=None, ignore_cooldown=True):
        can_blunder = ignore_cooldown or self._moves_since_blunder >= self.blunder_cooldown_moves
        blunder = can_blunder if self.enabled else 0
        blunder = blunder * max(0, self.blunder_chance) if blunder else 0
        weights = self.rank_weights(pool_size) if self.enabled else []
        if not self.enabled:
            blunder = 0
        total = blunder + sum(weights)
        if total > 1:
            scale = 1 / total
            blunder *= scale
            weights = [w * scale for w in weights]
        return {
            "best": max(0, 1 - blunder - sum(weights)),
            "ranks": weights,
            "blunder": blunder,
        }

    def pick_move(self, ranked_moves, pool_size=None):
        if not ranked_moves or not len(ranked_moves):
            return None
        if not self.enabled or len(ranked_moves) == 1:
            return {"move": ranked_moves[0]["move"], "rank_used": 1, "was_blunder": False}
        pool = max(1, min(int(pool_size or self.rank_pool_size or 3), len(ranked_moves)))
        roll = random.random()
        can_blunder = self._moves_since_blunder >= self.blunder_cooldown_moves
        blunder_p = can_blunder * self.blunder_chance
        weights = self.rank_weights(pool)
        total = blunder_p + sum(weights)
        if total > 1:
            scale = 1 / total
            blunder_p *= scale
            weights = [w * scale for w in weights]
        worst = ranked_moves[-1]
        if roll < blunder_p and len(ranked_moves) >= 2:
            self._moves_since_blunder = 0
            self._stats["blunders"] += 1
            return {"move": worst["move"], "rank_used": worst.get("rank", len(ranked_moves)), "was_blunder": True}
        self._moves_since_blunder += 1
        cum = blunder_p
        for i, w in enumerate(weights):
            cum += w
            mv = ranked_moves[i + 1] if i + 1 < len(ranked_moves) else None
            if roll < cum and mv:
                return {"move": mv["move"], "rank_used": mv.get("rank", i + 2), "was_blunder": False}
        return {"move": ranked_moves[0]["move"], "rank_used": ranked_moves[0].get("rank", 1), "was_blunder": False}

    def note_move_played(self):
        self._stats["moves_played"] += 1

    @property
    def stats(self):
        played = self._stats["moves_played"]
        return {
            **self._stats,
            "avg_think_ms": round(self._stats["_think_sum"] / played) if played else 0,
        }


# ============================================================================
# AUTOMOVE — HUMAN-LIKE MOUSE MOTION SIMULATION (ABCurves-inspired)
# ============================================================================
