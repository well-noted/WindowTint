"""Pure logic for the window switcher: entries, names, filtering and selection. No Windows or tkinter."""
from __future__ import annotations

import re
from dataclasses import dataclass

APP_NAMES = {
    "chrome": "Chrome", "msedge": "Edge", "vivaldi": "Vivaldi", "brave": "Brave", "firefox": "Firefox",
    "code": "VS Code", "windowsterminal": "Windows Terminal", "wt": "Windows Terminal",
    "powershell": "PowerShell", "pwsh": "PowerShell", "cmd": "Command Prompt", "explorer": "File Explorer",
    "claude": "Claude", "winword": "Word", "excel": "Excel", "powerpnt": "PowerPoint", "slack": "Slack",
    "spotify": "Spotify", "notepad": "Notepad", "applicationframehost": "Windows app",
}


def app_name(exe: str) -> str:
    stem = exe[:-4] if exe.lower().endswith(".exe") else exe
    known = APP_NAMES.get(stem.lower())
    if known:
        return known
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", stem).replace("_", " ").replace("-", " ").split()
    return " ".join(w[:1].upper() + w[1:] for w in words) or stem


@dataclass
class Entry:
    hwnd: int
    title: str        # what the row shows as its main line
    exe: str
    app: str          # friendly program name
    project: str = ""
    color: str = ""   # '#rrggbb' or ''
    pid: int = 0
    aspect: float = 1.6      # window width / height, for the card shape
    minimized: bool = False  # minimized windows have no live preview

    @property
    def subtitle(self) -> str:
        return "  ·  ".join(p for p in (self.project, self.app) if p)

    @property
    def haystack(self) -> str:
        return f"{self.title} {self.app} {self.project} {self.exe}".lower()


def make_entry(win, project: str, color: str) -> Entry:
    """win: rules.WinInfo. In-app text (Claude's 'Project / Chat') replaces a generic window title."""
    title = win.ui or win.title or win.exe
    return Entry(win.hwnd, title, win.exe, app_name(win.exe), project or "", color or "", getattr(win, "pid", 0) or 0)


def filter_entries(entries: list, query: str) -> list:
    """Every word of the query must appear somewhere in the title, program or project. Entries
    whose title, program or project STARTS with a word rank first; recency order is kept otherwise."""
    words = query.lower().split()
    if not words:
        return list(entries)
    scored = []
    for i, e in enumerate(entries):
        hay = e.haystack
        if not all(w in hay for w in words):
            continue
        starts = sum(1 for w in words if any(f.lower().startswith(w) for f in (e.title, e.app, e.project)))
        scored.append((-starts, i, e))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [e for _s, _i, e in scored]


def initial_selection(entries: list, foreground: int) -> int:
    """Like Alt+Tab: start on the previous window, not the one you are already in."""
    return 1 if len(entries) > 1 and entries[0].hwnd == foreground else 0


def move(index: int, delta: int, count: int) -> int:
    return 0 if count <= 0 else (index + delta) % count


def scroll_offset(selected: int, offset: int, visible: int, count: int) -> int:
    """Smallest change to the first visible row that keeps the selection in view."""
    if count <= visible:
        return 0
    if selected < offset:
        offset = selected
    elif selected >= offset + visible:
        offset = selected - visible + 1
    return max(0, min(offset, count - visible))
