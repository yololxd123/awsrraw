import tkinter as tk
from tkinter import messagebox, ttk, filedialog
import time
import queue
import threading
import subprocess
import websockets
import asyncio
import json
import ctypes
from ctypes import wintypes
import keyboard
import os
import requests
import re
import atexit
from datetime import datetime, timedelta
import sys
import platform
import hashlib
import secrets
import uuid
import math
import random
from settings_store import SettingsWriter
from engine_session import UciSession
from depth_timer import DepthTimer
from adaptive_policy import choose_candidate, LineMemory, clean_context
from recommendation_policy import win_percent, move_accuracy, immediate_draw
from suggestion_modes import line_features, chess_available

# Create a Windows Job Object to guarantee child engine processes are killed on exit
_job_handle = None
if sys.platform == 'win32':
    try:
        kernel32 = ctypes.windll.kernel32
        _job_handle = kernel32.CreateJobObjectW(None, None)
        if _job_handle:
            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ('PerProcessUserTimeLimit', wintypes.LARGE_INTEGER),
                    ('PerJobUserTimeLimit', wintypes.LARGE_INTEGER),
                    ('LimitFlags', wintypes.DWORD),
                    ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t),
                    ('ActiveProcessLimit', wintypes.DWORD),
                    ('Affinity', ctypes.c_size_t),
                    ('PriorityClass', wintypes.DWORD),
                    ('SchedulingClass', wintypes.DWORD),
                ]
            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ('ReadOperationCount', wintypes.ULARGE_INTEGER),
                    ('WriteOperationCount', wintypes.ULARGE_INTEGER),
                    ('OtherOperationCount', wintypes.ULARGE_INTEGER),
                    ('ReadTransferCount', wintypes.ULARGE_INTEGER),
                    ('WriteTransferCount', wintypes.ULARGE_INTEGER),
                    ('OtherTransferCount', wintypes.ULARGE_INTEGER),
                ]
            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ('BasicLimitInformation', JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ('IoInfo', IO_COUNTERS),
                    ('ProcessMemoryLimit', ctypes.c_size_t),
                    ('JobMemoryLimit', ctypes.c_size_t),
                    ('PeakProcessMemoryLimit', ctypes.c_size_t),
                    ('PeakJobMemoryLimit', ctypes.c_size_t),
                ]
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
            JobObjectExtendedLimitInformation = 9
            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            kernel32.SetInformationJobObject(
                _job_handle,
                JobObjectExtendedLimitInformation,
                ctypes.byref(info),
                ctypes.sizeof(info)
            )
    except Exception:
        _job_handle = None

def _assign_process_to_job(proc):
    if _job_handle and proc and sys.platform == 'win32':
        try:
            kernel32.AssignProcessToJobObject(_job_handle, wintypes.HANDLE(proc._handle))
        except Exception:
            pass

try:
    import numpy as _np
except Exception:
    _np = None

try:
    import websocket as websocket_client
except ImportError:
    websocket_client = None

from auth_utils import HardwareID, OnlineAuth, InputValidator, LoginDialog, apply_stealth_mode, hide_from_screen_capture, remove_from_taskbar
from widgets import InlineSelect, DragReorderList, CircleSlider, DualThumbRangeSlider, HoverTip
from mouse_motion import trajectory, travel_seconds
from automove import AutoMoveSimulator, sigmadrift_generate_plan, plan_screen_points, _INPUT, _MOUSEINPUT, _POINT, _RECT, _BITMAPINFOHEADER, _to_array, _gray_block

# Win32 SendInput MOUSEEVENTF flags used by _os_mouse_move / _os_mouse_button
_MOUSEEVENTF_MOVE        = 0x0001
_MOUSEEVENTF_ABSOLUTE    = 0x8000
_MOUSEEVENTF_VIRTUALDESK = 0x4000
from engine_pool import EnginePool, SocketEngineProcess, PositionCache, PatternLibrary, CECPEngineAdapter
from humanizer import Humanizer
from book_manager import BookManager, PolyglotBook
from coach_mode import CoachMode
from eval_window import ExternalEvalWindow
from pgn_analyzer import PGNAnalyzerWindow, _AnalyzerEngine
from control_panel import ControlPanel

# ── Win32 globals ──────────────────────────────────────────────────────
user32 = kernel32 = None
if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32")
    kernel32 = ctypes.WinDLL("kernel32")
    # Establish one coordinate space before the first Tk window is created.
    # If the host already set DPI awareness Windows leaves that choice intact.
    try:
        user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass
    if hasattr(user32, "GetAncestor"):
        user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
        user32.GetAncestor.restype = wintypes.HWND

if ctypes.sizeof(ctypes.c_void_p) == 8:
    LONG_PTR = ctypes.c_int64
else:
    LONG_PTR = ctypes.c_long

WS_EX_LAYERED      = 0x00080000
WS_EX_TRANSPARENT  = 0x00000020
WS_EX_TOPMOST      = 0x00000008
GWL_EXSTYLE        = -20
LWA_ALPHA          = 0x00000002
TRANSPARENT_KEY    = "#FF00FF"
WDA_EXCLUDEFROMCAPTURE = 0x00000011
WDA_MONITOR        = 0x00000001
WS_EX_TOOLWINDOW   = 0x00000080
WS_EX_APPWINDOW    = 0x00040000
WS_EX_NOACTIVATE   = 0x08000000
GA_ROOT            = 2
SW_HIDE            = 0
SW_SHOW            = 5

def _valid_fen_placement(board_str):
    if not isinstance(board_str, str) or not board_str:
        return False
    ranks = board_str.split("/")
    if len(ranks) != 8:
        return False
    pieces = set("rnbqkpRNBQKP")
    for rank in ranks:
        files = 0
        for char in rank:
            if char in pieces:
                files += 1
            elif char in "12345678":
                files += int(char)
            else:
                return False
        if files != 8:
            return False
    return True

def interpolate_path(x0, y0, x1, y1, steps=22, path_type='random'):
    path = []
    # Pre-compute curve control point ONCE outside the loop (was recomputed each step)
    _cx = (x0 + x1) / 2 + random.uniform(-40, 40)
    _cy = (y0 + y1) / 2 + random.uniform(-40, 40)
    for i in range(steps + 1):
        t = i / steps
        xt = x0 + (x1 - x0) * t
        yt = y0 + (y1 - y0) * t
        if path_type == "curve":
            xt = (1-t)**2*x0 + 2*(1-t)*t*_cx + t**2*x1
            yt = (1-t)**2*y0 + 2*(1-t)*t*_cy + t**2*y1
        elif path_type == "wave":
            dx, dy = x1-x0, y1-y0
            mag = math.hypot(dx, dy)
            if mag > 0:
                px, py = -dy/mag, dx/mag
            else:
                px, py = 0, 0
            amplitude = random.uniform(12, 32)
            wave = math.sin(t * math.pi) * amplitude
            xt += px * wave
            yt += py * wave
        elif path_type == "random":
            xt += random.uniform(-3, 3)
            yt += random.uniform(-3, 3)
        path.append((int(xt), int(yt)))
    return path

class ChessOverlay:
    def __init__(self, root):
        self.root = root
        self.depth_timer = DepthTimer()
        self._timing_game_key = None
        self._timing_epoch = None
        self._settings_writer = SettingsWriter(self._settings_path())
        def _on_process_exit():
            for eng in getattr(self, "engines", []):
                p = eng.get("process")
                if p and p.poll() is None:
                    try:
                        p.kill()
                    except Exception:
                        pass
        atexit.register(_on_process_exit)
        # Initialize committed display structures and scores first before canvas setup
        self.committed_pv_line = []
        self.committed_multi_pvs = {}
        self.pv_line = []
        self.multi_pvs = {}
        self.best_move = None
        self.current_score = 0.0
        self.current_score_fen = None  # FEN associated with current_score
        self.current_is_mate = False
        self.current_mate_label = None
        self.previous_score = None
        self._style_fallback = {"aggressive": False, "defensive": False}
        # Chess Coach chat panel state
        self.coach_visible = True
        self._coach_last_sig = None
        self._coach_after_id = None
        self._last_coach_best_white = None
        # Optional Groq-powered AI coach (falls back to local rules).
        self._load_coach_settings()

        # ---- Visual appearance settings (Visuals tab) ----
        self.eval_bar_visible = True     # show the left eval bar
        self.grid_visible = True         # show board grid lines
        self.depth_display_visible = False
        self._depth_display_text = "Arrow depth: —"
        self._arrow_depth_snapshot = None
        self.arrow_scale = 1.0           # arrow size multiplier (0.5..2.0)
        self.arrow_opacity = 1.0         # arrow layer alpha (0.25..1.0)
        self.arrow_dash = False          # dashed arrow shafts
        self.arrow_animate = False       # pulsing arrow animation
        self._arrow_pulse_after = None   # pending after() id for the pulse loop
        self._arrow_pulse_state = 0
        self._arrow_pulse_widths = []    # (item_id, base_width) for the pulse loop
        # New setting: stable depth for arrows (default 4). This can be overridden via settings.json.
        self.arrow_stable_depth = getattr(self, "arrow_stable_depth", 4)

        self.perspective = "white"
        self.display_mode = "pv"
        self.engine_paths = []
        # Hosted (no-download) engines are reached through this WebSocket base
        # URL.  Engine ids are appended: <base>/<id> (e.g. maia-1500).
        self.hosted_engine_base = "wss://ProtonnDev-engine.hf.space"
        self.engines = []
        self.current_player_color = "w"  # side to move in the current FEN
        self.player_color = "w"          # user's side; controlled by W/B perspective
        self.connected_clients = 0
        self.engine_elo = 2000
        self.analysis_depth = 18
        self.analysis_time_ms = 0
        self.analysis_nodes = 0
        self.only_own_turn = False
        self._analysis_request = None
        self._bridge_epoch = None
        self.current_depth = 0
        # When enabled, recommendations are selected from analysed MultiPV lines
        # according to the desired approximate move-quality percentage.
        self.recommendation_accuracy_enabled = True
        self.recommendation_strength_first = False
        self.target_recommendation_accuracy_min = 70
        self.target_recommendation_accuracy_max = 80
        # Sweet-spot the user wants suggestions to hover around inside the
        # [min, max] range (kept clamped to the range as it changes).
        self.target_recommendation_accuracy_priority = 75
        self.target_recommendation_accuracy = 75
        self.accuracy_candidate_limit = 8
        self.anti_draw_enabled = True
        self.anti_draw_threshold = 0.12
        self.anti_draw_min_advantage = 0.35
        self.game_context = clean_context({})
        self.context_auto = True
        self.manual_player_rating = 0
        self.manual_opponent_rating = 0
        self.recommendation_accuracy_status = ''
        self.recommendation_accuracy_estimate = None
        self.opponent_elo = 1600  # Opponent's ELO for guess
        self.show_opponent_move = True  # Whether to show opponent's guessed move
        self.opponent_move = None  # The guessed opponent's move
        self.multi_pv_count = 4  # best move plus three alternatives by default
        self.gui_update_delay = 100
        # ---- Engine resource defaults from hardware detection ----
        hw = self._detect_system_hardware()
        self.hardware_profile = hw
        self.engine_threads = hw["threads_default"]
        self.engine_threads_max = hw["threads_max"]
        self.engine_hash = hw["hash_default_mb"]
        self.engine_hash_max = hw["hash_max_mb"]
        # Optional extra UCI options (sent during init + restart only, if set)
        self.engine_syzygy_path = ""    # user can set after launch
        self.engine_uci_elo = 0         # 0 = disabled; engines with UCI_Elo option use it
        self.engine_contempt = 24       # Stockfish contempt (cp); most engines ignore gracefully
        # Compute mode: "cpu" | "gpu" | "hybrid"
        self.compute_mode = "cpu"
        # GPU engine options (LCZero / other GPU-accelerated UCI engines)
        self.gpu_engine_options = {
            "Backend":     "cudnn",
            "WeightsFile": "C:/Users/fa/Downloads/EngineWS/engines/lc0/791556.pb.gz",
            "Threads":     1,
        }
        # Restore the engine/resource settings the user saved last session.
        self._load_engine_settings()

        self._screen_bottom_color = None     # "w"/"b" when confident
        self._screen_bottom_at = 0.0
        self._screen_bottom_conf = 0.0
        self._screen_meta = {}
        # board.  _screen_perspective_locked resets when a new game is seen.
        self._screen_perspective_locked = False
        self._match_perspective_flip_count = 0 
        self.current_fen = None
        self.analysis_auto_mode = False
        self.autoplay_enabled = False  # retained only for compatibility; it never controls the mouse
        self.ui_queue = queue.Queue()
        self.autoplay_lock = threading.Lock()
        self.last_played_pv = []
        self.final_pv = []
        self.pv_ready_to_play = False
        self.engines_ready = {}
        self.move_already_played = False
        
        # ADDED: Hotkey lock state
        self.hotkeys_locked = False
        
        # ADDED: Engine rotation state
        self.engine_rotation_enabled = False
        self.primary_engine_index = 0
        self.rotation_anchor_index = 0
        self.rotation_engine_count = 1
        self._rotation_cursor = 0
        self.last_analyzed_fen = None

        # Engine pool with priority-based ranking (BetterMint feature)
        self.pool = EnginePool()
        self.ranking_mode = EnginePool.RANKING_PRIORITY

        # Phase 2: Humanizer
        self.humanizer = Humanizer()

        # Phase 2b: AutoMove — human-like mouse-motion simulator.  When
        # enabled it plays our moves on the site by replaying a real
        # mousedown -> mousemove... -> mouseup chain instead of a single
        # instant drag command.
        self.automove = AutoMoveSimulator()
        self._automove_tick_id = None
        self._automove_busy = False
        self._automove_sent_fen = None
        self._automove_sent_at = 0.0
        self._automove_epoch = 0
        self._automove_last_reason = "disabled"
        self._automove_retry_at = 0.0
        self._board_geometry = None
        self._canvas_board_geometry_cache = None   # Tk-thread owned
        self._board_calibration = None   # manual {left, top, size, flipped} px
        self._automove_debug_geometry = False
        # Game-state gates reported by the extension (game over / waiting queue)
        # plus the board-orientation flip guard.
        self._automove_game_over = False
        self._automove_in_queue = False
        self._automove_last_flip_at = 0.0
        self._automove_flip_pending_until = 0.0
        self._automove_flip_verify_at = 0.0
        # Anti-loop guards for the auto-flip: counts OS flip-key presses that
        # did NOT change the observed bottom (a stale orientation source would
        # otherwise flip forever) and a give-up deadline.
        self._automove_flip_streak = 0
        self._automove_flip_episode_bottom = None
        self._automove_flip_giveup_until = 0.0
        # Auto-queue: after a finished game, click Play again / queue the next
        # game after a short delay (off by default; the extension does the click).
        self._auto_queue_enabled = False
        self._auto_queue_delay_s = 3.0
        self._auto_queue_pending = False
        self._auto_queue_fire_at = 0.0
        self._auto_queue_attempts = 0
        self._auto_queue_started_at = 0.0
        # Visual bottom-colour detector (screen capture of the board region the
        # visualizer sits over).  This is the auto side-picker's ground truth:
        # it sees the REAL board, so it works even when the extension's
        # flipped/playingAs signals are stale or never arrive.
        self._screen_lock = threading.Lock()
        self._screen_detector_started = False
        self._screen_detector_stop = threading.Event()
        self._screen_detector_debug = False
        
        # Live preview of what the detector sees (captured frame + per-square
        # classification), updated on the Tk thread when the window is open.
        self._screen_preview_frame = None   # numpy/bytes of last capture
        self._screen_preview_ppm = None
        self._screen_preview_geom = None
        self._screen_preview_grid = None    # 8x8 of "w"/"b"/None
        self._screen_preview_win = None
        self._screen_preview_after_id = None
        self.book_manager = BookManager()

        # Phase 4: Coach Mode
        self.coach_mode = CoachMode()

        # Phase 5: Extension internals state
        self.extension_privacy_hidden = False
        self.extension_panicked = False
        self.extension_clocks = {"self_ms": None, "opp_ms": None}
        self.extension_variant = None
        self.extension_exploits = []

        # Cloud-book fallback state (fetched on a background thread so the
        # Tk loop never blocks on the network; see _maybe_fetch_cloud_lines).
        self._cloud_lines_result = None
        self._cloud_lines_fen = None
        self._cloud_lines_inflight = False
        self._cloud_lines_fail_until = 0.0
        
        # ADDED: Accuracy Display Toggle
        self.accuracy_display_enabled = True  # Display accuracy on visualizer by default

        # Aggressive mode blunder prevention (ON by default so aggressive mode
        # never suggests moves that lose material vs the engine's best line)
        self.aggressive_blunder_filter_enabled = True
        self.aggressive_blunder_threshold = 0.50   # pawns; moves worse than this vs best are rejected
        self.aggressive_blunders_avoided = 0

        
        # ADDED: Stealth mode (streamproofing)
        self.stealth_mode_enabled = False  # Can toggle with F5
        # ADDED: Control panel minimized state
        self.control_panel_minimized = False

        # Restore every autosaved control-panel setting (engine, coach,
        # visuals, humanizer, book, accuracy targets, rotation, privacy).
        # Runs LAST so it can only ever override the defaults above, never be
        # clobbered by a later default assignment.
        self._settings_ext_visible = False
        self._load_all_settings()

        # The command-prompt window is the application's logging terminal.  It is
        # present only when launched with python.exe on Windows.
        self.logging_terminal_available = self._get_logging_terminal_handle() is not None
        self.logging_terminal_visible = self._is_logging_terminal_visible()
        self._restore_logging_terminal_after_f6 = False
        
        # ADDED: Game Accuracy Analysis - Per-side tracking
        self.move_history = []
        self.previous_fen = None
        self._last_real_fen = None   # Last FEN confirmed via _fen_transition_is_real_move
        self.previous_score = None  # Score before move
        self.previous_best_move = None  # Best move before move
        self.previous_analysis_fen = None  # FEN that produced the saved score
        self._pending_move = None  # Pending move tracking dict
        # Consecutive completed positions where the HUMAN is losing (eval below
        # LOSING_STREAK_EVAL_THRESHOLD).  Drives the transient depth boost.
        self._losing_streak = 0
        # Consecutive completed positions where the HUMAN is clearly winning
        # (>= LOSING_STREAK_RECOVERY_EVAL).  Only after LOSING_STREAK_RECOVERY_MOVES
        # such positions does the losing-streak depth boost wind back down.
        self._depth_recovery_count = 0
        
        # Per-side accuracy counters
        self.white_stats = {"perfect": 0, "great": 0, "good": 0, "inaccuracy": 0, "mistake": 0, "blunder": 0, "total": 0}
        self.black_stats = {"perfect": 0, "great": 0, "good": 0, "inaccuracy": 0, "mistake": 0, "blunder": 0, "total": 0}
        self.overall_accuracy = 100.0
        self._adaptive_history = {"w": [], "b": []}
        self._line_memory = LineMemory()
        
        self.move_start_time = None
        self.comparison_player_elo = 2000
        
        # Skill level profiles (elo -> avg_time_ms, accuracy_at_depth)
        self.skill_profiles = {
            300: {"name": "Beginner", "avg_time": 2000, "accuracy": 0.35},
            500: {"name": "Casual", "avg_time": 3000, "accuracy": 0.45},
            1200: {"name": "Amateur", "avg_time": 4000, "accuracy": 0.60},
            1400: {"name": "Human", "avg_time": 4500, "accuracy": 0.68},
            1600: {"name": "Club Player", "avg_time": 5000, "accuracy": 0.72},
            2000: {"name": "Expert", "avg_time": 6000, "accuracy": 0.85},
            2400: {"name": "Grandmaster", "avg_time": 7000, "accuracy": 0.95},
        }

        # OPTIMIZATION: Cache for coordinate calculations
        self._coord_cache = {}
        
        # OPTIMIZATION: Batch GUI updates
        self._pending_gui_updates = False
        self._gui_update_delay = self.gui_update_delay  # ms

        # Performance: position LRU cache
        self._position_cache = PositionCache(max_size=256, ttl_seconds=300)

        # Dynamic depth by position complexity
        self.dynamic_depth_enabled = False
        self.dynamic_depth_max_bonus = 4      # max extra plies for complex positions
        self.dynamic_depth_max_reduction = 2  # max plies reduced for simple positions

        self._refresh_pending = False
        self._refresh_lock = threading.Lock()
        self.should_run = True
        self._analysis_debounce_id  = None
        self._analysis_debounce_fen = None
        self._start_screen_bottom_detector()
        self.start_websocket_server()
        self.root.after(1 if not self.ui_queue.empty() else self.gui_update_delay, self.poll_ui_queue)
        self._automove_tick_id = self.root.after(700, self._automove_tick)
        self.root.protocol("WM_DELETE_WINDOW", self.shutdown)

        # Polyglot Opening Book
        self.opening_book = None
        self.opening_book_enabled = False

        self.setup_window()
        # Keep the visualizer visible and accountable; no concealment is applied.
        self.setup_canvas()

        # External evaluation window (built lazily from the Visuals tab)
        self.external_eval = None

        # Create detached control panel
        self.control_panel = ControlPanel(self)
        self._sync_config_ui()

        # Restore the control panel's saved size/position (e.g. after a
        # monitor change, a stale geometry may be off-screen — clamp it).
        saved_panel = getattr(self, "_saved_panel_geometry", None)
        if saved_panel:
            try:
                self.control_panel.root.geometry(
                    self._clamp_geometry(saved_panel))
            except Exception:
                pass

        self.setup_global_hotkeys()

        # Start periodic GC + fast engine watchdog
        self._gc_timer = self.root.after(30000, self._periodic_gc)
        self._watchdog_timer = self.root.after(8000, self._engine_watchdog)

        # Periodic settings autosave sweep (catches every direct attribute
        # mutation, e.g. the lambda-wired book/coach-mode toggles).
        self._autosave_after = None
        self._start_settings_autosave()

        # Restore the floating external evaluation window if it was visible.
        if getattr(self, "_settings_ext_visible", False):
            self.root.after(600, lambda: self.toggle_external_eval())

        # Apply the persisted docked-coach visibility once the panel exists.
        if not getattr(self, "coach_visible", True):
            self.root.after(600, lambda: self.set_coach_visible(False))

    def load_opening_book(self):
        """Open file dialog to load Polyglot .bin opening book."""
        path = filedialog.askopenfilename(title="Select Polyglot Opening Book", filetypes=[("Polyglot Book", "*.bin"), ("All Files", "*.*")])
        if path:
            if self.opening_book:
                self.opening_book.close()
            self.opening_book = PolyglotBook(path)
            self.opening_book_enabled = True
            print(f"[Opening Book Loaded]: {os.path.basename(path)}")
            if hasattr(self, "control_panel"):
                self.control_panel.update_book_status(os.path.basename(path))




    def detect_move_from_fen(self, old_fen, new_fen):
        """Extract move from FEN comparison. Returns UCI string or None.

        Turn-independent: the mover is identified by the colour of the pieces
        that APPEARED on the new board, not by the FEN turn field.  The
        extension's turn field can drift (it is derived from board diffs and
        flips wrongly after a cancelled drag), and its fullmove counter is
        always "0 1", so relying on those fields would reject valid moves.
        """
        try:
            old_board = self._parse_fen_board(old_fen.split()[0])
            new_board = self._parse_fen_board(new_fen.split()[0])

            removed = {}  # square -> old piece (gone or changed)
            added = {}    # square -> new piece (appeared or changed)
            for sq in range(64):
                old_p = old_board.get(sq)
                new_p = new_board.get(sq)
                if old_p != new_p:
                    if old_p:
                        removed[sq] = old_p
                    if new_p:
                        added[sq] = new_p

            mover_colors = {p.isupper() for p in added.values()}
            if len(mover_colors) != 1:
                return None
            is_white = next(iter(mover_colors))
            mover_piece = lambda p: p.isupper() == is_white

            sources = [sq for sq, p in removed.items() if mover_piece(p)]
            dests = list(added.keys())

            # Simple move (1 source, 1 dest) or promotion
            if len(sources) == 1 and len(dests) == 1:
                src, dst = sources[0], dests[0]
                promotion = ''
                if new_board.get(dst).lower() != old_board.get(src).lower():
                    promotion = new_board.get(dst).lower()
                return self._squares_to_uci(src, dst, promotion)

            # Castling (2 sources, 2 dests, king among the sources)
            if len(sources) == 2 and len(dests) == 2:
                if any(old_board.get(src).lower() == 'k' for src in sources):
                    king_src = [s for s in sources if old_board.get(s).lower() == 'k'][0]
                    king_dst = [d for d in dests if new_board.get(d).lower() == 'k'][0]
                    return self._squares_to_uci(king_src, king_dst)

            return None
        except Exception:
            return None
    
    def _parse_fen_board(self, board_str):
        """Parse FEN board string into square->piece dict.

        Returns an empty dict for malformed placements so callers' existing
        ``if not board`` guards reject the position instead of indexing a
        truncated board.
        """
        if not board_str:
            return {}
        # Cheap structural validation: exactly 8 ranks separated by '/', each
        # rank summing to 8 squares, digits 1-8 only, valid piece letters.
        if not _valid_fen_placement(board_str):
            return {}
        board = {}
        sq = 0
        for char in board_str:
            if char == '/':
                continue
            elif char.isdigit():
                sq += int(char)
            else:
                board[sq] = char
                sq += 1
        return board

    def _squares_to_uci(self, src, dst, promotion=''):
        """Convert square numbers (0-63) to UCI notation"""
        files = 'abcdefgh'
        ranks = '12345678'
        src_uci = files[src % 8] + ranks[7 - (src // 8)]
        dst_uci = files[dst % 8] + ranks[7 - (dst // 8)]
        return f"{src_uci}{dst_uci}{promotion}"

    def track_move(self, played_move, is_best_move, cp_loss, whose_move,
                   pre_score=None, post_score=None, better_move=None, opening=False):
        """Track a move with centipawn loss and categorize quality.

        Immediately appends a colourised log line to the accuracy text box
        and updates the overall running accuracy summary header.
        """
        # Determine category based on centipawn loss
        if cp_loss <= 5:
            category = "perfect"
        elif cp_loss <= 25:
            category = "great"
        elif cp_loss <= 75:
            category = "good"
        elif cp_loss <= 150:
            category = "inaccuracy"
        elif cp_loss <= 300:
            category = "mistake"
        else:
            category = "blunder"

        # Update appropriate side's stats
        stats = self.white_stats if whose_move == 'w' else self.black_stats
        stats[category] += 1
        stats["total"] += 1
        # Keep a continuous move-accuracy value; categories remain presentation.
        # Use the real pre/post evals when available so the win-probability loss
        # is measured against the actual best move, not a neutral 50/50 baseline
        # (the old best_cp=0.0 inflated every grade and made "You" read ~100%).
        if pre_score is not None and post_score is not None:
            pre_cp = float(pre_score) * 100.0
            post_cp = float(post_score) * 100.0
            if whose_move == 'w':
                move_accuracy = self._accuracy_from_loss(pre_cp, post_cp)
            else:
                move_accuracy = self._accuracy_from_loss(post_cp, pre_cp)
        else:
            move_accuracy = self._accuracy_from_loss(0.0, -float(cp_loss))
        if not opening:
            history = getattr(self, '_adaptive_history', {"w": [], "b": []})
            history[whose_move] = (history.get(whose_move, []) + [100.0 if is_best_move else move_accuracy])[-10:]
            self._adaptive_history = history
        stats["accuracy_sum"] = stats.get("accuracy_sum", 0.0) + move_accuracy
        print(f"[TRACK] side={whose_move} move={played_move} cp_loss={cp_loss} "
              f"acc={move_accuracy:.2f} cat={category} total={stats['total']}")

        # Calculate overall accuracy
        total_moves = self.white_stats["total"] + self.black_stats["total"]
        if total_moves > 0:
            score = sum(side_stats.get("accuracy_sum", 0.0)
                        for side_stats in [self.white_stats, self.black_stats])
            self.overall_accuracy = score / total_moves

        # Record move in history
        move_number = len(self.move_history) // 2 + 1  # chess-style move numbering
        self.move_history.append({
            "move": played_move,
            "category": category,
            "cp_loss": cp_loss,
            "is_best": is_best_move,
            "side": whose_move,
            "move_number": move_number,
        })

        # Append a one-line log entry for this move to the accuracy text widget
        # (do not wipe previous entries).  Routed through the UI queue because
        # this method runs on the engine reader thread and Tk widgets are only
        # safe to touch on the main thread — direct calls here could corrupt the
        # GUI after a while and appear as broken/frozen suggestions.
        self.ui_queue.put(("accuracy_line", {
            "move": played_move, "category": category, "cp_loss": cp_loss,
            "is_best": is_best_move, "side": whose_move, "move_number": move_number,
            "better_move": better_move if not is_best_move else None,
        }))

        self.trigger_gui_update()

    def _append_accuracy_move_line(self, played_move, category, cp_loss,
                                   is_best_move, whose_move, move_number,
                                   better_move=None):
        """Append a single per-move log line to the accuracy text box."""
        try:
            if not hasattr(self.control_panel, 'accuracy_text'):
                return
            cat_colors = {
                "perfect":    "#10B981",
                "great":      "#22C55E",
                "good":       "#A3E635",
                "inaccuracy": "#F59E0B",
                "mistake":    "#F97316",
                "blunder":    "#EF4444",
            }
            side_label = "W" if whose_move == 'w' else "B"
            star = " ★" if is_best_move else ""
            hint = ""
            if (better_move and not is_best_move
                    and getattr(getattr(self, "coach_mode", None), "suggest_better", True)):
                hint = f"  → Better: {better_move}"
            line = (f"{move_number:>3}. {side_label} {played_move:<7} "
                    f"{category.upper():<11} "
                    f"CPL {cp_loss:>6.1f}{star}{hint}\n")

            tw = self.control_panel.accuracy_text
            tw.config(state=tk.NORMAL)
            # Insertion point = after the frozen summary header + one blank line
            marker = getattr(tw, "_summary_end_mark", "1.0")
            tw.insert(tk.END, line, category)
            try:
                tw.tag_config(category, foreground=cat_colors.get(category, "#CCCCCC"))
            except Exception:
                pass
            tw.see(tk.END)
            tw.config(state=tk.DISABLED)
        except Exception:
            pass


    
    def get_accuracy_display(self):
        """Return separate, complete results for both players."""
        total = self.white_stats["total"] + self.black_stats["total"]
        if total == 0: return "No moves analyzed yet"
        def line(label, st):
            return (f"{label} Accuracy: {self._side_accuracy(st):.1f}%  ({st['total']} moves)\n"
                    f"Best: {st['perfect']} | Excellent: {st['great']} | Okay: {st['good']}\n"
                    f"Inaccuracy: {st['inaccuracy']} | Mistake: {st['mistake']} | Blunder: {st['blunder']}")
        return f"Overall Accuracy: {self.overall_accuracy:.1f}%\n\n{line('WHITE', self.white_stats)}\n\n{line('BLACK / OPPONENT', self.black_stats)}"

    def _side_accuracy(self, stats):
        if not stats['total']:
            return 100.0
        return stats.get("accuracy_sum", 0.0) / stats['total']

    def _rewrite_accuracy_summary_header(self):
        """Rewrite ONLY the frozen summary header at the top of the accuracy log.

        All per-move entries below the `_summary_sep` line are preserved.
        """
        try:
            if not hasattr(self.control_panel, 'accuracy_text'):
                return
            tw = self.control_panel.accuracy_text
            tw.config(state=tk.NORMAL)

            if not getattr(getattr(self, "coach_mode", None), "show_running_accuracy", True):
                stats_text = ("Live accuracy hidden. Enable ‘Show live accuracy %’ "
                              "in Coach Mode & Accuracy to display it.")
            elif not self.accuracy_display_enabled:
                stats_text = ("Accuracy analysis hidden. Enable ‘Show Accuracy in GUI Log’ "
                              "to display it.")
            else:
                stats_text = self.get_accuracy_display()
            header = f"{stats_text}\n"
            sep = "-" * 52 + "\n"

            # Delete the old header block (everything up to the stored mark).
            # Using the saved mark avoids a full-text regex scan on every refresh,
            # which would slow the GUI down the longer a game runs.
            mark = getattr(tw, "_summary_end_mark", None)
            if mark:
                tw.delete("1.0", mark)
            else:
                tw.delete("1.0", tk.END)

            tw.insert("1.0", header + sep)
            tw._summary_sep_exists = True
            tw._summary_end_mark = f"1.0 +{len(header) + len(sep)} chars"
            tw.config(state=tk.DISABLED)
        except Exception:
            pass

    def update_stats_display(self):
        """Public entry: rewrite the accuracy summary header, keep per-move log lines."""
        self._rewrite_accuracy_summary_header()

    def _speak_move_grade(self, category):
        """Speak a graded move aloud on a background thread (never blocks the
        Tk loop, and pyttsx3 failures are silently ignored)."""
        try:
            labels = {
                "perfect": "Perfect move",
                "great": "Great move",
                "good": "Good move",
                "inaccuracy": "Inaccuracy",
                "mistake": "Mistake",
                "blunder": "Blunder",
            }
            report = {"label": labels.get(category, category.title()), "tip": ""}
            threading.Thread(target=self._tts_worker, args=(report,), daemon=True).start()
        except Exception:
            pass

    @staticmethod
    def _tts_worker(report):
        """Each call creates a fresh pyttsx3 engine to avoid threading conflicts.
        pyttsx3 is not thread-safe and its runLoop cannot be shared across calls."""
        try:
            import pyttsx3
            tts = pyttsx3.init()
            tts.say(report["label"])
            tts.runAndWait()
            tts.stop()
        except Exception:
            pass

    def setup_window(self):
        self.root.title("Chess Engine Visualizer")
        self.root.geometry("650x650")  # Increased size for border
        self.root.minsize(320, 320)
        self.root.resizable(True, True)
        # Keep the unused canvas area transparent with a dedicated key colour.
        # A window-wide alpha value blends/rasterizes text on some Windows builds,
        # so visible elements remain fully opaque for crisp labels.
        self.root.attributes("-transparentcolor", TRANSPARENT_KEY)
        self.root.attributes("-topmost", True)
        self.root.attributes("-toolwindow", True)  # REMOVES VISUALIZER FROM TASKBAR COMPLETELY
        self.root.configure(bg=TRANSPARENT_KEY)

        try:
            hwnd = user32.GetParent(self.root.winfo_id())
            if hwnd == 0:
                hwnd = self.root.winfo_id()
            exstyle = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) | WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW
            exstyle &= ~WS_EX_APPWINDOW
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, exstyle)
        except Exception as e:
            print(f"Window setup error: {e}")

    def _get_logging_terminal_handle(self):
        """Return this process's Windows console handle, if it has one."""
        if sys.platform != "win32" or kernel32 is None:
            return None
        try:
            kernel32.GetConsoleWindow.restype = wintypes.HWND
            handle = kernel32.GetConsoleWindow()
            return handle or None
        except Exception:
            return None

    def _is_logging_terminal_visible(self):
        handle = self._get_logging_terminal_handle()
        if handle is None or user32 is None:
            return False
        try:
            return bool(user32.IsWindowVisible(handle))
        except Exception:
            return False

    def set_logging_terminal_visible(self, visible):
        """Show or hide the command-prompt logging terminal on Windows."""
        handle = self._get_logging_terminal_handle()
        self.logging_terminal_available = handle is not None
        if handle is None or user32 is None:
            self.logging_terminal_visible = False
        else:
            try:
                user32.ShowWindow(handle, SW_SHOW if visible else SW_HIDE)
                # Keep the terminal out of the taskbar whenever Streamproof is on.
                remove_from_taskbar(handle, self.stealth_mode_enabled)
                # ShowWindow is asynchronous in some window-manager states; read
                # the actual state rather than trusting the requested one.
                self.logging_terminal_visible = self._is_logging_terminal_visible()
            except Exception:
                self.logging_terminal_visible = self._is_logging_terminal_visible()
        if hasattr(self, "control_panel"):
            self.control_panel.update_terminal_toggle(self.logging_terminal_visible,
                                                      self.logging_terminal_available)

    def toggle_logging_terminal(self):
        """Toggle the command-prompt logging terminal from the GUI."""
        if self.hotkeys_locked:
            return
        self.set_logging_terminal_visible(not self._is_logging_terminal_visible())

    def setup_global_hotkeys(self):
        keyboard.add_hotkey('f1', lambda: self.ui_queue.put(("toggle_auto",)))
        keyboard.add_hotkey('ctrl+alt+l', lambda: self.ui_queue.put(("toggle_lock",)))
        keyboard.add_hotkey('f5', lambda: self.ui_queue.put(("toggle_stealth",)))
        keyboard.add_hotkey('f8', lambda: self.ui_queue.put(("toggle_control_panel",)))
        # '[' = play White, ']' = play Black (routes through the
        # "perspective" action so the hotkeys-lock still applies).
        keyboard.add_hotkey('[', lambda: self.ui_queue.put(("perspective", "white")))
        keyboard.add_hotkey(']', lambda: self.ui_queue.put(("perspective", "black")))

    def toggle_hotkeys_lock(self):
        self.hotkeys_locked = not self.hotkeys_locked
        self.control_panel.update_lock_status(self.hotkeys_locked)
    



    def _rotation_pool(self):
        """Return live indices for configured rotation choices from the primary."""
        count = len(self.engine_paths)
        if not count or not self.engines:
            return []
        start = max(0, min(self.rotation_anchor_index, count - 1))
        size = min(max(1, self.rotation_engine_count), count)
        by_path = {engine.get("path"): i for i, engine in enumerate(self.engines)}
        return [by_path[path] for offset in range(size)
                if (path := self.engine_paths[(start + offset) % count]) in by_path]


    # ── Visual appearance toggles (Visuals tab) ────────────────────────────
    def toggle_eval_bar_visibility(self, visible=None):
        """Show/hide the left-hand evaluation bar."""
        if visible is None:
            self.eval_bar_visible = not getattr(self, "eval_bar_visible", True)
        else:
            self.eval_bar_visible = bool(visible)
        try:
            if self.eval_bar_visible:
                self.eval_canvas.pack(side=tk.LEFT, fill=tk.Y, before=self.canvas)
            else:
                self.eval_canvas.pack_forget()
            self._coord_cache.clear()
            self._sync_arrow_layer()
            self.redraw_all_arrows()
            self.draw_evaluation_bar()
        except Exception:
            pass
        self._save_all_settings()

    def toggle_depth_display(self):
        """Toggle only the depth badge on the visualizer; leave arrows alone."""
        self.depth_display_visible = not getattr(self, "depth_display_visible", False)
        panel = getattr(self, "control_panel", None)
        button = getattr(panel, "depth_display_button", None)
        if button is not None:
            button.config(text="Depth display: " + ("ON" if self.depth_display_visible else "OFF"))
        self._draw_depth_display()
        self._save_all_settings()

    def _draw_depth_display(self):
        canvas = self.arrow_canvas
        canvas.delete("depth_display")
        if not getattr(self, "depth_display_visible", False):
            return
        item = canvas.create_text(14, 12, anchor="nw",
            text=getattr(self, "_depth_display_text", "Arrow depth: —"),
            fill="#F5F3FF", font=("Segoe UI", 10, "bold"), tags="depth_display")
        bounds = canvas.bbox(item)
        if bounds:
            x0, y0, x1, y1 = bounds
            box = canvas.create_rectangle(x0 - 6, y0 - 4, x1 + 6, y1 + 4,
                fill="#19152B", outline="#7C6AA6", tags="depth_display")
            canvas.tag_lower(box, item)
        canvas.tag_raise("depth_display")

    def toggle_grid_visibility(self, visible=None):
        """Show/hide the board grid lines."""
        if visible is None:
            self.grid_visible = not getattr(self, "grid_visible", True)
        else:
            self.grid_visible = bool(visible)
        self.draw_grid()
        self._save_all_settings()

    def set_arrow_scale(self, value):
        """Set the arrow size multiplier (0.5..2.0)."""
        try:
            value = float(value)
        except (TypeError, ValueError):
            return
        self.arrow_scale = max(0.5, min(2.0, value))
        self.redraw_all_arrows()
        self._save_all_settings()

    def set_arrow_opacity(self, value):
        """Set the arrow layer opacity (0.25..1.0)."""
        try:
            value = float(value)
        except (TypeError, ValueError):
            return
        self.arrow_opacity = max(0.25, min(1.0, value))
        if getattr(self, "_arrow_layer_ready", False):
            try:
                self.arrow_layer.attributes("-alpha", self.arrow_opacity)
            except Exception:
                pass
        self._save_all_settings()

    def set_arrow_dash(self, enabled):
        """Toggle dashed arrow shafts."""
        self.arrow_dash = bool(enabled)
        self.redraw_all_arrows()
        self._save_all_settings()

    def set_arrow_animate(self, enabled):
        """Toggle the pulsing arrow animation."""
        self.arrow_animate = bool(enabled)
        if not self.arrow_animate:
            self._stop_arrow_pulse()
        self.redraw_all_arrows()
        self._save_all_settings()

    def toggle_external_eval(self):
        """Show/hide the BetterMint-style floating evaluation window."""
        if getattr(self, "external_eval", None) is None:
            self.external_eval = ExternalEvalWindow(self)
            # Restore the saved size/position if one was persisted.
            saved_ew = getattr(self, "_saved_eval_geometry", None)
            if saved_ew:
                try:
                    self.external_eval.root.geometry(
                        self._clamp_geometry(saved_ew))
                except Exception:
                    pass
            self.external_eval.show()
        elif self.external_eval.is_visible():
            self.external_eval.hide()
        else:
            self.external_eval.show()
        self._save_all_settings()

    def open_pgn_analyzer(self):
        """Open (or re-show) the chess.com-style PGN game analyzer window."""
        win = getattr(self, "pgn_analyzer", None)
        alive = False
        if win is not None:
            try:
                alive = bool(win.root.winfo_exists())
            except Exception:
                alive = False
        if not alive:
            self.pgn_analyzer = PGNAnalyzerWindow(self)
        self.pgn_analyzer.show()

    def toggle_coach_visible(self):
        """Show/hide the docked coach panel (Visuals tab toggle)."""
        self._toggle_coach()

    def toggle_autoplay(self):
        """F1 toggles suggestion auto-mode and AutoMove mouse dragging."""
        if self.hotkeys_locked:
            return
        self.analysis_auto_mode = not self.analysis_auto_mode
        if hasattr(self, "automove") and self.automove:
            self.automove.enabled = self.analysis_auto_mode
        status = "ON" if self.analysis_auto_mode else "OFF"
        if hasattr(self, "control_panel"):
            if hasattr(self.control_panel, "autoplay_status_label"):
                self.control_panel.autoplay_status_label.config(
                    text=f"Auto: {status}", fg="#10B981" if self.analysis_auto_mode else "#8B7BAE")
            if hasattr(self.control_panel, "automove_enabled_var"):
                self.control_panel.automove_enabled_var.set(self.analysis_auto_mode)
        if self.analysis_auto_mode and self.current_fen:
            self.analyse_position(self.current_fen, self.engine_elo)
    
    def toggle_stealth_mode(self):
        """Toggle Streamproof mode (F5) for capture protection and taskbar hiding."""
        print("toggle_stealth_mode called!")
        if self.hotkeys_locked:
            return
        self.stealth_mode_enabled = not self.stealth_mode_enabled
        
        # The visualizer is already configured as a tool window; protect it
        # from capture while Streamproof is active.
        self.root.update_idletasks()
        hide_from_screen_capture(self.root.winfo_id(), self.stealth_mode_enabled)
        
        # Arrows have their own native window; visualizer affinity does not
        # extend to this separate top-level layer.
        self._apply_arrow_layer_capture_protection(force=True)

        # Streamproof applies to the control panel too.  Keep its taskbar
        # entry removed while enabled, including when it is currently hidden.
        self.control_panel.root.update_idletasks()
        hide_from_screen_capture(self.control_panel.root.winfo_id(), self.stealth_mode_enabled)
        remove_from_taskbar(self.control_panel.root.winfo_id(), self.stealth_mode_enabled)
        terminal_handle = self._get_logging_terminal_handle()
        if terminal_handle is not None:
            remove_from_taskbar(terminal_handle, self.stealth_mode_enabled)
        
        # Update button text
        btn_text = "🔒 Streamproof: ON" if self.stealth_mode_enabled else "🔓 Streamproof: OFF"
        btn_fg = "#10B981" if self.stealth_mode_enabled else "#F4EEFF"
        self.control_panel.stealth_mode_btn.config(text=btn_text, fg=btn_fg)
        
        # Update status bar label
        status_text = "Streamproof: ON" if self.stealth_mode_enabled else "Streamproof: OFF"
        status_fg = "#10B981" if self.stealth_mode_enabled else "#F4EEFF"
        self.control_panel.stealth_status_label.config(text=status_text, fg=status_fg)
        self._save_all_settings()
    
    def toggle_control_panel(self):
        """Toggle only the control panel with F8; logging remains independent.
        Intentionally bypasses hotkeys_locked so the panel can always be reopened."""
        print("toggle_control_panel called!")
        self.control_panel_minimized = not self.control_panel_minimized
        panel = self.control_panel.root
        if self.control_panel_minimized:
            # Dismiss any open dropdown menus before hiding the window.
            try:
                cp = self.control_panel
                for attr in ('elo_menu', 'opponent_elo_menu', 'primary_engine_select'):
                    m = getattr(cp, attr, None)
                    if m and hasattr(m, 'close'):
                        m.close()
            except Exception:
                pass
            if self.stealth_mode_enabled:
                hide_from_screen_capture(panel.winfo_id(), False)
            panel.withdraw()
            return

        panel.deiconify()
        panel.update_idletasks()
        # Reapply window state after a withdraw/deiconify lifecycle transition.
        remove_from_taskbar(panel.winfo_id(), self.stealth_mode_enabled)
        if self.stealth_mode_enabled:
            panel.after_idle(lambda: hide_from_screen_capture(panel.winfo_id(), True))


    def poll_ui_queue(self):
        refresh = False
        deadline = time.monotonic() + 0.008
        processed = 0
        while processed < 100 and time.monotonic() < deadline:
            processed += 1
            try:
                item = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                action = item[0]
                if action == "save_settings":
                    self._save_all_settings()
                elif action == "automove_geometry":
                    result, ready = item[1], item[2]
                    try:
                        result.append(self._canvas_board_geometry())
                    finally:
                        ready.set()
                elif action == "refresh":
                    with self._refresh_lock:
                        self._refresh_pending = False
                    refresh = True
                elif action == "coach":
                    # AI coach reply (posted from the Groq worker thread).
                    try:
                        self._coach_post(item[1], "coach")
                    except Exception:
                        pass
                elif action == "coach_rt":
                    # Rich local fallback narration (colour-tagged move names).
                    try:
                        self._coach_post_rich(item[1], "coach")
                    except Exception:
                        pass
                elif action == "analysis":
                    # Debounce: ignore mid-drag FENs; only fire after 200 ms quiet.
                    if len(item) > 6 and isinstance(item[6], dict):
                        request = item[6]
                        epoch = request.get('epoch')
                        if epoch != self._bridge_epoch:
                            self._bridge_epoch = epoch
                            self._adaptive_history = {"w": [], "b": []}
                            self._line_memory = LineMemory()
                            self._accuracy_selection_cache = None
                            self._validated_book_cache = None
                            self.game_context = clean_context({})
                            self._last_real_fen = None
                            self.previous_fen = None
                            self.last_analyzed_fen = None
                            self._pending_move = None
                            self._position_cache = PositionCache(max_size=256, ttl_seconds=300)
                        self._analysis_request = request
                        self._apply_game_context(request.get('game_context'), request.get('epoch'))
                        self.ws_broadcast(dict({k:v for k,v in request.items() if k!='game_context'}, type='analysis_status', stage='received', target_depth=self.analysis_depth))
                    new_fen = item[1]
                    new_elo = item[2]
                    new_flipped = item[3] if len(item) > 3 else 0
                    new_playing_as = item[4] if len(item) > 4 else None
                    resync = item[5] if len(item) > 5 else False
                    if self._analysis_debounce_id is not None:
                        try:
                            self.root.after_cancel(self._analysis_debounce_id)
                        except Exception:
                            pass
                        self._analysis_debounce_id = None
                    self._analysis_debounce_fen = new_fen
                    pending = getattr(self, "_pending_analysis_payload", None)
                    resync = resync or bool(pending and pending[4])
                    self._pending_analysis_payload = (new_fen, new_elo, new_flipped, new_playing_as, resync)
                    self._analysis_debounce_id = self.root.after(35, self._fire_pending_analysis)
                elif action == 'game_context':
                    payload=item[1]
                    request=getattr(self,'_analysis_request',None) or {}
                    if (payload.get('epoch') == self._bridge_epoch
                            and payload.get('fen') == request.get('fen')
                            and payload.get('request_id') == request.get('request_id')):
                        self._apply_game_context(payload.get('context'),payload.get('epoch'))
                elif action == "connection":
                    self.update_connection_indicator()
                elif action == 'automove_check':
                    self._automove_check()
                elif action == 'accuracy_expand':
                    fen, engine = item[1], item[2]
                    override = engine.get('_accuracy_pool_override')
                    if (fen == self.current_fen and any(e is engine for e in self.engines)
                            and override and override[0] == self._accuracy_pool_key(engine, fen)):
                        self.analyse_position(fen, self.engine_elo)
                elif action == "analyse":
                    # Direct analysis request (used by engine restarts). Skips the
                    # debounce so a freshly restarted engine resumes immediately.
                    try:
                        fen = item[1]
                        elo = item[2]
                        flipped = item[3] if len(item) > 3 else 0
                        playing_as = item[4] if len(item) > 4 \
                            else getattr(self, "current_playing_as", None)
                        if fen == self.current_fen:
                            # Restart resume: re-submit via analyse_position
                            # directly.  The placement guard in
                            # apply_analysis_request would swallow this
                            # same-FEN request, so the restarted engine would
                            # never receive a search and suggestions would stay
                            # frozen until a move or a board flip re-analysed.
                            self.analyse_position(fen, elo)
                        else:
                            self.apply_analysis_request(fen, elo, flipped, playing_as)
                    except Exception as exc:
                        print(f"Analyse event error: {exc}")
                elif action == "accuracy_line":
                    # Accuracy log updates arrive from the engine reader thread;
                    # Tk widgets may only be touched on the main thread.
                    try:
                        d = item[1]
                        self._append_accuracy_move_line(d["move"], d["category"], d["cp_loss"],
                                                        d["is_best"], d["side"], d["move_number"],
                                                        better_move=d.get("better_move"))
                        self.update_stats_display()
                        if getattr(getattr(self, "coach_mode", None), "tts_enabled", False):
                            self._speak_move_grade(d["category"])
                    except Exception as exc:
                        print(f"Accuracy line error: {exc}")
                elif action == "cloud_lines":
                    # Cloud-explorer fetch finished on a background thread;
                    # commit the cached result on the Tk thread and redraw.
                    try:
                        fen = item[1]
                        result = item[2]
                        if fen == self.current_fen:
                            self._cloud_lines_fen = fen
                            self._cloud_lines_result = result
                            if not result:
                                self._cloud_lines_fail_until = time.time() + 30.0
                        self._cloud_lines_inflight = False
                        self.update_display()
                    except Exception:
                        self._cloud_lines_inflight = False
                elif action == "perspective" and not self.hotkeys_locked:
                    self.set_perspective(item[1])
                    refresh = True
                elif action == "toggle_auto":
                    self.toggle_autoplay()
                elif action == "toggle_lock":
                    self.toggle_hotkeys_lock()
                elif action == "toggle_stealth":
                    self.toggle_stealth_mode()
                elif action == "toggle_control_panel":
                    self.toggle_control_panel()
                elif action == "toggle_panic":
                    self.panic()
                elif action == "ext_cmd":
                    # Forward command to all connected extension clients
                    cmd_str = item[1]
                    try:
                        self.ws_broadcast(json.loads(cmd_str))
                    except Exception:
                        pass
                elif action == "promotion_geometry":
                    self._accept_promotion_geometry(data)
                elif action == "board_geometry":
                    # Board screen geometry reported by the extension; consumed
                    # by the real-mouse AutoMove path.
                    try:
                        d = item[1]
                        if isinstance(d, dict) and d.get("ok") and d.get("width"):
                            curr_key = (round(float(d.get("left", 0))), round(float(d.get("top", 0))),
                                        round(float(d.get("width", 0))), bool(d.get("flipped")))
                            if getattr(self, "_last_logged_board_geom", None) != curr_key:
                                self._last_logged_board_geom = curr_key
                                g = d
                                print("[AutoMove] board geometry: "
                                      f"{g.get('width')}x{g.get('height')} px "
                                      f"@({g.get('left')},{g.get('top')}) "
                                      f"scale={g.get('scale')} "
                                      f"flipped={bool(g.get('flipped'))}")
                            self._board_geometry = d
                        else:
                            self._board_geometry = None
                    except Exception:
                        self._board_geometry = None
                elif action == "ui_state":
                    # Extension game-state gate for AutoMove: game-over and
                    # waiting-queue states pause the automove without touching
                    # its enabled toggle.
                    try:
                        d = item[1]
                        new_over = bool(d.get("gameOver"))
                        new_queue = bool(d.get("inQueue"))
                        was_over = self._automove_game_over
                        self._automove_game_over = new_over
                        self._automove_in_queue = new_queue
                        if new_over and not was_over:
                            print("[AutoMove] game over detected — AutoMove paused")
                            # Auto-queue the next game a short while after the
                            # result screen settles (only once per game end).
                            self._arm_auto_queue()
                        if not new_over and was_over:
                            # Left the result screen / fresh game: reset ALL
                            # per-game AutoMove state (played-FEN, game-over
                            # gate, flip episode, stale drag epoch) so the next
                            # game can never inherit the previous one's state.
                            self._reset_automove_for_new_game()
                            print("[AutoMove] game over cleared — "
                                  "AutoMove re-armed for the new game")
                    except Exception:
                        pass
                elif action == "clocks":
                    # Clock times arrived from the extension thread; commit them
                    # on the Tk thread and refresh so flag-mode and the clock
                    # labels pick up the new values immediately (the old handler
                    # was a no-op, leaving the refresh button effectively dead).
                    try:
                        d = item[1]
                        if isinstance(d, dict) and d.get("self_ms") is not None:
                            self.extension_clocks = {
                                "self_ms": d.get("self_ms"),
                                "opp_ms": d.get("opp_ms"),
                                "src": d.get("src"),
                            }
                    except Exception:
                        pass
                    refresh = True
                elif action == "premove_clear":
                    # The opponent (or we) just moved: drop any queued premove
                    # arrows AND stale suggestion data immediately.  A stale
                    # premove must never survive onto the new position while the
                    # fresh analysis is still being debounced/searching.
                    try:
                        self.arrow_canvas.delete("arrow")
                        self.arrow_canvas.delete("premove")
                        self._stop_arrow_pulse()
                        self.multi_pvs.clear()
                        self.committed_multi_pvs.clear()
                        self.pv_line = []
                        self.committed_pv_line = []
                        self.best_move = None
                    except Exception:
                        pass
            except Exception as exc:
                # Keep the UI pump alive even if one malformed/stale event fails.
                print(f"UI event error ({item[0] if item else 'unknown'}): {exc}")
        if refresh or getattr(self, "_pending_gui_updates", False):
            self._pending_gui_updates = False
            try:
                self.update_display()
            except Exception as exc:
                # Never let a display bug kill the UI pump; otherwise every
                # refresh stops and the overlay appears to "break" mid-session.
                print(f"Display update error: {exc}")
        if self.root.winfo_exists():
            self.root.after(1 if not self.ui_queue.empty() else self.gui_update_delay, self.poll_ui_queue)

    def _fire_pending_analysis(self):
        self._analysis_debounce_id = None
        payload = getattr(self, "_pending_analysis_payload", None)
        self._pending_analysis_payload = None
        if payload:
            fen, elo, flipped, playing_as, resync = payload
            if getattr(self, "_analysis_debounce_fen", None) == fen:
                self.apply_analysis_request(fen, elo, flipped, playing_as, resync=resync)

    def has_capture_sequence(self, pv_moves):
        """OPTIMIZED: Early termination and simplified logic"""
        if len(pv_moves) < 4:
            return False

        consecutive = 0
        for i in range(len(pv_moves) - 1):
            if len(pv_moves[i]) >= 4 and len(pv_moves[i+1]) >= 4:
                if pv_moves[i][2:4] == pv_moves[i+1][:2]:
                    consecutive += 1
                    if consecutive >= 2:  # Early termination
                        return True
                else:
                    consecutive = 0
        return False

    def execute_autoplay(self):
        """Deprecated: external mouse control is intentionally disabled."""
        return

    # ====================================================================
    # AutoMove — human-like mouse-motion orchestration
    # ====================================================================
    def _automove_tick(self):
        """Periodic check that fires the automove when it is our turn and the
        engine has committed a move for the current position."""
        try:
            self._canvas_board_geometry_cache = self._canvas_board_geometry()
        except Exception:
            pass
        if getattr(self, "_automove_debug_geometry", False):
            try:
                self._debug_geometry_dump()
            except Exception:
                pass
        try:
            self._automove_ensure_orientation()
        except Exception:
            pass
        try:
            self._auto_queue_check()
        except Exception:
            pass
        try:
            self._automove_check()
        except Exception:
            pass
        self.update_automove_status_label()
        if getattr(self, "should_run", False) and self.root.winfo_exists():
            self._automove_tick_id = self.root.after(700, self._automove_tick)

    def _arm_auto_queue(self):
        """Arm the auto-queue click after a game ends (once per game end)."""
        if self._auto_queue_enabled and not self._auto_queue_pending:
            self._auto_queue_pending = True
            self._auto_queue_attempts = 0
            self._auto_queue_started_at = time.time()
            self._auto_queue_fire_at = (time.time() + self._auto_queue_delay_s)
            print(f"[AutoQueue] game over — queueing next game "
                  f"in {self._auto_queue_delay_s:.1f}s")

    def _reset_automove_for_new_game(self):
        """Clear ALL per-game AutoMove state when a fresh game starts so the
        new game can never inherit the previous game's already-played FEN,
        game-over gate, stale flip episode, or a half-finished drag.  Called
        from the gameOver->live ui_state transition and from the new-game
        branch of apply_analysis_request.  Tk-thread only."""
        self._automove_game_over = False
        self._automove_in_queue = False
        self._automove_sent_fen = None
        self._automove_sent_at = 0.0
        self._automove_retry_at = 0.0
        self._automove_epoch += 1          # invalidate any stale worker drags
        self._automove_flip_streak = 0
        self._automove_flip_episode_bottom = None
        self._automove_flip_giveup_until = 0.0
        self._automove_last_flip_at = 0.0
        self._automove_flip_pending_until = 0.0
        self._automove_flip_verify_at = 0.0
        self._auto_queue_pending = False
        self._auto_queue_attempts = 0
        self._auto_queue_started_at = 0.0
        # A fresh game = a fresh visual reading; drop the previous game's
        # bottom colour so the new board is what decides the side.
        with self._screen_lock:
            self._screen_bottom_color = None
            self._screen_bottom_at = 0.0
            self._screen_bottom_conf = 0.0
            self._screen_meta = {}
        # Re-arm the "only read the side at game start" gate: a new match is
        # exactly when the board is clean and easy to read.
        self._screen_perspective_locked = False
        self._match_perspective_flip_count = 0
        if getattr(self, "analysis_auto_mode", False) or getattr(self, "player_color_setting", "auto") == "auto":
            self.player_color = "auto"
            self.detected_player_color = None

    def _auto_queue_check(self):
        """Fire the queued 'queue_next' extension click once the post-game
        delay elapses.  Retries a few times if the click does not land (the
        result screen can take a moment to show the Play-again affordance);
        self-clears if a new game starts or all retries are exhausted."""
        if not getattr(self, "_auto_queue_pending", False):
            return
        now = time.time()
        if not self._automove_game_over:
            self._auto_queue_pending = False
            self._auto_queue_attempts = 0
            self._auto_queue_started_at = 0.0
            return
        if now < getattr(self, "_auto_queue_fire_at", 0.0):
            return
        if now > getattr(self, "_auto_queue_started_at", 0.0) + 30.0 \
                or self._auto_queue_attempts >= 3:
            print("[AutoQueue] click never landed — giving up")
            self._auto_queue_pending = False
            self._auto_queue_attempts = 0
            self._auto_queue_started_at = 0.0
            return
        self._auto_queue_attempts += 1
        self._auto_queue_fire_at = now + 5.0
        print(f"[AutoQueue] clicking Play again / queue... "
              f"(attempt {self._auto_queue_attempts}/3)")
        self.send_extension_command({"action": "queue_next"})

    def _debug_geometry_dump(self):
        """Live dump (once per tick) of everything geometry-related, so the
        user can line the visualizer up over the board and compare the printed
        a1/h8 against the real cursor position."""
        try:
            canvas = getattr(self, "canvas", None)
            rx = ry = cw = ch = -1
            if canvas is not None:
                try:
                    if canvas.winfo_exists():
                        rx, ry = canvas.winfo_rootx(), canvas.winfo_rooty()
                        cw, ch = canvas.winfo_width(), canvas.winfo_height()
                except Exception:
                    pass
            ratio = 1.0
            try:
                dpi = ctypes.windll.user32.GetDpiForSystem()
                ratio = max(0.5, dpi / 96.0) if dpi else 1.0
            except Exception:
                pass
            canvas_g = getattr(self, "_canvas_board_geometry_cache", None)
            ext = self._board_geometry
            cal = getattr(self, "_board_calibration", None)
            cx = cy = -1
            try:
                p = _POINT()
                if ctypes.windll.user32.GetCursorPos(ctypes.byref(p)):
                    cx, cy = int(p.x), int(p.y)
            except Exception:
                pass

            def _sq(geom, sq):
                try:
                    x, y = self._square_screen_coords(sq, geom)
                    return "%s(%.0f,%.0f)" % (sq, x, y)
                except Exception:
                    return "%s(err)" % sq

            if isinstance(canvas_g, dict) and canvas_g.get("ok"):
                cw_ = float(canvas_g.get("width") or 0)
                line3 = ("[GeomDebug] visualizer: %.0fpx "
                         "@(%.0f,%.0f) flipped=%s dpi_ratio=%.2f  "
                         "a1=%s h8=%s  a8=%s h1=%s"
                         % (cw_, canvas_g.get("left"), canvas_g.get("top"),
                            bool(canvas_g.get("flipped")),
                            canvas_g.get("ratio", 1.0),
                            _sq(canvas_g, "a1"), _sq(canvas_g, "h8"),
                            _sq(canvas_g, "a8"), _sq(canvas_g, "h1")))
            else:
                line3 = "[GeomDebug] visualizer: None"
            if isinstance(ext, dict) and ext.get("width"):
                line4 = ("[GeomDebug] extension: %.0fx%.0f @(%.0f,%.0f)"
                         % (ext.get("width"), ext.get("height"),
                            ext.get("left"), ext.get("top")))
            else:
                line4 = "[GeomDebug] extension: None"
            if isinstance(cal, dict) and cal.get("size"):
                line4 += (" | calibration: %.0fpx @(%.0f,%.0f)"
                          % (float(cal["size"]), cal.get("left"), cal.get("top")))
            else:
                line4 += " | calibration: None"
            print("[GeomDebug] canvas root=(%d,%d) size=%dx%d "
                  "flipped=%s" % (rx, ry, cw, ch,
                                  self.perspective == "black"))
            print("[GeomDebug] cursor=(%d,%d)  rel-to-canvas=(%d,%d)"
                  % (cx, cy, cx - rx, cy - ry))
            print(line3)
            print(line4)
        except Exception as exc:
            print("[GeomDebug] error: %s" % exc)

    def _kick_automove(self):
        """Marshal engine callbacks to Tk without waiting on Tk from a reader."""
        if getattr(self, 'should_run', False):
            self.ui_queue.put(('automove_check',))

    def _bottom_color_now(self, trusted_only=False):
        """Current PHYSICAL bottom-of-board colour ('w'/'b').

        Authoritative Priority Pipeline:
          1. Chrome Extension / API Broadcast (Game.getPlayingAs() or flipped flag) — 100% Authoritative!
          2. Manual board calibration saved by user.
          3. Visual Screen Detector (with temporal voting lock during opening).
          4. FEN Piece Distribution Fallback.
        """
        # 1. Extension API Broadcast (Game object / flipped flag)
        fl = getattr(self, "current_flipped", None)
        if fl in (0, 1, True, False):
            return "b" if fl else "w"
        g = getattr(self, "_board_geometry", None)
        if isinstance(g, dict) and g.get("width"):
            return "b" if bool(g.get("flipped")) else "w"
        
        # 2. Saved Calibration
        cal = getattr(self, "_board_calibration", None)
        if isinstance(cal, dict) and cal.get("size"):
            return "b" if cal.get("flipped") else "w"

        # 3. Visual Screen Detector (only if fresh and confident)
        screen, age = self._screen_bottom_result()
        if screen in ("w", "b") and age < 12.0:
            return screen
            
        if trusted_only:
            return None

        # 4. Detected player side / FEN fallback
        dp = getattr(self, "detected_player_color", None)
        if dp in ("w", "b"):
            return dp
        return None

    def _automove_ensure_orientation(self):
        """Flip the physical board so the human's colour is at the bottom.

        chess.com orients your own colour to the bottom automatically, but a
        stale orientation (e.g. a previous game as black) can leave the board
        showing the wrong side.  That makes the auto-detected human colour
        disagree with reality and AutoMove stops firing.  When the human's
        side is known and the board bottom disagrees:

          1. Ask the extension to click the board's flip control (no OS focus
             needed).  If ~2s later the bottom STILL disagrees, the click did
             not take — fall back to pressing the site's real flip hotkey
             through the OS ('x' chess.com / 'f' lichess), a trusted keypress.

        MASTER GATE: this runs on every tick, so it ONLY does anything while
        AutoMove is genuinely enabled (same gate as _automove_check) — a
        disabled AutoMove must NEVER press the OS flip hotkey.  The flip-spam
        bug was the retry loop re-arming forever: the trusted bottom source can
        stay silent (no geometry broadcast / static calibration), so the
        "still disagrees" verification fired every ~6s indefinitely.  An
        anti-loop episode guard below gives up for 30s after a few hotkey
        presses that never rotate the board.
        """
        try:
            am = getattr(self, "automove", None)
            if am is None or not getattr(am, "enabled", False):
                return
            if not getattr(self, "analysis_auto_mode", False):
                return
            if self._automove_busy:
                return
            if getattr(self, "_automove_game_over", False):
                return
            if getattr(self, "_automove_in_queue", False):
                return
            now = time.time()
            if now < getattr(self, "_automove_flip_giveup_until", 0.0):
                return
            human = self._human_color()
            if human not in ("w", "b"):
                return
            # Only trust a VISUAL bottom signal (geometry broadcast /
            # calibration) for the flip decision — chess.com's
            # getOptions().flipped can be stale, and flipping on a stale signal
            # would flip an already-correct board in a loop.  If we only have
            # the FEN flipped flag, chess.com auto-orients in live games anyway
            # and the side is already resolved by getPlayingAs.
            bottom = self._bottom_color_now(trusted_only=True)
            if bottom not in ("w", "b") or bottom == human:
                return
            last = getattr(self, "_automove_last_flip_at", 0.0)
            episode = getattr(self, "_automove_flip_episode_bottom", None)
            if episode is None:
                # Start a flip episode: remember the bottom BEFORE the flip so
                # verification can tell a flip that actually worked from one
                # that did nothing (the whole point of the anti-loop guard).
                self._automove_flip_episode_bottom = bottom
                self._automove_flip_streak = 0
                episode = bottom
            # A flip was already attempted recently.  If enough time has passed
            # without the bottom fixing itself, the click failed — press the
            # real OS flip hotkey (trusted keypress on the focused tab).
            if now - last >= 2.0 and now - last < 10.0:
                if now >= getattr(self, "_automove_flip_verify_at", 0.0):
                    # Anti-loop: the flip only "worked" if the observed bottom
                    # actually changed.  A stale/silent source means it never
                    # changes, so a handful of OS hotkey presses that produce no
                    # rotation are enough to give up for a while — otherwise we
                    # would press the key forever.
                    if bottom == episode:
                        self._automove_flip_streak += 1
                    else:
                        # The board rotated: reset the streak but keep the new
                        # bottom as the episode anchor; if it still disagrees
                        # with the human's side the click round restarts.
                        self._automove_flip_episode_bottom = bottom
                        episode = bottom
                        self._automove_flip_streak = 0
                    if self._automove_flip_streak >= 3:
                        self._automove_flip_giveup_until = now + 30.0
                        self._automove_flip_episode_bottom = None
                        self._automove_flip_streak = 0
                        print("[AutoMove] flip did not take after 3 hotkey "
                              "tries — giving up for 30s (stale board source?)")
                        return
                    self._automove_last_flip_at = now
                    self._automove_flip_pending_until = now + 6.0
                    self._automove_flip_verify_at = now + 4.0
                    print(f"[AutoMove] flip click didn't take — pressing OS "
                          f"flip hotkey (bottom={bottom}, you={human})")
                    self._send_os_flip_key()
                    return
            if now < getattr(self, "_automove_flip_pending_until", 0.0):
                return
            if now - last < 4.0:
                return
            self._automove_last_flip_at = now
            self._automove_flip_pending_until = now + 4.0
            self._automove_flip_verify_at = now + 2.0
            print(f"[AutoMove] board oriented wrong (bottom={bottom}, "
                  f"you={human}) — flipping")
            self.send_extension_command({"action": "flip_board"})
        except Exception as exc:
            print(f"[AutoMove] orientation check error: {exc}")

    def _send_os_flip_key(self):
        """Press the site's real board-flip hotkey through the OS: 'x' on
        chess.com, 'f' on lichess.  A trusted OS keypress (no synthetic page
        events).  Requires the chess.com tab to have focus, which is true while
        AutoMove is dragging with the real mouse anyway."""
        try:
            key = 0x58   # VK: 'X'
            g = getattr(self, "_board_geometry", None)
            if isinstance(g, dict) and "lichess" in str(g.get("host") or ""):
                key = 0x46   # VK: 'F'
            user32 = ctypes.windll.user32
            user32.keybd_event(key, 0, 0, 0)      # KEYEVENTF_KEYDOWN
            time.sleep(0.04)
            user32.keybd_event(key, 0, 2, 0)      # KEYEVENTF_KEYUP
            print(f"[AutoMove] OS flip key 0x{key:02X} pressed")
            return True
        except Exception as exc:
            print(f"[AutoMove] OS flip key failed: {exc}")
            return False

    def _automove_chosen_move(self):
        """Pick the EXACT move shown as the #1 (headline) suggestion arrow.

        Always draws from _compute_visible_suggestions() so AutoMove moves
        the EXACT same move that is drawn on screen (including opening book lines,
        mode ordering, target accuracy candidates, and draw avoidance).
        NEVER falls back to best_move — if there is no visible suggestion the
        move is skipped entirely so we never play something the user can't see.
        """
        try:
            visible = self._compute_visible_suggestions()
            if visible and len(visible) > 0:
                _rank, moves, _score = visible[0]
                if moves and len(moves) > 0:
                    return moves[0]
        except Exception:
            pass
        return None

    def _green_automove_candidate(self):
        """Pick a deliberately-lower but still-good "green" line (below the
        target band, floor at ~45% so it is never a blunder).  Returns the move
        string, or None when nothing qualifies — e.g. in the opening, where the
        best move must stay #1."""
        try:
            if self._game_phase() == "opening":
                return None
            candidates = []
            for rank, data in self._visible_multi_pvs_raw():
                if not data or not data[0]:
                    continue
                score = self._display_to_white_score(data[1])
                if score is None:
                    continue
                candidates.append((rank, data, score))
            if len(candidates) < 2:
                return None
            us = self._human_color()
            _best_rank, _best_data, best_score = candidates[0]
            best_human = best_score if us == "w" else -best_score
            low = float(getattr(self, "target_recommendation_accuracy_min", 70) or 70)
            floor = 45.0
            best_green = None
            for rank, data, score in candidates[1:]:
                est = self._accuracy_from_loss(
                    best_human * 100.0,
                    self._human_pov_score(score) * 100.0)
                if est is None:
                    continue
                if floor <= est < low:
                    if best_green is None or est > best_green[0]:
                        best_green = (est, data)
            if best_green is None:
                return None
            return best_green[1][0][0]
        except Exception:
            return None

    def _automove_check(self):
        am = getattr(self, "automove", None)
        if am is None:
            self._automove_last_reason = "disabled"
            return
        if not getattr(am, "enabled", False):
            self._automove_last_reason = "disabled"
            return
        fen = getattr(self, "current_fen", None)
        if not fen:
            self._automove_last_reason = "no FEN yet"
            return
        # Game-over / waiting-queue gates (extension-driven, with a cheap FEN
        # fallback for standard-chess checkmate where the board loses a king).
        if not getattr(self, "_automove_game_over", False):
            try:
                if not getattr(self, "extension_variant", None):
                    board = self._parse_fen_board(fen.split()[0])
                    kings = sum(1 for p in board.values() if p.lower() == "k")
                    if kings < 2:
                        self._automove_game_over = True
                        self._automove_last_reason = "game over — AutoMove paused"
                        print("[AutoMove] king(s) gone — game over, AutoMove paused")
                        self._arm_auto_queue()
                        return
            except Exception:
                pass
        else:
            # Re-arm fallback: if the extension's gameOver->live transition
            # never arrived but the board now holds a full two-king position,
            # a new game has started — drop the stale game-over gate so the
            # new game can actually be played.
            try:
                if not getattr(self, "extension_variant", None):
                    board = self._parse_fen_board(fen.split()[0])
                    kings = sum(1 for p in board.values() if p.lower() == "k")
                    if kings >= 2:
                        self._reset_automove_for_new_game()
                        print("[AutoMove] full board detected — new game "
                              "re-armed AutoMove")
            except Exception:
                pass
        if getattr(self, "_automove_game_over", False):
            self._automove_last_reason = "game over — AutoMove paused"
            return
        if getattr(self, "_automove_in_queue", False):
            self._automove_last_reason = "waiting for opponent — no moves"
            return
        if self._automove_busy:
            self._automove_last_reason = "busy dragging"
            return
        if fen == self._automove_sent_fen:
            # Safety net: if the drag never registered on the site after a long
            # while (window focus, promotion UI, etc.) allow a fresh attempt.
            if self._automove_sent_at and (time.time() - self._automove_sent_at) > 90:
                self._automove_sent_fen = None
            else:
                self._automove_last_reason = "move already sent for this FEN"
                return
        if time.time() < getattr(self, "_automove_retry_at", 0.0):
            self._automove_last_reason = "waiting to retry board geometry"
            return
        if not self._is_human_turn():
            self._automove_last_reason = (
                f"opponent's turn (side to move "
                f"= {getattr(self, 'current_player_color', '?')}, "
                f"you = {self._human_color()})")
            return
        if not getattr(self, "pv_ready_to_play", False):
            self._automove_last_reason = "engine hasn't committed a move yet"
            return
        if self.recommendation_accuracy_enabled and self.engines:
            primary = self.engines[min(self.primary_engine_index, len(self.engines)-1)]
            session = primary.get('session')
            if session and session.state in ('uci', 'ready', 'search', 'stopping'):
                self._automove_last_reason = 'waiting for accuracy search to finish'
                return
        move = self._automove_chosen_move()
        if not move:
            self._automove_last_reason = "no recommended move"
            return
        if self.recommendation_accuracy_enabled:
            for engine in self.engines:
                override = engine.get('_accuracy_pool_override')
                if (override and override[0] == self._accuracy_pool_key(engine, fen)
                        and override[1] > engine.get('_multipv_count', 1)):
                    self._automove_last_reason = 'finding accuracy alternatives'
                    return
        src = "accuracy-band"
        if move == getattr(self, "best_move", None):
            src = "engine-best"
        if getattr(self, "recommendation_accuracy_estimate", None) is not None \
                and src == "accuracy-band":
            src += (f" ({getattr(self, 'recommendation_accuracy_estimate', 0):.0f}% "
                    f"estimated)" if not isinstance(
                        getattr(self, "recommendation_accuracy_estimate", None),
                        type(None)) else " (estimate n/a)")

        # Safety: verify the chosen move actually starts from a square owned by
        # the human's colour.  This prevents stale PVs from a previous position
        # or a wrong-side analysis being played as a move.
        try:
            move_color = self._mover_color_from_uci(fen, move)
            if move_color is not None and move_color != self._human_color():
                self._automove_last_reason = (
                    f"move {move} belongs to opponent ({move_color}), skipping")
                print(f"[AutoMove] BLOCKED {move} — move belongs to "
                      f"{move_color} not {self._human_color()}")
                self._automove_sent_fen = None
                return
        except Exception:
            pass

        # Commit: remember this position so we never double-fire, then run the
        # thinking-time + drag on a worker thread (never blocks the Tk loop).
        self._automove_sent_fen = fen
        self._automove_sent_at = time.time()
        self._automove_busy = True
        self._automove_epoch += 1
        epoch = self._automove_epoch
        self._automove_last_reason = "committed — dragging"
        print(f"[AutoMove] commit {move} ({fen}) [{src}] — thinking then "
              f"dragging with the real mouse")
        threading.Thread(target=self._automove_worker,
                         args=(fen, move, epoch), daemon=True).start()

    def _automove_worker(self, fen, move, epoch):
        """Background: think like a human, then drag the piece with the REAL
        OS mouse along a generated curved path."""
        if threading.current_thread() is threading.main_thread():
            threading.Thread(target=self._automove_worker,
                             args=(fen, move, epoch), daemon=True).start()
            return
        try:
            am = getattr(self, "automove", None)
            if am is None:
                return
            think_ms = am.thinking_time_ms(fen)
            deadline = time.time() + (think_ms / 1000.0)
            while time.time() < deadline:
                if (self._automove_epoch != epoch
                        or not getattr(am, "enabled", False)):
                    return
                time.sleep(0.02)

            # Guard against the board changing while we were "thinking".
            if (self._automove_epoch != epoch
                    or not getattr(am, "enabled", False)
                    or getattr(self, "_automove_game_over", False)
                    or getattr(self, "_automove_in_queue", False)
                    or getattr(self, "current_fen", None) != fen
                    or not self._is_human_turn()):
                print("[AutoMove] cancelled during think")
                return

            # Re-evaluate move selection AFTER the thinking pause!
            # If the engine or target accuracy updated during thinking time,
            # pick the latest move currently drawn on screen (Arrow #1).
            latest_move = self._automove_chosen_move()
            if latest_move:
                move = latest_move

            plan = am.generate_plan(move)
            if self._automove_epoch != epoch or not getattr(am, "enabled", False):
                return

            geom = self._get_board_geometry(wait_ms=4000)
            if not (geom and geom.get("ok") and geom.get("width")):
                print("[AutoMove] NO board geometry — move NOT played")
                self._automove_sent_fen = None
                self._automove_retry_at = time.time() + 8.0
                return
            if (self._automove_epoch != epoch or not getattr(am, "enabled", False)
                    or getattr(self, "current_fen", None) != fen
                    or getattr(self, "_automove_game_over", False)
                    or getattr(self, "_automove_in_queue", False)
                    or not self._is_human_turn()):
                print("[AutoMove] cancelled while obtaining geometry")
                return

            print(f"[AutoMove] dragging {move} with real mouse...")
            self._real_mouse_drag(move, plan, geom)
            if len(move) > 4:
                try:
                    if not self._native_promotion(move[4], epoch):
                        raise RuntimeError("promotion was not selected")
                except Exception:
                    am.enabled = False  # Do not re-drag while a chooser is unresolved.
                    raise
            print("[AutoMove] drag done")
        except Exception as exc:
            print(f"[AutoMove] error: {exc}")
            if getattr(self, '_automove_sent_fen', None) == fen:
                self._automove_sent_fen = None
                self._automove_retry_at = time.time() + 3.0
        finally:
            self._automove_busy = False


    def _get_board_geometry(self, wait_ms=4000):
        """Where is the chess board on the physical screen?

        Prefer the visualizer's current canvas rectangle.  Tk must be queried
        on its own thread; a stale canvas snapshot is not safe to click.
        """
        if threading.current_thread() is threading.main_thread():
            canvas_g = self._canvas_board_geometry()
        else:
            result, ready = [], threading.Event()
            self.ui_queue.put(("automove_geometry", result, ready))
            timeout = max(wait_ms / 1000.0, getattr(self, 'gui_update_delay', 100) / 1000.0 + 0.5)
            if not ready.wait(timeout):
                print("[AutoMove] visualizer geometry request timed out")
                return None
            canvas_g = result[0] if result else None
        if isinstance(canvas_g, dict) and canvas_g.get("ok") and canvas_g.get("width") and canvas_g.get("height"):
            return canvas_g
        g = self._board_geometry
        if isinstance(g, dict) and g.get("ok") and g.get("width"):
            if getattr(self.automove, "enabled", False):
                print("[AutoMove] board geometry (live broadcast): "
                      f"{g.get('width')}x{g.get('height')} px "
                      f"@({g.get('left')},{g.get('top')}) "
                      f"scale={g.get('scale')} flipped={bool(g.get('flipped'))}")
            return g
        try:
            self.send_extension_command({"action": "get_board_geometry"})
        except Exception as exc:
            print(f"[AutoMove] geometry request error: {exc}")
            self._board_geometry = None
        deadline = time.time() + max(100, wait_ms) / 1000.0
        resent = False
        while time.time() < deadline:
            g = self._board_geometry
            if g is not None:
                print("[AutoMove] board geometry received: "
                      f"{g.get('width')}x{g.get('height')} px "
                      f"@({g.get('left')},{g.get('top')}) "
                      f"scale={g.get('scale')} flipped={bool(g.get('flipped'))}")
                return g
            if not resent and time.time() > deadline - 2.5:
                # One retry — the first reply occasionally gets lost.
                resent = True
                try:
                    self.send_extension_command(
                        {"action": "get_board_geometry"})
                except Exception:
                    pass
            time.sleep(0.1)
        cal = getattr(self, "_board_calibration", None)
        if isinstance(cal, dict) and cal.get("size"):
            size = float(cal["size"])
            print(f"[AutoMove] extension silent — using calibrated board "
                  f"{size:.0f}px @({cal.get('left')},{cal.get('top')}) "
                  f"flipped={bool(cal.get('flipped'))}")
            return {"ok": True, "left": cal["left"], "top": cal["top"],
                    "width": size, "height": size, "scale": 1.0,
                    "flipped": bool(cal.get("flipped")), "host": "calibrated"}
        print(f"[AutoMove] board geometry timed out after {wait_ms} ms — "
              f"extension did not answer and no calibration saved")
        return self._board_geometry

    def _canvas_board_geometry(self):
        """Derive the board's on-screen rectangle from the overlay's OWN board.

        The transparent canvas sits over the chess.com board (arrows are drawn
        on it), so the canvas's a1 / h8 square centres ARE the physical board
        positions.  No extension and no calibration needed.  Tk-thread only —
        never call this from a worker thread.
        """
        try:
            canvas = getattr(self, "canvas", None)
            if canvas is None:
                return None
            try:
                if not canvas.winfo_exists() or not canvas.winfo_ismapped():
                    return None
            except Exception:
                return None
            cw, ch = canvas.winfo_width(), canvas.winfo_height()
            if cw <= 8 or ch <= 8:
                return None
            rx, ry = canvas.winfo_rootx(), canvas.winfo_rooty()
            bw, bh = cw - 6, ch - 6
            if min(bw, bh) < 64:
                return None
            return {"ok": True, "left": rx + 3, "top": ry + 3, "width": bw,
                    "height": bh, "scale": 1.0,
                    "flipped": self.perspective == "black", "host": "canvas"}
        except Exception:
            return None

    # ====================================================================
    # Visual bottom-colour detector — "is white or black at the bottom?"
    #
    # The auto side chooser (player_color == "auto") needs to know which side
    # the human actually is.  The extension's flipped/playingAs signals can be
    # stale or absent, so instead we LOOK at the board: capture the physical
    # screen region the visualizer sits over (GDI BitBlt — the same capture
    # OpenCV/DXGI wrappers use, no extra dependency) and analyse the piece
    # colours in the bottom two ranks vs the top two ranks.  White pieces are
    # bright, black pieces are dark, so a bottom that is whiter than the top
    # means the human is white.  This never needs the extension.
    # ====================================================================
    def _capture_board_region(self, geom, max_dimension=None):
        """Capture the on-screen board rectangle (from the visualizer geometry,
        physical pixels) and return it as a numpy HxWx3 BGR array, or None.
        Falls back to a pure-stdlib scanline reader when numpy is absent."""
        try:
            left = int(round(float(geom.get("left") or 0)))
            top = int(round(float(geom.get("top") or 0)))
            width = int(round(float(geom.get("width") or 0)))
            height = int(round(float(geom.get("height") or 0)))
            if width < 64 or height < 64:
                return None
            # Guard against the board being off-screen or on a secondary
            # monitor (the visualizer normally sits over the primary board).
            sm_x = user32.GetSystemMetrics(76)
            sm_y = user32.GetSystemMetrics(77)
            sm_w = user32.GetSystemMetrics(78)
            sm_h = user32.GetSystemMetrics(79)
            if left < sm_x or top < sm_y or \
                    left + width > sm_x + sm_w or top + height > sm_y + sm_h:
                return None

            gdi32 = ctypes.windll.gdi32
            # GDI handles are pointer-sized; ctypes otherwise defaults to int
            # and can truncate handles in 64-bit Windows processes.
            user32.GetDC.argtypes = [wintypes.HWND]
            user32.GetDC.restype = wintypes.HDC
            user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
            gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
            gdi32.CreateCompatibleDC.restype = wintypes.HDC
            gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
            gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
            gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
            gdi32.SelectObject.restype = wintypes.HGDIOBJ
            gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
            gdi32.DeleteDC.argtypes = [wintypes.HDC]
            gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
            gdi32.StretchBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                         ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int,
                                         ctypes.c_int, ctypes.c_int, wintypes.DWORD]
            gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT,
                                        wintypes.UINT, ctypes.c_void_p,
                                        ctypes.POINTER(_BITMAPINFOHEADER), wintypes.UINT]
            hdc = user32.GetDC(0)                    # whole screen
            factor = min(1.0, float(max_dimension) / max(width, height)) if max_dimension else 1.0
            out_width = max(1, int(round(width * factor)))
            out_height = max(1, int(round(height * factor)))
            mem = gdi32.CreateCompatibleDC(hdc)
            bmp = gdi32.CreateCompatibleBitmap(hdc, out_width, out_height)
            old = gdi32.SelectObject(mem, bmp)
            selected = True
            try:
                if factor < 1.0:
                    copied = gdi32.StretchBlt(mem, 0, 0, out_width, out_height,
                                              hdc, left, top, width, height, 0x00CC0020)
                else:
                    copied = gdi32.BitBlt(mem, 0, 0, width, height, hdc,
                                          left, top, 0x00CC0020)
                if not copied:
                    return None
                # GetDIBits requires the bitmap to be deselected from its DC.
                gdi32.SelectObject(mem, old)
                selected = False
                if _np is not None:
                    buf = ctypes.create_string_buffer(out_width * out_height * 4)
                    info = _BITMAPINFOHEADER(out_width, out_height)
                    if gdi32.GetDIBits(hdc, bmp, 0, out_height, buf, ctypes.byref(info), 0):
                        arr = _np.frombuffer(buf, dtype=_np.uint8)
                        arr = arr.reshape(out_height, out_width, 4)[:, :, :3]  # BGRA->BGR
                        return _np.ascontiguousarray(arr.copy())
                    return None
                # stdlib fallback: read 32-bit scanlines, drop alpha.
                row = out_width * 4
                stride = (row + 3) & ~3
                raw = ctypes.create_string_buffer(stride * out_height)
                info = _BITMAPINFOHEADER(out_width, out_height)
                if not gdi32.GetDIBits(hdc, bmp, 0, out_height, raw, ctypes.byref(info), 0):
                    return None
                data = bytearray(raw.raw)
                pixels = bytearray(out_width * out_height * 3)
                for y in range(out_height):
                    base = y * stride
                    out = y * out_width * 3
                    for x in range(out_width):
                        b0 = data[base + x * 4]
                        g0 = data[base + x * 4 + 1]
                        r0 = data[base + x * 4 + 2]
                        pixels[out + x * 3] = b0
                        pixels[out + x * 3 + 1] = g0
                        pixels[out + x * 3 + 2] = r0
                return _to_array(pixels, out_width, out_height)
            finally:
                if selected:
                    gdi32.SelectObject(mem, old)
                gdi32.DeleteObject(bmp)
                gdi32.DeleteDC(mem)
                user32.ReleaseDC(0, hdc)
        except Exception as exc:
            print(f"[ScreenBottom] capture failed: {exc}")
            return None

    def _screen_bottom_from_frame(self, frame, geom):
        """Analyse a captured board frame and return (bottom_color, conf,
        meta).  bottom_color is 'w'/'b' or None; conf in 0..1; meta is a
        diagnostic dict.  Pure numpy when available, stdlib fallback."""
        try:
            if _np is not None and not isinstance(frame, list):
                h, w = frame.shape[0], frame.shape[1]
                use_np = True
            else:
                # stdlib fallback frame: list-of-lists of (b,g,r) tuples.
                h, w = len(frame), len(frame[0]) if frame else 0
                use_np = False
            if h < 32 or w < 32:
                return None, 0.0, {"reason": "frame too small"}
            # Use the board area only (skip any file/rank labels + frame).
            bx0 = int(round(w * 0.045))
            by0 = int(round(h * 0.045))
            bx1 = int(round(w * 0.955))
            by1 = int(round(h * 0.955))
            sqw = (bx1 - bx0) / 8.0
            sqh = (by1 - by0) / 8.0
            # Verification gate: Is the captured region actually an 8x8 chessboard?
            if use_np:
                gray_all = frame.mean(axis=2)
                sq_m = _np.zeros((8, 8))
                for iy in range(8):
                    for ix in range(8):
                        x0 = int(bx0 + ix * sqw); y0 = int(by0 + iy * sqh)
                        x1 = int(x0 + sqw); y1 = int(y0 + sqh)
                        c_gray = gray_all[int(y0 + 0.25 * sqh):int(y1 - 0.25 * sqh),
                                          int(x0 + 0.25 * sqw):int(x1 - 0.25 * sqw)]
                        sq_m[iy, ix] = float(c_gray.mean()) if c_gray.size else 128.0
                mask_a = (_np.add.outer(_np.arange(8), _np.arange(8)) % 2 == 0)
                group_a = float(sq_m[mask_a].mean())
                group_b = float(sq_m[~mask_a].mean())
                checker_contrast = abs(group_a - group_b)
                total_range = float(sq_m.max() - sq_m.min())
                if checker_contrast < 10.0 or total_range < 25.0:
                    return None, 0.0, {"reason": "not a chessboard (no 8x8 checkerboard grid detected)"}

            def square_piece_score(ix, iy):
                """Compare the square's CENTRE (where the piece sits) against
                its own border RING (the bare board colour).  Returns signed delta
                if a piece is present, or 0.0 when the square is empty."""
                x0 = int(bx0 + ix * sqw)
                y0 = int(by0 + iy * sqh)
                x1 = int(x0 + sqw)
                y1 = int(y0 + sqh)
                pad_w = max(2, int(sqw * 0.05))
                pad_h = max(2, int(sqh * 0.05))
                if use_np:
                    sq_crop = frame[y0:y1, x0:x1]
                    gray = sq_crop.mean(axis=2)
                    top = gray[0:pad_h, :]
                    bot = gray[-pad_h:, :]
                    ring_mean = float((top.mean() + bot.mean()) / 2.0)
                    center = gray[int(sqh * 0.2):int(sqh * 0.8), int(sqw * 0.2):int(sqw * 0.8)]
                    cmean = float(center.mean())
                    std_val = float(center.std())
                    rng_val = float(center.max() - center.min())
                else:
                    cvals = _gray_block(frame, int(x0 + 0.25 * sqw), int(y0 + 0.25 * sqh), int(0.5 * sqw), int(0.5 * sqh))
                    cv = [v for v in cvals if v is not None]
                    cmean = sum(cv) / len(cv) if cv else 128.0
                    ring_mean = 128.0
                    std_val = 15.0
                    rng_val = 40.0
                delta = cmean - ring_mean
                # Empty square gate: uniform texture and center matching bare board edge
                if abs(delta) < 16.0 and std_val < 12.0 and rng_val < 35.0:
                    return 0.0
                if delta >= 16.0 or (cmean >= 200.0 and std_val >= 10.0):
                    return delta if delta > 0 else 25.0
                if delta <= -16.0 or (cmean <= 85.0 and std_val >= 10.0):
                    return delta if delta < 0 else -25.0
                return 0.0

            rows_w = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
            rows_pc = [0, 0, 0, 0, 0, 0, 0, 0]
            for iy in range(8):
                for ix in range(8):
                    sc = square_piece_score(ix, iy)
                    if sc != 0.0:
                        rows_pc[iy] += 1
                    rows_w[iy] += sc
            bottom = rows_w[7] + rows_w[6]
            top = rows_w[0] + rows_w[1]
            total_pc = sum(rows_pc)
            diff = bottom - top
            conf = 0.0
            if total_pc >= 4 and abs(diff) > 0.001:
                conf = min(1.0, (total_pc / 12.0) * 0.5
                           + min(1.0, abs(diff) / (8.0 * 90.0)) * 0.5)
            if conf < 0.35:
                return None, conf, {
                    "diff": round(diff, 1), "pieces": total_pc,
                    "bottom": round(bottom, 1), "top": round(top, 1),
                    "reason": "low confidence"}
            side = "w" if diff > 0 else "b"
            meta = {"diff": round(diff, 1), "pieces": total_pc,
                    "bottom": round(bottom, 1), "top": round(top, 1),
                    "conf": round(conf, 2)}
            return side, conf, meta
        except Exception as exc:
            return None, 0.0, {"reason": str(exc)}

    def _run_screen_bottom_detector(self):
        """Background analysis loop: every ~1.5s analyse a captured frame for
        bottom-colour / side-to-play perspective. Does NOT update the preview
        frame itself — that is done by _run_screen_preview_capture at 30 FPS."""
        try:
            time.sleep(1.0)
            while not self._screen_detector_stop.is_set():
                try:
                    geom = None
                    g = getattr(self, "_canvas_board_geometry_cache", None)
                    if isinstance(g, dict) and g.get("width"):
                        geom = g
                    else:
                        g2 = getattr(self, "_board_geometry", None)
                        if isinstance(g2, dict) and g2.get("width"):
                            geom = g2
                    if geom is not None:
                        frame = self._capture_board_region(geom)
                        if frame is not None:
                            side, conf, meta = \
                                self._screen_bottom_from_frame(frame, geom)
                            if self._screen_detector_debug:
                                src_name = "ext" if g is geom else "canvas"
                                try:
                                    fh = len(frame)
                                    fw = len(frame[0]) if fh else 0
                                    if _np is not None and not isinstance(frame, list):
                                        fh, fw = frame.shape[0], frame.shape[1]
                                    def rowavg(yf):
                                        y = max(0, min(fh - 1, int(fh * yf)))
                                        row = frame[y]
                                        xs = [int(fw * f) for f in (0.10, 0.30, 0.50, 0.70, 0.90)]
                                        vals = []
                                        for x in xs:
                                            px = row[min(x, fw - 1)]
                                            if isinstance(px, (list, tuple)):
                                                vals.append(round(sum(px) / len(px), 1))
                                            else:
                                                vals.append(round(float(px), 1))
                                        return vals
                                    sample = "top=" + str(rowavg(0.10)) + \
                                        " mid=" + str(rowavg(0.50)) + \
                                        " bot=" + str(rowavg(0.90))
                                except Exception:
                                    sample = ""
                                print(f"[ScreenBottom] src={src_name} side={side} "
                                      f"conf={conf:.2f} {meta} "
                                      f"region=({geom.get('left'):.0f},"
                                      f"{geom.get('top'):.0f}) "
                                      f"{geom.get('width'):.0f}x"
                                      f"{geom.get('height'):.0f}"
                                      f" pc={self.player_color}"
                                      f" {sample}")
                            if side in ("w", "b"):
                                with self._screen_lock:
                                    self._screen_bottom_color = side
                                    self._screen_bottom_at = time.time()
                                    self._screen_bottom_conf = conf
                                    self._screen_meta = meta
                                self._apply_screen_perspective()
                            else:
                                with self._screen_lock:
                                    self._screen_bottom_color = None
                                    self._screen_bottom_at = 0.0
                                    self._screen_bottom_conf = 0.0
                                    self._screen_meta = meta if isinstance(meta, dict) else {}
                except Exception as exc:
                    if self._screen_detector_debug:
                        print(f"[ScreenBottom] pass error: {exc}")
                self._screen_detector_stop.wait(1.5)
        except Exception:
            pass

    def _run_screen_preview_capture(self):
        """Capture and encode a small preview on the background thread."""
        try:
            while not self._screen_detector_stop.is_set():
                # Only capture if preview window exists
                if getattr(self, "_screen_preview_win", None) is None:
                    self._screen_detector_stop.wait(0.25)
                    continue
                try:
                    geom = None
                    g = getattr(self, "_canvas_board_geometry_cache", None)
                    if isinstance(g, dict) and g.get("width"):
                        geom = g
                    else:
                        g2 = getattr(self, "_board_geometry", None)
                        if isinstance(g2, dict) and g2.get("width"):
                            geom = g2
                    if geom is not None:
                        frame = self._capture_board_region(geom, max_dimension=240)
                        if frame is not None:
                            # Compute the grid overlay from the current FEN (very fast)
                            try:
                                grid = self._screen_grid_from_frame(frame, geom)
                            except Exception:
                                grid = None
                            with self._screen_lock:
                                previous = self._screen_preview_frame
                                same = False
                                if previous is not None:
                                    try:
                                        same = bool(_np.array_equal(previous, frame)) if _np is not None else previous == frame
                                    except Exception:
                                        pass
                                if same and grid == self._screen_preview_grid and geom == self._screen_preview_geom:
                                    frame = previous
                                else:
                                    previous = None
                            ppm = None if previous is not None else self._frame_to_ppm_bytes(frame, 240, 240)
                            with self._screen_lock:
                                self._screen_preview_frame = frame
                                if ppm is not None:
                                    self._screen_preview_ppm = ppm
                                self._screen_preview_geom  = geom
                                self._screen_preview_grid  = grid
                except Exception:
                    pass
                self._screen_detector_stop.wait(0.16)
        except Exception:
            pass

    def _start_screen_bottom_detector(self):
        """Idempotent start of the visual detector threads."""
        if self._screen_detector_started:
            return
        self._screen_detector_started = True
        threading.Thread(target=self._run_screen_bottom_detector,
                         daemon=True).start()
        threading.Thread(target=self._run_screen_preview_capture,
                         daemon=True).start()

    def _screen_grid_from_frame(self, frame, geom):
        """Per-square reading for the preview window: an 8x8 list of
        'w'/'b'/None meaning the detector classifies that square as a white
        piece, a black piece, or empty."""
        try:
            fen = getattr(self, "current_fen", None)
            persp = getattr(self, "perspective", "white")
            cached = getattr(self, "_cached_screen_grid", None)
            if fen and cached and cached[0] == fen and cached[1] == persp:
                return cached[2]

            if fen:
                try:
                    placement = fen.split()[0]
                    ranks = placement.split("/")
                    if len(ranks) == 8:
                        grid = []
                        is_black = (persp == "black")
                        for iy in range(8):
                            r_idx = (7 - iy) if is_black else iy
                            rank_str = ranks[r_idx]
                            row_pieces = []
                            for ch in rank_str:
                                if ch.isdigit():
                                    row_pieces.extend([None] * int(ch))
                                else:
                                    row_pieces.append("w" if ch.isupper() else "b")
                            if is_black:
                                row_pieces.reverse()
                            grid.append(row_pieces)
                        if len(grid) == 8 and all(len(row) == 8 for row in grid):
                            self._cached_screen_grid = (fen, persp, grid)
                            return grid
                except Exception:
                    pass

            if _np is not None and not isinstance(frame, list):
                h, w = frame.shape[0], frame.shape[1]
                use_np = True
            else:
                h = len(frame) if frame else 0
                w = len(frame[0]) if h else 0
                use_np = False
            if h < 32 or w < 32:
                return None
            bx0 = int(round(w * 0.045)); by0 = int(round(h * 0.045))
            bx1 = int(round(w * 0.955)); by1 = int(round(h * 0.955))
            sqw = (bx1 - bx0) / 8.0; sqh = (by1 - by0) / 8.0
            grid = []
            for iy in range(8):
                row_out = []
                for ix in range(8):
                    x0 = int(bx0 + ix * sqw); y0 = int(by0 + iy * sqh)
                    x1 = int(x0 + sqw); y1 = int(y0 + sqh)
                    pad_w = max(2, int(sqw * 0.05))
                    pad_h = max(2, int(sqh * 0.05))
                    if use_np:
                        sq_crop = frame[y0:y1, x0:x1]
                        gray = sq_crop.mean(axis=2)
                        center = gray[int(sqh * 0.25):int(sqh * 0.75), int(sqw * 0.25):int(sqw * 0.75)]
                        std_val = float(center.std())
                        rng_val = float(center.max() - center.min())
                    else:
                        std_val = 5.0; rng_val = 10.0

                    if std_val < 18.0 or rng_val < 45.0:
                        row_out.append(None)
                    else:
                        row_out.append("w" if iy >= 4 else "b")
                grid.append(row_out)
            return grid
        except Exception:
            return None

    def _frame_to_png_bytes(self, frame, target_w=480, target_h=480):
        """Convert a numpy array (BGR) or list-of-lists frame to a resized PNG byte string."""
        import zlib, struct
        if _np is not None and isinstance(frame, _np.ndarray):
            fh, fw = frame.shape[0], frame.shape[1]
            ys = (_np.linspace(0, fh - 1, target_h)).astype(_np.int32)
            xs = (_np.linspace(0, fw - 1, target_w)).astype(_np.int32)
            scaled = frame[_np.ix_(ys, xs)]  # HxWx3 BGR
            rgb = scaled[:, :, ::-1].astype(_np.uint8) # BGR -> RGB
            raw_bytes = rgb.tobytes()
        else:
            fh = len(frame)
            fw = len(frame[0]) if fh else 1
            raw = bytearray(target_w * target_h * 3)
            out_idx = 0
            for ty in range(target_h):
                fy = min(fh - 1, int(ty * fh / target_h))
                row = frame[fy]
                for tx in range(target_w):
                    fx = min(fw - 1, int(tx * fw / target_w))
                    px = row[fx]
                    if isinstance(px, (list, tuple)) and len(px) >= 3:
                        raw[out_idx] = px[2]     # R (from BGR)
                        raw[out_idx + 1] = px[1] # G
                        raw[out_idx + 2] = px[0] # B
                    else:
                        raw[out_idx] = raw[out_idx + 1] = raw[out_idx + 2] = int(px)
                    out_idx += 3
            raw_bytes = bytes(raw)

        png = bytearray(b'\x89PNG\r\n\x1a\n')
        ihdr = struct.pack('>IIBBBBB', target_w, target_h, 8, 2, 0, 0, 0)
        png.extend(struct.pack('>I', 13) + b'IHDR' + ihdr + struct.pack('>I', zlib.crc32(b'IHDR' + ihdr)))
        scanlines = bytearray()
        row_bytes = target_w * 3
        for y in range(target_h):
            scanlines.append(0)
            scanlines.extend(raw_bytes[y * row_bytes : (y + 1) * row_bytes])
        idat = zlib.compress(scanlines, level=1)
        png.extend(struct.pack('>I', len(idat)) + b'IDAT' + idat + struct.pack('>I', zlib.crc32(b'IDAT' + idat)))
        png.extend(struct.pack('>I', 0) + b'IEND' + struct.pack('>I', zlib.crc32(b'IEND')))
        return bytes(png)


    def _frame_to_ppm_bytes(self, frame, target_w, target_h):
        """Ultra-fast 60 FPS PPM raw image format using pure numpy/stdlib (no cv2 required)."""
        try:
            if _np is not None and isinstance(frame, _np.ndarray):
                h, w = frame.shape[0], frame.shape[1]
                ys = _np.linspace(0, h - 1, target_h, dtype=_np.int32)
                xs = _np.linspace(0, w - 1, target_w, dtype=_np.int32)
                resized = frame[ys[:, None], xs]
                rgb = resized[:, :, ::-1]   # Convert BGR -> RGB
                raw_bytes = _np.ascontiguousarray(rgb).tobytes()
            elif isinstance(frame, list) and len(frame) > 0:
                h, w = len(frame), len(frame[0])
                raw = bytearray(target_w * target_h * 3)
                idx = 0
                for ty in range(target_h):
                    fy = min(h - 1, int(ty * h / target_h))
                    row = frame[fy]
                    for tx in range(target_w):
                        fx = min(w - 1, int(tx * w / target_w))
                        b, g, r = row[fx]
                        raw[idx] = r
                        raw[idx + 1] = g
                        raw[idx + 2] = b
                        idx += 3
                raw_bytes = bytes(raw)
            else:
                raw_bytes = bytes([60] * (target_w * target_h * 3))
            header = f"P6\n{target_w} {target_h}\n255\n".encode("ascii")
            return header + raw_bytes
        except Exception:
            header = f"P6\n{target_w} {target_h}\n255\n".encode("ascii")
            return header + bytes([60] * (target_w * target_h * 3))

    def _screen_preview_refresh(self):
        """Tk thread: show the actual captured screenshot image with ESP-style
        boxes drawn over detected piece positions. Image encoding is done by
        the capture thread, so the Tk thread only displays the prepared image.
        """
        self._screen_preview_after_id = None
        win = getattr(self, "_screen_preview_win", None)
        if win is None:
            return
        try:
            if not win.winfo_exists():
                self._screen_preview_win = None
                return
            with self._screen_lock:
                frame = self._screen_preview_frame
                ppm_data = self._screen_preview_ppm
                grid  = self._screen_preview_grid
                side  = self._screen_bottom_color
                conf  = self._screen_bottom_conf
            canvas = getattr(self, "_screen_preview_canvas", None)
            if canvas is None:
                return

            PW       = 480
            STATUS_H = 22
            CELL     = PW // 8

            # Dirty-frame detection: skip full re-render if frame hasn't changed
            frame_id = id(frame) if frame is not None else None
            last_id  = getattr(self, "_preview_last_frame_id", None)
            last_side = getattr(self, "_preview_last_side", None)
            status_key = (side, round(conf, 2))
            frame_changed = (frame_id != last_id)
            status_changed = (status_key != last_side)

            if not frame_changed and not status_changed:
                self._screen_preview_after_id = self.root.after(160, self._screen_preview_refresh)
                return

            self._preview_last_frame_id = frame_id
            self._preview_last_side = status_key
            self._preview_last_frame = frame  # Keep identity alive until the next render.
            canvas.delete("all")

            if frame is None:
                canvas.create_rectangle(0, 0, PW, PW + STATUS_H,
                                        fill="#1a1a1a", outline="")
                canvas.create_text(PW // 2, PW // 2,
                                   text="Waiting for board capture...\n"
                                        "Position the visualizer over the board.",
                                   fill="#888", font=("Segoe UI", 10),
                                   justify="center")
            else:
                # Raw PPM buffer — zero compression, direct pixel transfer
                try:
                    photo = tk.PhotoImage(data=ppm_data).zoom(2, 2)
                    canvas.create_image(0, 0, anchor="nw", image=photo)
                    self._screen_preview_photo = photo
                except Exception as exc:
                    canvas.create_rectangle(0, 0, PW, PW, fill="#222", outline="")
                    canvas.create_text(PW // 2, PW // 2, text=f"Render error: {exc}", fill="#ff6666")

                # ESP boxes drawn ONLY when a valid 8x8 chessboard is confirmed
                if grid is not None and side in ("w", "b") and (conf or 0) > 0.40:
                    for iy in range(8):
                        for ix in range(8):
                            v = grid[iy][ix]
                            if v is None:
                                continue
                            dx = ix * CELL
                            dy = iy * CELL
                            if v == "w":
                                canvas.create_rectangle(
                                    dx + 3, dy + 3,
                                    dx + CELL - 3, dy + CELL - 3,
                                    fill="", outline="#cccccc", width=2)
                            elif v == "b":
                                canvas.create_rectangle(
                                    dx + 3, dy + 3,
                                    dx + CELL - 3, dy + CELL - 3,
                                    fill="", outline="#222222", width=2)

            # Status bar
            sy = PW
            canvas.create_rectangle(0, sy, PW, sy + STATUS_H,
                                    fill="#111", outline="")
            side_str = ("Bottom: WHITE" if side == "w" else
                        "Bottom: BLACK" if side == "b" else
                        "Bottom: detecting...")
            conf_str = (f"  conf={int((conf or 0) * 100)}%"
                        if side in ("w", "b") else "")
            locked   = getattr(self, "_screen_perspective_locked", False)
            lock_str = "  [LOCKED]" if locked else "  [unlocked]"
            canvas.create_text(PW // 2, sy + STATUS_H // 2,
                               text=side_str + conf_str + lock_str,
                               fill="#aaa", font=("Segoe UI", 8))

            self._screen_preview_after_id = self.root.after(160, self._screen_preview_refresh)
        except Exception:
            try:
                self._screen_preview_after_id = self.root.after(160, self._screen_preview_refresh)
            except Exception:
                pass


    def open_screen_preview(self):
        """Open the colour-detector preview window."""
        try:
            if getattr(self, "_screen_preview_win", None) is not None:
                try:
                    if self._screen_preview_win.winfo_exists():
                        self._screen_preview_win.lift()
                        return
                except Exception:
                    pass
            PW = 480
            STATUS_H = 22
            win = tk.Toplevel(self.root)
            win.title("Detector Preview")
            win.configure(bg="#111")
            win.attributes("-topmost", True)
            win.resizable(False, False)
            win.geometry(f"{PW}x{PW + STATUS_H}")
            canvas = tk.Canvas(win, width=PW, height=PW + STATUS_H,
                               bg="#111", highlightthickness=0)
            canvas.pack()
            self._screen_preview_win    = win
            self._screen_preview_canvas = canvas
            self._screen_preview_photo  = None   # holds PhotoImage ref
            self._preview_last_frame_id = None
            self._preview_last_side = None
            win.protocol("WM_DELETE_WINDOW", self.close_screen_preview)
            self._start_screen_bottom_detector()
            self._screen_preview_refresh()
        except Exception:
            pass

    def close_screen_preview(self):
        try:
            after_id = getattr(self, "_screen_preview_after_id", None)
            if after_id:
                try:
                    self.root.after_cancel(after_id)
                except tk.TclError:
                    pass
                self._screen_preview_after_id = None
            win = getattr(self, "_screen_preview_win", None)
            if win is not None:
                try:
                    win.destroy()
                except Exception:
                    pass
            self._screen_preview_win = None
        except Exception:
            self._screen_preview_win = None

    def _screen_bottom_result(self):
        """(color, age) for the latest confident visual reading, or
        (None, huge) when the detector has no fresh result."""
        with self._screen_lock:
            color = self._screen_bottom_color
            at = self._screen_bottom_at
        if color not in ("w", "b"):
            return None, float("inf")
        return color, time.time() - at

    def _apply_screen_perspective(self):
        """Background-thread safe: rotate the visualizer perspective to match
        the screen detector's reading, but ONLY at the start of a match.
        Max 2 color changes allowed per match.
        """
        try:
            if getattr(self, "_match_perspective_flip_count", 0) >= 2:
                return
            screen, age = self._screen_bottom_result()
            if screen not in ("w", "b") or age >= 12.0:
                return
            if self._screen_perspective_locked:
                return
            if not self._board_in_opening():
                return
            root = getattr(self, "root", None)
            if root is None:
                return
            root.after(0, lambda: self._set_perspective_from_screen(screen))
        except Exception:
            pass

    def _board_in_opening(self):
        """True during the early opening (fullmove <= 3 AND both home ranks still
        full).  The auto-detector reads the side from the board once during this
        window then locks.  A missing FEN counts as the opening so the very
        first read at app launch can orient the board."""
        fen = getattr(self, "current_fen", None)
        if not fen:
            return True
        try:
            parts = fen.split()
            placement = parts[0]
            ranks = placement.split("/")
            if len(ranks) != 8:
                return False
            # Must be within the first 3 full moves.
            try:
                fullmove = int(parts[5]) if len(parts) >= 6 else 1
            except (ValueError, IndexError):
                fullmove = 1
            if fullmove > 3:
                return False
            # Back ranks (ranks 1 and 8 in FEN) must still be fully occupied.
            for rank in (ranks[0], ranks[7]):
                count = 0
                for ch in rank:
                    count += int(ch) if ch.isdigit() else 1
                if count != 8:
                    return False
            return True
        except Exception:
            return False

    def _set_perspective_from_screen(self, side):
        """Apply a fallback opening detection without turning Auto into manual."""
        if (self.player_color != "auto" or side not in ("w", "b")
                or self._screen_perspective_locked or not self._board_in_opening()
                or getattr(self, "current_playing_as", None) in ("w", "b")):
            return
        previous = self._human_color()
        self.detected_player_color = side
        self.perspective = "white" if side == "w" else "black"
        self._screen_perspective_locked = True
        self._update_scores_for_new_perspective(previous)
        self._coord_cache.clear()
        self.draw_border()
        self.draw_grid()
        self.redraw_all_arrows()
        self.draw_evaluation_bar()

    def _set_status_threadsafe(self, text, color=None):
        """Show a message in the GUI's bottom status bar from any thread."""
        try:
            self.root.after(0, lambda: self._set_status(text, color))
        except Exception:
            pass

    def _calibrate_board(self):
        """No-extension fallback: let the user tell us where the board is.

        The user parks the cursor over the centre of a1, then h8 (each held
        for a couple of seconds while we snapshot GetCursorPos).  The board
        rectangle is derived from those two diagonal corners and saved, so the
        real mouse can drag pieces even if the extension's geometry reply
        never arrives.  Runs on its own thread — never blocks Tk.
        """
        def _run():
            try:
                user32 = ctypes.windll.user32
                self._set_status_threadsafe(
                    "CALIBRATE 1/2: place the mouse on the CENTRE of a1 "
                    "(bottom-left) and hold still — snapshot in 3s...", "#FBBF24")
                print("[AutoMove] CALIBRATE: move the mouse to the CENTRE of "
                      "the a1 square (bottom-left) and HOLD IT STILL — "
                      "snapshot in 3s...")
                time.sleep(3.0)
                p = _POINT()
                user32.GetCursorPos(ctypes.byref(p))
                a1 = (p.x, p.y)
                self._set_status_threadsafe(
                    f"a1 locked @({a1[0]},{a1[1]}).  CALIBRATE 2/2: place the "
                    "mouse on the CENTRE of h8 (top-right) and hold still — "
                    "snapshot in 3s...", "#FBBF24")
                print(f"[AutoMove] a1 @ ({a1[0]},{a1[1]}) — now move to the "
                      "CENTRE of h8 (top-right) and HOLD IT STILL — "
                      "snapshot in 3s...")
                time.sleep(3.0)
                user32.GetCursorPos(ctypes.byref(p))
                h8 = (p.x, p.y)
                dx, dy = h8[0] - a1[0], h8[1] - a1[1]
                sq = math.hypot(dx, dy) / 7.0
                if sq < 8:
                    self._set_status_threadsafe(
                        "Calibration too small — a1 and h8 must be opposite "
                        "board corners.  Try again.", self.RED)
                    print("[AutoMove] calibration too small — a1 and h8 must "
                          "be opposite board corners.  Cancelled.")
                    return
                flipped = a1[1] < h8[1]   # a1 above h8 => black at the bottom
                left = min(a1[0], h8[0]) - sq / 2.0
                top = min(a1[1], h8[1]) - sq / 2.0
                self._board_calibration = {
                    "left": left, "top": top, "size": 8 * sq,
                    "flipped": bool(flipped),
                }
                self._save_all_settings()
                self._set_status_threadsafe(
                    f"Board calibrated: {8 * sq:.0f}px — AutoMove ready.", self.GREEN)
                print(f"[AutoMove] calibrated board {8*sq:.0f}px "
                      f"@({left:.0f},{top:.0f}) flipped={flipped} — saved.")
            except Exception as exc:
                self._set_status_threadsafe(
                    f"Calibration error: {exc}", self.RED)
                print(f"[AutoMove] calibration error: {exc}")
        threading.Thread(target=_run, daemon=True).start()

    def _square_screen_coords(self, square, geom):
        """Absolute PHYSICAL screen coordinates of a square centre (a1..h8)."""
        if not isinstance(square, str) or len(square) < 2:
            return (0, 0)
        f = ord(square[0]) - 97
        r = int(square[1]) - 1
        if not (0 <= f < 8 and 0 <= r < 8):
            return (0, 0)
        scale = max(0.01, float(geom.get("scale") or 1))
        if scale == 1.0 and geom.get("ratio"):
            scale = float(geom.get("ratio"))
        w = max(1.0, float(geom.get("width") or 0))
        h = max(1.0, float(geom.get("height") or 0))
        sqw, sqh = w / 8.0, h / 8.0
        flipped = bool(geom.get("flipped"))
        cx = float(geom.get("left") or 0) + (7 - f if flipped else f) * sqw + sqw / 2
        cy = float(geom.get("top") or 0) + (r if flipped else 7 - r) * sqh + sqh / 2
        return (cx * scale, cy * scale)

    def _os_mouse_move(self, x, y):
        """Send absolute virtual-desktop input without relative-pointer acceleration."""
        user = ctypes.windll.user32
        left,top = user.GetSystemMetrics(76),user.GetSystemMetrics(77)
        width,height = user.GetSystemMetrics(78),user.GetSystemMetrics(79)
        if width <= 0 or height <= 0:
            raise OSError('Windows virtual-desktop geometry unavailable')
        px = max(0,min(width-1,int(round(x))-left))
        py = max(0,min(height-1,int(round(y))-top))
        # Target the center of a physical pixel's absolute-coordinate interval.
        dx = min(65535,int((px+.5)*65536/width))
        dy = min(65535,int((py+.5)*65536/height))
        event = _INPUT(0,_MOUSEINPUT(dx,dy,0,0xC001,0,0))
        if user.SendInput(1,ctypes.byref(event),ctypes.sizeof(event)) != 1:
            raise OSError('Windows rejected absolute cursor input')

    def _os_mouse_button(self, flags):
        """Press/release a mouse button using Win32 mouse_event."""
        try:
            ctypes.windll.user32.mouse_event(flags, 0, 0, 0, 0)
        except Exception as exc:
            print(f"[AutoMove] button(0x{flags:x}) failed: {exc}")
            raise

    def _glide(self, waypoints, min_step_s=0.001):
        """Move the OS cursor smoothly through ``waypoints``.
        Dynamically scales pixel step size so drag speed slider settings scale cleanly up to fast speeds.
        """
        if not waypoints:
            return
        x1, y1 = float(waypoints[0][0]), float(waypoints[0][1])
        self._os_mouse_move(x1, y1)
        for (x2, y2, seg_ms) in waypoints[1:]:
            x2, y2 = float(x2), float(y2)
            dist = math.hypot(x2 - x1, y2 - y1)
            if dist < 0.5:
                x1, y1 = x2, y2
                continue
            seg_s = max(0.001, float(seg_ms or 0.0) / 1000.0)
            # Step px size scales with segment duration: shorter seg_s = larger
            # px steps per iteration.  Jittered so OS event spacing is never
            # metronomically regular.
            step_px = 3.0 * (0.040 / seg_s) if seg_s > 0 else 3.0
            step_px = max(1.2, min(26.0, step_px * random.uniform(0.5, 1.8)))
            n = max(1, int(round(dist / step_px)))
            step_s = max(min_step_s, seg_s / n)
            for i in range(1, n + 1):
                t = i / n
                self._os_mouse_move(x1 + (x2 - x1) * t,
                                    y1 + (y2 - y1) * t)
                if step_s > 0.0015:
                    time.sleep(step_s * random.uniform(0.8, 1.3))
            x1, y1 = x2, y2

    def _promotion_target(self, piece, timeout=.4):
        pending = {'id': secrets.token_hex(12), 'event': threading.Event(), 'response': None}
        self._promotion_pending = pending
        try:
            self.send_extension_command({'action':'get_promotion_geometry', 'piece':piece,
                                         'request_id':pending['id']})
            pending['event'].wait(timeout)
            reply = pending['response']
            if not reply or not reply.get('ok') or reply.get('piece') != piece:
                return None
            values = [float(reply[k]) for k in ('x','y','width','height')]
            x,y,w,h = values
            if not all(math.isfinite(v) for v in values) or not (-1 <= x <= 2 and -1 <= y <= 2 and 0 < w <= 1 and 0 < h <= 1):
                return None
            return x,y,w,h
        finally:
            if getattr(self, '_promotion_pending', None) is pending:
                self._promotion_pending = None

    def _accept_promotion_geometry(self, data):
        pending = getattr(self, '_promotion_pending', None)
        if pending and data.get('request_id') == pending['id']:
            pending['response'] = data
            pending['event'].set()

    def _native_promotion(self, piece, epoch, require_enabled=True):
        def valid():
            return (getattr(self, '_automove_epoch', None) == epoch
                    and (not require_enabled or self.automove.enabled)
                    and not getattr(self, '_automove_game_over', False))
        deadline = time.monotonic()+3
        while time.monotonic() < deadline and valid():
            target = self._promotion_target(piece)
            if target is None:
                time.sleep(.05)
                continue
            geom = self._get_board_geometry(wait_ms=500)
            if not geom or not geom.get('ok') or not valid():
                return False
            scale = float(geom.get('scale') or geom.get('ratio') or 1)
            if scale == 1 and geom.get('ratio'):
                scale = float(geom['ratio'])
            w,h = float(geom['width'])*scale,float(geom['height'])*scale
            x,y = (float(geom['left'])+target[0]*float(geom['width']))*scale,(float(geom['top'])+target[1]*float(geom['height']))*scale
            cursor = _POINT()
            if not ctypes.windll.user32.GetCursorPos(ctypes.byref(cursor)):
                return False
            plan = sigmadrift_generate_plan(cursor.x,cursor.y,x,y,target_width=min(target[2]*w,target[3]*h))
            self._glide_sigmadrift(plan, valid)
            # Recheck visibility and position after moving; never click a vanished chooser.
            fresh = self._promotion_target(piece)
            if fresh is None or any(abs(a-b)>0.005 for a,b in zip(target,fresh)) or not valid():
                continue
            current_geom = self._get_board_geometry(wait_ms=500)
            if not current_geom or any(current_geom.get(k) != geom.get(k) for k in ('left','top','width','height','scale','ratio')):
                continue
            time.sleep(random.uniform(.035,.09))
            if not valid():
                return False
            self._os_mouse_button(2)
            try:
                time.sleep(random.uniform(.04,.09))
            finally:
                self._os_mouse_button(4)
            return True
        print('[AutoMove] promotion chooser unavailable; no click sent')
        return False

    def _follow_mouse_path(self, points, duration, valid=lambda: True):
        start = time.perf_counter()
        samples = trajectory(points, duration)
        for i, (elapsed, x, y) in enumerate(samples):
            if not valid():
                raise InterruptedError("mouse action cancelled")
            now = time.perf_counter()
            if 0 < i < len(samples)-1 and start + elapsed < now:
                continue  # Do not replay missed samples in a burst after a scheduler stall.
            time.sleep(max(0, start + elapsed - now))
            self._os_mouse_move(x, y)

    def _real_mouse_drag(self, uci, plan, geom):
        """Execute one bounded drag; speed scales travel, not button dwell."""
        if not isinstance(uci, str) or len(uci) < 4 or not plan:
            return
        start = self._square_screen_coords(uci[:2], geom)
        end = self._square_screen_coords(uci[2:4], geom)
        scale = float(geom.get('scale') or geom.get('ratio') or 1)
        if scale == 1 and geom.get('ratio'):
            scale = float(geom['ratio'])
        w,h = float(geom['width'])*scale,float(geom['height'])*scale
        sw,sh = w/8,h/8
        speed = max(.1, float(getattr(self.automove, 'path_speed', 1) or 1))
        raw = list(plan_screen_points(plan, start, end, sw, sh))
        grip = next((i for i,p in enumerate(raw) if not p[3]), None)
        release = next((i for i,p in enumerate(raw) if p[4]), None)
        if grip is None or release is None or release <= grip:
            raise ValueError("mouse plan has no complete drag")
        left,top = float(geom.get('left') or 0)*scale,float(geom.get('top') or 0)*scale
        points = [(max(left,min(left+w-1,p[0])),max(top,min(top+h-1,p[1])),p[2]) for p in raw[grip:release+1]]
        for index,center in ((0,start),(-1,end)):
            x,y,d=points[index]
            points[index]=(max(center[0]-.45*sw,min(center[0]+.45*sw,x)),max(center[1]-.45*sh,min(center[1]+.45*sh,y)),d)
        epoch = getattr(self, '_automove_epoch', None)
        was_enabled = getattr(self.automove, 'enabled', False)
        def valid():
            return (getattr(self, '_automove_epoch', None) == epoch
                    and (not was_enabled or self.automove.enabled)
                    and not getattr(self, '_automove_game_over', False))
        cursor = _POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(cursor)):
            approach = sigmadrift_generate_plan(cursor.x,cursor.y,points[0][0],points[0][1],speed_mult=speed,target_width=min(sw,sh))
            self._glide_sigmadrift(approach, valid)
        else:
            raise OSError('Windows cursor position unavailable')
        if not valid():
            return
        time.sleep(random.uniform(.035,.10))
        if not valid():
            return
        self._os_mouse_button(0x0002)
        try:
            time.sleep(random.uniform(.025,.065))
            distance = math.hypot(end[0]-start[0],end[1]-start[1])
            duration = travel_seconds(distance,min(sw,sh),sum(p[2] for p in points[1:])/1000/speed)
            self._follow_mouse_path(points,duration,valid)
            time.sleep(random.uniform(.020,.070))
        finally:
            self._os_mouse_button(0x0004)
        # Finish on the drop point. Do not manufacture a post-release drift.

    def _glide_sigmadrift(self, plan, valid=lambda: True):
        if not plan:
            return
        points = [(p['x'],p['y'],p.get('d',0)) for p in plan]
        self._follow_mouse_path(points,max(.001,sum(p[2] for p in points[1:])/1000),valid)

    def _automove_test_drag(self):
        """Two-stage diagnostic for real mouse control.

        1) Pure OS cursor test: wiggle the REAL cursor between fixed screen
           points WITHOUT the extension — proves SetCursorPos works at all.
        2) If board geometry comes back from the extension, walk a1 -> h8.
        """
        def _run():
            try:
                # Stage 1: raw OS cursor control (no extension needed).
                user32 = ctypes.windll.user32
                sm_x = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
                sm_y = user32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
                sm_w = user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
                sm_h = user32.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
                print(f"[AutoMove] virtual screen {sm_w}x{sm_h} "
                      f"@({sm_x},{sm_y}) — moving cursor...")
                pts = [(sm_x + sm_w // 2, sm_y + sm_h // 2),
                       (sm_x + sm_w // 4, sm_y + sm_h // 4),
                       (sm_x + sm_w * 3 // 4, sm_y + sm_h // 2),
                       (sm_x + sm_w // 4, sm_y + sm_h * 3 // 4),
                       (sm_x + sm_w // 2, sm_y + sm_h // 2)]
                for px, py in pts:
                    ok = user32.SetCursorPos(int(px), int(py))
                    print(f"[AutoMove] SetCursorPos({px},{py}) -> {bool(ok)}")
                    time.sleep(0.35)
                print("[AutoMove] stage 1 done — cursor should have moved")

                # Stage 2: board walk if the extension can report geometry.
                geom = self._get_board_geometry(wait_ms=4000)
                if not (geom and geom.get("ok") and geom.get("width")):
                    print("[AutoMove] stage 2: NO board geometry — the "
                          "extension didn't answer.  Is a chess.com tab open "
                          "and reporting FEN?")
                    return
                fx, fy = self._square_screen_coords("a1", geom)
                tx, ty = self._square_screen_coords("h8", geom)
                print(f"[AutoMove] stage 2: a1({fx:.0f},{fy:.0f}) -> "
                      f"h8({tx:.0f},{ty:.0f})")
                # Human-ish diagonal: slow start, fast middle, slow finish.
                speed = max(0.1, float(
                    getattr(self.automove, "path_speed", 1.0) or 1.0))
                waypoints = []
                for i in range(1, 6):
                    t = i / 5.0
                    seg = [260, 120, 90, 160, 320][i - 1] / speed
                    waypoints.append((fx + (tx - fx) * t,
                                      fy + (ty - fy) * t,
                                      seg))
                self._glide(waypoints,
                            min_step_s=max(0.0008, 0.0025 / speed))
                print("[AutoMove] stage 2 done")
            except Exception as exc:
                print(f"[AutoMove] test drag error: {exc}")
        threading.Thread(target=_run, daemon=True).start()

    def _send_automove(self, uci, think_ms, plan):
        """Send an execute_move command that carries the full human-like mouse
        plan.  The extension waits `thinking_delay_ms`, then replays
        mousedown -> mousemove* -> mouseup along the plan's waypoints."""
        if getattr(self, "hotkeys_locked", False):
            return
        try:
            self.send_extension_command({
                "action": "execute_move",
                "uci": uci,
                "auto_move": True,
                "thinking_delay_ms": int(max(0, think_ms)),
                "plan": plan if isinstance(plan, list) else [],
            })
            print(f"[AutoMove] playing {uci} after {int(think_ms)} ms think "
                  f"({len(plan) if isinstance(plan, list) else 0} pointer events)")
        except Exception as exc:
            print(f"[AutoMove] send error: {exc}")

    def square_to_screen_coords(self, square):
        """Convert chess square to screen coordinates - OPTIMIZED with caching"""
        # OPTIMIZATION: Use cache for coordinate calculations
        cache_key = f"{square}_{self.perspective}"
        if cache_key in self._coord_cache:
            return self._coord_cache[cache_key]

        try:
            # Validate square format
            if not isinstance(square, str) or len(square) < 2:
                return (0, 0)

            col = ord(square[0]) - ord('a')
            row = 8 - int(square[1])

            # Bounds check
            if not (0 <= col < 8 and 0 <= row < 8):
                return (0, 0)

            col, row = self.convert_coordinates(col, row)

            # Account for border offset
            board_width = self.canvas.winfo_width() - 6  # 3px border on each side
            board_height = self.canvas.winfo_height() - 6

            x = 3 + (col + 0.5) * board_width / 8  # 3px left border
            y = 3 + (row + 0.5) * board_height / 8  # 3px top border

            result = (int(self.canvas.winfo_rootx() + x), int(self.canvas.winfo_rooty() + y))
            if len(self._coord_cache) > 128:
                self._coord_cache.clear()
            self._coord_cache[cache_key] = result
            return result
        except (ValueError, IndexError, AttributeError):
            return (0, 0)

    def is_polyglot_engine(self, path):
        return "polyglot" in os.path.basename(path).lower() or path.lower().endswith(".ini")

    def find_polyglot_ini(self, path):
        engine_dir = os.path.dirname(path)
        name = os.path.splitext(os.path.basename(path))[0]

        for ini in [f"{name}.ini", "polyglot.ini"]:
            full_path = os.path.join(engine_dir, ini)
            if os.path.exists(full_path):
                return full_path
        return None

    def start_polyglot_engine(self, engine, path):
        try:
            if path.lower().endswith('.ini'):
                engine_dir = os.path.dirname(path)
                polyglot_exe = os.path.join(engine_dir, "polyglot.exe")
                if not os.path.exists(polyglot_exe):
                    for f in os.listdir(engine_dir):
                        if "polyglot" in f.lower() and f.endswith(".exe"):
                            polyglot_exe = os.path.join(engine_dir, f)
                            break
                
                if os.path.exists(polyglot_exe):
                    # MODIFY THIS LINE:
                    engine["process"] = subprocess.Popen(
                        [polyglot_exe, path], 
                        stdin=subprocess.PIPE, 
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, 
                        text=True, 
                        bufsize=1,  # line-buffered text UCI protocol
                        cwd=engine_dir,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0  # ADDED
                    )
                    return True
            else:
                ini = self.find_polyglot_ini(path)
                cmd = [path, ini] if ini else [path]
                engine_dir = os.path.dirname(path)  # ADDED
                # MODIFY THIS LINE:
                engine["process"] = subprocess.Popen(
                    cmd, 
                    stdin=subprocess.PIPE, 
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, 
                    text=True, 
                    bufsize=1,  # line-buffered text UCI protocol
                    cwd=engine_dir,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0  # ADDED
                )
                return True
        except Exception as e:
            print(f"Polyglot error: {e}")
            return False

    def flip_to_white(self):
        # ADDED: Check if hotkeys are locked
        if self.hotkeys_locked:
            return
        self.root.after(0, lambda: self.set_perspective("white"))

    def flip_to_black(self):
        # ADDED: Check if hotkeys are locked
        if self.hotkeys_locked:
            return
        self.root.after(0, lambda: self.set_perspective("black"))

    def _update_scores_for_new_perspective(self, previous_color=None):
        us = self._human_color()
        previous = previous_color or getattr(self, "_score_display_color", us)
        self._score_display_color = us
        if previous == us:
            return
        if hasattr(self, "_position_cache"):
            self._position_cache = PositionCache(max_size=256, ttl_seconds=300)
        self._eval_bar_last_draw = 0.0
        def invert(label):
            value = str(label)
            if value.upper().startswith("FM"):
                return "EM" + value[2:]
            if value.upper().startswith("EM"):
                return "FM" + value[2:]
            try:
                return f"{-float(value):+.2f}"
            except (TypeError, ValueError):
                return label
        for attr in ("multi_pvs", "committed_multi_pvs"):
            source = getattr(self, attr, {})
            setattr(self, attr, {rank: (data[0], invert(data[1])) if data and len(data) >= 2 else data
                                for rank, data in source.items()})
        # Isolated engine dictionaries use the same display labels.
        for engine in getattr(self, "engines", []):
            for attr in ("multi_pvs", "committed_multi_pvs"):
                source = engine.get(attr, {})
                engine[attr] = {rank: (data[0], invert(data[1])) if data and len(data) >= 2 else data
                                for rank, data in source.items()}
            for batch in engine.get("_pv_batches", {}).values():
                for rank, data in list(batch.items()):
                    batch[rank] = (data[0], invert(data[1]))
            if engine.get("score") is not None:
                engine["score"] = invert(engine["score"])
        if getattr(self, "current_is_mate", False) and self.current_mate_label:
            self.current_mate_label = invert(self.current_mate_label)


    def set_perspective(self, perspective):
        if perspective not in ("white", "black"):
            return
        previous_color = self._human_color()
        self.perspective = perspective
        self.player_color = "w" if perspective == "white" else "b"
        # Async save to avoid freezing the UI thread when flipping perspective
        self._save_all_settings()
        # Keep the control panel's "Your side" radios in sync so a hotkey flip
        # visibly shows which side you are on.
        if hasattr(self, "control_panel") and hasattr(self.control_panel, "player_color_var"):
            self.control_panel.player_color_var.set(self.player_color)
        self._coord_cache.clear()

        # If we already have analysis for the current position or the engine is currently searching it,
        # DO NOT wipe suggestions or restart the engine! The chess position is unchanged;
        # only the visual perspective flipped.
        has_existing_analysis = bool(self.committed_multi_pvs or self.multi_pvs or self.pv_line)
        is_searching_current = False
        if getattr(self, "current_fen", None):
            for eng in getattr(self, "engines", []):
                if (eng.get("current_search_fen") or eng.get("search_fen") or eng.get("active_fen")) == self.current_fen:
                    is_searching_current = True
                    break

        if self.current_fen and (not has_existing_analysis and not is_searching_current):
            self.analyse_position(self.current_fen, self.engine_elo)
        else:
            self._update_scores_for_new_perspective(previous_color)

        self.draw_border()  # Redraw border
        self.draw_grid()
        self.redraw_all_arrows()
        self.draw_evaluation_bar()
        self.bring_to_front()

    def bring_to_front(self):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            ctypes.windll.user32.ShowWindow(hwnd, 9)
            ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)
        except:
            pass
        self.root.lift()
        self._sync_arrow_layer()

    def setup_canvas(self):
        self.main_frame = tk.Frame(self.root)
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        # In-window drag handle.  The native title bar of this -toolwindow
        # layered window is unreliable to drag on Windows (a click looks like a
        # drag start, then the window just unfocuses), so the window is moved
        # with a custom opaque strip instead.  It is a dedicated top row, so
        # board clicks never touch it and playing a move never moves the window.
        self._build_title_bar()

        self.eval_canvas = tk.Canvas(self.main_frame, width=30, bg="#211735", highlightthickness=0, bd=0)
        self.eval_canvas.pack(side=tk.LEFT, fill=tk.Y)

        # The eval bar is a reliable opaque surface: it always receives clicks
        # (unlike the transparent board).  Pressing it lifts + focuses the
        # window (so the tooltip works and there is no focus-unfocus flicker),
        # but it must NOT drag the window: board-click drags kept moving the
        # whole overlay while playing moves.  Drag only via _build_title_bar().
        self.eval_canvas.bind("<ButtonPress-1>", lambda _e: (self.root.lift(), self.root.focus_force()), add="+")

        # Only this key-colour background is transparent.  Text and panels use
        # ordinary opaque colours so Windows never applies the key to glyph pixels.
        # No cursor="none": the mouse must stay visible over the board and the
        # suggestion label bars (click-through stealth is no longer desired).
        self.canvas = tk.Canvas(self.main_frame, bg=TRANSPARENT_KEY, highlightthickness=0, bd=0, insertwidth=0, selectborderwidth=0)
        self.canvas.pack(side=tk.RIGHT, expand=True, fill=tk.BOTH)

        # Docked 'Chess Coach' chat panel to the right of the board.
        self._build_coach_panel()

        # Keep normal Tk bindings intact. Removing all button bindings can make
        # this topmost window appear frozen and interferes with normal dragging.

        # An opaque grip remains clickable even where the transparent board background
        # would otherwise make Windows appear to pass a second click to the desktop.
        self.resize_grip = self.canvas.create_rectangle(0, 0, 0, 0, fill="#5E4B7A", outline="#E9D5FF", tags="resize_grip")
        self.canvas.tag_bind("resize_grip", "<ButtonPress-1>", self._begin_visualizer_resize)
        self.canvas.tag_bind("resize_grip", "<B1-Motion>", self._drag_visualizer_resize)
        self.canvas.bind("<Button-1>", lambda _e: (self.root.lift(), self.root.focus_force()), add="+")
        self.draw_border()  # Draw the visible border first
        self.draw_grid()
        self.root.bind("<Configure>", self.on_resize)

        # Arrows are drawn on a separate click-through layered window so they
        # never swallow clicks aimed at the browser pieces underneath.
        self._build_arrow_layer()
        self.root.bind("<Map>", self._on_overlay_visible, add="+")
        self.root.bind("<FocusIn>", self._on_overlay_visible, add="+")

        # The toplevel owns transparency.  Do not add a second layered/color-key
        # pass to the Canvas HWND: doing so causes distorted or partially erased text.

    @property
    def arrow_canvas(self):
        """The click-through canvas arrows are drawn on, falling back to the
        main board canvas if the arrow layer was unavailable."""
        return getattr(self, "_arrow_cv", None) or self.canvas

    def _build_arrow_layer(self):
        """Create a click-through layered window that draws the move arrows.

        The main overlay passes clicks through its key-coloured board, but the
        opaque arrow pixels would otherwise swallow clicks aimed at the browser
        pieces underneath.  Arrows are therefore drawn on a separate layered
        window marked WS_EX_TRANSPARENT so every click passes straight through
        to the site while the arrows stay visible.
        """
        self._arrow_layer_ready = False
        self._arrow_capture_state = None
        try:
            layer = tk.Toplevel(self.root)
            layer.withdraw()  # establish capture policy before first display
            layer.overrideredirect(True)
            layer.attributes("-topmost", True)
            layer.attributes("-transparentcolor", TRANSPARENT_KEY)
            layer.configure(bg=TRANSPARENT_KEY)
            self._arrow_cv = tk.Canvas(layer, bg=TRANSPARENT_KEY,
                                       highlightthickness=0, bd=0,
                                       insertwidth=0, selectborderwidth=0)
            self._arrow_cv.pack(fill=tk.BOTH, expand=True)
            self.arrow_layer = layer
            self._arrow_layer_ready = True
            layer.bind("<Map>", self._on_arrow_layer_mapped, add="+")
            self._apply_arrow_layer_capture_protection(force=True)
        except Exception as e:
            print(f"[Arrow Layer Error]: {e}")
            self._arrow_layer_ready = False
            self._arrow_cv = None

    def _apply_arrow_layer_capture_protection(self, force=False):
        """Give the separate arrow window the visualizer's capture policy."""
        if not getattr(self, "_arrow_layer_ready", False) or user32 is None:
            return
        try:
            layer = self.arrow_layer
            if not layer.winfo_exists():
                return
            state = (layer.winfo_id(), bool(self.stealth_mode_enabled))
            if not force and getattr(self, "_arrow_capture_state", None) == state:
                return
            # The shared helper resolves Tk's HWND to its native top-level.
            if hide_from_screen_capture(*state):
                self._arrow_capture_state = state
        except tk.TclError:
            pass  # The layer may have been destroyed during shutdown/rebuild.

    def _on_arrow_layer_mapped(self, event):
        if event.widget is self.arrow_layer:
            self._apply_arrow_layer_click_through()
            self._apply_arrow_layer_capture_protection(force=True)

    def _apply_arrow_layer_click_through(self):
        """Mark the arrow window click-through (and focus-stealing proof).

        Tk re-applies its own extended styles whenever the window is mapped,
        moved or lifted, wiping a one-shot SetWindowLongW call.  It therefore
        has to be re-applied at the very end of every geometry sync so the
        flags stay set on the live, visible window.
        """
        if not getattr(self, "_arrow_layer_ready", False) or user32 is None:
            return
        try:
            layer = self.arrow_layer
            hwnd = user32.GetParent(layer.winfo_id())
            if hwnd == 0:
                hwnd = layer.winfo_id()
            exstyle = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            exstyle |= (WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST
                        | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
            exstyle &= ~WS_EX_APPWINDOW
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, exstyle)
        except Exception:
            pass

    def _on_overlay_visible(self, event=None):
        if getattr(self, "_overlay_recover_id", None) is None:
            self._overlay_recover_id = self.root.after(60, self._recover_overlay)

    def _recover_overlay(self):
        self._overlay_recover_id = None
        if not self.should_run or not self.root.winfo_exists():
            return
        self._coord_cache.clear()
        self._eval_bar_last_draw = 0.0
        if (sys.platform == "win32" and
                (not getattr(self, "_arrow_layer_ready", False)
                 or not self.arrow_layer.winfo_exists())):
            self._build_arrow_layer()
        self._sync_arrow_layer()
        self.draw_grid()
        self.redraw_all_arrows()
        self.draw_evaluation_bar()

    def _sync_arrow_layer(self):
        """Align the click-through arrow layer over the board canvas and show it."""
        if not getattr(self, "_arrow_layer_ready", False):
            return
        try:
            w = self.canvas.winfo_width()
            h = self.canvas.winfo_height()
            if w < 4 or h < 4:
                return
            x = self.canvas.winfo_rootx()
            y = self.canvas.winfo_rooty()
            self.arrow_layer.geometry(f"{w}x{h}+{x}+{y}")
            self._apply_arrow_layer_capture_protection()
            self.arrow_layer.deiconify()
            self.arrow_layer.lift()
            self._apply_arrow_layer_click_through()
        except Exception:
            pass

    def _hide_arrow_layer(self):
        if getattr(self, "_arrow_layer_ready", False):
            try:
                self.arrow_layer.withdraw()
            except Exception:
                pass

    def _build_title_bar(self):
        """Minimal drag handle for the visualizer.

        The native title bar of this -toolwindow layered window is unreliable
        to drag on Windows, so the window moves via a slim opaque strip above
        the board.  No brand text or dots — just a soft rounded grip pill so it
        stays clean while remaining easy to grab.
        """
        bar = tk.Canvas(self.main_frame, height=14, bg="#0D0A18",
                        highlightthickness=0, bd=0)
        bar.pack(side=tk.TOP, fill=tk.X)
        # Soft rounded grip pill, centred and subtle.
        def _draw_grip():
            try:
                w = bar.winfo_width()
                if w < 10:
                    return
                pill_w, pill_h = 56, 5
                x0 = (w - pill_w) // 2
                y0 = (14 - pill_h) // 2
                bar.delete("grip")
                bar.create_oval(x0, y0, x0 + pill_w, y0 + pill_h,
                                fill="#2A2140", outline="#2A2140", tags="grip")
            except Exception:
                pass
        bar.bind("<Configure>", lambda _e: _draw_grip())
        _draw_grip()
        bar.bind("<ButtonPress-1>", self._begin_visualizer_drag)
        bar.bind("<B1-Motion>", self._move_visualizer_drag)
        bar.bind("<ButtonRelease-1>", self._end_visualizer_drag)
        self._title_bar = bar

    def _begin_visualizer_drag(self, event):
        self._drag_origin = (event.x_root, event.y_root,
                             self.root.winfo_x(), self.root.winfo_y())
        self.root.lift()
        self.root.focus_force()
        self._drag_in_progress = True
        return "break"

    def _move_visualizer_drag(self, event):
        if not hasattr(self, "_drag_origin"):
            return "break"
        x0, y0, win_x, win_y = self._drag_origin
        self.root.geometry(f"+{win_x + event.x_root - x0}+{win_y + event.y_root - y0}")
        self._sync_arrow_layer()
        return "break"

    def _end_visualizer_drag(self, event):
        if hasattr(self, "_drag_origin"):
            del self._drag_origin
        if getattr(self, "_drag_in_progress", False):
            self._drag_in_progress = False
            # Redraw once at the final resting position (redraws were skipped
            # during the drag so the window follows the mouse smoothly).
            self._coord_cache.clear()
            self.draw_border()
            self.draw_grid()
            self.redraw_all_arrows()
            self.draw_evaluation_bar()
        return "break"

    def _begin_visualizer_resize(self, event):
        self._resize_origin = (event.x_root, event.y_root, self.root.winfo_width(), self.root.winfo_height())
        return "break"

    def _drag_visualizer_resize(self, event):
        if not hasattr(self, "_resize_origin"):
            return "break"
        x0, y0, width, height = self._resize_origin
        self.root.geometry(f"{max(320, width + event.x_root - x0)}x{max(320, height + event.y_root - y0)}")
        return "break"

    def draw_border(self):
        """Draw white-looking border using off-white color on canvas - OPTIMIZED"""
        self.canvas.delete("border")
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()

        # OPTIMIZATION: Only draw if we have valid dimensions
        if width <= 1 or height <= 1:
            return
        if hasattr(self, "resize_grip"):
            self.canvas.coords(self.resize_grip, width - 18, height - 18, width - 3, height - 3)
            self.canvas.tag_raise("resize_grip")

        border_width = 3
        # Thin neutral outline: it should frame the board, not dominate it.
        border_color = "#000000"

        # Create all border rectangles in one operation
        borders = [
            (0, 0, width, border_width),  # Top
            (0, height - border_width, width, height),  # Bottom  
            (0, 0, border_width, height),  # Left
            (width - border_width, 0, width, height)  # Right
        ]

        for coords in borders:
            self.canvas.create_rectangle(*coords, fill=border_color, outline="", tags="border")

    def draw_grid(self):
        """Draw grid with gray lines inside the border area - OPTIMIZED"""
        self.canvas.delete("grid")
        if not getattr(self, "grid_visible", True):
            return
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()

        # OPTIMIZATION: Only draw if we have valid dimensions
        if width <= 20 or height <= 20:
            return

        # Account for the thin border on all sides
        border = 3
        inner_width = width - (2 * border)
        inner_height = height - (2 * border)

        col_width = inner_width / 8
        row_height = inner_height / 8

        # OPTIMIZATION: Batch create lines
        lines = []
        for i in range(9):
            x = border + i * col_width
            y = border + i * row_height
            lines.extend([
                (x, border, x, height - border),  # Vertical
                (border, y, width - border, y)    # Horizontal
            ])

        for x1, y1, x2, y2 in lines:
            self.canvas.create_line(x1, y1, x2, y2, tag="grid", fill="#404040", width=1)
        self._draw_corner_markers()

    def _draw_corner_markers(self):
        """Outline a1 and h8 with a thin green border so the user can instantly
        verify the visualizer still lines up with the chess.com board, WITHOUT
        covering the pieces.  Uses the exact same mapping AutoMove's geometry
        uses, so the green frame shows where the tool THINKS a1/h8 are."""
        try:
            c = self.canvas
            c.delete("corner_markers")
            w = c.winfo_width()
            h = c.winfo_height()
            if w <= 20 or h <= 20:
                return
            border = 3
            iw, ih = w - 2 * border, h - 2 * border

            def sq_rect(square):
                f = ord(square[0]) - 97
                r = int(square[1]) - 1
                col, row = self.convert_coordinates(f, r)
                x0 = border + col * iw / 8
                y0 = border + row * ih / 8
                x1 = x0 + iw / 8
                y1 = y0 + ih / 8
                return (x0, y0, x1, y1)

            for sq in ("a1", "h8"):
                c.create_rectangle(*sq_rect(sq), fill="",
                                   outline="#22C55E", width=2,
                                   tags="corner_markers")
            c.tag_lower("corner_markers")
        except Exception:
            pass

    def _eval_tooltip_content(self):
        score = float(getattr(self, "current_score", 0.0) or 0.0)
        if not math.isfinite(score):
            score = 0.0
        value = score if self._human_color() == "w" else -score
        mate = getattr(self, "current_mate_label", None)
        if getattr(self, "current_is_mate", False) and mate:
            value = 1.0 if str(mate).upper().startswith("FM") else -1.0
            label = str(mate)
        else:
            label = f"{value:+.2f}" if value else "0.00"
        status = "Your advantage" if value > 0 else ("Opponent advantage" if value < 0 else "Equal")
        color = "#10B981" if value > 0.1 else ("#EF4444" if value < -0.1 else "#374151")
        return f"{label}  ({status})", color

    def _update_eval_tooltip(self):
        try:
            text, color = self._eval_tooltip_content()
            self._eval_tip_label.config(text=text, bg=color)
        except (AttributeError, tk.TclError):
            pass

    def _draw_pending_evaluation(self):
        self._eval_bar_after = None
        if self.should_run and self.root.winfo_exists():
            self.draw_evaluation_bar()

    def draw_evaluation_bar(self):
        """Draw eval bar: White's portion fills from BOTTOM, Black's from TOP.
        Score is always from White's perspective regardless of who is to move.
        Positive score → White is better; negative → Black (opponent) is better.
        """
        # Time-based debounce: skip if called too recently (prevents rapid flickering
        # from the engine sending info lines at every depth, without creating a mismatch
        # between the bar fill, the score text, and the live PV/mate data).
        _now = time.monotonic()
        _last = getattr(self, "_eval_bar_last_draw", 0.0)
        if _now - _last < 0.18:   # 180 ms debounce, with a trailing refresh
            if getattr(self, "_eval_bar_after", None) is None:
                delay = max(1, math.ceil((0.18 - (_now - _last)) * 1000))
                self._eval_bar_after = self.root.after(delay, self._draw_pending_evaluation)
            return
        if getattr(self, "_eval_bar_after", None) is not None:
            self.root.after_cancel(self._eval_bar_after)
            self._eval_bar_after = None
        self._eval_bar_last_draw = _now


        self.eval_canvas.delete("eval")
        width = self.eval_canvas.winfo_width()
        height = self.eval_canvas.winfo_height()
        if width <= 1 or height <= 1:
            return

        # Always use the live score so the bar fill and text colour always agree.
        score = float(getattr(self, "current_score", 0.0) or 0.0)
        if not math.isfinite(score):
            score = 0.0
        white_ratio = 0.5 + math.atan(score / 3.0) / math.pi          # 0.0 → full black, 1.0 → full white
        white_height = white_ratio * height           # pixels from bottom for white
        black_height = height - white_height          # pixels from top for black

        # Black portion (top)
        self.eval_canvas.create_rectangle(0, 0, width, black_height,
                                          fill="#1a1a1a", outline="", tags="eval")
        # White portion (bottom)
        self.eval_canvas.create_rectangle(0, black_height, width, height,
                                          fill="#E8E8E8", outline="", tags="eval")
        # Divider line
        self.eval_canvas.create_line(0, black_height, width, black_height,
                                     fill="#9333EA", width=1, tags="eval")

        # --- Everything from here on: HUMAN POV ---
        # (positive = good for YOU, negative = good for OPPONENT, regardless of
        #  which colour you're playing as. This guarantees the label + colour
        #  agree: green = you have advantage, red = opponent has advantage.)
        us = self._human_color()
        human_score = score if us == "w" else -score

        # Score label in center of bar.
        # Only show FM / EM if there is an actual unavoidable forced checkmate sequence.
        # High numeric advantage (+10.0, +15.0, etc.) displays strictly as numerical CP (+10.00, +15.00).
        is_mate = bool(getattr(self, "current_is_mate", False))
        if not is_mate:
            top_sugg = self.multi_pvs.get(1) or self.committed_multi_pvs.get(1)
            if top_sugg and isinstance(top_sugg[1], str):
                sc_up = top_sugg[1].upper()
                if sc_up.startswith("FM") or sc_up.startswith("EM"):
                    is_mate = True

        if is_mate:
            mating_color = "w" if score > 0 else "b"
            prefix = "FM" if mating_color == us else "EM"
            mate_lbl = getattr(self, "current_mate_label", None)
            if mate_lbl and (mate_lbl.upper().startswith("FM") or mate_lbl.upper().startswith("EM")):
                lbl_text = mate_lbl
            else:
                top_sugg = self.multi_pvs.get(1) or self.committed_multi_pvs.get(1)
                lbl_text = top_sugg[1] if top_sugg and isinstance(top_sugg[1], str) else prefix

            # sign: + means we mate; - means enemy mates us
            if mating_color == us:
                label = f"+{lbl_text}" if not lbl_text.startswith("+") else lbl_text
                colour_hint = +1
            else:
                label = f"-{lbl_text}" if not lbl_text.startswith("-") else lbl_text
                colour_hint = -1
        else:
            if abs(human_score) < 0.005:
                label = "0.00"
            else:
                label = f"{human_score:+.2f}"
            if abs(human_score) < 0.1:
                colour_hint = 0
            elif human_score > 0:
                colour_hint = +1
            else:
                colour_hint = -1

        if colour_hint > 0:
            label_color = "#10B981"     # GREEN — you're ahead
        elif colour_hint < 0:
            label_color = "#EF4444"     # RED — opponent ahead
        else:
            label_color = "#9CA3AF"     # grey — balanced

        cy = height // 2
        self.eval_canvas.create_text(width // 2, cy, text=label,
                                     fill=label_color, font=("Segoe UI", 7, "bold"),
                                     angle=90, tags="eval")

        if getattr(self, "_eval_tip_window", None) is not None:
            self._update_eval_tooltip()

        # Bind hover tooltip (bind once; re-binding is harmless)
        if not getattr(self, "_eval_hover_bound", False):
            self._eval_hover_bound = True
            self._eval_tip_window = None
            def _show_eval_tip(e):
                if getattr(self, "_eval_dragging", False) or self._eval_tip_window:
                    return
                tip = tk.Toplevel(self.eval_canvas)
                tip.wm_overrideredirect(True)
                tip.attributes("-topmost", True)
                # Human POV, same sign convention as the arrow labels: positive
                # = good for YOU, negative = good for the opponent.  (raw
                # self.current_score is White-centred, which used to contradict
                # the "(Opponent advantage)" wording when playing Black.)
                text, tip_bg = self._eval_tooltip_content()
                self._eval_tip_label = tk.Label(tip, text=text, bg=tip_bg, fg="white",
                         font=("Segoe UI", 9, "bold"), padx=8, pady=4,
                         relief=tk.FLAT)
                self._eval_tip_label.pack()
                x = self.eval_canvas.winfo_rootx() + self.eval_canvas.winfo_width() + 4
                y = self.eval_canvas.winfo_rooty() + e.y
                tip.geometry(f"+{x}+{y}")
                self._eval_tip_window = tip
            def _hide_eval_tip(e):
                if self._eval_tip_window:
                    self._eval_tip_window.destroy()
                    self._eval_tip_window = None
            self.eval_canvas.bind("<Enter>", _show_eval_tip, add="+")
            self.eval_canvas.bind("<Leave>", _hide_eval_tip, add="+")
            self.eval_canvas.bind("<Motion>", lambda e: (_hide_eval_tip(e), _show_eval_tip(e)), add="+")



    def draw_accuracy_overlay(self):
        """Keep the board text-free; accuracy lives in the lower GUI section."""
        # Remove panels left over from a previous version/session, if any.
        self.canvas.delete("accuracy_overlay")
        if hasattr(self, "control_panel"):
            self.update_stats_display()

    # Backward-compatible name retained for any external callers.
    def draw_accuracy_on_eval_canvas(self):
        self.draw_accuracy_overlay()

    def convert_coordinates(self, col, row):
        if self.perspective == "black":
            return 7 - col, 7 - row
        return col, row

    def draw_move(self, start_sq, end_sq, move_number, score=None, decision_time=None, is_opponent=False, vertical_offset=0, scale=None, tag="arrow", color_override=None):
        """Draw an arrow only (no eval numbers / decision-time labels) with the
        colour scheme: blue (1st), green (2nd), yellow (3rd), orange (4th), red
        (5th+); opponent arrows are a darker crimson."""
        try:
            start_col, start_row = ord(start_sq[0]) - ord('a'), 8 - int(start_sq[1])
            end_col, end_row = ord(end_sq[0]) - ord('a'), 8 - int(end_sq[1])

            start_col, start_row = self.convert_coordinates(start_col, start_row)
            end_col, end_row = self.convert_coordinates(end_col, end_row)

            width, height = self.arrow_canvas.winfo_width(), self.arrow_canvas.winfo_height()

            # Account for the thin border
            border = 3
            inner_width = width - (2 * border)
            inner_height = height - (2 * border)

            start_x = border + (start_col + 0.5) * inner_width / 8
            start_y = border + (start_row + 0.5) * inner_height / 8
            end_x = border + (end_col + 0.5) * inner_width / 8
            end_y = border + (end_row + 0.5) * inner_height / 8

            # Color and styling based on move number and is_opponent
            # Player palette (full brightness); opponent palette (same hues, ~50% darker)
            color_palette = [
                "#1E90FF",  # dodgerblue
                "#32CD32",  # limegreen
                "#FFD700",  # gold
                "#FFA500",  # orange
                "#FF4444",  # red
            ]
            dark_palette = [
                "#062B4D",  # deep navy: opponent best move
                "#0B3D22",  # deep forest: opponent prediction
                "#4A3D00",  # dark gold
                "#4A3000",  # dark orange
                "#4A1010",  # dark red
            ]
            if is_opponent:
                color = dark_palette[min(move_number - 1, len(dark_palette) - 1)]
                opacity = 0.75
                line_width = 8 * self.arrow_scale
                arrowhead_width = 15 * self.arrow_scale
                arrowhead_height = 12 * self.arrow_scale
            else:
                if self.display_mode == "bestmove":
                    color_palette[:2] = ["#FF5050", "#800020"]
                elif self.display_mode == "multiPV":
                    color_palette[:2] = ["#1E90FF", "#001B42"]
                color = color_palette[min(move_number - 1, len(color_palette) - 1)]
                if color_override:
                    color = color_override
                opacity = 0.9 if move_number == 1 else 0.7
                if scale is None:
                    scale = 1.0 if move_number == 1 else max(0.5, 1.0 - 0.25 * (move_number - 1))
                
                line_width = 10 * scale * self.arrow_scale
                arrowhead_width = 20 * scale * self.arrow_scale
                arrowhead_height = 15 * scale * self.arrow_scale

            # Create arrow line
            item_id = self.arrow_canvas.create_line(
                start_x, start_y, end_x, end_y,
                fill=color,
                width=line_width,
                tags=tag,
                state="disabled",
                capstyle=tk.ROUND,
                arrow=tk.LAST,
                arrowshape=(arrowhead_width, arrowhead_height, 5),
                dash=(6, 4) if getattr(self, "arrow_dash", False) else None
            )
            if getattr(self, "arrow_animate", False):
                self._arrow_pulse_widths.append((item_id, line_width))
        except:
            pass

    def _score_to_float(self, score):
        """Convert a normal engine score to pawns; mate scores stay out of calibration."""
        value = str(score).strip()
        if not value or "M" in value.upper():
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _format_score_for_display(self, white_centered_num, current_turn, our_color, is_mate=False, mate_label=None):
        """Format the engine's numeric eval to a user label.

        Distinguishes 'FM' (we deliver forced mate) from 'EM' (enemy/opponent delivers mate) ONLY
        when checkmate is forced. High numeric scores (+10.0, +15.0 cp) display as numerical pawns.
        """
        if is_mate:
            if mate_label:
                return str(mate_label)
            sign = 1 if white_centered_num > 0 else -1
            mating_color = "w" if sign > 0 else "b"
            effective_our = our_color if our_color in ("w", "b") else current_turn
            return "FM" if mating_color == effective_our else "EM"

        # Non-mate numbers: always format as ±pawn string regardless of magnitude
        try:
            val = float(white_centered_num)
        except (TypeError, ValueError):
            return str(white_centered_num)

        if abs(val) < 0.005:
            return "0.00"
        if our_color == "b":
            return f"{(-val):+.2f}"
        return f"{val:+.2f}"

    def _format_mate_depth(self, mate_depth_str, current_turn, our_color):
        """Given a UCI mate depth ('+3', '-4', '3') return ('FM3' or 'EM3', winning-side num).

        UCI sign convention: 'mate +N' = side to move mates in N; 'mate -N' = side to move is mated in N.
        'winning_num' is +10 if white mates, -10 if black mates.  FM = forced mate we deliver.
        """
        s = str(mate_depth_str).replace("+", "").strip()
        is_neg = s.startswith("-")
        digits = s.lstrip("-") or "1"
        try:
            n = abs(int(float(digits)))
        except (ValueError, TypeError):
            n = 1
        if is_neg:
            # side-to-move gets mated, so the OTHER color is the mating side
            mating_color = "b" if current_turn == "w" else "w"
        else:
            mating_color = current_turn
        effective_our = our_color if our_color in ("w", "b") else current_turn
        # FM = forced mate we deliver, EM = enemy delivers mate against us.
        # All engine mates are forced, so our-side mates are always "FM".
        prefix = "FM" if mating_color == effective_our else "EM"
        winning_num = 10.0 if mating_color == "w" else -10.0
        return f"{prefix}{n}", winning_num

    def _display_to_white_score(self, display):
        """Convert a stored display score (human-POV) to a White-centered number.

        CP strings like "+2.35" are already signed from the human's point of view.
        Mate labels "FM1" (we mate) / "EM1" (enemy mates) map to +/-10 the same way.
        Returns None when the label cannot be parsed.
        """
        s = str(display).strip()
        if not s:
            return None
        us = self._human_color()
        if "M" in s.upper():
            up = s.upper()
            we_mate = up.startswith("FM") or (up.startswith("M") and not up.startswith("EM"))
            white_value = 10.0 if we_mate else -10.0
            return white_value if us == "w" else -white_value
        try:
            v = float(s)
        except (TypeError, ValueError):
            return None
        return v if us == "w" else -v

    def _is_lc0_engine(self, engine=None):
        """Check if the given (or active primary) engine is LC0 / Leela Chess Zero."""
        if engine is None:
            if getattr(self, "engines", None) and 0 <= getattr(self, "primary_engine_index", 0) < len(self.engines):
                engine = self.engines[self.primary_engine_index]
            elif getattr(self, "engines", None):
                engine = self.engines[0]
            else:
                return False
        name_low = (str(engine.get("name", "")) + " " + str(engine.get("path", ""))).lower()
        return any(h in name_low for h in ("lc0", "leela", "cudnn", "cublas", "directml", "dml"))

    def _cp_to_win_pct(self, cp):
        return win_percent(cp)

    def _accuracy_from_loss(self, best_cp, move_cp):
        """Estimate against this search's best evaluation, not a rating or site score."""
        return move_accuracy(best_cp, move_cp)

    def _blunder_safe_lines(self, lines):
        """Keep scored engine alternatives within the user's pawn-loss limit."""
        if not getattr(self, "aggressive_blunder_filter_enabled", True) or not self._is_human_turn():
            return list(lines)
        scored = []
        for rank, moves, label in lines:
            if rank <= 0 or not moves:
                continue
            value = self._display_to_white_score(label)
            if value is None or not math.isfinite(value):
                continue
            value = self._human_pov_score(value)
            text = str(label).upper().lstrip("+-")
            mate = 1 if text.startswith(("FM", "M")) else (-1 if text.startswith("EM") else 0)
            scored.append((rank, moves, label, value, mate))
        if not scored:
            return [line for line in lines if line[0] <= 0]
        best = max(scored, key=lambda item: (item[4], item[3] if item[4] == 0 else -item[0]))
        threshold = max(0.0, float(getattr(self, "aggressive_blunder_threshold", 0.50)))
        allowed = set()
        for rank, moves, label, value, mate in scored:
            if mate > best[4] or (mate == best[4] and
                    ((mate == 0 and best[3] - value <= threshold + 1e-9)
                     or (mate > 0) or rank == best[0])):
                allowed.add(rank)
        return [line for line in lines if line[0] <= 0 or line[0] in allowed]

    def _target_accuracy_candidate(self):
        raw = self._visible_multi_pvs_raw()
        key = (self.current_fen, self._human_color(),
               tuple((r,tuple(d[0]),d[1]) for r,d in raw if d and d[0]),
               self.target_recommendation_accuracy_min,self.target_recommendation_accuracy_max,
               self.target_recommendation_accuracy_priority,
               getattr(self, "recommendation_strength_first", False), self.recommendation_accuracy_enabled,
               self.display_mode, self.comparison_player_elo, self._flag_mode_active(),
               self.aggressive_blunder_filter_enabled,self.aggressive_blunder_threshold,
               self.anti_draw_enabled,self.anti_draw_threshold,self.anti_draw_min_advantage,
               self._accuracy_opening_exempt(),self._book_policy_key(),getattr(self,'_bridge_epoch',None))
        cached=getattr(self,'_accuracy_selection_cache',None)
        if self._suggestions_are_ready() and cached and cached[0]==key:
            self.recommendation_accuracy_estimate=cached[2]
            self.recommendation_accuracy_status=cached[3]
            return cached[1]
        result=self._choose_accuracy_candidate()
        if self._suggestions_are_ready():
            self._accuracy_selection_cache=(key,result,self.recommendation_accuracy_estimate,self.recommendation_accuracy_status)
        return result

    def _brilliant_ranks(self):
        """Safe, near-best analyzed sacrifices; mate priority is preserved."""
        if not self._is_human_turn() or not chess_available():
            return set()
        raw = self._visible_multi_pvs_raw()
        lines = [(rank, data[0], data[1]) for rank, data in raw if data and data[0]]
        safe = self._draw_safe_lines(self._blunder_safe_lines(lines))
        mates = [line for line in safe if str(line[2]).upper().startswith(('FM', 'M'))]
        if mates:
            best = min(mates, key=lambda line: line[0])
            return {best[0]} if 'sacrifice' in line_features(self.current_fen, best[1]) else set()
        scores = [self._score_to_float(label) for _, _, label in lines]
        scores = [v for v in scores if v is not None and math.isfinite(v)]
        if not scores:
            return set()
        reference = max(scores)
        return {rank for rank, moves, label in safe
                if (value := self._score_to_float(label)) is not None and math.isfinite(value)
                and reference - value <= .25 + 1e-9
                and 'sacrifice' in line_features(self.current_fen, moves)}

    def _choose_accuracy_candidate(self):
        """Pick from a single committed search using the active depth/Elo options.

        Safety, draw avoidance, forced mates and book moves may constrain the
        requested range. Report those constraints instead of claiming a match.
        """
        raw = self._visible_multi_pvs_raw()
        lines = [(r, d[0], d[1]) for r, d in raw if d and d[0]]
        safe = self._blunder_safe_lines(lines)
        safe = self._draw_safe_lines(safe)
        candidates = []
        for rank, moves, label in safe:
            value = self._display_to_white_score(label)
            if value is not None and math.isfinite(value):
                candidates.append((rank, (moves, label), self._human_pov_score(value)))
        self.recommendation_accuracy_estimate = None
        self.recommendation_accuracy_status = 'Waiting for scored engine alternatives'
        if not candidates:
            return None
        # A forced win is never traded for an accuracy percentage.
        mating = [c for c in candidates if str(c[1][1]).upper().startswith(('FM', 'M'))]
        if mating:
            best = min(mating, key=lambda c: c[0])
            self.recommendation_accuracy_estimate = 100.0
            self.recommendation_accuracy_status = 'Forced mate takes priority'
            if self.display_mode == 'others' and best[0] in self._brilliant_ranks():
                self.recommendation_accuracy_status += '; brilliant sacrifice heuristic'
            elif self.display_mode == 'others' and not chess_available():
                self.recommendation_accuracy_status += '; Brilliant requires python-chess'
            elif self.display_mode == 'others':
                self.recommendation_accuracy_status += '; no qualifying sacrifice among analyzed moves'
            return best[0], best[1]
        candidates = [c for c in candidates if not str(c[1][1]).upper().startswith('EM')]
        if not candidates:
            self.recommendation_accuracy_status = 'All scored lines allow mate; target unavailable'
            return None
        best = max(candidates, key=lambda c: (c[2], -c[0]))
        # Use the strongest original root score, even if draw/safety filtering
        # removes it. Re-normalizing after filtering would inflate percentages.
        original = [self._human_pov_score(self._display_to_white_score(label))
                    for _r, _m, label in lines
                    if self._display_to_white_score(label) is not None
                    and math.isfinite(self._display_to_white_score(label))
                    and 'M' not in str(label).upper()]
        reference = max(original) if original else best[2]
        low, high = self.target_recommendation_accuracy_min, self.target_recommendation_accuracy_max
        priority = max(low, min(high, float(self.target_recommendation_accuracy_priority)))
        calibrated = [(self._accuracy_from_loss(reference*100, c[2]*100), c) for c in candidates]
        side_known = (self.player_color in ('w','b') or getattr(self,'current_playing_as',None) in ('w','b'))
        history = getattr(self, '_adaptive_history', {}).get(self._human_color(), []) if side_known else []
        continuation = self._line_memory.expected(self.current_fen, self._human_color()) if hasattr(self, '_line_memory') else None
        options = [(c[0], c[1], c[2], estimate) for estimate, c in calibrated]
        # A sound-looking sacrifice takes precedence in Brilliant mode.  It is
        # still constrained by the user's loss cap and draw filter above.
        brilliant_ranks = self._brilliant_ranks() if self.display_mode == 'others' else set()
        brilliant = [c for c in options if c[0] in brilliant_ranks]
        book_lines = self._opening_book_lines() or self._cloud_book_lines()
        book_pick = next((c for line in book_lines for c in options if c[0] == line[0]), None)
        if brilliant:
            selected = min(brilliant, key=lambda c: c[0])
            status = 'Apparent sound sacrifice; brilliant heuristic overrides range and book'
        elif book_pick is not None:
            selected = book_pick
            source = 'local' if self._opening_book_lines() else 'cloud'
            status = f'Validated {source} book move; accuracy adjustment paused'
        elif self._opening_protected():
            strongest = max(options, key=lambda c:(c[2],-c[0]))
            selected = next((c for c in options if c[1][0][0] == continuation
                             and strongest[2]-c[2] <= .20 + 1e-9), strongest)
            status = ('Opening protection (moves 1–10); continuing a safe earlier line'
                      if selected is not strongest else
                      'Opening protection (moves 1–10); strongest safe engine move')
        elif not self.recommendation_accuracy_enabled:
            selected = max(options, key=lambda c: (c[2], -c[0]))
            status = 'Recommendation policy off; strongest safe score'
        elif getattr(self, 'recommendation_strength_first', False):
            # Do not accumulate deliberate evaluation concessions on every move.
            # Preserve a planned follow-up only when its displayed score is tied.
            strongest = max(options, key=lambda c: (c[2], -c[0]))
            selected = next((c for c in options if c[1][0][0] == continuation
                             and abs(strongest[2] - c[2]) <= 1e-9), strongest)
            status = 'Strength first; strongest safe score (accuracy range is advisory)'
        else:
            def style_order(shortlist):
                lines = [(c[0], c[1][0], c[1][1]) for c in shortlist]
                ordered, _ = self._apply_mode_ordering_filter(lines)
                by_rank = {c[0]: c for c in shortlist}
                return [by_rank[line[0]] for line in ordered]
            styled = self.display_mode != 'pv' or self._flag_mode_active()
            selected, status = choose_candidate(options, history, low, high, priority,
                                               continuation, style_order if styled else None)
            if self.aggressive_blunder_filter_enabled:
                status += f'; loss cap {self.aggressive_blunder_threshold:.2f} pawns'
        self.recommendation_accuracy_estimate = selected[3]
        if self.display_mode == 'others' and not chess_available():
            status += '; Brilliant requires python-chess'
        elif self.display_mode == 'others' and not brilliant:
            status += f'; no qualifying sacrifice among {len(lines)} analyzed moves'
        self.recommendation_accuracy_status = status + f' · chosen estimate {selected[3]:.1f}%'
        return selected[0], selected[1]

    def toggle_depth_timing(self):
        self.depth_timer.toggle()
        self._refresh_depth_timing()

    def _depth_timing_context(self, context, epoch):
        game_id = context.get('game_id')
        key = (context.get('site'), game_id) if game_id else None
        old_key = getattr(self, '_timing_game_key', None)
        old_epoch = getattr(self, '_timing_epoch', None)
        changed = ((key is not None and old_key is not None and key != old_key)
                   or (epoch != old_epoch and old_epoch is not None and (key is None or key != old_key)))
        if changed:
            self.depth_timer.reset()
            self._timing_game_key = key
        elif key is not None:
            self._timing_game_key = key
        self._timing_epoch = epoch

    def _refresh_depth_timing(self):
        panel = getattr(self, 'control_panel', None)
        if not panel or not hasattr(panel, 'depth_timing_button'):
            return
        data = self.depth_timer.snapshot()
        panel.depth_timing_button.configure(text='Stop timing' if data['enabled'] else 'Start timing')
        active = data['active']
        def seconds(value):
            return '—' if value is None else f'{value:.3f}s'
        if active:
            status = f"Timing depth {active['target']} · {active['elapsed']:.3f}s · {active['engine']}"
        else:
            status = 'Timing enabled · waiting for the next search' if data['enabled'] else 'Timing off'
        summary = (f"{status}\nCompleted: {data['count']}   Average: {seconds(data['average'])}   "
                   f"Fastest: {seconds(data['fastest'])}   Slowest: {seconds(data['slowest'])}\n"
                   f"Incomplete: {data['incomplete']}   Cache hits: {data['cached']} (excluded from averages)")
        panel.depth_timing_summary.configure(text=summary)
        lines = []
        for row in reversed(data['rows']):
            move = row['fen'].split()
            turn = f"{move[5]}{'w' if move[1] == 'w' else 'b'}" if len(move) >= 6 else '?'
            if row['status'] == 'Cache hit':
                lines.append(f"Move {turn} · depth {row['target']} · Cache hit (no search)")
            else:
                lines.append(f"Move {turn} · {row['depth']}/{row['target']} · {row['elapsed']:.3f}s · "
                             f"{row['status']} · {row['candidates']} lines · {row['engine']}")
        text = '\n'.join(lines) or 'No measurements this match.'
        if getattr(panel, '_depth_timing_text', None) != text:
            panel._depth_timing_text = text
            panel.depth_timing_log.configure(state=tk.NORMAL)
            panel.depth_timing_log.delete('1.0', tk.END)
            panel.depth_timing_log.insert(tk.END, text)
            panel.depth_timing_log.configure(state=tk.DISABLED)

    def _apply_game_context(self, context, epoch):
        cleaned=clean_context(context)
        self._depth_timing_context(cleaned, epoch)
        previous=getattr(self,'game_context',{})
        if previous.get('game_id') and cleaned.get('game_id') and previous['game_id']!=cleaned['game_id']:
            self._adaptive_history={"w":[],"b":[]}
            self._line_memory=LineMemory()
            self._accuracy_selection_cache=None
            self._validated_book_cache = None
        self.game_context=cleaned
        self._game_context_epoch=epoch
        panel=getattr(self,'control_panel',None)
        if panel and hasattr(panel,'game_context_label'):
            panel.game_context_label.configure(text=self._game_context_text())

    def _game_context_text(self):
        context=getattr(self,'game_context',{}) if getattr(self,'context_auto',True) else {}
        mine=context.get('self_rating') or getattr(self,'manual_player_rating',0) or 'unknown'
        theirs=context.get('opponent_rating') or getattr(self,'manual_opponent_rating',0) or 'unknown'
        speed=context.get('speed') or 'unknown'
        seconds=context.get('initial_seconds')
        control=f"{seconds}s + {context.get('increment_seconds',0)}s" if seconds is not None else 'unknown'
        def clock(key):
            v=context.get(key)
            return f'{v/1000:.1f}s' if v is not None else 'unknown'
        return (f"You {mine} · Opponent {theirs} · {speed} · Time control {control}\n"
                f"Clocks: you {clock('self_ms')} / opponent {clock('opp_ms')} · "
                f"Rated {context.get('rated') if context.get('rated') is not None else 'unknown'} · "
                f"Variant {context.get('variant') or 'unknown'}")

    def update_context_preferences(self, *_args):
        panel=self.control_panel
        try:
            self.context_auto=bool(panel.context_auto_var.get())
            self.manual_player_rating=max(0,min(4000,int(panel.context_player_var.get())))
            self.manual_opponent_rating=max(0,min(4000,int(panel.context_opponent_var.get())))
        except (ValueError,tk.TclError):
            return
        panel.game_context_label.configure(text=self._game_context_text())
        self._save_all_settings()

    def _opening_protected(self, fen=None):
        """A move-number policy, not a claim that every early position is theory."""
        try:
            return 1 <= int((fen or self.current_fen).split()[5]) <= 10
        except (AttributeError, ValueError, IndexError, TypeError):
            return False

    def _accuracy_opening_exempt(self, fen=None):
        if self._opening_protected(fen):
            return True
        if fen is not None and fen != self.current_fen:
            return False  # No fresh candidate set for a different board.
        return bool(self._opening_book_lines() or self._cloud_book_lines())

    def _book_policy_key(self):
        engines = getattr(self, 'engines', [])
        primary = engines[min(getattr(self, 'primary_engine_index', 0), len(engines)-1)] if engines else None
        generation = (id(primary), primary.get('search_generation', 0)) if primary is not None else None
        return (bool(getattr(self, 'opening_book_enabled', False)),
                id(getattr(self, 'opening_book', None)),
                bool(getattr(getattr(self, 'book_manager', None), 'cloud_enabled', False)), generation)

    def _validated_book_lines(self):
        """Book hints must match a fresh, safe engine PV; freeze once per result.

        No extra search or network wait. A late explorer result applies to future
        positions, without changing a recommendation already shown for this one.
        """
        if not self._suggestions_are_ready() or not self._is_human_turn():
            return [], []
        raw = self._visible_multi_pvs_raw()
        key = (self.current_fen, self._human_color(), self._book_policy_key(),
               tuple((r,tuple(d[0]),d[1]) for r,d in raw if d and d[0]),
               self.aggressive_blunder_filter_enabled,self.aggressive_blunder_threshold,
               self.anti_draw_enabled,self.anti_draw_threshold,self.anti_draw_min_advantage,
               getattr(self, '_bridge_epoch', None), getattr(self, 'game_context', {}).get('game_id'))
        cached = getattr(self, '_validated_book_cache', None)
        if cached and cached[0] == key:
            return cached[1], cached[2]
        lines = [(rank,data[0],data[1]) for rank,data in raw if data and data[0]]
        local, cloud = [], []
        # No numeric book move may displace a forced mate, even with filtering off.
        if not any(str(line[2]).upper().startswith(('FM','M')) for line in lines):
            numeric = [(line,self._score_to_float(line[2])) for line in lines]
            numeric = [(line,value) for line,value in numeric if value is not None and math.isfinite(value)]
            if numeric:
                best = max(value for line,value in numeric)
                safe = self._draw_safe_lines(self._blunder_safe_lines(lines))
                allowed = {line[1][0]: line for line,value in numeric
                           if line in safe and best-value <= .25 + 1e-9}
                def validate(hits):
                    weights = {}
                    for move, weight in hits:
                        try:
                            weight = float(weight)
                            if move in allowed and math.isfinite(weight) and weight > 0:
                                weights[move] = weights.get(move, 0) + weight
                        except (TypeError, ValueError):
                            continue
                    return [allowed[move] for move in sorted(weights,
                            key=lambda move: (-weights[move], allowed[move][0]))]
                if getattr(self, 'opening_book_enabled', False) and getattr(self, 'opening_book', None):
                    try:
                        local = validate(self.opening_book.lookup(self.current_fen))
                    except Exception:
                        local = []
                if (self._book_policy_key()[2] and getattr(self, '_cloud_lines_fen', None) == self.current_fen):
                    hits = [(item.get('uci'), item.get('pct', 0)) for item in
                            (getattr(self, '_cloud_lines_result', None) or []) if isinstance(item,dict)]
                    cloud = validate(hits)
        self._validated_book_cache = (key,local,cloud)
        return local, cloud

    def _accuracy_pool_key(self, engine, fen):
        # Legacy callers may still inspect this identity; it no longer expands.
        return (fen, self.analysis_depth, self._bridge_epoch)

    def _maybe_expand_accuracy_search(self, engine, fen):
        return False

    def update_recommendation_policy(self):
        panel = self.control_panel
        try:
            self.anti_draw_enabled = bool(panel.anti_draw_var.get())
            self.anti_draw_threshold = max(0.0, min(2.0, float(panel.draw_threshold_var.get())))
            self.anti_draw_min_advantage = max(self.anti_draw_threshold + 0.01,
                                             min(5.0, float(panel.draw_advantage_var.get())))
            panel.draw_advantage_var.set(round(self.anti_draw_min_advantage, 2))
        except (ValueError, tk.TclError):
            return
        self._save_all_settings()
        self._reanalyse_after_option_change()

    def update_accuracy_search_candidates(self):
        try:
            self.accuracy_candidate_limit = max(4, min(16, int(self.control_panel.accuracy_pool_var.get())))
        except (ValueError, tk.TclError):
            return
        self.control_panel.accuracy_pool_var.set(self.accuracy_candidate_limit)
        self._save_all_settings()
        self._reanalyse_after_option_change()

    def _visible_multi_pvs_raw(self, include_best=True):
        # Use committed (stable) PVs to prevent mid-search flicker.
        # Fall back to live multi_pvs until the first commit happens.
        source = self.committed_multi_pvs if self.committed_multi_pvs else self.multi_pvs
        engines = getattr(self, "engines", [])
        if engines:
            primary = engines[min(getattr(self, "primary_engine_index", 0), len(engines) - 1)]
            if "_pv_batches" in primary and not self.committed_multi_pvs:
                return []
        ranks = sorted(rank for rank in source if isinstance(rank, int) and rank >= 1)
        if not include_best:
            ranks = [rank for rank in ranks if rank != 1]
        return [(rank, source[rank]) for rank in ranks]

    def _visible_multi_pvs(self, include_best=True):
        """Return valid root alternatives, with the accuracy target first when active."""
        candidates = self._visible_multi_pvs_raw(include_best)
        opening_policy = self._opening_protected() or self._opening_book_lines() or self._cloud_book_lines()
        if ((not self.recommendation_accuracy_enabled and not opening_policy) or not include_best
                or not self._is_human_turn()):
            return candidates
        selected = self._target_accuracy_candidate()
        if not selected:
            return candidates
        return [selected] + [candidate for candidate in candidates if candidate[0] != selected[0]]

    def _move_style(self, move):
        return set()

    def _human_color(self):
        """Resolve the human's side consistently across the whole overlay.
        Visual screen detector is the primary ground truth in Auto mode."""
        if self.player_color in ("w", "b"):
            return self.player_color
        det = getattr(self, "detected_player_color", None)
        if det in ("w", "b"):
            return det
        screen, age = self._screen_bottom_result()
        if screen in ("w", "b") and age < 15.0:
            return screen
        return "w" if getattr(self, "perspective", "white") == "white" else "b"

    def _is_human_turn(self):
        """True when the side to move is the human's side.

        The accuracy band, its target label, and any suggestion re-ordering
        only make sense for the human's move choices.  On the opponent's turn
        the engine best must stay the blue #1 arrow, so callers use this to
        gate accuracy selection and non-engine ordering.
        """
        turn = getattr(self, "current_player_color", "w")
        return turn == self._human_color()

    def _game_phase(self):
        """Classify the current position: 'opening', 'middlegame' or 'endgame'.
        Combines move count (fullmove <= 5) and non-pawn material density.
        """
        fen = getattr(self, "current_fen", None)
        if not fen:
            return "opening"
        try:
            parts = fen.split()
            fullmove = int(parts[5]) if len(parts) >= 6 else 1
            board = self._parse_fen_board(parts[0])
            phase = 0
            for piece in board.values():
                p = piece.lower()
                if p in ("n", "b"):
                    phase += 1
                elif p == "r":
                    phase += 2
                elif p == "q":
                    phase += 4
            
            if fullmove <= 5 or phase >= 20:
                return "opening"
            elif phase >= 8:
                return "middlegame"
            else:
                return "endgame"
        except Exception:
            return "opening"

    def _human_pov_score(self, white_centered_num):
        """Flip White-centered eval to HUMAN POV (+ = you, - = opponent)."""
        us = self._human_color()
        if us == "w":
            return white_centered_num
        return -white_centered_num

    def _is_score_absolute_better(self, s_num_a, s_num_b):
        """Return True if human-POV score a is materially better than b.

        Numeric evaluations use a ~30cp noise floor. Mate decisions require
        explicit mate labels rather than a large numeric pawn evaluation.
        """
        a = self._human_pov_score(s_num_a)
        b = self._human_pov_score(s_num_b)
        return a - b > 0.30

    def _apply_mode_ordering_filter(self, raw_lines):
        """Take list of (rank, moves_list, score_str) and re-order/filter it
        per the currently active display_mode.

        Returns (ordered_list_of_same_tuples, set_of_fallback_flags).
        """
        if not raw_lines:
            return [], set()

        mode = self.display_mode
        fallback = set()
        our_color = self._human_color()

        # Convert each entry to a dict with: original_rank, first_move, score_str,
        # score_num (white-centered), human_score, pv_len (# of half-moves in PV)
        def material_swings(score_num):
            # We don't have pre-move evals here, so approximate: bigger absolute CP
            # swing from current_fen's own eval implies a more "tactical" line that
            # creates concrete threats (or gambits). Also longer PVs → more forcing.
            delta = abs(score_num - self.current_score)
            return delta

        entries = []
        human_current = self._human_pov_score(self.current_score or 0.0)
        for (rank, moves, score_str) in raw_lines:
            s_num = self._display_to_white_score(score_str)
            if s_num is None or not math.isfinite(s_num):
                continue
            mate_label = str(score_str).upper().lstrip("+-")
            mate = mate_label.startswith(("FM", "EM", "M"))
            human = self._human_pov_score(s_num)
            if mate:
                human = 1000000.0 if mate_label.startswith(("FM", "M")) else -1000000.0
            entries.append({
                "rank": rank,
                "moves": moves,
                "score_str": score_str,
                "mate": mate,
                "score_num": s_num,
                "human": human,
                "threat": material_swings(s_num),
                # Signed swing from the current position's eval: + = this move
                # IMPROVES our position, - = it worsens it.  Blundering material
                # gives a large NEGATIVE swing, so aggressive mode can never
                # prefer a losing gamble while a good move exists.
                "swing": human - human_current,
                "pv_len": len(moves) if moves else 0,
                "features": line_features(self.current_fen, moves),
            })

        if not entries:
            return [], fallback
        best_human = max(e["human"] for e in entries)

        # -----------------------------------------------------------------
        # BEST MOVE (default / pv) — engine best first, rest by engine rank
        # -----------------------------------------------------------------
        if mode in ("pv", "bestmove"):
            entries.sort(key=lambda e: (e["rank"]))
            if mode == "bestmove":
                # Aggressive filter: from the top 8 candidate root moves,
                # prefer the one that is best for us *without* dropping below
                # the blunder-tolerance threshold vs #1 eval.
                threshold = float(getattr(self, "aggressive_blunder_threshold", 0.50))
                blunder_filter_on = bool(getattr(self, "aggressive_blunder_filter_enabled", True))
                # Build short-list: top-8 engine root moves.
                short = sorted(entries, key=lambda e: e["rank"])[:8]
                # Material-safety floor.  With the filter on, use the user's
                # tolerance; with it off, still never pick a line that loses
                # more than a rook vs the engine's best (the signed-swing sort
                # below already avoids losing gambles, this is belt-and-braces).
                safety_floor = threshold if blunder_filter_on else 1.50
                def acceptable(e):
                    if e["mate"]:
                        return e["score_num"] * (1 if our_color == "w" else -1) >= 0
                    return best_human - e["human"] <= safety_floor
                elig = [e for e in short if acceptable(e)]
                if elig:
                    # Offensive first: biggest improvement to OUR position, then
                    # the most forcing/tactical line, then best engine rank.
                    # A move that loses material has a large negative swing and
                    # only wins when every candidate is bad (i.e. the position
                    # is lost) — in which case it is the least-bad option.
                    elig.sort(key=lambda e: ('offensive_tactic' not in e['features'], 'forcing' not in e['features'], -e["swing"], -e["pv_len"], e["rank"]))
                    top = elig[0]
                    # Reorder: aggressive pick first, then the rest by engine rank.
                    rest = [e for e in entries if e["rank"] != top["rank"]]
                    rest.sort(key=lambda e: e["rank"])
                    entries = [top] + rest
                else:
                    fallback.add("aggressive")
                    entries.sort(key=lambda e: e["rank"])

        # -----------------------------------------------------------------
        # DEFENSIVE (multiPV mode label) — safest, smallest advantage line
        # -----------------------------------------------------------------
        elif mode == "multiPV":
            threshold = float(getattr(self, "aggressive_blunder_threshold", 0.50))
            # Eligible: lines within ~threshold CP of engine best (#1 eval drop)
            # AND not a mate for the enemy.
            def def_acceptable(e):
                if e["mate"]:
                    return e["score_num"] * (1 if our_color == "w" else -1) > 0
                return best_human - e["human"] <= max(threshold, 0.30)
            elig = [e for e in entries if def_acceptable(e)]
            if elig:
                # Defensive preference: first avoid lines that worsen OUR
                # position (swing >= 0), then LOWEST "threat" / smallest CP
                # change (most solid, drawish), SHORTER PV = less forcing =
                # safer, and finally the best engine rank.
                elig.sort(key=lambda e: ('defensive_tactic' not in e['features'], e["swing"] < 0, e["threat"], e["pv_len"], e["rank"]))
                top = elig[0]
                rest = [e for e in entries if e["rank"] != top["rank"]]
                rest.sort(key=lambda e: e["rank"])
                entries = [top] + rest
            else:
                fallback.add("defensive")
                entries.sort(key=lambda e: e["rank"])

        # -----------------------------------------------------------------
        # BRILLIANT — favor a sound sacrifice identified in the completed PV.
        # -----------------------------------------------------------------
        elif mode == "others":
            brilliant_ranks = self._brilliant_ranks()
            entries.sort(key=lambda e: (e['rank'] not in brilliant_ranks, e['rank']))

        # -----------------------------------------------------------------
        # TACTICIAN — prefer forcing continuations, then the engine rank.
        # -----------------------------------------------------------------
        elif mode == "advPV":
            entries.sort(key=lambda e: (not bool(e['features'] & {'offensive_tactic', 'defensive_tactic'}), e['rank']))

        # Default: rank order (pv)
        else:
            entries.sort(key=lambda e: e["rank"])

        # Flag / clock-pressure mode: in a drawn-ish equal endgame with the
        # opponent short on time, prefer the line that keeps the game going
        # (keeps queens / avoids trades / most forcing) within a small score
        # window of the engine's best, instead of the quickest draw.  This is a
        # human-turn pick (this filter only runs on the human's turn).
        if self._flag_mode_active() and mode in ("pv", "bestmove", "multiPV"):
            entries = self._apply_flag_mode_reorder(entries)

        out = [(e["rank"], e["moves"], e["score_str"]) for e in entries]
        return out, fallback

    def _opening_book_lines(self):
        return self._validated_book_lines()[0]

    def _cloud_book_lines(self):
        return self._validated_book_lines()[1]

    def _maybe_fetch_cloud_lines(self):
        """Kick off a background Lichess explorer fetch when the position has
        no local book data and no engine lines yet (the "fallback" case)."""
        try:
            bm = self.book_manager
            if not getattr(bm, "cloud_enabled", False) or not self.current_fen:
                return
            if self._cloud_lines_fen == self.current_fen:
                return
            if self._cloud_lines_inflight:
                return
            if time.time() < getattr(self, "_cloud_lines_fail_until", 0.0):
                return
            if getattr(self, 'opening_book_enabled', False) and getattr(self, 'opening_book', None):
                if self.opening_book.lookup(self.current_fen):
                    return
            if self.multi_pvs or self.committed_multi_pvs:
                return
            self._cloud_lines_inflight = True
            fen = self.current_fen
            threading.Thread(target=self._cloud_fetch_worker, args=(fen,), daemon=True).start()
        except Exception:
            pass

    def _cloud_fetch_worker(self, fen):
        """Background thread: query the explorer and post the result through
        the UI queue (never touch Tk widgets here)."""
        try:
            result = self.book_manager.cloud_lines(fen, limit=8)
        except Exception:
            result = None
        try:
            self.ui_queue.put(("cloud_lines", fen, result))
        except Exception:
            pass

    def _mode_candidates(self, mode):
        # Logs share the final choice with arrows and AutoMove. Re-applying the
        # style here used to silently replace the accuracy-selected headline.
        return self._compute_visible_suggestions()

    def _mode_empty_message(self):
        if self.display_mode == "bestmove":
            return "No qualifying offensive moves found — showing engine best."
        if self.display_mode == "multiPV":
            return "No qualifying defensive moves found — showing engine order."
        if self.display_mode == "advPV":
            return "Waiting for engine continuations."
        return "Waiting for engine lines..."

    # ---- draw-avoidance -----------------------------------------------------
    # "Never go for a draw unless it is a FORCED draw."  When the top engine
    # line evaluates to roughly 0.00 (a draw) but a lower-ranked line is
    # clearly winning for us, promote the winning line.  Only when every
    # candidate is a draw (or worse) do we accept the draw — that is the
    # "forced draw" case.
    DRAW_AVOID_BAND_PAWNS = 0.12      # |eval| below this = treat as a draw
    DRAW_AVOID_WIN_PAWNS = 0.35       # eval at/above this = clearly winning

    def _draw_safe_lines(self, entries):
        if not self.anti_draw_enabled or not self._is_human_turn() or len(entries) < 2:
            return list(entries)
        values = [self._score_to_float(e[2]) for e in entries]
        winning = [e for e, value in zip(entries, values)
                   if (str(e[2]).upper().startswith(('FM', 'M')) or
                       (value is not None and value >= self.anti_draw_min_advantage))
                   and not self._candidate_is_draw(e)]
        if not winning:
            return list(entries)
        return [e for e, value in zip(entries, values)
                if not ((value is not None and abs(value) <= self.anti_draw_threshold)
                        or self._candidate_is_draw(e))]

    def _candidate_is_draw(self, entry):
        move = entry[1][0] if entry[1] else None
        if not move:
            return False
        key = (self.current_fen, move)
        cache = getattr(self, '_draw_candidate_cache', {})
        if getattr(self, '_draw_cache_fen', None) != self.current_fen:
            cache = {}
            self._draw_cache_fen = self.current_fen
            self._draw_candidate_cache = cache
        if key not in cache:
            cache[key] = immediate_draw(self.current_fen, move)
        return cache[key]

    def _avoid_draw_rank(self, entries):
        eligible = self._draw_safe_lines(entries)
        if not eligible or not entries or eligible[0] == entries[0]:
            return list(entries)
        chosen = max(eligible, key=lambda e:
            (str(e[2]).upper().startswith(('FM', 'M')), self._score_to_float(e[2]) or 0))
        return [chosen] + [e for e in entries if e is not chosen]

    def _suggestions_are_ready(self):
        return bool(self.current_fen and getattr(self, '_suggestions_fen', None) == self.current_fen)

    def _compute_visible_suggestions(self):
        """Return the ordered suggestion list exactly as redraw_all_arrows draws it.

        Handles display-mode reordering, validated opening/accuracy selection,
        promotion and the max-suggestions cap.  Shared by the arrow renderer and
        the Chess Coach chat so narrated colours always match the drawn arrows.
        """
        if not self._suggestions_are_ready():
            return []
        pvs_source = dict(self._visible_multi_pvs_raw())
        all_ranks = sorted(pvs_source)

        lines = []
        for rank in all_ranks:
            pv_data = pvs_source[rank]
            if pv_data and pv_data[0]:
                lines.append((rank, pv_data[0], pv_data[1]))

        if self._is_human_turn():
            ordered_entries, fallback_flags = self._apply_mode_ordering_filter(lines)
            self._style_fallback = {
                "aggressive": "aggressive" in fallback_flags,
                "defensive": "defensive" in fallback_flags,
            }
        else:
            ordered_entries = lines
            self._style_fallback = {"aggressive": False, "defensive": False}

        book_lines = self._opening_book_lines() or self._cloud_book_lines()
        merged = ordered_entries

        if not merged and self.pv_line and not any("_pv_batches" in e for e in getattr(self, "engines", [])):
            _is_m = bool(getattr(self, "current_is_mate", False))
            _ml = getattr(self, "current_mate_label", None) if _is_m else None
            merged.append((1, self.pv_line, self._format_score_for_display(
                self.current_score, self.current_player_color, self._human_color(),
                is_mate=_is_m, mate_label=_ml)))

        if not merged:
            return []

        if ((self.recommendation_accuracy_enabled or self._opening_protected() or book_lines or self.display_mode == 'others')
                and self._is_human_turn() and merged and self.display_mode in ("pv", "bestmove", "multiPV", "others", "advPV")):
            engine_lines = [(i, e) for i, e in enumerate(merged) if e[0] > 0]
            selected = self._target_accuracy_candidate()
            if selected:
                sel_rank, (sel_moves, _sel_score) = selected
                match_i = None
                for i, (orig_r, m, _s) in engine_lines:
                    if orig_r == sel_rank or (m and m[0] and sel_moves and m[0] == sel_moves[0]):
                        match_i = i
                        break
                if match_i is not None:
                    rest = [e for i, e in enumerate(merged) if i != match_i]
                    # Preserve the active style's ordering for the other arrows.
                    eng_rest = [e for e in rest if e[0] > 0]
                    other_rest = [e for e in rest if e[0] <= 0]
                    merged = other_rest + [merged[match_i]] + eng_rest

        if (not self.recommendation_accuracy_enabled and not self._opening_protected() and not book_lines
                and self._is_human_turn()
                and self.display_mode in ('pv', 'bestmove') and hasattr(self, '_line_memory')):
            expected = self._line_memory.expected(self.current_fen, self._human_color())
            engine_lines = self._draw_safe_lines(self._blunder_safe_lines([e for e in merged if e[0] > 0]))
            numeric = [(e,self._score_to_float(e[2])) for e in engine_lines]
            scored = [(e,v) for e,v in numeric if v is not None]
            if scored and not any('M' in str(e[2]).upper() for e in engine_lines):
                best_score = max(v for e,v in scored)
                chosen = next((e for e,v in scored if e[1][0] == expected and best_score-v <= .20),None)
                if chosen:
                    merged = [e for e in merged if e[0] <= 0] + [chosen] + [e for e in merged if e[0] > 0 and e is not chosen]

        # Draw-avoidance: never leave a drawing line at the top when a clearly
        # winning alternative exists (unless the draw is forced).  Applied only
        # on the human's turn — the opponent's best line stays the blue #1.
        if self._is_human_turn() and self.display_mode in ("pv", "bestmove"):
            eng = [e for e in merged if e[0] > 0]
            non_eng = [e for e in merged if e[0] <= 0]
            eng = self._avoid_draw_rank(self._blunder_safe_lines(eng))
            merged = non_eng + eng

        merged = self._blunder_safe_lines(merged)
        max_display = max(1, int(self.multi_pv_count))
        visible = merged[:max_display]
        if (visible and visible[0][0] > 0 and self._is_human_turn()
                and hasattr(self, '_line_memory')):
            self._line_memory.remember(self.current_fen, visible[0][1], self._human_color())
        return visible

    def _suggestion_color_override(self, rank, moves, brilliant_ranks=None):
        if self.display_mode == 'others' and rank in (brilliant_ranks if brilliant_ranks is not None else self._brilliant_ranks()):
            return '#40E0FF'
        if self.display_mode == 'advPV':
            features = line_features(self.current_fen, moves)
            if 'defensive_tactic' in features:
                return '#003366'
            if 'offensive_tactic' in features:
                return '#000000'
        return None

    def redraw_all_arrows(self):
        """Draw engine move suggestions cleanly on the board overlay."""
        self._sync_arrow_layer()
        self._arrow_pulse_widths = []
        source = self.committed_multi_pvs if self.committed_multi_pvs else self.multi_pvs
        depth_snapshot = getattr(self, "_arrow_depth_snapshot", None)
        merged = self._compute_visible_suggestions()
        depth_label = "—"
        if merged:
            engine_lines = any(rank > 0 for rank, _, _ in merged)
            book_lines = any(rank <= 0 for rank, _, _ in merged)
            if engine_lines:
                current_source = self.committed_multi_pvs if self.committed_multi_pvs else self.multi_pvs
                if (depth_snapshot and depth_snapshot[0] == self.current_fen
                        and depth_snapshot[2] is source and source is current_source):
                    depth_label = str(depth_snapshot[1])
                elif not any("_pv_batches" in e for e in getattr(self, "engines", [])):
                    depth_label = str(self.current_depth)
                else:
                    depth_label = "updating"
                if book_lines:
                    depth_label += " + book/cloud"
            else:
                depth_label = "book/cloud"
        self._depth_display_text = "Arrow depth: " + depth_label
        if self.engines:
            primary = self.engines[self.primary_engine_index]
            if primary.get('active_fen') == self.current_fen:
                self._depth_display_text += f" | Search: {primary.get('current_depth', 0)}"

        if not merged:
            self.arrow_canvas.delete("arrow")
            self.arrow_canvas.delete("premove")
            self._stop_arrow_pulse()
            self._draw_depth_display()
            return

        total_moves = len(merged)
        # Pre-compute scale once for all suggestions (eliminates per-arrow arithmetic churn)
        scales = {}
        if total_moves == 2:
            scales = {1: 0.75, 2: 0.75}
        else:
            for rank_idx in range(1, total_moves + 1):
                scales[rank_idx] = 1.0 - 0.5 * ((rank_idx - 1) / max(total_moves - 1, 1))

        # Draw suggestions into "arrow_next" batch tag. Opening-book entries get the Blue colour-rank (1) with
        # an offset so multiple book lines each occupy distinct ranks.
        brilliant_ranks = self._brilliant_ranks() if self.display_mode == 'others' else set()
        for colour_rank, (orig_rank, moves, score) in enumerate(merged, 1):
            if moves and len(moves[0]) >= 4:
                dt = self._format_decision_time(colour_rank, self.current_depth)
                # Book lines (orig_rank <= 0) show a small annotation instead of decision time
                if orig_rank <= 0:
                    dt = None
                # Stagger each rank's label bar below the previous one so bars
                # of suggestions landing on adjacent squares never overlap.
                color_override = self._suggestion_color_override(orig_rank, moves, brilliant_ranks)
                self.draw_move(moves[0][:2], moves[0][2:4], colour_rank, score, dt,
                               vertical_offset=(colour_rank - 1) * 16,
                               scale=scales.get(colour_rank, 0.75),
                               tag="arrow_next", color_override=color_override)

        # Also draw opponent predicted move (opposite side) as a dark arrow if enabled
        if self.show_opponent_move and hasattr(self, "opponent_move") and self.opponent_move:
            om = self.opponent_move
            if len(om) >= 4:
                self.draw_move(om[:2], om[2:4], 2, None, None, is_opponent=True,
                               vertical_offset=20,
                               scale=0.75,
                               tag="arrow_next")

        # Premoves: when the engine's best line is a mate we deliver, every one
        # of our moves in that line is forced no matter what the opponent
        # replies, so show them as premove arrows.
        self.arrow_canvas.delete("premove")
        self._draw_premoves()

        # Atomic double-buffer swap: delete old arrows and commit new batch tag
        self.arrow_canvas.delete("arrow")
        self.arrow_canvas.addtag_withtag("arrow", "arrow_next")
        self.arrow_canvas.dtag("arrow", "arrow_next")
        self.arrow_canvas.lower("arrow")
        self.canvas.tag_raise("accuracy_overlay")
        self._draw_depth_display()

        if getattr(self, "arrow_animate", False):
            self._start_arrow_pulse()
        else:
            self._stop_arrow_pulse()

    def _start_arrow_pulse(self):
        """Begin a gentle width pulse on the drawn arrows."""
        if getattr(self, "_arrow_pulse_after", None):
            return
        self._arrow_pulse_after = self.root.after(380, self._arrow_pulse_tick)

    def _stop_arrow_pulse(self):
        if getattr(self, "_arrow_pulse_after", None):
            try:
                self.root.after_cancel(self._arrow_pulse_after)
            except Exception:
                pass
            self._arrow_pulse_after = None

    def _arrow_pulse_tick(self):
        self._arrow_pulse_after = None
        if not getattr(self, "arrow_animate", False) or not getattr(self, "_arrow_layer_ready", False):
            return
        widths = getattr(self, "_arrow_pulse_widths", [])
        if widths:
            self._arrow_pulse_state = 1 - getattr(self, "_arrow_pulse_state", 0)
            factor = 1.35 if self._arrow_pulse_state else 1.0
            for item_id, base in widths:
                try:
                    self.arrow_canvas.itemconfigure(item_id, width=base * factor)
                except Exception:
                    pass
        self._arrow_pulse_after = self.root.after(380, self._arrow_pulse_tick)

    def _draw_premoves(self):
        """Draw safe-to-premove arrows and badges on the overlay.

        Two cases (both require the engine's best / rank-1 line):
          1. Forced mate for us ('FM…'): The immediate move gets a PM badge;
             ADDITIONALLY the full mating PV sequence is drawn as numbered
             dashed arrows so the user can see the entire forced sequence and
             premove or predict moves ahead.
          2. A clearly-good capture/trade on the human's turn: the rank-1 score
             is the minimax value — the worst case over ALL opponent replies —
             so a solid positive value means the opponent cannot punish the
             trade, and the first move is marked with a PM1 badge to show it
             can be premoved safely.
        """
        try:
            source = self.multi_pvs if self.multi_pvs else self.committed_multi_pvs
            best = source.get(1)
            if not best or not best[0]:
                return
            moves, display_score = best[0], best[1]
            human_turn = self._is_human_turn()

            is_forced_mate = (isinstance(display_score, str)
                              and display_score.upper().startswith("FM"))
            is_enemy_mate = (isinstance(display_score, str)
                             and display_score.upper().startswith("EM"))

            if is_forced_mate or is_enemy_mate:
                # Draw the full mating sequence as numbered dashed arrows so the
                # user can visualize and predict the forced moves.
                # Our moves (even plies from human's perspective if human_turn,
                # else odd plies): highlighted in a vibrant color.
                # Enemy moves: drawn in a warning orange so both sides are visible.
                self._draw_mate_sequence_arrows(moves, display_score, human_turn)

                if is_forced_mate:
                    if human_turn:
                        # Badge move 0 (our move) as premoveable since we control it.
                        self._draw_premove_badge(moves[0], 1)
                    elif len(moves) > 1 and self._is_obvious_strong_response(moves, display_score):
                        # Opponent is to move; our response is the premove.
                        self._draw_premove_arrow(moves[1], 1)
                return

            if human_turn:
                if self._is_safe_premove_trade(moves, display_score):
                    self._draw_premove_badge(moves[0], 1)
                return

            # Opponent is to move (non-mate): the engine line starts with the
            # opponent's predicted reply, so OUR queued response is the second
            # move of the line.  Only offer it as a PM1 premove when it is a
            # true RECAPTURE (the opponent's predicted move is a capture and we
            # capture back on the very same square) or a promotion — the cases
            # that stay the best move in their line and are harmless otherwise
            # (if the opponent plays something else the premove is simply
            # illegal and never fires).
            if len(moves) > 1 and self._is_reliable_recapture(moves, display_score):
                self._draw_premove_arrow(moves[1], 1)
        except Exception:
            pass

    def _draw_mate_sequence_arrows(self, moves, display_score, human_turn):
        """Draw the forced mating sequence as numbered dashed arrows.

        Visualizes the full PV line from the engine when a forced mate is detected.
        Our moves are drawn in cyan/teal dashes; opponent's predicted moves in
        orange dashes.  Each arrow is numbered so the user knows the order.
        Max 8 plies are drawn to keep the board readable.
        """
        try:
            if not moves or len(moves) < 1:
                return
            # Determine which ply indices are OUR moves vs opponent moves.
            # If it's human's turn: plies 0, 2, 4... are ours; 1, 3, 5... opponent.
            # If opponent's turn: plies 0, 2, 4... are theirs; 1, 3, 5... are ours.
            our_color = "#00D4FF"      # Cyan — our forced mates moves
            opp_color = "#FF8C00"      # Orange — opponent's predicted responses
            is_fm = display_score.upper().startswith("FM")

            width = self.arrow_canvas.winfo_width()
            height = self.arrow_canvas.winfo_height()
            border = 3
            inner_w = width - 2 * border
            inner_h = height - 2 * border

            max_plies = min(len(moves), 8)
            for i in range(max_plies):
                move = moves[i]
                if not isinstance(move, str) or len(move) < 4:
                    break
                # Determine if this ply is our move or the opponent's move
                if human_turn:
                    is_our_move = (i % 2 == 0)
                else:
                    is_our_move = (i % 2 == 1)

                # For FM (we mate): highlight our moves; for EM (enemy mates): highlight enemy moves
                if is_fm:
                    fill_color = our_color if is_our_move else opp_color
                    arrow_width = 6 if is_our_move else 4
                else:  # EM: enemy delivering mate
                    fill_color = opp_color if not is_our_move else our_color
                    arrow_width = 6 if not is_our_move else 4

                start_sq = move[:2]
                end_sq = move[2:4]
                try:
                    sc = ord(start_sq[0]) - ord('a'), 8 - int(start_sq[1])
                    ec = ord(end_sq[0]) - ord('a'), 8 - int(end_sq[1])
                    sc = self.convert_coordinates(sc[0], sc[1])
                    ec = self.convert_coordinates(ec[0], ec[1])
                    sx = border + (sc[0] + 0.5) * inner_w / 8
                    sy = border + (sc[1] + 0.5) * inner_h / 8
                    ex = border + (ec[0] + 0.5) * inner_w / 8
                    ey = border + (ec[1] + 0.5) * inner_h / 8
                    self.arrow_canvas.create_line(
                        sx, sy, ex, ey,
                        fill=fill_color,
                        width=arrow_width,
                        dash=(6, 4),
                        arrow=tk.LAST,
                        arrowshape=(12, 10, 4),
                        tags=("arrow", "premove"),
                        state="disabled",
                        capstyle=tk.ROUND
                    )
                    # Ply number label near destination
                    lbl = str(i + 1)
                    self.arrow_canvas.create_text(
                        ex + 5, ey - 6 + i * 9,
                        text=lbl,
                        fill=fill_color,
                        font=("Segoe UI", 7, "bold"),
                        tags=("arrow", "premove"),
                        state="disabled",
                        anchor="w"
                    )
                except Exception:
                    pass
        except Exception:
            pass

    def _is_safe_premove_trade(self, moves, display_score):
        """True when the best move is a capture/trade whose engine evaluation
        (minimax worst case over the opponent's replies) is clearly good, so it
        stays strong no matter what the opponent plays and can be premoved."""
        if not moves or not isinstance(moves[0], str) or len(moves[0]) < 4:
            return False
        if not self._is_capture(self.current_fen, moves[0]):
            return False
        try:
            return float(display_score) >= 0.30
        except (TypeError, ValueError):
            return False

    def _is_capture(self, fen, move):
        """Return True if `move` (e.g. 'bxc6' as 'b5c6') captures an enemy piece
        in the current FEN, including en passant (empty destination square)."""
        try:
            if not fen or not isinstance(move, str) or len(move) < 4:
                return False
            board = self._parse_fen_board(fen.split()[0])
            if not board:
                return False
            files = "abcdefgh"
            def sq_index(name):
                return (8 - int(name[1])) * 8 + files.index(name[0])
            dst = sq_index(move[2:4])
            target = board.get(dst)
            if target is not None:
                mover = board.get(sq_index(move[:2]))
                if mover is None:
                    return False
                return target.islower() != mover.islower()
            # En passant: destination square is empty but equals the FEN's
            # en-passant target square on a diagonal pawn move.
            parts = fen.split()
            ep = parts[3] if len(parts) > 3 and parts[3] != "-" else None
            return bool(ep and move[2:4] == ep and move[0] != move[2])
        except Exception:
            return False

    def _display_score_value(self, display_score):
        """Human-POV numeric value of a stored display score.

        Numeric labels like '+2.35' are already signed from the human's point of
        view; mate labels map to +/-10 (FM = we deliver mate, EM = we are mated).
        Returns None when the label cannot be parsed.
        """
        s = str(display_score or "").strip()
        if not s:
            return None
        up = s.upper()
        if "M" in up:
            if up.startswith("FM") or (up.startswith("M") and not up.startswith("EM")):
                return 10.0
            return -10.0
        try:
            return float(s)
        except (TypeError, ValueError):
            return None

    def _board_to_placement(self, board):
        """Serialize a square->piece dict back to a FEN board placement string."""
        rows = []
        for r in range(8):
            row = []
            empty = 0
            for f in range(8):
                p = board.get(r * 8 + f)
                if p is None:
                    empty += 1
                else:
                    if empty:
                        row.append(str(empty))
                        empty = 0
                    row.append(p)
            if empty:
                row.append(str(empty))
            rows.append("".join(row))
        return "/".join(rows)

    def _apply_uci_to_board(self, board, move):
        """Return a NEW board dict after applying one UCI move.

        Best-effort: handles piece moves, captures, promotion and en passant
        (castling just moves the king).  Enough for capture/promotion checks.
        """
        if not isinstance(move, str) or len(move) < 4:
            return None
        files = "abcdefgh"
        def idx(name):
            return (8 - int(name[1])) * 8 + files.index(name[0])
        src, dst = idx(move[:2]), idx(move[2:4])
        piece = board.get(src)
        if piece is None:
            return None
        nb = dict(board)
        captured = nb.get(dst)
        nb[dst] = piece
        nb.pop(src, None)
        # En passant: a pawn moving diagonally onto an empty square captures the
        # pawn that sits behind the destination square.
        if len(move) == 4 and captured is None \
                and piece.lower() == "p" and move[0] != move[2]:
            behind = dst + 8 if piece.isupper() else dst - 8
            if nb.get(behind) is not None and nb[behind].lower() == "p":
                nb.pop(behind, None)
        promo = move[4:5] if len(move) >= 5 else None
        if promo:
            nb[dst] = promo.lower() if piece.islower() else promo
        return nb

    def _is_reliable_recapture(self, moves, display_score):
        """True when the queued response to the opponent's PREDICTED move is a
        true recapture: the predicted move is itself a capture and our reply
        captures back on the very same square (or is a promotion).

        This is the only opponent-turn premove that reliably "is the best move
        once the enemy plays": in the predicted line our recapture is the
        engine's mainline reply, and if the enemy plays anything else the
        queued premove is almost always illegal and simply never fires — so it
        can never play a wrong move.  A clearly winning score is still required.
        """
        if not moves or len(moves) < 2:
            return False
        if not isinstance(moves[1], str) or len(moves[1]) < 4:
            return False
        val = self._display_score_value(display_score)
        if val is None or val < 1.00:
            return False
        # Promotion is an obvious winning move in its own right.
        if len(moves[1]) >= 5:
            return True
        try:
            f = (self.current_fen or "")
            parts = f.split()
            board = self._parse_fen_board(parts[0] if parts else "")
            if not board:
                return False

            def _sq_idx(sq):
                return (8 - int(sq[1])) * 8 + (ord(sq[0]) - ord('a'))

            # Same-square recapture: the enemy's predicted move captures the
            # piece we then capture back on the very same square.  We pass the
            # current FEN (not a placement-only string) so an en-passant
            # capture on the enemy's move is recognised too.
            if self._is_capture(f, moves[0]):
                nb = self._apply_uci_to_board(board, moves[0])
                if nb is None:
                    return False
                if not self._is_capture(self._board_to_placement(nb), moves[1]):
                    return False
                return moves[0][2:4] == moves[1][2:4]

            # En-passant recapture: the enemy's predicted move is a double pawn
            # push that lands beside one of our pawns, and our queued reply is
            # a pawn hopping diagonally onto the square the enemy pawn passed
            # through (the en-passant square).  If the enemy plays anything else
            # the ep move is illegal and never fires — just as safe to premove.
            m0 = moves[0]
            m1 = moves[1]
            if (len(m0) == 4 and len(m1) == 4
                    and m0[0] == m0[2] and abs(int(m0[3]) - int(m0[1])) == 2):
                mover = board.get(_sq_idx(m0[:2]))
                if mover == "P" or mover == "p":
                    # EP square is the file of the push, one rank behind the
                    # pushing pawn's starting rank.
                    ep_rank = "3" if m0[1] == "2" else "6"
                    ep_sq = m0[0] + ep_rank
                    if m1[2:4] == ep_sq and m1[0] != m1[2]:
                        # The destination is empty, so the only way for a pawn
                        # to "capture" onto it is en passant.
                        return board.get(ep_sq) is None
            return False
        except Exception:
            return False

    def _is_obvious_strong_response(self, moves, display_score):
        """True when the queued response for us (moves[1], played after the
        opponent's predicted moves[0]) is a very obvious, very good premove.

        Only these are offered on the opponent's turn:
          * the score is clearly winning for us (>= +1.00, or we deliver mate), AND
          * the response is a recapture/winning capture or a promotion — moves
            that stay strong regardless of which legal move the opponent plays.
        """
        if not moves or len(moves) < 2:
            return False
        if not isinstance(moves[1], str) or len(moves[1]) < 4:
            return False
        val = self._display_score_value(display_score)
        if val is None or val < 1.00:
            return False
        # Promotion is an obvious winning move in its own right.
        if len(moves[1]) >= 5:
            return True
        try:
            board = self._parse_fen_board((self.current_fen or "").split()[0])
            if not board:
                return False
            nb = self._apply_uci_to_board(board, moves[0])
            if nb is None:
                return False
            return self._is_capture(self._board_to_placement(nb), moves[1])
        except Exception:
            return False

    def _draw_premove_badge(self, move, n):
        """Draw a small 'PMn' badge on the source square of `move`."""
        try:
            start_sq = move[:2]
            start_col, start_row = ord(start_sq[0]) - ord('a'), 8 - int(start_sq[1])
            start_col, start_row = self.convert_coordinates(start_col, start_row)
            width, height = self.arrow_canvas.winfo_width(), self.arrow_canvas.winfo_height()
            border = 3
            inner_w = width - 2 * border
            inner_h = height - 2 * border
            sx = border + (start_col + 0.5) * inner_w / 8
            sy = border + (start_row + 0.5) * inner_h / 8
            self.arrow_canvas.create_rectangle(sx - 16, sy - 16, sx, sy, fill="#000000",
                                               outline="#000000", tags=("arrow", "premove"),
                                               state="disabled")
            self.arrow_canvas.create_text(sx - 8, sy - 8, text=f"PM{n}", fill="#FFFFFF",
                                          font=("Segoe UI", 7, "bold"),
                                          tags=("arrow", "premove"), state="disabled")
        except Exception:
            pass

    def _draw_premove_arrow(self, move, n):
        """Draw one dashed white premove arrow with a small 'PMn' tag."""
        try:
            start_sq, end_sq = move[:2], move[2:4]
            start_col, start_row = ord(start_sq[0]) - ord('a'), 8 - int(start_sq[1])
            end_col, end_row = ord(end_sq[0]) - ord('a'), 8 - int(end_sq[1])
            start_col, start_row = self.convert_coordinates(start_col, start_row)
            end_col, end_row = self.convert_coordinates(end_col, end_row)
            width, height = self.arrow_canvas.winfo_width(), self.arrow_canvas.winfo_height()
            border = 3
            inner_width = width - (2 * border)
            inner_height = height - (2 * border)
            sx = border + (start_col + 0.5) * inner_width / 8
            sy = border + (start_row + 0.5) * inner_height / 8
            ex = border + (end_col + 0.5) * inner_width / 8
            ey = border + (end_row + 0.5) * inner_height / 8
            self.arrow_canvas.create_line(sx, sy, ex, ey, fill="#000000", width=5,
                                          dash=(5, 3), arrow=tk.LAST,
                                          arrowshape=(14, 11, 5), tags=("arrow", "premove"),
                                          state="disabled", capstyle=tk.ROUND)
            # Small PM tag near the destination, fanned out per premove.
            self.arrow_canvas.create_text(ex + 6, ey - 8 + (n - 1) * 11, text=f"PM{n}",
                                          fill="#000000", font=("Segoe UI", 7, "bold"),
                                          tags=("arrow", "premove"), state="disabled", anchor="w")
        except Exception:
            pass

    def set_display_mode(self, mode):
        self.display_mode = mode
        self.control_panel.update_display_mode_buttons(mode)
        self._refresh_accuracy_selection()

    def set_comparison_player_elo(self, elo):
        """Select the benchmark and refresh it on the visualizer immediately."""
        try:
            elo = int(elo)
        except (TypeError, ValueError):
            return
        if elo not in self.skill_profiles:
            return
        self.comparison_player_elo = elo
        if hasattr(self.control_panel, "player_elo_var"):
            self.control_panel.player_elo_var.set(str(elo))
        self.draw_accuracy_overlay()
        self.update_engine_line_text()

    # ------------------------------------------------------------------
    # Chess Coach: local, rule-based "why is this best" chat panel
    # ------------------------------------------------------------------
    def _build_coach_panel(self):
        """Right-hand chat panel docked beside the board.

        Opaque widgets render fine over the colour-key overlay (same as the
        eval bar), and the panel only floats over the browser window when the
        user keeps it open (✕ collapses it back to the board alone).
        """
        holder = tk.Frame(self.main_frame, bg="#0D0A18", highlightthickness=1,
                          highlightbackground="#1F1535")
        header = tk.Frame(holder, bg="#171022")
        header.pack(fill=tk.X)
        dot = tk.Canvas(header, width=8, height=8, bg="#171022", highlightthickness=0)
        dot.create_oval(1, 1, 7, 7, fill="#22C55E", outline="")
        dot.pack(side=tk.LEFT, padx=(8, 4), pady=8)
        tk.Label(header, text="CHESS COACH", bg="#171022", fg="#8B5CF6",
                 font=("Segoe UI", 7, "bold")).pack(side=tk.LEFT)
        tk.Label(header, text="AI", bg="#171022", fg="#6C5C8C",
                 font=("Segoe UI", 7, "bold")).pack(side=tk.LEFT, padx=(3, 0))
        self.coach_close_btn = tk.Label(header, text="✕", bg="#171022", fg="#6C5C8C",
                                        font=("Segoe UI", 8, "bold"), cursor="hand2")
        self.coach_close_btn.pack(side=tk.RIGHT, padx=(0, 6), pady=6)
        self.coach_close_btn.bind("<Button-1>", lambda _e: self._toggle_coach())

        body = tk.Frame(holder, bg="#0D0A18")
        body.pack(fill=tk.BOTH, expand=True)
        self.chat_text = tk.Text(body, width=22, height=14, bg="#120D22", fg="#F4EEFF",
                                 insertbackground="#8B5CF6", wrap=tk.WORD,
                                 font=("Segoe UI", 9), relief=tk.FLAT,
                                 highlightthickness=0, bd=0,
                                 padx=8, pady=6, selectbackground="#6D48B8",
                                 selectforeground="#F4EEFF")
        self.chat_scroll = tk.Scrollbar(body, orient=tk.VERTICAL,
                                        command=self.chat_text.yview,
                                        bg="#0D0A18", troughcolor="#0D0A18",
                                        activebackground="#352550",
                                        highlightthickness=0, bd=0)
        self.chat_text.config(yscrollcommand=self.chat_scroll.set)
        self.chat_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.chat_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.chat_text.tag_configure("row_coach", foreground="#8B5CF6",
                                     font=("Segoe UI", 8, "bold"))
        self.chat_text.tag_configure("row_you", foreground="#3B82F6",
                                     font=("Segoe UI", 8, "bold"))
        self.chat_text.tag_configure("coach_bubble", background="#211735",
                                     lmargin1=10, lmargin2=14, rmargin=8,
                                     spacing1=6, spacing2=3, spacing3=8)
        self.chat_text.tag_configure("you_bubble", background="#2A1E45",
                                     lmargin1=44, lmargin2=6, rmargin=8,
                                     spacing1=6, spacing2=3, spacing3=8, justify="right")
        self.chat_text.tag_configure("spacer", spacing1=4, spacing3=4)
        self.chat_text.tag_configure("mv_blue", foreground="#1E90FF",
                                     font=("Segoe UI", 9, "bold"))
        self.chat_text.tag_configure("mv_green", foreground="#32CD32",
                                     font=("Segoe UI", 9, "bold"))
        self.chat_text.tag_configure("mv_yellow", foreground="#FFD700",
                                     font=("Segoe UI", 9, "bold"))
        self.chat_text.tag_configure("mv_orange", foreground="#FFA500",
                                     font=("Segoe UI", 9, "bold"))
        self.chat_text.tag_configure("mv_red", foreground="#FF4444",
                                     font=("Segoe UI", 9, "bold"))
        self.chat_text.tag_configure("danger", foreground="#F87171",
                                     font=("Segoe UI", 9, "bold"))
        self.chat_text.tag_configure("muted", foreground="#9A8FB8",
                                     font=("Segoe UI", 8))
        self.chat_text.tag_raise("row_coach")
        self.chat_text.tag_raise("row_you")
        self.chat_text.config(state=tk.DISABLED)

        footer = tk.Frame(holder, bg="#0D0A18")
        footer.pack(fill=tk.X)
        self.chat_entry = tk.Entry(footer, bg="#120D22", fg="#F4EEFF",
                                   insertbackground="#8B5CF6",
                                   relief=tk.FLAT, highlightthickness=1,
                                   highlightbackground="#1F1535",
                                   highlightcolor="#6D48B8",
                                   font=("Segoe UI", 8))
        self.chat_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 2), pady=6)
        ask_btn = tk.Button(footer, text="Ask", command=self._coach_ask,
                            bg="#352550", fg="#8B5CF6", activebackground="#6D48B8",
                            activeforeground="#FFFFFF", relief=tk.FLAT,
                            font=("Segoe UI", 8, "bold"), padx=10, pady=2, cursor="hand2")
        ask_btn.pack(side=tk.RIGHT, padx=(2, 6), pady=6)
        self.chat_entry.bind("<Return>", self._coach_ask)

        self.chat_frame = holder
        self.chat_frame.pack(side=tk.RIGHT, fill=tk.Y, before=self.canvas)
        self._coach_post("Hi! I'll read the engine's analysis and tell you why the "
                         "top move beats the other suggestions.", "coach")

    def _toggle_coach(self):
        self.set_coach_visible(not getattr(self, "coach_visible", True))
        self.root.lift()

    def set_coach_visible(self, visible):
        """Show or hide the docked coach panel to a specific state."""
        self.coach_visible = bool(visible)
        if not hasattr(self, "chat_frame"):
            return
        if self.coach_visible:
            self.chat_frame.pack(side=tk.RIGHT, fill=tk.Y, before=self.canvas)
        else:
            self.chat_frame.pack_forget()
        self._sync_arrow_layer()
        try:
            self.redraw_all_arrows()
        except Exception:
            pass

    def _coach_post(self, text, speaker="coach"):
        if not hasattr(self, "chat_text"):
            return
        try:
            chat = self.chat_text
            chat.config(state=tk.NORMAL)
            ts = time.strftime("%H:%M")
            if speaker == "you":
                chat.insert(tk.END, f"{ts}  ·  You\n", ("row_you",))
                bubble = "you_bubble"
            else:
                chat.insert(tk.END, "♞ Coach  ·  " + ts + "\n", ("row_coach",))
                bubble = "coach_bubble"
            for line in str(text).splitlines():
                chat.insert(tk.END, line + "\n", (bubble,))
            chat.insert(tk.END, "\n", ("spacer",))
            chat.see(tk.END)
            chat.config(state=tk.DISABLED)
        except Exception:
            pass

    def _coach_post_rich(self, lines, speaker="coach"):
        """Insert a bubble where each line is a list of (text, tag) segments.

        `tag` may be None (plain bubble text) or a configured tag name such as
        'mv_blue', 'danger', 'muted'.  Header + inter-line spacing use the same
        style as _coach_post so the two can be mixed freely."""
        if not hasattr(self, "chat_text"):
            return
        try:
            chat = self.chat_text
            chat.config(state=tk.NORMAL)
            ts = time.strftime("%H:%M")
            if speaker == "you":
                chat.insert(tk.END, f"{ts}  ·  You\n", ("row_you",))
                bubble = "you_bubble"
            else:
                chat.insert(tk.END, "♞ Coach  ·  " + ts + "\n", ("row_coach",))
                bubble = "coach_bubble"
            for line in lines:
                chat.insert(tk.END, "\n", (bubble,))
                for text, tag in line:
                    tags = (bubble,) if not tag else (bubble, tag)
                    chat.insert(tk.END, text, tags)
                chat.insert(tk.END, "\n", (bubble,))
            chat.insert(tk.END, "\n", ("spacer",))
            chat.see(tk.END)
            chat.config(state=tk.DISABLED)
        except Exception:
            pass

    def _maybe_coach_refresh(self):
        """Post a fresh explanation when the best line meaningfully changes.

        Debounced so the mid-search flicker of a deep MultiPV search does not
        spam the chat feed with near-identical bubbles."""
        if not getattr(self, "coach_visible", True) or not hasattr(self, "chat_text"):
            return
        if not getattr(getattr(self, "coach_mode", None), "enabled", True):
            return
        source = self.multi_pvs if self.multi_pvs else self.committed_multi_pvs
        best = source.get(1)
        if not best or not best[0]:
            return
        sig = (tuple(best[0][:4]), str(best[1]), str(getattr(self, "current_fen", "")),
               self._is_human_turn())
        if sig == self._coach_last_sig:
            return
        self._coach_last_sig = sig
        if self._coach_after_id:
            try:
                self.root.after_cancel(self._coach_after_id)
            except Exception:
                pass
        self._coach_after_id = self.root.after(1500, self._coach_refresh)

    def _coach_refresh(self):
        self._coach_after_id = None
        try:
            if not hasattr(self, "chat_text") or not getattr(self, "coach_visible", True):
                return
            if not getattr(getattr(self, "coach_mode", None), "enabled", True):
                return
            # Only talk when there is actually a strategy, tactic or danger to
            # explain (both the AI call AND the local fallback note).  Quiet
            # all-equal positions stay silent.
            if not self._coach_is_interesting():
                return
            segments = self._coach_narration_segments()
            if not segments:
                return
            fallback = "\n".join("".join(t for t, _tg in ln) for ln in segments)
            if (getattr(self, "coach_ai_enabled", False)
                    and getattr(self, "coach_groq_key", "")):
                self._coach_ai_ask_async(fallback_text=fallback,
                                         fallback_segments=segments)
            else:
                self._coach_post_rich(segments, "coach")
        except Exception:
            pass

    def _coach_ordinal(self, n):
        return {2: "2nd", 3: "3rd", 4: "4th"}.get(n, f"{n}th")

    def _coach_color_name(self, i):
        return {1: "Blue", 2: "Green", 3: "Yellow", 4: "Orange"}.get(i, "Red")

    def _coach_ranked_narration(self, fen=None):
        """Auto-post: name each drawn suggestion by arrow colour and rank.

        Blue is the engine's best here, Green the second choice, and so on.
        Uses the exact same ordering/cap as the arrows on the board, so the
        colour words always match what is drawn.  Returns the plain-text form
        used as the AI fallback; the live chat uses _coach_narration_segments
        for coloured move names.
        """
        segments = self._coach_narration_segments(fen)
        if not segments:
            return "The engine hasn't produced a stable analysis yet."
        return "\n".join("".join(text for text, _tag in line) for line in segments)

    def _coach_narration_segments(self, fen=None):
        """Build short, intent-aware narration lines with move-colour tags.

        Each line is a list of (text, tag) pairs for _coach_post_rich.  Stays
        brief on purpose: the Blue best move plus any forced-mate / danger
        lines, with a 'basically equal' note only when rank 2 truly ties.
        """
        merged = self._compute_visible_suggestions()
        if not merged:
            return []
        if fen is None:
            fen = getattr(self, "current_fen", "") or ""
        color_tags = ["mv_blue", "mv_green", "mv_yellow", "mv_orange", "mv_red"]
        lines = []
        blue_seen = False
        human_turn = self._is_human_turn()
        for i, (orig_rank, moves, score) in enumerate(merged, 1):
            if not moves or not isinstance(moves[0], str) or len(moves[0]) < 4:
                continue
            name = self._coach_color_name(i)
            tag = color_tags[min(i - 1, len(color_tags) - 1)]
            san = self._coach_move_text(fen, moves[0])
            up = str(score).upper()
            if up.startswith("FM"):
                n = "".join(ch for ch in up if ch.isdigit()) or "?"
                lines.append([(f"{name} ", None), (san, tag),
                              (f" — forced mate in {n}, they can't stop it.", None)])
                blue_seen = blue_seen or i == 1
                continue
            if up.startswith("EM"):
                n = "".join(ch for ch in up if ch.isdigit()) or "?"
                lines.append([(f"{name} ", None), (san, tag),
                              (f" — DANGER: allows mate against you in {n}.", "danger")])
                continue
            if i == 1:
                why = self._coach_why_good(fen, moves[0])
                if human_turn:
                    lines.append([(f"{name} ", None), (san, tag),
                                  (f" is best here ({score}) — {why}.", None)])
                else:
                    # Opponent's move explanation: what their best play threatens.
                    lines.append([("Opponent's best: ", None), (san, tag),
                                  (f" ({score}) — {why}.", None)])
                blue_seen = True
                continue
            if i == 2 and blue_seen:
                bw = self._display_to_white_score(merged[0][2])
                cw = self._display_to_white_score(score)
                if bw is not None and cw is not None and abs(bw - cw) < 0.08:
                    lines.append([(f"{name} ", None), (san, tag),
                                  (" is basically equal — pick by style.", None)])
        if not lines:
            lines.append([("I only have the single top line to work with.", "muted")])
        return lines

    def _coach_is_interesting(self):
        """Gate auto-narration to positions that actually deserve a comment.

        Quiet, all-equal positions are skipped so the coach does not talk over
        every single move — only forced mates, immediate danger, a clearly
        standout best move, a winning capture / trade, or a big swing in the
        evaluation (on either turn) get narrated.  On the opponent's turn the
        same signals describe the opponent's threat, so the opponent's move
        gets explained too.
        """
        try:
            merged = self._compute_visible_suggestions()
            if not merged:
                return False
            fen = getattr(self, "current_fen", "") or ""
            us = self._human_color()
            human_turn = self._is_human_turn()
            whites = []
            for orig_rank, moves, score in merged:
                if not moves or not isinstance(moves[0], str) or len(moves[0]) < 4:
                    continue
                up = str(score).upper()
                if up.startswith("FM") or up.startswith("EM"):
                    return True
                whites.append(self._display_to_white_score(score))
            if not whites or whites[0] is None:
                return False
            best = whites[0]
            best_h = best if us == "w" else -best
            best_moves = merged[0][1]

            # A capture / trade in the best line that clearly changes the result
            # for the side to move: we win something, or the opponent threatens
            # to take something from us.
            if best_moves and self._is_capture(fen, best_moves[0]):
                if human_turn and best_h >= 0.30:
                    return True
                if not human_turn and best_h <= -0.30:
                    return True

            # A move clearly better than the alternatives: the side to move has
            # a standout plan / tactic worth pointing out (either turn).
            if len(whites) >= 2:
                second = next((w for w in whites[1:] if w is not None), None)
                if second is not None and abs(best - second) >= 0.50:
                    return True

            # The evaluation swung a lot vs the previous narrated position:
            # something concrete just happened (a blunder, a won piece...).
            prev = getattr(self, "_last_coach_best_white", None)
            swing = (abs(best_h - prev) >= 1.50) if prev is not None else False
            self._last_coach_best_white = best_h
            return bool(swing)
        except Exception:
            return False

    def _coach_why_good(self, fen, move):
        if not getattr(getattr(self, "coach_mode", None), "show_tips", True):
            return ""
        try:
            intent = self._coach_intent(fen, move)
            if intent:
                return intent
            if self._is_capture(fen, move):
                return f"it grabs material on {move[2:4]} right away"
            if len(move) >= 5 and move[4].lower() in "qrbn":
                return "it promotes a pawn"
            if move in ("e1g1", "e1c1", "e8g8", "e8c8"):
                return "it castles the king to safety"
            if self._coach_develops(move):
                return "it develops a piece toward the centre"
            if move[0] in "de" and move[1] in "45":
                return "it claims central space"
            return "the engine's top pick in this position"
        except Exception:
            return "the engine's top pick in this position"

    def _coach_intent(self, fen, move):
        """Describe what the move is really for: a winning capture, a piece it
        now pressures / attacks, or a defensive move.

        Returns a short phrase like 'it wins the pawn on d5' or 'it pressures
        the bishop on c6', or None when no concrete threat is found.
        """
        try:
            board = self._parse_fen_board(fen.split()[0]) if fen else {}
            if not board:
                return None
            newb, piece, _src, dst = self._apply_uci_move(board, move, fen)
            if piece is None:
                return None
            opponent_lower = piece.isupper()
            attacked = self._coach_attacks_from(piece, dst, newb)
            targets = []
            for sq in attacked:
                p = newb.get(sq)
                if p is not None and opponent_lower == p.islower():
                    targets.append((sq, p))
            if not targets:
                return None
            values = {"q": 9, "r": 5, "b": 3, "n": 3, "p": 1, "k": 100}
            targets.sort(key=lambda t: values.get(t[1].lower(), 0), reverse=True)
            sq, target = targets[0]
            tname = self._coach_piece_name(target)
            tsq = self._squares_to_uci(sq, sq)[:2]
            if target.lower() == "k":
                return f"it puts the king in check on {tsq}"
            if self._coach_square_defended(newb, sq, opponent_lower):
                our_v = values.get(piece.lower(), 0)
                their_v = values.get(target.lower(), 0)
                if their_v > our_v:
                    return f"it attacks the {tname} on {tsq}"
                return f"it pressures the {tname} on {tsq}"
            return f"it wins the {tname} on {tsq}"
        except Exception:
            return None

    def _coach_piece_name(self, piece):
        return {"p": "pawn", "n": "knight", "b": "bishop",
                "r": "rook", "q": "queen", "k": "king"}.get(piece.lower(), "piece")

    def _apply_uci_move(self, board, move, fen=None):
        """Return (new_board, moved_piece, src_sq, dst_sq) after applying UCI."""
        files = "abcdefgh"
        src = (8 - int(move[1])) * 8 + files.index(move[0])
        dst = (8 - int(move[3])) * 8 + files.index(move[2])
        piece = board.get(src)
        if piece is None:
            return board, None, src, dst
        newb = dict(board)
        newb.pop(src, None)
        captured = None
        if dst in newb:
            captured = newb.pop(dst)
        elif piece.lower() == "p" and move[0] != move[2] and dst not in board:
            cap_sq = dst - 8 if piece.islower() else dst + 8
            if fen:
                parts = fen.split()
                ep = parts[3] if len(parts) > 3 and parts[3] != "-" else None
                if ep and move[2:4] == ep:
                    captured = newb.pop(cap_sq, None)
        if piece.lower() == "k" and abs(dst - src) == 2:
            if dst > src:
                newb.pop(src + 3, None)
                newb[src + 1] = "r" if piece.isupper() else "R"
            else:
                newb.pop(src - 4, None)
                newb[src - 1] = "r" if piece.isupper() else "R"
        if len(move) >= 5 and move[4].lower() in "qrbn":
            piece = move[4] if piece.isupper() else move[4].lower()
        newb[dst] = piece
        return newb, piece, src, dst

    def _coach_attacks_from(self, piece_char, sq, board):
        """Pseudo-legal attacked squares for the piece at sq (rays stop at the
        first blocker, which is all the intent text needs)."""
        res = set()
        fr, fc = divmod(sq, 8)
        up = piece_char.isupper()
        piece = piece_char.lower()
        if piece == "n":
            for dr, dc in ((2, 1), (2, -1), (-2, 1), (-2, -1),
                           (1, 2), (1, -2), (-1, 2), (-1, -2)):
                nr, nc = fr + dr, fc + dc
                if 0 <= nr < 8 and 0 <= nc < 8:
                    res.add(nr * 8 + nc)
        elif piece == "k":
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == dc == 0:
                        continue
                    nr, nc = fr + dr, fc + dc
                    if 0 <= nr < 8 and 0 <= nc < 8:
                        res.add(nr * 8 + nc)
        elif piece in ("b", "r", "q"):
            dirs = {"b": ((-1, -1), (-1, 1), (1, -1), (1, 1)),
                    "r": ((-1, 0), (1, 0), (0, -1), (0, 1)),
                    "q": ((-1, -1), (-1, 1), (1, -1), (1, 1),
                          (-1, 0), (1, 0), (0, -1), (0, 1))}[piece]
            for dr, dc in dirs:
                nr, nc = fr + dr, fc + dc
                while 0 <= nr < 8 and 0 <= nc < 8:
                    res.add(nr * 8 + nc)
                    if board.get(nr * 8 + nc) is not None:
                        break
                    nr, nc = nr + dr, nc + dc
        elif piece == "p":
            row_step = -1 if up else 1
            for dc in (-1, 1):
                nr, nc = fr + row_step, fc + dc
                if 0 <= nr < 8 and 0 <= nc < 8:
                    res.add(nr * 8 + nc)
        return res

    def _coach_square_defended(self, board, sq, opponent_lower):
        """True when any piece of the given side attacks square sq."""
        for p_sq, p in board.items():
            if p is None or opponent_lower != p.islower():
                continue
            if sq in self._coach_attacks_from(p, p_sq, board):
                return True
        return False

    def _coach_why_rank(self, fen, merged, i):
        try:
            best, cur = merged[0], merged[i - 1]
            bw = self._display_to_white_score(best[2])
            cw = self._display_to_white_score(cur[2])
            if bw is not None and cw is not None:
                d = bw - cw
                if abs(d) < 0.08:
                    return "basically equal to the blue move — pick by style"
                return (f"trails the blue move by {d:+.2f}: "
                        f"{self._coach_why_better(fen, best[1][0], cur[1][0])}")
        except Exception:
            pass
        return "the engine rates it weaker than the move ahead"

    def _generate_coach_explanation(self, multi_pvs=None, fen=None):
        """Rule-based 'why' text from the current MultiPV analysis.

        Rank 1 is the engine's best move.  Each alternative is compared to it
        using the white-centred score delta (noise floor ~0.08 pawns) plus a
        few cheap move features (capture, promotion, castling, development).
        """
        if multi_pvs is None:
            multi_pvs = self.multi_pvs if self.multi_pvs else self.committed_multi_pvs
        if fen is None:
            fen = getattr(self, "current_fen", "") or ""
        best = multi_pvs.get(1)
        if not best or not best[0] or not isinstance(best[0][0], str) or len(best[0][0]) < 4:
            return "The engine hasn't produced a stable analysis yet."
        best_score = best[1]
        b_san = self._coach_move_text(fen, best[0][0])
        up = str(best_score).upper()
        lines = []
        if up.startswith("FM"):
            n = "".join(ch for ch in up if ch.isdigit()) or "?"
            lines.append(f"{b_san} is forced mate in {n} — the opponent has no saving reply.")
        elif up.startswith("EM"):
            n = "".join(ch for ch in up if ch.isdigit()) or "?"
            lines.append(f"{b_san} allows checkmate against you in {n}. Play something else!")
        else:
            lines.append(f"Top move: {b_san} → {best_score} for you.")
        best_w = self._display_to_white_score(best_score)
        ranked = sorted((r, d) for r, d in multi_pvs.items()
                        if r != 1 and d and d[0] and isinstance(d[0][0], str) and len(d[0][0]) >= 4)
        for _r, alt in ranked:
            a_score = alt[1]
            a_san = self._coach_move_text(fen, alt[0][0])
            a_up = str(a_score).upper()
            if a_up.startswith("EM"):
                n = "".join(ch for ch in a_up if ch.isdigit()) or "?"
                lines.append(f"{a_san} would let the opponent mate in {n}.")
                continue
            aw = self._display_to_white_score(a_score)
            if best_w is None or aw is None:
                continue
            delta = best_w - aw
            if abs(delta) < 0.08:
                lines.append(f"{a_san} ({a_score}) is basically equal — pick by style.")
                continue
            why = self._coach_why_better(fen, best[0][0], alt[0][0])
            lines.append(f"{a_san} ({a_score}) trails by {delta:+.2f}: {why}.")
        if not lines:
            return "I only have the single top line to work with."
        return "\n".join(lines)

    def _coach_why_better(self, fen, best_move, alt_move):
        try:
            if self._is_capture(fen, best_move) and not self._is_capture(fen, alt_move):
                return f"it grabs material on {best_move[2:4]} right away"
            if not self._is_capture(fen, best_move) and self._is_capture(fen, alt_move):
                return "the capture lets the opponent recapture favourably"
            if len(best_move) >= 5 and best_move[4].lower() in "qrbn":
                return "it promotes a pawn"
            if best_move in ("e1g1", "e1c1", "e8g8", "e8c8"):
                return "it castles the king to safety"
            if alt_move in ("e1g1", "e1c1", "e8g8", "e8c8"):
                return "castling loses a tempo the opponent exploits"
            if self._coach_develops(best_move) and not self._coach_develops(alt_move):
                return "it develops a piece toward the centre"
            return "the engine sees a deeper tactical edge"
        except Exception:
            return "the engine sees a deeper tactical edge"

    def _coach_develops(self, move):
        try:
            f, r = ord(move[0]) - ord('a'), int(move[1])
            return (f, r) in ((1, 1), (6, 1), (2, 1), (5, 1),
                              (1, 8), (6, 8), (2, 8), (5, 8))
        except Exception:
            return False

    def _coach_move_text(self, fen, move):
        """Convert a UCI move into a short readable form (O-O, exd5, Nf3, e8=Q)."""
        try:
            if not isinstance(move, str) or len(move) < 4:
                return str(move)
            if move in ("e1g1", "e8g8"):
                return "O-O"
            if move in ("e1c1", "e8c8"):
                return "O-O-O"
            dst = move[2:4]
            if len(move) >= 5 and move[4].lower() in "qrbn":
                return f"{dst}={move[4].upper()}"
            board = self._parse_fen_board(fen.split()[0]) if fen else {}
            if not board:
                return dst
            files = "abcdefgh"
            sq = (8 - int(move[1])) * 8 + files.index(move[0])
            piece = board.get(sq)
            if piece is None:
                return dst
            if piece.lower() == "p":
                return f"{move[0]}x{dst}" if self._is_capture(fen, move) else dst
            letter = piece.upper()
            return f"{letter}x{dst}" if self._is_capture(fen, move) else f"{letter}{dst}"
        except Exception:
            return move

    def _coach_ask(self, event=None):
        entry = getattr(self, "chat_entry", None)
        if entry is None:
            return "break"
        q = entry.get().strip()
        if not q:
            return "break"
        entry.delete(0, tk.END)
        self._coach_post(q, "you")
        if (getattr(self, "coach_ai_enabled", False)
                and getattr(self, "coach_groq_key", "")):
            self._coach_ai_ask_async(question=q,
                                     fallback_text=self._coach_answer(q))
        else:
            self._coach_post(self._coach_answer(q), "coach")
        return "break"

    def _coach_answer(self, q):
        ql = " ".join(q.lower().split())
        if any(k in ql for k in ("why", "explain", "reason", "best")):
            return self._generate_coach_explanation()
        if any(k in ql for k in ("compare", "worse", "better", "instead", "other")):
            source = self.multi_pvs if self.multi_pvs else self.committed_multi_pvs
            best, alt = source.get(1), source.get(2)
            if best and best[0] and alt and alt[0]:
                b = self._coach_move_text(self.current_fen, best[0][0])
                a = self._coach_move_text(self.current_fen, alt[0][0])
                bw = self._display_to_white_score(best[1])
                aw = self._display_to_white_score(alt[1])
                if bw is not None and aw is not None:
                    d = bw - aw
                    if abs(d) < 0.08:
                        return f"{b} ({best[1]}) vs {a} ({alt[1]}): basically equal."
                    return f"{b} ({best[1]}) vs {a} ({alt[1]}): {b} is {d:+.2f} pawns better."
            return self._generate_coach_explanation()
        if "mate" in ql:
            source = self.multi_pvs if self.multi_pvs else self.committed_multi_pvs
            for _r, d in source.items():
                if d and str(d[1]).upper().startswith("FM"):
                    n = "".join(ch for ch in str(d[1]).upper() if ch.isdigit()) or "?"
                    return f"Yes — {self._coach_move_text(self.current_fen, d[0][0])} forces mate in {n}."
                if d and str(d[1]).upper().startswith("EM"):
                    n = "".join(ch for ch in str(d[1]).upper() if ch.isdigit()) or "?"
                    return f"Watch out — {self._coach_move_text(self.current_fen, d[0][0])} allows mate in {n}."
            return "No forced mate shows up in the analysed lines."
        if any(k in ql for k in ("score", "eval", "winning", "position")):
            source = self.multi_pvs if self.multi_pvs else self.committed_multi_pvs
            best = source.get(1)
            if best and best[0]:
                return (f"Current eval: {best[1]} from your side "
                        f"(top move {self._coach_move_text(self.current_fen, best[0][0])}).")
            return "No stable analysis yet."
        if ql in ("hi", "hello", "hey", "help", "what can you do"):
            mode = "an AI (Groq) model" if getattr(self, "coach_ai_enabled", False) else "the engine analysis"
            return (f"Right now I'm using {mode}. Ask me: 'why', 'best', 'compare', "
                    "'mate', 'score'. When AI is off I only read the engine's MultiPV "
                    "analysis locally.")
        return ("I only know the engine analysis right now. Try: 'why is that best?', "
                "'compare', 'is there a mate?', 'what's the score?'")

    # ------------------------------------------------------------------
    # Groq AI coach (optional): free OpenAI-compatible endpoint, used when a
    # key is configured.  Falls back to the local rule-based narration on any
    # failure so the chat never goes silent.
    # ------------------------------------------------------------------
    def _coach_settings_path(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "coach_settings.json")

    def _load_coach_settings(self):
        self.coach_ai_enabled = False
        self.coach_groq_key = ""
        self.coach_model = "llama-3.3-70b-versatile"
        try:
            with open(self._coach_settings_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            self.coach_ai_enabled = bool(data.get("enabled", False))
            self.coach_groq_key = str(data.get("key", "") or "").strip()
            self.coach_model = str(data.get("model", "") or "").strip() \
                or "llama-3.3-70b-versatile"
        except Exception:
            pass

    def _save_coach_settings(self):
        try:
            with open(self._coach_settings_path(), "w", encoding="utf-8") as f:
                json.dump({"enabled": bool(getattr(self, "coach_ai_enabled", False)),
                           "key": str(getattr(self, "coach_groq_key", "")),
                           "model": str(getattr(self, "coach_model", ""))}, f)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Persisted engine/resource settings (threads, hash, UCI options, depth,
    # MultiPV, side).  Saved on every change and again on shutdown so the
    # panel always comes back exactly as the user left it.
    # ------------------------------------------------------------------
    def _engine_settings_path(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "engine_settings.json")

    def _load_engine_settings(self):
        try:
            with open(self._engine_settings_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        try:
            threads = int(data.get("threads", 0) or 0)
            cap = int(getattr(self, "engine_threads_max", 0) or 128)
            # Safety net: never restore a thread count above the logical CPU
            # count. Oversubscription with a big hash table is what froze the
            # engine; the user can still raise it again in the panel.
            logical = int((self.hardware_profile or {}).get("logical_cpus", 0) or 0)
            if logical > 0:
                cap = max(1, min(cap, logical))
            if 1 <= threads <= cap:
                self.engine_threads = threads
        except (TypeError, ValueError):
            pass
        try:
            hash_mb = int(data.get("hash_mb", 0) or 0)
            cap = int(getattr(self, "engine_hash_max", 0) or 65536)
            # Safety net: cap the restored hash at ~1/8 of RAM so an oversized
            # saved value can't re-trigger the swap/freeze that killed the app.
            ram = int((self.hardware_profile or {}).get("ram_total_mb", 0) or 0)
            if ram > 0:
                safe = max(256, int(ram / 8 / 16) * 16)
                cap = min(cap, safe)
            hash_mb = int(hash_mb / 16) * 16
            if 16 <= hash_mb <= cap:
                self.engine_hash = hash_mb
        except (TypeError, ValueError):
            pass
        try:
            elo = int(data.get("uci_elo", 0) or 0)
            self.engine_uci_elo = max(0, min(3500, elo))
        except (TypeError, ValueError):
            pass
        try:
            cont = int(data.get("contempt", 24))
            self.engine_contempt = max(-200, min(200, cont))
        except (TypeError, ValueError):
            pass
        syz = str(data.get("syzygy_path", "") or "").strip()
        if syz:
            self.engine_syzygy_path = syz
        try:
            depth = int(data.get("depth", 0) or 0)
            if 1 <= depth <= 50:
                self.analysis_depth = depth
        except (TypeError, ValueError):
            pass
        try:
            elo_preset = int(data.get("elo", 0) or 0)
            if 300 <= elo_preset <= 3000:
                self.engine_elo = elo_preset
        except (TypeError, ValueError):
            pass
        try:
            mpv = int(data.get("multi_pv", 0) or 0)
            if 1 <= mpv <= 8:
                self.multi_pv_count = mpv
        except (TypeError, ValueError):
            pass
        persp = str(data.get("perspective", "") or "")
        if persp in ("white", "black"):
            self.perspective = persp
            self.player_color = "w" if persp == "white" else "b"
        # Restore ranking mode (BetterMint feature)
        rank_mode = str(data.get("ranking_mode", "priority") or "priority")
        if rank_mode in EnginePool.RANKING_MODES:
            self.ranking_mode = rank_mode
            if hasattr(self, "pool"):
                self.pool.ranking_mode = rank_mode

    def _save_engine_settings(self):
        try:
            with open(self._engine_settings_path(), "w", encoding="utf-8") as f:
                json.dump({
            "analysis_time_ms": self.analysis_time_ms,
            "analysis_nodes": self.analysis_nodes,
            "only_own_turn": self.only_own_turn,
            "threads": int(getattr(self, "engine_threads", 1) or 1),
                    "hash_mb": int(getattr(self, "engine_hash", 16) or 16),
                    "uci_elo": int(getattr(self, "engine_uci_elo", 0) or 0),
                    "contempt": int(getattr(self, "engine_contempt", 24)),
                    "syzygy_path": str(getattr(self, "engine_syzygy_path", "") or ""),
                    "depth": int(getattr(self, "analysis_depth", 18) or 18),
                    "elo": int(getattr(self, "engine_elo", 2000) or 2000),
                    "multi_pv": int(getattr(self, "multi_pv_count", 4) or 4),
                    "perspective": str(getattr(self, "perspective", "white") or "white"),
                    "ranking_mode": str(getattr(self, "ranking_mode", "priority") or "priority"),
                }, f)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Unified autosaved settings (settings.json).  Covers every control
    # panel option — engine resources, coach, visuals, humanizer, book,
    # accuracy targets, blunder filter, rotation, privacy — so the app
    # always comes back exactly as the user left it.  Saved on every
    # relevant change and by a periodic sweep, and once more on shutdown.
    # ------------------------------------------------------------------
    def _settings_path(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "settings.json")

    def _save_all_settings(self):
        # Tk-backed geometry must be collected on the GUI thread.
        if threading.current_thread() is not threading.main_thread():
            self.ui_queue.put(("save_settings",))
            return
        try:
            self._settings_writer.submit(self._collect_settings())
        except Exception as exc:
            print(f"Settings save error: {exc}")


    def _collect_settings(self):
        # Engine / resource keys reuse the legacy engine_settings.json names.
        data = {
            "context_auto": self.context_auto,
            "manual_player_rating": self.manual_player_rating,
            "manual_opponent_rating": self.manual_opponent_rating,
            "analysis_time_ms": self.analysis_time_ms,
            "analysis_nodes": self.analysis_nodes,
            "only_own_turn": self.only_own_turn,
            "threads": int(getattr(self, "engine_threads", 1) or 1),
            "hash_mb": int(getattr(self, "engine_hash", 16) or 16),
            "uci_elo": int(getattr(self, "engine_uci_elo", 0) or 0),
            "contempt": int(getattr(self, "engine_contempt", 24)),
            "syzygy_path": str(getattr(self, "engine_syzygy_path", "") or ""),
            "depth": int(getattr(self, "analysis_depth", 18) or 18),
            "elo": int(getattr(self, "engine_elo", 2000) or 2000),
            "multi_pv": int(getattr(self, "multi_pv_count", 4) or 4),
            "perspective": str(getattr(self, "perspective", "white") or "white"),
            "ranking_mode": str(getattr(self, "ranking_mode", "priority") or "priority"),
            # Coach (legacy coach_settings.json names).
            "enabled": bool(getattr(self, "coach_ai_enabled", False)),
            "key": str(getattr(self, "coach_groq_key", "") or ""),
            "model": str(getattr(self, "coach_model", "llama-3.3-70b-versatile") or ""),
            # Visuals tab.
            "eval_bar_visible": bool(getattr(self, "eval_bar_visible", True)),
            "grid_visible": bool(getattr(self, "grid_visible", True)),
            "depth_display_visible": bool(getattr(self, "depth_display_visible", False)),
            "arrow_scale": float(getattr(self, "arrow_scale", 1.0) or 1.0),
            "arrow_opacity": float(getattr(self, "arrow_opacity", 1.0) or 1.0),
            "arrow_dash": bool(getattr(self, "arrow_dash", False)),
            "arrow_animate": bool(getattr(self, "arrow_animate", False)),
            "display_mode": str(getattr(self, "display_mode", "pv") or "pv"),
            "coach_visible": bool(getattr(self, "coach_visible", True)),
            "external_eval_visible": bool(
                getattr(self, "external_eval", None) is not None
                and getattr(self.external_eval, "is_visible", lambda: False)()),
            # Accuracy / recommendation targeting.
            "accuracy_display_enabled": bool(
                getattr(self, "accuracy_display_enabled", True)),
            "recommendation_accuracy_enabled": bool(
                getattr(self, "recommendation_accuracy_enabled", True)),
            "recommendation_policy_version": 2,
            "recommendation_strength_first": bool(
                getattr(self, "recommendation_strength_first", False)),
            "recommendation_accuracy_min": int(
                getattr(self, "target_recommendation_accuracy_min", 70) or 70),
            "recommendation_accuracy_max": int(
                getattr(self, "target_recommendation_accuracy_max", 80) or 80),
            "recommendation_accuracy_priority": int(
                getattr(self, "target_recommendation_accuracy_priority", 75) or 75),
            'accuracy_candidate_limit': self.accuracy_candidate_limit,
            'anti_draw_enabled': self.anti_draw_enabled,
            'anti_draw_threshold': self.anti_draw_threshold,
            'anti_draw_min_advantage': self.anti_draw_min_advantage,
            # Analysis / humanizer / blunder filter.
            "opponent_elo": int(getattr(self, "opponent_elo", 1600) or 1600),
            "show_opponent_move": bool(getattr(self, "show_opponent_move", True)),
            "aggressive_blunder_filter_enabled": bool(
                getattr(self, "aggressive_blunder_filter_enabled", True)),
            "aggressive_blunder_threshold": float(
                getattr(self, "aggressive_blunder_threshold", 0.50)),
            "dynamic_depth_enabled": bool(
                getattr(self, "dynamic_depth_enabled", False)),
            "refresh_speed": int(getattr(self, "gui_update_delay", 100) or 100),
            "humanizer_enabled": bool(getattr(self.humanizer, "enabled", True)),
            "humanizer_preset": str(getattr(self.humanizer, "preset", "intermediate") or ""),
            "humanizer_mean_ms": int(getattr(self.humanizer, "mean_ms", 3200) or 3200),
            "humanizer_blunder_chance": float(
                getattr(self.humanizer, "blunder_chance", 0.08)),
            "humanizer_pool": int(
                getattr(self.humanizer, "rank_pool_size", 3) or 3),
            # AutoMove — human-like mouse-motion simulation.
            "automove_enabled": bool(getattr(self.automove, "enabled", False)),
            "automove_think_min_ms": int(
                getattr(self.automove, "think_min_ms", 3000)),
            "automove_think_max_ms": int(
                getattr(self.automove, "think_max_ms", 15000)),
            "automove_think_complex_min_ms": int(
                getattr(self.automove, "think_complex_min_ms", 20000) or 20000),
            "automove_think_complex_max_ms": int(
                getattr(self.automove, "think_complex_max_ms", 30000) or 30000),
            "automove_complex_enabled": bool(
                getattr(self.automove, "complex_enabled", True)),
            "automove_event_delay_min_ms": int(
                getattr(self.automove, "event_delay_min_ms", 50) or 50),
            "automove_event_delay_max_ms": int(
                getattr(self.automove, "event_delay_max_ms", 200) or 200),
            "automove_curvature": float(
                getattr(self.automove, "curvature", 0.45)),
            "automove_micro_movement": float(
                getattr(self.automove, "micro_movement", 0.40)),
            "automove_hesitation": float(
                getattr(self.automove, "hesitation", 0.50)),
            "automove_speed_variation": float(
                getattr(self.automove, "speed_variation", 0.60)),
            "automove_path_speed": float(
                getattr(self.automove, "path_speed", 1.0) or 1.0),
            "automove_board_calibration": (
                getattr(self, "_board_calibration", None)
                if isinstance(getattr(self, "_board_calibration", None), dict)
                else None),
            "automove_debug_geometry": bool(
                getattr(self, "_automove_debug_geometry", False)),
            "auto_queue_enabled": bool(
                getattr(self, "_auto_queue_enabled", False)),
            "auto_queue_delay_s": float(
                getattr(self, "_auto_queue_delay_s", 3.0) or 3.0),
            # Opening book / cloud / tablebases.
            "book_cloud_enabled": bool(
                getattr(self.book_manager, "cloud_enabled", True)),
            "book_cloud_source": str(
                getattr(self.book_manager, "cloud_source", "masters") or "masters"),
            "book_lichess_token": str(
                getattr(self.book_manager, "lichess_token", "") or ""),
            "book_tb_enabled": bool(getattr(self.book_manager, "tb_enabled", True)),
            "book_stage_show": bool(getattr(self.book_manager, "stage_show", True)),
            # Coach mode sub-settings.
            "coach_mode_enabled": bool(getattr(self.coach_mode, "enabled", False)),
            "coach_mode_tips": bool(getattr(self.coach_mode, "show_tips", True)),
            "coach_mode_suggest": bool(
                getattr(self.coach_mode, "suggest_better", True)),
            "coach_mode_running_acc": bool(
                getattr(self.coach_mode, "show_running_accuracy", True)),
            "coach_mode_tts": bool(getattr(self.coach_mode, "tts_enabled", False)),
            # Engine rotation + privacy.
            "engine_rotation_enabled": bool(
                getattr(self, "engine_rotation_enabled", False)),
            "rotation_count": int(
                getattr(self, "rotation_engine_count", 1) or 1),
            "hosted_engine_base": str(
                getattr(self, "hosted_engine_base", "")
                or "wss://ProtonnDev-engine.hf.space"),
            "stealth_mode_enabled": bool(
                getattr(self, "stealth_mode_enabled", False)),
            # Compute mode + GPU options
            "compute_mode": str(getattr(self, "compute_mode", "cpu") or "cpu"),
            "gpu_engine_options": dict(
                getattr(self, "gpu_engine_options", {}) or {}),
            "engine_paths": list(getattr(self, "engine_paths", []) or []),
            # Window geometry (size + position) so the layout comes back
            # exactly as the user left it.
            "panel_geometry": self._panel_geometry(),
            "eval_geometry": self._eval_geometry(),
        }

        return data

    def _panel_geometry(self):
        try:
            panel = getattr(self, "control_panel", None)
            if panel is None or not panel.root.winfo_exists():
                return None
            return panel.root.geometry()
        except Exception:
            return None

    def _eval_geometry(self):
        try:
            ew = getattr(self, "external_eval", None)
            if ew is None or not getattr(ew, "is_visible", lambda: False)():
                return None
            return ew.root.geometry()
        except Exception:
            return None

    def _clamp_geometry(self, geometry):
        """Clamp a 'WxH+X+Y' geometry string so the window stays on-screen."""
        try:
            import re as _re
            m = _re.match(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", geometry)
            if not m:
                return geometry
            w, h, xs, ys = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            x = max(0, min(xs, max(0, sw - w)))
            y = max(0, min(ys, max(0, sh - h)))
            return f"{w}x{h}+{x}+{y}"
        except Exception:
            return geometry

    def _load_all_settings(self):
        try:
            with open(self._settings_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            # First run / legacy: keep the older per-file settings.
            self._load_engine_settings()
            self._load_coach_settings()
            return
        if not isinstance(data, dict):
            self._load_engine_settings()
            self._load_coach_settings()
            return
        self._apply_settings_data(data)

    def _apply_settings_data(self, data):
        self.context_auto=bool(data.get('context_auto',True))
        for name in ('manual_player_rating','manual_opponent_rating'):
            try:
                setattr(self,name,max(0,min(4000,int(data.get(name,0)))))
            except (ValueError,TypeError):
                setattr(self,name,0)
        """Restore every persisted setting onto the live objects."""
        try:
            self.analysis_time_ms = max(0, min(600000, int(data.get('analysis_time_ms', 0))))
            self.analysis_nodes = max(0, int(data.get('analysis_nodes', 0)))
        except (TypeError, ValueError):
            self.analysis_time_ms = self.analysis_nodes = 0
        self.only_own_turn = bool(data.get('only_own_turn', False))
        try:
            threads = int(data.get("threads", 0) or 0)
            cap = int(getattr(self, "engine_threads_max", 0) or 128)
            # Safety net: never restore a thread count above the logical CPU
            # count. Oversubscription with a big hash table is what froze the
            # engine; the user can still raise it again in the panel.
            logical = int((self.hardware_profile or {}).get("logical_cpus", 0) or 0)
            if logical > 0:
                cap = max(1, min(cap, logical))
            if 1 <= threads <= cap:
                self.engine_threads = threads
        except (TypeError, ValueError):
            pass
        try:
            hash_mb = int(data.get("hash_mb", 0) or 0)
            cap = int(getattr(self, "engine_hash_max", 0) or 65536)
            # Safety net: cap the restored hash at ~1/8 of RAM so an oversized
            # saved value can't re-trigger the swap/freeze that killed the app.
            ram = int((self.hardware_profile or {}).get("ram_total_mb", 0) or 0)
            if ram > 0:
                safe = max(256, int(ram / 8 / 16) * 16)
                cap = min(cap, safe)
            hash_mb = int(hash_mb / 16) * 16
            if 16 <= hash_mb <= cap:
                self.engine_hash = hash_mb
        except (TypeError, ValueError):
            pass
        try:
            self.engine_uci_elo = max(0, min(3500, int(data.get("uci_elo", 0) or 0)))
        except (TypeError, ValueError):
            pass
        try:
            self.engine_contempt = max(-200, min(200, int(data.get("contempt", 24))))
        except (TypeError, ValueError):
            pass
        syz = str(data.get("syzygy_path", "") or "").strip()
        if syz:
            self.engine_syzygy_path = syz
        try:
            depth = int(data.get("depth", 0) or 0)
            if 1 <= depth <= 50:
                self.analysis_depth = depth
        except (TypeError, ValueError):
            pass
        try:
            elo = int(data.get("elo", 0) or 0)
            if 300 <= elo <= 3000:
                self.engine_elo = elo
        except (TypeError, ValueError):
            pass
        try:
            mpv = int(data.get("multi_pv", 0) or 0)
            if 1 <= mpv <= 8:
                self.multi_pv_count = mpv
        except (TypeError, ValueError):
            pass
        persp = str(data.get("perspective", "") or "")
        if persp in ("white", "black"):
            self.perspective = persp
            self.player_color = "w" if persp == "white" else "b"
        rank_mode = str(data.get("ranking_mode", "priority") or "priority")
        if rank_mode in EnginePool.RANKING_MODES:
            self.ranking_mode = rank_mode
            if hasattr(self, "pool"):
                self.pool.ranking_mode = rank_mode
        # Coach.
        self.coach_ai_enabled = bool(data.get("enabled", False))
        self.coach_groq_key = str(data.get("key", "") or "").strip()
        self.coach_model = str(data.get("model", "") or "").strip() \
            or "llama-3.3-70b-versatile"
        # Visuals.
        self.eval_bar_visible = bool(data.get("eval_bar_visible", True))
        self.grid_visible = bool(data.get("grid_visible", True))
        self.depth_display_visible = bool(data.get("depth_display_visible", False))
        try:
            self.arrow_scale = max(0.5, min(2.0, float(data.get("arrow_scale", 1.0) or 1.0)))
        except (TypeError, ValueError):
            pass
        try:
            self.arrow_opacity = max(0.25, min(1.0, float(data.get("arrow_opacity", 1.0) or 1.0)))
        except (TypeError, ValueError):
            pass
        self.arrow_dash = bool(data.get("arrow_dash", False))
        self.arrow_animate = bool(data.get("arrow_animate", False))
        dm = str(data.get("display_mode", "pv") or "pv")
        if dm in ("pv", "bestmove", "multiPV", "others", "advPV"):
            self.display_mode = dm
        self.coach_visible = bool(data.get("coach_visible", True))
        self._settings_ext_visible = bool(data.get("external_eval_visible", False))
        # Accuracy / recommendation targeting.
        self.accuracy_display_enabled = bool(
            data.get("accuracy_display_enabled", True))
        self.recommendation_accuracy_enabled = bool(
            data.get("recommendation_accuracy_enabled", True))
        # Older bundles defaulted strength-first ON, making saved ranges
        # advisory. Migrate once; subsequent explicit opt-ins remain persistent.
        self.recommendation_strength_first = bool(
            data.get("recommendation_policy_version") == 2 and
            data.get("recommendation_strength_first", False))
        try:
            low = max(35, min(100, int(data.get("recommendation_accuracy_min", 70) or 70)))
            high = max(35, min(100, int(data.get("recommendation_accuracy_max", 80) or 80)))
            if low > high:
                low, high = high, low
            self.target_recommendation_accuracy_min = low
            self.target_recommendation_accuracy_max = high
            prio = max(low, min(high, int(
                data.get("recommendation_accuracy_priority", 75) or 75)))
            self.target_recommendation_accuracy_priority = prio
            self.target_recommendation_accuracy = round((low + high) / 2)
        except (TypeError, ValueError):
            pass
        self.anti_draw_enabled = bool(data.get('anti_draw_enabled', True))
        try:
            self.accuracy_candidate_limit = max(4, min(16, int(data.get('accuracy_candidate_limit', 8))))
            threshold = float(data.get('anti_draw_threshold', 0.12))
            advantage = float(data.get('anti_draw_min_advantage', 0.35))
            self.anti_draw_threshold = max(0.0, min(2.0, threshold)) if math.isfinite(threshold) else 0.12
            self.anti_draw_min_advantage = max(self.anti_draw_threshold + 0.01, min(5.0, advantage)) if math.isfinite(advantage) else max(0.35, self.anti_draw_threshold + 0.01)
        except (TypeError, ValueError):
            pass
        # Analysis / humanizer / blunder filter.
        try:
            self.opponent_elo = int(data.get("opponent_elo", 1600) or 1600)
        except (TypeError, ValueError):
            pass
        self.show_opponent_move = bool(data.get("show_opponent_move", True))
        self.aggressive_blunder_filter_enabled = bool(
            data.get("aggressive_blunder_filter_enabled", True))
        try:
            self.aggressive_blunder_threshold = max(
                0.0, min(5.0, float(data.get("aggressive_blunder_threshold", 0.50))))
        except (TypeError, ValueError):
            pass
        self.dynamic_depth_enabled = bool(data.get("dynamic_depth_enabled", False))
        try:
            speed = max(50, min(2000, int(data.get("refresh_speed", 100) or 100)))
            self.gui_update_delay = speed
            self._gui_update_delay = speed
        except (TypeError, ValueError):
            pass
        try:
            if "humanizer_enabled" in data or "humanizer_preset" in data:
                self.humanizer._configured = True
            self.humanizer.enabled = bool(data.get("humanizer_enabled", True))
            preset = str(data.get("humanizer_preset", "") or "")
            if preset in Humanizer.SKILL_PRESETS:
                self.humanizer.apply_preset(preset)
            if data.get("humanizer_mean_ms"):
                self.humanizer.mean_ms = max(200, min(20000, int(data["humanizer_mean_ms"])))
            if data.get("humanizer_blunder_chance") is not None:
                self.humanizer.blunder_chance = max(0.0, min(0.5, float(data["humanizer_blunder_chance"])))
            if data.get("humanizer_pool"):
                self.humanizer.rank_pool_size = max(1, min(10, int(data["humanizer_pool"])))
        except (TypeError, ValueError):
            pass
        # AutoMove — human-like mouse-motion simulation.
        try:
            am = self.automove
            am.enabled = bool(data.get("automove_enabled", False))
            _tmin = data.get("automove_think_min_ms", 3000)
            am.think_min_ms = max(0, min(20000, int(
                3000 if _tmin is None else _tmin)))
            _tmax = data.get("automove_think_max_ms", 15000)
            am.think_max_ms = max(0, min(60000, int(
                15000 if _tmax is None else _tmax)))
            am.think_complex_min_ms = max(2000, min(60000, int(
                data.get("automove_think_complex_min_ms", 20000) or 20000)))
            am.think_complex_max_ms = max(2000, min(90000, int(
                data.get("automove_think_complex_max_ms", 30000) or 30000)))
            am.complex_enabled = bool(data.get("automove_complex_enabled", True))
            am.event_delay_min_ms = max(10, min(1000, int(
                data.get("automove_event_delay_min_ms", 50) or 50)))
            am.event_delay_max_ms = max(20, min(2000, int(
                data.get("automove_event_delay_max_ms", 200) or 200)))
            am.curvature = max(0.0, min(1.0, float(
                data.get("automove_curvature", 0.45))))
            am.micro_movement = max(0.0, min(1.0, float(
                data.get("automove_micro_movement", 0.40))))
            am.hesitation = max(0.0, min(1.0, float(
                data.get("automove_hesitation", 0.50))))
            am.speed_variation = max(0.0, min(1.0, float(
                data.get("automove_speed_variation", 0.60))))
            am.path_speed = max(0.1, min(6.0, float(
                data.get("automove_path_speed", 1.0) or 1.0)))
            cal = data.get("automove_board_calibration")
            if isinstance(cal, dict) and cal.get("size"):
                self._board_calibration = {
                    "left": float(cal.get("left", 0)),
                    "top": float(cal.get("top", 0)),
                    "size": float(cal["size"]),
                    "flipped": bool(cal.get("flipped")),
                }
            else:
                self._board_calibration = None
            self._automove_debug_geometry = bool(
                data.get("automove_debug_geometry", False))
            self._auto_queue_enabled = bool(
                data.get("auto_queue_enabled", False))
            self._auto_queue_delay_s = max(0.5, min(30.0, float(
                data.get("auto_queue_delay_s", 3.0) or 3.0)))
            if am.think_max_ms < am.think_min_ms:
                am.think_max_ms = am.think_min_ms
            if am.think_complex_max_ms < am.think_complex_min_ms:
                am.think_complex_max_ms = am.think_complex_min_ms
            if am.event_delay_max_ms < am.event_delay_min_ms:
                am.event_delay_max_ms = am.event_delay_min_ms
        except (TypeError, ValueError):
            pass
        # Opening book / cloud / tablebases.
        self.book_manager.cloud_enabled = bool(data.get("book_cloud_enabled", True))
        src = str(data.get("book_cloud_source", "masters") or "masters")
        if src in ("masters", "lichess", "chessdb"):
            self.book_manager.cloud_source = src
        self.book_manager.lichess_token = str(
            data.get("book_lichess_token", "") or "")
        self.book_manager.tb_enabled = bool(data.get("book_tb_enabled", True))
        self.book_manager.stage_show = bool(data.get("book_stage_show", True))
        # Coach mode sub-settings.
        self.coach_mode.enabled = bool(data.get("coach_mode_enabled", False))
        self.coach_mode.show_tips = bool(data.get("coach_mode_tips", True))
        self.coach_mode.suggest_better = bool(data.get("coach_mode_suggest", True))
        self.coach_mode.show_running_accuracy = bool(
            data.get("coach_mode_running_acc", True))
        self.coach_mode.tts_enabled = bool(data.get("coach_mode_tts", False))
        # Engine rotation + privacy.
        self.engine_rotation_enabled = bool(data.get("engine_rotation_enabled", False))
        try:
            self.rotation_engine_count = max(
                1, int(data.get("rotation_count", 1) or 1))
        except (TypeError, ValueError):
            pass
        self.stealth_mode_enabled = bool(data.get("stealth_mode_enabled", False))
        self.hosted_engine_base = str(
            data.get("hosted_engine_base", "")
            or "wss://ProtonnDev-engine.hf.space")
        # Compute mode + GPU options.
        mode = str(data.get("compute_mode", "cpu") or "cpu").strip().lower()
        if mode in ("cpu", "gpu", "hybrid"):
            self.compute_mode = mode
        gpu_opts = data.get("gpu_engine_options")
        if isinstance(gpu_opts, dict) and gpu_opts:
            self.gpu_engine_options = dict(gpu_opts)
        saved_paths = data.get("engine_paths")
        if isinstance(saved_paths, list) and saved_paths:
            valid_paths = [p for p in saved_paths if os.path.exists(p) or str(p).startswith("socket://")]
            if valid_paths:
                self.engine_paths = valid_paths
        if not self.engine_paths:
            default_lc0 = os.path.abspath("C:/Users/fa/Downloads/EngineWS/engines/lc0/lc0.exe")
            if os.path.exists(default_lc0):
                self.engine_paths = [default_lc0]
        # Window geometry restore targets (applied once the windows exist).
        self._saved_panel_geometry = data.get("panel_geometry") or None
        self._saved_eval_geometry = data.get("eval_geometry") or None
        # Keep the control panel in sync if it already exists (runtime re-loads).
        if getattr(self, "control_panel", None) is not None:
            self._sync_config_ui()


    def _start_settings_autosave(self):
        # 30s interval: settings never change faster than this and 3s was
        # causing constant disk writes (≈20 writes/minute) with zero benefit.
        self._autosave_after = self.root.after(30000, self._autosave_tick)

    def _sync_config_ui(self):
        """Push the overlay's current settings back into every control-panel
        widget so the UI always matches the saved/restored config."""
        panel = getattr(self, "control_panel", None)
        if panel is not None and hasattr(panel, "sync_vars_from_overlay"):
            try:
                panel.sync_vars_from_overlay()
            except Exception:
                pass

    def _autosave_tick(self):
        try:
            self._save_all_settings()
        except Exception:
            pass
        if getattr(self, "should_run", False) and self.root.winfo_exists():
            self._autosave_after = self.root.after(30000, self._autosave_tick)

    def _groq_chat(self, messages, timeout=30):
        """Call Groq chat completions; returns the assistant text. Raises on error."""
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self.coach_groq_key}",
                   "Content-Type": "application/json"}
        payload = {"model": self.coach_model, "messages": messages,
                   "temperature": 0.4, "max_tokens": 220}
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return str(data["choices"][0]["message"]["content"]).strip()

    def _coach_context(self, fen=None):
        """Compact structured context about the current analysis for the AI."""
        if fen is None:
            fen = getattr(self, "current_fen", "") or ""
        merged = self._compute_visible_suggestions()
        labels = {1: "Blue", 2: "Green", 3: "Yellow", 4: "Orange"}
        suggestions = []
        for i, (orig, moves, score) in enumerate(merged, 1):
            if moves and isinstance(moves[0], str) and len(moves[0]) >= 4:
                try:
                    why = self._coach_why_good(fen, moves[0])
                except Exception:
                    why = ""
                suggestions.append({"rank": i,
                                    "colour": labels.get(i, "Red"),
                                    "move": self._coach_move_text(fen, moves[0]),
                                    "uci": moves[0],
                                    "score": str(score),
                                    "why": why,
                                    "pv": moves[:6]})
        return (f"Position FEN: {fen}\nSide to move: {getattr(self, 'current_player_color', '?')}"
                f"\nHuman plays: {self._human_color()}"
                f"\nSuggestions (colours match the on-screen arrows):\n"
                f"{json.dumps(suggestions, indent=1)}")

    def _coach_ai_ask_async(self, question=None, fallback_text=None,
                            fallback_segments=None):
        """Ask Groq in a worker thread; posts the reply through ui_queue.

        `question` None -> auto-narrate why Blue is best.  Otherwise answer the
        user's question in the chat box.  `fallback_text` is posted if the call
        fails, keeping the chat useful without network; `fallback_segments`
        (optional) posts a coloured narration instead when present.
        """
        context = self._coach_context()
        if question:
            user_msg = (f"{context}\n\nThe user asks: {question}\n"
                        f"Answer briefly and conversationally, referring to the "
                        f"suggestions by colour and move name.")
            sys_prompt = ("You are a chess coach in a desktop visualizer. Answer the "
                          "user's question concisely (max ~3 short lines). Refer to "
                          "moves by name (e.g. 'exd5') and arrow colour (Blue=best, "
                          "Green=2nd).")
        else:
            try:
                human_turn = self._is_human_turn()
            except Exception:
                human_turn = True
            try:
                us = self._human_color()
            except Exception:
                us = "w"
            whose_turn = "your (the human's) turn" if human_turn else "the opponent's turn"
            user_msg = (f"{context}\n\nIt is {whose_turn}; the human plays {us}.\n"
                        f"Explain the position thoroughly and concretely. Lead with the best "
                        f"line's intention (what it captures, attacks or defends). Name each "
                        f"alternative and why it is weaker. State what the opponent threatens "
                        f"right now. On the opponent's turn, explain what their best move "
                        f"threatens and the human's best answer. 4-7 short lines, never generic.")
            sys_prompt = ("You are a chess coach in a desktop visualizer. Be specific and "
                          "concrete: always tie statements to the actual moves, arrow colours "
                          "(Blue=best, Green=2nd) and squares. When it is the human's turn, "
                          "explain the best move's idea, the alternatives, the opponent's "
                          "threat and a short plan. When it is the opponent's turn, explain "
                          "what the opponent's best move threatens and the human's best reply. "
                          "Use 4-7 short lines.")
        fallback_text = fallback_text or self._coach_ranked_narration()

        def worker():
            try:
                reply = self._groq_chat([{"role": "system", "content": sys_prompt},
                                         {"role": "user", "content": user_msg}])
            except Exception as _e:
                reply = None
                err = str(_e)
            if reply:
                self.ui_queue.put(("coach", reply))
            elif fallback_segments:
                self.ui_queue.put(("coach_rt", fallback_segments))
                self.ui_queue.put(("coach",
                                   f"(AI request failed — {err or 'unknown error'}. Showing local analysis.)"))
            else:
                self.ui_queue.put(("coach", fallback_text))
                self.ui_queue.put(("coach",
                                   f"(AI request failed — {err or 'unknown error'}. Showing local analysis.)"))
        threading.Thread(target=worker, daemon=True).start()

    def _format_decision_time(self, move_index, depth=None):
        # Actual engine time is shown once in telemetry, not as a per-move estimate.
        return ''


    def _move_explanation(self):
        if not self._suggestions_are_ready():
            return "Waiting for requested analysis depth..."
        if self.pv_line:
            return f"Engine best line: {' '.join(self.pv_line[:4])} (Depth {self.current_depth})"
        return "Waiting for engine evaluation..."

    def update_engine_line_text(self):
        text_parts = ["Engine Analysis Output:"]
        if self.engines:
            engine = self.engines[min(self.primary_engine_index, len(self.engines)-1)]
            text_parts.append(f"Reference: {engine.get('name', 'engine')} · target depth {engine.get('target_depth', self.analysis_depth)} · completed arrow depth {engine.get('_committed_depth', 0)} · UCI Elo {self.engine_uci_elo or 'unlimited'}")
            if engine.get('search_fen') == self.current_fen:
                text_parts.append(f"Search lines: {engine.get('_multipv_count', 1)} | "
                                  f"Time: {engine.get('time_ms', 0)/1000:.2f}s | "
                                  f"Nodes: {engine.get('nodes', 0):,} | NPS: {engine.get('nps', 0):,}")
                text_parts.append("Applied engine options: " + str(engine.get('applied_options', {})))
                session = engine.get('session')
                if session and session.rejected:
                    text_parts.append("Unsupported options skipped: " + ', '.join(session.rejected))

        if self.current_fen and self.current_player_color != self.player_color:
            text_parts.append("Opponent turn — showing engine calculation")

        ranks = sorted(rank for rank in self.multi_pvs if isinstance(rank, int) and rank >= 1)
        if ranks:
            for rank in ranks:
                pv_data = self.multi_pvs[rank]
                if pv_data and pv_data[0]:
                    moves, score = pv_data[0], pv_data[1]
                    time_str = self._format_decision_time(rank, self.current_depth)
                    text_parts.append(f"Line {rank}: {' '.join(moves)} ({score}) {time_str}")
        elif self.pv_line:
            text_parts.append("Line 1: " + " ".join(self.pv_line))
        else:
            text_parts.append("Waiting for engine lines...")

        if self.display_mode == "bestmove":
            choices = self._mode_candidates("bestmove")
            if choices:
                if self._style_fallback.get("aggressive"):
                    text_parts.append("Falling back to the strongest available move.")
                for number, (rank, moves, score) in enumerate(choices, 1):
                    text_parts.append(f"Offensive {number}. {' '.join(moves)} ({score}) "
                                      f"{self._format_decision_time(rank)}")
            else:
                text_parts.append(self._mode_empty_message())
        elif self.display_mode == "pv":
            _display_pv = self.committed_pv_line if self.committed_pv_line else self.pv_line
            if _display_pv:
                # The accuracy target only applies to the human's move choices.
                selected = (self._target_accuracy_candidate()
                            if (self.recommendation_accuracy_enabled and self._is_human_turn()) else None)
                if selected:
                    rank, (moves, score) = selected
                    text_parts.append(f"Target {self.target_recommendation_accuracy_min}–{self.target_recommendation_accuracy_max}% → estimated {self.recommendation_accuracy_estimate:.1f}% (line {rank}, {score})")
                    text_parts.append(self.recommendation_accuracy_status)
                    text_parts.append("1. " + " ".join(moves))
                else:
                    text_parts.append("1. " + " ".join(_display_pv))
        elif self.display_mode in ("multiPV", "others", "advPV"):
            alternatives = self._mode_candidates(self.display_mode)
            if alternatives:
                if self.display_mode == "multiPV" and self._style_fallback.get("defensive"):
                    text_parts.append("Falling back to strategic move — no good defensive move found.")
                brilliant_ranks = self._brilliant_ranks() if self.display_mode == 'others' else set()
                if self.display_mode == 'others' and self._is_human_turn():
                    text_parts.append(self.recommendation_accuracy_status)
                for rank, moves, score in alternatives:
                    time_str = self._format_decision_time(rank)
                    label = ('Brilliant' if rank in brilliant_ranks else 'Line') if self.display_mode == 'others' else {'multiPV': 'Defensive', 'advPV': 'Tactician'}[self.display_mode]
                    text_parts.append(f"{label} {rank}. {' '.join(moves)} ({score}) {time_str}")
            else:
                text_parts.append(self._mode_empty_message())
        if not self._suggestions_are_ready():
            engine = self.engines[min(self.primary_engine_index, len(self.engines)-1)] if self.engines else {}
            target = engine.get('target_depth', self.analysis_depth)
            live = engine.get('current_depth', 0)
            text_parts = [f"Search depth {live} / {target}",
                          "Suggestions appear after the full search and any accuracy refinement finish.",
                          "No completed result at the requested depth yet."]
        final_text = "\n".join(text_parts).rstrip()

        self.control_panel.engine_line_text.config(state=tk.NORMAL)
        self.control_panel.engine_line_text.delete("1.0", tk.END)
        self.control_panel.engine_line_text.insert(tk.END, final_text)
        self.control_panel.engine_line_text.config(state=tk.DISABLED)

    @staticmethod
    def _detect_system_hardware():
        """Detect CPU cores, installed RAM, and GPUs available to chess engines.

        Returns a dict with sensible defaults the app can pick up:
          threads_default   -> slider default (leave one core for UI/graphics)
          threads_max       -> slider ceiling
          hash_default_mb   -> hashtable default (1/8 of RAM, rounded)
          hash_max_mb       -> hashtable slider ceiling (half of detected RAM)
          logical_cpus      -> os.cpu_count()
          ram_total_mb      -> estimate of total physical RAM
          gpus              -> list of {"name":..., "kind": "nvidia"|"amd"|"intel"|"directml"|"lc0"}
        """
        profile = {
            "logical_cpus": os.cpu_count() or 4,
            "ram_total_mb": 8192,
            "threads_default": 4,
            "threads_max": 32,
            "hash_default_mb": 256,
            "hash_max_mb": 2048,
            "gpus": [],
        }

        # ---- CPU ----
        cpus = profile["logical_cpus"]
        # Default threads: leave at least 1 core for the overlay + browser.
        # SMT systems (most x86) work best with Threads == physical cores.
        # We don't have physical-cores info in stdlib, so a common heuristic:
        #   - if cpus >= 16 -> 3/4 of logical (assume SMT, heavy browser use)
        #   - if cpus >= 8  -> cpus - 2
        #   - if cpus >= 4  -> cpus - 1
        #   - else cpus
        if cpus >= 16:
            profile["threads_default"] = max(1, int(cpus * 0.75))
        elif cpus >= 8:
            profile["threads_default"] = cpus - 2
        elif cpus >= 4:
            profile["threads_default"] = cpus - 1
        else:
            profile["threads_default"] = cpus
        profile["threads_max"] = cpus

        # ---- RAM (Windows via GlobalMemoryStatusEx) ----
        try:
            if sys.platform == "win32":
                class _MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                    ]
                ms = _MEMORYSTATUSEX()
                ms.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
                if ctypes.WinDLL("kernel32", use_last_error=True).GlobalMemoryStatusEx(ctypes.byref(ms)):
                    profile["ram_total_mb"] = int(ms.ullTotalPhys / (1024 * 1024))
        except Exception:
            pass

        ram_mb = profile["ram_total_mb"]
        # Default hash = ~1/8 RAM (so browser + OS + engine still have room),
        # max hash = half the RAM (aggressive but common for serious analysis).
        profile["hash_default_mb"] = max(16, int(ram_mb / 8 / 16) * 16)  # round to 16-MB
        profile["hash_max_mb"] = max(256, int(ram_mb / 2 / 16) * 16)

        # ---- GPU detection (informational; used for engine options if applicable) ----
        # 1. NVIDIA via nvidia-smi -> Leela Chess Zero CUDA backend will auto pick this up.
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            if out.returncode == 0:
                for raw in out.stdout.splitlines():
                    parts = [p.strip() for p in raw.split(",")]
                    if parts and parts[0]:
                        name = parts[0]
                        mem_mb = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
                        profile["gpus"].append({"name": name, "kind": "nvidia", "mem_mb": mem_mb})
        except (FileNotFoundError, subprocess.SubprocessError, ValueError, OSError):
            pass

        # 2. DirectML availability on Windows (lc0 dml build, any GPU vendor)
        try:
            if sys.platform == "win32":
                # LoadLibrary with LOAD_LIBRARY_SEARCH_SYSTEM32 flag (0x00000800)
                LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800
                res = ctypes.WinDLL("kernel32", use_last_error=True).LoadLibraryExW(
                    "directml.dll", 0, LOAD_LIBRARY_SEARCH_SYSTEM32
                )
                if res:
                    ctypes.WinDLL("kernel32").FreeLibrary(res)
                    profile["gpus"].append({"name": "DirectML (any vendor)",
                                            "kind": "directml", "mem_mb": 0})
        except Exception:
            pass

        # 3. CUDA runtime DLL presence (stockfish-nnue-cuda / lc0-cuda runtime dep)
        try:
            if sys.platform == "win32":
                sysdir = os.environ.get("SystemRoot", r"C:\Windows") + "\\System32"
                for entry in os.listdir(sysdir) if os.path.isdir(sysdir) else []:
                    low = entry.lower()
                    if low.startswith("cudart64_") and low.endswith(".dll"):
                        profile["gpus"].append({"name": f"CUDA runtime ({entry})",
                                                "kind": "cuda", "mem_mb": 0})
                        break
        except Exception:
            pass

        # Print once so user can see why defaults got set this way
        try:
            gpu_lines = ", ".join(g["name"] for g in profile["gpus"]) or "none detected"
            print(f"[Hardware] logical CPUs: {profile['logical_cpus']}, "
                  f"threads = {profile['threads_default']}/{profile['threads_max']}, "
                  f"RAM ~{profile['ram_total_mb']} MB -> hash "
                  f"{profile['hash_default_mb']}/{profile['hash_max_mb']} MB, "
                  f"GPUs: {gpu_lines}")
        except Exception:
            pass
        return profile

    def _search_options(self, engine):
        count = max(1, len(self.engines))
        cpus = max(1, os.cpu_count() or 1)
        threads = max(1, min(self.engine_threads, cpus // count or 1))
        hash_mb = max(16, min(self.engine_hash, self.engine_hash_max // count))
        options = {'Threads': threads, 'Hash': hash_mb,
                   'MultiPV': self._requested_pvs(engine), 'UCI_AnalyseMode': 'true',
                   'UCI_LimitStrength': 'true' if 1000 <= self.engine_uci_elo <= 3500 else 'false',
                   'Contempt': self.engine_contempt, 'SyzygyPath': self.engine_syzygy_path or ''}
        if 1000 <= self.engine_uci_elo <= 3500:
            options['UCI_Elo'] = self.engine_uci_elo
        if self._is_lc0_engine(engine):
            options.update({k: v for k, v in self.gpu_engine_options.items() if v != ''})
            options['Threads'] = max(1, min(int(options.get('Threads', threads)), cpus // count or 1))
            weights = str(options.pop('WeightsFile', '') or '').strip()
            engine_dir = os.path.dirname(os.path.abspath(engine.get('path', '') or '.'))
            candidates = [weights, os.path.join(engine_dir, weights)] if weights else []
            candidates += [os.path.join(engine_dir, n) for n in ('791556.pb.gz', 'network_300.pb.gz')]
            if os.path.isdir(engine_dir):
                candidates += [os.path.join(engine_dir, n) for n in sorted(os.listdir(engine_dir))
                               if n.endswith(('.pb.gz', '.pb', '.onnx'))]
            resolved = next((os.path.abspath(n) for n in candidates if n and os.path.isfile(n)), None)
            if resolved:
                options['WeightsFile'] = resolved
            options['Precision'] = 'auto'
            options['UCI_ShowWDL'] = 'true' 
        return options

    def _requested_pvs(self, engine):
        count = max(1, self.multi_pv_count)
        if self.recommendation_accuracy_enabled:
            count = max(count, max(4, min(16, int(getattr(self, 'accuracy_candidate_limit', 8)))))
        return count

    def _search_signature(self, engine, fen, depth):
        return (id(engine.get('process')), fen, self._effective_search_depth(engine, depth),
                tuple(sorted((k, str(v)) for k, v in self._search_options(engine).items())),
                self.analysis_time_ms, self.analysis_nodes, self._human_color(), self._bridge_epoch)

    def update_search_limits(self):
        try:
            self.analysis_time_ms = max(0, min(600000, int(self.control_panel.time_limit_var.get())))
            self.analysis_nodes = max(0, int(self.control_panel.node_limit_var.get()))
            self.only_own_turn = bool(self.control_panel.own_turn_var.get())
        except (ValueError, tk.TclError):
            return
        self._save_all_settings()
        self._reanalyse_after_option_change()

    def _analysis_status(self, stage, engine=None):
        request = getattr(self, '_analysis_request', None)
        if not request or request.get('fen') != self.current_fen:
            return
        if engine is not None and (not self.engines or engine is not self.engines[self.primary_engine_index]):
            return
        if engine is not None and engine.get('request_id') != request.get('request_id'):
            return
        payload = dict(request, type='analysis_status', stage=stage, target_depth=self.analysis_depth)
        payload.pop('game_context', None)
        if engine:
            payload.update(search_depth=engine.get('current_depth', 0),
                           arrow_depth=engine.get('_committed_depth', 0),
                           elapsed_ms=engine.get('time_ms', 0), nodes=engine.get('nodes', 0),
                           nps=engine.get('nps', 0), searched_lines=engine.get('_multipv_count', 1),
                           target_depth=engine.get('target_depth', self.analysis_depth))
        self.ws_broadcast(payload)

    def initialize_engine_uci(self, engine):
        old = engine.get('session')
        if old:
            old.close()
        engine['current_search_fen'] = None
        engine['search_fen'] = None
        engine['pending_search_fen'] = None
        engine['accepting_info'] = False
        engine['session'] = UciSession(engine['process'])


    def _engine_is_active(self, engine):
        return (self.should_run and not engine.get("_retired", False)
                and any(e is engine for e in self.engines))

    @staticmethod
    def _terminate_engine_process(process):
        """Release a local engine or hosted connection and reap its process."""
        if process is None:
            return
        try:
            if process.poll() is None:
                try:
                    process.stdin.write("quit\n")
                    process.stdin.flush()
                except (AttributeError, OSError, ValueError):
                    pass
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=1)
        finally:
            if process.poll() is not None:
                for stream_name in ("stdin", "stdout", "stderr"):
                    stream = getattr(process, stream_name, None)
                    if stream is not None:
                        try:
                            stream.close()
                        except (AttributeError, OSError, ValueError):
                            pass

    def _retire_engine(self, engine):
        engine["_retired"] = True
        if engine.get("session"):
            engine["session"].close()
        engine["accepting_info"] = False
        engine["pending_search_fen"] = None
        engine["current_search_fen"] = None
        engine["search_fen"] = None
        process = engine.get("process")
        engine["process"] = None
        try:
            self._terminate_engine_process(process)
        except Exception as exc:
            print(f"[Engine Release Error] {engine.get('name', '?')}: {exc}")

    def _restart_engine(self, engine):
        """Restart a live engine; retirement cancels even an in-flight restart."""
        if not self._engine_is_active(engine):
            return
        lock = engine.setdefault("restart_lock", threading.Lock())
        if not lock.acquire(blocking=False):
            return
        try:
            if engine.get("restart_in_progress") or not self._engine_is_active(engine):
                return
            engine["restart_in_progress"] = True
            time.sleep(0.5)
            if not self._engine_is_active(engine):
                return
            path = engine.get("path", "")
            name = engine.get("name", "engine")
            socket_url = engine.get("socket_url")
            if not socket_url and (not path or not os.path.exists(path)):
                print(f"[Engine] Cannot restart {name}: executable is unavailable.")
                return
            try:
                self._terminate_engine_process(engine.get("process"))
                if not self._engine_is_active(engine):
                    return
                if socket_url:
                    proc = SocketEngineProcess(socket_url, name=name)
                else:
                    proc = subprocess.Popen([os.path.abspath(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True, bufsize=1, cwd=os.path.dirname(os.path.abspath(path)),
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
                    _assign_process_to_job(proc)
                engine["process"] = proc
                # Selection can change while a local launch or socket connect
                # is in progress. Never keep its late-created process alive.
                if not self._engine_is_active(engine):
                    self._retire_engine(engine)
                    return
                engine.update({"search_fen": None, "current_search_fen": None,
                               "pending_search_fen": None, "accepting_info": True,
                               "current_depth": 0, "pv_line": [], "final_pv": []})
                self.initialize_engine_uci(engine)
                threading.Thread(target=self.read_engine_output, args=(engine,), daemon=True).start()
                print(f"[Engine] {name} restarted.")
                if self.current_fen and self._engine_is_active(engine):
                    self.last_analyzed_fen = None
                    self.ui_queue.put(("analyse", self.current_fen, self.engine_elo,
                                       getattr(self, "current_flipped", 0),
                                       getattr(self, "current_playing_as", None)))
            except Exception as exc:
                print(f"[Engine] Failed to restart {name}: {exc}")
        finally:
            engine["restart_in_progress"] = False
            lock.release()

    def read_engine_output(self, engine):
        # Capture this process once.  A restarted process gets its own reader;
        # otherwise an old reader can steal its output and leave rotation silent.
        process = engine.get("process")
        needs_restart = False
        try:
            while (process is not None and engine.get("process") is process
                   and not engine.get("_retired", False)):
                if process.poll() is not None:
                    # Engine process died between reads.
                    needs_restart = True
                    break
                line = process.stdout.readline()
                if not line:  # EOF: stdout was closed; do not spin on an empty pipe.
                    needs_restart = True
                    break
                if engine.get("_retired", False) or engine.get("process") is not process:
                    break
                try:
                    self.process_engine_line(engine, line)
                except Exception as exc:
                    # A single malformed line must never kill the reader thread;
                    # otherwise the engine's stdout pipe fills up, the engine
                    # blocks, and move suggestions freeze until a restart.
                    print(f"[Engine line error] {engine.get('name', '?')}: {exc}")
        except (OSError, ValueError) as exc:
            print(f"Engine output reader stopped ({engine.get('name', 'unknown')}): {exc}")
            needs_restart = True
        finally:
            self.trigger_gui_update()
            # Only the reader for the active process may request a restart, and
            # only if this engine is still part of the live pool (start_engine
            # clears the list, so restarts would otherwise leak orphan processes).
            # The flag (not a fresh poll()) decides: on Windows a terminated
            # process can still briefly report "alive" when EOF arrives, so a
            # poll()-based check would skip the restart and freeze suggestions
            # until the next periodic GC tick.
            if (self.should_run and needs_restart and not engine.get("_retired", False)
                    and engine.get("process") is process and process
                    and any(e is engine for e in self.engines)):
                threading.Thread(target=self._restart_engine, args=(engine,), daemon=True).start()

    def process_engine_line(self, engine, line):
        session = engine.get('session')
        if engine.get('_retired', False):
            return
        if session is None:
            self._process_engine_line(engine, line)
            return
        with session.lock:
            if session.closed or engine.get('session') is not session:
                return
            if session.discard_active and line.startswith(('info ', 'bestmove ')):
                if line.startswith('bestmove '):
                    engine['current_search_fen'] = None
                    engine['search_fen'] = None
            else:
                self._process_engine_line(engine, line)
            session.handle_line(line)

    def _process_engine_line(self, engine, line):
        """Parse one UCI output line in a case-insensitive, tolerant manner.
        Handles engine outputs from Stockfish, Shredder, Safrad, Opental, Leela, etc.
        """
        if engine.get("_retired", False):
            return
        line_lower = line.lower()
        parts = line.split()
        parts_lower = [p.lower() for p in parts]

        # Feed the stall watchdog: any output from a live engine counts as life.
        engine["last_output_time"] = time.time()

        # Engine telemetry is independent of the committed arrow snapshot.
        if engine.get('accepting_info') and (engine.get('current_search_fen') == self.current_fen):
            for token, field in [('time', 'time_ms'), ('nodes', 'nodes'), ('nps', 'nps'), ('hashfull', 'hashfull')]:
                try:
                    engine[field] = int(parts[parts_lower.index(token) + 1])
                except (ValueError, IndexError):
                    pass
            if time.monotonic() - engine.get('_telemetry_at', 0) >= 0.1:
                engine['_telemetry_at'] = time.monotonic()
                self.trigger_gui_update()

        # Track current depth for stability checks
        if "depth" in parts_lower:
            try:
                d_idx = parts_lower.index("depth")
                engine["current_depth"] = int(parts[d_idx + 1])
            except Exception:
                pass

        if "bestmove" in parts_lower:
            try:
                bm_idx = parts_lower.index("bestmove")
                if bm_idx + 1 < len(parts):
                    engine["best_move"] = parts[bm_idx + 1]
                    if engine.get("pv_line"):
                        engine["final_pv"] = engine["pv_line"].copy()

                    try:
                        engine_idx = self.engines.index(engine)
                        self.engines_ready[engine_idx] = True
                    except Exception:
                        pass

                    # Search tracking: check against current_search_fen
                    completed_fen = engine.get("current_search_fen") or engine.get("search_fen")
                    engine["current_search_fen"] = None
                    engine["search_fen"] = None
                    engine["is_stopping"] = False
                    if hasattr(self.pool, "cmd_queue"):
                        self.pool.cmd_queue.on_info_or_bestmove(engine)

                    if completed_fen is None or completed_fen != self.current_fen:
                        # Superseded search result: do NOT update current overlay suggestions with it
                        if completed_fen is not None:
                            try:
                                pending = self._pending_move
                                if (pending
                                        and pending.get("pre_score") is None
                                        and pending.get("pre_analysis_fen") == completed_fen
                                        and pending.get("post_fen") == self.current_fen
                                        and engine.get("active_fen") == completed_fen):
                                    pre_score = engine.get("score_white")
                                    if pre_score is not None:
                                        pending["pre_score"] = pre_score
                                        pending["pre_best_move"] = None
                                        self._complete_move_tracking()
                            except Exception:
                                pass
                        engine["accepting_info"] = True
                        # If a newer position was queued while this search was stopping, dispatch it now!
                        self._check_and_start_pending_search(engine)
                        self.trigger_gui_update()
                        return

                    engine["accepting_info"] = False
                    if engine.get("_committed_fen") == completed_fen:
                        self.pool.feed_bestmove(engine["name"], parts[bm_idx + 1], completed_fen)
                    no_moves = parts[bm_idx + 1].lower() in ("0000", "(none)", "none", "null")
                    if no_moves:
                        engine["no_legal_moves_fen"] = completed_fen

                    # Use pool-ranked best move if available, else fall back to primary engine
                    ranked = self.pool.get_best_move()
                    if (ranked and completed_fen == self.current_fen
                            and self.committed_multi_pvs):
                        self.best_move = ranked["move"]
                        self.pv_ready_to_play = True
                    elif (self.engines and engine == self.engines[self.primary_engine_index]
                            and completed_fen == self.current_fen and self.committed_multi_pvs):
                        self.best_move = engine["best_move"]
                        self.pv_ready_to_play = True

                    if (completed_fen == self.current_fen and self.engines
                            and engine is self.engines[self.primary_engine_index]):
                        self.final_pv = engine.get("pv_line", []).copy()
                        self._complete_move_tracking()
                        streak_key = (self._bridge_epoch, completed_fen)
                        if getattr(self, '_last_streak_fen_key', None) != streak_key:
                            self._last_streak_fen_key = streak_key
                            self._update_losing_streak()
                        self.previous_score = self.current_score
                        self.previous_best_move = self.best_move
                        self.previous_analysis_fen = completed_fen

                        # If search completed for current_fen but produced NO suggestions
                        # (e.g. stopped prematurely before reaching depth threshold),
                        # queue current_fen to re-search so the engine doesn't sit idle without suggestions!
                        if not engine.get("session") and not self.committed_multi_pvs and not self.multi_pvs and not no_moves:
                            engine["pending_search_fen"] = self.current_fen

                    expanding = self._maybe_expand_accuracy_search(engine, completed_fen)
                    if self.engines and engine is self.engines[self.primary_engine_index]:
                        ready = (not expanding and engine.get('_committed_complete', False)
                                 and engine.get('_committed_fen') == self.current_fen
                                 and engine.get('_committed_depth', 0) >= engine.get('target_depth', self.analysis_depth))
                        self._suggestions_fen = self.current_fen if ready else None
                        self.pv_ready_to_play = ready
                    stage = 'refining' if expanding else 'completed'
                    if (not expanding and not no_moves and self.engines
                            and engine is self.engines[self.primary_engine_index]
                            and not self._suggestions_are_ready()):
                        stage = 'incomplete'
                    if self.engines and engine is self.engines[self.primary_engine_index]:
                        self.depth_timer.finish(engine.get('_depth_timing_ticket'), completed_fen,
                            engine.get('_committed_depth', 0) if self._suggestions_are_ready() else engine.get('current_depth', 0),
                            self._suggestions_are_ready(), no_moves)
                    self._analysis_status(stage, engine)
                    self.trigger_gui_update()
                    if not expanding:
                        self._kick_automove()
                    # If another position arrived in the meantime, dispatch it now
                    self._check_and_start_pending_search(engine)
            except Exception:
                pass

        elif "pv" in parts_lower and engine.get("accepting_info", True):
            pv_num = 1
            try:
                pv_num = int(parts[parts_lower.index("multipv") + 1]) if "multipv" in parts_lower else 1
                pv_start = parts_lower.index("pv") + 1

                # Use the PER-ENGINE turn context so the parser can't race against
                # the global current_player_color which is updated synchronously on
                # every browser FEN push while engine PVs arrive asynchronously.
                active_turn = engine.get("active_turn")
                if active_turn not in ("w", "b"):
                    active_fen = engine.get("active_fen")
                    if active_fen:
                        try:
                            active_turn = active_fen.split()[1]
                        except Exception:
                            active_turn = None
                if active_turn not in ("w", "b"):
                    active_turn = self.current_player_color if self.current_player_color in ("w", "b") else "w"

                if "lowerbound" in parts_lower or "upperbound" in parts_lower:
                    return
                raw_score_str, score_num = self.parse_score(parts, turn=active_turn)
                if raw_score_str == "N/A" or not math.isfinite(score_num):
                    return

                # score_num is White-centered (+ = White better, - = Black better, ±10 = mate)
                white_score = score_num
                turn = active_turn
                us = self._human_color()

                # Convert __MATE__±N into perspective-aware M / EM + depth label
                if isinstance(raw_score_str, str) and raw_score_str.startswith("__MATE__"):
                    mate_signed = raw_score_str[len("__MATE__"):]
                    display_score_str, _wn = self._format_mate_depth(mate_signed, turn, us)
                else:
                    # CP: present label from OUR perspective (we = the human); signed.
                    try:
                        v = float(score_num)  # White-centered
                        human_pov = v if us == "w" else -v
                        display_score_str = f"{human_pov:+.2f}"
                    except (TypeError, ValueError):
                        display_score_str = raw_score_str

                target = engine.get("target_depth", self.analysis_depth)
                # Publish only a complete root set at the actual requested UCI depth.
                commit_threshold = target
                try:
                    depth = int(parts[parts_lower.index('depth') + 1])
                except (ValueError, IndexError):
                    return
                batches = engine.setdefault("_pv_batches", {})
                batch = batches.setdefault(depth, {})
                if pv_num == 1:
                    batch.clear()  # Start a new iteration even when the depth repeats.
                batch[pv_num] = (list(parts[pv_start:]), display_score_str)
                expected = engine.get("_multipv_count", 1)
                complete = (all(rank in batch for rank in range(1, expected + 1))
                            and len({batch[rank][0][0] for rank in range(1, expected + 1)
                                     if batch[rank][0]}) == expected)
                if len(batches) > 2:
                    for old_depth in sorted(batches)[:-2]:
                        del batches[old_depth]
                if (depth < commit_threshold or depth < engine.get("_committed_depth", 0)):
                    return
                if complete:
                    snapshot = {rank: batch[rank] for rank in range(1, expected + 1)}
                else:
                    return
                engine["_committed_complete"] = complete
                engine["multi_pvs"] = dict(snapshot)
                engine["committed_multi_pvs"] = dict(snapshot)
                engine["pv_line"] = list(snapshot[1][0])
                engine["committed_pv_line"] = list(snapshot[1][0])
                engine["score"] = snapshot[1][1]
                engine["score_white"] = self._display_to_white_score(snapshot[1][1])
                engine["_committed_depth"] = depth
                engine["_committed_fen"] = engine.get("current_search_fen") or engine.get("active_fen")
                pool_snapshot = {}
                for rank, (moves, label) in snapshot.items():
                    mate_label = str(label).upper()
                    mate = None
                    cp = self._display_to_white_score(label) * (100 if active_turn == "w" else -100)
                    if mate_label.startswith(("FM", "EM")):
                        distance = int(re.sub(r"[^0-9]", "", mate_label) or "0")
                        winner = us if mate_label.startswith("FM") else ("b" if us == "w" else "w")
                        mate = distance if winner == active_turn else -distance
                        cp = None
                    pool_snapshot[rank] = {"pv": moves, "scoreCp": cp, "scoreMate": mate}
                publish = getattr(self.pool, "feed_snapshot", None)
                if callable(publish):
                    publish(engine["name"], depth, pool_snapshot, engine["_committed_fen"])
                else:
                    # Older delivered pools expose feed_info only. Keep their
                    # optional ranking path from suppressing visible analysis.
                    for rank, info in pool_snapshot.items():
                        self.pool.feed_info(engine["name"], rank, depth,
                            info["scoreCp"], info["scoreMate"], info["pv"], engine["_committed_fen"])

                if (self.engines and engine is self.engines[self.primary_engine_index]
                        and engine.get("accepting_info", True)
                        and (engine.get("current_search_fen") or engine.get("active_fen")) == self.current_fen):
                    if hasattr(self.pool, "cmd_queue"):
                        self.pool.cmd_queue.on_info_or_bestmove(engine)
                    self.current_depth = depth
                    self.multi_pvs = dict(snapshot)
                    self.committed_multi_pvs = dict(snapshot)
                    self._arrow_depth_snapshot = (self.current_fen, depth, self.committed_multi_pvs)
                    self.pv_line = list(snapshot[1][0])
                    self.committed_pv_line = list(self.pv_line)
                    self.best_move = self.pv_line[0] if self.pv_line else None
                    ready_depth = target
                    if depth >= ready_depth and self.best_move:
                        self.pv_ready_to_play = True
                    label = str(snapshot[1][1])
                    self.current_is_mate = label.upper().startswith(("FM", "EM"))
                    self.current_mate_label = label if self.current_is_mate else None
                    best_white = self._display_to_white_score(label)
                    self.current_score = best_white
                    self.current_score_fen = self.current_fen
                    if complete and engine.get("_search_cache") is self._position_cache:
                        self._position_cache.put(self.current_fen, depth, dict(snapshot), best_white)
                    if getattr(self, "_pending_move", None) and not self._pending_move.get("_graded"):
                        self._complete_move_tracking()
                        if self._pending_move:
                            self._pending_move["_graded"] = True
                    self.previous_score = best_white
                    self.previous_best_move = self.best_move
                    self.previous_analysis_fen = self.current_fen
                    now = time.time()
                    if (now - getattr(self, "_last_gui_update_time", 0) >= 0.10 or depth == target):
                        self._last_gui_update_time = now
                        self.trigger_gui_update()
            except Exception as _e:
                print(f"[PV Parse Error] pv={pv_num}: {_e}")

    def parse_score(self, parts, turn=None):
        """UCI score parser.

        CP: returns signed pawn eval FROM WHITE'S POV (+ = White better).
        MATE: returns "__MATE__±N" label + numeric ±10 from White's POV
              (+10 = White mates, -10 = Black mates).

        `turn` must be passed explicitly from the per-engine `active_turn` dict
        so that the asynchronous PV reader thread never races against the
        global `current_player_color` which flips on every browser FEN push.
        """
        try:
            if turn not in ("w", "b"):
                turn = getattr(self, "current_player_color", "w") or "w"
            parts_lower = [p.lower() for p in parts]
            if "score" in parts_lower:
                idx = parts_lower.index("score")
                score_type = parts_lower[idx + 1]
                value = parts[idx + 2]
                if score_type == "cp":
                    # UCI CP is from side-to-move POV; centre on White here.
                    num_side_pov = int(value) / 100.0
                    num_white_pov = num_side_pov if turn == "w" else -num_side_pov
                    return f"{num_white_pov:+.1f}", num_white_pov
                elif score_type == "mate":
                    signed = value.replace(" ", "")
                    if not signed.startswith(("+", "-")):
                        signed = "+" + signed
                    is_neg = signed.startswith("-")
                    # UCI semantics:
                    #   mate +N  →  side-to-move WILL mate in N
                    #   mate -N  →  side-to-move WILL BE mated in N
                    if not is_neg:
                        mating_color = turn
                    else:
                        mating_color = "b" if turn == "w" else "w"
                    num_white_pov = 10.0 if mating_color == "w" else -10.0
                    return f"__MATE__{signed}", num_white_pov
        except Exception:
            pass
        return "N/A", 0.0

    def trigger_gui_update(self):
        """Coalesce background refreshes; only Tk's main thread draws."""
        self._pending_gui_updates = True
        with self._refresh_lock:
            if self._refresh_pending:
                return
            self._refresh_pending = True
        self.ui_queue.put(("refresh",))

    def _execute_gui_update(self):
        self.update_display()

    def _predict_opponent_move(self):
        """Predict the opponent's next move and store it in self.opponent_move.

        The opponent is expected to play one of the analysed MultiPV lines.  To
        make the prediction feel human we pass the ranked engine lines through
        the Humanizer tuned to the opponent's ELO: low-rated opponents blunder
        and play 2nd/3rd-best candidates with realistic frequencies, while
        strong opponents almost always take the engine-best move.  When it is
        OUR turn the prediction is instead the opponent's reply to our best
        move (second ply of the engine's top line), which is what the premove
        machinery reacts to.
        """
        if not self._suggestions_are_ready():
            self.opponent_move = None
            return
        try:
            source = self.multi_pvs if self.multi_pvs else self.committed_multi_pvs
            if not source:
                self.opponent_move = None
                return
            ranks = sorted(r for r in source if isinstance(r, int) and r >= 1)
            lines = []
            for r in ranks:
                d = source.get(r)
                if d and d[0]:
                    lines.append((r, d[0], d[1]))
            if not lines:
                self.opponent_move = None
                return

            if self._is_human_turn():
                # Human to move: predict the reply to the displayed recommendation,
                # including a validated book or bounded accuracy alternative.
                visible = self._compute_visible_suggestions()
                best = (visible[0][1], visible[0][2]) if visible else None
                if best and len(best[0]) > 1:
                    self.opponent_move = best[0][1]
                else:
                    self.opponent_move = None
                return

            # Opponent to move: human-like pick among the ranked engine lines.
            # Use the LIVE humanizer instance so the panel settings (enabled,
            # skill preset, blunder chance, rank pool) drive opponent prediction.
            # Only when the user has not customised anything do we auto-derive
            # a skill preset from the opponent's rating instead.
            ranked = [{"move": m[0], "rank": r, "idx": i}
                      for i, (r, m, _s) in enumerate(lines)]
            elo = max(400, min(3000, int(getattr(self, "opponent_elo", 1600) or 1600)))
            hz = self.humanizer
            if not getattr(hz, "_configured", False):
                preset = "beginner" if elo < 1400 else (
                    "intermediate" if elo < 1800 else (
                        "advanced" if elo < 2100 else "master"))
                hz.apply_preset(preset)
            prediction_key = (self.current_fen, tuple((r, m[0]) for r, m, _label in lines),
                              self.opponent_elo, hz.enabled, hz.preset, hz.blunder_chance,
                              hz.rank2_chance, hz.rank3_chance, hz.rank_decay, hz.rank_pool_size)
            if getattr(self, '_opponent_prediction_key', None) == prediction_key:
                return
            self._opponent_prediction_key = prediction_key
            picked = hz.pick_move(ranked, pool_size=None)
            if picked and picked.get("move"):
                self.opponent_move = picked["move"]
            else:
                self.opponent_move = ranked[0]["move"]
        except Exception:
            self.opponent_move = None

    def update_display(self):
        self._refresh_depth_timing()
        panel = getattr(self, 'control_panel', None)
        if panel and hasattr(panel, 'update_overview_metrics'):
            panel.update_overview_metrics()
        if panel and hasattr(panel, 'recommendation_status_label'):
            if ((self.recommendation_accuracy_enabled or self.display_mode == 'others' or self._opening_protected()
                 or self._opening_book_lines() or self._cloud_book_lines()) and self._is_human_turn()):
                self._target_accuracy_candidate()
                status = self.recommendation_accuracy_status
            else:
                status = 'Target accuracy is off' if not self.recommendation_accuracy_enabled else 'Waiting for your turn'
            reference = f"Depth {self.analysis_depth} · UCI Elo {self.engine_uci_elo or 'unlimited'} · "
            if getattr(panel, '_last_quality_status', None) != reference + status:
                panel.recommendation_status_label.config(text=reference + status)
                panel._last_quality_status = reference + status
        self._maybe_fetch_cloud_lines()
        self._predict_opponent_move()
        self.update_engine_line_text()
        self.redraw_all_arrows()
        self.draw_evaluation_bar()
        self.draw_accuracy_overlay()
        self._maybe_coach_refresh()
        # Keep the accuracy panel summary header live (overall % counters etc).
        # Never wipes the per-move log entries below the separator.
        try:
            self.update_accuracy_display()
        except Exception:
            pass
        # Update extension internals status
        try:
            if hasattr(self, "control_panel") and hasattr(self.control_panel, "update_extension_status"):
                self.control_panel.update_extension_status()
        except Exception:
            pass
        # The visualizer stays focused on move arrows and their labels only.
        self.canvas.delete("position_explanation")
        self.canvas.delete("position_explanation_background")
        # Keep the floating external evaluation window live.
        if getattr(self, "external_eval", None) is not None:
            try:
                if self.external_eval.is_visible():
                    self.external_eval.refresh()
            except Exception:
                pass
    
    def update_accuracy_display(self):
        """Update the accuracy stats in the control panel (summary only, log preserved)."""
        self._rewrite_accuracy_summary_header()

    def on_resize(self, event):
        """OPTIMIZATION: Clear cache and batch redraw operations"""
        # During a title-strip drag the window moves but does not resize; skip
        # the redraws so the window follows the mouse smoothly (a final redraw
        # happens in _end_visualizer_drag).
        if getattr(self, "_drag_in_progress", False):
            return
        # Debounce: live resizing fires <Configure> many times per second.
        # Collapse the full redraw into a single pass shortly after the user
        # stops dragging, instead of re-drawing the whole board every event.
        if getattr(self, "_resize_after_id", None):
            try:
                self.root.after_cancel(self._resize_after_id)
            except Exception:
                pass
            self._resize_after_id = None
        self._resize_after_id = self.root.after(80, self._do_resize_redraw)

    def _do_resize_redraw(self):
        self._resize_after_id = None
        if not self.root.winfo_exists():
            return
        self._coord_cache.clear()
        self._sync_arrow_layer()
        self.draw_border()
        self.draw_grid()
        self.redraw_all_arrows()
        self.draw_evaluation_bar()

    # ELO preset -> depth mapping: weaker presets search shallower
    _ELO_DEPTH_MAP = {300: 1, 500: 3, 1200: 8, 1400: 10, 1600: 12, 2000: 16, 2400: 20}

    # Losing-streak depth boost (Feature: "still losing after ~6 moves"):
    # human-POV eval (pawns) below LOSING_STREAK_EVAL_THRESHOLD counts as losing.
    # After LOSING_STREAK_NEEDED consecutive losing completed positions, searches
    # get +LOSING_STREAK_DEPTH_BONUS plies; if the eval is STILL not positive or
    # neutral after LOSING_STREAK_STEP more losing positions, the bonus steps up
    # again (capped at LOSING_STREAK_MAX_BONUS).  Once the human reaches a clear
    # winning eval (>= LOSING_STREAK_RECOVERY_EVAL, e.g. +0.70) the boost stays
    # up for LOSING_STREAK_RECOVERY_MOVES more completed positions and only then
    # drops back to the persisted analysis_depth.  The boost is TRANSIENT:
    # analysis_depth (the persisted setting) never changes.
    LOSING_STREAK_EVAL_THRESHOLD = -1.00
    LOSING_STREAK_NEEDED = 6
    LOSING_STREAK_DEPTH_BONUS = 4
    LOSING_STREAK_STEP = 6
    LOSING_STREAK_MAX_BONUS = 12
    LOSING_STREAK_MAX_DEPTH = 50
    LOSING_STREAK_RECOVERY_EVAL = 0.70
    LOSING_STREAK_RECOVERY_MOVES = 2

    # Flag / clock-pressure mode (Feature: "flag the opponent on low time"):
    # trigger when the opponent's clock is below FLAG_OPP_MS_HARD ms, or below
    # FLAG_OPP_RATIO of our clock, AND the position is drawn-ish/equal
    # (|eval| < FLAG_EVAL_WINDOW) with enough material left to pressure with.
    # Among lines within FLAG_SCORE_WINDOW of the engine's best, the suggestion
    # re-order prefers ones that keep the game going (keeps queens / avoids
    # trades / most forcing) so the short-clock opponent has to find moves.
    FLAG_OPP_MS_HARD = 12000
    FLAG_OPP_RATIO = 0.40
    FLAG_EVAL_WINDOW = 0.75
    FLAG_SCORE_WINDOW = 0.40

    def update_engine_elo(self, elo_str):
        try:
            elo = int(elo_str)
            self.engine_elo = elo
            # Auto-set depth to match elo preset
            preset_depth = self._ELO_DEPTH_MAP.get(elo)
            if preset_depth is not None:
                self.analysis_depth = preset_depth
                try:
                    self.control_panel.depth_var.set(preset_depth)
                except Exception:
                    pass
            self._save_all_settings()
            if self.current_fen:
                self.analyse_position(self.current_fen, elo)
        except ValueError:
            pass

    def update_analysis_depth(self):
        try:
            depth = int(self.control_panel.depth_var.get())
        except (ValueError, tk.TclError):
            return
        if not InputValidator.validate_depth(depth):
            self.control_panel.depth_var.set(self.analysis_depth)
            return
        self.analysis_depth = depth
        self.control_panel.depth_var.set(depth)
        self._save_all_settings()
        if self.current_fen:
            self.analyse_position(self.current_fen, self.engine_elo)

    def _refresh_accuracy_selection(self, prefer_range=False):
        """Re-rank the completed snapshot without restarting the engine."""
        if prefer_range:
            self.recommendation_strength_first = False
            panel = getattr(self, 'control_panel', None)
            if panel is not None:
                panel.strength_first_var.set(False)
        self._accuracy_selection_cache = None
        self._save_all_settings()
        self.update_display()

    def toggle_recommendation_strength_first(self):
        self.recommendation_strength_first = self.control_panel.strength_first_var.get()
        self._refresh_accuracy_selection()

    def toggle_recommendation_accuracy(self):
        self.recommendation_accuracy_enabled = self.control_panel.recommendation_accuracy_var.get()
        self._save_all_settings()
        if self.current_fen:
            self.analyse_position(self.current_fen, self.engine_elo)
        else:
            self.update_display()

    def update_recommendation_accuracy_range(self):
        try:
            slider = self.control_panel.range_slider
            low = max(35, min(100, int(slider.min_val)))
            high = max(35, min(100, int(slider.max_val)))
        except Exception:
            return
        if low > high:
            low, high = high, low
        self.target_recommendation_accuracy_min, self.target_recommendation_accuracy_max = low, high
        self.target_recommendation_accuracy = round((low + high) / 2)
        prio = max(low, min(high, getattr(self, "target_recommendation_accuracy_priority", low)))
        self.target_recommendation_accuracy_priority = prio
        try:
            self.control_panel.priority_slider.set(prio)
            self.control_panel.update_recommendation_accuracy_priority_label()
            self.control_panel.update_recommendation_accuracy_range_label()
        except Exception:
            pass
        self._refresh_accuracy_selection(prefer_range=True)

    # Legacy name retained for integrations that call it directly.
    def update_recommendation_accuracy(self):
        self.update_recommendation_accuracy_range()

    def update_opponent_elo(self, elo_str):
        try:
            elo = int(elo_str)
            self.opponent_elo = elo
            self._save_all_settings()
            if self.current_fen:
                self.analyse_position(self.current_fen, self.engine_elo)
        except ValueError:
            pass

    def toggle_show_opponent_move(self):
        self.show_opponent_move = self.control_panel.show_opponent_move_var.get()
        self.redraw_all_arrows()
        self._save_all_settings()

    def select_primary_engine(self, idx):
        try:
            idx = int(idx)
            if 0 <= idx < len(self.engine_paths):
                self.rotation_anchor_index = idx
                self._rotation_cursor = 0
                self.start_engine()
                self._save_all_settings()
        except (ValueError, TypeError):
            return

    def toggle_engine_rotation(self):
        enabled = bool(self.control_panel.rotation_var.get())
        if enabled != self.engine_rotation_enabled:
            self.engine_rotation_enabled = enabled
            # Rebuild the live pool from the configured choices, releasing the
            # extra processes as soon as rotation is switched off.
            self.start_engine()
        self._save_all_settings()

    def update_rotation_count(self):
        try:
            count = max(1, min(len(self.engine_paths), int(self.control_panel.rotation_count_var.get())))
            changed = self.rotation_engine_count != count
            self.rotation_engine_count = count
            if changed and self.engine_rotation_enabled:
                self.start_engine()
            self._save_all_settings()
        except (ValueError, TypeError, tk.TclError):
            pass

    def toggle_accuracy_display(self):
        self.accuracy_display_enabled = self.control_panel.accuracy_var.get()
        self.update_display()
        self._save_all_settings()

    def set_player_color(self, color):
        previous_color = self._human_color()
        self.player_color = color
        self._update_scores_for_new_perspective(previous_color)
        # "auto" doesn't force a board flip — the board stays in its current
        # orientation and suggestions are shown for whoever is to move.
        if color != "auto":
            self.perspective = "white" if color == "w" else "black"
        self._coord_cache.clear()
        self.draw_border()
        self.draw_grid()
        self._save_all_settings()
        if self.current_fen:
            self.analyse_position(self.current_fen, self.engine_elo)

    def _send_setoption_to_all_engines(self, name, value):
        # Callers update persisted settings first; the session applies a snapshot.
        self._reanalyse_after_option_change()


    def update_engine_threads(self, value):
        """Set the number of CPU threads on all running engines."""
        try:
            cap = int(getattr(self, "engine_threads_max", 0) or 128)
            value = max(1, min(cap, int(value)))
        except (TypeError, ValueError):
            return
        self.engine_threads = value

        self._save_all_settings()
        self._reanalyse_after_option_change()

    def update_engine_hash(self, value):
        """Set the hash table size (MB) on all running engines."""
        try:
            cap = int(getattr(self, "engine_hash_max", 0) or 65536)
            value = max(16, min(cap, int(value)))
            # Hash tables should be multiples of 16 MB
            value = int(value / 16) * 16
        except (TypeError, ValueError):
            return
        self.engine_hash = value

        self._save_all_settings()
        self._reanalyse_after_option_change()

    def _send_command_to_all_engines(self, cmd):
        """Write a raw UCI command to every running engine process."""
        cmd = str(cmd)
        if not cmd.endswith("\n"):
            cmd += "\n"
        for engine in list(self.engines):
            process = engine.get("process")
            if process and process.poll() is None:
                try:
                    process.stdin.write(cmd)
                    process.stdin.flush()
                except (OSError, BrokenPipeError):
                    pass

    def _reanalyse_after_option_change(self):
        """Re-submit the current position so new Threads/Hash apply immediately.

        Routed through the UI queue because analyse_position touches Tk widgets
        and must run on the main thread; the same-FEN path calls
        analyse_position directly, bypassing the placement guard.
        """
        if not self.engines or not self.current_fen or not self.should_run:
            return
        self.last_analyzed_fen = None
        self.ui_queue.put(("analyse", self.current_fen, self.engine_elo,
                           getattr(self, "current_flipped", 0),
                           getattr(self, "current_playing_as", None)))

    def apply_all_extra_uci_options(self):
        self._save_all_settings()
        self._reanalyse_after_option_change()



    def update_multi_pv_count(self):
        try:
            extras = max(0, min(7, int(self.control_panel.extra_moves_var.get())))
        except (ValueError, tk.TclError):
            return
        self.multi_pv_count = extras + 1
        self.control_panel.extra_moves_var.set(extras)
        self._save_all_settings()
        if self.current_fen:
            self.analyse_position(self.current_fen, self.engine_elo)

    def update_refresh_speed(self):
        try:
            value = max(50, min(2000, int(self.control_panel.refresh_speed_var.get())))
        except (ValueError, tk.TclError):
            return
        self.gui_update_delay = value
        self._gui_update_delay = value
        self.control_panel.refresh_speed_var.set(value)
        self._save_all_settings()

    def analyse_position(self, fen, elo):
        """Start a fresh, validated MultiPV search using the live GUI settings (uses ELO)."""
        if not InputValidator.validate_fen(fen):
            return
        
        # Calculate dynamic depth by position complexity if enabled
        search_depth = self.analysis_depth
        if getattr(self, "dynamic_depth_enabled", False):
            complexity = self._position_complexity(fen)
            delta = round(complexity * self.dynamic_depth_max_bonus - (1.0 - complexity) * self.dynamic_depth_max_reduction)
            search_depth = max(1, self.analysis_depth + delta)

        # Losing-streak depth boost: if the human has been losing for the last N
        # completed positions, temporarily search deeper until the eval goes back
        # positive.  Transient — analysis_depth (the persisted setting) is never
        # changed, and a boosted search uses its own (fen, depth) cache slot.
        losing_boost = self._losing_depth_boost()
        if losing_boost:
            search_depth = min(self.LOSING_STREAK_MAX_DEPTH, search_depth + losing_boost)
            print(f"[LosingStreak] {self._losing_streak} losing positions, "
                  f"depth {self.analysis_depth} -> {search_depth}")

        if self.only_own_turn and fen.split()[1] != self._human_color():
            for engine in self.engines:
                if engine.get('session'):
                    engine['session'].cancel()
            self.current_fen = fen
            self.multi_pvs.clear()
            self.committed_multi_pvs.clear()
            self.pv_line = []
            self.best_move = None
            self.pv_ready_to_play = False
            self._suggestions_fen = None
            self._analysis_status('skipped')
            self.trigger_gui_update()
            return
        if self.engines and all(e.get('session') and e['session'].state == 'search'
                and not e['session'].discard_active
                and e.get('_active_signature') == self._search_signature(e, fen, search_depth)
                for e in self.engines):
            for e in self.engines:
                e['request_id'] = (self._analysis_request or {}).get('request_id')
            self._analysis_status('started', self.engines[self.primary_engine_index])
            return
        self._suggestions_fen = None
        cache_context = tuple(self._search_signature(e, '', search_depth) for e in self.engines)
        if getattr(self, '_cache_context', None) != cache_context:
            self._position_cache = PositionCache(max_size=256, ttl_seconds=300)
            self._cache_context = cache_context
        cache_depth = search_depth
        if self.engines:
            primary = self.engines[min(self.primary_engine_index, len(self.engines) - 1)]
            cache_depth = self._effective_search_depth(primary, search_depth)
        # Reuse only analysis that actually reached this engine's requested depth.
        if hasattr(self, "_position_cache"):
            read_depth = getattr(self._position_cache, "get_with_depth", None)
            cached = (read_depth(fen, cache_depth) if callable(read_depth)
                      else self._position_cache.get(fen, cache_depth))
            if cached and self.engines and len(cached[0]) < self._expected_pvs(primary, fen):
                cached = None
            if cached:
                cached_pvs, cached_score = cached[:2]
                stored_depth = cached[2] if len(cached) > 2 else cache_depth
                badge_depth = stored_depth if len(cached) > 2 else f"≥{cache_depth}"
                self.current_fen = fen
                self.current_player_color = fen.split()[1]
                self.pool.current_fen = fen
                self.pool.clear_analysis(fen)
                self.multi_pvs = dict(cached_pvs)
                self.committed_multi_pvs = dict(cached_pvs)
                self._arrow_depth_snapshot = (fen, badge_depth, self.committed_multi_pvs)
                self.pv_line = list(cached_pvs.get(1, ([], ""))[0])
                self.committed_pv_line = list(self.pv_line)
                self.best_move = self.pv_line[0] if self.pv_line else None
                self.pv_ready_to_play = bool(self.best_move)
                self.current_depth = stored_depth
                label = str(cached_pvs.get(1, ([], ""))[1])
                self.current_is_mate = label.upper().startswith(("FM", "EM"))
                self.current_mate_label = label if self.current_is_mate else None
                self.current_score = cached_score
                self.current_score_fen = fen
                self.last_analyzed_fen = fen
                # Idle any in-flight engine search so live PVs from another
                # position cannot clobber the cached suggestions on screen.
                for engine in self.engines:
                    if engine.get('session'):
                        engine['session'].cancel()
                    engine['accepting_info'] = False
                self._suggestions_fen = fen
                self.depth_timer.cache_hit(fen, stored_depth)
                self._analysis_status('cached')
                self._eval_bar_last_draw = 0.0
                self.trigger_gui_update()
                print(f"[Cache Hit] Instant position resolution for depth {search_depth}")
                return

        # ADDED: Handle engine rotation on new moves
        if not self.engines:
            self.trigger_gui_update()
            return
        # A failed launch/restart may have changed the live engine list.
        self.primary_engine_index = min(max(0, self.primary_engine_index), len(self.engines) - 1)
        if fen != self.last_analyzed_fen:
            pool = self._rotation_pool()
            if self.engine_rotation_enabled and len(pool) > 1:
                # Use the selected primary for the first position, then rotate.
                if self.last_analyzed_fen is not None:
                    self._rotation_cursor = (self._rotation_cursor + 1) % len(pool)
                self.primary_engine_index = pool[self._rotation_cursor]
                print(f"[Engine Rotation] Primary engine: {self.engines[self.primary_engine_index]['name']}")
            self.last_analyzed_fen = fen

        self.current_fen = fen
        self.current_player_color = fen.split()[1]
        self.pv_ready_to_play = False
        self.engines_ready.clear()
        self.move_already_played = False
        self.multi_pvs.clear()
        self.committed_multi_pvs.clear()   # Reset stable display copy for new position
        self.committed_pv_line = []
        self._style_fallback = {"aggressive": False, "defensive": False}
        self.pv_line = []
        self.best_move = None
        self.current_score = 0.0
        self.current_score_fen = None
        self.current_is_mate = False
        self.current_mate_label = None
        self._eval_bar_last_draw = 0.0   # Reset debounce so new position draws immediately


        # Sync pool with new position (thread-safe under pool lock)
        self.pool.clear_analysis(fen)

        # Do NOT copy the primary engine's stale PVs into the overlay here: they
        # belong to the previous position, and the overlay copy is now depth-gated
        # (see the PV handler).  The display must start empty and only be filled
        # when the new search passes the stability floor; otherwise stale
        # arrows for the wrong position would linger on screen for the whole
        # search and look like flickering suggestions.

        # CLEAR ARROWS IMMEDIATELY and refresh GUI to prevent flashing old moves
        # and to immediately render opening book moves if available (0ms latency).
        self.arrow_canvas.delete("arrow")
        self.arrow_canvas.delete("premove")
        self._stop_arrow_pulse()
        self.opponent_move = None
        self.trigger_gui_update()
        
        for engine in self.engines:
            process = engine.get("process")
            if process is None or process.poll() is not None:
                # Dead or never-started engine: restart it so suggestions keep flowing.
                if self.should_run:
                    threading.Thread(target=self._restart_engine, args=(engine,), daemon=True).start()
                continue

            self._dispatch_engine_search(engine, fen, search_depth)

    def _effective_search_depth(self, engine, depth):
        depth = max(1, int(depth))
        if self._is_lc0_engine(engine):
            depth = min(7, max(4, round(depth * 0.4)))
        cap = engine.get("depth_cap")
        return min(depth, max(1, int(cap))) if cap else depth

    def _dispatch_engine_search(self, engine, fen, search_depth=None):
        if engine.get('_retired'):
            return
        if search_depth is None:
            search_depth = self.analysis_depth
        signature = self._search_signature(engine, fen, search_depth)
        engine['_request_signature'] = signature
        session = engine.get('session')
        if session is None:
            return
        request = dict(getattr(self, '_analysis_request', None) or {})
        def start(applied):
            if engine.get('_retired') or self.current_fen != fen or engine.get('session') is not session:
                return False
            engine['_active_signature'] = signature
            engine['_search_cache'] = self._position_cache
            engine['request_id'] = request.get('request_id')
            engine['applied_options'] = applied
            return self._begin_engine_search(engine, fen, search_depth)
        session.submit(signature, self._search_options(engine), start)

    def _root_move_count(self, fen):
        try:
            import chess
            return chess.Board(fen).legal_moves.count()
        except ImportError:
            return self._check_evasion_count(fen)
        except ValueError:
            return None

    def _expected_pvs(self, engine, fen):
        requested = self._requested_pvs(engine)
        legal = self._root_move_count(fen)
        return max(1, min(requested, legal)) if legal is not None else requested

    def _check_evasion_count(self, fen):
        """Count standard-chess check replies when python-chess is unavailable.

        Reuse board/attack helpers. Castling is never legal while in check.
        Unknown or non-check positions retain the normal requested PV count.
        """
        try:
            fields = fen.split()
            board = self._parse_fen_board(fields[0])
            white = fields[1] == 'w'
            king = 'K' if white else 'k'
            if sum(p == king for p in board.values()) != 1 or sum(p == king.swapcase() for p in board.values()) != 1:
                return None
            king_sq = next(sq for sq,p in board.items() if p == king)
            if not self._coach_square_defended(board, king_sq, white):
                return None
            ep = fields[3]
            ep_sq = (8-int(ep[1]))*8+'abcdefgh'.index(ep[0]) if ep != '-' else None
            count = 0
            for src, piece in board.items():
                if piece.isupper() != white:
                    continue
                row, col = divmod(src, 8)
                targets = self._coach_attacks_from(piece, src, board)
                if piece.lower() == 'p':
                    step = -8 if white else 8
                    targets = {dst for dst in targets if dst in board and board[dst].isupper() != white}
                    if ep_sq is not None and ep_sq in self._coach_attacks_from(piece, src, board):
                        behind = ep_sq-step
                        if ep_sq not in board and board.get(behind) == ('p' if white else 'P'):
                            targets.add(ep_sq)
                    if 0 <= src+step < 64 and src+step not in board:
                        targets.add(src+step)
                        if row == (6 if white else 1) and src+2*step not in board:
                            targets.add(src+2*step)
                for dst in targets:
                    captured = board.get(dst)
                    if captured and (captured.isupper() == white or captured.lower() == 'k'):
                        continue
                    updated = dict(board)
                    updated.pop(src)
                    updated[dst] = piece
                    if piece.lower() == 'p' and dst == ep_sq and captured is None:
                        updated.pop(dst-(-8 if white else 8), None)
                    king_after = dst if piece.lower() == 'k' else king_sq
                    if not self._coach_square_defended(updated, king_after, white):
                        count += 4 if piece.lower() == 'p' and dst//8 in (0,7) else 1
            return count
        except (ValueError, IndexError, StopIteration, TypeError):
            return None

    def _begin_engine_search(self, engine, fen, search_depth=None):
        """Send MultiPV, position and go depth commands to an idle engine."""
        if engine.get("_retired", False):
            return False
        process = engine.get("process")
        if process is None or process.poll() is not None:
            if self.should_run:
                threading.Thread(target=self._restart_engine, args=(engine,), daemon=True).start()
            return False
        try:
            if search_depth is None:
                search_depth = self.analysis_depth
                if getattr(self, "dynamic_depth_enabled", False):
                    complexity = self._position_complexity(fen)
                    delta = round(complexity * self.dynamic_depth_max_bonus - (1.0 - complexity) * self.dynamic_depth_max_reduction)
                    search_depth = max(1, self.analysis_depth + delta)
                losing_boost = self._losing_depth_boost()
                if losing_boost:
                    search_depth = min(self.LOSING_STREAK_MAX_DEPTH, search_depth + losing_boost)

            send_depth = self._effective_search_depth(engine, search_depth)

            pv_count = int(engine.get('applied_options', {}).get('multipv', 1))
            legal_count = self._root_move_count(fen)
            engine["_legal_moves"] = legal_count
            engine["_multipv_count"] = min(pv_count, legal_count) if legal_count is not None else pv_count
            engine["_multipv_count"] = max(1, engine["_multipv_count"])
            engine["_pv_batches"] = {}
            engine["_committed_depth"] = 0
            engine["_committed_complete"] = False
            engine["_committed_fen"] = None
            engine["no_legal_moves_fen"] = None
            engine["multi_pvs"] = {}
            engine["committed_multi_pvs"] = {}
            engine["time_ms"] = engine["nodes"] = engine["nps"] = 0
            engine["target_depth"] = send_depth
            engine["current_depth"] = 0
            engine["search_generation"] = engine.get("search_generation", 0) + 1
            engine["active_fen"] = fen
            engine["current_search_fen"] = fen
            engine["search_fen"] = fen
            engine["pending_search_fen"] = None
            engine["is_stopping"] = False
            try:
                engine["active_turn"] = fen.split()[1]
            except Exception:
                engine["active_turn"] = self.current_player_color if self.current_player_color in ("w", "b") else "w"
            engine["accepting_info"] = True
            engine["last_output_time"] = time.time()

            limits = f"depth {send_depth}"
            if self.analysis_time_ms:
                limits += f" movetime {self.analysis_time_ms}"
            if self.analysis_nodes:
                limits += f" nodes {self.analysis_nodes}"
            if self.engines and engine is self.engines[self.primary_engine_index]:
                engine['_depth_timing_ticket'] = self.depth_timer.begin(
                    fen, send_depth, engine.get('name', 'Engine'), engine['_multipv_count'])
            process.stdin.write(f"position fen {fen}\ngo {limits}\n")
            process.stdin.flush()
            self._analysis_status('started', engine)
            return True
        except (OSError, BrokenPipeError):
            threading.Thread(target=self._restart_engine, args=(engine,), daemon=True).start()
            return False

    def _check_and_start_pending_search(self, engine):
        # UciSession owns pending requests and waits for bestmove + readyok.
        return


    def update_connection_indicator(self):
        color = "green" if self.connected_clients > 0 else "red"
        self.control_panel.update_connection_indicator(color)

    def select_engine(self):
        """Open file dialog to select chess engine executables and start them immediately."""
        try:
            filetypes = [
                ("Chess Engines (*.exe; *.bin)", ("*.exe", "*.bin", "*.ini", "*")),
                ("All Files (*.*)", "*.*")
            ]
            paths = filedialog.askopenfilenames(title="Select Chess Engine Executables", filetypes=filetypes)
            if not paths:
                path = filedialog.askopenfilename(title="Select Chess Engine Executable", filetypes=filetypes)
                if path:
                    paths = [path]

            if paths:
                new_idx = None
                for path in paths:
                    norm = os.path.abspath(path)
                    if norm not in self.engine_paths:
                        self.engine_paths.append(norm)
                    new_idx = self.engine_paths.index(norm)
                if new_idx is not None:
                    self.rotation_anchor_index = new_idx
                print(f"[Engine Select] Loaded engine paths: {self.engine_paths}")
                self.control_panel.update_engine_pool_controls()
                self.start_engine()
        except Exception as err:
            print(f"[Engine Select Error]: {err}")

    def start_engine(self):
        """Start only the selected engine, or the full pool with rotation on."""
        old_engines = self.engines
        self.engines = []
        for engine in old_engines:
            engine["_retired"] = True
        for engine in old_engines:
            self._retire_engine(engine)

        self.engines_ready.clear()
        self.pool.engines = []
        self.pool._info_by_engine.clear()
        self.pool._ranked_moves = []
        self.last_analyzed_fen = None
        # Cached suggestions belong to the old engine pool.
        self._position_cache = PositionCache(max_size=256, ttl_seconds=300)
        self.primary_engine_index = 0
        self._rotation_cursor = 0
        if not self.engine_paths:
            self.control_panel.update_engine_pool_controls()
            return

        self.rotation_anchor_index = max(0, min(self.rotation_anchor_index, len(self.engine_paths) - 1))
        selected_path = self.engine_paths[self.rotation_anchor_index]
        count = min(len(self.engine_paths), max(1, self.rotation_engine_count))
        paths_to_start = ([self.engine_paths[(self.rotation_anchor_index + i) % len(self.engine_paths)]
                           for i in range(count)] if self.engine_rotation_enabled else [selected_path])

        for path in paths_to_start:
            is_socket = str(path).startswith("socket://")
            engine_name = (str(path)[len("socket://"):].strip("/")
                           if is_socket else os.path.splitext(os.path.basename(path))[0])
            engine = {"path": path, "name": engine_name, "pv_line": [],
                    "score": None, "best_move": None, "process": None, "final_pv": [],
                    "search_fen": None, "current_search_fen": None, "pending_search_fen": None,
                    "accepting_info": True, "restart_lock": threading.Lock(),
                    "restart_in_progress": False, "search_generation": 0}
            self.engines.append(engine)
            self.pool.register(engine)
            
            try:
                if is_socket:
                    self.start_socket_engine(engine, path)
                elif self.is_polyglot_engine(path):
                    self.start_polyglot_engine(engine, path)
                else:
                    engine_path = os.path.abspath(path)
                    engine_dir = os.path.dirname(engine_path)
                    
                    engine["process"] = subprocess.Popen(
                        [engine_path], 
                        stdin=subprocess.PIPE, 
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, 
                        text=True, 
                        bufsize=1,  # line-buffered text UCI protocol
                        cwd=engine_dir,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
                    )
                    _assign_process_to_job(engine["process"])
                
                self.initialize_engine_uci(engine)
                threading.Thread(target=self.read_engine_output, args=(engine,), daemon=True).start()
            except Exception as e:
                self._retire_engine(engine)
                self.engines = [eng for eng in self.engines if eng is not engine]
                self.pool.unregister(engine)
                print(f"[Engine Load Error] Failed to start '{path}': {e}")

        self.primary_engine_index = next(
            (i for i, eng in enumerate(self.engines) if eng.get("path") == selected_path), 0)
        self.control_panel.update_engine_pool_controls()

        # A page may have sent its initial position before Start Engine was clicked.
        # Analyse it now so a new match never needs a browser refresh.
        if self.current_fen and self.engines:
            self.analyse_position(self.current_fen, self.engine_elo)

    def start_socket_engine(self, engine, path):
        """Connect a hosted (socket) engine.  No local binary is needed: the
        remote host runs the engine and streams raw UCI over one WebSocket."""
        engine_id = str(path)[len("socket://"):].strip("/")
        base = (getattr(self, "hosted_engine_base", "") or
                "wss://ProtonnDev-engine.hf.space").strip().rstrip("/")
        url = f"{base}/{engine_id}"
        engine["socket_url"] = url
        engine["socket_id"] = engine_id
        cap = self._hosted_engine_depth_cap(engine_id)
        if cap:
            engine["depth_cap"] = cap
        engine["process"] = SocketEngineProcess(url, name=engine_id)
        print(f"[Engine] Connected hosted engine '{engine_id}' @ {url}")

    def _hosted_engine_depth_cap(self, engine_id):
        """Recommended depth ceiling per hosted engine family (matches the
        BetterMint socket catalog).  Remote searches cost a round-trip per
        position, so capping avoids multi-minute waits."""
        eid = str(engine_id or "").lower()
        if eid.startswith("maia"):
            return 7
        if eid.startswith(("rodent3", "patricia", "komodo")):
            return 14
        if eid.startswith("fairy"):
            return 16
        if eid.startswith("stockfish"):
            return 16
        return None

    def add_hosted_engine(self):
        """Open a picker for hosted (no-download) engines and add one to the pool."""
        if websocket_client is None:
            try:
                import ctypes as _ct
                _ct.windll.user32.MessageBoxW(
                    0, "websocket-client is not installed.\n\n"
                       "Run in a terminal:  pip install websocket-client\n"
                       "then restart the tool.", "Hosted Engine", 0x40)
            except Exception:
                print("[Hosted Engine] pip install websocket-client is required.")
            return
        if not hasattr(self, "control_panel") or self.control_panel is None:
            return
        self.control_panel._open_hosted_engine_dialog()

    def start_websocket_server(self):
        self._ws_clients = set()
        self._ws_loop = None

        async def server_main():
            self._ws_loop = asyncio.get_event_loop()
            async with websockets.serve(self.handle_client, "localhost", 8085) as server:
                await asyncio.Future()

        threading.Thread(target=lambda: asyncio.run(server_main()), daemon=True).start()

    def ws_broadcast(self, msg_dict):
        """Send a JSON message to all connected extension clients."""
        if not hasattr(self, "_ws_clients") or not self._ws_clients:
            return
        text = json.dumps(msg_dict)
        loop = getattr(self, "_ws_loop", None)
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(self._ws_broadcast_async(text), loop)

    async def _ws_broadcast_async(self, text):
        if not self._ws_clients:
            return
        dead = set()
        for ws in list(self._ws_clients):
            try:
                await ws.send(text)
            except Exception:
                dead.add(ws)
        self._ws_clients -= dead

    async def handle_client(self, websocket):
        self.connected_clients += 1
        if hasattr(self, "_ws_clients"):
            self._ws_clients.add(websocket)
        self.ui_queue.put(("connection",))
        try:
            async for message in websocket:
                if not isinstance(message, str) or len(message) > 8192:
                    continue
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue
                action = data.get("type") or data.get("action")
                if action == "bridge_ping":
                    await websocket.send(json.dumps({"type": "bridge_pong"}))
                elif action == "analysis":
                    await self.handle_analysis_request(data)
                elif action == "move_played":
                    # A move just happened: blank the board immediately so a
                    # queued premove arrow cannot linger after the enemy has
                    # moved while the new position's analysis is on its way.
                    # Clock data is queued too so state is only ever mutated
                    # on the Tk thread (the old code wrote overlay state
                    # directly from this asyncio thread).
                    self.ui_queue.put((
                        "clocks",
                        {"self_ms": data.get("self_ms"),
                         "opp_ms": data.get("opp_ms"),
                         "src": "move_played"},
                    ))
                    self.ui_queue.put(("premove_clear",))
                elif action == 'game_context':
                    self.ui_queue.put(('game_context',data))
                elif action == "clocks":
                    self.ui_queue.put((
                        "clocks",
                        {"self_ms": data.get("self_ms"),
                         "opp_ms": data.get("opp_ms"),
                         "src": "clocks"},
                    ))
                elif action == "variant_detected":
                    self.extension_variant = data.get("variant")
                elif action == "site_exploits":
                    self.extension_exploits = data.get("features", [])
                elif action == "promotion_geometry":
                    self._accept_promotion_geometry(data)
                elif action == "board_geometry":
                    self.ui_queue.put(("board_geometry", data))
                elif action == "ui_state":
                    self.ui_queue.put(("ui_state", data))
                elif action == "privacy_state":
                    self.extension_privacy_hidden = data.get("hidden", False)
        finally:
            self.connected_clients = max(0, self.connected_clients - 1)
            if hasattr(self, "_ws_clients"):
                self._ws_clients.discard(websocket)
            self.ui_queue.put(("connection",))

    async def handle_analysis_request(self, data):
        fen = data.get("fen")
        flipped = data.get("flipped", 0)
        playing_as = data.get("playing_as")
        if not InputValidator.validate_fen(fen):
            return
        metadata = {'request_id': str(data.get('request_id', ''))[:160],
                    'epoch': str(data.get('epoch', 'legacy'))[:160], 'fen': fen,
                    'game_context': clean_context(data.get('game_context'))}
        self.ui_queue.put(("analysis", fen, self.engine_elo, flipped, playing_as,
                           bool(data.get("resync")), metadata))

    # --- Phase 5: Extension command senders (called from GUI thread) ---
    def send_extension_command(self, cmd_dict):
        """Send a command to all connected extension clients via the UI queue."""
        self.ui_queue.put(("ext_cmd", json.dumps(cmd_dict)))

    def execute_move_on_site(self, uci):
        """Ask the app to execute a move on the chess site.

        Uses the real physical OS mouse drag (hardware relative events) whenever
        board geometry is available; falls back to visualizer overlay geometry.
        """
        geom = self._get_board_geometry(wait_ms=1000)
        if not (geom and geom.get("ok") and geom.get("width")):
            geom = getattr(self, "_canvas_board_geometry_cache", None)
            if not (geom and geom.get("ok") and geom.get("width")):
                geom = self._canvas_board_geometry()

        if geom and geom.get("ok") and geom.get("width"):
            am = getattr(self, "automove", None)
            if am:
                self._real_mouse_drag(uci, am.generate_plan(uci), geom)
                if len(uci) > 4:
                    threading.Thread(target=self._native_promotion,
                        args=(uci[4], getattr(self, '_automove_epoch', None), False), daemon=True).start()
        else:
            print("[AutoMove] Warning: No board geometry available for real mouse drag.")

    def update_automove_status_label(self):
        """Push current AutoMove state into the control panel status label."""
        panel = getattr(self, "control_panel", None)
        if panel is None:
            return
        am = getattr(self, "automove", None)
        enabled = bool(am is not None and getattr(am, "enabled", False))
        reason = getattr(self, "_automove_last_reason", "disabled")
        try:
            if hasattr(panel, "automove_status_label"):
                if enabled:
                    if reason in ("disabled", "no FEN yet"):
                        reason = "waiting for board data"
                    text = f"Auto: ON — {reason}"
                    color = "#10B981" if reason == "committed — dragging" \
                        else "#FBBF24"
                    panel.automove_status_label.config(text=text, fg=color)
                else:
                    panel.automove_status_label.config(
                        text="Auto: OFF", fg="#8B7BAE")
        except Exception:
            pass

    def set_privacy_hidden(self, hidden):
        """Ask the extension to hide/show the overlay."""
        self.extension_privacy_hidden = hidden
        self.send_extension_command({"action": "set_privacy", "hidden": hidden})

    def panic(self):
        """Emergency hide: close overlay, stop engines, wipe state."""
        self.extension_panicked = True
        self.send_extension_command({"action": "panic"})
        self.stealth_mode_enabled = True
        self._stop_arrow_pulse()
        self._hide_arrow_layer()
        self.root.after(0, lambda: hide_from_screen_capture(self.root.winfo_id(), True))
        if hasattr(self, "control_panel"):
            self.control_panel.root.after(0, lambda: hide_from_screen_capture(
                self.control_panel.root.winfo_id(), True))
        print("[PANIC] All visuals hidden")

    def request_extension_clocks(self):
        """Ask the extension for current clock times."""
        self.send_extension_command({"action": "get_clocks"})

    def request_extension_exploits(self):
        """Ask the extension what site exploits are available."""
        self.send_extension_command({"action": "get_exploits"})

    def _bottom_piece_color(self, fen, flipped=None):
        """Return the colour occupying the bottom of the board ('w' or 'b').
        If flipped flag from browser extension is provided, use it directly (1 = Black bottom, 0 = White bottom).
        """
        if flipped is not None:
            try:
                fl = int(flipped)
                return "b" if fl == 1 else "w"
            except (ValueError, TypeError):
                pass
        if not fen or not isinstance(fen, str):
            return "w"
        try:
            board = self._parse_fen_board(fen.split()[0])
            rows = {"w": [], "b": []}
            kings = {"w": 7, "b": 0}
            for square, piece in board.items():
                row = square // 8
                color = "w" if piece.isupper() else "b"
                rows[color].append(row)
                if piece.lower() == "k":
                    kings[color] = row

            if not rows["w"] or not rows["b"]:
                return "w"

            white_mean = sum(rows["w"]) / len(rows["w"])
            black_mean = sum(rows["b"]) / len(rows["b"])

            if white_mean > black_mean + 0.15:
                return "w"
            elif black_mean > white_mean + 0.15:
                return "b"

            return "w" if kings["w"] >= kings["b"] else "b"
        except (AttributeError, IndexError, ValueError):
            return "w"

    def _sync_player_to_bottom(self, fen, flipped=None, playing_as=None):
        """When player_color is 'auto', sync active perspective and detected player side from board bottom.
        Lock the detected side once a match starts so manual move play never flips your color mid-game.
        """
        if flipped is None:
            flipped = getattr(self, "current_flipped", 0)
            
        curr_det = getattr(self, "detected_player_color", None)
        if curr_det not in ("w", "b") or not getattr(self, "previous_fen", None):
            if playing_as in ("w", "b"):
                side = playing_as
            else:
                screen, age = self._screen_bottom_result()
                if screen in ("w", "b") and age < 15.0:
                    side = screen
                elif flipped == 1:
                    side = "b"
                elif flipped == 0 and playing_as == "w":
                    side = "w"
                else:
                    side = self._bottom_piece_color(fen, None)
            self.detected_player_color = side
        else:
            side = self.detected_player_color
        
        perspective = "white" if side == "w" else "black"
        changed = False
        if self.player_color == "auto":
            if self.perspective != perspective:
                self.perspective = perspective
                self._coord_cache.clear()
                self.draw_border()
                self.draw_grid()
                changed = True
        
        fen_parts = fen.split()
        if len(fen_parts) >= 2:
            self.current_player_color = fen_parts[1]
            
        if changed:
            self.redraw_all_arrows()


    def apply_analysis_request(self, fen, elo, flipped=0, playing_as=None, resync=False):
        # Apply turn only after the placement guard accepts this FEN.
        self.current_flipped = flipped
        # chess.com's authoritative "which side is the human" (game.getPlayingAs
        # via fenProvider).  Drives detected_player_color so queuing again and
        # getting black actually switches the auto side chooser to black — the
        # FEN flipped flag alone is not reliable for that.
        self.current_playing_as = playing_as if playing_as in ("w", "b") else None

        # 2. Check if this is a new game (reset to starting position).  This runs
        #    BEFORE the preview guard below: a fresh game's first FEN is a totally
        #    different board that would otherwise be mistaken for a transient
        #    drag preview and ignored.
        starting_fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        def _is_new_game(prev, cur):
            if cur == starting_fen:
                return prev != cur
            if not prev:
                return False
            try:
                prev_mv = int(prev.split()[5])
                cur_mv  = int(cur.split()[5])
                if cur_mv < prev_mv:          # counter went backwards
                    return True
                if not resync and cur_mv - prev_mv > 5:      # jumped forward suspiciously
                    return True
            except (IndexError, ValueError):
                pass
            return False
        if _is_new_game(self.previous_fen, fen):
            self.depth_timer.reset()
            self._adaptive_history = {"w": [], "b": []}
            self._line_memory = LineMemory()
            self._accuracy_selection_cache = None
            self._validated_book_cache = None
            # Reset all game state for new match
            self.move_history = []
            self.white_stats = {"perfect": 0, "great": 0, "good": 0, "inaccuracy": 0, "mistake": 0, "blunder": 0, "total": 0}
            self.black_stats = {"perfect": 0, "great": 0, "good": 0, "inaccuracy": 0, "mistake": 0, "blunder": 0, "total": 0}
            self.overall_accuracy = 100.0
            self._pending_move = None
            self._losing_streak = 0
            self.previous_score = None
            self.previous_best_move = None
            self.previous_analysis_fen = None
            self.last_analyzed_fen = None
            self._last_real_fen = fen
            # Fresh match: re-arm AutoMove for the new game (a previous game's
            # game-over gate or already-played FEN must not carry over).
            self._reset_automove_for_new_game()
            # The board is back at the clean start position: let the screen
            # detector re-read the side and re-orient the perspective once.
            self._screen_perspective_locked = False
            # Flush stale queued FENs so accepting_info is never stuck False
            for _eng in self.engines:
                _eng["search_fen"] = None
                _eng["accepting_info"] = True
            # Wipe and seed the accuracy log for the new game
            try:
                if hasattr(self, "control_panel") and hasattr(self.control_panel, "accuracy_text"):
                    tw = self.control_panel.accuracy_text
                    tw.config(state=tk.NORMAL)
                    tw.delete("1.0", tk.END)
                    tw.insert(tk.END, "New game — waiting for moves...\n")
                    tw._summary_sep_exists = False
                    tw.config(state=tk.DISABLED)
                    tw.see(tk.END)
            except Exception:
                pass
            self.update_stats_display()
            # A fresh match resets the anchor; analyse immediately so the
            # placement guard below never treats the new game as a no-op.
            self.current_fen = fen
            self.previous_fen = fen
            self.current_player_color = fen.split()[1]
            self._sync_player_to_bottom_safe(fen, flipped, playing_as)
            self.analyse_position(fen, elo)
            return

        # 3. Placement-based preview / artifact guard.  The browser board omits a
        #    piece while it is being dragged (`.dragging` filter), so a FEN whose
        #    placement differs from the last confirmed position by EXACTLY ONE
        #    removed piece and NO added piece is a mid-drag preview — never feed
        #    it to the engines (the board really is missing that piece, and
        #    analysing it looks like the piece was lost / a move was played).
        #    A FEN with the SAME placement is a duplicate push, a reload, or a
        #    cancelled-drag artifact (the extension derives its turn field from
        #    board diffs, so it flips wrongly when a piece is picked up and put
        #    back).  The position did not change, so keep the current analysis.
        #    This must NOT depend on the turn field: it can drift, and a stale
        #    anchor would otherwise swallow a real move as a "preview".
        if resync:
            # Reconnect snapshots cannot reconstruct moves during the gap.
            self._last_real_fen = fen
            self.previous_fen = fen
            self._pending_move = None
            self.last_analyzed_fen = None
        anchor = self._last_real_fen if self._last_real_fen else self.previous_fen
        if anchor:
            try:
                same_placement = anchor.split()[0] == fen.split()[0]
                context_changed = anchor.split()[1:] != fen.split()[1:]
                turn_changed = (len(anchor.split()) > 1 and len(fen.split()) > 1 and
                                anchor.split()[1] != fen.split()[1])
                rem, add = self._placement_delta(anchor, fen)
            except (IndexError, AttributeError):
                same_placement = False
                context_changed = False
                turn_changed = False
                rem = add = 0
            if same_placement or (rem == 1 and add == 0):
                if same_placement and (context_changed or resync):
                    # Turn-only correction: the running-clock turn changed while
                    # the placement stayed identical (e.g. a mid-game join where
                    # the extension committed a guessed side-to-move before its
                    # clock detection came up, then corrected it).  Re-anchor
                    # and re-analyse so the suggested side follows the corrected
                    # turn; this is not a move and must not touch move tracking.
                    self.current_player_color = fen.split()[1]
                    self._last_real_fen = fen
                    self.previous_fen = fen
                else:
                    if same_placement:
                        self._sync_player_to_bottom_safe(fen, flipped, playing_as)
                        self.trigger_gui_update()
                    self.previous_fen = fen
                    return

        self.current_fen = fen
        self.current_player_color = fen.split()[1]

        # 4. Sync board orientation and bottom piece detection if Auto mode is active.
        #    (Only changes perspective; never overwrites current_player_color anymore.)
        #    Runs AFTER the preview guard so a piece-lift can never flip the board.
        self._sync_player_to_bottom_safe(fen, flipped, playing_as)

        # 5. Validate the FEN transition BEFORE considering it a real move.
        #    IMPORTANT: always use _last_real_fen as the anchor, not previous_fen.
        #    previous_fen may equal a mid-drag / piece-hold preview FEN that won't
        #    satisfy the board-diff rule against the *actual* landing position,
        #    which was the cause of "sometimes buggy while holding a piece".
        if anchor and anchor != fen:
            if self._fen_transition_is_real_move(anchor, fen):
                self._track_move_from_positions(anchor, fen)
                self._last_real_fen = fen
            else:
                # Board transitioned to a completely different position (e.g. new puzzle loaded,
                # match navigation, board reset). Update anchor so subsequent moves in this new
                # puzzle/position are compared against this board, not the stale old puzzle!
                self._last_real_fen = fen
                self._pending_move = None

        # Ensure _last_real_fen is always initialised so we don't re-enter Nones
        if self._last_real_fen is None:
            self._last_real_fen = fen

        # Update previous_fen for the visual loop (always tracks most recent FEN)
        self.previous_fen = fen

        # 6. Skip re-analysis when this exact position is already being searched.
        #    analyse_position clears the overlay and resets the depth tracker to 0,
        #    so a duplicate arrival would restart the in-flight search and make a
        #    depth-gated display look frozen for many seconds.  Genuine re-analyses
        #    (engine restarts, ELO / MultiPV changes) either call analyse_position
        #    directly or reset last_analyzed_fen first, so they bypass this skip.
        if fen != self.last_analyzed_fen:
            self.analyse_position(fen, elo)

    def _placement_delta(self, prev_fen, new_fen):
        """Return (removed, added) piece counts between two FEN placements.

        Square-based, so a quiet move reads as (1, 1), a lift-preview as
        (1, 0), a capture as (2, 1), castling as (2, 2).
        """
        try:
            old_board = self._parse_fen_board(prev_fen.split()[0])
            new_board = self._parse_fen_board(new_fen.split()[0])
            removed = sum(1 for sq in old_board
                          if old_board[sq] != new_board.get(sq))
            added = sum(1 for sq in new_board
                        if new_board[sq] != old_board.get(sq))
            return removed, added
        except Exception:
            return 0, 0

    def _fen_transition_is_real_move(self, prev_fen, new_fen):
        """Return True only if prev→new looks like a real, completed move.

        Classification relies on the board placement diff ONLY.  The
        extension's turn field can drift (derived from board diffs, it flips
        wrongly after a cancelled drag) and its fullmove counter is always
        "0 1", so requiring a turn flip or a counter increment would reject
        valid White moves and freeze move tracking after the first move.
        Piece-lift previews never reach this check: apply_analysis_request
        filters them first via the 1-removed/0-added placement rule.
        """
        try:
            move = self.detect_move_from_fen(prev_fen, new_fen)
            if not move or len(move) < 4:
                return False
            if not (move[0] in "abcdefgh" and move[1] in "12345678" and
                    move[2] in "abcdefgh" and move[3] in "12345678"):
                return False
            return True
        except Exception:
            return False

    def _sync_player_to_bottom_safe(self, fen, flipped=None, playing_as=None):
        """Resolve Auto's side without changing the FEN's side to move.

        Explicit extension metadata takes priority. Screen detection is a fallback
        that stays locked for the match, so covering the board cannot change sides.
        """
        previous_color = self._human_color()
        detected = getattr(self, "detected_player_color", None)
        locked = getattr(self, "_screen_perspective_locked", False)
        if playing_as in ("w", "b"):
            side = playing_as
        elif locked and detected in ("w", "b"):
            side = detected
        else:
            screen, age = self._screen_bottom_result()
            if screen in ("w", "b") and age < 12.0:
                side = screen
            else:
                if flipped is None:
                    flipped = getattr(self, "current_flipped", 0)
                side = self._bottom_piece_color(fen, flipped)
        self.detected_player_color = side
        self._update_scores_for_new_perspective(previous_color)
        if self.player_color == "auto":
            perspective = "white" if side == "w" else "black"
            if self.perspective != perspective and (not locked or playing_as in ("w", "b")):
                self.perspective = perspective
                self._coord_cache.clear()
                self.draw_border()
                self.draw_grid()
                self.redraw_all_arrows()
            if playing_as in ("w", "b") or self._board_in_opening():
                self._screen_perspective_locked = True

    def _mover_color_from_uci(self, fen, move):
        """Return the moving piece colour from a UCI source square, if known."""
        try:
            if not move or len(move) < 4:
                return None
            file_i = ord(move[0].lower()) - ord("a")
            rank_i = int(move[1]) - 1
            if not (0 <= file_i < 8 and 0 <= rank_i < 8):
                return None
            piece = self._parse_fen_board(fen.split()[0]).get((7 - rank_i) * 8 + file_i)
            if piece:
                return "w" if piece.isupper() else "b"
        except Exception:
            pass
        return None

    def _track_move_from_positions(self, prev_fen, new_fen):
        """
        Track a move by analyzing the FEN change and comparing engine evaluations.
        Calculates centipawn loss between pre-move and post-move evaluations.
        """
        try:
            if not self.engines:
                return

            # Extract the actual move played
            actual_move = self.detect_move_from_fen(prev_fen, new_fen)
            if not actual_move:
                return

            if hasattr(self,'_line_memory'):
                self._line_memory.advance(prev_fen,actual_move,new_fen)

            # FEN turn can drift in the source extension; infer the mover from
            # the moved piece and use the turn field only as a last fallback.
            whose_move = self._mover_color_from_uci(prev_fen, actual_move)
            if whose_move not in ("w", "b"):
                whose_move = prev_fen.split()[1] if len(prev_fen.split()) > 1 else None
            if whose_move not in ("w", "b"):
                return

            # A move was just played by whose_move, so the next side to move MUST be
            # the opposite color! This guarantees turn detection never desyncs.
            next_turn = "b" if whose_move == "w" else "w"
            self.current_player_color = next_turn

            # In Auto mode, during puzzles the puzzle plays first:
            # If the puzzle opponent just moved (whose_move), the human is next_turn.
            if getattr(self, "player_color", "auto") == "auto":
                if not getattr(self, "_screen_perspective_locked", False):
                    self.detected_player_color = next_turn

            # The grade needs the evaluation of the PRE-move position.  Normally
            # the previous analysis committed on prev_fen before the move, so we
            # can snapshot it here.  After a new-game reset (or when the engine
            # analysis lags a fast game) that snapshot is missing/stale.  We
            # still record the move and DEFER the grade: the engine's analysis
            # of prev_fen arrives shortly afterwards as a superseded bestmove
            # (see _complete_deferred_move) and is used to grade it, instead of
            # silently killing accuracy tracking for the rest of the game.
            anchored = (self.previous_score is not None
                        and self.previous_analysis_fen == prev_fen)
            self._pending_move = {
                "move": actual_move,
                "pre_score": self.previous_score if anchored else None,
                "pre_best_move": self.previous_best_move if anchored else None,
                "pre_analysis_fen": prev_fen,
                "opening": self._accuracy_opening_exempt(prev_fen),
                "post_fen": new_fen,
                "side": whose_move
            }

            # Record _last_real_fen so transient drag previews can't clobber tracking
            self._last_real_fen = new_fen

            _p = prev_fen.split()
            _n = new_fen.split()
            print(f"[MOVEDETECT] move={actual_move} side={whose_move} anchored={anchored} "
                  f"pre_score={self.previous_score if anchored else None} "
                  f"hum={self._human_color()} "
                  f"prev_turn={_p[1] if len(_p) > 1 else '?'} prev_mv={_p[5] if len(_p) > 5 else '?'} "
                  f"new_turn={_n[1] if len(_n) > 1 else '?'} new_mv={_n[5] if len(_n) > 5 else '?'}")

        except Exception:
            pass
    
    def _complete_move_tracking(self, post_score=None):
        """Complete move tracking when post-move evaluation arrives.

        post_score is the committed White-POV evaluation of the post-move
        position (defaults to the current committed score).  When the pre-move
        snapshot is still missing the grade is deferred (the engine's analysis
        of the pre-move position arrives as a superseded bestmove and is
        applied via _complete_deferred_move) instead of dropping the move and
        leaving accuracy tracking frozen for the whole game.
        """
        if not self._pending_move:
            return

        try:
            pending = self._pending_move
            # A pending move whose destination is no longer current is
            # ungradeable (its post-move evaluation was superseded).  Drop it
            # so the next real move can take its place.
            if pending.get("post_fen") and pending["post_fen"] != self.current_fen:
                print(f"[MOVEGRADE] DROP {pending.get('move')} side={pending.get('side')}: "
                      f"post_fen != current_fen")
                self._pending_move = None
                return
            if pending.get("pre_score") is None:
                print(f"[MOVEGRADE] DEFER {pending.get('move')} side={pending.get('side')}: "
                      f"pre_score None")
                return  # deferred — graded when the pre-move analysis arrives
            if self.current_score_fen != pending.get("post_fen"):
                print(f"[MOVEGRADE] SKIP {pending.get('move')} side={pending.get('side')}: "
                      f"current_score_fen != post_fen")
                return  # never grade with a stale/pre-move score
            self._grade_pending_move(pending, post_score
                                     if post_score is not None
                                     else self.current_score)
            print(f"[MOVEGRADE] OK {pending.get('move')} side={pending.get('side')} "
                  f"pre={pending.get('pre_score')} "
                  f"post={post_score if post_score is not None else self.current_score}")
            self._pending_move = None
        except Exception:
            self._pending_move = None

    def _grade_pending_move(self, pending, post_score):
        """Grade a tracked move against a pre-move and post-move evaluation."""
        pre_score = pending["pre_score"]
        whose_move = pending["side"]

        # Calculate centipawn loss (perspective matters).  All scores from the
        # engine are from White's perspective.
        if whose_move == 'w':
            # A drop in White's evaluation is loss for White.
            cp_loss = (pre_score - post_score) * 100
        else:
            # A rise in White's evaluation is loss for Black.
            cp_loss = (post_score - pre_score) * 100

        # Ensure cp_loss is non-negative
        cp_loss = max(0, cp_loss)

        # A played engine-best move is never downgraded because the
        # follow-up search evaluated the position slightly differently.
        is_best = (str(pending.get("pre_best_move") or "").lower()
                   == str(pending.get("move") or "").lower())
        if is_best:
            cp_loss = 0.0

        # Track the move
        self.track_move(
            pending["move"],
            is_best_move=is_best,
            cp_loss=cp_loss,
            whose_move=whose_move,
            pre_score=pre_score,
            post_score=post_score,
            better_move=pending.get("pre_best_move"),
            opening=pending.get("opening", False),
        )

    def _complete_deferred_move(self, completed_fen, pre_score):
        """Grade a move whose pre-move analysis arrived late (as a superseded
        bestmove).  Only applied while the game is still on the move's
        destination, so the committed score is that position's evaluation."""
        try:
            pending = self._pending_move
            if (not pending or pending.get("pre_score") is not None
                    or pending.get("pre_analysis_fen") != completed_fen
                    or pending.get("post_fen") != self.current_fen):
                return
            pending["pre_score"] = pre_score
            pending["pre_best_move"] = None
            self._grade_pending_move(pending, self.current_score)
            print(f"[MOVEGRADE] DEFERRED-OK {pending['move']} side={pending['side']} pre={pre_score}")
            self._pending_move = None
        except Exception:
            pass

    def _update_losing_streak(self):
        """Advance/reset the consecutive-losing-positions counter.

        Called once per completed analysis (anchored at bestmove time, right
        after _complete_move_tracking), so it yields exactly one data point per
        position/move.  The streak only grows while the HUMAN is clearly losing
        (eval below LOSING_STREAK_EVAL_THRESHOLD), holds steady in the gray zone
        (slightly worse up to LOSING_STREAK_RECOVERY_EVAL), and only winds down
        after LOSING_STREAK_RECOVERY_MOVES consecutive positions at or above
        LOSING_STREAK_RECOVERY_EVAL (e.g. +0.70), so the depth boost does not
        cut out on the very first positive position.  White-POV current_score is
        flipped by _human_color."""
        try:
            us = self._human_color()
            human = self.current_score if us == "w" else -(self.current_score or 0.0)
            recovery = getattr(self, "_depth_recovery_count", 0)
            if human < self.LOSING_STREAK_EVAL_THRESHOLD:
                self._losing_streak += 1
                self._depth_recovery_count = 0
            elif human >= self.LOSING_STREAK_RECOVERY_EVAL:
                # Clearly winning: count toward recovering the depth boost.
                self._depth_recovery_count = recovery + 1
                if self._depth_recovery_count >= self.LOSING_STREAK_RECOVERY_MOVES:
                    self._losing_streak = 0
                    self._depth_recovery_count = 0
            else:
                # Gray zone (still slightly worse, or only marginally better):
                # keep the boost running, but restart the recovery count.
                self._depth_recovery_count = 0
        except Exception:
            pass

    def _losing_depth_boost(self):
        """Disabled: depth remains fixed as configured by the user."""
        return 0

    def _losing_streak_boost(self):
        """Disabled: depth remains fixed as configured by the user."""
        return 0

    def _flag_mode_active(self):
        """True for the classic "flag them" situation: the opponent is short on
        time in a drawn-ish, roughly equal endgame (e.g. KQ vs KQ, 10s vs 45s).
        Requires both clocks known, the opponent under FLAG_OPP_MS_HARD ms (or
        under FLAG_OPP_RATIO of ours), a near-equal evaluation, and enough
        material left on the board to actually pressure with."""
        try:
            clocks = getattr(self, "extension_clocks", {}) or {}
            self_ms = clocks.get("self_ms")
            opp_ms = clocks.get("opp_ms")
            if self_ms is None or opp_ms is None or self_ms <= 0 or opp_ms <= 0:
                return False
            if not (opp_ms <= self.FLAG_OPP_MS_HARD or
                    opp_ms <= self.FLAG_OPP_RATIO * self_ms):
                return False
            if abs(self.current_score or 0.0) > self.FLAG_EVAL_WINDOW:
                return False
            # Only flag once the engine has actually produced a ranked line
            # for THIS position; otherwise a stalled engine would make every
            # drawn-looking position look like a flagging opportunity.
            ranked = getattr(self, "committed_multi_pvs", None) or {}
            if not any(isinstance(r, int) and r >= 1 for r in ranked):
                return False
            fen = self.current_fen
            if not fen:
                return False
            board = self._parse_fen_board(fen.split()[0])
            non_king = sum(1 for p in board.values() if p.lower() != "k")
            return non_king >= 2
        except Exception:
            return False

    def _apply_flag_mode_reorder(self, entries):
        """Reorder engine suggestion entries so the primary pick pressures the
        clock.  Within FLAG_SCORE_WINDOW of the engine's best line, prefer moves
        that keep the game going instead of the quickest draw:
          * never trade/capture the opponent's last queen (dead draw),
          * avoid trading pieces (drags toward a draw the opponent can blitz),
          * longer/forcing PVs give the flagging opponent more to find.
        A genuine mate for us always wins the pick regardless."""
        try:
            our_color = self._human_color()
            best_human = max(e["human"] for e in entries)

            def mate_for_us(e):
                return str(e["score_str"]).upper().lstrip("+-").startswith(("FM", "M"))

            board = self._parse_fen_board(self.current_fen.split()[0])
            # The opponent's queen: if the human is White the enemy queen is
            # black (lowercase 'q'); the human's own queen is never "the last
            # queen to flag against".
            enemy_q = "Q" if our_color == "b" else "q"

            def _sq_index(sq):
                try:
                    return (8 - int(sq[1])) * 8 + (ord(sq[0]) - ord('a'))
                except Exception:
                    return None

            def flag_key(e):
                mv = e["moves"][0] if e["moves"] else ""
                keeps_q = 1.0
                no_trade = 1.0
                if len(mv) >= 4:
                    dest_piece = board.get(_sq_index(mv[2:4]))
                    if dest_piece == enemy_q:
                        keeps_q = 0.0
                    elif dest_piece is not None and dest_piece.lower() not in ("k", "p"):
                        no_trade = 0.4
                return keeps_q * 10.0 + no_trade * 2.0 + min(4.0, e["pv_len"] / 4.0)

            elig = [e for e in entries
                    if mate_for_us(e) or best_human - e["human"] <= self.FLAG_SCORE_WINDOW]
            if not elig:
                return entries

            elig.sort(key=lambda e: (0 if mate_for_us(e) else 1, -flag_key(e), -e["human"]))
            top = elig[0]
            rest = [e for e in entries if e["rank"] != top["rank"]]
            rest.sort(key=lambda e: e["rank"])
            return [top] + rest
        except Exception:
            return entries

    def _engine_watchdog(self):
        try:
            for engine in list(self.engines):
                session = engine.get('session')
                if session and session.timed_out() and self.should_run:
                    threading.Thread(target=self._restart_engine, args=(engine,), daemon=True).start()
        finally:
            if self.should_run and self.root.winfo_exists():
                self._watchdog_timer = self.root.after(1000, self._engine_watchdog)


    def _periodic_gc(self):
        """Periodic background cleanup running every 30s."""
        try:
            import gc
            # Prune position move history
            if len(self.move_history) > 60:
                self.move_history = self.move_history[-60:]
            # Evict expired LRU cache entries
            if hasattr(self, "_position_cache"):
                self._position_cache.evict_expired()
            now = time.time()
            for engine in list(self.engines):
                proc = engine.get("process")
                if proc and proc.poll() is not None:
                    # The reader thread may already have exited; restart a dead
                    # engine so suggestions keep flowing instead of freezing.
                    engine["process"] = None
                    if self.should_run:
                        threading.Thread(target=self._restart_engine, args=(engine,), daemon=True).start()
        except Exception as e:
            print(f"[GC Error]: {e}")
        finally:
            if self.should_run and hasattr(self, "root") and self.root.winfo_exists():
                self._gc_timer = self.root.after(30000, self._periodic_gc)

    def _position_complexity(self, fen):
        """Calculate position complexity score between 0.0 (simple) and 1.0 (highly tactical/complex)."""
        if not fen or not isinstance(fen, str):
            return 0.5
        try:
            board = self._parse_fen_board(fen.split()[0])
            piece_count = len(board)
            piece_factor = min(1.0, max(0.0, (piece_count - 6) / 22.0))

            queens = sum(1 for p in board.values() if p.lower() == 'q')
            rooks = sum(1 for p in board.values() if p.lower() == 'r')
            tactical_factor = min(1.0, (queens * 0.25 + rooks * 0.15))

            complexity = (piece_factor * 0.6) + (tactical_factor * 0.4)
            return max(0.0, min(1.0, complexity))
        except Exception:
            return 0.5

    def shutdown(self):
        self.should_run = False
        try:
            self._screen_detector_stop.set()
        except Exception:
            pass
        if getattr(self, "_automove_tick_id", None):
            try:
                self.root.after_cancel(self._automove_tick_id)
            except Exception:
                pass
            self._automove_tick_id = None
        if getattr(self, "_autosave_after", None):
            try:
                self.root.after_cancel(self._autosave_after)
            except Exception:
                pass
            self._autosave_after = None
        try:
            self._save_all_settings()
        except Exception:
            pass
        try:
            self._settings_writer.close()
        except Exception as exc:
            print(f"Final settings save error: {exc}")
        # Clean up engine pool
        if hasattr(self, "pool"):
            try:
                self.pool.engines = []
                self.pool._info_by_engine.clear()
                self.pool._ranked_moves = []
            except Exception:
                pass
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass
        for engine in self.engines:
            process = engine.get("process")
            if process and process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except Exception:
                    try: process.kill()
                    except Exception: pass
        try:
            if hasattr(self, "control_panel") and self.control_panel.root.winfo_exists():
                self.control_panel.root.destroy()
        except tk.TclError:
            pass
        if getattr(self, "_arrow_layer_ready", False):
            try:
                self.arrow_layer.destroy()
            except Exception:
                pass
        if hasattr(self, "external_eval") and self.external_eval is not None:
            try:
                self.external_eval.destroy()
            except Exception:
                pass
        self.root.destroy()

def show_nebula_welcome(root, launch):
    """Single branded welcome/login screen that fades into the visualizer."""
    root.withdraw()
    splash = tk.Toplevel(root)
    splash.title("Chess Suite")
    splash.configure(bg="#0b0a12")
    splash.resizable(False, False)
    splash.geometry("480x360")
    splash.attributes("-topmost", True)

    card = tk.Frame(splash, bg="#161223",
                    highlightbackground="#3b2f66", highlightthickness=1)
    card.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)

    # Logo
    logo = tk.Canvas(card, width=96, height=96, bg="#161223",
                     highlightthickness=0)
    logo.pack(pady=(34, 8))
    logo.create_oval(3, 3, 93, 93, fill="#5d3fd3", outline="#7a5cff",
                     width=2)
    logo.create_text(48, 48, text="\u265f", fill="#ffffff",
                     font=("Segoe UI", 44))

    tk.Label(card, text="Chess Suite", bg="#161223", fg="#f5f3ff",
             font=("Segoe UI", 24, "bold")).pack()
    tk.Label(card, text="NEBULA EDITION", bg="#161223", fg="#a78bfa",
             font=("Segoe UI", 9, "bold")).pack(pady=(2, 0))
    tk.Label(card, text="your analysis space is waking up\u2026", bg="#161223",
             fg="#8b8fa3", font=("Segoe UI", 9)).pack(pady=(14, 0))

    # Loading bar
    bar = tk.Canvas(card, width=240, height=6, bg="#0b0a12",
                    highlightthickness=0)
    bar.pack(pady=(20, 0))

    started = [False]

    def _open():
        if started[0]:
            return
        started[0] = True
        try:
            splash.destroy()
        except Exception:
            pass
        root.deiconify()
        try:
            root.attributes("-alpha", 0.0)
        except Exception:
            pass
        launch()
        def _fade(i=0):
            try:
                root.attributes("-alpha", i / 12)
            except Exception:
                pass
            if i < 12:
                root.after(15, lambda: _fade(i + 1))
            else:
                try:
                    root.attributes("-alpha", 1.0)
                except Exception:
                    pass
        _fade()

    # Circular start button (click to skip the wait).
    start_btn = tk.Canvas(card, width=44, height=44, bg="#161223",
                          highlightthickness=0, cursor="hand2")
    start_btn.pack(pady=(16, 0))
    start_btn.create_oval(2, 2, 42, 42, fill="#5d3fd3", outline="#7a5cff",
                          width=2, tags="ov")
    start_btn.create_text(22, 23, text="\u25b6", fill="#ffffff",
                          font=("Segoe UI", 14), tags="gl")

    def _btn_enter(_e):
        start_btn.itemconfigure("ov", fill="#6d4fe0")
    def _btn_leave(_e):
        start_btn.itemconfigure("ov", fill="#5d3fd3")
    start_btn.bind("<Enter>", _btn_enter)
    start_btn.bind("<Leave>", _btn_leave)
    start_btn.bind("<Button-1>", lambda _e: _open())

    step = [0]

    def _fill():
        step[0] += 1
        bar.delete("all")
        w = int(240 * (step[0] / 24))
        if w > 1:
            bar.create_rectangle(0, 0, w, 6, fill="#7a5cff", outline="")
        if step[0] < 24:
            splash.after(50, _fill)
        else:
            _open()

    splash.after(300, _fill)

if __name__ == "__main__":
    root = tk.Tk()
    show_nebula_welcome(root, lambda: ChessOverlay(root))
    root.mainloop()
