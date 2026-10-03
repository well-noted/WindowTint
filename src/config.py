"""Configuration load/save and validation for WindowTint. Pure Python, no Windows APIs."""
from __future__ import annotations

import copy
import json
import os
import re
import threading
from pathlib import Path

APP_NAME = "WindowTint"
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
MODIFIER_CHOICES = ["ctrl+alt", "ctrl+shift", "alt+shift", "ctrl+win", "alt+win"]

DEFAULTS = {
    "poll_ms": 500,
    "hotkey_modifiers": "ctrl+alt",      # with 1-9 assigns a project, with 0 returns to automatic
    "hotkey_new_rule": "ctrl+alt+r",     # make a rule from the focused window
    "hotkey_switcher": "ctrl+alt+w",     # open the window switcher
    "color_border": True,
    "border_px": 1,           # 1 = Windows' own thin border; 2-8 draws a thicker frame around the window
    "color_caption": True,
    "alt_tab": False,         # use WindowTint's switcher for Alt+Tab instead of Windows'
    "tab_overlay": True,      # color browser tabs whose title matches a rule
    "sidebar_overlay": True,  # color Claude sidebar chat rows (needs a rule with In-app text)
    "projects": [
        {"name": "Project A", "color": "#e53935"},
        {"name": "Project B", "color": "#1e88e5"},
        {"name": "Project C", "color": "#43a047"},
        {"name": "Project D", "color": "#fb8c00"},
        {"name": "Project E", "color": "#8e24aa"},
    ],
    # Rule: {"project": str, "title": regex, "process": glob, "cwd": path prefix, "ui": regex}
    # "ui" matches text read from inside the app (needs "process" set, e.g. "claude").
    # Empty fields are ignored; all non-empty fields must match.
    "rules": [],
}


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def normalize(raw: dict) -> dict:
    """Merge raw data over defaults, dropping anything malformed."""
    data = copy.deepcopy(DEFAULTS)
    if not isinstance(raw, dict):
        return data
    try:
        data["poll_ms"] = min(5000, max(100, int(raw.get("poll_ms", data["poll_ms"]))))
    except (TypeError, ValueError):
        pass
    try:
        data["border_px"] = min(8, max(1, int(raw.get("border_px", data["border_px"]))))
    except (TypeError, ValueError):
        pass
    mods = raw.get("hotkey_modifiers")
    if mods in MODIFIER_CHOICES:
        data["hotkey_modifiers"] = mods
    import hotkeys
    for key in ("hotkey_new_rule", "hotkey_switcher"):
        value = raw.get(key)
        if isinstance(value, str) and hotkeys.is_valid(value):
            data[key] = hotkeys.normalize_combo(value)
    if data["hotkey_new_rule"] == data["hotkey_switcher"]:      # two actions cannot share one combination
        data["hotkey_new_rule"], data["hotkey_switcher"] = DEFAULTS["hotkey_new_rule"], DEFAULTS["hotkey_switcher"]
    for key in ("color_border", "color_caption", "sidebar_overlay", "tab_overlay", "alt_tab"):
        if isinstance(raw.get(key), bool):
            data[key] = raw[key]

    if "projects" in raw and isinstance(raw["projects"], list):
        projects, seen = [], set()
        for p in raw["projects"]:
            if not isinstance(p, dict):
                continue
            name = str(p.get("name", "")).strip()
            color = str(p.get("color", ""))
            if name and name not in seen and HEX_RE.match(color):
                projects.append({"name": name, "color": color.lower()})
                seen.add(name)
        data["projects"] = projects

    if isinstance(raw.get("rules"), list):
        rules = []
        for r in raw["rules"]:
            if not isinstance(r, dict):
                continue
            rule = {k: str(r.get(k, "")).strip() for k in ("project", "title", "process", "cwd", "ui")}
            if rule["project"] and (rule["title"] or rule["process"] or rule["cwd"] or rule["ui"]):
                rules.append(rule)
        data["rules"] = rules
    return data


class Config:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else app_dir() / "config.json"
        self.data = copy.deepcopy(DEFAULTS)
        self.lock = threading.RLock()

    # -- persistence -------------------------------------------------------
    def load(self) -> None:
        with self.lock:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                self.save()
                return
            except (OSError, ValueError):
                try:
                    os.replace(self.path, self.path.with_suffix(".json.bad"))
                except OSError:
                    pass
                self.save()
                return
            self.data = normalize(raw)

    def save(self) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)

    # -- accessors ---------------------------------------------------------
    @property
    def projects(self) -> list:
        return self.data["projects"]

    @property
    def rules(self) -> list:
        return self.data["rules"]

    def project_names(self) -> list:
        with self.lock:
            return [p["name"] for p in self.projects]

    def color_of(self, name: str) -> str | None:
        with self.lock:
            for p in self.projects:
                if p["name"] == name:
                    return p["color"]
        return None

    # -- mutations ---------------------------------------------------------
    def rename_project(self, old: str, new: str) -> None:
        with self.lock:
            for p in self.projects:
                if p["name"] == old:
                    p["name"] = new
            for r in self.rules:
                if r["project"] == old:
                    r["project"] = new

    def delete_project(self, name: str) -> None:
        with self.lock:
            self.data["projects"] = [p for p in self.projects if p["name"] != name]
            self.data["rules"] = [r for r in self.rules if r["project"] != name]
