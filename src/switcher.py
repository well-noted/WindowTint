"""The window switcher popup (tkinter + Pillow + DWM): a grid of window cards with live previews, like
Windows' own Ctrl+Alt+Tab view, each card in its project color. Used for the hotkey (type to search) and,
when switched on, for Alt+Tab (hold mode)."""
from __future__ import annotations

import tkinter as tk

from PIL import ImageTk

import switcher_layout as grid
import switcher_logic as logic
import switcher_render as render
import winapi

try:
    import icons
except Exception:        # not importable off Windows
    icons = None


def _log(text: str) -> None:
    """Optional diagnostic trail: set the environment variable WINDOWTINT_DEBUG=1 to write
    %APPDATA%\\WindowTint\\switcher.log (trimmed so it never grows)."""
    import os
    if not os.environ.get("WINDOWTINT_DEBUG"):
        return
    try:
        import time
        from config import app_dir
        path = app_dir() / "switcher.log"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()[-300:]
        except OSError:
            lines = []
        lines.append(f"{time.strftime('%H:%M:%S')} {text}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


class Switcher:
    def __init__(self, root, engine):
        self.root, self.engine = root, engine
        self.win = None
        self.label = None
        self.photo = None
        self.visible = False
        self.all: list = []
        self.items: list = []
        self.query = ""
        self.selected = 0
        self.fg = 0
        self.scale = 1.0
        self.layout = None
        self.mode = "search"          # 'search' (hotkey, type to filter) or 'hold' (Alt+Tab)
        self.hold_active = False       # an Alt+Tab session is in progress
        self._pending = None           # scheduled first draw of the hold UI
        self._mouse0 = None
        self._top = 0                  # y of the panel (search mode keeps the search field still)
        self._anchor = (0, 0)
        self._area = (0, 0, 1920, 1080)
        self._thumbs = None
        self._icons: dict = {}

    # -- opening and closing ------------------------------------------------------
    def toggle(self, fg: int = 0) -> None:
        if self.visible:
            self.close()
        else:
            self.show(fg)

    def show(self, fg: int = 0) -> None:
        self._end_hold(hide=True)
        self.mode = "search"
        self.fg = fg or winapi.get_foreground()
        self.all = self.engine.switcher_entries()
        if not self.all:
            return
        self.query = ""
        self.items = list(self.all)
        self.selected = logic.initial_selection(self.items, self.fg)
        self._mouse0 = None
        self._ensure_window()
        self._begin_draw()
        self.win.update_idletasks()
        self.win.deiconify()
        self.win.lift()
        winapi.set_noactivate(self.win.winfo_id(), False)
        winapi.style_popup(self.win.winfo_id())
        winapi.focus_window(self._hwnd())
        self.win.focus_force()
        self.visible = True
        self._repaint()
        self._apply_previews()

    def _repaint(self) -> None:
        """Make sure the popup shows THIS session's cards, not the picture it had when it was last hidden."""
        self.win.update_idletasks()
        self.label.configure(image=self.photo)
        fn = getattr(winapi, "repaint", None)
        if fn is not None:
            fn(self.win.winfo_id())
        self.win.update_idletasks()

    def close(self, restore: bool = True) -> None:
        if not self.visible:
            return
        self.visible = False
        self._clear_previews()
        self.win.withdraw()
        if restore:
            winapi.focus_window(self.fg)

    def _hwnd(self) -> int:
        return winapi.user32.GetParent(self.win.winfo_id()) or self.win.winfo_id()

    def _ensure_window(self) -> None:
        if self.win is not None:
            return
        win = self.win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg="#1c1c21")
        self.label = tk.Label(win, bd=0, highlightthickness=0, bg="#1c1c21")
        self.label.pack()
        win.bind("<Key>", self._on_key)
        win.bind("<Motion>", self._on_motion)
        win.bind("<Button-1>", self._on_click)
        win.bind("<MouseWheel>", self._on_wheel)
        win.bind("<FocusOut>", self._on_focus_out)

    # -- drawing ---------------------------------------------------------------------------
    def _begin_draw(self) -> None:
        """First draw of a session: pick the screen, the scale and the vertical position."""
        self.scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        self._area = winapi.cursor_work_area() or (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        self._redraw(reposition=True)

    def _compute_layout(self) -> None:
        l, t, r, b = self._area
        self.layout = grid.compute_layout([e.aspect for e in self.items], self.selected, self.scale,
                                          int((r - l) * 0.94), int((b - t) * (0.82 if self.mode == "search" else 0.88)),
                                          with_search=self.mode == "search")

    def _ensure_layout(self) -> None:
        if self.layout is None or len(self.layout.centers) != len(self.items):
            self._compute_layout()

    def _icon_for(self, e):
        if icons is None:
            return None
        if e.hwnd not in self._icons:
            self._icons[e.hwnd] = icons.get_icon(e.hwnd, e.pid)
        return self._icons[e.hwnd]

    def _redraw(self, reposition: bool = False) -> None:
        old = self.layout
        self._compute_layout()
        lay = self.layout
        for c in lay.cards:
            self._icon_for(self.items[c.index])
        img = render.render(self.items, lay, self.selected, self.query, self.mode, total=len(self.all),
                            icons=self._icons)
        self.photo = ImageTk.PhotoImage(img)
        self.label.configure(image=self.photo)
        l, t, r, b = self._area
        w, h = img.size
        x = l + (r - l - w) // 2
        if self.mode == "hold":
            y = t + (b - t - h) // 2
        else:
            if reposition:
                self._top = t + int((b - t) * 0.09)
            y = self._top
        self.win.geometry(f"{w}x{h}+{x}+{y}")
        self._anchor = (x, y)
        if self.visible and (old is None or [(c.index, c.x, c.y, c.w) for c in old.cards] !=
                             [(c.index, c.x, c.y, c.w) for c in lay.cards]):
            self._apply_previews()

    def _apply_previews(self) -> None:
        """Ask Windows to draw each visible window's live preview inside its card."""
        if self.layout is None or not hasattr(winapi, "ThumbnailSet"):
            return
        if self._thumbs is None:
            self._thumbs = winapi.ThumbnailSet()
        try:
            self._thumbs.apply(self._hwnd(), render.preview_rects(self.items, self.layout))
        except Exception:
            pass

    def _clear_previews(self) -> None:
        if self._thumbs is not None:
            try:
                self._thumbs.clear()
            except Exception:
                pass

    def _refilter(self) -> None:
        self.items = logic.filter_entries(self.all, self.query)
        self.selected = 0
        self._redraw()

    # -- Alt+Tab (hold) mode -----------------------------------------------------------------
    SHOW_DELAY_MS = 110     # a quick Alt+Tab tap switches before anything is drawn, as in Windows

    def begin_hold(self, shift: bool = False) -> None:
        """Alt+Tab pressed. Nothing is drawn for the first moment, so a quick tap is instant."""
        if self.hold_active:
            # The hook only sends "begin" when no Alt+Tab session is open, so this one is left over
            # (for example an Alt release that went to an elevated window). Start over with fresh data.
            _log("begin while a previous Alt+Tab session was still open: starting over")
            self._end_hold(hide=True)
        if self.visible:
            self.close(restore=False)
        fg = winapi.get_foreground()
        entries = self.engine.switcher_entries()
        _log("alt+tab: " + " | ".join(f"{e.title[:28]!r}->{e.project or '-'}" for e in entries[:4]))
        if len(entries) < 2:
            return
        self.fg, self.all, self.items, self.query = fg, entries, list(entries), ""
        self.selected = len(entries) - 1 if shift else logic.initial_selection(entries, fg)
        self.mode = "hold"
        self.layout = None
        self.hold_active = True
        self._mouse0 = None
        self._pending = self.root.after(self.SHOW_DELAY_MS, self._show_hold_ui)

    def _hold_changed(self) -> None:
        if self.visible:
            self._redraw()
        elif self._pending is not None:      # repeated presses: no reason to wait any longer
            self.root.after_cancel(self._pending)
            self._pending = None
            self._show_hold_ui()

    def step(self, delta: int) -> None:
        if not self.hold_active:
            return
        self.selected = logic.move(self.selected, delta, len(self.items))
        self._hold_changed()

    def arrow(self, direction: str) -> None:
        """Arrow key during Alt+Tab (or in the searchable view): move across the grid."""
        if not self.items:
            return
        if self.mode == "hold" and not self.hold_active:
            return
        self._ensure_layout_for_nav()
        self.selected = grid.neighbor(self.layout, self.selected, direction, len(self.items))
        if self.mode == "hold":
            self._hold_changed()
        else:
            self._redraw()

    def _ensure_layout_for_nav(self) -> None:
        if self.layout is None or len(self.layout.centers) != len(self.items):
            if not self._area or self._area == (0, 0, 1920, 1080):
                self._area = winapi.cursor_work_area() or self._area
            self.scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
            self._compute_layout()

    def commit(self) -> None:
        """Alt released: switch to the selected window."""
        if not self.hold_active:
            return
        target = self.items[self.selected].hwnd if 0 <= self.selected < len(self.items) else 0
        self._end_hold(hide=True)
        winapi.focus_window(target)

    def cancel_hold(self) -> None:
        self._end_hold(hide=True)

    def _end_hold(self, hide: bool) -> None:
        if self._pending is not None:
            self.root.after_cancel(self._pending)
            self._pending = None
        was = self.hold_active
        self.hold_active = False
        if was and hide and self.visible:
            self.close(restore=False)

    def _show_hold_ui(self) -> None:
        self._pending = None
        if not self.hold_active:
            return
        self._ensure_window()
        self._begin_draw()
        self.win.update_idletasks()
        winapi.set_noactivate(self.win.winfo_id(), True)   # the popup must not take focus from the window you are in
        self.win.deiconify()
        self.win.lift()
        winapi.style_popup(self.win.winfo_id())
        self.visible = True
        self._repaint()
        self._apply_previews()

    # -- input ------------------------------------------------------------------------------
    def _on_key(self, e) -> None:
        key = e.keysym
        if key == "Escape":
            self.close()
        elif key in ("Return", "KP_Enter"):
            self._activate(self.selected)
        elif key in ("Left", "Right", "Up", "Down"):
            self.arrow(key.lower())
        elif key == "ISO_Left_Tab" or (key == "Tab" and e.state & 0x1):
            self._select(logic.move(self.selected, -1, len(self.items)))
        elif key == "Tab":
            self._select(logic.move(self.selected, 1, len(self.items)))
        elif key == "Home":
            self._select(0)
        elif key == "End":
            self._select(max(0, len(self.items) - 1))
        elif key == "BackSpace":
            self.query = "" if e.state & 0x4 else self.query[:-1]
            self._refilter()
        elif e.char and e.char.isprintable() and not (e.state & 0x4):
            self.query += e.char
            self._refilter()

    def _select(self, index: int) -> None:
        if not self.items or index == self.selected:
            return
        self.selected = index
        self._redraw()

    def _card_under(self, x: int, y: int) -> int:
        c = self.layout.card_at(x, y) if self.layout else None
        return c.index if c else -1

    def _on_motion(self, e) -> None:
        # Windows reports a mouse move when the popup appears under a resting pointer; only a real move counts.
        if self._mouse0 is None:
            self._mouse0 = (e.x_root, e.y_root)
            return
        if abs(e.x_root - self._mouse0[0]) + abs(e.y_root - self._mouse0[1]) < 4:
            return
        i = self._card_under(e.x, e.y)
        if i >= 0 and i != self.selected:
            self.selected = i
            self._redraw()

    def _on_click(self, e) -> None:
        i = self._card_under(e.x, e.y)
        if i >= 0:
            self._activate(i)

    def _on_wheel(self, e) -> None:
        self._select(max(0, min(len(self.items) - 1, self.selected + (-1 if e.delta > 0 else 1))))

    def _on_focus_out(self, _e) -> None:
        self.win.after(150, self._maybe_close)

    def _maybe_close(self) -> None:
        if self.mode == "hold":
            return
        if self.visible and self.win.focus_displayof() is None:
            self.close(restore=False)

    def _activate(self, index: int) -> None:
        if not (0 <= index < len(self.items)):
            return
        target = self.items[index].hwnd
        self._end_hold(hide=False)
        self.close(restore=False)
        winapi.focus_window(target)
