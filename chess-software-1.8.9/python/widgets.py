import tkinter as tk

class InlineSelect(tk.Frame):
    """An in-window selector whose choices remain part of the protected panel."""
    def __init__(self, parent, variable, options, command=None, width=22,
                 bg="#202226", button_bg="#2b2e34"):
        super().__init__(parent, bg=bg)
        self.variable, self.command = variable, command
        self.bg, self.button_bg, self.width = bg, button_bg, width
        self.options = []
        self.button = tk.Button(self, textvariable=variable, command=self.toggle,
                                anchor="w", padx=12, pady=7, font=("Segoe UI", 10), bg=button_bg, fg="#e7eaf0",
                                activebackground="#176bb6", activeforeground="white",
                                relief=tk.FLAT, borderwidth=0, width=width)
        self.button.pack(fill=tk.X)
        self.choice_frame = tk.Frame(self, bg="#191b1f", highlightthickness=1,
                                     highlightbackground="#39424f")
        self.open = False
        self.update_options(options)

    def toggle(self):
        if self.open:
            self.choice_frame.pack_forget()
        else:
            self.choice_frame.pack(fill=tk.X, pady=(2, 0))
        self.open = not self.open

    def close(self):
        if self.open:
            self.choice_frame.pack_forget(); self.open = False

    def choose(self, value, label):
        self.variable.set(label)
        self.close()
        if self.command:
            self.command(value)

    def update_options(self, options, selected_value=None):
        for child in self.choice_frame.winfo_children():
            child.destroy()
        self.options = list(options)  # (value, label)
        for value, label in self.options:
            tk.Button(self.choice_frame, text=label, anchor="w", padx=12, pady=6, font=("Segoe UI", 10),
                      command=lambda v=value, l=label: self.choose(v, l),
                      bg="#202226", fg="#e7eaf0", activebackground="#176bb6",
                      activeforeground="white", relief=tk.FLAT, borderwidth=0
                      ).pack(fill=tk.X, pady=1)
        if selected_value is not None:
            for value, label in self.options:
                if str(value) == str(selected_value):
                    self.variable.set(label); break


