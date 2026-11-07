"""BMO theme: the Adventure Time look for every window except BMO's own panel.

Keep this file beside adventure_time_launcher.py.

What it gives the launcher:
  * T                  the palette (BMO teal frame, parchment paper, slate-blue buttons, red titles)
  * install(root)      styles ttk (tabs, scrollbars, tables...) and Tk popup menus, and automatically
                       re-skins any other pop-up window (so the TOOLS windows match without being edited)
  * frame_window(win)  teal BMO frame + parchment page + scroll banner title
  * Pill, Toggle, Slider, ScrollFrame, Banner, HeartBar, toggle_row, radio_group, heading ...

All drawing is plain tkinter (no images, no extra packages).
"""
import random
import tkinter as tk
from tkinter import ttk

T = {"body": "#55B8B0", "dark": "#287E80", "light": "#9CE0D5",
     "paper": "#FCE3B3", "paper_dk": "#F5C98A", "paper_lt": "#FFF3D6", "edge": "#E8A458",
     "brown": "#6B4528", "title": "#D4202A", "star": "#F2A93B",
     "slate": "#5E93B3", "slate_dk": "#3E6F94", "slate_lt": "#A9D3E6",
     "grey": "#BEBEBE", "grey_dk": "#8D8D8D", "white": "#FFFFFF",
     "green": "#4FA066", "red": "#C8404E", "amber": "#9A6B12"}
FONT = "Comic Sans MS"

QUOTES = ("Algebraic!", "Mathematical!", "Adventure Time!", "Oh my glob!",
          "Who wants to play video games?", "Let's go on an adventure!", "Rad!", "Whoa, that's so lumpin' good!")


_PAL = {}


def palette(C):
    """The launcher's colour dict with the pale-green 'screen' and teal 'ink' swapped for parchment and brown,
    so windows that read C["screen"] / C["ink"] pick up the Adventure Time paper automatically."""
    key = id(C)
    if key not in _PAL:
        p = dict(C)
        p.update(screen=T["paper"], ink=T["brown"])
        _PAL[key] = p
    return _PAL[key]


def quote():
    return random.choice(QUOTES)


def _mix(c, other="#FFFFFF", amt=.2):
    """Blend colour c toward other by amt (0-1)."""
    try:
        a = [int(c[i:i + 2], 16) for i in (1, 3, 5)]
        b = [int(other[i:i + 2], 16) for i in (1, 3, 5)]
        return "#%02X%02X%02X" % tuple(int(x + (y - x) * amt) for x, y in zip(a, b))
    except Exception:
        return c


def _bg(parent, fallback=None):
    try:
        return parent.cget("bg")
    except Exception:
        return fallback or T["paper"]


