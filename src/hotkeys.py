"""Hotkey strings like 'ctrl+alt+w': parsing, display and capture from tkinter key events.
Pure Python (no Windows APIs), so it can be tested anywhere."""
from __future__ import annotations

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x1, 0x2, 0x4, 0x8
MOD_NAMES = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}
MOD_ORDER = ("ctrl", "alt", "shift", "win")

SPECIAL_KEYS = {
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "backspace": 0x08, "insert": 0x2D, "delete": 0x2E,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "`": 0xC0,
}
KEY_DISPLAY = {"pageup": "PageUp", "pagedown": "PageDown", "backspace": "Backspace"}

# tkinter keysym -> our key name
KEYSYMS = {
    "space": "space", "Tab": "tab", "ISO_Left_Tab": "tab", "Return": "enter", "KP_Enter": "enter",
    "BackSpace": "backspace", "Insert": "insert", "Delete": "delete", "Home": "home", "End": "end",
    "Prior": "pageup", "Next": "pagedown", "Left": "left", "Up": "up", "Right": "right", "Down": "down",
    "grave": "`", "quoteleft": "`",
}
MODIFIER_KEYSYMS = {"Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L", "Shift_R", "Win_L", "Win_R",
                    "Super_L", "Super_R", "Meta_L", "Meta_R", "Caps_Lock", "Num_Lock"}

# tkinter event.state bits on Windows
STATE_SHIFT, STATE_CONTROL, STATE_ALT = 0x1, 0x4, 0x20000


def key_vk(key: str) -> int:
    """Virtual-key code for a key name, or 0 if unsupported."""
    if len(key) == 1 and key.isalnum() and key.isascii():
        return ord(key.upper())
    if key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        return 0x70 + int(key[1:]) - 1
    return SPECIAL_KEYS.get(key, 0)


def parse_combo(spec: str):
    """'ctrl+alt+w' -> (modifier_flags, vk). Raises ValueError for anything unusable.
    At least one modifier is required so a hotkey never steals a plain key."""
    parts = [p.strip().lower() for p in (spec or "").split("+") if p.strip()]
    if len(parts) < 2:
        raise ValueError("a hotkey needs a modifier and a key")
    *mod_parts, key = parts
    mods = 0
    for m in mod_parts:
        if m not in MOD_NAMES:
            raise ValueError(f"unknown modifier '{m}'")
        mods |= MOD_NAMES[m]
    if not mods:
        raise ValueError("a hotkey needs a modifier")
    vk = key_vk(key)
    if not vk:
        raise ValueError(f"unsupported key '{key}'")
    return mods, vk


def normalize_combo(spec: str) -> str:
    """Canonical lowercase form with modifiers in a fixed order, e.g. 'Alt+Ctrl+W' -> 'ctrl+alt+w'."""
    mods, _vk = parse_combo(spec)
    parts = [name for name in MOD_ORDER if MOD_NAMES[name] & mods]
    return "+".join(parts + [spec.split("+")[-1].strip().lower()])


def is_valid(spec: str) -> bool:
    try:
        parse_combo(spec)
        return True
    except ValueError:
        return False


def display_combo(spec: str) -> str:
    """'ctrl+alt+w' -> 'Ctrl+Alt+W'."""
    if not spec:
        return ""
    out = []
    for p in spec.split("+"):
        p = p.strip().lower()
        out.append(KEY_DISPLAY.get(p) or (p.upper() if len(p) <= 3 and not p.isalpha() or len(p) == 1 else p.capitalize()))
    return "+".join(out)


def combo_from_event(state: int, keysym: str):
    """Hotkey string for a key press, or None while only modifiers are held or the key is unsupported."""
    if keysym in MODIFIER_KEYSYMS:
        return None
    key = KEYSYMS.get(keysym)
    if key is None:
        k = keysym.lower()
        key = k if (len(k) == 1 and k.isalnum() and k.isascii()) or (k.startswith("f") and key_vk(k)) else None
    if key is None or not key_vk(key):
        return None
    mods = []
    if state & STATE_CONTROL:
        mods.append("ctrl")
    if state & STATE_ALT:
        mods.append("alt")
    if state & STATE_SHIFT:
        mods.append("shift")
    if not mods:
        return None
    return "+".join(mods + [key])
