"""Diagnostic: captures the Windows task switcher (Ctrl+Alt+Tab, or Alt+Tab) so its window cards can be read.

Run:  python dump_alttab.py
It counts down 10 seconds. Press Ctrl+Alt+Tab (the switcher stays open until you press Esc or Enter)
and leave it open until the script prints that it is done. Writes alttab_tree.txt."""
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
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [ENUM, wintypes.LPARAM]
HOSTS = ("XamlExplorerHostIslandWindow", "MultitaskingViewFrame", "Windows.UI.Core.CoreWindow", "XamlWindow")

out = []


def line(depth, node):
    out.append(f"{'  ' * depth}{uia._type(node)} name={uia._name(node)[:80]!r} "
               f"id={getattr(node, 'AutomationId', '')[:30]!r} class={uia._class(node)[:40]!r} rect={uia._rect(node)}")


def dump(node, depth=0, count=None, max_depth=14, cap=600):
    count = count if count is not None else [0]
    if count[0] >= cap or depth > max_depth:
        return
    count[0] += 1
    line(depth, node)
    for c in uia._kids(node):
        dump(c, depth + 1, count, max_depth, cap)


print("Press Ctrl+Alt+Tab within 10 seconds and leave the switcher open...")
for i in range(10, 0, -1):
    print(i, end=" ", flush=True)
    time.sleep(1)
print()

hosts = []


def cb(hwnd, _):
    if not user32.IsWindowVisible(hwnd):
        return True
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    cls = buf.value
    user32.GetWindowTextW(hwnd, buf, 256)
    title = buf.value
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    if cls in HOSTS or title in ("Task Switching", "Task View"):
        hosts.append((hwnd, cls, title, (r.left, r.top, r.right, r.bottom)))
    return True


user32.EnumWindows(ENUM(cb), 0)
out.append(f"candidate windows: {[(h[1], h[2], h[3]) for h in hosts]}")
for hwnd, cls, title, rect in hosts:
    out.append(f"=== window {hwnd} class={cls} title={title!r} rect={rect}")
    dump(auto.ControlFromHandle(hwnd))

# What does UI Automation find in the middle of the screen, where the cards are?
w, h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
for fx, fy in ((0.5, 0.5), (0.3, 0.45), (0.7, 0.55)):
    x, y = int(w * fx), int(h * fy)
    out.append(f"--- point ({x},{y}): ancestors from the hit element upward")
    try:
        node, chain = auto.ControlFromPoint(x, y), []
    except Exception as e:
        out.append(f"error {e!r}")
        continue
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

text = "\n".join(out)
Path(__file__).with_name("alttab_tree.txt").write_text(text, encoding="utf-8")
print(text[-2500:])
print("\nDone (press Esc to close the switcher). Written to alttab_tree.txt")
