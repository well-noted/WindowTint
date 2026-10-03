"""WindowTint entry point: tray app that colors window borders/title bars by project (Windows 11)."""
from __future__ import annotations

import ctypes
import os
import queue
import sys
import threading
import time
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import messagebox


def _message(text: str, title: str = "WindowTint", icon: int = 0x40) -> None:
    ctypes.windll.user32.MessageBoxW(0, text, title, icon)


def main() -> int:
    import winapi

    winapi.enable_dpi_awareness()
    if not winapi.acquire_single_instance():
        _message("WindowTint is already running. Look for its icon in the system tray "
                 "(click the ^ arrow near the clock if it is hidden).")
        return 0

    root = tk.Tk()
    root.withdraw()
    import theme
    theme.apply(root)

    if sys.getwindowsversion().build < 22000:
        messagebox.showerror("WindowTint", "WindowTint needs Windows 11 (build 22000 or later) "
                                           "for window border and title bar colors.")
        return 1

    from config import Config
    from engine import Engine
    from settings_ui import SettingsWindow
    from switcher import Switcher
    from tray import Tray

    cfg = Config()
    cfg.load()

    engine = Engine(cfg)
    engine.start()

    ui_queue: "queue.Queue" = queue.Queue()
    beat = {"t": time.monotonic()}   # the Alt+Tab hook gives Alt+Tab back to Windows if this stops updating
    import alttab_hook
    alttab = alttab_hook.AltTabHook(lambda ev: ui_queue.put(("alttab",) + ev),
                                    alive=lambda: time.monotonic() - beat["t"] < 1.0)
    alttab.start()
    if cfg.data.get("alt_tab"):
        alttab.set_enabled(True)
    # The switcher hotkey skips the engine's polling queue so it opens instantly.
    hotkeys = winapi.HotkeyThread(cfg, engine.events,
                                  direct={12: lambda fg: ui_queue.put(("switcher", fg))})
    hotkeys.start()

    # Window events make new windows get their color almost immediately instead of at the next poll.
    win_events = winapi.WinEventThread(engine.mark_dirty)
    win_events.start()

    tray = Tray(cfg, engine, ui_queue)
    tray.start()

    state = {"settings": None}
    switcher = Switcher(root, engine)

    def open_settings():
        win = state["settings"]
        if win and win.alive():
            win.lift()
            return
        # Tray updates go through a short-lived thread so the settings window never waits on the tray.
        state["settings"] = SettingsWindow(
            root, cfg, engine, lambda: threading.Thread(target=tray.refresh, daemon=True).start(), apply_hotkeys)

    def open_rule_editor(hwnd):
        from rule_editor import RuleEditor
        w = engine.find_window(hwnd)
        if w is None:
            tray._notify("That window can't be used for a rule (it may have closed).")
            return

        def saved():
            threading.Thread(target=tray.refresh, daemon=True).start()
            sw = state["settings"]
            if sw and sw.alive():
                sw.refresh_all()
        RuleEditor(root, cfg, engine, window=w, on_saved=saved)

    engine.on_new_rule = lambda hwnd: ui_queue.put(("newrule", hwnd))

    def apply_hotkeys():
        """Re-register hotkeys and switch the Alt+Tab replacement on or off to match the config."""
        want = bool(cfg.data.get("alt_tab"))
        if alttab.set_enabled(want) != want and want:
            tray._notify("The Alt+Tab replacement could not start: " + (alttab.error or "unknown error"))
        return hotkeys.reload()

    def toggle_alttab():
        with cfg.lock:
            cfg.data["alt_tab"] = not cfg.data.get("alt_tab")
        cfg.save()
        want = bool(cfg.data["alt_tab"])
        installed = alttab.set_enabled(want)
        threading.Thread(target=tray.refresh, daemon=True).start()
        if want and not installed:
            tray._notify("The Alt+Tab replacement could not start: " + (alttab.error or "unknown error"))
        else:
            tray._notify("Alt+Tab now opens WindowTint's switcher" if want else "Alt+Tab is back to the Windows switcher")

    def shutdown():
        alttab.set_enabled(False)
        alttab.stop()
        engine.stop()
        win_events.stop()
        hotkeys.stop()
        tray.stop()
        root.destroy()

    def pump():
        beat["t"] = time.monotonic()
        try:
            while True:
                cmd = ui_queue.get_nowait()
                if isinstance(cmd, tuple) and cmd[0] == "alttab":
                    kind = cmd[1]
                    if kind == "begin":
                        switcher.begin_hold(bool(cmd[2]))
                    elif kind == "step":
                        switcher.step(cmd[2])
                    elif kind == "arrow":
                        switcher.arrow(cmd[2])
                    elif kind == "commit":
                        switcher.commit()
                    elif kind == "cancel":
                        switcher.cancel_hold()
                elif cmd == "toggle_alttab":
                    toggle_alttab()
                elif isinstance(cmd, tuple) and cmd[0] == "newrule":
                    open_rule_editor(cmd[1])
                elif isinstance(cmd, tuple) and cmd[0] == "switcher":
                    switcher.toggle(cmd[1])
                elif cmd == "switcher":
                    switcher.toggle()
                elif cmd == "settings":
                    open_settings()
                elif cmd == "quit":
                    shutdown()
                    return
        except queue.Empty:
            pass
        root.after(10, pump)

    root.after(10, pump)
    import hotkeys as hk
    if hotkeys._ready.wait(2) and hotkeys.failed:
        tray._notify("These hotkeys are used by another program; change them in Settings > General: "
                     + ", ".join(hotkeys.failed))
    else:
        mods = cfg.data["hotkey_modifiers"].title()
        tray._notify(f"Running. {mods}+1 colors the focused window, "
                     f"{hk.display_combo(cfg.data['hotkey_new_rule'])} makes a rule from it, "
                     f"{hk.display_combo(cfg.data['hotkey_switcher'])} opens the window switcher.")
    root.mainloop()
    return 0


def run() -> int:
    """Run main(); on any crash, write error.log and show the error instead of dying silently
    (pythonw has no console, so a plain traceback would never be seen)."""
    try:
        return main()
    except Exception:
        text = traceback.format_exc()
        try:
            log = Path(os.environ.get("APPDATA", ".")) / "WindowTint" / "error.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(text, encoding="utf-8")
        except OSError:
            pass
        _message(text[-1500:], "WindowTint crashed", 0x10)
        return 1


if __name__ == "__main__":
    sys.exit(run())
