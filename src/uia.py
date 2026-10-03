"""Reads the open conversation (and its project) and the sidebar chat list out of the Claude
desktop app through Windows UI Automation. Rules use this text, and the sidebar overlay uses
the row positions.

What the Claude window exposes (verified from a tree dump of the real app):
  - a DocumentControl with AutomationId "RootWebArea" whose Name is "<chat title> - Claude"
  - in the header, a breadcrumb "<project> / <chat title>": a HyperlinkControl (the project)
    followed by a TextControl named "/". A chat outside any project has no such breadcrumb.
  - a sidebar (a group with AutomationId "frame-peek-popover") listing chats. Each chat is a
    ButtonControl whose Name is "<status> <title>" and which holds a status image and a group
    with the title text. Chats inside a project sit in a ListControl whose preceding sibling
    holds the project name.

Only the standard UI Automation tree is used (no injection into the app).
"""
from __future__ import annotations

import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field

DOC_MAX_DEPTH = 16
CRUMB_MAX_DEPTH = 10
NODE_CAP = 1500
SIDEBAR_ID = "frame-peek-popover"
SIDEBAR_NODE_CAP = 4000
RESCAN_EVERY = 3.0  # seconds between full structural scans of the sidebar
FULL_READ_EVERY = 2.0  # seconds between full position reads when nothing seems to change


def _kids(ctl) -> list:
    try:
        return list(ctl.GetChildren())
    except Exception:
        return []


def _type(ctl) -> str:
    try:
        return ctl.ControlTypeName
    except Exception:
        return ""


def _name(ctl) -> str:
    try:
        return ctl.Name or ""
    except Exception:
        return ""


def _crumb_in(kids: list) -> str:
    """Project name if these siblings contain  <Hyperlink> '/'  in that order, else ''."""
    for i in range(1, len(kids)):
        if _type(kids[i]) == "TextControl" and _name(kids[i]) == "/" and _type(kids[i - 1]) == "HyperlinkControl":
            return _name(kids[i - 1])
    return ""


def compose(title: str, project: str) -> str:
    title = re.sub(r"\s+-\s+Claude$", "", title or "").strip()
    return " / ".join(p for p in (project, title) if p)


def log_once(key: str, text: str, _seen=set()) -> None:
    """Append a diagnostic line to %APPDATA%\\WindowTint\\uia.log, once per key."""
    if key in _seen:
        return
    _seen.add(key)
    try:
        from config import app_dir
        with open(app_dir() / "uia.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
    except Exception:
        pass


class _State:
    __slots__ = ("doc", "name", "header")

    def __init__(self, doc, name, header):
        self.doc, self.name, self.header = doc, name, header


class Reader:
    """`auto` is the uiautomation module (or any object with ControlFromHandle)."""

    def __init__(self, auto):
        self.auto = auto
        self._state: dict = {}

    def forget(self, hwnd: int) -> None:
        self._state.pop(hwnd, None)

    def read(self, hwnd: int):
        """Returns the context string, '' if the window has no readable page, None on error."""
        st = self._state.get(hwnd)
        if st is not None:
            try:
                name = st.doc.Name
                if name == st.name:  # same chat as last time: cheap path
                    if st.header is None:
                        return compose(name, "")
                    project = _crumb_in(_kids(st.header))
                    if project:
                        return compose(name, project)
                # chat changed, or the breadcrumb disappeared: re-read inside the same page element
                header, project = self._find_breadcrumb(st.doc)
                title = _name(st.doc)
                self._state[hwnd] = _State(st.doc, title, header)
                return compose(title, project)
            except Exception:
                self._state.pop(hwnd, None)

        root = self.auto.ControlFromHandle(hwnd)
        if not root:
            return None
        doc = self._find_doc(root)
        if doc is None:
            self._state.pop(hwnd, None)
            return ""
        header, project = self._find_breadcrumb(doc)
        title = _name(doc)
        self._state[hwnd] = _State(doc, title, header)
        return compose(title, project)

    # -- tree searches -------------------------------------------------------
    def _find_doc(self, root):
        queue, seen = deque([(root, 0)]), 0
        while queue and seen < NODE_CAP:
            node, depth = queue.popleft()
            seen += 1
            for child in _kids(node):
                if (_type(child) == "DocumentControl" and _name(child)
                        and getattr(child, "AutomationId", "") == "RootWebArea"):
                    return child
                if depth + 1 < DOC_MAX_DEPTH:
                    queue.append((child, depth + 1))
        return None

    def _find_breadcrumb(self, doc):
        queue, seen = deque([(doc, 0)]), 0
        while queue and seen < NODE_CAP:
            node, depth = queue.popleft()
            seen += 1
            kids = _kids(node)
            project = _crumb_in(kids)
            if project:
                return node, project
            if depth < CRUMB_MAX_DEPTH:
                for child in kids:
                    queue.append((child, depth + 1))
        return None, ""


# ---------------------------------------------------------------------------
# sidebar: chat rows and their screen positions
# ---------------------------------------------------------------------------
@dataclass
class Row:
    project: str
    title: str
    elem: object = field(repr=False, compare=False)

    @property
    def text(self) -> str:
        """Same shape as the window-level text, so one set of rules colors both."""
        return " / ".join(p for p in (self.project, self.title) if p)


@dataclass
class Snapshot:
    hwnd: int
    clip: tuple      # (left, top, right, bottom) of the visible sidebar list, screen pixels
    items: list      # [(row_text, (left, top, right, bottom))], already clipped


def _rect(ctl):
    try:
        r = ctl.BoundingRectangle
        return (int(r.left), int(r.top), int(r.right), int(r.bottom))
    except Exception:
        return None


def _empty(r) -> bool:
    return r is None or r[2] <= r[0] or r[3] <= r[1]


def _intersect(a, b):
    return (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))