def _round(c, x1, y1, x2, y2, r, **kw):
    r = max(1, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    p = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
         x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(p, smooth=True, splinesteps=10, **kw)


# ---------------------------------------------------------------- buttons
class Pill(tk.Canvas):
    """The blue beveled button from the Adventure Time menu. kinds: blue, green, red, cream, amber."""
    KINDS = {"blue": ("#5E93B3", "#3E6F94", "#A9D3E6"),
             "green": ("#58A96B", "#2F7544", "#B7E8C0"),
             "red": ("#D2606A", "#A03040", "#F6B5BB"),
             "amber": ("#E2A64B", "#A8741F", "#FFE3A6"),
             "cream": ("#FBE3B4", "#D9A25A", "#FFF6DF")}

    def __init__(self, parent, text, command=None, kind="blue", width=None, height=30, size=9, bg=None):
        w = width or max(64, int(len(text) * size * 0.95) + 30)
        super().__init__(parent, width=w, height=height, bg=bg or _bg(parent), highlightthickness=0, bd=0,
                         cursor="hand2")
        self.text, self.command, self.kind, self.size = text, command, kind, size
        self.w, self.h = w, height
        self.hover = self.pressed = False
        self.draw()
        self.bind("<Enter>", lambda e: self._set(hover=True))
        self.bind("<Leave>", lambda e: self._set(hover=False, pressed=False))
        self.bind("<ButtonPress-1>", lambda e: self._set(pressed=True))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Configure>", self._resize)

    def _resize(self, e):
        if e.width > 20 and e.width != self.w:     # pack(fill="x") stretches the button: redraw to fit
            self.w = e.width
            self._set()

    def _set(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        try:
            self.draw()
        except tk.TclError:
            pass

    def _release(self, e):
        was = self.pressed
        self._set(pressed=False)
        if was and 0 <= e.x <= self.w and 0 <= e.y <= self.h and self.command:
            self.command()

    def configure_text(self, text):
        self.text = text
        self.draw()

    def draw(self):
        c, w, h = self, self.w, self.h
        c.delete("all")
        fill, edge, light = self.KINDS.get(self.kind, self.KINDS["blue"])
        if self.hover:
            fill, light = _mix(fill, "#FFFFFF", .16), _mix(light, "#FFFFFF", .3)
        dy = 2 if self.pressed else 0
        ink = T["brown"] if self.kind == "cream" else T["white"]
        _round(c, 1, 3, w - 1, h - 1, 9, fill=edge, outline=edge)
        _round(c, 1, 1 + dy, w - 1, h - 3 + dy, 9, fill=light, outline=light)
        _round(c, 4, 4 + dy, w - 4, h - 6 + dy, 6, fill=fill, outline=edge)
        c.create_line(11, 8 + dy, w - 11, 8 + dy, fill=_mix(fill, "#FFFFFF", .45))
        mid = (4 + h - 6) / 2 + dy
        c.create_text(w / 2 + 1, mid + 2, text=self.text, fill=edge, font=(FONT, self.size, "bold"))
        c.create_text(w / 2, mid + 1, text=self.text, fill=ink, font=(FONT, self.size, "bold"))


class Toggle(tk.Canvas):
    """OFF / ON pill switch (a Tk BooleanVar drives it)."""
    W, H = 64, 28

    def __init__(self, parent, variable, command=None, bg=None):
        super().__init__(parent, width=self.W, height=self.H, bg=bg or _bg(parent), highlightthickness=0, bd=0,
                         cursor="hand2")
        self.var, self.command = variable, command
        self.draw()
        self.bind("<ButtonRelease-1>", lambda e: self.toggle())

    def toggle(self):
        self.var.set(not bool(self.var.get()))
        try:
            self.draw()
        except tk.TclError:
            pass
        if self.command:
            self.command()

    def draw(self):
        c, W, H = self, self.W, self.H
        c.delete("all")
        on = bool(self.var.get())
        _round(c, 1, 3, W - 1, H - 3, 11, fill=T["star"] if on else T["grey"],
               outline=T["edge"] if on else T["grey_dk"], width=2)
        d = H - 6
        kx = W - 3 - d if on else 3
        c.create_text(14 if on else W - 17, H / 2, text="ON" if on else "OFF",
                      fill=T["white"] if on else "#6E6E6E", font=(FONT, 8, "bold"))
        c.create_oval(kx, 3, kx + d, 3 + d, fill=T["slate"], outline=T["slate_lt"], width=2)
        c.create_oval(kx + 5, 6, kx + 11, 11, fill=T["slate_lt"], outline="")


class Slider(tk.Canvas):
    """Orange-fill slider with a round blue knob and the number on the right."""

    def __init__(self, parent, variable, lo, hi, step, command=None, width=400, height=30, bg=None):
        super().__init__(parent, width=width, height=height, bg=bg or _bg(parent), highlightthickness=0, bd=0,
                         cursor="hand2")
        self.var, self.lo, self.hi, self.step, self.command = variable, lo, hi, step, command
        self.w, self.h = width, height
        s = str(step)
        self.dec = len(s.split(".")[1]) if "." in s and not s.endswith(".0") else 0
        self.x0, self.x1 = 14, width - 62
        self.draw()
        self.bind("<ButtonPress-1>", self._mouse)
        self.bind("<B1-Motion>", self._mouse)

    def set_width(self, width):
        width = int(width)
        if width == self.w:
            return
        self.w, self.x1 = width, width - 62
        self.configure(width=width)
        self.draw()

    def _value(self):
        try:
            return float(self.var.get())
        except (tk.TclError, ValueError):
            return self.lo

    def _mouse(self, e):
        frac = min(1.0, max(0.0, (e.x - self.x0) / float(self.x1 - self.x0)))
        v = self.lo + round(frac * (self.hi - self.lo) / self.step) * self.step
        v = round(min(self.hi, max(self.lo, v)), 4)
        if abs(v - self._value()) > 1e-9:
            self.var.set(v)
            self.draw()
            if self.command:
                self.command(v)

    def draw(self):
        c = self
        c.delete("all")
        v = min(self.hi, max(self.lo, self._value()))
        frac = (v - self.lo) / float(self.hi - self.lo) if self.hi > self.lo else 0
        cy, kx = self.h / 2, self.x0 + frac * (self.x1 - self.x0)
        _round(c, self.x0, cy - 5, self.x1, cy + 5, 5, fill=T["grey"], outline=T["grey_dk"])
        if kx - self.x0 > 4:
            _round(c, self.x0, cy - 5, kx, cy + 5, 5, fill="#F5B35E", outline=T["edge"])
        c.create_oval(kx - 10, cy - 10, kx + 10, cy + 10, fill=T["slate"], outline=T["slate_lt"], width=2)
        c.create_oval(kx - 5, cy - 7, kx + 1, cy - 2, fill=T["slate_lt"], outline="")
        txt = f"{v:.{self.dec}f}" if self.dec else str(int(round(v)))
        c.create_text(self.w - 30, cy, text=txt, fill=T["brown"], font=(FONT, 9, "bold"))


# ---------------------------------------------------------------- layout pieces
class Banner(tk.Canvas):
    """The parchment scroll with a red title (like MAIN MENU in the reference)."""

    def __init__(self, parent, text, bg=None, height=58, size=17):
        super().__init__(parent, height=height, bg=bg or _bg(parent), highlightthickness=0, bd=0)
        self.text, self.size, self.h = text, size, height
        self.bind("<Configure>", lambda e: self.draw())

    def draw(self):
        c, h = self, self.h
        c.delete("all")
        w = c.winfo_width()
        if w < 60:
            return
        _round(c, 22, 8, w - 22, h - 8, 10, fill=T["paper_lt"], outline=T["edge"], width=2)
        for x in (4, w - 30):
            c.create_oval(x, 12, x + 26, h - 4, fill=T["paper_dk"], outline=T["edge"], width=2)
            c.create_oval(x + 7, 19, x + 19, h - 11, outline=T["edge"])
        for x in (30, w - 30):
            c.create_text(x if x < w / 2 else x, h / 2 - 2, text="\u2605", fill=T["star"], font=("Arial", 9))
        c.create_text(w / 2 + 1, h / 2 - 1, text=self.text, fill="#F0A860", font=(FONT, self.size, "bold"))
        c.create_text(w / 2, h / 2 - 2, text=self.text, fill=T["title"], font=(FONT, self.size, "bold"))


def frame_window(win, title):
    """Give a Toplevel the BMO frame: teal body, parchment page, scroll banner. Returns the page to build into."""
    win.configure(bg=T["dark"])
    outer = tk.Frame(win, bg=T["body"], highlightbackground=T["light"], highlightthickness=2)
    outer.pack(fill="both", expand=True, padx=5, pady=5)
    page = tk.Frame(outer, bg=T["paper"], highlightbackground=T["edge"], highlightthickness=2)
    page.pack(fill="both", expand=True, padx=7, pady=7)
    Banner(page, title, bg=T["paper"]).pack(fill="x", padx=6, pady=(6, 2))
    return page


def _scroll_parent(widget):
    p = widget.master
    while p is not None and not isinstance(p, ScrollFrame):
        p = p.master
    return p


def autowrap(label, pad):
    """If `label` is inside a ScrollFrame, keep its wraplength at (frame width - pad) so the text never runs past
    the right edge. `pad` = the horizontal space around the label that is not its own."""
    sf = _scroll_parent(label)
    if sf is not None:
        sf.wrap_labels.append((label, pad))
        sf.rewrap()


def autosize(widget, pad):
    """Same for a widget with set_width() (the sliders)."""
    sf = _scroll_parent(widget)
    if sf is not None:
        sf.sized_widgets.append((widget, pad))
        sf.rewrap()


def heading(parent, text, wrap=380, bg=None):
    bg = bg or _bg(parent)
    f = tk.Frame(parent, bg=bg)
    tk.Label(f, text="\u2605", bg=bg, fg=T["star"], font=("Arial", 10)).pack(side="left", anchor="n", pady=(2, 0))
    lb = tk.Label(f, text=text, bg=bg, fg=T["title"], font=(FONT, 9, "bold"), wraplength=wrap,
                  justify="left")
    lb.pack(side="left", padx=(4, 0))
    f.pack(anchor="w", padx=10, pady=(10, 3))
    autowrap(lb, 60)
    return f


def label(parent, text, wrap=380, bold=False, fg=None, size=9, **pack):
    bg = _bg(parent)
    lb = tk.Label(parent, text=text, bg=bg, fg=fg or T["brown"], font=(FONT, size, "bold" if bold else "normal"),
                  wraplength=wrap, justify="left", anchor="w")
    lb.pack(**({"anchor": "w", "padx": 12} | pack))
    autowrap(lb, 40)
    return lb


def toggle_row(parent, text, var, command=None, wrap=330):
    bg = _bg(parent)
    f = tk.Frame(parent, bg=bg)
    t = Toggle(f, var, command, bg=bg)
    t.pack(side="left", padx=(0, 8))
    lb = tk.Label(f, text=text, bg=bg, fg=T["brown"], font=(FONT, 9), wraplength=wrap, justify="left",
                  anchor="w", cursor="hand2")
    lb.pack(side="left", fill="x", expand=True)
    lb.bind("<ButtonRelease-1>", lambda e: t.toggle())
    f.pack(anchor="w", padx=12, pady=3, fill="x")
    autowrap(lb, Toggle.W + 8 + 24 + 16)      # the switch, its gap, the row's side margins, and a little spare
    return f


def radio_group(parent, var, options, command=None, wrap=330):
    """Round dot choices. options = [(value, label), ...]."""
    bg = _bg(parent)
    dots = []

    def paint():
        for value, cv in dots:
            try:
                cv.delete("all")
                cv.create_oval(2, 2, 18, 18, fill=T["paper_lt"], outline=T["slate_dk"], width=2)
                if var.get() == value:
                    cv.create_oval(6, 6, 14, 14, fill=T["slate"], outline=T["slate_lt"])
            except tk.TclError:
                pass

    def pick(value):
        var.set(value)
        paint()
        if command:
            command()

    for value, text in options:
        row = tk.Frame(parent, bg=bg)
        cv = tk.Canvas(row, width=20, height=20, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        cv.pack(side="left", padx=(0, 8))
        lb = tk.Label(row, text=text, bg=bg, fg=T["brown"], font=(FONT, 9), wraplength=wrap, justify="left",
                      anchor="w", cursor="hand2")
        lb.pack(side="left", fill="x", expand=True)
        for wdg in (cv, lb):
            wdg.bind("<ButtonRelease-1>", lambda e, v=value: pick(v))
        row.pack(anchor="w", padx=24, pady=2, fill="x")
        autowrap(lb, 20 + 8 + 48 + 16)
        dots.append((value, cv))
    paint()


class ScrollFrame(tk.Frame):
    """A frame that scrolls (mouse wheel works while the pointer is over it). Build into .inner"""

    def __init__(self, parent, bg=None):
        bg = bg or _bg(parent)
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.wrap_labels = []       # (label, space it must leave free) - wraplength follows the real width
        self.sized_widgets = []     # (slider, space it must leave free) - width follows the real width
        self.canvas.bind("<Configure>", self._resized)
        self.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.bind("<Leave>", self._leave)

    def _resized(self, e):
        self.canvas.itemconfigure(self._win, width=e.width)
        self.rewrap(e.width)

    def rewrap(self, width=None):
        """Fit the text and sliders inside the visible width. Fixed pixel widths used to run past the window's
        right edge once the frame, tab padding and scrollbar were taken off, so text was cut off."""
        try:
            width = width or self.canvas.winfo_width()
        except tk.TclError:
            return
        if width <= 1:
            return
        for lb, pad in list(self.wrap_labels):
            try:
                lb.configure(wraplength=max(120, width - pad))
            except tk.TclError:
                self.wrap_labels.remove((lb, pad))
        for wdg, pad in list(self.sized_widgets):
            try:
                wdg.set_width(max(240, width - pad))
            except tk.TclError:
                self.sized_widgets.remove((wdg, pad))

    def _wheel(self, e):
        try:
            self.canvas.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")
        except tk.TclError:
            pass

    def _leave(self, e):
        try:
            x, y = self.winfo_pointerxy()
            w = self.winfo_containing(x, y)
            if w is None or not str(w).startswith(str(self)):
                self.canvas.unbind_all("<MouseWheel>")
        except (tk.TclError, KeyError):
            pass


def draw_heart(c, x, y, s=1.0, happy=True):
    """A little Adventure Time heart-buddy (red and smiling, or grey and grumpy)."""
    col, out = ("#E8343F", "#8E1B24") if happy else ("#77777A", "#3F3F42")
    for lx in (3, 17):
        c.create_line(x + (lx - 2) * s, y + 22 * s, x + (lx - 4) * s, y + 30 * s, fill=out, width=2)
    c.create_oval(x, y, x + 15 * s, y + 15 * s, fill=col, outline=out, width=2)
    c.create_oval(x + 12 * s, y, x + 27 * s, y + 15 * s, fill=col, outline=out, width=2)
    c.create_polygon(x + 1.5 * s, y + 9 * s, x + 25.5 * s, y + 9 * s, x + 13.5 * s, y + 25 * s,
                     fill=col, outline=out, width=2)
    c.create_polygon(x + 3 * s, y + 6 * s, x + 24 * s, y + 6 * s, x + 13.5 * s, y + 22 * s, fill=col, outline="")
    for ex in (9, 17):
        c.create_oval(x + ex * s, y + 9 * s, x + (ex + 2.5) * s, y + 11.5 * s, fill=out, outline="")
    if happy:
        c.create_arc(x + 9 * s, y + 11 * s, x + 18 * s, y + 18 * s, start=200, extent=140, style="arc",
                     outline=out, width=1)
    else:
        c.create_arc(x + 9 * s, y + 15 * s, x + 18 * s, y + 21 * s, start=20, extent=140, style="arc",
                     outline=out, width=1)


class HeartBar(tk.Canvas):
    """A row of hearts: `full` of `total` are red and smiling, the rest grey and grumpy."""

    def __init__(self, parent, full, total=5, bg=None):
        w = total * 36 + 6
        super().__init__(parent, width=w, height=38, bg=bg or _bg(parent), highlightthickness=0, bd=0)
        for i in range(total):
            draw_heart(self, 4 + i * 36, 2, 1.0, happy=i < full)


# ---------------------------------------------------------------- install / re-skin
_SKINNED = set()
_DEF_BG = {"", "systembuttonface", "#f0f0f0", "#d9d9d9", "#ececec", "#ddf7e9"}
_DEF_FIELD = {"", "systemwindow", "white", "#ffffff", "#f4fff9", "systembuttonface", "#ddf7e9"}
_DEF_FG = {"", "systembuttontext", "systemwindowtext", "black", "#000000"}


def _is(v, group):
    return str(v).lower() in group


def _skin_one(w):
    cls = w.winfo_class()
    if cls in ("Toplevel", "Frame", "Labelframe", "Label", "Checkbutton", "Radiobutton", "Scale"):
        if _is(w.cget("bg"), _DEF_BG):
            w.configure(bg=T["paper"])
        if cls in ("Label", "Checkbutton", "Radiobutton", "Labelframe") and _is(w.cget("fg"), _DEF_FG):
            w.configure(fg=T["brown"])
        if cls in ("Checkbutton", "Radiobutton"):
            w.configure(activebackground=T["paper"], selectcolor=T["paper_lt"])
        if cls == "Scale":
            w.configure(troughcolor=T["paper_dk"], activebackground=T["slate_lt"])
    elif cls == "Button":
        if _is(w.cget("bg"), _DEF_BG):
            w.configure(bg=T["slate"], fg=T["white"], activebackground="#76A7C4", activeforeground=T["white"])
        w.configure(relief="raised", bd=2, cursor="hand2")
    elif cls in ("Text", "Entry", "Listbox", "Spinbox"):
        if _is(w.cget("bg"), _DEF_FIELD):
            w.configure(bg=T["paper_lt"])
        if _is(w.cget("fg"), _DEF_FG):
            w.configure(fg=T["brown"])
    elif cls == "Canvas":
        if str(w.cget("bg")).lower() in ("#ddf7e9", "#f4fff9"):
            w.configure(bg=T["paper_lt"])


def skin(win):
    """Re-colour the plain Tk widgets inside a window that was built without the theme."""
    stack = [win]
    while stack:
        w = stack.pop()
        try:
            stack.extend(w.winfo_children())
            _skin_one(w)
        except tk.TclError:
            pass


def _on_map(root, e):
    w = e.widget
    if w is root or not isinstance(w, tk.Toplevel):
        return
    key = str(w)
    if key in _SKINNED:
        return
    _SKINNED.add(key)
    try:
        w.after(60, lambda: skin(w))
    except tk.TclError:
        pass


def install(root):
    """Call once after creating the Tk root."""
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass
    P, B = T["paper"], T["brown"]
    st.configure(".", background=P, foreground=B, font=(FONT, 9))
    for name in ("TFrame", "TLabel", "TCheckbutton", "TRadiobutton"):
        st.configure(name, background=P, foreground=B)
    st.configure("TLabelframe", background=P, bordercolor=T["edge"])
    st.configure("TLabelframe.Label", background=P, foreground=T["title"], font=(FONT, 9, "bold"))
    st.configure("TNotebook", background=P, bordercolor=T["edge"], lightcolor=T["edge"], darkcolor=T["edge"],
                 borderwidth=2, tabmargins=[2, 4, 2, 0])
    st.configure("TNotebook.Tab", background=T["slate"], foreground=T["white"], padding=[12, 5],
                 font=(FONT, 9, "bold"), bordercolor=T["slate_dk"], lightcolor=T["slate_lt"],
                 darkcolor=T["slate_dk"], focuscolor=T["slate"])
    st.map("TNotebook.Tab", background=[("selected", P), ("active", "#76A7C4")],
           foreground=[("selected", T["title"])], focuscolor=[("selected", P)],
           expand=[("selected", [1, 1, 1, 0])])
    for o in ("Vertical", "Horizontal"):
        st.configure(f"{o}.TScrollbar", background=T["slate"], troughcolor=T["paper_dk"], bordercolor=T["edge"],
                     arrowcolor=T["white"], lightcolor=T["slate_lt"], darkcolor=T["slate_dk"])
        st.map(f"{o}.TScrollbar", background=[("active", "#76A7C4")])
    st.configure("TButton", background=T["slate"], foreground=T["white"], font=(FONT, 9, "bold"), padding=6,
                 bordercolor=T["slate_dk"], lightcolor=T["slate_lt"], darkcolor=T["slate_dk"], focuscolor=T["slate"])
    st.map("TButton", background=[("pressed", T["slate_dk"]), ("active", "#76A7C4"), ("disabled", T["grey"])],
           foreground=[("disabled", "#EEEEEE")])
    for name in ("TEntry", "TCombobox", "TSpinbox"):
        st.configure(name, fieldbackground=T["paper_lt"], foreground=B, bordercolor=T["edge"],
                     lightcolor=T["edge"], darkcolor=T["edge"], arrowcolor=B, insertcolor=B)
    st.configure("Treeview", background=T["paper_lt"], fieldbackground=T["paper_lt"], foreground=B, rowheight=22,
                 bordercolor=T["edge"])
    st.configure("Treeview.Heading", background=T["slate"], foreground=T["white"], font=(FONT, 9, "bold"),
                 relief="flat")
    st.map("Treeview", background=[("selected", T["slate"])], foreground=[("selected", T["white"])])
    st.map("Treeview.Heading", background=[("active", "#76A7C4")])
    st.configure("Horizontal.TProgressbar", troughcolor=T["grey"], background="#F5B35E", bordercolor=T["edge"],
                 lightcolor="#F5B35E", darkcolor="#F5B35E")
    st.configure("Horizontal.TScale", background=P, troughcolor=T["grey"])
    f = "{Comic Sans MS} 9 bold"
    for k, v in (("*Menu.background", P), ("*Menu.foreground", B), ("*Menu.activeBackground", T["slate"]),
                 ("*Menu.activeForeground", T["white"]), ("*Menu.font", f), ("*Menu.relief", "flat"),
                 ("*Menu.borderWidth", 2), ("*TCombobox*Listbox.background", T["paper_lt"]),
                 ("*TCombobox*Listbox.foreground", B), ("*TCombobox*Listbox.selectBackground", T["slate"]),
                 ("*TCombobox*Listbox.selectForeground", T["white"]),
                 ("*Entry.background", T["paper_lt"]), ("*Entry.foreground", B),
                 ("*Entry.insertBackground", B), ("*Entry.highlightThickness", 1),
                 ("*Entry.highlightBackground", T["edge"]), ("*Entry.highlightColor", T["slate"]),
                 ("*Entry.relief", "flat"), ("*Text.background", T["paper_lt"]), ("*Text.foreground", B),
                 ("*Text.insertBackground", B), ("*Listbox.background", T["paper_lt"]),
                 ("*Listbox.foreground", B), ("*Listbox.selectBackground", T["slate"]),
                 ("*Listbox.selectForeground", T["white"]), ("*Button.relief", "raised"),
                 ("*Button.borderWidth", 2), ("*Button.cursor", "hand2")):
        try:
            root.option_add(k, v)
        except tk.TclError:
            pass
    root.bind_class("Toplevel", "<Map>", lambda e: _on_map(root, e), add="+")
