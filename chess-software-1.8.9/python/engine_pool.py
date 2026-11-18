import os
import sys
import re
import time
import queue
import socket
import threading
import subprocess
import json
import ctypes
from ctypes import wintypes
from collections import OrderedDict, deque

try:
    import websocket as websocket_client
except ImportError:
    websocket_client = None

class PatternLibrary:
    """Classifies chess pawn structures and strategic motifs for pattern recognition."""
    @staticmethod
    def classify_position(fen):
        if not fen or not isinstance(fen, str):
            return "Standard Position"
        try:
            parts = fen.split()
            board_str = parts[0]
            if "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR" in board_str:
                return "Starting Position"
            elif "e4 c5" in fen or "e4 c5" in board_str:
                return "Sicilian Defense Motif"
            elif "e4 e5" in fen:
                return "Open Game Motif"
            elif "d4 d5" in fen:
                return "Closed Game Motif"
            
            # Count pawns in center files (c, d, e, f)
            pawn_count = board_str.lower().count('p')
            if pawn_count <= 8:
                return "Open Endgame Structure"
            elif pawn_count >= 14:
                return "Closed Tactical Pawn Structure"
            return "Balanced Strategic Position"
        except Exception:
            return "Standard Position"


class CECPEngineAdapter:
    """Translates UCI commands into WinBoard/CECP protocol for older engine compatibility."""
    @staticmethod
    def is_cecp(engine_line):
        return "xboard" in engine_line.lower() or "feature" in engine_line.lower()

    @staticmethod
    def get_init_commands():
        return ["xboard", "protover 2", "new", "random", "post"]


