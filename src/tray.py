"""System tray icon and menu (pystray)."""
from __future__ import annotations

import pystray
from PIL import Image, ImageDraw

import winapi


def make_icon_image(colors: list) -> Image.Image:
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, size - 3, size - 3), radius=10, fill=(40, 40, 40, 255))
    colors = colors[:4] or ["#888888"]
    n = len(colors)
    inner = size - 16
    if n <= 2:
        cells = [(8 + i * inner // n, 8, 8 + (i + 1) * inner // n - 1, 8 + inner) for i in range(n)]
    else:
        half = inner // 2
        cells = [(8 + (i % 2) * half, 8 + (i // 2) * half, 8 + (i % 2) * half + half - 2, 8 + (i // 2) * half + half - 2)
                 for i in range(n)]
    for box, color in zip(cells, colors):
        d.rectangle(box, fill=color)
    return img


class Tray:
    def __init__(self, cfg, engine, ui_queue):
        self.cfg = cfg
        self.engine = engine
        self.ui = ui_queue  # main-thread queue: "settings" | "quit"
        self.icon = pystray.Icon("WindowTint", make_icon_image(self._colors()), "WindowTint", self._menu())
        engine.notify = self._notify

    def _colors(self) -> list:
        with self.cfg.lock:
            return [p["color"] for p in self.cfg.projects]

    # -- menu ---------------------------------------------------------------
    def _menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(self._last_label, None, enabled=False),
            pystray.MenuItem("Assign last active window to", pystray.Menu(self._project_items)),
            pystray.MenuItem("Auto (use rules)", self._auto),
            pystray.MenuItem("No color for this window", self._none),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Window switcher", self._open_switcher),
            pystray.MenuItem("Use WindowTint for Alt+Tab", self._toggle_alttab,
                             checked=lambda item: bool(self.cfg.data.get("alt_tab"))),
            pystray.MenuItem("Settings...", self._open_settings, default=True),
            pystray.MenuItem("Pause", self._toggle_pause, checked=lambda item: self.engine.paused),
            pystray.MenuItem("Start with Windows", self._toggle_autostart,
                             checked=lambda item: winapi.autostart_enabled()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._quit),
        )

    def _last_label(self, item) -> str:
        w = self.engine.last_active
        if not w:
            return "Last active window: none yet"
        title = w.title if len(w.title) <= 38 else w.title[:37] + "..."
        return f"Last: {title}"

    def _project_items(self):
        with self.cfg.lock:
            names = [p["name"] for p in self.cfg.projects]
        if not names:
            yield pystray.MenuItem("(no projects, add some in Settings)", None, enabled=False)
        for i, name in enumerate(names):
            yield pystray.MenuItem(f"{i + 1}. {name}", self._assign_action(name), checked=self._checked_for(name))

    def _assign_action(self, name):
        def action(icon, item):
            w = self.engine.last_active
            if w:
                self.engine.assign(w.hwnd, name)
        return action

    def _checked_for(self, name):
        def checked(item):
            w = self.engine.last_active
            return bool(w) and self.engine.project_of(w.hwnd)[0] == name
        return checked

    def _auto(self, icon, item):
        if self.engine.last_active:
            self.engine.auto(self.engine.last_active.hwnd)

    def _none(self, icon, item):
        if self.engine.last_active:
            self.engine.force_none(self.engine.last_active.hwnd)

    def _toggle_alttab(self, icon, item):
        self.ui.put("toggle_alttab")

    def _open_switcher(self, icon, item):
        self.ui.put("switcher")

    def _open_settings(self, icon, item):
        self.ui.put("settings")

    def _toggle_pause(self, icon, item):
        self.engine.set_paused(not self.engine.paused)

    def _toggle_autostart(self, icon, item):
        winapi.set_autostart(not winapi.autostart_enabled())

    def _quit(self, icon, item):
        self.ui.put("quit")

    # -- public ---------------------------------------------------------------
    def start(self) -> None:
        self.icon.run_detached()

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:
            pass

    def refresh(self) -> None:
        """Call after projects change so the icon and menu reflect them."""
        self.icon.icon = make_icon_image(self._colors())
        self.icon.update_menu()

    def _notify(self, message: str) -> None:
        try:
            self.icon.notify(message, "WindowTint")
        except Exception:
            pass