class DragReorderList(tk.Frame):
    """A reorderable list of items with drag-and-drop support.

    Each item shows a priority number and label. Dragging an item reorders
    the list and fires a callback with the new order.
    """
    def __init__(self, parent, items=None, on_reorder=None, bg="#18191b",
                 item_bg="#202226", accent="#218cf4", fg="#e7eaf0",
                 muted="#a8b0bc", height=5, **kwargs):
        super().__init__(parent, bg=bg, **kwargs)
        self.on_reorder = on_reorder
        self.accent = accent
        self.fg = fg
        self.muted = muted
        self.item_bg = item_bg
        self._items = []          # [(id, label), ...]
        self._labels = []         # tk.Label references
        self._drag_start_y = 0
        self._drag_idx = None
        self._drag_ghost = None

        self._canvas = tk.Canvas(self, bg=bg, highlightthickness=0, height=height * 24,
                                 cursor="hand2")
        self._canvas.pack(fill=tk.BOTH, expand=True)
        self._canvas.bind("<ButtonPress-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)

        if items:
            self.set_items(items)

    def set_items(self, items):
        """items = [(id, label), ...]"""
        self._items = list(items)
        self._redraw()

    def get_order(self):
        """Return current item ids in order."""
        return [item_id for item_id, _ in self._items]

    def _redraw(self):
        self._canvas.delete("all")
        self._labels = []
        for i, (item_id, label) in enumerate(self._items):
            y = i * 24
            # priority number
            self._canvas.create_text(
                14, y + 12, text=f"{i + 1}.", fill=self.muted,
                font=("Segoe UI", 8, "bold"), anchor="center", tags=f"item_{i}"
            )
            # engine name
            self._canvas.create_text(
                32, y + 12, text=label, fill=self.fg,
                font=("Segoe UI", 9), anchor="w", tags=f"item_{i}"
            )
            # row background
            self._canvas.create_rectangle(
                0, y, self._canvas.winfo_width() or 300, y + 23,
                fill="", outline="", tags=f"bg_{i}"
            )

    def _on_press(self, event):
        idx = event.y // 24
        if 0 <= idx < len(self._items):
            self._drag_idx = idx
            self._drag_start_y = event.y
            # highlight
            self._canvas.delete("highlight")
            y = idx * 24
            self._canvas.create_rectangle(
                0, y, self._canvas.winfo_width() or 300, y + 23,
                fill=self.accent, outline="", stipple="gray25",
                tags="highlight"
            )

    def _on_drag(self, event):
        if self._drag_idx is None:
            return
        dy = event.y - self._drag_start_y
        if abs(dy) < 4:
            return
        new_idx = max(0, min(len(self._items) - 1, (self._drag_start_y + dy) // 24))
        if new_idx != self._drag_idx:
            # swap
            item = self._items.pop(self._drag_idx)
            self._items.insert(new_idx, item)
            self._drag_idx = new_idx
            self._drag_start_y = event.y
            self._redraw()
            # re-highlight
            self._canvas.delete("highlight")
            y = new_idx * 24
            self._canvas.create_rectangle(
                0, y, self._canvas.winfo_width() or 300, y + 23,
                fill=self.accent, outline="", stipple="gray25",
                tags="highlight"
            )

    def _on_release(self, event):
        if self._drag_idx is None:
            return
        self._drag_idx = None
        self._canvas.delete("highlight")
        if self.on_reorder:
            try:
                self.on_reorder(self.get_order())
            except Exception:
                pass


class CircleSlider(tk.Canvas):
    """Modern single-value slider with a sleek track and circular handle."""

    def __init__(self, parent, from_=1, to=16, value=4, resolution=1, command=None, bg="#202226", **kwargs):
        super().__init__(parent, bg=bg, highlightthickness=0, height=28, **kwargs)
        self.from_ = from_
        self.to = to
        self.value = value
        self.resolution = resolution
        self.command = command
        self.thumb_radius = 7

        self.bind("<Configure>", self._draw)
        self.bind("<Button-1>", self._on_click)
        self.bind("<B1-Motion>", self._on_drag)

    def _val_to_x(self, val):
        w = self.winfo_width()
        padding = 12
        track_width = max(10, w - 2 * padding)
        ratio = (val - self.from_) / float(self.to - self.from_) if self.to > self.from_ else 0
        return padding + ratio * track_width

    def _x_to_val(self, x):
        w = self.winfo_width()
        padding = 12
        track_width = max(10, w - 2 * padding)
        ratio = max(0.0, min(1.0, (x - padding) / float(track_width)))
        val = self.from_ + ratio * (self.to - self.from_)
        if self.resolution >= 1:
            return round(val / self.resolution) * self.resolution
        return val

    def _draw(self, _event=None):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 24:
            return

        cy = h // 2
        padding = 12
        x = self._val_to_x(self.value)

        # Track background line
        self.create_line(padding, cy, w - padding, cy, fill="#343b45", width=5, capstyle=tk.ROUND)
        # Active track fill line
        self.create_line(padding, cy, x, cy, fill="#218cf4", width=5, capstyle=tk.ROUND)
        # Circular thumb handle
        r = self.thumb_radius
        self.create_oval(x - r, cy - r, x + r, cy + r, fill="#86c5ff", outline="#FFFFFF", width=2)

    def _on_click(self, event):
        self._on_drag(event)

    def _on_drag(self, event):
        val = self._x_to_val(event.x)
        if val != self.value:
            self.value = val
            self._draw()
            if self.command:
                self.command(self.value)

    def set(self, val):
        self.value = val
        self._draw()


class DualThumbRangeSlider(tk.Canvas):
    """Stake-style dual-thumb range slider with circular handles on a single track bar."""
    def __init__(self, parent, from_=35, to=100, min_val=70, max_val=80, command=None, bg="#202226", **kwargs):
        super().__init__(parent, bg=bg, highlightthickness=0, height=32, **kwargs)
        self.from_ = from_
        self.to = to
        self.min_val = min_val
        self.max_val = max_val
        self.command = command
        self.active_thumb = None
        self.thumb_radius = 8

        self.bind("<Configure>", self._draw)
        self.bind("<Button-1>", self._on_click)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _val_to_x(self, val):
        w = self.winfo_width()
        padding = 14
        track_width = max(10, w - 2 * padding)
        ratio = (val - self.from_) / float(self.to - self.from_) if self.to > self.from_ else 0
        return padding + ratio * track_width

    def _x_to_val(self, x):
        w = self.winfo_width()
        padding = 14
        track_width = max(10, w - 2 * padding)
        ratio = max(0.0, min(1.0, (x - padding) / float(track_width)))
        val = self.from_ + ratio * (self.to - self.from_)
        return round(val)

    def _draw(self, _event=None):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 30:
            return

        cy = h // 2
        padding = 14
        min_x = self._val_to_x(self.min_val)
        max_x = self._val_to_x(self.max_val)

        # Track background line
        self.create_line(padding, cy, w - padding, cy, fill="#343b45", width=6, capstyle=tk.ROUND)
        # Active highlight range bar between min_x and max_x
        self.create_line(min_x, cy, max_x, cy, fill="#218cf4", width=6, capstyle=tk.ROUND)

        # Left circular handle (Min)
        r = self.thumb_radius
        self.create_oval(min_x - r, cy - r, min_x + r, cy + r, fill="#86c5ff", outline="#FFFFFF", width=2, tags="min_thumb")
        # Right circular handle (Max)
        self.create_oval(max_x - r, cy - r, max_x + r, cy + r, fill="#218cf4", outline="#FFFFFF", width=2, tags="max_thumb")

    def _on_click(self, event):
        min_x = self._val_to_x(self.min_val)
        max_x = self._val_to_x(self.max_val)
        dist_min = abs(event.x - min_x)
        dist_max = abs(event.x - max_x)

        if dist_min <= dist_max:
            self.active_thumb = "min"
        else:
            self.active_thumb = "max"
        self._on_drag(event)

    def _on_drag(self, event):
        val = self._x_to_val(event.x)
        if self.active_thumb == "min":
            self.min_val = min(val, self.max_val)
        elif self.active_thumb == "max":
            self.max_val = max(val, self.min_val)

        self._draw()
        if self.command:
            self.command(self.min_val, self.max_val)

    def _on_release(self, _event):
        self.active_thumb = None

    def set_range(self, min_val, max_val):
        self.min_val = min_val
        self.max_val = max_val
        self._draw()


class HoverTip:
    """Small, dependency-free help bubble for control widgets."""
    def __init__(self, widget, text):
        self.widget, self.text, self.window = widget, text, None
        widget.bind("<Enter>", self.show, add="+")
        widget.bind("<Leave>", self.hide, add="+")
    def show(self, _event=None):
        if self.window: return
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.attributes("-topmost", True)
        x, y = self.widget.winfo_rootx() + self.widget.winfo_width() + 8, self.widget.winfo_rooty()
        self.window.geometry(f"+{x}+{y}")
        tk.Label(self.window, text=self.text, justify=tk.LEFT, bg="#fffbd5", fg="#222",
                 relief=tk.SOLID, borderwidth=1, font=("Arial", 8), padx=6, pady=4).pack()
    def hide(self, _event=None):
        if self.window:
            self.window.destroy(); self.window = None

# ============================================================================
# EXTERNAL EVALUATION WINDOW (BETTERMINT STYLE)
# ============================================================================
