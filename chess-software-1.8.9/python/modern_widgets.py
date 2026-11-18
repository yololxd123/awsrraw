"""Small native Tk controls for the desktop panel; no extra dependencies."""
import tkinter as tk


def rounded(canvas, x, y, w, h, radius=14, **kwargs):
    r = min(radius, w / 2, h / 2)
    return canvas.create_polygon(x+r,y, x+w-r,y, x+w,y, x+w,y+r,
        x+w,y+h-r, x+w,y+h, x+w-r,y+h, x+r,y+h, x,y+h,
        x,y+h-r, x,y+r, x,y, smooth=True, splinesteps=24, **kwargs)


class RoundedSurface(tk.Canvas):
    def __init__(self, parent, bg='#18191b', surface='#202226', border='#2b2e33', padding=14):
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0, height=80, width=100)
        self.surface, self.border, self.padding = surface, border, padding
        self.body = tk.Frame(self, bg=surface)
        self.window = self.create_window(padding, padding, window=self.body, anchor='nw')
        self.bind('<Configure>', self._layout)
        self.body.bind('<Configure>', self._height)

    def _height(self, _event=None):
        height = self.body.winfo_reqheight() + 2*self.padding
        if int(self.cget('height')) != height:
            self.configure(height=height)

    def _layout(self, event):
        self.itemconfigure(self.window, width=max(1, event.width-2*self.padding))
        self.delete('surface')
        rounded(self, 1, 1, max(1,event.width-2), max(1,event.height-2),
                fill=self.surface, outline=self.border, tags='surface')
        self.tag_lower('surface')


class NavItem(tk.Canvas):
    def __init__(self, parent, text, command):
        super().__init__(parent, width=176, height=40, bg='#151618',
                         highlightthickness=0, takefocus=True, cursor='hand2')
        self.text, self.command, self.selected, self.hover = text, command, False, False
        self.bind('<Configure>', lambda e:self.redraw())
        self.bind('<Enter>', lambda e:self._hover(True))
        self.bind('<Leave>', lambda e:self._hover(False))
        self.bind('<ButtonRelease-1>', lambda e:self.invoke())
        self.bind('<Return>', lambda e:self.invoke())
        self.bind('<space>', lambda e:self.invoke())
        self.bind('<FocusIn>', lambda e:self.redraw())
        self.bind('<FocusOut>', lambda e:self.redraw())

    def invoke(self):
        self.command()

    def _hover(self, value):
        self.hover = value
        self.redraw()

    def set_selected(self, selected):
        self.selected = selected
        self.redraw()

    def redraw(self):
        self.delete('all')
        fill = '#202e3c' if self.selected else ('#222428' if self.hover else '#151618')
        rounded(self, 1, 1, max(2,self.winfo_width()-2), 38, 10, fill=fill,
                outline='#389cf7' if self.focus_get() is self else fill)
        self.create_oval(14,17,20,23,fill='#42a5ff' if self.selected else '#737b87',outline='')
        self.create_text(32,20,text=self.text,anchor='w',fill='#eaf3ff' if self.selected else '#aab1bc',font=('Segoe UI',10))


class ToggleSwitch(tk.Frame):
    def __init__(self, parent, text, variable, command, bg='#202226'):
        super().__init__(parent,bg=bg,takefocus=True,highlightthickness=0,cursor='hand2')
        self.variable, self.command = variable, command
        self.label=tk.Label(self,text=text,bg=bg,fg='#e7eaf0',font=('Segoe UI',10),anchor='w')
        self.track=tk.Canvas(self,width=42,height=24,bg=bg,highlightthickness=0)
        self.track.pack(side=tk.RIGHT,padx=12)
        self.label.pack(side=tk.LEFT,fill=tk.X,expand=True,padx=(12,10),pady=12)
        self.label.bind('<Configure>',lambda e:self.label.configure(wraplength=max(60,e.width-8)))
        for widget in (self,self.label,self.track):
            widget.bind('<ButtonRelease-1>',lambda e:self.invoke())
        self.bind('<space>',lambda e:self.invoke())
        self.bind('<Return>',lambda e:self.invoke())
        self.bind('<FocusIn>',lambda e:self.label.configure(fg='#65b8ff'))
        self.bind('<FocusOut>',lambda e:self.label.configure(fg='#e7eaf0'))
        self._trace=variable.trace_add('write',lambda *args:self.redraw())
        self.bind('<Destroy>',self._cleanup)
        self.redraw()

    def _cleanup(self,event):
        if event.widget is self:
            self.variable.trace_remove('write',self._trace)

    def invoke(self):
        self.focus_set()
        self.variable.set(not self.variable.get())
        if self.command:self.command()

    def redraw(self):
        on=bool(self.variable.get())
        self.track.delete('all')
        rounded(self.track,1,2,40,20,10,fill='#248af0' if on else '#42464e',outline='')
        x=23 if on else 3
        self.track.create_oval(x,4,x+16,20,fill='#ffffff' if on else '#aab1bb',outline='')