def _offscreen(ctl) -> bool:
    try:
        return bool(ctl.IsOffscreen)
    except Exception:
        return False


def _scrollable(ctl) -> bool:
    try:
        pattern = ctl.GetScrollPattern()
        return bool(pattern) and bool(pattern.VerticallyScrollable)
    except Exception:
        return False


def _first_text(node, depth: int = 0, limit: int = 6) -> str:
    for c in _kids(node):
        if _type(c) == "TextControl" and _name(c):
            return _name(c)
        if depth < limit:
            found = _first_text(c, depth + 1, limit)
            if found:
                return found
    return ""


def _row_title(btn) -> str:
    """Chat title if this button is a sidebar chat row, else ''. A row's Name is
    '<status> <title>' ('Idle ...', 'Mark as unread ...', and whatever the app shows while a
    reply is streaming) and it holds a group containing the title text. Project headers (Name
    equals the text) and navigation buttons (no such group) do not match. The status glyph is
    deliberately not checked: its control type changes while a chat is running."""
    title = ""
    for k in _kids(btn):
        if _type(k) == "GroupControl":
            title = _first_text(k, limit=3)
            if title:
                break
    name = _name(btn)
    if title and name != title and name.endswith(title):
        return title
    return ""


def find_sidebar(root):
    queue, seen = deque([(root, 0)]), 0
    while queue and seen < NODE_CAP:
        node, depth = queue.popleft()
        seen += 1
        for child in _kids(node):
            if getattr(child, "AutomationId", "") == SIDEBAR_ID:
                return child
            if depth + 1 < 24:
                queue.append((child, depth + 1))
    return None


def scan_sidebar(sidebar):
    """Returns (rows, clip_element). clip_element is the nearest scrollable ancestor of the
    first chat row (the visible list area), or the whole sidebar when none is found."""
    rows: list = []
    first_path: list = []
    budget = [SIDEBAR_NODE_CAP]

    def walk(node, project, path):
        if budget[0] <= 0:
            return
        budget[0] -= 1
        kids = _kids(node)
        for i, k in enumerate(kids):
            t = _type(k)
            if t == "ButtonControl":
                title = _row_title(k)
                if title:
                    if not rows:
                        first_path.extend(path + [node])
                    rows.append(Row(project, title, k))
                    continue
            if t == "ListControl":
                header = _first_text(kids[i - 1]) if i > 0 else ""
                walk(k, header or project, path + [node])
            else:
                walk(k, project, path + [node])

    walk(sidebar, "", [])
    clip = next((a for a in reversed(first_path) if _scrollable(a)), None)
    return rows, clip if clip is not None else sidebar


