"""Pure helpers behind the rule editor: turning a window into suggested rule fields and
previewing which open windows a rule would match. No tkinter or Windows imports."""
from __future__ import annotations

import re
from typing import Callable, Iterable

from rules import WinInfo, norm_path, rule_matches

PALETTE = ["#e53935", "#1e88e5", "#43a047", "#fb8c00", "#8e24aa", "#00acc1",
           "#d81b60", "#6d4c41", "#7cb342", "#5e35b1", "#f4511e", "#3949ab"]
BROWSERS = {"chrome", "brave", "vivaldi", "msedge", "opera", "chromium"}
TERMINALS = {"windowsterminal", "wt", "powershell", "pwsh", "cmd", "conhost", "openconsole",
             "wsl", "alacritty", "wezterm-gui", "mintty"}
_RE_SPECIAL = re.compile(r"([\\.^$*+?{}\[\]|()])")
_TITLE_SPLIT = re.compile(r"\s+[-\u2013\u2014|]\s+")


def regex_escape(text: str) -> str:
    """Escape only characters that are special in a regex (re.escape also escapes '-' and spaces)."""
    return _RE_SPECIAL.sub(r"\\\1", text)


def process_stem(exe: str) -> str:
    return exe[:-4] if exe.lower().endswith(".exe") else exe


def is_browser(exe: str) -> bool:
    return process_stem(exe).lower() in BROWSERS


def is_terminal(exe: str) -> bool:
    return process_stem(exe).lower() in TERMINALS


def suggest_title_pattern(title: str) -> str:
    """The part of a window title that usually identifies the document or project, i.e. the
    first segment of 'repo-a - Visual Studio Code' or 'Untitled - Notepad'."""
    parts = [p.strip() for p in _TITLE_SPLIT.split(title) if p.strip()]
    first = (parts[0] if parts else title.strip())[:40].strip()
    return regex_escape(first)


def split_ui_text(ui: str):
    """'Project / Chat title' -> ('Project', 'Chat title'); no project -> ('', ui)."""
    if " / " in ui:
        project, chat = ui.split(" / ", 1)
        return project.strip(), chat.strip()
    return "", ui.strip()


def suggest_ui_patterns(ui: str) -> list:
    """Candidate In-app-text regexes for a window's ui text, broadest first."""
    if not ui:
        return []
    project, _chat = split_ui_text(ui)
    out = []
    if project:
        out.append(f"^{regex_escape(project)} /")
    out.append(f"^{regex_escape(ui)}$")
    return out


def describe_ui_pattern(pattern: str, ui: str) -> str:
    project, _ = split_ui_text(ui)
    if project and pattern == f"^{regex_escape(project)} /":
        return f"every chat in the project '{project}'"
    if pattern == f"^{regex_escape(ui)}$":
        return "only this exact chat"
    return "custom"


def cwd_candidates(cwds: Iterable[str], home: str = "") -> list:
    """Working folders worth offering as a rule prefix: most specific first, with the home
    folder, its parents and the Windows folders dropped."""
    seen, out = set(), []
    home_n = norm_path(home) if home else ""
    for c in cwds:
        n = norm_path(c)
        if not n or n in seen:
            continue
        seen.add(n)
        if n.startswith("c:\\windows"):
            continue
        if home_n and (n == home_n or home_n.startswith(n + "\\") or len(n) <= 3):
            continue
        out.append(c.rstrip("\\/") if len(c) > 3 else c)
    out.sort(key=lambda p: -len(norm_path(p)))
    return out


def defaults_for_window(w: WinInfo, cwds: Iterable[str] = (), home: str = "") -> dict:
    """Suggested rule fields and which of them to switch on, for 'make a rule from this window'."""
    ui_options = suggest_ui_patterns(w.ui)
    folders = cwd_candidates(cwds, home)
    terminal = is_terminal(w.exe)
    return {
        "process": process_stem(w.exe),
        "title": suggest_title_pattern(w.title),
        "ui": ui_options[0] if ui_options else "",
        "ui_options": ui_options,
        "cwd": folders[0] if folders else "",
        "cwd_options": folders,
        "use": {
            "process": True,
            "title": is_browser(w.exe),   # also colors matching browser tabs
            "ui": bool(ui_options) and not is_browser(w.exe),   # a browser's tab title is covered by Window title
            "cwd": terminal and bool(folders),
        },
    }


def build_rule(project: str, use: dict, values: dict) -> dict:
    rule = {"project": project, "title": "", "process": "", "cwd": "", "ui": ""}
    for key in ("title", "process", "cwd", "ui"):
        if use.get(key):
            rule[key] = values.get(key, "").strip()
    return rule


def has_condition(rule: dict) -> bool:
    return any(rule.get(k, "").strip() for k in ("title", "process", "cwd", "ui"))


def validate_rule(rule: dict) -> str:
    """Empty string when fine, otherwise a message for the user."""
    if not rule.get("project"):
        return "Choose a project."
    if not has_condition(rule):
        return "Switch on at least one condition."
    for key, label in (("title", "Window title"), ("ui", "In-app text")):
        if rule.get(key):
            try:
                re.compile(rule[key])
            except re.error as e:
                return f"{label} is not a valid regular expression: {e}"
    if rule.get("ui") and not rule.get("process"):
        return "In-app text needs a program name too (for the Claude app: claude)."
    return ""


def preview_matches(rule: dict, windows: Iterable[WinInfo],
                    cwds_for: Callable[[WinInfo], Iterable[str]]) -> list:
    if not has_condition(rule):
        return []
    return [w for w in windows if rule_matches(rule, w, lambda w=w: cwds_for(w))]


def next_color(used: Iterable[str]) -> str:
    used_n = {u.lower() for u in used}
    for color in PALETTE:
        if color not in used_n:
            return color
    return PALETTE[len(used_n) % len(PALETTE)]


def describe_rule(rule: dict) -> str:
    """A rule in plain words, for the rules list: 'program is chrome and title contains "repo"'."""
    parts = []
    if rule.get("process"):
        parts.append(f"program is {rule['process']}")
    if rule.get("title"):
        parts.append(f'title contains "{rule["title"]}"')
    if rule.get("ui"):
        parts.append(f'in-app text matches "{rule["ui"]}"')
    if rule.get("cwd"):
        parts.append(f"working folder is under {rule['cwd']}")
    return " and ".join(parts) if parts else "no conditions yet"