def _cache_locked(method):
    def locked(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return locked


class PositionCache:
    """LRU cache for engine analysis results. Keyed by compact FEN.
    Avoids re-analyzing positions that were recently searched.
    Uses OrderedDict for O(1) LRU operations instead of O(n) list scans.
    """
    def __init__(self, max_size=256, ttl_seconds=300):
        self._max = max_size
        self._ttl = ttl_seconds
        # OrderedDict gives O(1) move-to-end (MRU): compact -> (timestamp, depth, pvs, score)
        self._store = OrderedDict()
        self._lock = threading.RLock()

    def _compact_fen(self, fen):
        if not fen or not isinstance(fen, str):
            return ""
        # Keep the halfmove clock for draw rules; ignore only the fullmove number.
        parts = fen.split()
        if len(parts) >= 4:
            board, turn, castling, ep = parts[0], parts[1], parts[2], parts[3]
            if ep != "-":
                try:
                    ep_file = ord(ep[0]) - ord('a')
                    ep_rank = int(ep[1])
                    ranks = board.split("/")
                    pawn_rank_idx = (8 - ep_rank) + (1 if turn == 'w' else -1)
                    capture_pawn = 'P' if turn == 'w' else 'p'
                    can_capture = False
                    if 0 <= pawn_rank_idx < 8 and len(ranks) == 8:
                        row_expanded = []
                        for ch in ranks[pawn_rank_idx]:
                            if ch.isdigit():
                                row_expanded.extend(['.'] * int(ch))
                            else:
                                row_expanded.append(ch)
                        for adj in (ep_file - 1, ep_file + 1):
                            if 0 <= adj < 8 and len(row_expanded) == 8 and row_expanded[adj] == capture_pawn:
                                can_capture = True
                                break
                    if not can_capture:
                        ep = "-"
                except Exception:
                    pass
            return f"{board} {turn} {castling} {ep} {parts[4] if len(parts) > 4 else 0}"
        return fen

    def _key(self, fen, depth=0):
        return (self._compact_fen(fen), int(depth))

    def get(self, fen, depth=1):
        entry = self.get_with_depth(fen, depth)
        return entry[:2] if entry else None

    @_cache_locked
    def get_with_depth(self, fen, depth=1):
        """Include the actual analyzed depth for the optional canvas badge."""
        compact = self._compact_fen(fen)
        entry = self._store.get(compact)
        if entry is None:
            return None
        ts, d, pvs, score = entry
        if time.time() - ts > self._ttl:
            del self._store[compact]
            return None
        # A shallow snapshot must not masquerade as completed deeper analysis.
        if d < max(1, int(depth)):
            return None
        # Move to end (most recently used) — O(1)
        self._store.move_to_end(compact)
        return pvs, score, d

    @_cache_locked
    def put(self, fen, depth, pvs, score):
        compact = self._compact_fen(fen)
        if compact in self._store:
            existing = self._store[compact]
            existing_d = existing[1]
            existing_pvs = existing[2]
            # Don't replace deeper search results with a shallower early-search snapshot
            if int(depth) < existing_d and existing_pvs:
                self._store.move_to_end(compact)
                return
            self._store.move_to_end(compact)
        self._store[compact] = (time.time(), int(depth), pvs, score)
        # Evict oldest (first item) if over capacity — O(1)
        while len(self._store) > self._max:
            self._store.popitem(last=False)

    @_cache_locked
    def evict_expired(self):
        now = time.time()
        expired = [k for k, v in self._store.items() if now - v[0] > self._ttl]
        for k in expired:
            self._store.pop(k, None)


class EngineCommandQueue:
    """Manages command flow and back-pressure to UCI engines.
    Prevents pipe saturation and deadlocks by throttling redundant 'stop' commands."""
    def __init__(self, min_stop_interval=0.035):
        self._last_stop_time = {}   # engine_id -> timestamp
        self._min_stop_interval = min_stop_interval
        self._lock = threading.Lock()

    def safe_stop(self, engine):
        """Send 'stop' with back-pressure. Returns True if stop was dispatched."""
        proc = engine.get("process")
        if not proc or proc.poll() is not None:
            return False
        eng_id = id(engine)
        now = time.time()
        with self._lock:
            # If already stopping and less than min_stop_interval elapsed, avoid spamming pipe
            if engine.get("is_stopping", False):
                if now - self._last_stop_time.get(eng_id, 0) < self._min_stop_interval:
                    return False
            engine["is_stopping"] = True
            self._last_stop_time[eng_id] = now
            try:
                proc.stdin.write("stop\n")
                proc.stdin.flush()
                return True
            except (OSError, BrokenPipeError):
                return False

    def on_info_or_bestmove(self, engine):
        """Called when engine acknowledges search progress or completion."""
        engine["is_stopping"] = False


# ============================================================================
# ENGINE POOL — multi-engine priority ranking (ported from BetterMint)
# ============================================================================
class SocketEngineProcess:
    """Mimic subprocess.Popen for a hosted UCI engine over WebSocket.

    Exposes .stdin (write/flush), .stdout (readline), .poll(), .terminate(),
    .kill() and .wait() so the existing UCI reader thread and engine pool code
    work against a remote engine exactly as they do against a local binary.
    No engine download is required: the remote host runs the engine and
    streams raw UCI back over one WebSocket connection.
    """

    def __init__(self, url, name="", connect_timeout=20.0, recv_timeout=20.0):
        if websocket_client is None:
            raise RuntimeError(
                "websocket-client is not installed. Run:  pip install websocket-client")
        self.url = url
        self.name = name
        self._lock = threading.Lock()
        self._ws = None
        self._closed = False
        self._exit_code = None
        self._recv_timeout = recv_timeout
        self._pending_lines = deque()
        self._finished = threading.Event()
        try:
            self._ws = websocket_client.create_connection(
                url, timeout=connect_timeout, enable_multithread=True)
            self._ws.settimeout(recv_timeout)
        except Exception as exc:
            self._exit_code = 1
            self.close()
            raise RuntimeError(f"cannot connect to hosted engine {url}: {exc}")
        self.stdin = _SocketStdin(self)
        self.stdout = _SocketStdout(self)

    def send(self, data):
        with self._lock:
            if self._closed or self._ws is None:
                raise BrokenPipeError("hosted engine connection is closed")
            try:
                self._ws.send(str(data))
            except Exception as exc:
                self._exit_code = 1
                self.close()
                raise OSError("could not send to hosted engine") from exc

    def readline(self):
        # One WebSocket message can contain several UCI lines. Return one line
        # per call, as subprocess stdout does, without dropping the remainder.
        while self._pending_lines or (self._exit_code is None and not self._closed):
            if self._pending_lines:
                return self._pending_lines.popleft() + "\n"
            ws = self._ws
            if ws is None:
                return ""
            try:
                data = ws.recv()
            except websocket_client.WebSocketTimeoutException:
                continue  # a quiet search is normal; keep waiting
            except Exception:
                self._exit_code = 1
                self.close()
                return ""
            if data is None or data == "" or data == b"":
                self._exit_code = 1
                self.close()
                return ""
            if isinstance(data, (bytes, bytearray)):
                data = data.decode("utf-8", "replace")
            self._pending_lines.extend(str(data).splitlines())
        return ""

    def poll(self):
        if self._exit_code is not None:
            return self._exit_code
        ws = self._ws
        if ws is None:
            return 1
        try:
            if getattr(ws, "connected", True) is False:
                self._exit_code = 1
                self.close()
                return 1
        except Exception:
            pass
        return None

    def terminate(self):
        self.close()

    def kill(self):
        self.close()

    def wait(self, timeout=None):
        # Waiting observes process completion; terminate/kill request shutdown.
        if self.poll() is None and not self._finished.wait(timeout):
            raise subprocess.TimeoutExpired(self.url, timeout)
        return self._exit_code

    def close(self):
        self._closed = True
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        if self._exit_code is None:
            self._exit_code = 0
        self._finished.set()


class _SocketStdin:
    def __init__(self, owner):
        self._owner = owner

    def write(self, data):
        self._owner.send(data)

    def flush(self):
        pass


class _SocketStdout:
    def __init__(self, owner):
        self._owner = owner

    def readline(self):
        return self._owner.readline()


class EnginePool:
    """Manages multiple UCI engine processes with priority-based ranking.

    Ranking modes (mirrors BetterMint's EngineManager):
      PRIORITY  – engines listed in priority order; best move from highest-priority engine wins
      SMART     – all engine moves ranked purely by score (cp / mate)
      ROUNDROBIN – moves interleaved round-robin across engines by priority
    """

    RANKING_PRIORITY = "priority"
    RANKING_SMART = "smart"
    RANKING_ROUNDROBIN = "roundrobin"
    RANKING_MODES = (RANKING_PRIORITY, RANKING_SMART, RANKING_ROUNDROBIN)

    def __init__(self):
        self.engines = []               # list of engine dicts (same objects as ChessOverlay.engines)
        self.ranking_mode = self.RANKING_PRIORITY
        self.current_fen = None
        self._info_by_engine = {}       # engine_name -> {mpv_num -> info_dict}
        self._ranked_moves = []         # unified ranked move list
        self._lock = threading.RLock()  # protects _ranked_moves and _info_by_engine (re-entrant safe)
        self.cmd_queue = EngineCommandQueue()

    # ---- engine bookkeeping --------------------------------------------------

    def register(self, engine_dict):
        """Register an engine dict with the pool (adds priority field)."""
        if "priority" not in engine_dict:
            engine_dict["priority"] = self._next_priority()
        if engine_dict["name"] not in self._info_by_engine:
            self._info_by_engine[engine_dict["name"]] = {}
        self.engines = sorted(self.engines + [engine_dict], key=lambda e: e["priority"])

    def unregister(self, engine_dict):
        """Remove an engine from the pool."""
        self.engines = [e for e in self.engines if e is not engine_dict]
        self._info_by_engine.pop(engine_dict.get("name", ""), None)
        self._rerank()

    def _next_priority(self):
        if not self.engines:
            return 1
        return max(e.get("priority", 1) for e in self.engines) + 1

    def set_priority_order(self, ordered_names):
        """Reorder engines by name list; first name gets priority 1."""
        name_map = {e["name"]: e for e in self.engines}
        for i, name in enumerate(ordered_names):
            if name in name_map:
                name_map[name]["priority"] = i + 1
        self.engines.sort(key=lambda e: e["priority"])
        self._rerank()

    def set_skill(self, name, skill_level=None, uci_elo=None):
        """Push skill / UCI_Elo to a specific engine."""
        for eng in self.engines:
            if eng["name"] != name:
                continue
            proc = eng.get("process")
            if not proc or proc.poll() is not None:
                break
            try:
                if skill_level is not None:
                    proc.stdin.write(f"setoption name Skill Level value {skill_level}\n")
                if uci_elo is not None:
                    proc.stdin.write("setoption name UCI_LimitStrength value true\n")
                    proc.stdin.write(f"setoption name UCI_Elo value {uci_elo}\n")
                proc.stdin.flush()
            except (OSError, BrokenPipeError):
                pass
            break

    def status_list(self):
        """Return a list of engine status dicts sorted by priority."""
        return [
            {
                "name": e["name"],
                "priority": e.get("priority", 99),
                "alive": e.get("process") is not None and e["process"].poll() is None,
                "best_move": e.get("best_move"),
                "score": e.get("score"),
            }
            for e in self.engines
        ]

    # ---- ranking core --------------------------------------------------------

    def feed_info(self, engine_name, pv_num, depth, score_cp, score_mate, pv, fen):
        """Called from process_engine_line when an info line arrives."""
        with self._lock:
            by_mpv = self._info_by_engine.setdefault(engine_name, {})
            prev = by_mpv.get(pv_num)
            if prev and (prev.get("depth") or 0) > (depth or 0):
                return  # deeper info already recorded
            by_mpv[pv_num] = {
                "depth": depth,
                "scoreCp": score_cp,
                "scoreMate": score_mate,
                "pv": pv,
                "fen": fen,
            }
        self._rerank()

    def feed_snapshot(self, engine_name, depth, lines, fen):
        """Publish one complete, same-depth MultiPV iteration atomically."""
        with self._lock:
            if fen != self.current_fen:
                return
            self._info_by_engine[engine_name] = {
                rank: dict(info, depth=depth, fen=fen) for rank, info in lines.items()
            }
            self._rerank()

    def feed_bestmove(self, engine_name, move, fen):
        """Called when a bestmove arrives; updates the engine dict."""
        with self._lock:
            for eng in self.engines:
                if eng["name"] == engine_name:
                    eng["lastBestmove"] = move
                    eng["lastBestmoveFen"] = fen
                    break
        self._rerank()

    def clear_analysis(self, fen=None):
        """Clear all in-flight analysis (e.g. when a book move is played)."""
        with self._lock:
            self.current_fen = fen or self.current_fen
            self._info_by_engine.clear()
            for eng in self.engines:
                eng["search_fen"] = None
            self._ranked_moves = []

    def _rerank(self):
        """Unified ranking of moves from all engines — mirrors BetterMint's _rerank()."""
        with self._lock:
            per_engine = self._moves_by_engine_locked()
            if not per_engine:
                self._ranked_moves = []
                return

            ordered = []
            if self.ranking_mode == self.RANKING_SMART:
                for _, moves in per_engine:
                    ordered.extend(moves)
                ordered.sort(key=lambda m: _pool_score_value(m), reverse=True)
            elif self.ranking_mode == self.RANKING_ROUNDROBIN:
                max_depth = max((len(m) for _, m in per_engine), default=0)
                for i in range(max_depth):
                    for _, moves in per_engine:
                        if i < len(moves):
                            ordered.append(moves[i])
            else:  # PRIORITY
                for _, moves in per_engine:
                    ordered.extend(moves)

            # deduplicate by move
            seen = set()
            deduped = []
            for m in ordered:
                if m["move"] in seen:
                    continue
                seen.add(m["move"])
                m["rank"] = len(deduped) + 1
                deduped.append(m)
            self._ranked_moves = deduped

    def _moves_by_engine_locked(self):
        """Collect ranked moves per engine (must hold lock)."""
        per_engine = []
        for eng in self.engines:
            moves = []
            name = eng["name"]
            by_mpv = self._info_by_engine.get(name, {})
            if by_mpv:
                for mpv_num in sorted(by_mpv.keys()):
                    info = by_mpv[mpv_num]
                    pv = info.get("pv") or []
                    move = pv[0] if pv else None
                    if _pool_is_null_move(move):
                        continue
                    info_fen = info.get("fen")
                    if info_fen and self.current_fen and info_fen != self.current_fen:
                        continue
                    moves.append({
                        "move": move,
                        "engine": name,
                        "priority": eng.get("priority", 99),
                        "scoreCp": info.get("scoreCp"),
                        "scoreMate": info.get("scoreMate"),
                        "pv": pv,
                        "multipv": mpv_num,
                        "depth": info.get("depth"),
                        "rank": 0,
                    })
            elif eng.get("lastBestmove") and eng.get("lastBestmoveFen") == self.current_fen:
                moves.append({
                    "move": eng["lastBestmove"],
                    "engine": name,
                    "priority": eng.get("priority", 99),
                    "scoreCp": None,
                    "scoreMate": None,
                    "pv": [],
                    "multipv": 1,
                    "depth": None,
                    "rank": 0,
                })
            if moves:
                per_engine.append((eng, moves))
        return per_engine

    # ---- public query --------------------------------------------------------

    def get_ranked_moves(self):
        """Return a copy of the current ranked move list."""
        with self._lock:
            return list(self._ranked_moves)

    def get_best_move(self):
        """Return the top-ranked move dict, or None."""
        with self._lock:
            return self._ranked_moves[0] if self._ranked_moves else None

    def get_move_at_rank(self, rank):
        """Return the move at the given 1-indexed rank."""
        with self._lock:
            for m in self._ranked_moves:
                if m["rank"] == rank:
                    return m
            return None

    def get_rank_count(self):
        with self._lock:
            return len(self._ranked_moves)


def _pool_is_null_move(move):
    """Check for null / sentinel UCI moves."""
    if not move:
        return True
    m = str(move).strip().lower()
    if m in ("0000", "(none)", "none", "null"):
        return True
    if len(m) >= 4 and m[:2] == m[2:4]:
        return True
    return False


def _pool_score_value(m):
    """Numeric score for sorting: mate scores dwarf cp scores."""
    mate = m.get("scoreMate")
    if mate is not None:
        return 100000 - mate if mate > 0 else -100000 - mate
    return m.get("scoreCp") or 0


# ============================================================================
# PHASE 2 — HUMANIZER (ported from Humanizer.js)
# ============================================================================
