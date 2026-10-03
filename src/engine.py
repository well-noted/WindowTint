"""Background loop: find windows, decide their project, apply DWM colors."""
from __future__ import annotations

import json
import os
import queue
import threading
import time

import sys

import overlay
import uia
import winapi
from config import Config, app_dir
from rules import FORCE_NONE, WinInfo, process_matches, resolve_project

REAPPLY_EVERY = 10  # ticks; some apps reset their frame color on state changes


class Engine(threading.Thread):
    def __init__(self, cfg: Config):
        super().__init__(daemon=True, name="WindowTintEngine")
        self.cfg = cfg
        self.procs = winapi.ProcCache()
        self.events: "queue.Queue" = queue.Queue()
        self.manual: dict = {}          # hwnd -> project name | FORCE_NONE
        self.paused = False
        self.last_active: WinInfo | None = None
        self.notify = lambda msg: None  # set by the tray
        self.probe = None               # uia.ContextProbe, started when a rule uses "ui"
        self.tab_overlay = None         # overlay.OverlayManager for browser tab marks
        self.frames = None              # frame_overlay.FrameOverlay, for borders thicker than 1px
        self._frame_colors: dict = {}   # hwnd -> color of every window that currently has a project
        self.overlay = None             # overlay.OverlayManager, draws the sidebar marks
        self.preview_ui_process = ""    # set by the rule editor so in-app text can be previewed
        self.on_new_rule = lambda hwnd: None  # set by main: open the rule editor for a window
        self._ui_error_shown = False
        self._applied: dict = {}        # hwnd -> (key, tick)
        self._snapshot: list = []       # [(WinInfo, project|None, source)]
        self._tick = 0
        self._lock = threading.RLock()
        self._snap_lock = threading.Lock()
        self._wake = threading.Event()      # set to run a pass now instead of at the next poll
        self._dirty: set = set()            # hwnds reported by window events
        self._dirty_lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._state_path = app_dir() / "state.json"
        self._load_state()

    # -- state persistence -------------------------------------------------
    def _load_state(self) -> None:
        """Manual assignments survive an app restart as long as the window still exists
        and still belongs to the same executable."""
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
            for item in raw.get("manual", []):
                hwnd = int(item["hwnd"])
                if not winapi.is_window(hwnd):
                    continue
                pid = self._pid_of(hwnd)
                if pid and self.procs.exe(pid).lower() == str(item.get("exe", "")).lower():
                    self.manual[hwnd] = item["project"] if item["project"] is not None else FORCE_NONE
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def _pid_of(self, hwnd: int) -> int:
        from ctypes import wintypes
        import ctypes
        pid = wintypes.DWORD(0)
        winapi.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def _save_state(self) -> None:
        items = []
        for hwnd, proj in self.manual.items():
            pid = self._pid_of(hwnd)
            items.append({
                "hwnd": hwnd,
                "exe": self.procs.exe(pid) if pid else "",
                "project": None if proj == FORCE_NONE else proj,
            })
        try:
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"manual": items}, indent=2), encoding="utf-8")
            os.replace(tmp, self._state_path)
        except OSError:
            pass

    # -- public API (thread-safe) -----------------------------------------
    def assign(self, hwnd: int, project: str) -> None:
        with self._lock:
            self.manual[hwnd] = project
            self._save_state()

    def force_none(self, hwnd: int) -> None:
        with self._lock:
            self.manual[hwnd] = FORCE_NONE
            self._save_state()

    def auto(self, hwnd: int) -> None:
        with self._lock:
            self.manual.pop(hwnd, None)
            self._save_state()

    def rename_project(self, old: str, new: str) -> None:
        with self._lock:
            for h, p in list(self.manual.items()):
                if p == old:
                    self.manual[h] = new
            self._save_state()

    def delete_project(self, name: str) -> None:
        with self._lock:
            for h, p in list(self.manual.items()):
                if p == name:
                    del self.manual[h]
            self._save_state()

    # The snapshot has its own small lock so the UI and tray threads can read it at any
    # time without waiting for a tick to finish.
    def windows(self) -> list:
        with self._snap_lock:
            return list(self._snapshot)

    def project_of(self, hwnd: int):
        with self._snap_lock:
            for w, proj, src in self._snapshot:
                if w.hwnd == hwnd:
                    return proj, src
        return None, ""

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            self.paused = paused
            if paused:
                self._reset_all_locked()

    def reset_all(self) -> None:
        with self._lock:
            self._reset_all_locked()

    def stop(self) -> None:
        self._stop_evt.set()
        self._wake.set()
        if self.probe:
            self.probe.stop()
        if self.overlay:
            self.overlay.stop()
        if self.tab_overlay:
            self.tab_overlay.stop()
        if self.frames:
            self.frames.stop()
        self.reset_all()

    # -- loop ---------------------------------------------------------------
    def run(self) -> None:
        """Full pass every poll interval; in between, windows named by mark_dirty() are
        re-evaluated on their own, within a few milliseconds of the event."""
        next_full = 0.0
        while not self._stop_evt.is_set():
            self._wake.wait(max(0.0, next_full - time.monotonic()))
            self._wake.clear()
            if self._stop_evt.is_set():
                break
            now = time.monotonic()
            if now >= next_full:
                try:
                    self.tick()
                except Exception:
                    pass  # never let one bad pass kill the loop
                next_full = time.monotonic() + max(0.1, self.cfg.data["poll_ms"] / 1000.0)
            with self._dirty_lock:
                dirty, self._dirty = self._dirty, set()
            for hwnd in dirty:
                try:
                    self.refresh_window(hwnd)
                except Exception:
                    pass
            self._stop_evt.wait(0.02)  # coalesce bursts of events

    def mark_dirty(self, hwnd: int) -> None:
        """Ask for a prompt re-evaluation of one window. Safe to call from any thread."""
        with self._dirty_lock:
            self._dirty.add(hwnd)
        self._wake.set()

    def refresh_window(self, hwnd: int) -> None:
        """Re-evaluate a single window now, without a full enumeration."""
        w = winapi.window_info(hwnd, self.procs)
        if w is None:
            return
        names = self.cfg.project_names()
        with self.cfg.lock:
            rules = list(self.cfg.rules)
        self._attach_ui_text(w, rules)
        with self._lock:
            manual = dict(self.manual)
        proj, src = resolve_project(names, rules, w, manual, lambda: self.procs.cwds(w.pid))
        with self._lock:
            if w.hwnd == winapi.get_foreground():
                self.last_active = w
            self._apply(w.hwnd, None if self.paused else proj)
            with self._snap_lock:
                self._snapshot = [e for e in self._snapshot if e[0].hwnd != w.hwnd] + [(w, proj, src)]
        self._sync_frames()

    def _handle_events(self, names: list) -> list:
        """Apply queued hotkey events. Caller holds self._lock. Returns messages to show."""
        messages = []
        while True:
            try:
                kind, ident, hwnd = self.events.get_nowait()
            except queue.Empty:
                return messages
            if kind != "hotkey" or not hwnd or not winapi.is_window(hwnd) or self._pid_of(hwnd) == os.getpid():
                continue
            if ident == 11:
                messages.append(("newrule", hwnd))
                continue
            if ident == 10:
                self.manual.pop(hwnd, None)
                messages.append("Window color: auto (rules)")
            elif 1 <= ident <= len(names):
                self.manual[hwnd] = names[ident - 1]
                messages.append(f"Window assigned to {names[ident - 1]}")
            else:
                continue
            self._save_state()

    def tick(self) -> None:
        # Slow or cross-process work (window enumeration, process lookups, UI Automation
        # hand-off, notifications) happens outside self._lock. Holding the lock across it
        # would freeze the settings window whenever a tick is running.
        wins = winapi.enum_windows(self.procs)
        names = self.cfg.project_names()
        with self.cfg.lock:
            rules = list(self.cfg.rules)
        me = os.getpid()
        fg = winapi.get_foreground()
        self._update_ui_text(wins, rules)
        self._update_tabs(wins, rules)

        with self._lock:
            self._tick += 1
            messages = self._handle_events(names)
            manual = dict(self.manual)
        for item in messages:
            if isinstance(item, tuple):
                self.on_new_rule(item[1])
            else:
                self.notify(item)

        decisions = []
        for w in wins:
            if w.pid == me:
                continue
            proj, src = resolve_project(names, rules, w, manual, lambda pid=w.pid: self.procs.cwds(pid))
            decisions.append((w, proj, src))

        with self._lock:
            for w, proj, _src in decisions:
                if w.hwnd == fg:
                    self.last_active = w
                self._apply(w.hwnd, None if self.paused else proj)
            with self._snap_lock:
                self._snapshot = decisions

            for hwnd in list(self._applied):
                if not winapi.is_window(hwnd):
                    del self._applied[hwnd]
                    self._frame_colors.pop(hwnd, None)
            dead = [h for h in self.manual if not winapi.is_window(h)]
            for h in dead:
                del self.manual[h]
            if dead:
                self._save_state()
        self._sync_frames()

    def _update_ui_text(self, wins: list, rules: list) -> None:
        """Fill WinInfo.ui for windows that a "ui" rule could apply to. The reading itself
        happens on the probe thread because UI Automation calls are slow."""
        real_rules = [r for r in rules if r.get("ui") and r.get("process")]
        ui_procs = [r["process"] for r in real_rules]
        if self.preview_ui_process:
            ui_procs.append(self.preview_ui_process)
        if not ui_procs:
            if self.probe:
                self.probe.set_targets([])
            if self.overlay:
                self.overlay.set_targets([])
            return
        if self.probe is None:
            self.probe = uia.ContextProbe(on_change=self.mark_dirty, foreground=winapi.get_foreground)
            self.probe.start()
        targets = [w for w in wins if any(process_matches(p, w.exe) for p in ui_procs)]
        self.probe.set_targets([w.hwnd for w in targets])
        for w in targets:
            w.ui = self.probe.get(w.hwnd)
        if self.probe.error and not self._ui_error_shown:
            self._ui_error_shown = True
            self.notify(self.probe.error)

        # Sidebar marks: one colored mark per chat row, using the same rules as the windows.
        marks_on = bool(self.cfg.data.get("sidebar_overlay", True)) and not self.paused and bool(real_rules)
        self.probe.sidebar_enabled = marks_on
        if marks_on and self.overlay is None and sys.platform == "win32":
            self.overlay = overlay.OverlayManager(self.probe.get_sidebar, self.sidebar_color)
            self.overlay.start()
        if self.overlay:
            self.overlay.set_targets([w.hwnd for w in targets] if marks_on else [])

    @staticmethod
    def _tab_rules(rules: list) -> list:
        """Rules that can say something about a single browser tab. A tab only has a title, and
        for a browser the in-app text is that same title, so both fields are tested against it."""
        return [r for r in rules if not r.get("cwd") and (r.get("title") or r.get("process"))
                and (not r.get("ui") or r.get("process"))]

    def _update_tabs(self, wins: list, rules: list) -> None:
        """Browser tab marks: every tab whose title matches a rule gets a colored mark."""
        on = (bool(self.cfg.data.get("tab_overlay", True)) and not self.paused and sys.platform == "win32"
              and bool(self._tab_rules(rules)))
        targets = [w for w in wins if uia.is_browser(w.exe)] if on else []
        uia.log_once(f"tabs-state-{on}-{len(targets)}",
                     f"tab marks: enabled={on}, browser windows={len(targets)}, tab rules={len(self._tab_rules(rules))}")
        if not targets and self.probe is None:
            return
        if self.probe is None:
            self.probe = uia.ContextProbe(on_change=self.mark_dirty, foreground=winapi.get_foreground)
            self.probe.start()
        self.probe.tabs_enabled = bool(targets)
        self.probe.set_tab_targets([w.hwnd for w in targets])
        if targets and self.tab_overlay is None:
            self.tab_overlay = overlay.OverlayManager(self.probe.get_tabs, self.tab_color, style="tab")
            self.tab_overlay.start()
        if self.tab_overlay:
            self.tab_overlay.set_targets([w.hwnd for w in targets])

    def tab_color(self, hwnd: int, text: str):
        """Color for one browser tab, from its title, or None."""
        with self._snap_lock:
            win = next((w for w, _p, _s in self._snapshot if w.hwnd == hwnd), None)
        if win is None:
            return None
        names = self.cfg.project_names()
        with self.cfg.lock:
            rules = self._tab_rules(list(self.cfg.rules))
        tab = WinInfo(-1, win.pid, text, win.exe, win.cls, ui=text)
        project, _src = resolve_project(names, rules, tab, {}, lambda: [])
        return self.cfg.color_of(project) if project else None

    def _attach_ui_text(self, w: WinInfo, rules: list) -> None:
        """Single-window version of the targeting done in _update_ui_text."""
        if self.probe is None:
            return
        ui_procs = [r["process"] for r in rules if r.get("ui") and r.get("process")]
        if self.preview_ui_process:
            ui_procs.append(self.preview_ui_process)
        if any(process_matches(p, w.exe) for p in ui_procs):
            self.probe.add_target(w.hwnd)
            w.ui = self.probe.get(w.hwnd)

    def find_window(self, hwnd: int):
        """WinInfo for a window: from the latest pass if known, otherwise looked up directly."""
        with self._snap_lock:
            for w, _p, _s in self._snapshot:
                if w.hwnd == hwnd:
                    return w
        return winapi.window_info(hwnd, self.procs)

    def switcher_entries(self) -> list:
        """Open windows for the switcher, most recently used first, each with its project color.
        Projects are worked out again right now from each window's current title and the latest in-app
        text, so a conversation or browser tab you just changed is already reflected."""
        from switcher_logic import make_entry
        wins = winapi.enum_windows(self.procs)       # top-level z-order, which is also recency order
        names = self.cfg.project_names()
        with self.cfg.lock:
            rules = list(self.cfg.rules)
        with self._lock:
            manual = dict(self.manual)
        me, out = os.getpid(), []
        for w in wins:
            if w.pid == me:
                continue
            self._attach_ui_text(w, rules)
            project = None
            if not self.paused:
                project, _src = resolve_project(names, rules, w, manual, lambda pid=w.pid: self.procs.cwds(pid))
            entry = make_entry(w, project or "", (self.cfg.color_of(project) if project else "") or "")
            geometry = getattr(winapi, "window_geometry", None)
            if geometry is not None:
                try:
                    entry.aspect, entry.minimized = geometry(w.hwnd)
                except Exception:
                    pass
            out.append(entry)
        return out

    def refresh_all(self) -> None:
        """Re-evaluate every known window now (after rules or projects changed)."""
        for w, _p, _s in self.windows():
            self.mark_dirty(w.hwnd)

    def sidebar_color(self, hwnd: int, text: str):
        """Color for one sidebar chat row ('Project / Chat title' or 'Chat title'), or None."""
        with self._snap_lock:
            win = next((w for w, _p, _s in self._snapshot if w.hwnd == hwnd), None)
        if win is None:
            return None
        names = self.cfg.project_names()
        with self.cfg.lock:
            rules = list(self.cfg.rules)
        row = WinInfo(-1, win.pid, win.title, win.exe, win.cls, ui=text)
        project, _src = resolve_project(names, rules, row, {}, lambda: [])
        return self.cfg.color_of(project) if project else None

    # -- applying colors ------------------------------------------------------
    def _apply(self, hwnd: int, project) -> None:
        if project is None:
            self._frame_colors.pop(hwnd, None)
            if hwnd in self._applied:
                winapi.reset_colors(hwnd)
                del self._applied[hwnd]
            return
        color = self.cfg.color_of(project)
        if color is None:
            return
        self._frame_colors[hwnd] = color
        default = winapi.DWMWA_COLOR_DEFAULT
        use_border = self.cfg.data["color_border"]
        use_caption = self.cfg.data["color_caption"]
        key = (
            winapi.hex_to_colorref(color) if use_border else default,
            winapi.hex_to_colorref(color) if use_caption else default,
            winapi.contrast_colorref(color) if use_caption else default,
        )
        prev = self._applied.get(hwnd)
        stale = prev is None or prev[0] != key or self._tick - prev[1] >= REAPPLY_EVERY
        if not stale:
            # Some apps (Windows Terminal, for one) set their own frame colors again when the
            # window gains or loses focus. Read the colors back so that gets undone right away.
            current = winapi.read_colors(hwnd)
            stale = current is not None and current != key[:2]
        if stale:
            winapi.set_colors(hwnd, *key)
            self._applied[hwnd] = (key, self._tick)

    def _sync_frames(self) -> None:
        """Start, update or park the thick-border overlay to match the settings and the colored windows."""
        px = int(self.cfg.data.get("border_px", 1))
        on = px > 1 and bool(self.cfg.data.get("color_border")) and not self.paused and sys.platform == "win32"
        if on and self.frames is None:
            import frame_overlay
            self.frames = frame_overlay.FrameOverlay(px)
            self.frames.start()
        if self.frames is not None:
            with self._lock:
                targets = dict(self._frame_colors) if on else {}
            self.frames.set_targets(targets, px)

    def _reset_all_locked(self) -> None:
        for hwnd in list(self._applied):
            if winapi.is_window(hwnd):
                winapi.reset_colors(hwnd)
        self._applied.clear()
        self._frame_colors.clear()
