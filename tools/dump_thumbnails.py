"""Diagnostic: captures the taskbar hover preview (the window thumbnails above a taskbar button).

Run:  python dump_thumbnails.py
Within 10 seconds, move the mouse onto a taskbar button that has several windows open and leave it
there until the script prints that it is done (about 15 seconds). Writes thumbnails_tree.txt."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import ctypes
import time
from ctypes import wintypes
from pathlib import Path

import uiautomation as auto

import uia
import winapi

winapi.enable_dpi_awareness()
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]

out = []


def line(depth, node):
    out.append(f"{'  ' * depth}{uia._type(node)} name={uia._name(node)[:80]!r} "
               f"id={getattr(node, 'AutomationId', '')[:30]!r} class={uia._class(node)[:40]!r} rect={uia._rect(node)}")


def dump(node, depth=0, limit=[0], max_depth=12, cap=500):
    if limit[0] >= cap or depth > max_depth:
        return
    limit[0] += 1
    line(depth, node)
    for c in uia._kids(node):
        dump(c, depth + 1, limit, max_depth, cap)


print("Hover over a taskbar button with several windows now; capture in 10 seconds...")
for i in range(10, 0, -1):
    print(i, end=" ", flush=True)
    time.sleep(1)
print()

pt = wintypes.POINT()
user32.GetCursorPos(ctypes.byref(pt))
tray = user32.FindWindowW("Shell_TrayWnd", None)
tr = wintypes.RECT()
user32.GetWindowRect(tray, ctypes.byref(tr))
out.append(f"cursor=({pt.x},{pt.y}) taskbar rect=({tr.left},{tr.top},{tr.right},{tr.bottom})")

# What does UI Automation find above the taskbar button, where the thumbnails are?
for dy in (50, 100, 150, 200, 260):
    y = tr.top - dy
    try:
        ctl = auto.ControlFromPoint(pt.x, y)
    except Exception as e:
        out.append(f"point ({pt.x},{y}): error {e!r}")
        continue
    out.append(f"--- point ({pt.x},{y}): ancestors from the hit element upward")
    node, chain = ctl, []
    for _ in range(14):
        if node is None:
            break
        chain.append(node)
        try:
            node = node.GetParentControl()
        except Exception:
            break
    for depth, n in enumerate(chain):
        line(depth, n)
    # subtree of the highest ancestor that is clearly smaller than the screen: the preview container
    screen_w = tr.right - tr.left
    container = next((n for n in reversed(chain) if (r := uia._rect(n)) and 0 < r[2] - r[0] < screen_w * 0.8
                      and r[3] - r[1] > 0), None)
    if container is not None and dy == 100:
        out.append("--- subtree of the preview container")
        dump(container, 0, [0], 14, 400)

text = "\n".join(out)
Path(__file__).with_name("thumbnails_tree.txt").write_text(text, encoding="utf-8")
print(text[-2500:])
print("\nDone. Written to thumbnails_tree.txt")
