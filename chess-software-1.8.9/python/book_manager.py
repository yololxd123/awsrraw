import os
import time
import sys
import struct
import threading
import requests

class PolyglotBook:
    """Reads Polyglot .bin opening book files to provide 0ms instant book move recommendations.

    Polyglot binary format (16-byte entries):
        bytes 00-07 : uint64  zobrist hash (big-endian)
        bytes 08-09 : uint16  move (little/big endian per spec, see _decode_move)
        bytes 10-11 : uint16  weight (higher = more often played)
        bytes 12-15 : uint32  learn (ignored)
    Files are sorted ascending by hash, so we binary-search for the FEN's hash then read
    consecutive matching entries.
    """

    # Maps each square index map for 13 pieces × 64 squares for Zobrist hashing
    _RANKS = "87654321"
    _FILES = "abcdefgh"

    def __init__(self, path=None):
        self.path = path
        self.file = None
        self._file_lock = threading.RLock()
        self._entry_size = 16
        self._count = 0
        if path and os.path.exists(path):
            try:
                self.file = open(path, "rb")
                self.file.seek(0, os.SEEK_END)
                self._count = self.file.tell() // self._entry_size
            except Exception as e:
                print(f"[Polyglot Book Error]: {e}")

    _POLY_RNG_TABLE = None

    @staticmethod
    def _build_zobrist_table():
        from chess.polyglot import POLYGLOT_RANDOM_ARRAY
        return list(POLYGLOT_RANDOM_ARRAY)


    @classmethod
    def _zobrist_hash(cls, fen):
        import chess
        import chess.polyglot
        return chess.polyglot.zobrist_hash(chess.Board(fen))


    @staticmethod
    def _decode_move(raw_move):
        """Polyglot 16-bit move → UCI move.

        Layout (LSB first):
          bits 0-2  to-file    (0=a..7=h)
          bits 3-5  to-rank    (0=1..7=8)
          bits 6-8  from-file
          bits 9-11 from-rank
          bits 12-14 promotion: 0=none, 1=knight, 2=bishop, 3=rook, 4=queen
        Castling encoded as "king captures own rook" → translate to king-destination.
        """
        tf = (raw_move >> 0) & 0x7
        tr = (raw_move >> 3) & 0x7
        ff = (raw_move >> 6) & 0x7
        fr = (raw_move >> 9) & 0x7
        pr = (raw_move >> 12) & 0x7
        files = "abcdefgh"
        ranks = "12345678"
        from_sq = files[ff] + ranks[fr]
        to_sq = files[tf] + ranks[tr]
        prom_map = {1: "n", 2: "b", 3: "r", 4: "q"}
        if pr in prom_map:
            return from_sq + to_sq + prom_map[pr]
        castles = {
            ("e1","h1"): "e1g1", ("e1","a1"): "e1c1",
            ("e8","h8"): "e8g8", ("e8","a8"): "e8c8",
        }
        return castles.get((from_sq, to_sq), from_sq + to_sq)

    def lookup(self, fen):
        with self._file_lock:
            return self._lookup_locked(fen)

    def _lookup_locked(self, fen):
        """Returns list of (uci_move, weight) sorted by weight descending."""
        if not self.file or self._count == 0:
            return []
        try:
            h = self._zobrist_hash(fen)
            if h == 0:
                return []
            lo, hi = 0, self._count
            # Binary search for the first entry with hash >= target
            while lo < hi:
                mid = (lo + hi) // 2
                self.file.seek(mid * self._entry_size, os.SEEK_SET)
                rec = self.file.read(8)
                if len(rec) < 8:
                    return []
                rec_hash = int.from_bytes(rec, "big", signed=False)
                if rec_hash < h:
                    lo = mid + 1
                else:
                    hi = mid
            results = []
            cur = lo
            while cur < self._count:
                self.file.seek(cur * self._entry_size, os.SEEK_SET)
                buf = self.file.read(self._entry_size)
                if len(buf) < self._entry_size:
                    break
                rec_hash = int.from_bytes(buf[0:8], "big", signed=False)
                if rec_hash != h:
                    break
                raw = int.from_bytes(buf[8:10], "big", signed=False)
                weight = int.from_bytes(buf[10:12], "big", signed=False)
                uci = self._decode_move(raw)
                if uci:
                    results.append((uci, int(weight)))
                cur += 1
            results.sort(key=lambda x: x[1], reverse=True)
            return results
        except Exception as exc:
            print(f"[Book Lookup] {exc}")
            return []

    def close(self):
        with self._file_lock:
            self._close_locked()

    def _close_locked(self):
        if self.file:
            try: self.file.close()
            except Exception: pass
            self.file = None
            self._count = 0



