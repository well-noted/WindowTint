"""Rule matching. Pure Python so it can be tested on any OS."""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Callable, Iterable

FORCE_NONE = "\0none"  # manual override meaning "never tint this window"


@dataclass
class WinInfo:
    hwnd: int
    pid: int
    title: str
    exe: str  # e.g. "WindowsTerminal.exe"
    cls: str = ""
    ui: str = ""  # text read from inside the app, e.g. "Project / Chat title" (see uia.py)


def norm_path(p: str) -> str:
    return p.replace("/", "\\").rstrip("\\").lower()


def cwd_matches(prefix: str, cwd: str) -> bool:
    pre, cur = norm_path(prefix), norm_path(cwd)
    if not pre:
        return False
    return cur == pre or cur.startswith(pre + "\\")


def process_matches(pattern: str, exe: str) -> bool:
    pat = pattern.lower()
    exe = exe.lower()
    stem = exe[:-4] if exe.endswith(".exe") else exe
    return fnmatch.fnmatch(exe, pat) or fnmatch.fnmatch(stem, pat)


def rule_matches(rule: dict, win: WinInfo, get_cwds: Callable[[], Iterable[str]]) -> bool:
    title = rule.get("title", "").strip()
    process = rule.get("process", "").strip()
    cwd = rule.get("cwd", "").strip()
    ui = rule.get("ui", "").strip()
    if not (title or process or cwd or ui):
        return False
    if process and not process_matches(process, win.exe):
        return False
    for pattern, text in ((title, win.title), (ui, win.ui)):
        if pattern:
            try:
                if not re.search(pattern, text, re.IGNORECASE):
                    return False
            except re.error:
                return False
    if cwd and not any(cwd_matches(cwd, c) for c in get_cwds()):
        return False
    return True


def resolve_project(
    project_names: list,
    rules: list,
    win: WinInfo,
    manual: dict,
    get_cwds: Callable[[], Iterable[str]],
):
    """Return (project_name | None, source) where source is 'manual', 'rule' or ''."""
    override = manual.get(win.hwnd)
    if override == FORCE_NONE:
        return None, "manual"
    if override is not None and override in project_names:
        return override, "manual"
    for rule in rules:
        if rule.get("project") in project_names and rule_matches(rule, win, get_cwds):
            return rule["project"], "rule"
    return None, ""
