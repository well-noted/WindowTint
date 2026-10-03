"""Small reusable tkinter widgets for the settings window: a scrolling area, color dots, the
project dialog, and hotkey capture."""
from __future__ import annotations

import tkinter as tk
from tkinter import colorchooser, ttk

import hotkeys
import theme


class ScrollFrame(ttk.Frame):
    """A vertically scrolling area. Put children in `.body`."""

    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=theme.palette["bg"])
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas, style="Page.TFrame")
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=self._set_bar)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _set_bar(self, lo, hi):
        self.bar.set(lo, hi)
        if float(lo) <= 0.0 and float(hi) >= 1.0:
            self.bar.pack_forget()
        elif not self.bar.winfo_ismapped():
            self.bar.pack(side="right", fill="y")

    def _wheel(self, e):
        if self.body.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")


def tile(parent, padding=(14, 10)) -> ttk.Frame:
    """A white (or dark grey) panel with a thin border. Returns the inner frame; the caller packs `.outer`."""
    outer = tk.Frame(parent, bg=theme.palette["border"])
    inner = ttk.Frame(outer, style="Tile.TFrame", padding=padding)
    inner.pack(fill="both", expand=True, padx=1, pady=1)
    inner.outer = outer
    return inner


def tlabel(parent, **kw) -> ttk.Label:
    """A label that sits on a tile: the background is also set directly, because the theme can leave a
    differently colored box behind the text otherwise."""
    return ttk.Label(parent, background=theme.palette["card"], **kw)


def dot(parent, color: str, size: int = 22, bg: str | None = None, command=None) -> tk.Canvas:
    """A filled circle in a project's color."""
    c = tk.Canvas(parent, width=size, height=size, highlightthickness=0, bd=0, bg=bg or theme.palette["card"],
                  cursor="hand2" if command else "")
    c.create_oval(2, 2, size - 2, size - 2, fill=color, outline=color)
    if command:
        c.bind("<Button-1>", lambda e: command())
    return c


def chip(parent, text: str, color: str) -> tk.Label:
    return tk.Label(parent, text=text, bg=color, fg=theme.text_on(color), font=(theme.FONT, 9, "bold"), padx=8, pady=1)


class ProjectDialog(tk.Toplevel):
    """Name and color in one small window, with a palette so a color is one click away."""

    def __init__(self, parent, title: str, name: str = "", color: str = "", taken=(), on_done=None):
        super().__init__(parent)
        self.taken, self.on_done = set(taken), on_done
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.color = color or theme.PRESET_COLORS[len(self.taken) % len(theme.PRESET_COLORS)]
        frm = ttk.Frame(self, padding=20)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=title, style="Heading.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(frm, text="Name").grid(row=1, column=0, sticky="w", pady=(16, 4))
        self.name_var = tk.StringVar(value=name)
        self.entry = ttk.Entry(frm, textvariable=self.name_var, width=34)
        self.entry.grid(row=2, column=0, columnspan=2, sticky="ew")
        ttk.Label(frm, text="Color").grid(row=3, column=0, sticky="w", pady=(16, 4))
        self.palette = ttk.Frame(frm)
        self.palette.grid(row=4, column=0, columnspan=2, sticky="w")
        self._swatches: dict = {}
        for i, c in enumerate(theme.PRESET_COLORS):
            d = dot(self.palette, c, 30, bg=theme.palette["bg"], command=lambda c=c: self._choose(c))
            d.grid(row=i // 6, column=i % 6, padx=2, pady=2)
            self._swatches[c] = d
        ttk.Button(self.palette, text="Custom...", command=self._custom).grid(row=0, column=6, rowspan=2, padx=(12, 0))
        self.preview = tk.Label(frm, text="  Preview  ", font=(theme.FONT, 10, "bold"))
        self.preview.grid(row=5, column=0, sticky="w", pady=(14, 0))
        self.error = ttk.Label(frm, text="", foreground=theme.palette["danger"])
        self.error.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))
        btns = ttk.Frame(frm)
        btns.grid(row=7, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(btns, text="Save", style="Accent.TButton", command=self._save).pack(side="right")
        self.bind("<Return>", lambda e: self._save())
        self.bind("<Escape>", lambda e: self.destroy())
        self._choose(self.color)
        theme.style_window(self)
        self.grab_set()
        self.entry.focus_set()
        self.entry.selection_range(0, "end")

    def _choose(self, color: str) -> None:
        self.color = color.lower()
        self.preview.configure(bg=self.color, fg=theme.text_on(self.color), text=f"  {self.name_var.get() or 'Preview'}  ")
        for c, d in self._swatches.items():
            d.delete("ring")
            if c.lower() == self.color:
                d.create_oval(0, 0, 29, 29, outline=theme.palette["text"], width=2, tags="ring")

    def _custom(self) -> None:
        _rgb, color = colorchooser.askcolor(color=self.color, title="Custom color", parent=self)
        if color:
            self._choose(color)

    def _save(self) -> None:
        name = self.name_var.get().strip()
        if not name:
            self.error.configure(text="Give the project a name.")
            return
        if name in self.taken:
            self.error.configure(text="That name is already used.")
            return
        done = self.on_done
        self.destroy()
        if done:
            done(name, self.color)


class HotkeyField(ttk.Frame):
    """Click it, then press the key combination you want. Shows e.g. 'Ctrl+Alt+W'."""

    def __init__(self, parent, value: str, default: str, on_change=None):
        super().__init__(parent)
        self.value, self.default = value, default
        self.on_change = on_change
        self.var = tk.StringVar(value=hotkeys.display_combo(value))
        self.entry = ttk.Entry(self, textvariable=self.var, width=18, state="readonly", justify="center")
        self.entry.pack(side="left")
        ttk.Button(self, text="Reset", command=lambda: self.set(self.default)).pack(side="left", padx=(6, 0))
        self.entry.bind("<FocusIn>", lambda e: self.var.set("Press keys..."))
        self.entry.bind("<FocusOut>", lambda e: self.var.set(hotkeys.display_combo(self.value)))
        self.entry.bind("<Button-1>", lambda e: self.entry.focus_set())
        self.entry.bind("<KeyPress>", self._on_key)

    def set(self, value: str, notify: bool = True) -> None:
        changed = value != self.value
        self.value = value
        self.var.set(hotkeys.display_combo(value))
        if changed and notify and self.on_change:
            self.on_change()

    def _on_key(self, e):
        if e.keysym == "Escape":
            self.set(self.value, notify=False)
            self.winfo_toplevel().focus_set()
            return "break"
        combo = hotkeys.combo_from_event(e.state, e.keysym)
        if combo and hotkeys.is_valid(combo):
            self.set(combo)
            self.winfo_toplevel().focus_set()
        return "break"
