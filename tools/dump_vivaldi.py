"""Diagnostic: dumps the UI Automation tree of the first visible Vivaldi (or other browser) window,
including web-page areas, and lists every tab-like control it finds.
Run:  python dump_vivaldi.py [process]      (default: vivaldi)    -> writes vivaldi_tree.txt"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import sys
import time
from collections import deque
from pathlib import Path

import uiautomation as auto

import uia
import winapi

want = (sys.argv[1] if len(sys.argv) > 1 else "vivaldi").lower()
procs = winapi.ProcCache()
wins = [w for w in winapi.enum_windows(procs) if want in w.exe.lower()]
out = []
if not wins:
    out.append(f"No visible window for process '{want}'.")
for w in wins:
    out.append(f"=== window {w.hwnd} {w.exe}: {w.title!r}")
    root = auto.ControlFromHandle(w.hwnd)
    uia._kids(root)       # first request makes Chromium build its accessibility tree
    time.sleep(3)
    queue, seen, tabs = deque([(root, 0)]), 0, []
    while queue and seen < 4000:
        node, depth = queue.popleft()
        seen += 1
        t, n = uia._type(node), uia._name(node)
        rect = uia._rect(node)
        cls = getattr(node, "ClassName", "")
        aid = getattr(node, "AutomationId", "")
        if depth <= 7 or t in ("TabControl", "TabItemControl") or "tab" in n.lower():
            out.append(f"{'  ' * depth}{t} name={n[:60]!r} id={aid[:30]!r} class={cls[:30]!r} rect={rect}")
        if t in ("TabControl", "TabItemControl"):
            tabs.append((depth, t, n, rect))
        if depth < 30:
            for c in uia._kids(node):
                queue.append((c, depth + 1))
    out.append(f"--- scanned {seen} nodes; tab-like controls: {len(tabs)}")
    for d, t, n, r in tabs[:60]:
        out.append(f"    depth {d} {t} {n[:70]!r} {r}")
text = "\n".join(out)
Path(__file__).with_name("vivaldi_tree.txt").write_text(text, encoding="utf-8")
print(text[-3000:])
print("\nFull dump written to vivaldi_tree.txt")
