"""Diagnostic: lists the browser tab strip that WindowTint can read for each open browser window.
Run:  python dump_tabs.py      (writes tabs_dump.txt next to this script and prints it)"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from pathlib import Path

import uiautomation as auto

import uia
import winapi

import time

out = []
procs = winapi.ProcCache()
wins = [w for w in winapi.enum_windows(procs) if uia.is_browser(w.exe)]
# Chromium builds its accessibility tree only after the first UI Automation request,
# so ask once, wait, then read.
for w in wins:
    uia._kids(auto.ControlFromHandle(w.hwnd))
time.sleep(2.5)
for w in wins:
    out.append(f"window {w.hwnd} {w.exe}: {w.title!r}")
    root = auto.ControlFromHandle(w.hwnd)
    strip = uia.find_tabstrip(root) if root else None
    if strip is None:
        out.append("  no tab strip found; control types near the top of the tree:")
        for k in uia._kids(root)[:15]:
            out.append(f"    {uia._type(k)} {uia._name(k)!r}")
        continue
    out.append(f"  tab strip {uia._name(strip)!r} rect={uia._rect(strip)}")
    for k in uia._kids(strip):
        out.append(f"    {uia._type(k)} {uia._name(k)!r} rect={uia._rect(k)} offscreen={uia._offscreen(k)}")
text = "\n".join(out) or "No browser windows found."
Path(__file__).with_name("tabs_dump.txt").write_text(text, encoding="utf-8")
print(text)
