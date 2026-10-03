"""Diagnostic for browser tab marks: runs the real engine for a few seconds and prints each stage.
Run:  python diagnose_tabs.py      (leave a browser window visible, not minimized)"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import sys
import time

import uia
import winapi
from config import Config, app_dir
from engine import Engine

winapi.enable_dpi_awareness()
cfg = Config()
cfg.load()
print("tab_overlay setting:", cfg.data.get("tab_overlay"))
print("projects:", [(p["name"], p["color"]) for p in cfg.projects])
print("rules:")
for r in cfg.rules:
    print("  ", r)
tab_rules = Engine._tab_rules(cfg.rules)
print("rules that can apply to tabs:", tab_rules or "NONE (a tab rule needs Program and/or Window title; rules with a Folder never apply)")

eng = Engine(cfg)
eng.notify = lambda *a, **k: None
for _ in range(3):
    eng.tick()
    time.sleep(0.4)
time.sleep(2.5)
eng.tick()
time.sleep(1.5)
found = False
for w, proj, src in eng.windows():
    if not uia.is_browser(w.exe):
        continue
    found = True
    print(f"\nbrowser window {w.hwnd} {w.exe}: {w.title!r} -> window project {proj!r} ({src})")
    snap = eng.probe.get_tabs(w.hwnd) if eng.probe else None
    if snap is None:
        print("  tab snapshot: NONE (strip not found, or window minimized)")
        continue
    print("  tab strip:", snap.clip)
    for text, rect in snap.items:
        print(f"  tab {text!r} {rect} -> color {eng.tab_color(w.hwnd, text)}")
if not found:
    print("\nNo visible browser windows were found.")
log = app_dir() / "uia.log"
print("\nuia.log:")
print(log.read_text(encoding="utf-8")[-1500:] if log.exists() else "(none)")
eng.stop()
sys.exit(0)