class SidebarTracker:
    """Keeps chat-row positions up to date cheaply. The expensive structural scan runs every
    few seconds; between scans only two rectangles are read, and the full set of positions is
    re-read only when one of them moved (scroll, window move or resize). If cached rows have
    gone stale (the app re-rendered them, as it does while a reply streams in), it rescans at
    once instead of waiting."""

    class _Win:
        def __init__(self):
            self.rows, self.clip_elem = [], None
            self.scanned_at = self.full_at = -1e9
            self.ref = self.clip = None
            self.snapshot = None

    def __init__(self, auto):
        self.auto = auto
        self._wins: dict = {}

    def forget(self, hwnd: int) -> None:
        self._wins.pop(hwnd, None)

    def _rescan(self, hwnd: int, w, now: float) -> bool:
        root = self.auto.ControlFromHandle(hwnd)
        sidebar = find_sidebar(root) if root else None
        if sidebar is None:
            return False
        w.rows, w.clip_elem = scan_sidebar(sidebar)
        w.scanned_at = now
        return True

    def _read_all(self, hwnd: int, w, now: float):
        """Returns (snapshot | None, stale_count)."""
        clip = _rect(w.clip_elem)
        if _empty(clip):
            return None, len(w.rows)
        items, stale = [], 0
        for row in w.rows:
            r = _rect(row.elem)
            if r is None:
                stale += 1          # element no longer valid
                continue
            if _offscreen(row.elem) or _empty(r):
                continue
            r = _intersect(r, clip)
            if not _empty(r):
                items.append((row.text, r))
        w.ref, w.clip, w.full_at = _rect(w.rows[0].elem), clip, now
        return Snapshot(hwnd, clip, items), stale

    def update(self, hwnd: int, now: float):
        w = self._wins.setdefault(hwnd, self._Win())
        rescanned = False
        if now - w.scanned_at >= RESCAN_EVERY:
            if not self._rescan(hwnd, w, now):
                self._wins[hwnd] = self._Win()
                self._wins[hwnd].scanned_at = now
                return None
            rescanned = True
        if not w.rows:
            w.snapshot = None
            return None
        if not rescanned and w.snapshot is not None and now - w.full_at < FULL_READ_EVERY:
            if (_rect(w.rows[0].elem), _rect(w.clip_elem)) == (w.ref, w.clip):
                return w.snapshot
        snapshot, stale = self._read_all(hwnd, w, now)
        if stale and not rescanned:
            if self._rescan(hwnd, w, now) and w.rows:
                snapshot, _ = self._read_all(hwnd, w, now)
        w.snapshot = snapshot
        return snapshot


# ---------------------------------------------------------------------------
# browser tab strips (Chrome, Brave, Vivaldi and other Chromium browsers)
# ---------------------------------------------------------------------------
TAB_RESCAN_EVERY = 1.0   # seconds between structural scans of the tab strip
TAB_FULL_READ_EVERY = 0.5  # seconds between re-reads of every tab rectangle
TAB_NODE_CAP = 1200
TAB_WEB_NODE_CAP = 3000
BROWSER_PROCESSES = ("chrome", "brave", "vivaldi", "msedge", "opera", "chromium")


def is_browser(exe: str) -> bool:
    stem = exe[:-4] if exe.lower().endswith(".exe") else exe
    return stem.lower() in BROWSER_PROCESSES


_TAB_SUFFIX = re.compile(r"\s+-\s+Memory usage\s+-\s+[\d.,]+\s*\w+$")


def tab_title(name: str) -> str:
    """Tab name without the ' - Memory usage - 22.7 MB' hover text Edge and Vivaldi append."""
    return _TAB_SUFFIX.sub("", name)


def _class(ctl) -> str:
    try:
        return getattr(ctl, "ClassName", "") or ""
    except Exception:
        return ""


def find_tabstrip(root):
    """The browser's own tab strip.

    Chrome, Edge and Brave draw it natively: a TabControl holding TabItemControl children outside
    the web page. Web page content (the DocumentControl subtree) is skipped there so a site's own
    tabs are never picked. Vivaldi draws its tab bar as web content, inside a DocumentControl, as a
    TabControl with the CSS class "tab-strip"; that is looked for only when the native search finds
    nothing, and only by that class."""
    queue, seen = deque([(root, 0)]), 0
    while queue and seen < TAB_NODE_CAP:
        node, depth = queue.popleft()
        seen += 1
        kids = _kids(node)
        if _type(node) == "TabControl" and any(_type(k) == "TabItemControl" for k in kids):
            return node
        for child in kids:
            if _type(child) == "DocumentControl" or depth + 1 >= 12:
                continue
            queue.append((child, depth + 1))
    queue, seen = deque([(root, 0)]), 0
    while queue and seen < TAB_WEB_NODE_CAP:
        node, depth = queue.popleft()
        seen += 1
        kids = _kids(node)
        if _type(node) == "TabControl" and "tab-strip" in _class(node) and any(
                _type(k) == "TabItemControl" for k in kids):
            return node
        if depth + 1 < 30:
            queue.extend((c, depth + 1) for c in kids)
    return None