class BookManager:
    """Opening book + Lichess cloud explorer + Syzygy tablebase probing."""

    STAGE_PRE_GAME = "pre-game"
    STAGE_OPENING = "opening"
    STAGE_MIDDLEGAME = "middlegame"
    STAGE_ENDGAME = "endgame"

    def __init__(self):
        self.cloud_enabled = True
        self.cloud_source = "masters"
        self.cloud_cache = {}
        self._cloud_cached_at = {}
        self._cloud_cache_lock = threading.RLock()
        self.cloud_cooldown_until = 0
        self.cloud_fails = 0
        self.tb_enabled = True
        self.tb_use_online = True
        self.tb_max_pieces = 7
        self.tb_cache = {}
        self.tb_cache_ttl = 30.0
        self.stage_show = True
        self.stage_endgame_max_pieces = 7
        self.stage_opening_max_ply = 20
        self.stage_endgame_phase = 0.30
        self.lichess_token = ""

    # Stockfish-style non-pawn material weights used to derive the game phase.
    PHASE_WEIGHT = {"p": 0, "n": 1, "b": 1, "r": 2, "q": 4}

    def _game_phase(self, fen):
        """0.0 (bare kings) .. 1.0 (full opening material).

        Uses the classic non-pawn-material phase measure (N/B=1, R=2, Q=4 per
        side; kings and pawns don't count; full set = 24 points).  Queens off
        the board or heavy trades drag the phase toward the endgame boundary
        even while the ply counter is still low."""
        try:
            board_str = fen.split()[0]
            total = sum(self.PHASE_WEIGHT.get(c.lower(), 0)
                        for c in board_str if c.isalpha())
            return min(1.0, total / 24.0)
        except Exception:
            return 1.0

    def detect_stage(self, fen, ply=None):
        """Detect game stage from position.

        ``ply`` is derived from the FEN move number when not given, so a
        half-open board still reads as OPENING at the start of a game.  The
        endgame check is material-based (Stockfish-style phase AND total piece
        count), so a quiet trade-down or a queen exchange is called an endgame
        as soon as the remaining material is really low, instead of waiting for
        the ply counter to tick over.
        """
        if not fen or not isinstance(fen, str) or len(fen.split()) < 2:
            return self.STAGE_PRE_GAME
        try:
            parts = fen.split()
            board_str = parts[0]
            if len(board_str.split("/")) != 8:
                return self.STAGE_PRE_GAME
            if ply is None:
                ply = 0
                try:
                    fullmove = int(parts[5])
                    ply = (fullmove - 1) * 2 + (1 if parts[1].lower() == "b" else 0)
                except (IndexError, ValueError):
                    pass
            piece_count = sum(1 for c in board_str if c.isalpha())
            phase = self._game_phase(fen)
            if (piece_count <= self.stage_endgame_max_pieces
                    or phase < self.stage_endgame_phase):
                return self.STAGE_ENDGAME
            if ply <= self.stage_opening_max_ply:
                return self.STAGE_OPENING
            return self.STAGE_MIDDLEGAME
        except Exception:
            return self.STAGE_PRE_GAME

    def stage_meta(self, stage):
        return {
            self.STAGE_PRE_GAME: {"label": "SETUP", "color": "#8b8fa3"},
            self.STAGE_OPENING: {"label": "OPENING", "color": "#ff9800"},
            self.STAGE_MIDDLEGAME: {"label": "MIDGAME", "color": "#38bdf8"},
            self.STAGE_ENDGAME: {"label": "ENDGAME", "color": "#7a5cff"},
        }.get(stage, {"label": "?", "color": "#888"})

    def _prune_cloud_cache(self):
        now = time.monotonic()
        with self._cloud_cache_lock:
            for key in list(self.cloud_cache):
                created = self._cloud_cached_at.setdefault(key, now)
                ttl = 30.0 if self.cloud_cache[key] is None else 300.0
                if now - created >= ttl:
                    self.cloud_cache.pop(key, None)
                    self._cloud_cached_at.pop(key, None)
            for key in list(self._cloud_cached_at):
                if key not in self.cloud_cache:
                    self._cloud_cached_at.pop(key, None)
            while len(self.cloud_cache) > 512:
                key = next(iter(self.cloud_cache))
                self.cloud_cache.pop(key, None)
                self._cloud_cached_at.pop(key, None)

    def _get_cloud_cached(self, key):
        with self._cloud_cache_lock:
            self._prune_cloud_cache()
            return key in self.cloud_cache, self.cloud_cache.get(key)

    def _set_cloud_cached(self, key, value):
        with self._cloud_cache_lock:
            self.cloud_cache[key] = value
            self._cloud_cached_at[key] = time.monotonic()
            self._prune_cloud_cache()

    def cloud_eval(self, fen, multipv=3):
        """Query Lichess cloud eval. Returns dict or None."""
        if not self.cloud_enabled or not fen:
            return None
        key = f"{fen}|{multipv}"
        hit, cached = self._get_cloud_cached(key)
        if hit:
            return cached
        now = time.time()
        if now < self.cloud_cooldown_until:
            return None
        try:
            headers = {"Accept": "application/json"}
            if self.lichess_token:
                headers["Authorization"] = f"Bearer {self.lichess_token}"
            resp = requests.get(
                f"https://lichess.org/api/cloud-eval?fen={requests.utils.quote(fen)}&multiPv={multipv}",
                headers=headers, timeout=4,
            )
            if resp.status_code in (403, 404):
                self._set_cloud_cached(key, None)
                self.cloud_fails = 0
                return None
            if resp.status_code == 429:
                self._cloud_backoff("429")
                return None
            resp.raise_for_status()
            data = resp.json()
            if not data.get("pvs"):
                self._set_cloud_cached(key, None)
                return None
            out = {
                "depth": data.get("depth"),
                "lines": [
                    {"move": p["moves"].split()[0], "pv": p["moves"].split(),
                     "score_cp": p.get("cp"), "score_mate": p.get("mate"), "rank": i + 1}
                    for i, p in enumerate(data["pvs"])
                ],
            }
            self._set_cloud_cached(key, out)
            self.cloud_fails = 0
            return out
        except Exception:
            self._cloud_backoff("err")
            return None

    def _cloud_backoff(self, reason):
        self.cloud_fails += 1
        base = 60000 if reason == "429" else 5000
        wait = min(base * 2 ** (self.cloud_fails - 1), 600000)
        self.cloud_cooldown_until = time.time() + wait / 1000

    def cloud_lines(self, fen, limit=5):
        """Query the Lichess opening explorer for the most-played moves.

        The datasource is driven by ``cloud_source`` (masters / lichess /
        chessdb), and ``lichess_token`` is attached as a Bearer header when
        present, so both panel settings are real.  Returns a list of dicts
        ``{uci, plays, pct, win_pct}`` sorted by popularity, or None when
        disabled / failed / not found.  Results (including misses) are cached
        with bounded lifetimes to allow later retries and limit memory use.
        """
        if not self.cloud_enabled or not fen:
            return None
        src = self.cloud_source if self.cloud_source in ("masters", "lichess", "chessdb") else "masters"
        key = f"explorer:{src}:{fen}:{limit}"
        hit, cached = self._get_cloud_cached(key)
        if hit:
            return cached
        now = time.time()
        if now < self.cloud_cooldown_until:
            return None
        try:
            headers = {"Accept": "application/json"}
            if self.lichess_token:
                headers["Authorization"] = f"Bearer {self.lichess_token}"
            resp = requests.get(
                f"https://explorer.lichess.ovh/{src}",
                params={"fen": fen, "moves": str(max(1, min(10, int(limit))))},
                headers=headers, timeout=4,
            )
            if resp.status_code in (403, 404):
                self._set_cloud_cached(key, None)
                self.cloud_fails = 0
                return None
            if resp.status_code == 429:
                self._cloud_backoff("429")
                return None
            resp.raise_for_status()
            data = resp.json()
            moves = data.get("moves") or []
            if not moves:
                self._set_cloud_cached(key, None)
                return None
            total = sum(int(m.get(k, 0) or 0) for m in moves
                        for k in ("white", "draws", "black")) or 1
            out = []
            for m in moves[:limit]:
                uci = m.get("uci")
                if not uci:
                    continue
                plays = sum(int(m.get(k, 0) or 0) for k in ("white", "draws", "black"))
                scored = int(m.get("white", 0) or 0) + int(m.get("black", 0) or 0) + int(m.get("draws", 0) or 0)
                win_pct = (int(m.get("white", 0) or 0) / scored * 100.0) if scored else 0.0
                out.append({"uci": uci, "plays": plays,
                            "pct": plays / total * 100.0, "win_pct": win_pct})
            self._set_cloud_cached(key, out if out else None)
            self.cloud_fails = 0
            return out or None
        except Exception:
            self._cloud_backoff("err")
            return None


    def tb_probe(self, fen):
        """Probe Syzygy tablebase (online fallback). Returns dict or None.

        Results are cached per-FEN with a short TTL so the engine-reader
        thread never repeats the same 3s blocking HTTP call for a position
        that has not changed.
        """
        if not self.tb_enabled or not fen:
            return None
        now = time.time()
        hit = self.tb_cache.get(fen)
        if hit is not None and now - hit["t"] < self.tb_cache_ttl:
            return hit["value"]
        board_str = fen.split()[0]
        piece_count = sum(1 for c in board_str if c.isalpha())
        if piece_count > self.tb_max_pieces:
            return None
        value = None
        if self.tb_use_online:
            try:
                resp = requests.get(
                    f"https://tablebase.lichess.ovh/standard?fen={requests.utils.quote(fen)}",
                    timeout=3,
                )
                resp.raise_for_status()
                data = resp.json()
                dtz = data.get("dtz")
                dtm = data.get("dtm")
                category = data.get("category", "")
                value = {"dtz": dtz, "dtm": dtm, "category": category,
                         "best_move": data.get("moves", [{}])[0].get("uci") if data.get("moves") else None}
            except Exception:
                value = None
        # Cache the outcome (including a failed probe) so we do not hammer the
        # API while the same position stays on screen.
        if len(self.tb_cache) > 256:
            self.tb_cache.clear()
        self.tb_cache[fen] = {"t": now, "value": value}
        return value


# ============================================================================
# PHASE 4 — COACH MODE (ported from Coach.js)
# ============================================================================
