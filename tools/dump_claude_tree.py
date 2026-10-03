"""Writes what Windows UI Automation can see inside the Claude desktop window to
claude_tree.txt (next to this script). Run it once with a conversation open, then tell
Claude it is done; Claude reads the file directly.

    python dump_claude_tree.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

import psutil
import uiautomation as auto

OUT = Path(__file__).with_name("claude_tree.txt")
MAX_NODES = 8000
MAX_DEPTH = 40

user32 = ctypes.WinDLL("user32")
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def find_hwnds():
    found = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            try:
                if psutil.Process(pid.value).name().lower() == "claude.exe":
                    buf = ctypes.create_unicode_buffer(512)
                    user32.GetWindowTextW(hwnd, buf, 512)
                    if buf.value:
                        found.append(int(hwnd))
            except psutil.Error:
                pass
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def safe(fn, default=""):
    try:
        v = fn()
        return "" if v is None else v
    except Exception:
        return default


def dump(hwnd):
    lines, count = [], 0
    stack = [(auto.ControlFromHandle(hwnd), 0)]
    while stack and count < MAX_NODES:
        ctl, depth = stack.pop()
        count += 1
        name = safe(lambda: ctl.Name)
        sel = safe(lambda: ctl.GetSelectionItemPattern().IsSelected, "")
        status = safe(lambda: ctl.ItemStatus)
        aid = safe(lambda: ctl.AutomationId)
        help_ = safe(lambda: ctl.HelpText)
        lines.append(
            f"{'  ' * depth}{safe(lambda: ctl.ControlTypeName)} name={name[:90]!r}"
            f"{' selected=' + str(sel) if sel != '' else ''}"
            f"{' status=' + repr(status) if status else ''}"
            f"{' id=' + repr(aid[:40]) if aid else ''}"
            f"{' help=' + repr(help_[:40]) if help_ else ''}"
        )
        if depth < MAX_DEPTH:
            kids = safe(lambda: ctl.GetChildren(), [])
            for child in reversed(kids):
                stack.append((child, depth + 1))
    return lines, count


hwnds = find_hwnds()
if not hwnds:
    print("No visible claude.exe window with a title found.")
    sys.exit(1)

best = []
for attempt in range(2):  # Chromium builds its accessibility tree lazily; try twice
    for hwnd in hwnds:
        lines, count = dump(hwnd)
        print(f"attempt {attempt + 1}: hwnd {hwnd}: {count} nodes")
        if len(lines) > len(best):
            best = lines
    time.sleep(3)

OUT.write_text("\n".join(best), encoding="utf-8")
print(f"Wrote {len(best)} lines to {OUT}")