class TabTracker:
    """Tab titles and screen rectangles for browser windows. The strip is small, so a scan
    reads every tab; between scans only the first and last tab rectangles are checked, and
    everything is re-read at once if either moved (tab opened, closed, dragged, window resized)."""

    class _Win:
        def __init__(self):
            self.strip = None
            self.tabs = []                 # [(name, element)]
            self.scanned_at = -1e9
            self.ref = None
            self.full_at = -1e9
            self.snapshot = None

    def __init__(self, auto):
        self.auto = auto
        self._wins: dict = {}

    def forget(self, hwnd: int) -> None:
        self._wins.pop(hwnd, None)

    def _scan(self, hwnd: int, w, now: float) -> bool:
        if w.strip is None:
            root = self.auto.ControlFromHandle(hwnd)
            w.strip = find_tabstrip(root) if root else None
            if w.strip is None:
                return False
        tabs = [(tab_title(_name(k)), k) for k in _kids(w.strip) if _type(k) == "TabItemControl"]
        if not tabs:
            w.strip = None       # strip was rebuilt; look it up again next time
            return False
        w.tabs, w.scanned_at = tabs, now
        return True

    def _read_all(self, hwnd: int, w, now: float):
        clip = _rect(w.strip)
        if _empty(clip) or clip[0] <= -30000:    # collapsed, or the window is minimized (parked at -32000)
            return None
        items = []
        for name, elem in w.tabs:
            r = _rect(elem)
            if r is None or _offscreen(elem) or _empty(r) or r[0] <= -30000:
                continue
            r = _intersect(r, clip)
            if not _empty(r):
                items.append((name, r))
        w.ref, w.full_at = (_rect(w.tabs[0][1]), _rect(w.tabs[-1][1]), clip), now
        return Snapshot(hwnd, clip, items)

    def update(self, hwnd: int, now: float):
        w = self._wins.setdefault(hwnd, self._Win())
        scanned = False
        if now - w.scanned_at >= TAB_RESCAN_EVERY:
            if not self._scan(hwnd, w, now):
                w.strip, w.snapshot, w.scanned_at = None, None, now
                return None
            scanned = True
        if w.strip is None:      # last scan found nothing; wait for the next one rather than searching every cycle
            return None
        if not scanned and w.snapshot is not None and now - w.full_at < TAB_FULL_READ_EVERY:
            if (_rect(w.tabs[0][1]), _rect(w.tabs[-1][1]), _rect(w.strip)) == w.ref:
                return w.snapshot
        w.snapshot = self._read_all(hwnd, w, now)
        return w.snapshot


