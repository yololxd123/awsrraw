import os
import sys
import time
import re
import threading
import queue
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog
from engine_pool import SocketEngineProcess
from analysis_helpers import (
    ANALYZER_MATE, ANALYZER_CLS_ORDER, ANALYZER_CLS_COLORS, _ANALYZER_GLYPHS,
    _score_to_cp, _material_for, _win_prob, _classify_move, _move_accuracy, _fmt_eval,
)

class _AnalyzerEngine:
    """A dedicated single UCI engine session used ONLY for offline PGN analysis.

    Fully separate from the live game pool so an analysis run can never disturb
    the suggestions on the board.  Works with local binaries and with hosted
    socket engines alike."""

    def __init__(self, path, base_url="wss://ProtonnDev-engine.hf.space",
                 threads=2, hash_mb=256):
        self.path = path
        self._seq = 0
        self._cur_seq = 0
        self._results = {}
        self._lock = threading.Lock()
        self._uciok = threading.Event()
        self._proc = None
        self._depth_cap = None
        is_socket = str(path).startswith("socket://")
        if is_socket:
            eid = str(path)[len("socket://"):].strip("/")
            base = (base_url or "wss://ProtonnDev-engine.hf.space").strip().rstrip("/")
            self._proc = SocketEngineProcess(f"{base}/{eid}", name=eid)
            self._depth_cap = self._family_cap(eid)
        else:
            p = os.path.abspath(path)
            self._proc = subprocess.Popen(
                [p], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
                cwd=os.path.dirname(p),
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        threading.Thread(target=self._read_loop, daemon=True).start()
        try:
            self._handshake(threads, hash_mb)
        except Exception:
            self.close()
            raise

    @staticmethod
    def _family_cap(eid):
        eid = str(eid or "").lower()
        if eid.startswith("maia"):
            return 7
        if eid.startswith(("rodent3", "patricia", "komodo")):
            return 14
        if eid.startswith(("fairy", "stockfish")):
            return 16
        return None

    def _handshake(self, threads, hash_mb):
        proc = self._proc
        try:
            proc.stdin.write("uci\n")
            proc.stdin.flush()
        except Exception as exc:
            raise RuntimeError(f"cannot talk to engine: {exc}")
        if not self._uciok.wait(timeout=25):
            raise RuntimeError("engine did not answer uci")
        for cmd in (f"setoption name Threads value {int(threads or 2)}",
                    f"setoption name Hash value {int(hash_mb or 256)}",
                    "setoption name MultiPV value 1",
                    "isready"):
            try:
                proc.stdin.write(cmd + "\n")
                proc.stdin.flush()
            except Exception:
                pass

    def _read_loop(self):
        proc = self._proc
        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                self._handle_line(line)
        except Exception:
            pass

    def _handle_line(self, line):
        parts = line.split()
        lower = [p.lower() for p in parts]
        if "uciok" in lower:
            self._uciok.set()
            return
        with self._lock:
            seq = self._cur_seq
            if seq not in self._results:
                return
            res = self._results[seq]
        if "info" in lower:
            sc = _score_to_cp(parts)
            if sc is not None:
                res["score"] = sc
            try:
                pv_i = lower.index("pv")
                if pv_i + 1 < len(parts):
                    res["pv"] = parts[pv_i + 1:]
            except ValueError:
                pass
        elif "bestmove" in lower:
            try:
                bm_i = lower.index("bestmove")
                res["bestmove"] = parts[bm_i + 1]
                res["done"] = True
            except (IndexError, ValueError):
                pass

    def evaluate(self, fen, depth):
        with self._lock:
            self._seq += 1
            seq = self._seq
            self._cur_seq = seq
            res = {"score": None, "bestmove": None, "pv": [], "done": False}
            self._results[seq] = res
        d = max(1, int(depth))
        if self._depth_cap:
            d = min(d, int(self._depth_cap))
        try:
            self._proc.stdin.write(f"position fen {fen}\ngo depth {d}\n")
            self._proc.stdin.flush()
        except Exception:
            return None
        end = time.monotonic() + 120
        while time.monotonic() < end:
            with self._lock:
                r = self._results.get(seq)
                if r and r.get("done"):
                    break
            time.sleep(0.02)
        with self._lock:
            r = self._results.pop(seq, None)
        if not r or not r.get("done"):
            self.close()
            raise TimeoutError("engine search did not finish")
        return {"score": r.get("score"), "best_move": r.get("bestmove"),
                "pv": r.get("pv", []), "depth": d}

    def close(self):
        try:
            self._proc.stdin.write("quit\n")
            self._proc.stdin.flush()
        except Exception:
            pass
        try:
            if hasattr(self._proc, "terminate"):
                self._proc.terminate()
        except Exception:
            pass


class PGNAnalyzerWindow:
    """chess.com-style PGN game analyzer.

    Paste (or load) a PGN and press Analyse: the primary engine evaluates every
    position, then the window shows both players' accuracy, per-move
    classifications (brilliant / best / excellent / good / inaccuracy / miss /
    mistake / blunder), an interactive board with eval bar and a clickable
    move list.
    """

    BG        = "#13101c"
    PANEL     = "#181427"
    CARD      = "#1d1830"
    LINE      = "#2b2444"
    TEXT      = "#ece8ff"
    MUTED     = "#9a92c9"
    DIM       = "#6f6796"
    ACCENT    = "#7a5cff"
    ACCENT_DIM = "#4a3a8f"
    LIGHT_SQ  = "#f0d9b5"
    DARK_SQ   = "#b58863"
    HILITE    = "#f7e07b"
    GREEN     = "#4ade80"
    RED       = "#f87171"
    YELLOW    = "#f5c542"
    CELL      = 44

    def __init__(self, overlay):
        self.overlay = overlay
        self.game = None
        self.result = "*"
        self.moves = []          # per-move dicts (after classification)
        self.fens = []           # fens[ply] == position before move ply
        self.evals = []          # per-position dicts {cp, side, best_move, pv}
        self._cur_idx = 0
        self._thread = None
        self._cancel = False
        self._callbacks = queue.Queue()
        self._prog = (0, 0)
        self._busy = False
        self.book_plies = 8
        self.analysis_depth = int(getattr(overlay, "analysis_depth", 18) or 18)

        self.root = tk.Toplevel(overlay.root)
        self.root.title("PGN Analyzer")
        self.root.geometry("1120x740")
        self.root.minsize(920, 580)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=self.BG)
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Left>", lambda _e: self._nav(-1))
        self.root.bind("<Right>", lambda _e: self._nav(1))
        self._poll()

    # ---------------- helpers ----------------

    def _post(self, fn):
        # Worker threads never call Tk, even to schedule a callback.
        if not self._cancel:
            self._callbacks.put(fn)


    def _pick_engine(self):
        ov = self.overlay
        # The live pool contains only the selected engine when rotation is off;
        # its index need not match the saved engine-path list.
        engines = getattr(ov, "engines", None) or []
        if engines:
            idx = min(max(0, getattr(ov, "primary_engine_index", 0)), len(engines) - 1)
            if engines[idx].get("path"):
                return engines[idx]["path"]
        paths = getattr(ov, "engine_paths", None) or []
        if not paths:
            return None
        idx = min(max(0, getattr(ov, "rotation_anchor_index",
                              getattr(ov, "primary_engine_index", 0))), len(paths) - 1)
        return paths[idx]

    def _set_status(self, text, color=None):
        try:
            self.status_lbl.config(text=text, fg=color or self.MUTED)
        except Exception:
            pass

    # ---------------- ui build ----------------

    def _build(self):
        bg, panel, card = self.BG, self.PANEL, self.CARD
        text, muted, dim = self.TEXT, self.MUTED, self.DIM
        accent = self.ACCENT

        # ---- input deck ----
        inp = tk.Frame(self.root, bg=panel)
        inp.pack(fill=tk.X)
        top = tk.Frame(inp, bg=panel)
        top.pack(fill=tk.X, padx=12, pady=(10, 4))
        tk.Label(top, text="\u265e  PGN GAME ANALYZER", bg=panel, fg="#d9d4f0",
                 font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT)
        tk.Button(top, text="Load .pgn file", command=self._load_file,
                  bg=self.ACCENT_DIM, fg=text, relief=tk.FLAT,
                  font=("Segoe UI", 9, "bold"), padx=10, pady=5,
                  cursor="hand2").pack(side=tk.RIGHT)
        tk.Button(top, text="Analyse Game", command=self._start_analysis,
                  bg=accent, fg=text, relief=tk.FLAT,
                  font=("Segoe UI", 9, "bold"), padx=10, pady=5,
                  cursor="hand2").pack(side=tk.RIGHT, padx=(0, 8))

        self.pgn_text = tk.Text(inp, height=5, bg=card, fg=text,
                                insertbackground=text, relief=tk.FLAT,
                                font=("Consolas", 9), wrap=tk.NONE,
                                padx=8, pady=6, highlightthickness=1,
                                highlightbackground=self.LINE,
                                undo=True)
        self.pgn_text.pack(fill=tk.X, padx=12, pady=(0, 6))
        self.pgn_text.insert("1.0", "[Event \"?\"]\n[Site \"?\"]\n"
                                   "[White \"White\"]\n[Black \"Black\"]\n"
                                   "[Result \"*\"]\n\n1. e4 e5 2. Nf3 Nc6")

        opts = tk.Frame(inp, bg=panel)
        opts.pack(fill=tk.X, padx=12, pady=(0, 10))
        tk.Label(opts, text="Engine depth", bg=panel, fg=muted,
                 font=("Segoe UI", 8)).pack(side=tk.LEFT)
        self.depth_var = tk.IntVar(value=self.analysis_depth)
        tk.Spinbox(opts, from_=4, to=40, width=4, textvariable=self.depth_var,
                   justify="center", bg=card, fg=text, insertbackground=text,
                   buttonbackground=self.ACCENT_DIM, relief=tk.FLAT,
                   font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(4, 12))
        tk.Label(opts, text="Book plies (not graded)", bg=panel, fg=muted,
                 font=("Segoe UI", 8)).pack(side=tk.LEFT)
        self.book_var = tk.IntVar(value=self.book_plies)
        tk.Spinbox(opts, from_=0, to=40, width=4, textvariable=self.book_var,
                   justify="center", bg=card, fg=text, insertbackground=text,
                   buttonbackground=self.ACCENT_DIM, relief=tk.FLAT,
                   font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(4, 12))
        self.eng_lbl = tk.Label(opts, text="", bg=panel, fg=dim,
                                font=("Segoe UI", 8), anchor="e")
        self.eng_lbl.pack(side=tk.RIGHT)

        # ---- status / progress ----
        stat = tk.Frame(self.root, bg=bg)
        stat.pack(fill=tk.X, padx=12, pady=(2, 6))
        self.status_lbl = tk.Label(stat, text="Paste a PGN or load a file, then press Analyse Game.",
                                   bg=bg, fg=muted, font=("Segoe UI", 9), anchor="w")
        self.status_lbl.pack(fill=tk.X)
        self.prog_canvas = tk.Canvas(stat, height=6, bg=self.CARD,
                                     highlightthickness=0)
        self.prog_canvas.pack(fill=tk.X, pady=(6, 2))

        # ---- results area (filled after analysis) ----
        self.results = tk.Frame(self.root, bg=bg)
        self.results.pack(fill=tk.BOTH, expand=True)
        self._rendered = False

    def _draw_prog(self):
        try:
            c = self.prog_canvas
            w = c.winfo_width()
            h = c.winfo_height()
            done, total = self._prog
            fw = (done / total) if total else 0.0
            c.delete("all")
            c.create_rectangle(0, 0, w, h, fill=self.CARD, outline="")
            if w > 4 and fw > 0:
                c.create_rectangle(0, 0, int(fw * w), h, fill=self.ACCENT,
                                   outline="")
        except Exception:
            pass

    # ---------------- input actions ----------------

    def _load_file(self):
        path = filedialog.askopenfilename(
            title="Select a PGN file",
            filetypes=[("PGN files", "*.pgn *.PGN"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                data = fh.read()
            self.pgn_text.delete("1.0", "end")
            self.pgn_text.insert("1.0", data)
            self._set_status("PGN loaded: %s" % os.path.basename(path))
        except Exception as exc:
            self._set_status("Could not read file: %s" % exc, self.RED)

    def _start_analysis(self):
        if self._busy:
            return
        self.book_plies = max(0, int(self.book_var.get()))
        self.analysis_depth = max(1, int(self.depth_var.get()))
        pgn = self.pgn_text.get("1.0", "end").strip()
        if not pgn:
            self._set_status("Paste a PGN first.", self.YELLOW)
            return
        try:
            import io
            import chess as _c
            import chess.pgn as _pgn
        except ImportError:
            self._set_status("python-chess is required:  pip install python-chess", self.RED)
            return
        try:
            game = _pgn.read_game(io.StringIO(pgn))
        except Exception as exc:
            self._set_status("Could not parse PGN: %s" % exc, self.RED)
            return
        if game is None:
            self._set_status("No game found in that PGN.", self.RED)
            return
        path = self._pick_engine()
        if not path:
            self._set_status("Load an engine first (Engine Pool tab) to run the analysis.",
                             self.RED)
            return
        # prepare move metadata
        board = game.board()
        fens = [board.fen()]
        meta = []
        ply = 0
        for mv in game.mainline_moves():
            fen_before = board.fen()
            san = board.san(mv)
            player = "w" if board.turn else "b"
            fullmove = board.fullmove_number
            mat_before = _material_for(board, player == "w")
            board.push(mv)
            fens.append(board.fen())
            meta.append({"uci": mv.uci(), "san": san, "ply": ply,
                         "player": player, "fullmove": fullmove,
                         "mat_delta": _material_for(board, player == "w") - mat_before})
            ply += 1
        self.result = game.headers.get("Result") or game.result() or "*"
        self.game_white = game.headers.get("White") or "White"
        self.game_black = game.headers.get("Black") or "Black"
        self._cancel = False
        self._busy = True
        self._prog = (0, len(fens))
        self._draw_prog()
        self._set_status("Analysing %d positions at depth %d..." % (len(fens), self.analysis_depth),
                         self.YELLOW)
        ov = self.overlay
        base = getattr(ov, "hosted_engine_base", "wss://ProtonnDev-engine.hf.space")
        threads = max(1, min(getattr(ov, "engine_threads", 2) or 2, (os.cpu_count() or 2) // 2))
        hmb = min(getattr(ov, "engine_hash", 256) or 256, 256)
        self._thread = threading.Thread(
            target=self._worker,
            args=(game, fens, meta, self.analysis_depth, path, base, threads, hmb),
            daemon=True)
        self._thread.start()

    def _synthetic_eval(self, board, result):
        side = board.turn
        color = "w" if side else "b"
        if board.is_checkmate():
            return {"cp": -ANALYZER_MATE, "side": color,
                    "best_move": None, "pv": []}
        if board.is_stalemate() or board.is_insufficient_material() or result == "1/2-1/2":
            return {"cp": 0, "side": color, "best_move": None, "pv": []}
        if result == "1-0":
            return {"cp": ANALYZER_MATE if color == "w" else -ANALYZER_MATE,
                    "side": color, "best_move": None, "pv": []}
        if result == "0-1":
            return {"cp": -ANALYZER_MATE if color == "w" else ANALYZER_MATE,
                    "side": color, "best_move": None, "pv": []}
        return {"cp": 0, "side": color, "best_move": None, "pv": []}

    def _worker(self, game, fens, meta, depth, path, base, threads, hmb):
        import chess as _c
        eng = None
        try:
            eng = _AnalyzerEngine(path, base_url=base, threads=threads, hash_mb=hmb)
        except Exception as exc:
            self._post(lambda error=str(exc): self._set_status("Engine error: %s" % error, self.RED))
            self._busy = False
            return
        evals = []
        try:
            for i, fen in enumerate(fens):
                if self._cancel:
                    break
                self._prog = (i, len(fens))
                try:
                    b = _c.Board(fen)
                    if b.is_game_over():
                        evals.append(self._synthetic_eval(b, self.result))
                        continue
                except Exception:
                    pass
                res = eng.evaluate(fen, depth) if eng else None
                side = fen.split()[1]
                if res:
                    evals.append({"cp": res["score"], "side": side,
                                  "best_move": res["best_move"], "pv": res["pv"]})
                else:
                    evals.append({"cp": None, "side": side,
                                  "best_move": None, "pv": []})
            self._prog = (len(fens), len(fens))
        except Exception as exc:
            self._post(lambda error=str(exc): self._set_status(f"Analysis error: {error}", self.RED))
            self._busy = False
            return
        finally:
            try:
                eng.close()
            except Exception:
                pass
        if self._cancel:
            self._post(lambda: self._set_status("Analysis cancelled.", self.YELLOW))
            self._busy = False
            return
        self._classify(fens, meta, evals)
        self._post(self._render_results)
        self._post(lambda: self._set_status(
            "Analysis complete - %d positions at depth %d."
            % (len(fens), self.analysis_depth), self.GREEN))

    def _classify(self, fens, meta, evals):
        import chess as _c
        self.fens = fens
        self.evals = evals
        moves = []
        for k, mv in enumerate(meta):
            i = k
            ev_b = evals[i]
            ev_a = evals[i + 1] if i + 1 < len(evals) else None
            mover = mv["player"]
            best_cp = ev_b.get("cp") if ev_b else None
            best_move = ev_b.get("best_move") if ev_b else None
            win_before = _win_prob(best_cp) if best_cp is not None else None
            played_cp = None
            win_after = None
            if ev_a and ev_a.get("cp") is not None:
                played_cp = -ev_a["cp"]
                win_after = _win_prob(played_cp)
            is_best = bool(best_move and mv["uci"] == best_move)
            drop = max(0.0, (win_before - win_after) * 100.0) if win_before is not None and win_after is not None else None
            is_book = k < self.book_plies
            cls = _classify_move(win_before, win_after, is_best, played_cp,
                                 mv.get("mat_delta"), is_book)
            acc = 100.0 if is_book else (_move_accuracy(drop) if drop is not None else None)
            best_san = None
            if best_move:
                try:
                    best_san = _c.Board(fens[i]).san(_c.Move.from_uci(best_move))
                except Exception:
                    pass
            moves.append({"uci": mv["uci"], "san": mv["san"], "player": mover,
                          "ply": k, "fen_before": fens[i], "fen_after": fens[i + 1],
                          "best_cp": best_cp, "best_move": best_move,
                          "best_san": best_san, "played_cp": played_cp,
                          "win_before": win_before, "win_after": win_after,
                          "drop": drop, "cls": cls, "acc": acc,
                          "mat_delta": mv.get("mat_delta"),
                          "fullmove": mv.get("fullmove", k // 2 + 1)})
        self.moves = moves

    # ---------------- results rendering ----------------

    def _player_acc(self, player):
        accs = [m["acc"] for m in self.moves
                if m["player"] == player and m["cls"] != "book" and m["acc"] is not None]
        if not accs:
            return None
        return sum(accs) / len(accs)

    def _counts(self, player):
        from collections import Counter
        return Counter(m["cls"] for m in self.moves
                       if m["player"] == player and m["cls"] != "book")

    def _render_results(self):
        try:
            self._busy = False
            for child in self.results.winfo_children():
                child.destroy()
            bg, panel, card = self.BG, self.PANEL, self.CARD
            text, muted, dim = self.TEXT, self.MUTED, self.DIM

            white = self.game_white if hasattr(self, "game_white") else "White"
            black = self.game_black if hasattr(self, "game_black") else "Black"
            wa, ba = self._player_acc("w"), self._player_acc("b")

            # players + accuracy
            bar = tk.Frame(self.results, bg=panel)
            bar.pack(fill=tk.X, pady=(0, 6))
            wname = tk.Label(bar, bg=panel, font=("Segoe UI", 10, "bold"),
                             fg=self.TEXT, text="White  %s" % white)
            wname.pack(side=tk.LEFT, padx=(12, 4), pady=8)
            wacc = tk.Label(bar, bg=panel, font=("Segoe UI", 13, "bold"),
                            fg=self.GREEN,
                            text="%s%%" % ("%.1f" % wa if wa is not None else "\u2014"))
            wacc.pack(side=tk.LEFT, pady=8)
            res = tk.Label(bar, bg=panel, font=("Segoe UI", 10, "bold"),
                           fg=self.MUTED, text=self.result)
            res.pack(side=tk.LEFT, padx=18, pady=8)
            bacc = tk.Label(bar, bg=panel, font=("Segoe UI", 13, "bold"),
                            fg=self.GREEN,
                            text="%s%%" % ("%.1f" % ba if ba is not None else "\u2014"))
            bacc.pack(side=tk.RIGHT, padx=(4, 12), pady=8)
            bname = tk.Label(bar, bg=panel, font=("Segoe UI", 10, "bold"),
                             fg=self.TEXT, text="Black  %s" % black)
            bname.pack(side=tk.RIGHT, pady=8)

            # classification chips
            chips = tk.Frame(self.results, bg=bg)
            chips.pack(fill=tk.X, padx=12, pady=(0, 6))
            for player, counts in (("White", self._counts("w")),
                                   ("Black", self._counts("b"))):
                row = tk.Frame(chips, bg=bg)
                row.pack(fill=tk.X, pady=1)
                tk.Label(row, text=player, width=6, bg=bg, fg=dim,
                         font=("Segoe UI", 8, "bold"), anchor="w").pack(side=tk.LEFT)
                any_chip = False
                for cls in ANALYZER_CLS_ORDER:
                    n = counts.get(cls, 0)
                    if not n:
                        continue
                    any_chip = True
                    c = tk.Label(row, text="%s %d" % (cls.title(), n),
                                 bg=self._blend(ANALYZER_CLS_COLORS[cls], 0.22, self.CARD),
                                 fg=ANALYZER_CLS_COLORS[cls],
                                 font=("Segoe UI", 8, "bold"), padx=8, pady=2)
                    c.pack(side=tk.LEFT, padx=(0, 6))
                if not any_chip:
                    tk.Label(row, text="no graded moves", bg=bg, fg=dim,
                             font=("Segoe UI", 8)).pack(side=tk.LEFT)

            main = tk.Frame(self.results, bg=bg)
            main.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 10))
            left = tk.Frame(main, bg=bg)
            left.pack(side=tk.LEFT, fill=tk.Y)
            right = tk.Frame(main, bg=card)
            right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0))

            self.board_canvas = tk.Canvas(left, width=self.CELL * 8,
                                          height=self.CELL * 8, bg=bg,
                                          highlightthickness=0)
            self.board_canvas.pack(side=tk.LEFT)
            self.eval_canvas = tk.Canvas(left, width=24, height=self.CELL * 8,
                                         bg=bg, highlightthickness=0)
            self.eval_canvas.pack(side=tk.LEFT, fill=tk.Y, padx=(6, 0))

            self.best_lbl = tk.Label(left, text="", bg=bg, fg=muted,
                                     font=("Segoe UI", 9), anchor="w")
            self.best_lbl.pack(fill=tk.X, pady=(6, 0))
            self.pos_lbl = tk.Label(left, text="", bg=bg, fg=dim,
                                    font=("Segoe UI", 8), anchor="w")
            self.pos_lbl.pack(fill=tk.X)

            nav = tk.Frame(left, bg=bg)
            nav.pack(fill=tk.X, pady=(6, 0))
            for glyph, step in (("\u23ee", -100000), ("\u25c0", -1),
                                ("\u25b6", 1), ("\u23ed", 100000)):
                tk.Button(nav, text=glyph, command=lambda s=step: self._nav(s),
                          bg=self.CARD, fg=text, relief=tk.FLAT,
                          font=("Segoe UI", 11, "bold"), width=3,
                          cursor="hand2", activebackground=self.ACCENT_DIM,
                          activeforeground="#ffffff").pack(side=tk.LEFT, padx=(0, 4))

            # move list
            ml_wrap = tk.Frame(right, bg=card)
            ml_wrap.pack(fill=tk.BOTH, expand=True)
            ml_canvas = tk.Canvas(ml_wrap, bg=card, highlightthickness=0)
            ml_vsb = tk.Scrollbar(ml_wrap, orient="vertical",
                                  command=ml_canvas.yview, width=8, bd=0,
                                  troughcolor=self.BG,
                                  activebackground=self.ACCENT_DIM,
                                  relief=tk.FLAT)
            ml_canvas.configure(yscrollcommand=ml_vsb.set)
            ml_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            ml_vsb.pack(side=tk.RIGHT, fill=tk.Y)
            self.ml_frame = tk.Frame(ml_canvas, bg=card)
            self._ml_win = ml_canvas.create_window((0, 0), anchor="nw",
                                                   window=self.ml_frame)
            ml_canvas.bind("<Configure>", lambda _e: ml_canvas.itemconfigure(
                self._ml_win, width=ml_canvas.winfo_width()))
            ml_canvas.bind("<Enter>", lambda _e: ml_canvas.bind_all(
                "<MouseWheel>", self._on_ml_wheel))
            ml_canvas.bind("<Leave>", lambda _e: ml_canvas.unbind_all("<MouseWheel>"))
            self.ml_canvas = ml_canvas
            self._ml_rows = {}
            self._build_move_list()

            self._cur_idx = min(self._cur_idx, len(self.fens) - 1)
            self._show_position()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self._set_status("Render error: %s" % exc, self.RED)

    def _on_ml_wheel(self, event):
        try:
            self.ml_canvas.yview_scroll(int(-event.delta / 120), "units")
        except Exception:
            pass

    def _build_move_list(self):
        card = self.CARD
        hl = self.ACCENT_DIM
        for child in self.ml_frame.winfo_children():
            child.destroy()
        self._ml_rows = {}
        rows = {}
        for m in self.moves:
            rows.setdefault(m["fullmove"], {})[m["player"]] = m
        for fullmove in sorted(rows):
            row = tk.Frame(self.ml_frame, bg=card)
            row.pack(fill=tk.X)
            num = tk.Label(row, text="%d." % fullmove, bg=card, fg=self.DIM,
                           font=("Segoe UI", 8), width=4, anchor="e")
            num.pack(side=tk.LEFT, padx=(4, 2), pady=1)
            for player in ("w", "b"):
                mv = rows[fullmove].get(player)
                cell = tk.Frame(row, bg=card)
                cell.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=1)
                if mv is None:
                    continue
                san = tk.Label(cell, text=mv["san"], bg=card, fg=self.TEXT,
                               font=("Segoe UI", 9, "bold"), anchor="w",
                               padx=4, pady=2)
                san.pack(side=tk.LEFT)
                badge = tk.Label(cell, text=mv["cls"].title() if mv["cls"] != "book" else "book",
                                 bg=self._blend(ANALYZER_CLS_COLORS[mv["cls"]], 0.20, self.CARD),
                                 fg=ANALYZER_CLS_COLORS[mv["cls"]],
                                 font=("Segoe UI", 7, "bold"), padx=4, pady=1)
                badge.pack(side=tk.LEFT, padx=(4, 0))
                ev = tk.Label(cell, text=_fmt_eval(mv["played_cp"]), bg=card,
                              fg=self.MUTED, font=("Segoe UI", 8), anchor="e")
                ev.pack(side=tk.RIGHT, padx=(4, 4))
                for w in (cell, san, badge, ev):
                    w.bind("<Button-1>", lambda _e, p=mv["ply"]: self._goto(p))
                self._ml_rows[mv["ply"]] = (row, cell)

    def _goto(self, ply):
        self._cur_idx = min(max(0, ply), len(self.fens) - 1)
        self._show_position()
        self._scroll_ml_to(ply)

    def _scroll_ml_to(self, ply):
        try:
            row = self._ml_rows.get(ply)
            if row:
                y = row[0].winfo_y()
                self.ml_canvas.yview_moveto(max(0.0, y / max(1, self.ml_frame.winfo_reqheight())))
        except Exception:
            pass

    def _nav(self, step):
        if not self.fens or self._busy:
            return
        if step < -1:
            self._cur_idx = 0
        elif step > 1000:
            self._cur_idx = len(self.fens) - 1
        else:
            self._cur_idx = max(0, min(len(self.fens) - 1, self._cur_idx + step))
        self._show_position()

    def _show_position(self):
        try:
            idx = self._cur_idx
            fen = self.fens[idx]
            import chess as _c
            board = _c.Board(fen)
            last_uci = self.moves[idx - 1]["uci"] if 0 < idx <= len(self.moves) else None
            self._draw_board(board, last_uci)
            self._draw_eval(idx)
            mv = self.moves[idx - 1] if idx > 0 else None
            side = fen.split()[1]
            if idx == 0:
                self.pos_lbl.config(text="Start position - %s to move" % ("White" if board.turn else "Black"))
            else:
                self.pos_lbl.config(text="Move %s - %s to move"
                                       % (self._move_label(idx), "White" if side == "w" else "Black"))
            ev = self.evals[idx] if idx < len(self.evals) else None
            if ev and ev.get("best_move"):
                best_san = ""
                try:
                    best_san = _c.Board(fen).san(_c.Move.from_uci(ev["best_move"]))
                except Exception:
                    best_san = ev["best_move"]
                self.best_lbl.config(text="Best: %s  (%s)"
                                     % (best_san, _fmt_eval(ev.get("cp"))))
            elif ev and ev.get("cp") is not None:
                self.best_lbl.config(text="Evaluation: %s" % _fmt_eval(ev.get("cp")))
            else:
                self.best_lbl.config(text="")
            for ply, (row, cell) in self._ml_rows.items():
                row.config(bg=self.ACCENT_DIM if ply == idx - 1 else self.CARD)
                for ch in cell.winfo_children():
                    ch.config(bg=self.ACCENT_DIM if ply == idx - 1 else self.CARD)
        except Exception:
            pass

    def _move_label(self, idx):
        mv = self.moves[idx - 1]
        num = mv["fullmove"]
        return "%d... %s" % (num, mv["san"]) if mv["player"] == "b" else "%d. %s" % (num, mv["san"])

    def _draw_board(self, board, last_uci=None):
        import chess as _c
        c = self.board_canvas
        c.delete("all")
        cell = self.CELL
        hi = []
        if last_uci:
            try:
                hi = [_c.parse_square(last_uci[0:2]), _c.parse_square(last_uci[2:4])]
            except Exception:
                hi = []
        for r in range(8):
            for f in range(8):
                x0, y0 = f * cell, r * cell
                x1, y1 = x0 + cell, y0 + cell
                light = (r + f) % 2 == 0
                fill = self.LIGHT_SQ if light else self.DARK_SQ
                c.create_rectangle(x0, y0, x1, y1, fill=fill, outline="")
        if hi:
            for sq in hi:
                r, f = 7 - sq // 8, sq % 8
                c.create_rectangle(f * cell, r * cell, f * cell + cell,
                                   r * cell + cell, outline=self.HILITE,
                                   width=3)
        for sq in range(64):
            piece = board.piece_at(sq)
            if not piece:
                continue
            r, f = 7 - sq // 8, sq % 8
            cx = f * cell + cell / 2
            cy = r * cell + cell / 2
            glyph = _ANALYZER_GLYPHS.get(piece.symbol())
            if not glyph:
                continue
            fs = max(10, int(cell * 0.72))
            fill = "#ffffff" if piece.color else "#1d1a26"
            outline = "#2a2430" if piece.color else "#8f86b8"
            for ox, oy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                c.create_text(cx + ox, cy + oy, text=glyph,
                              font=("Segoe UI Symbol", fs, "bold"),
                              fill=outline)
            c.create_text(cx, cy, text=glyph,
                          font=("Segoe UI Symbol", fs, "bold"), fill=fill)
        c.create_rectangle(0, 0, cell * 8, cell * 8, outline=self.LINE, width=2)

    def _draw_eval(self, idx):
        c = self.eval_canvas
        c.delete("all")
        w = c.winfo_width() or 24
        h = c.winfo_height() or (self.CELL * 8)
        ev = self.evals[idx] if idx < len(self.evals) else None
        c.create_rectangle(0, 0, w, h, fill="#1d1a26", outline="")
        if not ev or ev.get("cp") is None:
            c.create_text(w / 2, h / 2, text="\u2013", fill=self.MUTED,
                          font=("Segoe UI", 10, "bold"))
            return
        cp = ev["cp"]
        white_cp = cp if ev["side"] == "w" else -cp
        share = max(0.05, min(0.95, _win_prob(white_cp)))
        white_h = share * h
        c.create_rectangle(1, h - white_h, w - 1, h, fill="#f5f5f5", outline="")
        c.create_rectangle(1, 0, w - 1, h - white_h, fill="#1d1a26", outline="")
        c.create_text(w / 2, h - white_h, text="\u25b6" if white_h > 16 else "",
                      fill="#111111" if share > 0.5 else "#f5f5f5",
                      font=("Segoe UI", 8), angle=90)
        c.create_text(w / 2, 8, text=_fmt_eval(white_cp), fill="#ece8ff",
                      font=("Segoe UI", 8, "bold"))

    @staticmethod
    def _blend(hexcol, alpha, onto):
        onto = onto or "#13101c"

        def _rgb(hx):
            hx = hx.lstrip("#")
            return int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)

        r1, g1, b1 = _rgb(onto)
        r2, g2, b2 = _rgb(hexcol)
        a = max(0.0, min(1.0, alpha))
        return "#%02x%02x%02x" % (
            int(r1 + (r2 - r1) * a),
            int(g1 + (g2 - g1) * a),
            int(b1 + (b2 - b1) * a))

    # ---------------- lifecycle ----------------

    def _poll(self):
        try:
            if not self.root.winfo_exists():
                return
            for _ in range(50):
                try:
                    callback = self._callbacks.get_nowait()
                except queue.Empty:
                    break
                try:
                    callback()
                except Exception as exc:
                    self._set_status(f"Analysis update error: {exc}", self.RED)
            self._draw_prog()
            self.root.after(50, self._poll)
        except Exception:
            pass

    def _on_close(self):
        self._cancel = True
        self._busy = False
        try:
            self.root.destroy()
        except Exception:
            pass

    def show(self):
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass


