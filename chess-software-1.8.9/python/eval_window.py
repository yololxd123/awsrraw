import tkinter as tk
from tkinter import ttk
import math
import threading

class ExternalEvalWindow:
    """BetterMint-style floating evaluation panel (port of OverlayWindow.js).

    A draggable, rounded, always-on-top panel showing game stage, active
    engines, a large advantage-coloured eval bar, depth progress and ranked
    top lines with per-engine attribution and eval-change chips.
    """

    BG      = "#13101c"
    PANEL   = "#181427"
    CARD    = "#1d1830"
    LINE    = "#2b2444"
    TEXT    = "#ece8ff"
    MUTED   = "#9a92c9"
    DIM     = "#6f6796"
    ACCENT  = "#7a5cff"
    ACCENT_DIM = "#4a3a8f"
    GREEN   = "#4ade80"
    RED     = "#f87171"
    YELLOW  = "#f5c542"
    BLUE    = "#58a6ff"
    PURPLE  = "#bc8cff"
    RANK_COLORS = {1: "#4ade80", 2: "#58a6ff", 3: "#f5c542", 4: "#bc8cff"}

    # Wide enough for move, source, evaluation and delta without clipping.
    WIDTH    = 560
    HEADER_H = 48
    ALPHA    = 0.93
    MIN_W    = 460
    MIN_H    = 320

    def __init__(self, overlay):
        self.overlay = overlay
        self._drag_origin = None
        self._resize_origin = None
        self._cur_w = self.WIDTH
        self._cur_h = 440
        self._collapsed = False
        # The window is opened at its computed size, but the body's real
        # requested height is only known once the top lines have been drawn.
        # After the first content fill we grow to fit so nothing is cut off;
        # the user can still shrink it afterwards via the grip.
        self._fitted = False
        self._fade_after = None
        self._bar_after = None
        self._depth_after = None
        self._bar_target = 0.0
        self._bar_req = None
        self._depth_target = 0.0
        self._depth_req = None
        self._lines_sig = None
        self._lines_rows = []
        self.root = tk.Toplevel(overlay.root)
        # Use the operating system title bar so the panel can be resized from
        # every edge/corner.  A borderless Toplevel cannot be resized by the
        # normal window manager, which left the old tiny bottom grip as the
        # only (and easily missed) option.
        self.root.overrideredirect(False)
        self.root.resizable(True, True)
        self.root.title("Engine Evaluation")
        self.root.minsize(self.MIN_W, self.MIN_H)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", self.ALPHA)
        self.root.configure(bg=self.BG)
        # Drop the OS caption (the white/blue "Vista" strip) so only the custom
        # purple header shows on top.  The resizable frame is kept, so edge and
        # corner resizing continue to work exactly as with the native title bar.
        self._strip_native_caption()
        self._build()
        self.root.update_idletasks()
        self._H = max(self.root.winfo_reqheight(), self.MIN_H)
        self._cur_h = self._H
        self.root.geometry(f"{self.WIDTH}x{max(self._H, 480)}")
        # Native title bars own their shape and resizing border; applying a
        # custom Windows region here can interfere with either of those.
        self.root.withdraw()
        self._place_near_overlay()
        # restore translucency whenever a drag/resize gesture ends
        self.root.bind(
            "<ButtonRelease-1>",
            lambda _e: self.root.attributes("-alpha", self.ALPHA))
        # Keep custom rounding and the saved resize baseline in sync with
        # native edge/corner resizing as well as the visible grip.
        self.root.bind("<Configure>", self._on_native_resize, add="+")

    # ---------------- geometry helpers ----------------

    def _strip_native_caption(self):
        """Remove the OS title bar from this panel.

        The window stays a normal (non-overrideredirect) top-level, so the OS
        still provides the invisible resize frame and the drop shadow, but the
        WS_CAPTION / WS_SYSMENU bits are cleared to hide the white/blue strip
        that used to sit above the custom purple header.  Silent fallback if
        the style cannot be changed."""
        try:
            import ctypes as _ct
            hwnd = _ct.windll.user32.GetParent(self.root.winfo_id())
            if hwnd == 0:
                hwnd = self.root.winfo_id()
            GWL_STYLE = -16
            WS_CAPTION = 0x00C00000   # WS_BORDER | WS_DLGFRAME
            WS_SYSMENU = 0x00080000
            style = _ct.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
            _ct.windll.user32.SetWindowLongW(
                hwnd, GWL_STYLE, style & ~(WS_CAPTION | WS_SYSMENU))
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_NOZORDER = 0x0004
            SWP_FRAMECHANGED = 0x0020
            _ct.windll.user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
        except Exception:
            pass

    def _on_native_resize(self, _event=None):
        """Track dimensions when the OS border/corner resize is used."""
        try:
            w, h = self.root.winfo_width(), self.root.winfo_height()
            if w >= self.MIN_W and h >= self.MIN_H:
                self._cur_w, self._cur_h = w, h
        except Exception:
            pass

    def _make_rounded(self):
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            if hwnd == 0:
                hwnd = self.root.winfo_id()
            w = self.root.winfo_width() or self._cur_w
            h = self.root.winfo_height() or self._cur_h
            region = ctypes.windll.gdi32.CreateRoundRectRgn(
                0, 0, w, h, 16, 16)
            ctypes.windll.user32.SetWindowRgn(hwnd, region, True)
        except Exception:
            pass

    @staticmethod
    def _rr(canvas, x0, y0, x1, y1, r, **kw):
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r,
               x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1,
               x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
        return canvas.create_polygon(pts, smooth=True, **kw)

    @classmethod
    def _blend(cls, hexcol, alpha, onto=None):
        onto = onto or cls.BG

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

    # ---------------- ui build ----------------

    def _round_btn(self, parent, glyph, fs, size=22):
        c = tk.Canvas(parent, width=size, height=size, bg=self.PANEL,
                      highlightthickness=0, cursor="hand2")
        c.create_oval(1, 1, size - 1, size - 1, fill=self.CARD,
                      outline=self.LINE, width=1, tags="ov")
        c.create_text(size / 2, size / 2 + 1, text=glyph, fill=self.MUTED,
                      font=("Segoe UI", fs, "bold"), tags="gl")

        def _enter(_e):
            c.itemconfigure("ov", fill=self.ACCENT_DIM, outline=self.ACCENT)
            c.itemconfigure("gl", fill="#ffffff")

        def _leave(_e):
            c.itemconfigure("ov", fill=self.CARD, outline=self.LINE)
            c.itemconfigure("gl", fill=self.MUTED)

        c.bind("<Enter>", _enter)
        c.bind("<Leave>", _leave)
        return c

    def _build(self):
        bg, panel, card = self.BG, self.PANEL, self.CARD
        text, muted, dim, accent = self.TEXT, self.MUTED, self.DIM, self.ACCENT

        # ---- header ----
        self.head = tk.Frame(self.root, bg=panel)
        self.head.pack(fill=tk.X)

        logo = tk.Canvas(self.head, width=26, height=26, bg=panel,
                         highlightthickness=0, cursor="fleur")
        logo.pack(side=tk.LEFT, padx=(12, 8), pady=11)
        logo.create_oval(1, 1, 25, 25, fill=accent, outline="")
        logo.create_text(13, 13, text="\u265e", fill="#ffffff",
                         font=("Segoe UI", 13, "bold"))

        self.title_lbl = tk.Label(self.head, text="ENGINE EVALUATION",
                                  bg=panel, fg="#d9d4f0",
                                  font=("Segoe UI", 10, "bold"))
        self.title_lbl.pack(side=tk.LEFT)

        self.stage_lbl = tk.Label(self.head, text="MIDGAME", bg=card,
                                  fg=self.PURPLE, font=("Segoe UI", 8, "bold"),
                                  padx=10, pady=3)
        self.stage_lbl.pack(side=tk.RIGHT, padx=(0, 6), pady=12)

        self.btn_close = self._round_btn(self.head, "\u2715", 9)
        self.btn_close.pack(side=tk.RIGHT, padx=(0, 12), pady=12)
        self.btn_close.bind("<Button-1>", lambda _e: self.hide())

        self.btn_min = self._round_btn(self.head, "\u2013", 11)
        self.btn_min.pack(side=tk.RIGHT, padx=(0, 4), pady=12)
        self.btn_min.bind("<Button-1>", lambda _e: self._toggle_collapse())

        self._bind_drag(self.head, skip={self.btn_min, self.btn_close})
        self._bind_drag(logo)

        tk.Frame(self.root, bg=self.LINE, height=1).pack(fill=tk.X)

        # Bottom-right resize grip is packed BEFORE the body so it always
        # keeps its own 16px strip no matter how small the window gets
        # (previously it was packed last with expand=True and got squeezed
        # to zero height when the panel was shrunk, leaving no handle).
        self.grip = tk.Canvas(self.root, width=16, height=16, bg=self.BG,
                              highlightthickness=0, cursor="bottom_right_corner")
        self.grip.pack(side=tk.BOTTOM, anchor=tk.SE)
        for x, y in ((2, 12), (6, 8), (10, 4)):
            self.grip.create_oval(x, y, x + 4, y + 4, fill=self.DIM,
                                  outline="", tags="dots")
        self.grip.bind("<ButtonPress-1>", self._start_resize)
        self.grip.bind("<B1-Motion>", self._on_resize)

        # ---- body (scrollable when the window is shrunk) ----
        self.body = tk.Frame(self.root, bg=bg)
        self.body.pack(fill=tk.BOTH, expand=True)
        self.body_canvas = tk.Canvas(self.body, bg=bg, highlightthickness=0,
                                     bd=0)
        self.body_vsb = tk.Scrollbar(self.body, orient="vertical",
                                     command=self.body_canvas.yview,
                                     width=8, bd=0,
                                     troughcolor=self.BG,
                                     activebackground=self.ACCENT_DIM,
                                     relief=tk.FLAT)
        self.body_canvas.configure(yscrollcommand=self.body_vsb.set)
        self.body_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.body_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.body_inner = tk.Frame(self.body_canvas, bg=bg)
        self._body_win = self.body_canvas.create_window(
            (0, 0), anchor="nw", window=self.body_inner)
        self.body_inner.bind(
            "<Configure>",
            lambda _e: self.body_canvas.configure(
                scrollregion=self.body_canvas.bbox("all")))
        def _resize_body(_e=None):
            width = max(1, self.body_canvas.winfo_width())
            self.body_canvas.itemconfigure(self._body_win, width=width)
            # Configure can fire while _build is still creating widgets.
            # Do not access the label until it exists.
            if hasattr(self, "eng_lbl"):
                self.eng_lbl.configure(wraplength=max(160, width - 24))
        self.body_canvas.bind("<Configure>", _resize_body)
        # Mousewheel scrolls the body whenever the cursor is over it
        # (bind_all on enter/leave so children like labels don't swallow it).
        self.body_canvas.bind(
            "<Enter>", lambda _e: self.body_canvas.bind_all(
                "<MouseWheel>", self._on_body_wheel))
        self.body_canvas.bind(
            "<Leave>", lambda _e: self.body_canvas.unbind_all("<MouseWheel>"))
        body = self.body_inner

        # engines
        self.eng_lbl = tk.Label(body, text="no engines active", bg=bg,
                                fg=dim, font=("Segoe UI", 9), anchor="w",
                                justify=tk.LEFT, wraplength=self.WIDTH - 30)
        self.eng_lbl.pack(fill=tk.X, padx=12, pady=(10, 0))

        # evaluation section
        ev_head = tk.Frame(body, bg=bg)
        ev_head.pack(fill=tk.X, padx=12, pady=(10, 2))
        tk.Frame(ev_head, bg=accent, width=3, height=11).pack(
            side=tk.LEFT, padx=(0, 6))
        tk.Label(ev_head, text="EVALUATION", bg=bg, fg=dim,
                 font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT)
        self.depth_lbl = tk.Label(ev_head, text="d0", bg=bg, fg=muted,
                                  font=("Segoe UI", 9, "bold"))
        self.depth_lbl.pack(side=tk.RIGHT)

        self.eval_lbl = tk.Label(body, text="0.00", bg=bg, fg=muted,
                                 font=("Segoe UI", 26, "bold"), anchor="w")
        self.eval_lbl.pack(fill=tk.X, padx=12, pady=(0, 2))

        self.bar = tk.Canvas(body, height=22, bg=bg,
                             highlightthickness=0)
        self.bar.pack(fill=tk.X, padx=12)
        self.bar.bind("<Configure>", lambda _e: self._draw_bar())

        dep_row = tk.Frame(body, bg=bg)
        dep_row.pack(fill=tk.X, padx=12, pady=(7, 0))
        tk.Label(dep_row, text="DEPTH", bg=bg, fg=dim,
                 font=("Segoe UI", 7, "bold")).pack(side=tk.LEFT, padx=(0, 8))
        self.dep_bar = tk.Canvas(dep_row, height=6, bg=self.CARD,
                                 highlightthickness=0)
        self.dep_bar.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.dep_bar.bind("<Configure>", lambda _e: self._draw_dep())

        # live per-side game accuracy
        acc_row = tk.Frame(body, bg=bg)
        acc_row.pack(fill=tk.X, padx=12, pady=(6, 0))
        tk.Label(acc_row, text="ACCURACY", bg=bg, fg=dim,
                 font=("Segoe UI", 7, "bold")).pack(side=tk.LEFT, padx=(0, 8))
        self.acc_lbl = tk.Label(acc_row, text="\u2014", bg=bg, fg=muted,
                                font=("Segoe UI", 9, "bold"))
        self.acc_lbl.pack(side=tk.RIGHT)

        # top lines
        mv_head = tk.Frame(body, bg=bg)
        mv_head.pack(fill=tk.X, padx=12, pady=(12, 4))
        tk.Frame(mv_head, bg=accent, width=3, height=11).pack(
            side=tk.LEFT, padx=(0, 6))
        tk.Label(mv_head, text="TOP LINES", bg=bg, fg=dim,
                 font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT)

        self.lines_canvas = tk.Canvas(body, bg=bg, highlightthickness=0,
                                      height=176)
        self.lines_canvas.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 10))
        self.lines_frame = tk.Frame(self.lines_canvas, bg=bg)
        self._lines_win = self.lines_canvas.create_window(
            0, 0, anchor="nw", window=self.lines_frame)
        self.lines_canvas.bind(
            "<Configure>",
            lambda _e: self.lines_canvas.itemconfigure(
                self._lines_win, width=self.lines_canvas.winfo_width()))

    def _on_body_wheel(self, event):
        """Scroll the panel body (used when the window is smaller than its
        content — without this the top lines could never be reached)."""
        try:
            self.body_canvas.yview_scroll(int(-event.delta / 120), "units")
        except Exception:
            pass

    def _bind_drag(self, widget, skip=()):
        widget.bind("<Button-1>", self._start_drag)
        widget.bind("<B1-Motion>", self._on_drag)
        for child in widget.winfo_children():
            if child in skip:
                continue
            child.bind("<Button-1>", self._start_drag)
            child.bind("<B1-Motion>", self._on_drag)

    def _start_drag(self, event):
        self._drag_origin = (event.x_root - self.root.winfo_rootx(),
                             event.y_root - self.root.winfo_rooty())
        try:
            self.root.attributes("-alpha", 0.82)
        except Exception:
            pass

    def _on_drag(self, event):
        if self._drag_origin:
            x0, y0 = self._drag_origin
            self.root.geometry(f"+{event.x_root - x0}+{event.y_root - y0}")

    def _start_resize(self, event):
        self._resize_origin = (event.x_root - self.root.winfo_rootx(),
                               event.y_root - self.root.winfo_rooty())
        try:
            self.root.attributes("-alpha", 0.82)
        except Exception:
            pass

    def _on_resize(self, event):
        if not self._resize_origin:
            return
        dx = event.x_root - self.root.winfo_rootx()
        dy = event.y_root - self.root.winfo_rooty()
        w = max(self.MIN_W, self._cur_w + dx - self._resize_origin[0])
        h = max(self.MIN_H, self._cur_h + dy - self._resize_origin[1])
        self._cur_w, self._cur_h = w, h
        self.root.geometry(f"{int(w)}x{int(h)}")
        self._make_rounded()

    def _place_near_overlay(self):
        try:
            ov = self.overlay.root
            x = ov.winfo_rootx() + ov.winfo_width() + 8
            y = max(0, ov.winfo_rooty() - 40)
            self.root.geometry(f"+{x}+{y}")
        except Exception:
            pass

    # ---------------- show / hide / collapse ----------------

    def show(self):
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.update_idletasks()
            # Sync the stored size with whatever geometry was applied (e.g. a
            # restored size from the last session) so the first drag-resize
            # gesture starts from the real size instead of jumping.
            self._cur_w = self.root.winfo_width() or self._cur_w
            self._cur_h = self.root.winfo_height() or self._cur_h
        except Exception:
            pass
        self.refresh()
        self._fit_content()
        self._fade_in()

    def _fit_content(self):
        """Grow the window once so the whole body is visible.  Content is
        populated only after the first refresh(), so the initial height would
        otherwise clip the bottom (top lines) behind the scroll area."""
        if self._fitted or self._collapsed:
            return
        self._fitted = True
        try:
            self.root.update_idletasks()
            # The body content lives inside the scroll canvas, so the root's
            # requested height stays small.  Needed height = chrome above the
            # body (header + separator) + content + the 16px grip strip.
            need = (self.HEADER_H + 1 + 16
                    + self.body_inner.winfo_reqheight())
            cur = self.root.winfo_height() or need
            if need > cur:
                max_h = max(self.MIN_H, self.root.winfo_screenheight() - 40)
                h = min(need, max_h)
                if h > cur:
                    self._cur_h = int(h)
                    self.root.geometry(f"{self._cur_w}x{int(h)}")
                    self._make_rounded()
        except Exception:
            pass

    def hide(self):
        if self._fade_after:
            try:
                self.root.after_cancel(self._fade_after)
            except Exception:
                pass
            self._fade_after = None
        try:
            self.root.attributes("-alpha", self.ALPHA)
        except Exception:
            pass
        self.root.withdraw()

    def _toggle_collapse(self):
        if self._collapsed:
            self.body.pack(fill=tk.BOTH, expand=True)
            self.btn_min.itemconfigure("gl", text="+")
            self._collapsed = False
            h = max(self._cur_h, self.root.winfo_reqheight())
        else:
            self.body.pack_forget()
            self.btn_min.itemconfigure("gl", text="+")
            self._collapsed = True
            h = self.HEADER_H
        self.root.update_idletasks()
        self.root.geometry(f"{int(self._cur_w)}x{int(h)}")
        self._make_rounded()

    def _fade_in(self):
        if self._fade_after:
            try:
                self.root.after_cancel(self._fade_after)
            except Exception:
                pass
        try:
            tx, ty = self.root.winfo_x(), self.root.winfo_y()
        except Exception:
            tx, ty = 0, 0
        fx, fy = tx, ty + 12
        steps, ms = 12, 14

        def _step(i):
            if i > steps:
                self._fade_after = None
                return
            f = i / steps
            e = 1 - (1 - f) ** 3
            try:
                self.root.attributes("-alpha", f * self.ALPHA)
                self.root.geometry(
                    f"+{int(fx + (tx - fx) * e)}+{int(fy + (ty - fy) * e)}")
            except Exception:
                pass
            if i < steps:
                self._fade_after = self.root.after(
                    ms, lambda: _step(i + 1))
            else:
                self._fade_after = None

        self.root.attributes("-alpha", 0.0)
        self._fade_after = self.root.after(0, lambda: _step(0))

    def destroy(self):
        for attr in ("_fade_after", "_bar_after", "_depth_after"):
            a = getattr(self, attr, None)
            if a:
                try:
                    self.root.after_cancel(a)
                except Exception:
                    pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def is_visible(self):
        try:
            return (self.root.winfo_exists()
                    and self.root.state() != "withdrawn")
        except Exception:
            return False

    # ---------------- data helpers ----------------

    @staticmethod
    def _parse_score(s):
        if s is None:
            return None
        s = str(s).strip()
        if not s:
            return None
        up = s.upper().replace("+", "")
        if "M" in up:
            sign = -1 if (s.startswith("E") or up.startswith("-")) else 1
            try:
                n = float(up.split("M")[1])
            except Exception:
                n = 1.0
            return sign * (1000.0 + n)
        try:
            return float(s)
        except Exception:
            return None

    def _eval_color(self, v):
        if v is None:
            return self.MUTED
        if v >= 0.5:
            return self.GREEN
        if v <= -0.5:
            return self.RED
        return self.TEXT

    def _acc_color(self, acc):
        if acc is None:
            return self.MUTED
        if acc >= 85:
            return self.GREEN
        if acc >= 65:
            return self.YELLOW
        return self.RED

    # ---------------- drawing ----------------

    def _draw_bar(self):
        c = self.bar
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 10 or h < 8:
            return
        self._rr(c, 0, 0, w, h, h / 2, fill=self.CARD,
                 outline=self.LINE, width=1)
        t = self._bar_target * 2 - 1
        cx = w / 2
        hw = max(0.0, (w - h) * 0.5 * abs(t))
        if t > 0.001 and hw > 1:
            self._rr(c, cx, 1, cx + hw, h - 1, h / 2,
                     fill=self.GREEN, outline="")
        elif t < -0.001 and hw > 1:
            self._rr(c, cx - hw, 1, cx, h - 1, h / 2,
                     fill=self.RED, outline="")
        c.create_line(cx, 1, cx, h - 1, fill=self.LINE, width=2)

    def _animate_bar(self, target):
        if abs(target - self._bar_target) < 0.01:
            self._bar_target = target
            self._draw_bar()
            return
        start = self._bar_target
        steps, ms = 10, 12

        def _step(i):
            f = (i / steps) ** 2
            self._bar_target = start + (target - start) * f
            self._draw_bar()
            if i < steps:
                self._bar_after = self.root.after(
                    ms, lambda: _step(i + 1))
            else:
                self._bar_after = None

        if self._bar_after:
            try:
                self.root.after_cancel(self._bar_after)
            except Exception:
                pass
        self._bar_after = self.root.after(0, lambda: _step(1))

    def _draw_dep(self):
        c = self.dep_bar
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 10 or h < 4:
            return
        self._rr(c, 0, 0, w, h, h / 2, fill=self.CARD,
                 outline=self.LINE, width=1)
        fw = max(0.0, min(1.0, self._depth_target)) * (w - 4)
        if fw > 1:
            self._rr(c, 2, 1, 2 + fw, h - 1, h / 2,
                     fill=self.ACCENT, outline="")

    def _animate_depth(self, target):
        if abs(target - self._depth_target) < 0.01:
            self._depth_target = target
            self._draw_dep()
            return
        start = self._depth_target
        steps, ms = 8, 12

        def _step(i):
            f = (i / steps) ** 2
            self._depth_target = start + (target - start) * f
            self._draw_dep()
            if i < steps:
                self._depth_after = self.root.after(
                    ms, lambda: _step(i + 1))
            else:
                self._depth_after = None

        if self._depth_after:
            try:
                self.root.after_cancel(self._depth_after)
            except Exception:
                pass
        self._depth_after = self.root.after(0, lambda: _step(1))

    # ---------------- refresh ----------------

    def refresh(self):
        try:
            ov = self.overlay
            fen = getattr(ov, "current_fen", None)

            # stage pill (dynamic game phase: Opening / Middlegame / Endgame)
            try:
                phase = ov._game_phase()
                if phase == "opening":
                    label, color = "OPENING", self.BLUE
                elif phase == "endgame":
                    label, color = "ENDGAME", self.GREEN
                else:
                    label, color = "MIDGAME", self.PURPLE
            except Exception:
                label, color = "MIDGAME", self.PURPLE
            self.stage_lbl.config(
                text=label, fg=color,
                bg=self._blend(color, 0.16, self.CARD))

            # engines
            engs = getattr(ov, "engines", None) or []
            active = [e.get("name") for e in engs
                      if e.get("process") and e["process"].poll() is None]
            active = [n for n in active if n]
            if active:
                shown = active[:4]
                txt = "  ".join("\u25cf " + n for n in shown)
                if len(active) > 4:
                    txt += "   +" + str(len(active) - 4)
                self.eng_lbl.config(text=txt, fg=self.MUTED)
            elif engs:
                self.eng_lbl.config(text="starting engines\u2026",
                                    fg=self.DIM)
            else:
                self.eng_lbl.config(text="no engines active", fg=self.DIM)

            # Use exactly the same ordered recommendations as the board arrows.
            # This includes display-mode ordering, book lines, and any
            # target-accuracy promotion; raw MultiPV rank is not the displayed rank.
            suggestions = ov._compute_visible_suggestions()
            score_str = "0.00"
            sval = None
            if suggestions and len(suggestions[0]) > 2:
                score_str = suggestions[0][2]
                sval = self._parse_score(score_str)
            self.eval_lbl.config(text=score_str, fg=self._eval_color(sval))

            # depth
            depth = getattr(ov, "current_depth", 0) or 0
            target = getattr(ov, "analysis_depth", 18) or 18
            self.depth_lbl.config(text="d%d" % depth)
            self._set_depth_anim(depth / max(target, 1))

            # accuracy readout (live per-side game accuracy)
            try:
                hum = ov._human_color()
                white = getattr(ov, "white_stats", {}) or {}
                black = getattr(ov, "black_stats", {}) or {}
                you = white if hum == "w" else black
                opp = black if hum == "w" else white
                you_total = you.get("total", 0) or 0
                opp_total = opp.get("total", 0) or 0
                if not you_total and not opp_total:
                    self.acc_lbl.config(text="no moves yet", fg=self.DIM)
                else:
                    parts = []
                    if you_total:
                        parts.append("You %.1f%%" % ov._side_accuracy(you))
                    if opp_total:
                        parts.append("Opp %.1f%%" % ov._side_accuracy(opp))
                    ref = you if you_total else opp
                    self.acc_lbl.config(
                        text="  |  ".join(parts),
                        fg=self._acc_color(ov._side_accuracy(ref)))
            except Exception:
                pass

            # eval bar (human POV: + is player winning, - is player losing)
            hum = ov._human_color()
            raw_score = getattr(ov, "current_score", 0.0) or 0.0
            human_score = raw_score if hum == "w" else -raw_score
            ratio = 0.5 + math.atan(human_score / 3.0) / math.pi
            self._set_bar_anim(ratio)

            # top lines
            pool_moves = {}
            try:
                for m in ov.pool.get_ranked_moves():
                    pool_moves[m.get("move")] = m
            except Exception:
                pass
            self._render_lines(suggestions, pool_moves, fen)
        except Exception:
            pass

    def _set_bar_anim(self, target):
        if self._bar_req is not None and abs(target - self._bar_req) < 0.001:
            return
        self._bar_req = target
        self._animate_bar(target)

    def _set_depth_anim(self, target):
        if self._depth_req is not None and abs(target - self._depth_req) < 0.01:
            return
        self._depth_req = target
        self._animate_depth(target)

    def _render_lines(self, suggestions, pool_moves, fen):
        """Render recommendations in their displayed order, never raw engine rank."""
        bg = self.BG

        book_uci = set()
        try:
            for uci, _w in self.overlay.book_manager.lookup(fen):
                book_uci.add(uci)
        except Exception:
            pass
        tb_uci = None
        try:
            tb = self.overlay.book_manager.tb_probe(fen)
            if tb:
                tb_uci = tb.get("best_move")
        except Exception:
            pass

        payloads = []
        for shown_rank, entry in enumerate(suggestions[:6], 1):
            if not entry or len(entry) < 3:
                continue
            original_rank, moves, sc_str = entry
            if not moves or not moves[0]:
                continue
            uci = moves[0]
            pm = pool_moves.get(uci) or {}
            if uci == tb_uci:
                att = "tablebase"
            elif uci in book_uci:
                att = "book"
            elif pm.get("engine"):
                att = pm["engine"]
            else:
                att = "engine"
            # shown_rank controls the visual recommendation order; retain the
            # original rank only as provenance, not as an ordering signal.
            payloads.append((shown_rank, original_rank, uci, sc_str, att))

        sig = (fen, tuple(payloads))
        if sig == self._lines_sig:
            return
        self._lines_sig = sig

        for row in self._lines_rows:
            try:
                row.destroy()
            except Exception:
                pass
        self._lines_rows = []

        if not payloads:
            msg = tk.Label(self.lines_frame,
                           text="waiting for engine lines\u2026",
                           bg=bg, fg=self.DIM, font=("Segoe UI", 8),
                           anchor="w")
            msg.pack(fill=tk.X, pady=6)
            self._lines_rows.append(msg)
            return

        top_score = None
        for i, (shown_rank, original_rank, uci, sc_str, att) in enumerate(payloads):
            score = self._parse_score(sc_str)
            if i == 0:
                top_score = score

            row = tk.Frame(self.lines_frame, bg=bg)
            row.pack(fill=tk.X, pady=1)

            rc = self.RANK_COLORS.get(shown_rank, self.DIM)
            chip = tk.Canvas(row, width=20, height=20, bg=bg,
                             highlightthickness=0)
            chip.pack(side=tk.LEFT, padx=(0, 6))
            chip.create_oval(1, 1, 19, 19, fill=self._blend(rc, 0.20),
                             outline="")
            chip.create_text(10, 10, text=str(shown_rank), fill=rc,
                             font=("Segoe UI", 9, "bold"))

            tk.Label(row, text=uci, bg=bg, fg=self.TEXT,
                     font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)

            tk.Label(row, text=att, bg=bg, fg=self.DIM,
                     font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(8, 0))

            # eval-change chip
            chip_col, chip_txt = self.DIM, "\u2014"
            if top_score is not None and score is not None:
                delta = score - top_score
                if abs(delta) < 0.02:
                    chip_col, chip_txt = self.DIM, "=0.0"
                else:
                    chip_col = self.GREEN if delta > 0 else self.RED
                    chip_txt = "%+.1f" % delta
            tk.Label(row, text=chip_txt, bg=self._blend(chip_col, 0.16),
                     fg=chip_col, font=("Segoe UI", 8, "bold"),
                     padx=5, pady=1).pack(side=tk.RIGHT)

            tk.Label(row, text=sc_str, bg=bg, fg=self._eval_color(score),
                     font=("Segoe UI", 9, "bold")).pack(side=tk.RIGHT,
                                                        padx=(0, 8))

            self._lines_rows.append(row)


# ============================================================================
# CHESS OVERLAY WITH CANVAS-DRAWN WHITE BORDER (PERFECT SOLUTION)
# ============================================================================

def _valid_fen_placement(placement):
    """Validate a FEN board-placement string (8 ranks, 8 squares each)."""
    try:
        ranks = placement.split("/")
        if len(ranks) != 8:
            return False
        for rank in ranks:
            if not rank:
                return False
            total = 0
            for ch in rank:
                if ch in "12345678":
                    total += int(ch)
                elif ch in "PRNBQKprnbqk":
                    total += 1
                else:
                    return False
            if total != 8:
                return False
    except Exception:
        return False
    return True


# ============================================================================
# PGN GAME ANALYZER  (chess.com-style: accuracy, brilliants, misses, ...)
# ============================================================================

# Shared helpers retain the public names formerly defined in this module.
from analysis_helpers import (  # re-export for compatibility
    ANALYZER_CLS_ORDER, ANALYZER_CLS_COLORS, ANALYZER_MATE,
    ANALYZER_PIECE_VALUES, _ANALYZER_GLYPHS, _win_prob, _score_to_cp,
    _move_accuracy, _fmt_eval, _material_for, _classify_move,
)