class ContextProbe(threading.Thread):
    """Background thread that keeps hwnd -> context-string (and sidebar snapshot) maps for the
    windows the engine asks about. UI Automation calls into another process are slow, so they
    stay off the engine thread."""

    def __init__(self, interval: float = 1.0, fast: float = 0.15, on_change=None, foreground=None,
                 fg_interval: float = 0.25):
        super().__init__(daemon=True, name="WindowTintUIA")
        self.interval, self.fast = interval, fast
        self.foreground = foreground      # callable -> hwnd of the active window; that one is read more often
        self.fg_interval = fg_interval
        self.on_change = on_change  # called with the hwnd when its context text changes
        self._wake = threading.Event()
        self.error = ""
        self.sidebar_enabled = False
        self._targets: set = set()
        self._values: dict = {}
        self._sidebars: dict = {}
        self.tabs_enabled = False
        self._tab_targets: set = set()
        self._tabs: dict = {}
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()

    def set_targets(self, hwnds) -> None:
        with self._lock:
            added = set(hwnds) - self._targets
            self._targets = set(hwnds)
            for store in (self._values, self._sidebars):
                for h in [h for h in store if h not in self._targets]:
                    del store[h]
        if added:
            self._wake.set()  # read new windows right away

    def add_target(self, hwnd: int) -> None:
        with self._lock:
            new = hwnd not in self._targets
            self._targets.add(hwnd)
        if new:
            self._wake.set()

    def get(self, hwnd: int) -> str:
        with self._lock:
            return self._values.get(hwnd, "")

    def set_tab_targets(self, hwnds) -> None:
        with self._lock:
            added = set(hwnds) - self._tab_targets
            self._tab_targets = set(hwnds)
            for h in [h for h in self._tabs if h not in self._tab_targets]:
                del self._tabs[h]
        if added:
            self._wake.set()

    def get_tabs(self, hwnd: int):
        with self._lock:
            return self._tabs.get(hwnd)

    def get_sidebar(self, hwnd: int):
        with self._lock:
            return self._sidebars.get(hwnd)

    def stop(self) -> None:
        self._stop_evt.set()
        self._wake.set()

    def _fg_run(self) -> None:
        """Fast lane: re-reads the context text of the ACTIVE window about 8 times a second."""
        try:
            import uiautomation as auto
        except ImportError:
            return
        with auto.UIAutomationInitializerInThread():
            reader = Reader(auto)
            while not self._stop_evt.is_set():
                try:
                    fg = self.foreground() if self.foreground else 0
                except Exception:
                    fg = 0
                with self._lock:
                    wanted = fg in self._targets
                if wanted:
                    try:
                        value = reader.read(fg)
                    except Exception as e:
                        reader.forget(fg)
                        log_once("ctx-fg", f"active window context read failed: {e!r}")
                        value = None
                    if value is not None:
                        changed = False
                        with self._lock:
                            if fg in self._targets:
                                changed = self._values.get(fg) != value
                                self._values[fg] = value
                        if changed and self.on_change:
                            self.on_change(fg)
                self._stop_evt.wait(0.12)

    def run(self) -> None:
        try:
            import uiautomation as auto
        except ImportError:
            self.error = "The 'uiautomation' package is not installed (pip install uiautomation)."
            return
        if self.foreground:
            threading.Thread(target=self._fg_run, daemon=True, name="WindowTintUIAFast").start()
        with auto.UIAutomationInitializerInThread():
            reader, tracker, tab_tracker = Reader(auto), SidebarTracker(auto), TabTracker(auto)
            last_ctx: dict = {}
            while not self._stop_evt.is_set():
                with self._lock:
                    targets = list(self._targets)
                want_sidebar = self.sidebar_enabled
                now = time.monotonic()
                try:
                    fg = self.foreground() if self.foreground else 0
                except Exception:
                    fg = 0
                for hwnd in targets:
                    if self.foreground and hwnd == fg:
                        pass                      # the fast lane reads the active window
                    elif now - last_ctx.get(hwnd, -1e9) >= self.interval:
                        last_ctx[hwnd] = now
                        try:
                            value = reader.read(hwnd)
                        except Exception as e:
                            reader.forget(hwnd)
                            log_once("ctx", f"context read failed: {e!r}")
                            value = None
                        if value is not None:
                            changed = False
                            with self._lock:
                                if hwnd in self._targets:
                                    changed = self._values.get(hwnd) != value
                                    self._values[hwnd] = value
                            if changed and self.on_change:
                                self.on_change(hwnd)  # let the engine recolor the window now
                    if want_sidebar:
                        try:
                            snap = tracker.update(hwnd, now)
                        except Exception as e:
                            tracker.forget(hwnd)
                            log_once("sidebar", f"sidebar read failed: {e!r}")
                            snap = None
                        with self._lock:
                            if hwnd in self._targets:
                                self._sidebars[hwnd] = snap
                    else:
                        with self._lock:
                            self._sidebars.pop(hwnd, None)
                with self._lock:
                    tab_targets = list(self._tab_targets) if self.tabs_enabled else []
                    if not self.tabs_enabled:
                        self._tabs.clear()
                for hwnd in tab_targets:
                    try:
                        snap = tab_tracker.update(hwnd, now)
                    except Exception as e:
                        tab_tracker.forget(hwnd)
                        log_once("tabs", f"tab strip read failed: {e!r}")
                        snap = None
                    if snap is None:
                        log_once(f"tabs-none-{hwnd}", f"no tab strip found in window {hwnd}")
                    with self._lock:
                        if hwnd in self._tab_targets:
                            self._tabs[hwnd] = snap
                quick = want_sidebar or tab_targets or fg in targets
                self._wake.wait(self.fast if quick else self.interval)
                self._wake.clear()
