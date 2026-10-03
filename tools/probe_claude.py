"""Diagnostic: shows what the Claude desktop window exposes while you switch conversations.

Run:  python probe_claude.py
Then click through a few conversations in different projects. Press Ctrl+C to stop.
Prints (1) the window title whenever it changes and (2) any selected/current items that
Windows UI Automation can see inside the window. Paste the output back to Claude.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import ctypes
import subprocess
import sys
import time

sys.stdout.reconfigure(line_buffering=True)
print("probe_claude starting...")

try:
    import uiautomation as auto
except ImportError:
    print("Installing uiautomation...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "uiautomation"])
    import uiautomation as auto

import psutil
from ctypes import wintypes

user32 = ctypes.WinDLL("user32")
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def claude_windows(every=False):
    out = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            name = psutil.Process(pid.value).name()
        except psutil.Error:
            return True
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if every or "claude" in name.lower() or "claude" in buf.value.lower():
            out.append((int(hwnd), name, cls.value, buf.value))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def selected_items(hwnd, max_nodes=4000):
    """Walk the UIA tree and collect elements that report themselves as selected."""
    found, count = [], 0
    root = auto.ControlFromHandle(hwnd)
    stack = [(root, 0)]
    while stack and count < max_nodes:
        ctl, depth = stack.pop()
        count += 1
        try:
            pat = ctl.GetSelectionItemPattern()
            if pat and pat.IsSelected:
                found.append((ctl.ControlTypeName, ctl.Name))
        except Exception:
            pass
        if depth < 25:
            try:
                for child in ctl.GetChildren():
                    stack.append((child, depth + 1))
            except Exception:
                pass
    return found, count


print("All visible windows right now (exe | class | title):", flush=True)
for _, exe, cls, title in claude_windows(every=True):
    if title:
        print(f"  {exe} | {cls} | {title!r}", flush=True)
print("\nWatching for windows with 'claude' in the exe name or title... (Ctrl+C to stop)", flush=True)

last = {}
while True:
    for hwnd, exe, cls, title in claude_windows():
        sel = None
        try:
            sel, n = selected_items(hwnd)
        except Exception as e:
            sel = [("error", str(e))]
        state = (title, tuple(sel))
        if last.get(hwnd) != state:
            last[hwnd] = state
            print(f"\n[{time.strftime('%H:%M:%S')}] hwnd={hwnd} exe={exe} class={cls}")
            print(f"  title: {title!r}")
            for kind, name in sel:
                print(f"  selected {kind}: {name!r}")
            if not sel:
                print("  (no selected items visible to UI Automation)")
    time.sleep(1.5)
