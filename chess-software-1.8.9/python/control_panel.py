import os
import sys
import json
import time
import math
import random
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from engine_pool import EnginePool
from widgets import InlineSelect, DragReorderList, CircleSlider, DualThumbRangeSlider, HoverTip
from modern_widgets import RoundedSurface, NavItem, ToggleSwitch
from version import APP_VERSION
from auth_utils import apply_stealth_mode, remove_from_taskbar, hide_from_screen_capture

class ControlPanel:
    # ── BetterMint design tokens ───────────────────────────────────────────
    BG       = "#18191b"
    BG_CARD  = "#202226"
    BG_DEEP  = "#151618"
    BORDER   = "#30343b"
    TEXT     = "#ffffff"
    MUTED    = "#a8b0bc"
    ACCENT   = "#218cf4"
    ACCENT2  = "#176bb6"
    ACCENT3  = "#14568f"
    FIELD    = "#292c32"
    BADGE    = "#292c32"
    GREEN    = "#4ade80"
    RED      = "#ef4444"

    # Per-category accent colours + glyphs so each section card reads at a
    # glance (used by create_section_card's colour chips).
    SECTION_COLORS = {
        "display":   "#58a6ff",
        "visuals":   "#bc8cff",
        "engine":    "#f5c542",
        "humanizer": "#4ade80",
        "quality": "#2dd4bf",
        "automove":  "#ff6b81",
        "coach":     "#ff9e64",
        "books":     "#38bdf8",
        "privacy":   "#f87171",
        "internals": "#9a92c9",
        "logs":      "#2dd4bf",
        "all":       "#218cf4",
    }
    SECTION_ICONS = {
        "display":   "\u25b2",
        "visuals":   "\u25c9",
        "engine":    "\u2699",
        "humanizer": "\u2660",
        "quality": "\u25c8",
        "automove":  "\u2192",
        "coach":     "\u2726",
        "books":     "\u25a0",
        "privacy":   "\u2756",
        "internals": "\u2691",
        "logs":      "\u2261",
        "all":       "\u25c8",
    }

    def __init__(self, overlay):
        self.overlay = overlay
        self.root = tk.Toplevel(overlay.root)
        self.root.title(f"Chess Suite · {APP_VERSION}")
        self.root.geometry(f"{min(1280, self.root.winfo_screenwidth()-80)}x{min(820, self.root.winfo_screenheight()-80)}")
        self.root.minsize(840, 600)
        self.root.resizable(True, True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=self.BG)
        self.root.option_add("*foreground", self.TEXT)
        self.root.option_add("*Button.highlightThickness", 0)
        self.root.option_add("*Button.borderWidth", 0)

        self.active_tab = "engine"

        # ── Main container ──────────────────────────────────────────────
        self.main_container = tk.Frame(self.root, bg=self.BG)
        self.main_container.pack(fill=tk.BOTH, expand=True)

        # ── Control frame (everything below the tab bar) ────────────────
        self.control_frame = tk.Frame(self.main_container, bg=self.BG,
                                      relief=tk.FLAT, borderwidth=0)
        self.control_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        # Dummy refs for legacy compat
        self.sidebar_collapsed = False
        self.sidebar_frame = None
        self.toggle_btn = None
        self.sidebar_canvas = None
        self.scrollable_sidebar = None

        # Build the control panel UI (the single welcome/login screen is
        # shown by show_nebula_welcome before the visualizer is created).
        self.setup_tab_bar()
        self.setup_main_controls()

        # The overlay's settings are already restored from settings.json before
        # this panel is built; make sure every widget reflects them, not just
        # the ones the constructor seeded directly from the overlay.
        self.sync_vars_from_overlay()
        self._fit_descriptions(self.control_frame)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _fit_descriptions(self, widget):
        if isinstance(widget, tk.Label) and int(widget.cget('wraplength') or 0) > 0:
            widget.bind('<Configure>',lambda e: e.widget.configure(wraplength=max(80,e.width-12)),add='+')
        for child in widget.winfo_children():
            self._fit_descriptions(child)

    def _show_splash(self):
        """Show a BetterMint-style animated splash overlay that fades out."""
        try:
            splash = tk.Toplevel(self.root)
            splash.overrideredirect(True)
            splash.attributes("-topmost", True)
            splash.configure(bg="#176bb6")

            # Centre over parent
            self.root.update_idletasks()
            rw = self.root.winfo_width()  or 1060
            rh = self.root.winfo_height() or 720
            rx = self.root.winfo_rootx()
            ry = self.root.winfo_rooty()
            sw, sh = 420, 220
            x = rx + (rw - sw) // 2
            y = ry + (rh - sh) // 2
            splash.geometry(f"{sw}x{sh}+{x}+{y}")

            # Content
            tk.Label(splash, text="\u265f", font=("Segoe UI", 52),
                     bg="#176bb6", fg="#ffffff").pack(pady=(28, 6))
            tk.Label(splash, text="Chess Suite",
                     font=("Segoe UI", 20, "bold"),
                     bg="#176bb6", fg="#ffffff").pack()
            tk.Label(splash, text="NEBULA EDITION",
                     font=("Segoe UI", 9),
                     bg="#176bb6", fg="#d0c4ff").pack(pady=(4, 0))

            # Fade out after 1.4 s
            def _fade(step=0):
                if not splash.winfo_exists():
                    return
                alpha = max(0.0, 1.0 - step * 0.08)
                try:
                    splash.attributes("-alpha", alpha)
                except Exception:
                    pass
                if alpha > 0:
                    splash.after(40, lambda: _fade(step + 1))
                else:
                    try:
                        splash.destroy()
                    except Exception:
                        pass

            splash.after(1400, _fade)
        except Exception:
            pass

    def _on_content_canvas_resize(self, event):
        self.content_canvas.itemconfigure(self._content_win, width=event.width)
        columns = 2 if event.width >= 1440 else 1
        if columns != getattr(self, '_layout_columns', 1):
            self._show_active_tab_sections()

    def _on_content_inner_configure(self):
        """The content frame resized (initial layout, tab switch, window
        resize): update the scrollregion and fade in whatever is visible."""
        try:
            self.content_canvas.configure(
                scrollregion=self.content_canvas.bbox("all"))
            self._unveil_visible_cards()
        except Exception:
            pass

    def _on_content_scroll(self, *args):
        """Scrollbar command: forward to the canvas, then reveal cards that
        scrolled into the viewport."""
        try:
            self.content_canvas.yview(*args)
            self._unveil_visible_cards()
        except Exception:
            pass

    def _on_mousewheel(self, event):
        if hasattr(self, "content_canvas") and self.content_canvas and self.content_canvas.winfo_exists():
            try:
                self.content_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except tk.TclError:
                pass
            # Fade in cards that just entered the viewport while scrolling.
            self._unveil_visible_cards()

    def setup_tab_bar(self):
        self.sidebar_frame = tk.Frame(self.main_container, bg=self.BG_DEEP, width=202)
        self.sidebar_frame.pack(side=tk.LEFT, fill=tk.Y, before=self.control_frame)
        self.sidebar_frame.pack_propagate(False)
        brand = tk.Frame(self.sidebar_frame, bg=self.BG_DEEP)
        brand.pack(fill=tk.X, padx=18, pady=(26,24))
        tk.Label(brand,text='♟',bg=self.ACCENT,fg='white',font=('Segoe UI',18),padx=7).pack(side=tk.LEFT)
        tk.Label(brand,text='Chess Suite',bg=self.BG_DEEP,fg=self.TEXT,font=('Segoe UI',13,'bold')).pack(side=tk.LEFT,padx=9)
        footer = tk.Frame(self.sidebar_frame,bg=self.BG_DEEP)
        footer.pack(side=tk.BOTTOM,fill=tk.X,padx=20,pady=16)
        tk.Label(footer,text=f'DESKTOP  /  {APP_VERSION}',bg=self.BG_DEEP,fg=self.MUTED,font=('Segoe UI',9)).pack(anchor='w')
        self.sidebar_canvas=tk.Canvas(self.sidebar_frame,bg=self.BG_DEEP,highlightthickness=0,width=202)
        self.sidebar_canvas.pack(fill=tk.BOTH,expand=True)
        nav=tk.Frame(self.sidebar_canvas,bg=self.BG_DEEP)
        win=self.sidebar_canvas.create_window(12,0,window=nav,anchor='nw',width=178)
        nav.bind('<Configure>',lambda e:self.sidebar_canvas.configure(scrollregion=self.sidebar_canvas.bbox('all')))
        self.nav_tabs=[('engine','Engine'),('quality','Accuracy & draws'),('display','Suggestions'),
                       ('humanizer','Opponent model'),('automove','AutoMove'),('logs','Analysis log'),
                       ('coach','Coach'),('books','Opening books'),('visuals','Appearance'),
                       ('privacy','Privacy'),('internals','Connection'),('all','All settings')]
        self.nav_buttons={}
        for i,(tab,label) in enumerate(self.nav_tabs):
            if i in (0,6,8):
                tk.Label(nav,text={0:'ANALYSIS',6:'TOOLS',8:'WORKSPACE'}[i],bg=self.BG_DEEP,fg='#767f8c',
                         font=('Segoe UI',8,'bold'),anchor='w').pack(fill=tk.X,padx=12,pady=(12,7))
            button=NavItem(nav,label,lambda t=tab:self.select_tab(t))
            button.pack(fill=tk.X,pady=2)
            for event,delta in [('<Button-4>',-1),('<Button-5>',1)]:
                button.bind(event,lambda e,d=delta:self.sidebar_canvas.yview_scroll(d,'units'))
            button.bind('<MouseWheel>',lambda e:self.sidebar_canvas.yview_scroll(-int(e.delta/120),'units'))
            self.nav_buttons[tab]=button
        self._update_tab_bar_style()
        header=tk.Frame(self.control_frame,bg=self.BG)
        header.pack(fill=tk.X,padx=28,pady=(28,8))
        tk.Label(header,text='WORKSPACE  /  ANALYSIS',bg=self.BG,fg=self.MUTED,font=('Segoe UI',9)).pack(side=tk.LEFT)
        self.header_right=tk.Frame(header,bg=self.BG)
        self.header_right.pack(side=tk.RIGHT)
        self.connection_text=tk.Label(self.header_right,text='Bridge disconnected',bg=self.BG,fg=self.MUTED,font=('Segoe UI',9))
        self.connection_text.pack(side=tk.LEFT,padx=8)
        self.connection_indicator=tk.Canvas(self.header_right,width=10,height=10,bg=self.BG,highlightthickness=0)
        self.connection_indicator.pack(side=tk.RIGHT)
        self.status_oval=self.connection_indicator.create_oval(1,1,9,9,fill=self.RED,outline='')
        self.header_title_label=tk.Label(self.control_frame,text='Engine workspace',bg=self.BG,fg=self.TEXT,
                                         font=('Segoe UI',24,'bold'),anchor='w')
        self.header_title_label.pack(fill=tk.X,padx=28,pady=(8,5))
        tk.Label(self.control_frame,text='Configure your analysis. Keep every move in focus.',bg=self.BG,fg=self.MUTED,
                 font=('Segoe UI',10),anchor='w').pack(fill=tk.X,padx=28,pady=(0,20))
        metrics=tk.Frame(self.control_frame,bg=self.BG)
        metrics.pack(fill=tk.X,padx=22,pady=(0,14))
        self.metric_values={}
        for i,(key,label) in enumerate([('target','TARGET DEPTH'),('search','SEARCH DEPTH'),('lines','CANDIDATES'),('state','SUGGESTIONS')]):
            tile=RoundedSurface(metrics,bg=self.BG,surface=self.BG_CARD,padding=12)
            tile.grid(row=0,column=i,sticky='ew',padx=6)
            metrics.grid_columnconfigure(i,weight=1,uniform='metric')
            tk.Label(tile.body,text=label,bg=self.BG_CARD,fg=self.MUTED,font=('Segoe UI',8),anchor='w').pack(fill=tk.X)
            value=tk.Label(tile.body,text='—',bg=self.BG_CARD,fg=self.TEXT,font=('Segoe UI',17,'bold'),anchor='w')
            value.pack(fill=tk.X,pady=(8,0))
            self.metric_values[key]=value
        self.update_overview_metrics()

    def update_overview_metrics(self):
        ov=self.overlay
        engines=getattr(ov,'engines',[])
        engine=engines[min(getattr(ov,'primary_engine_index',0),len(engines)-1)] if engines else {}
        ready=ov._suggestions_are_ready()
        values={'target':engine.get('target_depth',ov.analysis_depth),
                'search':engine.get('current_depth',0) if engines else '—',
                'lines':engine.get('_multipv_count','—'),
                'state':'Ready' if ready else ('No result' if engines and engine.get('accepting_info') is False else ('Searching' if engines else 'Idle'))}
        for key,value in values.items():
            self.metric_values[key].configure(text=str(value),fg=self.ACCENT if key=='search' else self.TEXT,
                                              font=('Segoe UI',12 if key=='state' else 17,'bold'))

    def select_tab(self, tab_id):
        self.active_tab = tab_id
        if hasattr(self, "content_canvas"):
            self.content_canvas.yview_moveto(0)
        self._update_tab_bar_style()
        self._show_active_tab_sections()

    def _update_tab_bar_style(self):
        for tab, button in self.nav_buttons.items():
            button.set_selected(tab == self.active_tab)

    def _on_tab_hover(self, tab_id, hovering):
        self.nav_buttons[tab_id]._hover(hovering)

    @staticmethod
    def _mix_color(c1, c2, t):
        a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
        b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
        return "#{:02X}{:02X}{:02X}".format(*(int(a[i] + (b[i] - a[i]) * t) for i in range(3)))

    def _collect_bg_widgets(self, w):
        """All widgets under `w` (including w) whose bg can be dimmed safely.

        Each widget is added exactly once.  A naive "append child, then recurse"
        version added every frame/label twice (once by the append, once because
        the recursion starts with the child itself), so _dim_card dimmed each
        surface twice and recorded the half-dimmed colour as its "original",
        leaving section headers permanently darker than the card (the black bar
        behind the header titles).
        """
        out = []
        seen = set()
        stack = [w]
        while stack:
            node = stack.pop()
            if id(node) in seen:
                continue
            seen.add(id(node))
            out.append(node)
            for child in node.winfo_children():
                if isinstance(child, (tk.Frame, tk.Label, tk.Checkbutton,
                                      tk.Radiobutton, tk.LabelFrame)):
                    stack.append(child)
        return out

    def _dim_card(self, card):
        """Freeze a card in its hidden colour state so the scroll-reveal can
        fade it up from here.  Records each surface's original colour so
        _fade_in_card can restore it exactly."""
        try:
            if not card.winfo_exists():
                return
            targets = self._collect_bg_widgets(card)
            orig = {}
            for w in targets:
                try:
                    col = w.cget("bg")
                except Exception:
                    col = self.BG_CARD
                orig[w] = col
                try:
                    w.config(bg=self._mix_color(col, self.BG_DEEP, 0.45))
                except Exception:
                    pass
            card._fade_targets = targets
            card._fade_orig = orig
            card._fade_after_id = None
        except Exception:
            pass

    @staticmethod
    def _ease_out_cubic(f):
        return 1 - (1 - f) ** 3

    def _fade_in_card(self, card, step=0, steps=10, delay=0):
        """Animate a section card from its dimmed state to its normal colours.

        Uses a smooth ease-out curve (the old effect was a linear dark->light
        flash that fought the widget's own row colours).  `delay` (ms) lets
        cards cascade in one-after-another when a page is rebuilt; the card is
        marked `_revealed` only once fully shown so the scroll-reveal ignores it.
        """
        try:
            if not card.winfo_exists() or not card.winfo_manager():
                return
            if step == 0:
                # The canvas fires <Configure> constantly while the window
                # lays itself out.  Restarting the fade from step 0 each time
                # cancelled the in-flight animation, so it never reached the
                # final restore: cards stayed half-dimmed and, after repeated
                # reveal passes, the section header frame ended up several
                # dim-levels darker than the card (the "black bar" behind the
                # header titles).  A running fade must be left alone.
                if getattr(card, "_fade_after_id", None) is not None:
                    return
                if getattr(card, "_fade_orig", None) is None:
                    self._dim_card(card)
                if delay and delay > 0:
                    card._fade_after_id = card.after(
                        delay, lambda: self._fade_in_card(card, 1, steps))
                    return

            targets = getattr(card, "_fade_targets", None) or []
            orig = getattr(card, "_fade_orig", None) or {}
            if not targets:
                card._revealed = True
                card._fade_after_id = None
                return

            f = step / steps
            e = self._ease_out_cubic(f)
            for w in targets:
                col = orig.get(w, self.BG_CARD)
                dim = self._mix_color(col, self.BG_DEEP, 0.45)
                try:
                    w.config(bg=self._mix_color(dim, col, e))
                except Exception:
                    pass
            if step < steps:
                card._fade_after_id = card.after(
                    16, lambda: self._fade_in_card(card, step + 1, steps))
            else:
                for w, col in orig.items():
                    try:
                        w.config(bg=col)
                    except Exception:
                        pass
                card._fade_targets = None
                card._fade_orig = None
                card._fade_after_id = None
                card._revealed = True
        except Exception:
            pass

    def _visible_canvas_range(self):
        try:
            top = self.content_canvas.canvasy(0)
            bottom = top + self.content_canvas.winfo_height()
            return top, bottom
        except Exception:
            return 0, 10 ** 9

    def _card_in_viewport(self, card, top, bottom):
        try:
            y = card.winfo_y()
            h = card.winfo_height()
            return (y + h >= top - 8) and (y <= bottom + 8)
        except Exception:
            return False

    def _unveil_visible_cards(self):
        # Settings stay at full contrast. Repainting need not animate every child.
        return

    def _show_active_tab_sections(self):
        if not hasattr(self, "section_frames"):
            return

        titles = {
            "all":       "All Settings",
            "display":   "Board Display & Suggestions",
            "visuals":   "Visualizer Appearance",
            "engine":    "Engine workspace",
            "quality":   "Accuracy & draws",
            "humanizer": "Opponent model",
            "automove":  "AutoMove — timing & mouse movement",
            "coach":     "Coach Mode",
            "books":     "Opening Books & Tablebases",
            "privacy":   "Streamproof & Privacy",
            "internals": "Extension Internals",
            "logs":      "Analysis Logs & Accuracy",
        }
        if hasattr(self, "header_title_label"):
            self.header_title_label.config(text=titles.get(self.active_tab, "Settings"))

        # 1) Hide every section frame first so switching is strictly exclusive.
        for cat, frame in self.section_frames.items():
            try:
                frame.pack_forget()
            except Exception:
                pass
            try:
                frame.grid_forget()
            except Exception:
                pass
            fade_id = getattr(frame, "_fade_after_id", None)
            if fade_id:
                try:
                    frame.after_cancel(fade_id)
                except Exception:
                    pass
                frame._fade_after_id = None
            frame._revealed = False

        columns = 2 if self.content_canvas.winfo_width() >= 1440 else 1
        for col in range(2):
            self.content_inner.grid_columnconfigure(col, weight=1 if col < columns else 0,
                                                    uniform='sec' if col < columns else '')
        if self.active_tab == 'all':
            for i, frame in enumerate(self.section_frames.values()):
                frame.grid(row=i//columns, column=i%columns, sticky='new', padx=16, pady=12)
        else:
            frame = self.section_frames.get(self.active_tab)
            if frame is not None:
                frame.grid(row=0, column=0, sticky='new', padx=16, pady=12, columnspan=columns)
        self._layout_columns = columns

        if hasattr(self, "content_canvas"):
            self.content_canvas.update_idletasks()
            self.content_canvas.configure(
                scrollregion=self.content_canvas.bbox("all"))
            self._unveil_visible_cards()

    def on_close(self):
        # Use the same cleanup path as closing the visualizer so engines,
        # hotkeys and the final settings write are all released.
        self.overlay.shutdown()

    def update_connection_indicator(self, color):
        fill_color = "#10B981" if color == "green" else "#EF4444"
        self.connection_indicator.itemconfig(self.status_oval, fill=fill_color)
        self.connection_text.config(text='Bridge connected' if color == 'green' else 'Bridge disconnected',
                                    fg=self.GREEN if color == 'green' else self.MUTED)

    def update_engine_pool_controls(self):
        try:
            paths = list(self.overlay.engine_paths)
            options = [(i, os.path.splitext(os.path.basename(p))[0]) for i, p in enumerate(paths)]
            count = len(options)
            if hasattr(self, "engine_pool_hint"):
                self.engine_pool_hint.config(text=(f"{count} engine{'s' if count != 1 else ''} loaded" if count else "No engines loaded"))
            if hasattr(self, "selected_engines_label"):
                self.selected_engines_label.config(text=f"Engines: {count}")
            if hasattr(self, "rotation_count_spin"):
                self.rotation_count_spin.config(to=max(1, count))
                self.rotation_count_var.set(max(1, min(self.rotation_count_var.get(), max(1, count))))
            selected = min(self.overlay.rotation_anchor_index, max(0, count - 1))
            if hasattr(self, "primary_engine_select"):
                self.primary_engine_select.update_options(options, selected if count else None)
            if not count and hasattr(self, "primary_engine_var"):
                self.primary_engine_var.set("Load an engine first")
            if hasattr(self, "engine_drag_list"):
                pool_engines = sorted(self.overlay.pool.engines, key=lambda e: e.get("priority", 99))
                items = [(e["name"], e["name"]) for e in pool_engines]
                self.engine_drag_list.set_items(items)
        except Exception as err:
            print(f"[UI Pool Update Error]: {err}")

    def _on_engine_reorder(self, ordered_names):
        """Called when the drag reorder list changes order."""
        try:
            self.overlay.pool.set_priority_order(ordered_names)
            if ordered_names and hasattr(self, "primary_engine_var"):
                self.primary_engine_var.set(ordered_names[0])
            self.overlay.trigger_gui_update()
        except Exception as err:
            print(f"[Engine Reorder Error]: {err}")

    def update_extension_status(self):
        """Update the Internals section with current extension state."""
        MUTED = self.MUTED
        try:
            count = self.overlay.connected_clients
            if hasattr(self, "ext_status_label"):
                self.ext_status_label.config(
                    text=f"{count} client{'s' if count != 1 else ''} connected" if count else "No extension",
                    fg="#10B981" if count else MUTED)
            if hasattr(self, "ext_clock_label"):
                clocks = getattr(self.overlay, "extension_clocks", {})
                s_ms = clocks.get("self_ms")
                o_ms = clocks.get("opp_ms")
                def fmt(ms):
                    if ms is None: return "--:--"
                    s = max(0, int(ms)) // 1000
                    return f"{s // 60}:{s % 60:02d}"
                self.ext_clock_label.config(text=f"{fmt(s_ms)} / {fmt(o_ms)}")
            if hasattr(self, "ext_variant_label"):
                self.ext_variant_label.config(text=getattr(self.overlay, "extension_variant", "standard") or "standard")
            if hasattr(self, "ext_exploits_label"):
                feats = getattr(self.overlay, "extension_exploits", [])
                self.ext_exploits_label.config(text=", ".join(feats) if feats else "None detected")
        except Exception:
            pass

    def _on_ranking_mode_change(self, value):
        """Called when the ranking mode dropdown changes."""
        mode = str(value).strip().lower()
        if mode in EnginePool.RANKING_MODES:
            self.overlay.ranking_mode = mode
            self.overlay.pool.ranking_mode = mode
            self.overlay.pool._rerank()
            self.overlay.trigger_gui_update()
            self.overlay._save_all_settings()
            print(f"[Ranking] Mode set to {mode}")

    def _on_hosted_base_change(self):
        """Persist the hosted-engine WebSocket base URL from the entry box."""
        try:
            value = str(self.hosted_base_entry.get() or "").strip().rstrip("/")
            if not value:
                value = "wss://ProtonnDev-engine.hf.space"
            self.overlay.hosted_engine_base = value
            self.overlay._save_all_settings()
        except Exception as err:
            print(f"[Hosted Engine Base Error]: {err}")

    def _open_hosted_engine_dialog(self):
        """Toplevel picker for hosted (no-download) engines."""
        try:
            groups = [
                ("Maia (human-like)", [
                    (f"maia-{e}", f"Maia {e} - plays like a {e}-ish human (depth ~5)")
                    for e in range(1100, 2000, 100)]),
                ("Rodent III personalities", [
                    ("rodent3-anand", "Rodent - Anand (fast, slight attacking bias)"),
                    ("rodent3-anderssen", "Rodent - Anderssen (romantic attacker)"),
                    ("rodent3-fischer", "Rodent - Fischer (attacking, contemptuous)"),
                    ("rodent3-larsen", "Rodent - Larsen (tricky, speculative)"),
                    ("rodent3-marshall", "Rodent - Marshall (very attacking, sacrificial)"),
                    ("rodent3-petrosian", "Rodent - Petrosian (defensive, closed)"),
                    ("rodent3-rubinstein", "Rodent - Rubinstein (classical, endgame)"),
                    ("rodent3-spassky", "Rodent - Spassky (defensive, space)"),
                    ("rodent3-tarrasch", "Rodent - Tarrasch (mobility, bishops)"),
                    ("rodent3-drunk", "Rodent - Drunk (fun, random noise)"),
                    ("rodent3-kinghunter", "Rodent - King Hunter (fun, mad attacker)"),
                    ("rodent3-tortoise", "Rodent - Tortoise (fun, slow defender)")]),
                ("Patricia (hyper-aggressive)", [
                    (f"patricia-{e}", f"Patricia {e} - attacking engine at {e} Elo")
                    for e in (1100, 1400, 1700, 2000, 2300, 2600, 2900, 3200)]),
                ("Stockfish (versioned)", [
                    (f"stockfish-{v}", f"Stockfish {v}")
                    for v in (1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16)]),
                ("Fairy-Stockfish (variants)", [
                    ("fairy-stockfish", "Fairy-Stockfish - Chess960, atomic, horde, ...")]),
                ("Komodo", [
                    ("komodo-dragon", "Komodo Dragon - strong positional NNUE"),
                    ("komodo-14", "Komodo 14 - the chess.com rated-bot engine")]),
            ]
            options = []
            for _g, entries in groups:
                for eid, label in entries:
                    options.append((label, eid))

            win = tk.Toplevel(self.root)
            win.title("Add Hosted Engine")
            win.configure(bg=self.BG_CARD)
            win.attributes("-topmost", True)
            win.resizable(False, False)
            tk.Label(win, text="Pick a hosted engine - it runs online, no download needed:",
                     bg=self.BG_CARD, fg=self.TEXT, font=("Segoe UI", 9, "bold"),
                     anchor="w").pack(fill=tk.X, padx=14, pady=(12, 6))

            combo_var = tk.StringVar()
            combo = ttk.Combobox(win, textvariable=combo_var, values=[o[0] for o in options],
                                 width=62, state="readonly")
            combo.current(0)
            combo.pack(padx=14, pady=(0, 8))

            tk.Label(win, text="Or type a custom engine id (e.g. rodent3-nimzowitsch, stockfish-17):",
                     bg=self.BG_CARD, fg=self.MUTED, font=("Segoe UI", 8), anchor="w").pack(fill=tk.X, padx=14)
            custom_var = tk.StringVar()
            custom_entry = tk.Entry(win, textvariable=custom_var, width=64, bg=self.FIELD, fg=self.TEXT,
                                    insertbackground=self.TEXT, relief=tk.FLAT, highlightthickness=1,
                                    highlightbackground=self.BORDER, font=("Consolas", 9))
            custom_entry.pack(padx=14, pady=(4, 10))

            btnrow = tk.Frame(win, bg=self.BG_CARD)
            btnrow.pack(fill=tk.X, padx=14, pady=(0, 12))
            tk.Button(btnrow, text="Cancel", command=win.destroy, bg=self.ACCENT2, fg=self.TEXT,
                      relief=tk.FLAT, font=("Segoe UI", 9, "bold"), padx=12, pady=5,
                      cursor="hand2").pack(side=tk.LEFT)
            tk.Button(btnrow, text="Add & Start", command=lambda: self._confirm_hosted_engine(
                          win, combo_var.get(), custom_var.get(), options),
                      bg=self.ACCENT, fg=self.TEXT, relief=tk.FLAT, font=("Segoe UI", 9, "bold"),
                      padx=12, pady=5, cursor="hand2").pack(side=tk.RIGHT)
            win.bind("<Return>", lambda _e: self._confirm_hosted_engine(
                win, combo_var.get(), custom_var.get(), options))
            custom_entry.focus_set()
        except Exception as err:
            print(f"[Hosted Engine Dialog Error]: {err}")

    def _confirm_hosted_engine(self, win, combo_text, custom_text, options):
        """Resolve the dialog choice into a socket:// engine path and start it."""
        try:
            engine_id = ""
            for label, eid in options:
                if label == combo_text:
                    engine_id = eid
                    break
            typed = str(custom_text or "").strip().strip("/")
            if typed:
                engine_id = typed
            engine_id = engine_id.strip()
            if not engine_id:
                return
            win.destroy()
            path = "socket://" + engine_id
            if path not in self.overlay.engine_paths:
                self.overlay.engine_paths.append(path)
            self.update_engine_pool_controls()
            self.overlay.start_engine()
        except Exception as err:
            print(f"[Hosted Engine Confirm Error]: {err}")

    # Phase 2: Humanizer callbacks
    def _on_humanizer_toggle(self):
        self.overlay.humanizer.enabled = self.humanizer_enabled_var.get()
        self.overlay.humanizer._configured = True
        self.overlay._save_all_settings()

    def _on_humanizer_preset_change(self, value):
        self.overlay.humanizer.apply_preset(str(value))
        self.overlay.humanizer._configured = True
        if hasattr(self, "humanizer_mean_var"):
            self.humanizer_mean_var.set(self.overlay.humanizer.mean_ms)
        if hasattr(self, "humanizer_blunder_var"):
            self.humanizer_blunder_var.set(self.overlay.humanizer.blunder_chance)
        self.overlay._save_all_settings()

    def _on_humanizer_mean_change(self, value):
        try:
            self.overlay.humanizer.mean_ms = max(200, min(20000, int(value)))
        except (ValueError, tk.TclError):
            pass
        self.overlay.humanizer._configured = True
        self.overlay._save_all_settings()

    def _on_humanizer_blunder_change(self, value):
        try:
            self.overlay.humanizer.blunder_chance = max(0, min(0.5, float(value)))
        except (ValueError, tk.TclError):
            pass
        self.overlay.humanizer._configured = True
        self.overlay._save_all_settings()

    def _on_humanizer_pool_change(self, value):
        try:
            self.overlay.humanizer.rank_pool_size = max(1, min(10, int(value)))
        except (ValueError, tk.TclError):
            pass
        self.overlay.humanizer._configured = True
        self.overlay._save_all_settings()

    def _on_automove_toggle(self):
        try:
            self.overlay.automove.enabled = bool(self.automove_enabled_var.get())
            self.overlay.analysis_auto_mode = bool(self.automove_enabled_var.get())
        except Exception:
            pass
        if not self.overlay.automove.enabled:
            # Auto off: never flip.  Clear any stale flip bookkeeping so the
            # give-up window / episode can't linger into the next enable.
            self.overlay._automove_flip_giveup_until = 0.0
            self.overlay._automove_flip_episode_bottom = None
            self.overlay._automove_flip_streak = 0
        self.overlay.update_automove_status_label()
        self.overlay._save_all_settings()

    def _on_automove_test_drag(self):
        try:
            self.overlay._automove_test_drag()
        except Exception:
            pass

    def _on_automove_calibrate(self):
        try:
            self.overlay._calibrate_board()
        except Exception:
            pass

    def _on_automove_debug_geometry(self):
        try:
            self.overlay._automove_debug_geometry = bool(
                self.automove_debug_geometry_var.get())
            if self.overlay._automove_debug_geometry:
                self.overlay._debug_geometry_dump()
        except Exception:
            pass
        self.overlay._save_all_settings()

    # ── Compute Mode ───────────────────────────────────────────────────────
    def _refresh_compute_mode_visibility(self):
        """Show/hide CPU and GPU frames based on current compute_mode.
        CPU settings (Threads, Hash) are always kept visible so users can configure
        CPU resources for Stockfish and UCI engines regardless of compute mode.
        """
        mode = getattr(self.overlay, "compute_mode", "cpu")
        show_gpu = mode in ("gpu", "hybrid")
        try:
            self._cpu_settings_frame.pack(fill=tk.X, before=self._gpu_settings_frame)
        except Exception:
            try:
                self._cpu_settings_frame.pack(fill=tk.X)
            except Exception:
                pass
        try:
            if show_gpu:
                self._gpu_settings_frame.pack(fill=tk.X)
            else:
                self._gpu_settings_frame.pack_forget()
        except Exception:
            pass

    def _on_compute_mode_change(self, mode):
        """Called when user clicks a Compute Mode button."""
        mode = str(mode).strip().lower()
        if mode not in ("cpu", "gpu", "hybrid"):
            return
        self.overlay.compute_mode = mode
        self._refresh_compute_mode_visibility()
        self.overlay._save_all_settings()
        print(f"[Compute Mode] Switched to: {mode}")

    def _on_gpu_backend_change(self, value):
        try:
            opts = getattr(self.overlay, "gpu_engine_options", {}) or {}
            opts["Backend"] = str(value or "cudnn")
            self.overlay.gpu_engine_options = opts
            self.overlay._save_all_settings()
        except Exception as err:
            print(f"[GPU Backend Change Error]: {err}")

    def _on_gpu_weights_change(self):
        try:
            path = str(self.gpu_weights_var.get() or "").strip()
            opts = getattr(self.overlay, "gpu_engine_options", {}) or {}
            opts["WeightsFile"] = path
            self.overlay.gpu_engine_options = opts
            self.overlay._save_all_settings()
        except Exception as err:
            print(f"[GPU Weights Change Error]: {err}")

    def _on_gpu_threads_change(self):
        try:
            t = max(0, min(128, int(self.gpu_threads_var.get())))
            opts = getattr(self.overlay, "gpu_engine_options", {}) or {}
            opts["Threads"] = t
            self.overlay.gpu_engine_options = opts
            self.overlay._save_all_settings()
        except Exception as err:
            print(f"[GPU Threads Change Error]: {err}")

    def _pick_gpu_weights(self):
        try:
            import tkinter.filedialog as fd
            path = fd.askopenfilename(
                title="Select LCZero network weights file",
                filetypes=[("Network weights", "*.gz *.pb *.pb.gz *.onnx"), ("All files", "*.*")])
            if path:
                self.gpu_weights_var.set(path)
                self._on_gpu_weights_change()
        except Exception as err:
            print(f"[Pick GPU Weights Error]: {err}")

    def _apply_gpu_options(self):
        """Persist GPU options then restart the engine so they take effect."""
        self._on_gpu_backend_change(self.gpu_backend_var.get())
        self._on_gpu_weights_change()
        self._on_gpu_threads_change()
        try:
            self.overlay.start_engine()
        except Exception as err:
            print(f"[Apply GPU Options Error]: {err}")

    def _on_automove_debug_screen(self):

        try:
            self.overlay._screen_detector_debug = bool(
                self.automove_debug_screen_var.get())
        except Exception:
            pass
        self.overlay._save_all_settings()

    def _on_automove_complex_enabled(self):
        try:
            self.overlay.automove.complex_enabled = bool(
                self.automove_complex_enabled_var.get())
        except Exception:
            pass
        self.overlay._save_all_settings()

    def _on_auto_queue_enabled(self):
        try:
            self.overlay._auto_queue_enabled = bool(
                self.auto_queue_enabled_var.get())
            if not self.overlay._auto_queue_enabled:
                self.overlay._auto_queue_pending = False
        except Exception:
            pass
        self.overlay._save_all_settings()

    def _clamp_automove_ranges(self):
        am = self.overlay.automove
        if am.think_max_ms < am.think_min_ms:
            am.think_max_ms = am.think_min_ms
        if am.think_complex_max_ms < am.think_complex_min_ms:
            am.think_complex_max_ms = am.think_complex_min_ms
        if am.event_delay_max_ms < am.event_delay_min_ms:
            am.event_delay_max_ms = am.event_delay_min_ms
        self._sync_automove_value_labels()

    def _sync_automove_value_labels(self):
        try:
            am = self.overlay.automove
            pairs = (
                ("automove_think_min_val", "%.1fs" % (am.think_min_ms / 1000.0)),
                ("automove_think_max_val", "%.1fs" % (am.think_max_ms / 1000.0)),
                ("automove_event_delay_min_val", "%.0fms" % am.event_delay_min_ms),
                ("automove_event_delay_max_val", "%.0fms" % am.event_delay_max_ms),
                ("automove_path_speed_val", "%.1fx" % am.path_speed),
                ("automove_curvature_val", "%.2f" % am.curvature),
                ("automove_micro_movement_val", "%.2f" % am.micro_movement),
                ("automove_hesitation_val", "%.2f" % am.hesitation),
                ("automove_speed_variation_val", "%.2f" % am.speed_variation),
            )
            for attr, text in pairs:
                lbl = getattr(self, attr, None)
                if lbl is not None:
                    try:
                        lbl.config(text=text)
                    except Exception:
                        pass
            cpx = getattr(self, "automove_complex_val", None)
            if cpx is not None:
                try:
                    cpx.config(text="%d–%ds" % (
                        am.think_complex_min_ms / 1000,
                        am.think_complex_max_ms / 1000))
                except Exception:
                    pass
        except Exception:
            pass

    def _on_automove_think_min(self, value):
        try:
            self.overlay.automove.think_min_ms = int(float(value) * 1000)
        except (ValueError, tk.TclError):
            pass
        self._clamp_automove_ranges()
        self.overlay._save_all_settings()

    def _on_automove_think_max(self, value):
        try:
            self.overlay.automove.think_max_ms = int(float(value) * 1000)
        except (ValueError, tk.TclError):
            pass
        self._clamp_automove_ranges()
        self.overlay._save_all_settings()

    def _on_automove_think_complex(self, value):
        try:
            self.overlay.automove.think_complex_max_ms = int(float(value) * 1000)
        except (ValueError, tk.TclError):
            pass
        self._clamp_automove_ranges()
        self.overlay._save_all_settings()

    def _on_automove_think_complex_range(self, min_val, max_val):
        try:
            am = self.overlay.automove
            am.think_complex_min_ms = int(min_val * 1000)
            am.think_complex_max_ms = int(max_val * 1000)
        except (ValueError, tk.TclError):
            pass
        self._clamp_automove_ranges()
        self.overlay._save_all_settings()

    def _on_automove_path_speed(self, value):
        try:
            self.overlay.automove.path_speed = max(0.1, min(6.0, float(value)))
        except (ValueError, tk.TclError):
            pass
        self._sync_automove_value_labels()
        self.overlay._save_all_settings()

    def _on_automove_event_delay_min(self, value):
        try:
            self.overlay.automove.event_delay_min_ms = int(float(value))
        except (ValueError, tk.TclError):
            pass
        self._clamp_automove_ranges()
        self.overlay._save_all_settings()

    def _on_automove_event_delay_max(self, value):
        try:
            self.overlay.automove.event_delay_max_ms = int(float(value))
        except (ValueError, tk.TclError):
            pass
        self._clamp_automove_ranges()
        self.overlay._save_all_settings()

    def _on_automove_ratio(self, attr, value):
        try:
            setattr(self.overlay.automove, attr, max(0.0, min(1.0, float(value))))
        except (ValueError, tk.TclError):
            pass
        self._sync_automove_value_labels()
        self.overlay._save_all_settings()

    def _on_coach_tts_change(self):
        try:
            self.overlay.coach_mode.tts_enabled = bool(self.coach_tts_var.get())
        except Exception:
            pass
        self.overlay._save_all_settings()

    def update_display_mode_buttons(self, mode):
        self.best_move_btn.config(bg=self.ACCENT if mode == "bestmove" else self.FIELD)
        self.pv_btn.config(bg=self.ACCENT if mode == "pv" else self.FIELD)
        self.multiPV_btn.config(bg=self.ACCENT if mode == "multiPV" else self.FIELD)
        self.others_btn.config(bg=self.ACCENT if mode == "others" else self.FIELD)
        self.advPV_btn.config(bg=self.ACCENT if mode == "advPV" else self.FIELD)

    def update_lock_status(self, locked):
        status = "LOCKED" if locked else "ON"
        color = self.RED if locked else self.GREEN
        self.lock_status_label.config(text=f"Hotkeys: {status}", fg=color)

    def update_terminal_toggle(self, visible, available):
        if not available:
            self.terminal_toggle_btn.config(text="Logging Terminal: Unavailable", state=tk.DISABLED, fg="#9CA3AF")
            return
        state = "HIDE" if visible else "SHOW"
        color = "#F4EEFF" if visible else "#A7F3D0"
        self.terminal_toggle_btn.config(text=f"Logging Terminal: {state}", state=tk.NORMAL, fg=color)

    def _on_threads_change(self, value):
        try:
            self.threads_val_label.config(text=str(int(value)))
        except Exception:
            pass
        if getattr(self, "_threads_debounce_id", None):
            try:
                self.root.after_cancel(self._threads_debounce_id)
            except Exception:
                pass
        self._threads_debounce_id = self.root.after(250, lambda: self.overlay.update_engine_threads(int(value)))

    def _on_hash_change(self, value):
        value = int(int(value) / 16) * 16
        try:
            self.hash_val_label.config(text=str(int(value)))
        except Exception:
            pass
        if getattr(self, "_hash_debounce_id", None):
            try:
                self.root.after_cancel(self._hash_debounce_id)
            except Exception:
                pass
        self._hash_debounce_id = self.root.after(300, lambda: self.overlay.update_engine_hash(int(value)))

    def _on_uci_elo_change(self):
        try:
            v = int(self.uci_elo_var.get())
        except (ValueError, tk.TclError):
            return
        if v < 0:
            v = 0
        if v > 3500:
            v = 3500
        self.overlay.engine_uci_elo = v
        self.overlay._save_all_settings()

    def _on_contempt_change(self):
        try:
            v = int(self.contempt_var.get())
        except (ValueError, tk.TclError):
            return
        v = max(-200, min(200, v))
        self.overlay.engine_contempt = v
        self.overlay._save_all_settings()

    @staticmethod
    def _elo_label_for(elo, opts, fallback="Custom"):
        for num, lbl in opts:
            if int(num) == int(elo):
                return lbl
        return f"{int(elo)} · {fallback}"

    def sync_vars_from_overlay(self):
        """Push the overlay's current settings (post-restore) into every panel
        variable so the UI always reflects the saved config.

        The panel seeds most vars from the overlay at build time; this is the
        single source of truth for the handful that used hardcoded defaults and
        for any later re-load of settings.json.
        """
        try:
            ov = self.overlay
            elo_opts = [
                (300, "300 · Beginner"), (500, "500 · Casual"),
                (1200, "1200 · Club"), (1400, "1400 · Intermediate"),
                (1600, "1600 · Advanced"), (2000, "2000 · Expert"),
                (2400, "2400 · Master"),
            ]
            def setv(name, value):
                if hasattr(self, name):
                    getattr(self, name).set(value)
            setv("player_color_var", getattr(ov, "player_color", "w"))
            setv("extra_moves_var", max(0, int(getattr(ov, "multi_pv_count", 1) or 1) - 1))
            setv("visuals_coach_var", bool(getattr(ov, "coach_visible", True)))
            setv("visuals_ext_var", bool(
                getattr(ov, "external_eval", None) is not None
                and getattr(getattr(ov, "external_eval", None), "is_visible", lambda: False)()))
            setv("visuals_eval_var", bool(getattr(ov, "eval_bar_visible", True)))
            setv("visuals_grid_var", bool(getattr(ov, "grid_visible", True)))
            if hasattr(self, "depth_display_button"):
                self.depth_display_button.config(text="Depth display: " +
                    ("ON" if getattr(ov, "depth_display_visible", False) else "OFF"))
            setv("visuals_dash_var", bool(getattr(ov, "arrow_dash", False)))
            setv("visuals_animate_var", bool(getattr(ov, "arrow_animate", False)))
            setv("rotation_var", bool(getattr(ov, "engine_rotation_enabled", False)))
            setv("rotation_count_var", max(1, int(getattr(ov, "rotation_engine_count", 1) or 1)))
            setv("ranking_mode_var", str(getattr(ov, "ranking_mode", "priority") or "priority"))
            setv("depth_var", int(getattr(ov, "analysis_depth", 18) or 18))
            setv("refresh_speed_var", int(getattr(ov, "gui_update_delay", 100) or 100))
            setv("uci_elo_var", int(getattr(ov, "engine_uci_elo", 0) or 0))
            setv("contempt_var", int(getattr(ov, "engine_contempt", 24) or 0))
            setv("syzygy_var", str(getattr(ov, "engine_syzygy_path", "") or ""))
            setv("recommendation_accuracy_var", bool(
                getattr(ov, "recommendation_accuracy_enabled", True)))
            setv("dynamic_depth_var", bool(getattr(ov, "dynamic_depth_enabled", False)))
            setv('strength_first_var', getattr(ov, 'recommendation_strength_first', False))
            setv('accuracy_pool_var', ov.accuracy_candidate_limit)
            setv('anti_draw_var', ov.anti_draw_enabled)
            setv('draw_threshold_var', ov.anti_draw_threshold)
            setv('draw_advantage_var', ov.anti_draw_min_advantage)
            setv("humanizer_enabled_var", bool(getattr(ov.humanizer, "enabled", True)))
            setv("humanizer_preset_var", str(
                getattr(ov.humanizer, "preset", "intermediate") or "intermediate"))
            setv("humanizer_mean_var", int(getattr(ov.humanizer, "mean_ms", 3200) or 3200))
            setv("humanizer_blunder_var", float(
                getattr(ov.humanizer, "blunder_chance", 0.08)))
            setv("humanizer_pool_var", int(
                getattr(ov.humanizer, "rank_pool_size", 3) or 3))
            setv("show_opponent_move_var", bool(getattr(ov, "show_opponent_move", True)))
            setv("accuracy_var", bool(getattr(ov, "accuracy_display_enabled", True)))
            setv("blunder_filter_var", bool(
                getattr(ov, "aggressive_blunder_filter_enabled", True)))
            setv("coach_key_var", str(getattr(ov, "coach_groq_key", "") or ""))
            setv("coach_model_var", str(
                getattr(ov, "coach_model", "llama-3.3-70b-versatile") or ""))
            setv("coach_tts_var", bool(getattr(ov.coach_mode, "tts_enabled", False)))
            setv("book_cloud_var", bool(getattr(ov.book_manager, "cloud_enabled", True)))
            setv("book_source_var", str(
                getattr(ov.book_manager, "cloud_source", "masters") or "masters"))
            setv("book_lichess_token_var", str(
                getattr(ov.book_manager, "lichess_token", "") or ""))
            setv("book_tb_var", bool(getattr(ov.book_manager, "tb_enabled", True)))
            setv("book_stage_var", bool(getattr(ov.book_manager, "stage_show", True)))
            if hasattr(self, "elo_var"):
                self.elo_var.set(self._elo_label_for(
                    getattr(ov, "engine_elo", 2000) or 2000, elo_opts, "Custom"))
            if hasattr(self, "opponent_elo_var"):
                self.opponent_elo_var.set(self._elo_label_for(
                    getattr(ov, "opponent_elo", 1600) or 1600, elo_opts, "Custom"))
            if hasattr(self, "threads_scale") and hasattr(self, "threads_val_label"):
                self.threads_scale.set(int(getattr(ov, "engine_threads", 1) or 1))
                self.threads_val_label.config(text=str(int(getattr(ov, "engine_threads", 1) or 1)))
            if hasattr(self, "hash_scale") and hasattr(self, "hash_val_label"):
                self.hash_scale.set(int(getattr(ov, "engine_hash", 16) or 16))
                self.hash_val_label.config(text=str(int(getattr(ov, "engine_hash", 16) or 16)))
            if hasattr(self, "blunder_threshold_scale") and hasattr(self, "blunder_threshold_val_label"):
                self.blunder_threshold_scale.set(float(
                    getattr(ov, "aggressive_blunder_threshold", 0.50)))
                self.blunder_threshold_val_label.config(text=f"{float(getattr(ov, 'aggressive_blunder_threshold', 0.50)):.2f}")
            if hasattr(self, "blunder_threshold_frame"):
                if getattr(ov, "aggressive_blunder_filter_enabled", True):
                    self.blunder_threshold_frame.pack(fill=tk.X)
                else:
                    self.blunder_threshold_frame.pack_forget()
            if hasattr(self, "range_slider"):
                self.range_slider.set_range(
                    int(getattr(ov, "target_recommendation_accuracy_min", 70) or 70),
                    int(getattr(ov, "target_recommendation_accuracy_max", 80) or 80))
                self.update_recommendation_accuracy_range_label()
            if hasattr(self, "priority_slider"):
                self.priority_slider.set(int(
                    getattr(ov, "target_recommendation_accuracy_priority", 75) or 75))
                self.update_recommendation_accuracy_priority_label()
            # AutoMove panel.
            setv("automove_enabled_var", bool(getattr(ov.automove, "enabled", False)))
            setv("automove_debug_geometry_var",
                 bool(getattr(ov, "_automove_debug_geometry", False)))
            setv("automove_debug_screen_var",
                 bool(getattr(ov, "_screen_detector_debug", False)))
            setv("automove_complex_enabled_var",
                 bool(getattr(ov.automove, "complex_enabled", True)))
            setv("auto_queue_enabled_var",
                 bool(getattr(ov, "_auto_queue_enabled", False)))
            if hasattr(self, "automove_think_min_slider"):
                self.automove_think_min_slider.set(ov.automove.think_min_ms / 1000.0)
            if hasattr(self, "automove_think_max_slider"):
                self.automove_think_max_slider.set(ov.automove.think_max_ms / 1000.0)
            if hasattr(self, "automove_complex_slider"):
                self.automove_complex_slider.set_range(
                    ov.automove.think_complex_min_ms / 1000.0,
                    ov.automove.think_complex_max_ms / 1000.0)
            if hasattr(self, "automove_event_delay_min_slider"):
                self.automove_event_delay_min_slider.set(ov.automove.event_delay_min_ms)
            if hasattr(self, "automove_event_delay_max_slider"):
                self.automove_event_delay_max_slider.set(ov.automove.event_delay_max_ms)
            if hasattr(self, "automove_path_speed_slider"):
                self.automove_path_speed_slider.set(ov.automove.path_speed)
            if hasattr(self, "automove_curvature_slider"):
                self.automove_curvature_slider.set(ov.automove.curvature)
            if hasattr(self, "automove_micro_movement_slider"):
                self.automove_micro_movement_slider.set(ov.automove.micro_movement)
            if hasattr(self, "automove_hesitation_slider"):
                self.automove_hesitation_slider.set(ov.automove.hesitation)
            if hasattr(self, "automove_speed_variation_slider"):
                self.automove_speed_variation_slider.set(ov.automove.speed_variation)
            self._sync_automove_value_labels()
            # Compute mode + GPU widgets
            _cm = str(getattr(ov, "compute_mode", "cpu") or "cpu")
            setv("compute_mode_var", _cm)
            _gpu_opts = getattr(ov, "gpu_engine_options", {}) or {}
            if hasattr(self, "gpu_backend_var"):
                self.gpu_backend_var.set(str(_gpu_opts.get("Backend", "cudnn") or "cudnn"))
            if hasattr(self, "gpu_weights_var"):
                self.gpu_weights_var.set(str(_gpu_opts.get("WeightsFile", "") or ""))
            if hasattr(self, "gpu_threads_var"):
                self.gpu_threads_var.set(int(_gpu_opts.get("Threads", 1)))
            # Refresh mode-button colours
            if hasattr(self, "_mode_btns_ref"):
                for _b, _mk in self._mode_btns_ref:
                    try:
                        _b.config(bg=self.ACCENT if _mk == _cm else self.FIELD,
                                  fg="#ffffff" if _mk == _cm else self.MUTED)
                    except Exception:
                        pass
            # Refresh visibility of CPU/GPU frames
            self._refresh_compute_mode_visibility()
        except Exception:
            pass

    def _pick_syzygy_dir(self):
        d = filedialog.askdirectory(title="Syzygy / tablebase directory")
        if d:
            self.syzygy_var.set(d)
            self.overlay.engine_syzygy_path = d
            self.overlay._save_all_settings()

    def _apply_extra_engine_options(self):
        """Copy every extra UCI option from the GUI vars into the overlay, then
        push them to every running engine."""
        try:
            elo = int(self.uci_elo_var.get())
        except (ValueError, tk.TclError):
            elo = 0
        self.overlay.engine_uci_elo = max(0, min(3500, elo))
        try:
            cont = int(self.contempt_var.get())
        except (ValueError, tk.TclError):
            cont = 24
        self.overlay.engine_contempt = max(-200, min(200, cont))
        self.overlay.engine_syzygy_path = (self.syzygy_var.get() or "").strip()
        self.overlay._save_all_settings()
        try:
            self.overlay.apply_all_extra_uci_options()
        except Exception as e:
            print(f"[UCI Apply] {e}")
        try:
            if self.overlay.current_fen:
                self.overlay.analyse_position(self.overlay.current_fen, self.overlay.engine_elo)
        except Exception:
            pass

    def _on_range_slider_change(self, min_val, max_val):
        self.overlay.target_recommendation_accuracy_min = min_val
        self.overlay.target_recommendation_accuracy_max = max_val
        self.overlay.target_recommendation_accuracy = round((min_val + max_val) / 2)
        prio = getattr(self.overlay, "target_recommendation_accuracy_priority", min_val)
        prio = max(min_val, min(max_val, prio))
        self.overlay.target_recommendation_accuracy_priority = prio
        try:
            self.priority_slider.set(prio)
        except Exception:
            pass
        self.update_recommendation_accuracy_range_label()
        self.update_recommendation_accuracy_priority_label()
        self.overlay._refresh_accuracy_selection(prefer_range=True)

    def _on_priority_slider_change(self, value):
        low = self.overlay.target_recommendation_accuracy_min
        high = self.overlay.target_recommendation_accuracy_max
        value = max(low, min(high, value))
        self.overlay.target_recommendation_accuracy_priority = value
        self.priority_slider.set(value)
        self.update_recommendation_accuracy_priority_label()
        self.overlay._refresh_accuracy_selection(prefer_range=True)

    def _refresh_ai_toggle_btn(self):
        on = bool(getattr(self.overlay, "coach_ai_enabled", False))
        self.ai_toggle_btn.config(text=f"\U0001f916 AI Coach: {'ON' if on else 'OFF'}")
        self.ai_toggle_btn.config(bg=self.ACCENT if on else self.FIELD,
                                  fg="#ffffff" if on else self.TEXT)

    def _toggle_coach_ai(self):
        self.overlay.coach_ai_enabled = not getattr(self.overlay, "coach_ai_enabled", False)
        self.overlay._save_all_settings()
        self._refresh_ai_toggle_btn()
        try:
            self.overlay._coach_refresh()
        except Exception:
            pass

    # ── Visuals tab callbacks ─────────────────────────────────────────────
    def _on_visuals_coach_toggle(self):
        try:
            self.overlay.set_coach_visible(bool(self.visuals_coach_var.get()))
        except Exception:
            pass
        self.overlay._save_all_settings()

    def _on_visuals_ext_toggle(self):
        try:
            self.overlay.toggle_external_eval()
            self.visuals_ext_var.set(self.overlay.external_eval is not None
                                     and self.overlay.external_eval.is_visible())
        except Exception:
            pass
        self.overlay._save_all_settings()

    def _on_visuals_eval_toggle(self):
        try:
            self.overlay.toggle_eval_bar_visibility(bool(self.visuals_eval_var.get()))
        except Exception:
            pass
        self.overlay._save_all_settings()

    def _on_visuals_grid_toggle(self):
        try:
            self.overlay.toggle_grid_visibility(bool(self.visuals_grid_var.get()))
        except Exception:
            pass
        self.overlay._save_all_settings()

    def _on_visuals_dash_toggle(self):
        self.overlay.set_arrow_dash(bool(self.visuals_dash_var.get()))
        self.overlay._save_all_settings()

    def _on_visuals_animate_toggle(self):
        self.overlay.set_arrow_animate(bool(self.visuals_animate_var.get()))
        self.overlay._save_all_settings()

    def _on_coach_key_change(self, event=None):
        self.overlay.coach_groq_key = (self.coach_key_var.get() or "").strip()
        self.overlay._save_all_settings()

    def _on_coach_model_change(self, event=None):
        model = (self.coach_model_var.get() or "").strip()
        self.overlay.coach_model = model or "llama-3.3-70b-versatile"
        self.coach_model_var.set(self.overlay.coach_model)
        self.overlay._save_all_settings()

    def _test_coach_ai(self):
        self._on_coach_key_change()
        self._on_coach_model_change()
        try:
            self.overlay._coach_post("Testing Groq connection…", "coach")
        except Exception:
            pass

        def worker():
            try:
                reply = self.overlay._groq_chat(
                    [{"role": "user", "content": "Reply with exactly: Groq OK"}],
                    timeout=20)
            except Exception as e:
                reply = f"Groq test failed: {e}"
            try:
                self.overlay.ui_queue.put(("coach", reply))
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()

    def _toggle_blunder_filter(self):
        self.overlay.aggressive_blunder_filter_enabled = self.blunder_filter_var.get()
        if self.overlay.aggressive_blunder_filter_enabled:
            self.blunder_threshold_frame.pack(fill=tk.X, padx=4)
        else:
            self.blunder_threshold_frame.pack_forget()
        self.overlay._save_all_settings()
        self.overlay._reanalyse_after_option_change()

    def update_book_status(self, filename):
        self.book_btn.config(text=f"\U0001f4d6 Book: {filename}")

    def _toggle_dynamic_depth(self):
        self.overlay.dynamic_depth_enabled = self.dynamic_depth_var.get()
        self.overlay._save_all_settings()

    def _on_blunder_threshold_change(self, value):
        try:
            fval = float(value)
            self.overlay.aggressive_blunder_threshold = fval
            self.blunder_threshold_val_label.config(text=f"{fval:.2f}")
        except (ValueError, TypeError, AttributeError):
            pass
        self.overlay._save_all_settings()
        self.overlay._reanalyse_after_option_change()

    def update_recommendation_accuracy_range_label(self):
        try:
            low = self.overlay.target_recommendation_accuracy_min
            high = self.overlay.target_recommendation_accuracy_max
            self.recommendation_accuracy_range_label.config(text=f"{low}% – {high}%")
        except Exception:
            pass

    def update_recommendation_accuracy_priority_label(self):
        try:
            p = self.overlay.target_recommendation_accuracy_priority
            self.recommendation_accuracy_priority_label.config(text=f"{p}%")
        except Exception:
            pass

    def _bind_mousewheel_recursive(self, widget):
        try:
            widget.bind("<MouseWheel>", self._on_mousewheel, add="+")
            for child in widget.winfo_children():
                self._bind_mousewheel_recursive(child)
        except Exception:
            pass

    def create_section_card(self, cat, title):
        """Build a BetterMint-style section card inside a scrollable content area.

        Lazily creates the content scaffold (header title, canvas, scrollbar,
        inner frame) on first use, then registers each card under its category
        id so tab filtering can show/hide it.
        """
        if not getattr(self, "content_canvas", None) or not self.content_canvas.winfo_exists():
            self.section_frames = {}

            self.content_outer = tk.Frame(self.control_frame, bg=self.BG)
            self.content_outer.pack(fill=tk.BOTH, expand=True)

            self.content_canvas = tk.Canvas(self.content_outer, bg=self.BG,
                                            highlightthickness=0, bd=0)
            self.content_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            sb = tk.Scrollbar(self.content_outer, orient=tk.VERTICAL,
                              command=self._on_content_scroll,
                              bg=self.BG_DEEP, troughcolor=self.BG,
                              activebackground=self.BORDER,
                              highlightthickness=0, borderwidth=0,
                              width=16)
            sb.pack(side=tk.RIGHT, fill=tk.Y)
            self.content_canvas.configure(yscrollcommand=sb.set)

            self.content_inner = tk.Frame(self.content_canvas, bg=self.BG)
            self._content_win = self.content_canvas.create_window(
                (0, 0), window=self.content_inner, anchor="nw")
            self.content_inner.bind(
                "<Configure>",
                lambda _e: self._on_content_inner_configure())
            self.content_canvas.bind(
                "<Configure>",
                self._on_content_canvas_resize)
            # Reveal dimmed cards once the canvas settles at a size (initial
            # layout and window resizes).
            self.content_canvas.bind(
                "<Configure>",
                lambda _e: self._unveil_visible_cards(), add="+")

        shell=RoundedSurface(self.content_inner,bg=self.BG,surface=self.BG_CARD,border=self.BORDER,padding=16)
        card=shell.body
        tk.Label(card,text=title,bg=self.BG_CARD,fg=self.TEXT,
                 font=('Segoe UI',12,'bold'),anchor='w').pack(fill=tk.X,padx=12,pady=(8,18))
        self.section_frames[cat]=shell
        return card

    def setup_main_controls(self):
        """Build main control deck with BetterMint section cards and scrollable content."""
        # ── BetterMint-aligned tokens ─────────────────────────────────────
        CARD_BG   = self.BG_CARD          # #202226
        ROW_BG    = "#1c1e22"             # slightly darker than card
        BORDER    = self.BORDER           # #30343b
        TEXT      = self.TEXT             # #ffffff
        MUTED     = self.MUTED            # #aaaaaa
        ACCENT    = self.ACCENT           # #218cf4
        ACCENT2   = self.ACCENT2          # #176bb6
        FIELD     = self.FIELD            # #202226
        BADGE     = self.BADGE            # #202226
        GREEN     = self.GREEN            # #4ade80

        def row(parent, label, alt=False):
            bg = ROW_BG if alt else CARD_BG
            r = tk.Frame(parent, bg=bg)
            r.pack(fill=tk.X)
            inner = tk.Frame(r, bg=bg)
            inner.pack(fill=tk.X, padx=18, pady=12)
            tk.Label(inner, text=label, bg=bg, fg=MUTED, font=("Segoe UI", 10), anchor="w", width=20, wraplength=190, justify=tk.LEFT).pack(side=tk.LEFT)
            return inner

        def badge(parent, text, bg=BADGE, fg="#86c5ff"):
            return tk.Label(parent, text=text, bg=bg, fg=fg, font=("Segoe UI", 9, "bold"), padx=6, pady=2, relief=tk.FLAT)

        def spin(parent, var, frm, to, cmd, inc=1):
            def _fire(*_a):
                # Some callbacks take the new value, others read the var
                # themselves; try the value first, fall back to no-arg.
                try:
                    cmd(var.get())
                except TypeError:
                    try:
                        cmd()
                    except Exception:
                        pass
                except (ValueError, tk.TclError):
                    pass

            w = tk.Spinbox(parent, from_=frm, to=to, increment=inc, width=6,
                           textvariable=var, command=_fire, justify="center",
                           bg=FIELD, fg=TEXT, insertbackground=TEXT,
                           buttonbackground=ACCENT2, relief=tk.FLAT, disabledbackground=FIELD,
                           font=("Segoe UI", 10), highlightthickness=1, highlightbackground=BORDER)
            w.pack(side=tk.RIGHT)
            w.bind("<FocusOut>", lambda _e: _fire())
            w.bind("<Return>", lambda _e: _fire())
            return w

        def toggle_check(parent, text, var, cmd, alt=False):
            control=ToggleSwitch(parent,text,var,cmd,bg=ROW_BG if alt else CARD_BG)
            control.pack(fill=tk.X,pady=3)
            return control

        def mode_btn(parent, label, mode, attr):
            active = (self.overlay.display_mode == mode)
            b = tk.Button(parent, text=label,
                          command=lambda m=mode: self.overlay.set_display_mode(m),
                          bg=ACCENT if active else FIELD, fg=TEXT,
                          activebackground=ACCENT, activeforeground=TEXT,
                          relief=tk.FLAT, font=("Segoe UI", 9, "bold"),
                          padx=8, pady=6, cursor="hand2",
                          borderwidth=0, highlightthickness=0)
            b.bind("<Enter>", lambda _e, btn=b, a=active:
                   btn.config(bg=self.ACCENT2 if a else self.BG_CARD))
            b.bind("<Leave>", lambda _e, btn=b, a=active:
                   btn.config(bg=ACCENT if a else FIELD))
            setattr(self, attr, b)
            return b

        def action_btn(parent, text, cmd, primary=False):
            bg = ACCENT if primary else ACCENT2
            return tk.Button(parent, text=text, command=cmd, bg=bg, fg=TEXT,
                             activebackground=ACCENT, activeforeground=TEXT,
                             relief=tk.FLAT, font=("Segoe UI", 9, "bold"),
                             padx=10, pady=6, cursor="hand2")

        # ═══════════════════════════════════════════════════════════════════
        # DISPLAY
        # ═══════════════════════════════════════════════════════════════════
        vis_sec = self.create_section_card("display", "Board Display & Suggestions")
        bar_outer = tk.Frame(vis_sec, bg=CARD_BG)
        bar_outer.pack(fill=tk.X, padx=12, pady=(8, 6))
        tk.Label(bar_outer, text="Display Mode", bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9), anchor="w").pack(anchor="w", pady=(0, 4))
        modebar = tk.Frame(bar_outer, bg=BORDER)
        modebar.pack(fill=tk.X)
        specs = [
            ("Offensive",  "bestmove", "best_move_btn"),
            ("Best Move",  "pv",       "pv_btn"),
            ("Defensive",  "multiPV",  "multiPV_btn"),
            ("Brilliant",  "others",   "others_btn"),
            ("Tactician",  "advPV",    "advPV_btn"),
        ]
        for i, (lbl, md, attr) in enumerate(specs):
            b = mode_btn(modebar, lbl, md, attr)
            b.grid(row=i // 3, column=i % 3, sticky="ew", padx=1, pady=1)
            modebar.grid_columnconfigure(i % 3, weight=1)

        side_row = row(vis_sec, "Your side", alt=True)
        self.player_color_var = tk.StringVar(value=self.overlay.player_color)
        for txt, val in (("White", "w"), ("Black", "b"), ("Auto", "auto")):
            tk.Radiobutton(side_row, text=txt, variable=self.player_color_var, value=val,
                           command=lambda v=val: self.overlay.set_player_color(v),
                           bg=ROW_BG, fg=TEXT, selectcolor=FIELD,
                           activebackground=ROW_BG, activeforeground=TEXT,
                           font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=4)

        xtr_row = row(vis_sec, "Extra suggestions")
        self.extra_moves_var = tk.IntVar(value=self.overlay.multi_pv_count - 1)
        spin(xtr_row, self.extra_moves_var, 0, 7, self.overlay.update_multi_pv_count)

        # ═══════════════════════════════════════════════════════════════════
        # VISUALS
        # ═══════════════════════════════════════════════════════════════════
        vis_app = self.create_section_card("visuals", "Visualizer Appearance")

        self.visuals_coach_var = tk.BooleanVar(value=getattr(self.overlay, "coach_visible", True))
        toggle_check(vis_app, "Show coach AI panel", self.visuals_coach_var,
                     self._on_visuals_coach_toggle, alt=True)

        self.visuals_ext_var = tk.BooleanVar(value=False)
        toggle_check(vis_app, "External evaluation window",
                     self.visuals_ext_var, self._on_visuals_ext_toggle)

        self.visuals_eval_var = tk.BooleanVar(value=getattr(self.overlay, "eval_bar_visible", True))
        toggle_check(vis_app, "Show evaluation bar", self.visuals_eval_var,
                     self._on_visuals_eval_toggle, alt=True)

        self.visuals_grid_var = tk.BooleanVar(value=getattr(self.overlay, "grid_visible", True))
        toggle_check(vis_app, "Show board grid", self.visuals_grid_var,
                     self._on_visuals_grid_toggle)

        self.depth_display_button = action_btn(vis_app,
            "Depth display: " + ("ON" if getattr(self.overlay, "depth_display_visible", False) else "OFF"),
            self.overlay.toggle_depth_display)
        self.depth_display_button.pack(fill=tk.X, padx=12, pady=(6, 8))

        self.visuals_dash_var = tk.BooleanVar(value=getattr(self.overlay, "arrow_dash", False))
        toggle_check(vis_app, "Dashed arrows", self.visuals_dash_var,
                     self._on_visuals_dash_toggle, alt=True)

        self.visuals_animate_var = tk.BooleanVar(value=getattr(self.overlay, "arrow_animate", False))
        toggle_check(vis_app, "Pulsing arrows", self.visuals_animate_var,
                     self._on_visuals_animate_toggle)

        sc_row = row(vis_app, "Arrow size")
        self.arrow_scale_slider = CircleSlider(sc_row, from_=0.5, to=2.0,
                                               value=getattr(self.overlay, "arrow_scale", 1.0),
                                               resolution=0.1, command=self.overlay.set_arrow_scale,
                                               bg=ROW_BG, width=160)
        self.arrow_scale_slider.pack(side=tk.RIGHT, fill=tk.X, expand=True)

        op_row = row(vis_app, "Arrow opacity", alt=True)
        self.arrow_opacity_slider = CircleSlider(op_row, from_=0.25, to=1.0,
                                                 value=getattr(self.overlay, "arrow_opacity", 1.0),
                                                 resolution=0.05, command=self.overlay.set_arrow_opacity,
                                                 bg=ROW_BG, width=160)
        self.arrow_opacity_slider.pack(side=tk.RIGHT, fill=tk.X, expand=True)

        hint_lbl = tk.Label(vis_app, text="Arrows are drawn on a click-through layer, so they never\n"
                                          "block clicks on the chess site beneath.",
                            bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9), justify="left")
        hint_lbl.pack(fill=tk.X, padx=12, pady=(4, 8))

        # ═══════════════════════════════════════════════════════════════════
        # ENGINE
        # ═══════════════════════════════════════════════════════════════════
        eng_sec = self.create_section_card("engine", "Engine Pool & Rotation")
        btn_row = tk.Frame(eng_sec, bg=CARD_BG)
        btn_row.pack(fill=tk.X, padx=12, pady=(8, 4))
        action_btn(btn_row, "Load Engine", self.overlay.select_engine).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        action_btn(btn_row, "Start Engine", self.overlay.start_engine, primary=True).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        hosted_row = tk.Frame(eng_sec, bg=CARD_BG)
        hosted_row.pack(fill=tk.X, padx=12, pady=(0, 4))
        action_btn(hosted_row, "Add Hosted Engine (no download)", self.overlay.add_hosted_engine).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.hosted_base_entry = tk.Entry(hosted_row, bg=FIELD, fg=TEXT, insertbackground=TEXT,
                                          relief=tk.FLAT, highlightthickness=1,
                                          highlightbackground=BORDER, font=("Consolas", 8), width=26)
        self.hosted_base_entry.insert(0, getattr(self.overlay, "hosted_engine_base", "wss://ProtonnDev-engine.hf.space"))
        self.hosted_base_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
        self.hosted_base_entry.bind("<Return>", lambda _e: self._on_hosted_base_change())
        self.hosted_base_entry.bind("<FocusOut>", lambda _e: self._on_hosted_base_change())

        self.engine_pool_hint = tk.Label(eng_sec, text="No engines loaded", bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9), anchor="w", padx=12, pady=2)
        self.engine_pool_hint.pack(fill=tk.X)

        sel_row = tk.Frame(eng_sec, bg=CARD_BG)
        sel_row.pack(fill=tk.X, padx=12, pady=(4, 2))
        tk.Label(sel_row, text="Primary Engine", bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9), anchor="w").pack(side=tk.LEFT)
        self.primary_engine_var = tk.StringVar(value="Load an engine first")
        self.primary_engine_select = InlineSelect(sel_row, self.primary_engine_var, [], self.overlay.select_primary_engine, width=24, bg=CARD_BG)
        self.primary_engine_select.pack(side=tk.RIGHT, fill=tk.X, expand=True)

        self.rotation_var = tk.BooleanVar(value=getattr(self.overlay, "engine_rotation_enabled", False))
        toggle_check(eng_sec, "Rotate primary after each move", self.rotation_var, self.overlay.toggle_engine_rotation, alt=True)

        rr = row(eng_sec, "Engines in rotation")
        self.rotation_count_var = tk.IntVar(value=getattr(self.overlay, "rotation_engine_count", 1))
        self.rotation_count_spin = spin(rr, self.rotation_count_var, 1, 1, self.overlay.update_rotation_count)

        rank_row = row(eng_sec, "Ranking mode", alt=True)
        self.ranking_mode_var = tk.StringVar(value=getattr(self.overlay, "ranking_mode", "priority"))
        rank_opts = [("priority", "Priority"), ("smart", "Smart (best score)"), ("roundrobin", "Round-robin")]
        rank_select = InlineSelect(rank_row, self.ranking_mode_var, rank_opts, self._on_ranking_mode_change, width=16, bg=CARD_BG)
        rank_select.pack(side=tk.RIGHT, fill=tk.X, expand=True)

        drag_hint = tk.Label(eng_sec, text="Drag to reorder engine priority", bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9), anchor="w")
        drag_hint.pack(fill=tk.X, padx=12, pady=(6, 2))
        self.engine_drag_list = DragReorderList(eng_sec, items=[], on_reorder=self._on_engine_reorder, bg=CARD_BG, item_bg=ROW_BG, height=4)
        self.engine_drag_list.pack(fill=tk.X, padx=12, pady=(0, 4))

        tk.Frame(eng_sec, bg=BORDER, height=1).pack(fill=tk.X, padx=12, pady=4)

        # ── Engine Search & Resources (same tab) ──
        res_row_label = tk.Label(eng_sec, text="\u25C8  Search & Resources", bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9, "bold"))
        res_row_label.pack(fill=tk.X, padx=12, pady=(4, 2))

        # ── Compute Mode selector ────────────────────────────────────────
        compute_row = tk.Frame(eng_sec, bg=CARD_BG)
        compute_row.pack(fill=tk.X, padx=12, pady=(2, 6))

        tk.Label(compute_row, text="Settings shown", bg=CARD_BG, fg=MUTED,
                 font=("Segoe UI", 9), anchor="w").pack(side=tk.LEFT)

        self.compute_mode_var = tk.StringVar(
            value=getattr(self.overlay, "compute_mode", "cpu"))

        btn_frame = tk.Frame(compute_row, bg=CARD_BG)
        btn_frame.pack(side=tk.RIGHT)

        def _make_mode_btn(parent, label, mode_key):
            accent = self.ACCENT
            def _on_click():
                self.compute_mode_var.set(mode_key)
                self._on_compute_mode_change(mode_key)
                for btn, mk in _mode_btns:
                    try:
                        btn.config(bg=accent if mk == mode_key else self.FIELD,
                                   fg="#ffffff" if mk == mode_key else self.MUTED)
                    except Exception:
                        pass
            b = tk.Button(parent, text=label, command=_on_click,
                          bg=self.FIELD, fg=self.MUTED,
                          activebackground=accent, activeforeground="#ffffff",
                          relief=tk.FLAT, font=("Segoe UI", 9, "bold"),
                          padx=10, pady=3, cursor="hand2", bd=0)
            b.pack(side=tk.LEFT, padx=(0, 4))
            return b

        _mode_btns = []
        _btn_cpu = _make_mode_btn(btn_frame, "CPU", "cpu")
        _btn_gpu = _make_mode_btn(btn_frame, "LC0 GPU", "gpu")
        _btn_hyb = _make_mode_btn(btn_frame, "All", "hybrid")
        _mode_btns += [(_btn_cpu, "cpu"), (_btn_gpu, "gpu"), (_btn_hyb, "hybrid")]
        # Colour the active button immediately
        _cur = getattr(self.overlay, "compute_mode", "cpu")
        for _b, _mk in _mode_btns:
            _b.config(bg=self.ACCENT if _mk == _cur else self.FIELD,
                      fg="#ffffff" if _mk == _cur else self.MUTED)
        self._mode_btns_ref = _mode_btns  # keep refs alive

        elo_opts = [
            (300,  "300 · Beginner"),
            (500,  "500 · Casual"),
            (1200, "1200 · Club"),
            (1400, "1400 · Intermediate"),
            (1600, "1600 · Advanced"),
            (2000, "2000 · Expert"),
            (2400, "2400 · Master"),
        ]

        er = row(eng_sec, "Depth preset")
        _elo_label = "2000 · Expert"
        for _elo_num, _elo_lbl in elo_opts:
            if _elo_num == int(getattr(self.overlay, "engine_elo", 2000) or 2000):
                _elo_label = _elo_lbl
                break
        self.elo_var = tk.StringVar(value=_elo_label)
        self.elo_menu = InlineSelect(er, self.elo_var, elo_opts, self.overlay.update_engine_elo, width=18, bg=CARD_BG)
        self.elo_menu.pack(side=tk.RIGHT, fill=tk.X, expand=True)

        depth_row = row(res_sec := eng_sec, "Analysis depth", alt=True)
        self.depth_var = tk.IntVar(value=self.overlay.analysis_depth)
        self.depth_spin = spin(depth_row, self.depth_var, 1, 50, self.overlay.update_analysis_depth)

        tk.Label(eng_sec, text="Engine selection determines compute hardware; Stockfish uses CPU.",
                 bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9), wraplength=470).pack(fill=tk.X, padx=12)
        time_row = row(eng_sec, "Time limit ms (0 = none)")
        self.time_limit_var = tk.IntVar(value=self.overlay.analysis_time_ms)
        spin(time_row, self.time_limit_var, 0, 600000, self.overlay.update_search_limits, 100)
        node_row = row(eng_sec, "Node limit (0 = none)")
        self.node_limit_var = tk.IntVar(value=self.overlay.analysis_nodes)
        spin(node_row, self.node_limit_var, 0, 1000000000, self.overlay.update_search_limits, 10000)
        self.own_turn_var = tk.BooleanVar(value=self.overlay.only_own_turn)
        tk.Checkbutton(eng_sec, text="Analyze only my turn (disables opponent predictions)",
                       variable=self.own_turn_var, command=self.overlay.update_search_limits,
                       bg=CARD_BG, fg=TEXT, selectcolor=FIELD).pack(anchor="w", padx=12)
        tk.Label(eng_sec, text="Suggestions stay hidden if a time/node limit stops before target depth.",
                 bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=12)

        ref_row = row(eng_sec, "UI refresh (ms)")
        self.refresh_speed_var = tk.IntVar(value=self.overlay.gui_update_delay)
        self.refresh_speed_spin = spin(ref_row, self.refresh_speed_var, 50, 2000, self.overlay.update_refresh_speed, 50)

        hw = getattr(self.overlay, "hardware_profile", {}) or {}
        threads_max = int(getattr(self.overlay, "engine_threads_max", 0) or hw.get("threads_max", 128) or 128)
        hash_max = int(getattr(self.overlay, "engine_hash_max", 0) or hw.get("hash_max_mb", 16384) or 16384)

        # ── CPU settings frame (shown in CPU Only + Hybrid) ──────────────
        self._cpu_settings_frame = tk.Frame(eng_sec, bg=CARD_BG)
        self._cpu_settings_frame.pack(fill=tk.X)

        thr_row = row(self._cpu_settings_frame, "CPU Threads", alt=True)
        thr_hdr = tk.Frame(thr_row, bg=CARD_BG)
        thr_hdr.pack(side=tk.RIGHT)
        self.threads_val_label = badge(thr_hdr, text=str(self.overlay.engine_threads))
        self.threads_val_label.pack(side=tk.LEFT)
        tk.Label(thr_hdr, text=f"/ {threads_max}", bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(2, 0))
        self.threads_scale = CircleSlider(self._cpu_settings_frame, from_=1, to=threads_max, value=self.overlay.engine_threads, resolution=1, bg=CARD_BG, command=lambda v: self._on_threads_change(int(v)))
        self.threads_scale.pack(fill=tk.X, padx=12, pady=(0, 4))

        hash_row = row(self._cpu_settings_frame, "Hash Memory (MB)")
        hash_hdr = tk.Frame(hash_row, bg=CARD_BG)
        hash_hdr.pack(side=tk.RIGHT)
        self.hash_val_label = badge(hash_hdr, text=str(self.overlay.engine_hash))
        self.hash_val_label.pack(side=tk.LEFT)
        tk.Label(hash_hdr, text=f"/ {hash_max}", bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(2, 0))
        self.hash_scale = CircleSlider(self._cpu_settings_frame, from_=16, to=hash_max, value=self.overlay.engine_hash, resolution=16, bg=CARD_BG, command=lambda v: self._on_hash_change(int(v)))
        self.hash_scale.pack(fill=tk.X, padx=12, pady=(0, 4))

        # ── GPU settings frame (shown in GPU Only + Hybrid) ──────────────
        _gpu_opts = getattr(self.overlay, "gpu_engine_options", {}) or {}
        self._gpu_settings_frame = tk.Frame(eng_sec, bg=CARD_BG)
        self._gpu_settings_frame.pack(fill=tk.X)

        gpu_hdr = tk.Label(self._gpu_settings_frame,
                           text="\u26a1  GPU Settings (LCZero)",
                           bg=CARD_BG, fg="#f5c542",
                           font=("Segoe UI", 9, "bold"), anchor="w")
        gpu_hdr.pack(fill=tk.X, padx=12, pady=(6, 2))

        bknd_row = row(self._gpu_settings_frame, "Backend")
        self.gpu_backend_var = tk.StringVar(
            value=str(_gpu_opts.get("Backend", "cudnn") or "cudnn"))
        bknd_opts = [
            ("cudnn",      "cudnn  (GTX 9xx / 10xx)"),
            ("cudnn-auto", "cudnn-auto (auto-select)"),
            ("cudnn-fp16", "cudnn-fp16 (RTX 20xx+, faster)"),
            ("cuda-auto",  "cuda-auto  (fallback)"),
            ("cuda",       "cuda       (generic)"),
        ]
        InlineSelect(bknd_row, self.gpu_backend_var, bknd_opts,
                     self._on_gpu_backend_change, width=22,
                     bg=CARD_BG).pack(side=tk.RIGHT, fill=tk.X, expand=True)

        wf_row = tk.Frame(self._gpu_settings_frame, bg=CARD_BG)
        wf_row.pack(fill=tk.X, padx=12, pady=(2, 2))
        tk.Label(wf_row, text="Network weights", bg=CARD_BG, fg=MUTED,
                 font=("Segoe UI", 9), anchor="w").pack(side=tk.LEFT)
        self.gpu_weights_var = tk.StringVar(
            value=str(_gpu_opts.get("WeightsFile",
                "C:/Users/fa/Downloads/EngineWS/engines/lc0/791556.pb.gz") or ""))
        self.gpu_weights_entry = tk.Entry(
            wf_row, textvariable=self.gpu_weights_var, width=30,
            bg=self.FIELD, fg=self.TEXT, insertbackground=self.TEXT,
            relief=tk.FLAT, highlightthickness=1,
            highlightbackground=self.BORDER, font=("Consolas", 8))
        self.gpu_weights_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 4))
        tk.Button(wf_row, text="Pick", command=self._pick_gpu_weights,
                  bg=self.FIELD, fg=self.TEXT, activebackground=self.ACCENT2,
                  activeforeground=self.TEXT, relief=tk.FLAT,
                  font=("Segoe UI", 9, "bold"), padx=8, cursor="hand2"
                  ).pack(side=tk.LEFT)
        self.gpu_weights_entry.bind("<Return>",  lambda _e: self._on_gpu_weights_change())
        self.gpu_weights_entry.bind("<FocusOut>", lambda _e: self._on_gpu_weights_change())

        gpu_threads_row = row(self._gpu_settings_frame, "GPU Threads (0=auto)", alt=True)
        self.gpu_threads_var = tk.IntVar(
            value=int(_gpu_opts.get("Threads", 1)))
        tk.Spinbox(gpu_threads_row, from_=0, to=128, increment=1, width=5,
                   textvariable=self.gpu_threads_var,
                   command=self._on_gpu_threads_change,
                   bg=self.FIELD, fg=self.TEXT, relief=tk.FLAT,
                   buttonuprelief=tk.FLAT, buttondownrelief=tk.FLAT,
                   highlightthickness=1, highlightbackground=self.BORDER,
                   font=("Segoe UI", 9)).pack(side=tk.RIGHT)

        # Apply GPU settings button
        tk.Button(self._gpu_settings_frame, text="Apply GPU Options & Restart Engine",
                  command=self._apply_gpu_options,
                  bg=self.ACCENT, fg="#ffffff", activebackground=self.ACCENT2,
                  activeforeground=self.TEXT, relief=tk.FLAT,
                  font=("Segoe UI", 9, "bold"), pady=5, cursor="hand2"
                  ).pack(fill=tk.X, padx=12, pady=(4, 6))

        # Apply initial visibility based on current compute_mode
        self._refresh_compute_mode_visibility()

        ext_sec = tk.Frame(eng_sec, bg=CARD_BG, highlightthickness=0)
        ext_sec.pack(fill=tk.X, padx=12, pady=(4, 6))

        elo_row = tk.Frame(ext_sec, bg=CARD_BG)
        elo_row.pack(fill=tk.X, pady=(0, 4))
        tk.Label(elo_row, text="UCI_Elo (0 = off)", bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9), anchor="w").pack(side=tk.LEFT)
        self.uci_elo_var = tk.IntVar(value=int(getattr(self.overlay, "engine_uci_elo", 0) or 0))
        self.uci_elo_spin = tk.Spinbox(elo_row, from_=0, to=3500, increment=50, width=7, textvariable=self.uci_elo_var, command=self._on_uci_elo_change, bg=FIELD, fg=TEXT, relief=tk.FLAT, buttonuprelief=tk.FLAT, buttondownrelief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER, font=("Segoe UI", 9))
        self.uci_elo_spin.pack(side=tk.RIGHT)
        tk.Label(elo_row, text="Contempt (cp)", bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9), anchor="w").pack(side=tk.LEFT, padx=(24, 0))
        self.contempt_var = tk.IntVar(value=int(getattr(self.overlay, "engine_contempt", 24) or 0))
        self.contempt_spin = tk.Spinbox(elo_row, from_=-200, to=200, increment=5, width=5, textvariable=self.contempt_var, command=self._on_contempt_change, bg=FIELD, fg=TEXT, relief=tk.FLAT, buttonuprelief=tk.FLAT, buttondownrelief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER, font=("Segoe UI", 9))
        self.contempt_spin.pack(side=tk.RIGHT)

        syz_row = tk.Frame(ext_sec, bg=CARD_BG)
        syz_row.pack(fill=tk.X)
        self.syzygy_var = tk.StringVar(value=getattr(self.overlay, "engine_syzygy_path", "") or "")
        tk.Label(syz_row, text="Syzygy TB path", bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9), anchor="w").pack(side=tk.LEFT)
        self.syzygy_entry = tk.Entry(syz_row, textvariable=self.syzygy_var, width=20, bg=FIELD, fg=TEXT, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER, font=("Consolas", 10))
        self.syzygy_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 4))
        self.syzygy_btn = tk.Button(syz_row, text="Pick", command=self._pick_syzygy_dir, bg=FIELD, fg=TEXT, activebackground=ACCENT2, activeforeground=TEXT, relief=tk.FLAT, font=("Segoe UI", 9, "bold"), padx=8, cursor="hand2")
        self.syzygy_btn.pack(side=tk.LEFT)
        self.apply_opts_btn = tk.Button(ext_sec, text="Apply UCI Options", command=self._apply_extra_engine_options, bg=ACCENT, fg="#FFFFFF", activebackground=ACCENT2, activeforeground=TEXT, relief=tk.FLAT, font=("Segoe UI", 9, "bold"), pady=4, cursor="hand2")
        self.apply_opts_btn.pack(fill=tk.X, pady=(4, 0))

        gpus = hw.get("gpus", []) if isinstance(hw, dict) else []
        if gpus:
            gp_line = ", ".join(g["name"] for g in gpus[:3])
            if len(gpus) > 3:
                gp_line += f", +{len(gpus)-3}"
            tk.Label(eng_sec, text=f"Detected: {gp_line}", bg=CARD_BG, fg=ACCENT2, font=("Segoe UI", 9, "bold"), anchor="w", padx=12, wraplength=340, justify="left").pack(fill=tk.X, pady=(2, 4))

        quality_sec = self.create_section_card('quality', 'Recommendation accuracy & draw protection')
        tk.Frame(quality_sec, bg=BORDER, height=1).pack(fill=tk.X, padx=18, pady=14)
        tk.Label(quality_sec, text='Recommendation quality', bg=CARD_BG, fg=TEXT,
                 font=('Segoe UI', 12, 'bold'), anchor='w').pack(fill=tk.X, padx=18, pady=(0, 6))
        tk.Label(quality_sec, text='Estimates use the current engine, depth and supported UCI Elo. A depth preset changes depth; UCI Elo limits engine strength when supported. Game-review scores can differ.',
                 bg=CARD_BG, fg=MUTED, font=('Segoe UI', 10), justify='left', wraplength=650,
                 anchor='w').pack(fill=tk.X, padx=18, pady=(0, 10))
        self.recommendation_status_label = tk.Label(quality_sec, text='Waiting for analysis',
                 bg=FIELD, fg=TEXT, font=('Segoe UI', 10), justify='left', wraplength=650,
                 anchor='w', padx=12, pady=12)
        self.recommendation_status_label.pack(fill=tk.X, padx=18, pady=(0, 12))
        tk.Label(quality_sec,text='Opening protection: moves 1–10 use a validated book move, a close earlier continuation, or the strongest safe engine move. They do not chase the accuracy range. After move 10, the range and priority guide safe choices. Modes choose among estimates near the priority. Brilliant and tactician identify patterns in engine continuations; they are not site classifications. Strength first is an optional override.',
                 bg=CARD_BG,fg=MUTED,font=('Segoe UI',10),wraplength=650,justify='left',anchor='w').pack(fill=tk.X,padx=18,pady=(0,14))
        self.recommendation_accuracy_var = tk.BooleanVar(value=self.overlay.recommendation_accuracy_enabled)
        toggle_check(quality_sec, "Enable recommendation policy", self.recommendation_accuracy_var, self.overlay.toggle_recommendation_accuracy, alt=True)
        self.strength_first_var = tk.BooleanVar(value=self.overlay.recommendation_strength_first)
        toggle_check(quality_sec, "Strength first (range is advisory)", self.strength_first_var,
                     self.overlay.toggle_recommendation_strength_first, alt=True)
        tk.Label(quality_sec, text='Range targeting is the default. Changing the range or priority turns strength first off. Your loss cap, draw protection, opening protection and forced mates can prevent a match; the status explains the result. Neither mode guarantees a site review score or Elo rating.',
                 bg=CARD_BG, fg=MUTED, font=('Segoe UI', 10), wraplength=650,
                 justify='left', anchor='w').pack(fill=tk.X, padx=18, pady=(4, 12))
        self.accuracy_pool_var = tk.IntVar(value=self.overlay.accuracy_candidate_limit)
        spin(row(quality_sec, 'Accuracy search candidates (4–16)'), self.accuracy_pool_var, 4, 16,
             self.overlay.update_accuracy_search_candidates)
        acc_row = row(quality_sec, "Move accuracy range (%)")
        self.recommendation_accuracy_range_label = badge(acc_row, text=f"{self.overlay.target_recommendation_accuracy_min}% – {self.overlay.target_recommendation_accuracy_max}%")
        self.recommendation_accuracy_range_label.pack(side=tk.RIGHT)
        self.range_slider = DualThumbRangeSlider(quality_sec, from_=35, to=100, min_val=self.overlay.target_recommendation_accuracy_min, max_val=self.overlay.target_recommendation_accuracy_max, bg=CARD_BG, command=self._on_range_slider_change)
        self.range_slider.pack(fill=tk.X, padx=12, pady=(0, 6))
        prio_row = row(quality_sec, "Priority (%)")
        self.recommendation_accuracy_priority_label = badge(prio_row, text=f"{self.overlay.target_recommendation_accuracy_priority}%")
        self.recommendation_accuracy_priority_label.pack(side=tk.RIGHT)
        self.priority_slider = CircleSlider(quality_sec, from_=35, to=100, value=self.overlay.target_recommendation_accuracy_priority, bg=CARD_BG, command=self._on_priority_slider_change)
        self.priority_slider.pack(fill=tk.X, padx=12, pady=(0, 6))
        self.blunder_filter_var = tk.BooleanVar(value=self.overlay.aggressive_blunder_filter_enabled)
        blunder_check = toggle_check(quality_sec, "Limit loss for all suggestions", self.blunder_filter_var, self._toggle_blunder_filter, alt=True)
        HoverTip(blunder_check, "Applies to every recommendation, including target accuracy.\nA 0.50-pawn cap can prevent a 70–85% target.\nRaise it only if you want to allow weaker moves.")
        self.blunder_threshold_frame = tk.Frame(quality_sec, bg=CARD_BG)
        brow = tk.Frame(self.blunder_threshold_frame, bg=CARD_BG)
        brow.pack(fill=tk.X, padx=12, pady=(4, 2))
        tk.Label(brow, text="Max loss vs best (pawns)", bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9), anchor="w").pack(side=tk.LEFT)
        self.blunder_threshold_val_label = badge(brow, text=f"{self.overlay.aggressive_blunder_threshold:.2f}")
        self.blunder_threshold_val_label.pack(side=tk.RIGHT)
        self.blunder_threshold_scale = CircleSlider(self.blunder_threshold_frame, from_=0.0, to=5.0, value=self.overlay.aggressive_blunder_threshold, resolution=0.05, bg=CARD_BG, command=self._on_blunder_threshold_change)
        self.blunder_threshold_scale.pack(fill=tk.X, padx=12, pady=(0, 4))
        if not self.overlay.aggressive_blunder_filter_enabled:
            self.blunder_threshold_frame.pack_forget()
        else:
            self.blunder_threshold_frame.pack(fill=tk.X)
        self.anti_draw_var = tk.BooleanVar(value=self.overlay.anti_draw_enabled)
        toggle_check(quality_sec, 'Avoid drawish recommendations', self.anti_draw_var,
                     self.overlay.update_recommendation_policy, alt=True)
        self.draw_threshold_var = tk.DoubleVar(value=self.overlay.anti_draw_threshold)
        spin(row(quality_sec, 'Draw band ± pawns'), self.draw_threshold_var, 0, 2,
             self.overlay.update_recommendation_policy, 0.01)
        self.draw_advantage_var = tk.DoubleVar(value=self.overlay.anti_draw_min_advantage)
        spin(row(quality_sec, 'Prefer advantage ≥ pawns'), self.draw_advantage_var, 0.01, 5,
             self.overlay.update_recommendation_policy, 0.05)
        tk.Label(quality_sec, text='Avoids near-zero scores or an immediate stalemate when a safe alternative reaches the advantage threshold. Keeps the draw when no such alternative was found. This does not prove a forced draw or detect repetition from FEN alone.',
                 bg=CARD_BG, fg=MUTED, font=('Segoe UI', 9), wraplength=650,
                 justify='left', anchor='w').pack(fill=tk.X, padx=18, pady=10)
        self.context_auto_var=tk.BooleanVar(value=self.overlay.context_auto)
        toggle_check(quality_sec,'Use detected game context',self.context_auto_var,self.overlay.update_context_preferences)
        self.context_player_var=tk.IntVar(value=self.overlay.manual_player_rating)
        self.context_opponent_var=tk.IntVar(value=self.overlay.manual_opponent_rating)
        spin(row(quality_sec,'Your rating fallback (0 = unknown)'),self.context_player_var,0,4000,self.overlay.update_context_preferences)
        spin(row(quality_sec,'Opponent rating fallback'),self.context_opponent_var,0,4000,self.overlay.update_context_preferences)
        self.game_context_label=tk.Label(quality_sec,text=self.overlay._game_context_text(),bg=FIELD,fg=TEXT,
                                        font=('Segoe UI',10),wraplength=650,justify='left',anchor='w',padx=12,pady=12)
        self.game_context_label.pack(fill=tk.X,padx=18,pady=12)
        tk.Label(quality_sec,text='Ratings and clocks are context, not an Elo-to-accuracy formula. Missing fields stay unknown; manual fallbacks never change engine depth or UCI Elo.',
                 bg=CARD_BG,fg=MUTED,font=('Segoe UI',9),wraplength=650,justify='left',anchor='w').pack(fill=tk.X,padx=18,pady=(0,12))
        self.dynamic_depth_var = tk.BooleanVar(value=self.overlay.dynamic_depth_enabled)
        toggle_check(eng_sec, "Dynamic depth by complexity", self.dynamic_depth_var, self._toggle_dynamic_depth)
        self.book_btn = tk.Button(eng_sec, text="Load Opening Book (.bin)", command=self.overlay.load_opening_book, bg=FIELD, fg=TEXT, activebackground=ACCENT2, activeforeground=TEXT, relief=tk.FLAT, font=("Segoe UI", 9, "bold"), pady=6, cursor="hand2")
        self.book_btn.pack(fill=tk.X, padx=12, pady=(4, 4))
        self.analyzer_btn = tk.Button(eng_sec, text="PGN Game Analyzer", command=self.overlay.open_pgn_analyzer, bg=ACCENT, fg="#FFFFFF", activebackground=ACCENT2, activeforeground=TEXT, relief=tk.FLAT, font=("Segoe UI", 9, "bold"), pady=6, cursor="hand2")
        self.analyzer_btn.pack(fill=tk.X, padx=12, pady=(0, 8))

        # ═══════════════════════════════════════════════════════════════════
        # HUMANIZER
        # ═══════════════════════════════════════════════════════════════════
        hum_sec = self.create_section_card("humanizer", "Opponent model")
        tk.Label(hum_sec, text='Humanizer varies the opponent move prediction only. It does not change engine Elo, depth, options, your suggested move, or AutoMove timing. Configure your recommendations in Accuracy & draws; configure mouse movement and delays in AutoMove.',
                 bg=CARD_BG, fg=TEXT, font=('Segoe UI', 10), wraplength=650,
                 justify='left', anchor='w').pack(fill=tk.X, padx=18, pady=14)
        self.humanizer_enabled_var = tk.BooleanVar(value=getattr(self.overlay.humanizer, "enabled", True))
        toggle_check(hum_sec, "Vary predicted opponent moves", self.humanizer_enabled_var, lambda: self._on_humanizer_toggle(), alt=True)
        hum_preset_row = row(hum_sec, "Opponent prediction preset")
        self.humanizer_preset_var = tk.StringVar(value=getattr(self.overlay.humanizer, "preset", "intermediate"))
        hum_preset_opts = [("beginner", "Beginner (~600)"), ("intermediate", "Intermediate (~1400)"), ("advanced", "Advanced (~1900)"), ("master", "Master (~2300)")]
        InlineSelect(hum_preset_row, self.humanizer_preset_var, hum_preset_opts, self._on_humanizer_preset_change, width=16, bg=CARD_BG).pack(side=tk.RIGHT, fill=tk.X, expand=True)
        hum_blunder_row = row(hum_sec, "Weakest-line probability")
        self.humanizer_blunder_var = tk.DoubleVar(value=getattr(self.overlay.humanizer, "blunder_chance", 0.08))
        spin(hum_blunder_row, self.humanizer_blunder_var, 0, 0.5, self._on_humanizer_blunder_change, 0.01)
        hum_pool_row = row(hum_sec, "Prediction candidate pool", alt=True)
        self.humanizer_pool_var = tk.IntVar(value=getattr(self.overlay.humanizer, "rank_pool_size", 3))
        spin(hum_pool_row, self.humanizer_pool_var, 1, 10, self._on_humanizer_pool_change, 1)

        tk.Frame(hum_sec, bg=BORDER, height=1).pack(fill=tk.X, padx=12, pady=4)
        tk.Label(hum_sec, text="\u25C8  Opponent Model", bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9, "bold")).pack(fill=tk.X, padx=12, pady=(4, 2))

        tk.Label(hum_sec, text="Prediction samples engine-ranked moves; it is not an Elo-calibrated simulation.", bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9), wraplength=340, justify="left", anchor="w", padx=12, pady=4).pack(fill=tk.X)
        orow = row(hum_sec, "Opponent level")
        _opp_elo = int(getattr(self.overlay, "opponent_elo", 1600) or 1600)
        _opp_label = next((lbl for num, lbl in elo_opts if num == _opp_elo), f"{_opp_elo} · Custom")
        self.opponent_elo_var = tk.StringVar(value=_opp_label)
        self.opponent_elo_menu = InlineSelect(orow, self.opponent_elo_var, elo_opts, self.overlay.update_opponent_elo, width=18, bg=CARD_BG)
        self.opponent_elo_menu.pack(side=tk.RIGHT, fill=tk.X, expand=True)
        self.show_opponent_move_var = tk.BooleanVar(value=getattr(self.overlay, "show_opponent_move", True))
        toggle_check(hum_sec, "Show predicted opponent move", self.show_opponent_move_var, self.overlay.toggle_show_opponent_move, alt=True)
        self.accuracy_var = tk.BooleanVar(value=getattr(self.overlay, "accuracy_display_enabled", True))
        toggle_check(hum_sec, "Show game accuracy in log", self.accuracy_var, self.overlay.toggle_accuracy_display)
        self.player_elo_var = tk.StringVar(value="1600")

        # ═══════════════════════════════════════════════════════════════════
        # AUTOMOVE — HUMAN-LIKE MOUSE MOTION
        # ═══════════════════════════════════════════════════════════════════
        am_sec = self.create_section_card("automove", "AutoMove — timing & mouse movement")
        tk.Label(am_sec, text='AutoMove plays the first displayed suggestion. These controls set waiting time and cursor motion; opponent Humanizer settings do not affect them.',
                 bg=CARD_BG, fg=TEXT, font=('Segoe UI', 10), wraplength=650,
                 justify='left', anchor='w').pack(fill=tk.X, padx=18, pady=14)
        self.automove_enabled_var = tk.BooleanVar(
            value=getattr(self.overlay.automove, "enabled", False))
        toggle_check(am_sec, "Enable Auto (play your moves for you)",
                     self.automove_enabled_var, self._on_automove_toggle, alt=True)
        self.automove_status_label = tk.Label(
            am_sec, text="Auto: OFF", bg=CARD_BG, fg="#8B7BAE",
            font=("Segoe UI", 9, "bold"), anchor="w", padx=12)
        self.automove_status_label.pack(fill=tk.X, pady=(0, 4))
        self.automove_debug_geometry_var = tk.BooleanVar(
            value=bool(getattr(self.overlay, "_automove_debug_geometry", False)))
        toggle_check(am_sec, "Debug geometry (print coords every 0.7s)",
                     self.automove_debug_geometry_var,
                     self._on_automove_debug_geometry, alt=True)
        self.automove_debug_screen_var = tk.BooleanVar(
            value=bool(getattr(self.overlay, "_screen_detector_debug", False)))
        toggle_check(am_sec, "Debug bottom-colour detector (print screen "
                             "capture result every 1.5s)",
                     self.automove_debug_screen_var,
                     self._on_automove_debug_screen, alt=True)
        # ── Detector preview button ───────────────────────────────────────
        preview_btn_row = tk.Frame(am_sec, bg=CARD_BG)
        preview_btn_row.pack(fill=tk.X, padx=12, pady=(4, 6))
        tk.Button(
            preview_btn_row,
            text="\u25cf  Open Detector Preview",
            command=lambda: self.overlay.open_screen_preview(),
            bg="#2d1f4a", fg="#bc8cff",
            activebackground="#4a2d7a", activeforeground="#ffffff",
            font=("Segoe UI", 9, "bold"),
            relief=tk.FLAT, borderwidth=0, padx=10, pady=5,
            cursor="hand2",
        ).pack(side=tk.LEFT)
        tk.Label(
            preview_btn_row,
            text="See what the auto-colour detector captures",
            bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9),
        ).pack(side=tk.LEFT, padx=(8, 0))
        tk.Label(am_sec,
                 text="Plays each move as a full mouse drag: a mousedown on the\n"
                      "piece, many mousemoves along a curved path, then a\n"
                      "mouseup on the target square.",
                 bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9), justify="left",
                 anchor="w", padx=12, wraplength=360).pack(fill=tk.X, pady=(0, 4))

        am = self.overlay.automove

        def am_slider_row(label, attr, from_, to, resolution, fmt, default, cmd, alt=False):
            r = row(am_sec, label, alt=alt)
            hdr = tk.Frame(r, bg=CARD_BG)
            hdr.pack(side=tk.RIGHT)
            lbl = tk.Label(hdr, text=fmt.format(default), bg=CARD_BG, fg=ACCENT,
                           font=("Segoe UI", 9, "bold"))
            lbl.pack(side=tk.LEFT)
            setattr(self, "automove_%s_val" % attr, lbl)
            s = CircleSlider(am_sec, from_=from_, to=to, value=default,
                             resolution=resolution, bg=CARD_BG, command=cmd)
            setattr(self, "automove_%s_slider" % attr, s)
            s.pack(fill=tk.X, padx=12, pady=(0, 4))

        tk.Label(am_sec, text="\u25C8  Thinking time", bg=CARD_BG, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(fill=tk.X, padx=12, pady=(6, 2))
        am_slider_row("Min think (s)", "think_min",
                      from_=0, to=20, resolution=0.5, fmt="{:.1f}s",
                      default=am.think_min_ms / 1000.0, cmd=self._on_automove_think_min)
        am_slider_row("Max think (s)", "think_max",
                      from_=0, to=40, resolution=0.5, fmt="{:.1f}s",
                      default=am.think_max_ms / 1000.0, cmd=self._on_automove_think_max, alt=True)
        self.automove_complex_enabled_var = tk.BooleanVar(
            value=bool(getattr(am, "complex_enabled", True)))
        complex_check = toggle_check(am_sec, "Enable complex think (rare long thinks)",
                                     self.automove_complex_enabled_var,
                                     self._on_automove_complex_enabled, alt=True)
        HoverTip(complex_check, "When on, occasional complex positions get a\n"
                                "longer think from the range below. When off,\n"
                                "every move stays inside Min/Max think.")
        cpx_row = row(am_sec, "Complex think (s)")
        self.automove_complex_val = badge(
            cpx_row,
            text="%d–%ds" % (am.think_complex_min_ms / 1000,
                             am.think_complex_max_ms / 1000))
        self.automove_complex_val.pack(side=tk.RIGHT)
        self.automove_complex_slider = DualThumbRangeSlider(
            am_sec, from_=2, to=60,
            min_val=am.think_complex_min_ms / 1000.0,
            max_val=am.think_complex_max_ms / 1000.0,
            bg=CARD_BG, command=self._on_automove_think_complex_range)
        self.automove_complex_slider.pack(fill=tk.X, padx=12, pady=(0, 4))

        self.auto_queue_enabled_var = tk.BooleanVar(
            value=bool(getattr(self.overlay, "_auto_queue_enabled", False)))
        queue_check = toggle_check(
            am_sec,
            "Auto queue next game (after game over)",
            self.auto_queue_enabled_var,
            self._on_auto_queue_enabled, alt=True)
        HoverTip(queue_check,
                 "When a game finishes, Auto clicks Play again / the queue\n"
                 "button so the next game starts on its own. Waiting for a\n"
                 "match is safe — the flag risk is the moves, not queueing.")

        tk.Label(am_sec, text="\u25C8  Mouse event pacing", bg=CARD_BG, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(fill=tk.X, padx=12, pady=(6, 2))
        am_slider_row("Event delay min (ms)", "event_delay_min",
                      from_=10, to=500, resolution=5, fmt="{:.0f}ms",
                      default=am.event_delay_min_ms, cmd=self._on_automove_event_delay_min)
        am_slider_row("Event delay max (ms)", "event_delay_max",
                      from_=20, to=2000, resolution=10, fmt="{:.0f}ms",
                      default=am.event_delay_max_ms, cmd=self._on_automove_event_delay_max, alt=True)

        tk.Label(am_sec, text="\u25C8  Drag speed", bg=CARD_BG, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(fill=tk.X, padx=12, pady=(6, 2))
        am_slider_row("Path speed", "path_speed",
                      from_=0.2, to=6, resolution=0.1, fmt="{:.1f}x",
                      default=am.path_speed, cmd=self._on_automove_path_speed, alt=True)

        tk.Label(am_sec, text="\u25C8  Motion character", bg=CARD_BG, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(fill=tk.X, padx=12, pady=(6, 2))
        am_slider_row("Path curvature", "curvature",
                      from_=0, to=1, resolution=0.05, fmt="{:.2f}",
                      default=am.curvature,
                      cmd=lambda v: self._on_automove_ratio("curvature", v))
        am_slider_row("Micro-movements", "micro_movement",
                      from_=0, to=1, resolution=0.05, fmt="{:.2f}",
                      default=am.micro_movement,
                      cmd=lambda v: self._on_automove_ratio("micro_movement", v), alt=True)
        am_slider_row("Destination hesitation", "hesitation",
                      from_=0, to=1, resolution=0.05, fmt="{:.2f}",
                      default=am.hesitation,
                      cmd=lambda v: self._on_automove_ratio("hesitation", v))
        am_slider_row("Speed variation", "speed_variation",
                      from_=0, to=1, resolution=0.05, fmt="{:.2f}",
                      default=am.speed_variation,
                      cmd=lambda v: self._on_automove_ratio("speed_variation", v), alt=True)

        tk.Button(am_sec, text="Test cursor path (a1 \u2192 h8)",
                  command=self._on_automove_test_drag,
                  bg=ACCENT, fg=TEXT, activebackground=ACCENT,
                  activeforeground=TEXT, relief=tk.FLAT,
                  font=("Segoe UI", 9, "bold")).pack(fill=tk.X, padx=12, pady=(6, 2))
        tk.Button(am_sec, text="Calibrate board (a1 then h8)",
                  command=self._on_automove_calibrate,
                  bg=FIELD, fg=TEXT, activebackground=ACCENT2,
                  activeforeground=TEXT, relief=tk.FLAT,
                  font=("Segoe UI", 9, "bold")).pack(fill=tk.X, padx=12, pady=(0, 2))
        tk.Label(am_sec,
                 text="Moves the REAL mouse cursor.  Keep the chess board fully\n"
                      "visible (not covered by this window) and the browser at\n"
                      "100% page zoom so square positions line up.  Calibrate is\n"
                      "a no-extension fallback: put the mouse on a1, wait, h8, wait.",
                 bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9), justify="left",
                 anchor="w", padx=12, wraplength=360).pack(fill=tk.X, pady=(2, 6))

        # ═══════════════════════════════════════════════════════════════════
        # COACH
        # ═══════════════════════════════════════════════════════════════════
        coach_sec = self.create_section_card("coach", "Coach Mode & Accuracy")
        coach_enabled_var = tk.BooleanVar(value=getattr(self.overlay.coach_mode, "enabled", False))
        toggle_check(coach_sec, "Enable coach mode", coach_enabled_var, lambda: setattr(self.overlay.coach_mode, "enabled", coach_enabled_var.get()), alt=True)
        coach_tips_var = tk.BooleanVar(value=getattr(self.overlay.coach_mode, "show_tips", True))
        toggle_check(coach_sec, "Show coaching tips", coach_tips_var, lambda: setattr(self.overlay.coach_mode, "show_tips", coach_tips_var.get()), alt=True)
        coach_suggest_var = tk.BooleanVar(value=getattr(self.overlay.coach_mode, "suggest_better", True))
        toggle_check(coach_sec, "Suggest better move when wrong", coach_suggest_var, lambda: setattr(self.overlay.coach_mode, "suggest_better", coach_suggest_var.get()))
        coach_acc_var = tk.BooleanVar(value=getattr(self.overlay.coach_mode, "show_running_accuracy", True))
        toggle_check(coach_sec, "Show live accuracy %", coach_acc_var, lambda: setattr(self.overlay.coach_mode, "show_running_accuracy", coach_acc_var.get()), alt=True)
        self.coach_tts_var = tk.BooleanVar(value=getattr(self.overlay.coach_mode, "tts_enabled", False))
        toggle_check(coach_sec, "Speak grades aloud (pyttsx3)", self.coach_tts_var, self._on_coach_tts_change, alt=True)

        tk.Frame(coach_sec, bg=BORDER, height=1).pack(fill=tk.X, padx=12, pady=4)
        tk.Label(coach_sec, text="\u25C8  AI Coach (Groq)", bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9, "bold")).pack(fill=tk.X, padx=12, pady=(4, 2))
        tk.Label(coach_sec, text="Optional natural-language coach via Groq API.", bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9), wraplength=340, justify="left", anchor="w", padx=12, pady=4).pack(fill=tk.X)
        self.ai_toggle_btn = tk.Button(coach_sec, command=self._toggle_coach_ai, bg=FIELD, fg=TEXT, activebackground=ACCENT2, activeforeground=TEXT, relief=tk.FLAT, font=("Segoe UI", 9, "bold"), pady=6, cursor="hand2")
        self.ai_toggle_btn.pack(fill=tk.X, padx=12, pady=4)
        self._refresh_ai_toggle_btn()
        key_row = row(coach_sec, "Groq API key")
        self.coach_key_var = tk.StringVar(value=self.overlay.coach_groq_key)
        self.coach_key_entry = tk.Entry(key_row, textvariable=self.coach_key_var, width=16, show="\u2022", bg=FIELD, fg=TEXT, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER, font=("Consolas", 10))
        self.coach_key_entry.pack(side=tk.RIGHT, padx=4, pady=2)
        self.coach_key_entry.bind("<FocusOut>", self._on_coach_key_change)
        self.coach_key_entry.bind("<Return>", self._on_coach_key_change)
        model_row = row(coach_sec, "Model")
        self.coach_model_var = tk.StringVar(value=self.overlay.coach_model)
        self.coach_model_entry = tk.Entry(model_row, textvariable=self.coach_model_var, width=16, bg=FIELD, fg=TEXT, relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER, font=("Consolas", 10))
        self.coach_model_entry.pack(side=tk.RIGHT, padx=4, pady=2)
        self.coach_model_entry.bind("<FocusOut>", self._on_coach_model_change)
        self.coach_model_entry.bind("<Return>", self._on_coach_model_change)
        self.coach_test_btn = tk.Button(coach_sec, text="Test connection", command=self._test_coach_ai, bg=ACCENT, fg="#FFFFFF", activebackground=ACCENT2, activeforeground=TEXT, relief=tk.FLAT, font=("Segoe UI", 9, "bold"), pady=4, cursor="hand2")
        self.coach_test_btn.pack(fill=tk.X, padx=12, pady=(2, 6))

        # ═══════════════════════════════════════════════════════════════════
        # BOOKS
        # ═══════════════════════════════════════════════════════════════════
        book_sec = self.create_section_card("books", "Opening Books & Tablebases")
        self.book_cloud_var = tk.BooleanVar(value=getattr(self.overlay.book_manager, "cloud_enabled", True))
        toggle_check(book_sec, "Lichess cloud eval fallback", self.book_cloud_var, lambda: setattr(self.overlay.book_manager, "cloud_enabled", self.book_cloud_var.get()))
        book_source_row = row(book_sec, "Cloud source", alt=True)
        self.book_source_var = tk.StringVar(value=getattr(self.overlay.book_manager, "cloud_source", "masters"))
        book_src_opts = [("masters", "Masters DB"), ("lichess", "Lichess players")]
        InlineSelect(book_source_row, self.book_source_var, book_src_opts, lambda v: setattr(self.overlay.book_manager, "cloud_source", v), width=14, bg=CARD_BG).pack(side=tk.RIGHT, fill=tk.X, expand=True)
        book_lichess_row = row(book_sec, "Lichess API token")
        self.book_lichess_token_var = tk.StringVar(value=getattr(self.overlay.book_manager, "lichess_token", ""))
        lichess_entry = tk.Entry(book_lichess_row, textvariable=self.book_lichess_token_var, bg=FIELD, fg=TEXT, insertbackground=TEXT, font=("Segoe UI", 9), relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER, width=20)
        lichess_entry.pack(side=tk.RIGHT, fill=tk.X, expand=True)
        lichess_entry.bind("<FocusOut>", lambda e: setattr(self.overlay.book_manager, "lichess_token", self.book_lichess_token_var.get()))
        self.book_tb_var = tk.BooleanVar(value=getattr(self.overlay.book_manager, "tb_enabled", True))
        toggle_check(book_sec, "Enable endgame tablebases", self.book_tb_var, lambda: setattr(self.overlay.book_manager, "tb_enabled", self.book_tb_var.get()), alt=True)
        self.book_stage_var = tk.BooleanVar(value=getattr(self.overlay.book_manager, "stage_show", True))
        toggle_check(book_sec, "Show opening/middlegame/endgame", self.book_stage_var, lambda: setattr(self.overlay.book_manager, "stage_show", self.book_stage_var.get()))

        # ═══════════════════════════════════════════════════════════════════
        # PRIVACY
        # ═══════════════════════════════════════════════════════════════════
        prv_sec = self.create_section_card("privacy", "Streamproof & Privacy")
        self.stealth_mode_btn = tk.Button(prv_sec, text="Streamproof: OFF", command=self.overlay.toggle_stealth_mode, bg=FIELD, fg=TEXT, activebackground=ACCENT2, activeforeground=TEXT, relief=tk.FLAT, font=("Segoe UI", 9, "bold"), pady=7, cursor="hand2")
        self.stealth_mode_btn.pack(fill=tk.X, padx=12, pady=(8, 3))
        self.terminal_toggle_btn = tk.Button(prv_sec, command=self.overlay.toggle_logging_terminal, bg="#0f1117", fg=TEXT, activebackground=FIELD, activeforeground=TEXT, relief=tk.FLAT, font=("Segoe UI", 9), pady=6, cursor="hand2")
        self.terminal_toggle_btn.pack(fill=tk.X, padx=12, pady=(0, 8))
        self.update_terminal_toggle(self.overlay.logging_terminal_visible, self.overlay.logging_terminal_available)

        self.autoplay_status_label = tk.Label(prv_sec, text="Auto: OFF", bg=CARD_BG, fg="#8B7BAE", font=("Segoe UI", 9, "bold"), anchor="w", padx=12)
        self.autoplay_status_label.pack(fill=tk.X, pady=(0, 2))
        self.stealth_status_label = tk.Label(prv_sec, text="Streamproof: OFF", bg=CARD_BG, fg="#F4EEFF", font=("Segoe UI", 9, "bold"), anchor="w", padx=12)
        self.stealth_status_label.pack(fill=tk.X, pady=(0, 2))
        self.lock_status_label = tk.Label(prv_sec, text="Hotkeys: ON", bg=CARD_BG, fg=self.GREEN, font=("Segoe UI", 9, "bold"), anchor="w", padx=12)
        self.lock_status_label.pack(fill=tk.X, pady=(0, 8))

        # ═══════════════════════════════════════════════════════════════════
        # INTERNALS
        # ═══════════════════════════════════════════════════════════════════
        int_sec = self.create_section_card("internals", "Extension Internals")
        int_conn_row = row(int_sec, "Extension status")
        self.ext_status_label = tk.Label(int_conn_row, text="No extension", bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9), anchor="w")
        self.ext_status_label.pack(side=tk.RIGHT)
        int_privacy_row = row(int_sec, "Privacy controls", alt=True)
        int_panic_btn = tk.Button(int_privacy_row, text="PANIC", command=self.overlay.panic, bg="#dc2626", fg="white", activebackground="#b91c1c", activeforeground="white", relief=tk.FLAT, font=("Segoe UI", 9, "bold"), cursor="hand2")
        int_panic_btn.pack(side=tk.RIGHT, padx=(4, 0))
        int_hide_btn = tk.Button(int_privacy_row, text="Toggle Hide", command=lambda: self.overlay.set_privacy_hidden(not self.overlay.extension_privacy_hidden), bg=FIELD, fg=TEXT, activebackground=ACCENT2, activeforeground=TEXT, relief=tk.FLAT, font=("Segoe UI", 9), cursor="hand2")
        int_hide_btn.pack(side=tk.RIGHT)
        int_clock_row = row(int_sec, "Clock info")
        self.ext_clock_label = tk.Label(int_clock_row, text="--:-- / --:--", bg=CARD_BG, fg=MUTED, font=("Consolas", 10), anchor="w")
        self.ext_clock_label.pack(side=tk.RIGHT)
        int_refresh_btn = tk.Button(int_clock_row, text="Refresh", command=self.overlay.request_extension_clocks, bg=FIELD, fg=TEXT, activebackground=ACCENT2, activeforeground=TEXT, relief=tk.FLAT, font=("Segoe UI", 9), cursor="hand2")
        int_refresh_btn.pack(side=tk.RIGHT, padx=(4, 0))
        int_variant_row = row(int_sec, "Detected variant", alt=True)
        self.ext_variant_label = tk.Label(int_variant_row, text="standard", bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9), anchor="w")
        self.ext_variant_label.pack(side=tk.RIGHT)
        int_exploits_row = row(int_sec, "Site exploits")
        self.ext_exploits_label = tk.Label(int_exploits_row, text="None detected", bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9), anchor="w", wraplength=280)
        self.ext_exploits_label.pack(side=tk.RIGHT)

        # ═══════════════════════════════════════════════════════════════════
        # LOGS
        # ═══════════════════════════════════════════════════════════════════
        logs_sec = self.create_section_card("logs", "Analysis Logs & Game Accuracy")
        timing_header = tk.Frame(logs_sec, bg=CARD_BG)
        timing_header.pack(fill=tk.X, padx=18, pady=(12, 6))
        tk.Label(timing_header, text='TIME TO TARGET DEPTH', bg=CARD_BG, fg=TEXT,
                 font=('Segoe UI', 10, 'bold')).pack(side=tk.LEFT)
        self.depth_timing_button = tk.Button(timing_header, text='Start timing',
                 command=self.overlay.toggle_depth_timing, bg=ACCENT, fg='white',
                 activebackground=ACCENT2, activeforeground='white', relief=tk.FLAT,
                 font=('Segoe UI', 10, 'bold'), padx=12, pady=6, cursor='hand2')
        self.depth_timing_button.pack(side=tk.RIGHT)
        tk.Label(logs_sec, text='Measures each primary-engine search from dispatch until full target-depth suggestions are ready. Resets for a new match; timing stays enabled. Starting mid-search measures the next search.',
                 bg=CARD_BG, fg=MUTED, font=('Segoe UI', 9), wraplength=650,
                 justify='left', anchor='w').pack(fill=tk.X, padx=18, pady=(0, 8))
        self.depth_timing_summary = tk.Label(logs_sec, text='Timing off', bg=FIELD, fg=TEXT,
                 font=('Segoe UI', 10), justify='left', anchor='w', wraplength=650, padx=10, pady=8)
        self.depth_timing_summary.pack(fill=tk.X, padx=18, pady=(0, 8))
        self.depth_timing_log = tk.Text(logs_sec, height=4, bg=FIELD, fg=TEXT,
                 font=('Consolas', 9), wrap=tk.WORD, relief=tk.FLAT, padx=8, pady=6,
                 state=tk.DISABLED)
        self.depth_timing_log.pack(fill=tk.X, padx=18, pady=(0, 14))
        log_hdr = tk.Frame(logs_sec, bg=CARD_BG)
        log_hdr.pack(fill=tk.X, padx=12, pady=(6, 2))
        tk.Label(log_hdr, text="ENGINE ANALYSIS LOG", bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9, "bold"), anchor="w").pack(side=tk.LEFT)
        self.engine_line_text = tk.Text(logs_sec, height=8, bg=CARD_BG, fg=TEXT, insertbackground=TEXT, wrap=tk.WORD, font=("Consolas", 10), relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER, selectbackground=ACCENT, selectforeground=TEXT, padx=8, pady=6)
        self.engine_line_text.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))
        self.engine_line_text.insert(tk.END, "Engine Line")
        self.engine_line_text.config(state=tk.DISABLED)

        acc_hdr = tk.Frame(logs_sec, bg=CARD_BG)
        acc_hdr.pack(fill=tk.X, padx=12, pady=(0, 2))
        tk.Label(acc_hdr, text="GAME ACCURACY ANALYSIS", bg=CARD_BG, fg=MUTED, font=("Segoe UI", 9, "bold"), anchor="w").pack(side=tk.LEFT)
        acc_wrap = tk.Frame(logs_sec, bg=CARD_BG)
        acc_wrap.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))
        self.accuracy_scroll = tk.Scrollbar(acc_wrap, orient="vertical", relief=tk.FLAT, activebackground=BORDER, bg=BORDER, troughcolor=CARD_BG, highlightthickness=0)
        self.accuracy_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.accuracy_text = tk.Text(acc_wrap, height=6, bg=CARD_BG, fg=TEXT, insertbackground=TEXT, wrap=tk.WORD, font=("Consolas", 10), relief=tk.FLAT, highlightthickness=1, highlightbackground=BORDER, selectbackground=ACCENT, selectforeground=TEXT, padx=8, pady=6, yscrollcommand=self.accuracy_scroll.set, state=tk.DISABLED)
        self.accuracy_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.accuracy_scroll.config(command=self.accuracy_text.yview)

        def _acc_mw(e):
            try:
                self.accuracy_text.yview_scroll(int(-1 * (e.delta / 120)), "units")
            except Exception:
                pass
            return "break"
        self.accuracy_text.bind("<MouseWheel>", _acc_mw)
        self.accuracy_text.bind("<Enter>", lambda e: self.accuracy_text.bind("<MouseWheel>", _acc_mw))
        self.accuracy_text.config(state=tk.NORMAL)
        self.accuracy_text.delete("1.0", tk.END)
        self.accuracy_text.insert(tk.END, "Waiting for moves...\n")
        self.accuracy_text.config(state=tk.DISABLED)
        self.accuracy_text.see(tk.END)

        self._bind_mousewheel_recursive(self.content_inner)

        self._show_active_tab_sections()

