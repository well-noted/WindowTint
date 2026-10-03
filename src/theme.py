"""Look and feel for WindowTint's windows: the Windows 11 style "Sun Valley" ttk theme (sv-ttk) in light or
dark to match the system, plus the few extra styles and colors the custom widgets use.

If sv-ttk is not installed the app still works with the plain Windows theme."""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk

LIGHT = {"bg": "#fafafa", "card": "#ffffff", "border": "#e3e3e6", "text": "#1b1b1f", "muted": "#6b6b73",
         "accent": "#0067c0", "hover": "#f1f1f4", "danger": "#c42b1c"}
DARK = {"bg": "#1c1c1c", "card": "#272727", "border": "#3a3a3a", "text": "#f3f3f3", "muted": "#a0a0a8",
        "accent": "#60cdff", "hover": "#303030", "danger": "#ff99a4"}

FONT = "Segoe UI"
PRESET_COLORS = ["#e53935", "#fb8c00", "#fdd835", "#43a047", "#00acc1", "#1e88e5", "#5e35b1", "#8e24aa",
                 "#d81b60", "#6d4c41", "#546e7a", "#00897b"]

palette = dict(LIGHT)
dark = False


def system_dark() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winapi
        return winapi.system_uses_dark_apps()
    except Exception:
        return False


def apply(root: tk.Misc, force_dark: bool | None = None) -> dict:
    """Install the theme on the Tk root and return the active palette."""
    global palette, dark
    dark = system_dark() if force_dark is None else force_dark
    palette = dict(DARK if dark else LIGHT)
    try:
        import sv_ttk
        sv_ttk.set_theme("dark" if dark else "light", root)
    except Exception:
        try:
            ttk.Style(root).theme_use("vista" if sys.platform == "win32" else "clam")
        except tk.TclError:
            pass
    try:
        root.update()        # lets the theme finish applying its colors before any window is built
    except tk.TclError:
        pass
    style = ttk.Style(root)
    p = palette
    style.configure("Tile.TFrame", background=p["card"])
    style.configure("Page.TFrame", background=p["bg"])
    style.configure("Tile.TLabel", background=p["card"], foreground=p["text"])
    style.configure("TileMuted.TLabel", background=p["card"], foreground=p["muted"], font=(FONT, 9))
    style.configure("TileTitle.TLabel", background=p["card"], foreground=p["text"], font=(FONT, 11, "bold"))
    style.configure("Tile.Switch.TCheckbutton", background=p["card"])
    style.configure("Muted.TLabel", foreground=p["muted"], font=(FONT, 9))
    style.configure("Heading.TLabel", font=(FONT, 15, "bold"))
    style.configure("Section.TLabel", font=(FONT, 11, "bold"))
    style.configure("Badge.TLabel", background=p["hover"], foreground=p["muted"], font=(FONT, 8), padding=(6, 1))
    style.configure("Icon.TButton", padding=(6, 3))
    return palette


def style_window(win: tk.Misc) -> None:
    """Dark title bar when the theme is dark (after the window exists)."""
    if sys.platform != "win32":
        return
    try:
        import winapi
        win.update_idletasks()
        winapi.style_titlebar(win.winfo_id(), dark)
    except Exception:
        pass


def text_on(bg: str) -> str:
    r, g, b = int(bg[1:3], 16), int(bg[3:5], 16), int(bg[5:7], 16)
    return "#101014" if (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.62 else "#ffffff"
