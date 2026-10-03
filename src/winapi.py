"""Windows-only helpers: window enumeration, DWM color calls, hotkeys, autostart."""
from __future__ import annotations

import ctypes
import os
import queue
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

import psutil

from rules import WinInfo

user32 = ctypes.WinDLL("user32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE

GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000

DWMWA_CLOAKED = 14
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36
DWMWA_COLOR_DEFAULT = 0xFFFFFFFF

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
ERROR_ALREADY_EXISTS = 183

IGNORED_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow"}


# --------------------------------------------------------------------------
# colors
# --------------------------------------------------------------------------
def hex_to_colorref(hex_color: str) -> int:
    r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
    return r | (g << 8) | (b << 16)


def contrast_colorref(hex_color: str) -> int:
    """Black or white text, whichever reads better on the given background."""
    r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
    lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return 0x000000 if lum > 0.6 else 0xFFFFFF


def _set_attr(hwnd: int, attr: int, value: int) -> bool:
    v = wintypes.DWORD(value)
    return dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v)) == 0


def set_colors(hwnd: int, border: int, caption: int, text: int) -> None:
    """Each argument is a COLORREF or DWMWA_COLOR_DEFAULT. Failures are ignored
    (older Windows builds and some elevated windows reject the call)."""
    _set_attr(hwnd, DWMWA_BORDER_COLOR, border)
    _set_attr(hwnd, DWMWA_CAPTION_COLOR, caption)
    _set_attr(hwnd, DWMWA_TEXT_COLOR, text)


def read_colors(hwnd: int):
    """(border, caption) as currently set on the window, or None if Windows won't say."""
    values = []
    for attr in (DWMWA_BORDER_COLOR, DWMWA_CAPTION_COLOR):
        v = wintypes.DWORD(0)
        if dwmapi.DwmGetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v)) != 0:
            return None
        values.append(v.value)
    return tuple(values)


def reset_colors(hwnd: int) -> None:
    set_colors(hwnd, DWMWA_COLOR_DEFAULT, DWMWA_COLOR_DEFAULT, DWMWA_COLOR_DEFAULT)


def is_window(hwnd: int) -> bool:
    return bool(user32.IsWindow(hwnd))


def get_foreground() -> int:
    return user32.GetForegroundWindow() or 0


# --------------------------------------------------------------------------
# process info
# --------------------------------------------------------------------------
class ProcCache:
    """Caches exe names and working directories (cwd of the process tree)."""

    def __init__(self):
        self._exe: dict = {}
        self._cwds: dict = {}
        self._lock = threading.Lock()

    def exe(self, pid: int) -> str:
        now = time.monotonic()
        with self._lock:
            hit = self._exe.get(pid)
            if hit and now - hit[1] < 30:
                return hit[0]
        try:
            name = psutil.Process(pid).name()
        except (psutil.Error, OSError):
            name = ""
        with self._lock:
            self._exe[pid] = (name, now)
        return name

    def cwds(self, pid: int) -> list:
        now = time.monotonic()
        with self._lock:
            hit = self._cwds.get(pid)
            if hit and now - hit[1] < 2.0:
                return hit[0]
        out = []
        try:
            root = psutil.Process(pid)
            for proc in [root] + root.children(recursive=True):
                try:
                    out.append(proc.cwd())
                except (psutil.Error, OSError):
                    continue
        except (psutil.Error, OSError):
            pass
        with self._lock:
            self._cwds[pid] = (out, now)
            if len(self._cwds) > 500:
                self._cwds.clear()
            if len(self._exe) > 2000:
                self._exe.clear()
        return out


# --------------------------------------------------------------------------
# window enumeration
# --------------------------------------------------------------------------
def _text(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def _class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_info(hwnd: int, procs: ProcCache):
    """WinInfo for a visible, un-cloaked, unowned top-level window with a title, else None."""
    try:
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, GW_OWNER):
            return None
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if ex & WS_EX_TOOLWINDOW and not ex & WS_EX_APPWINDOW:
            return None
        cloaked = wintypes.DWORD(0)
        dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value:
            return None
        # Skip our own windows BEFORE reading their text: GetWindowText on a window owned
        # by this process sends a message to the UI thread, which can deadlock if that
        # thread is waiting on the engine.
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid():
            return None
        title = _text(hwnd)
        if not title:
            return None
        cls = _class(hwnd)
        if cls in IGNORED_CLASSES:
            return None
        return WinInfo(hwnd=int(hwnd), pid=pid.value, title=title, exe=procs.exe(pid.value), cls=cls)
    except Exception:
        return None


def enum_windows(procs: ProcCache) -> list:
    """All windows that window_info() accepts."""
    found = []

    def cb(hwnd, _lparam):
        info = window_info(hwnd, procs)
        if info is not None:
            found.append(info)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


# --------------------------------------------------------------------------
# window events: react the moment a window appears, is renamed, or gets focus
# --------------------------------------------------------------------------
EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_SYSTEM_MINIMIZEEND = 0x0017
EVENT_OBJECT_SHOW = 0x8002
EVENT_OBJECT_NAMECHANGE = 0x800C
EVENT_OBJECT_UNCLOAKED = 0x8018
WATCHED_EVENTS = (EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_MINIMIZEEND, EVENT_OBJECT_SHOW,
                  EVENT_OBJECT_NAMECHANGE, EVENT_OBJECT_UNCLOAKED)
WINEVENT_OUTOFCONTEXT, WINEVENT_SKIPOWNPROCESS = 0x0, 0x2
OBJID_WINDOW = 0

WINEVENTPROC = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG,
                                  wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
user32.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE, WINEVENTPROC,
                                   wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
user32.SetWinEventHook.restype = wintypes.HANDLE
user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]


class WinEventThread(threading.Thread):
    """Calls sink(hwnd) whenever a top-level window is shown, un-cloaked, renamed, un-minimized
    or activated. The engine uses it to color a window within milliseconds instead of waiting
    for the next polling pass."""

    def __init__(self, sink):
        super().__init__(daemon=True, name="WindowTintWinEvents")
        self.sink = sink
        self._tid = 0
        self._ready = threading.Event()
        self._hooks: list = []
        self._proc = WINEVENTPROC(self._on_event)  # keep a reference or the callback is freed

    def _on_event(self, _hook, _event, hwnd, id_object, id_child, _thread, _time):
        if hwnd and id_object == OBJID_WINDOW and id_child == 0:
            try:
                self.sink(int(hwnd))
            except Exception:
                pass

    def run(self):
        self._tid = kernel32.GetCurrentThreadId()
        for event in WATCHED_EVENTS:
            hook = user32.SetWinEventHook(event, event, None, self._proc, 0, 0,
                                          WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS)
            if hook:
                self._hooks.append(hook)
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass  # out-of-context hooks are delivered through this thread's message queue
        for hook in self._hooks:
            user32.UnhookWinEvent(hook)

    def stop(self):
        self._ready.wait(2)
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)


# --------------------------------------------------------------------------
# global hotkeys
# --------------------------------------------------------------------------
def parse_modifiers(spec: str) -> int:
    table = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}
    mods = 0
    for part in spec.lower().split("+"):
        mods |= table.get(part.strip(), 0)
    return mods | MOD_NOREPEAT


WM_APP = 0x8000


class HotkeyThread(threading.Thread):
    """Registers the hotkeys from the config: MOD+1..9 (assign the focused window to project N),
    MOD+0 (back to automatic), and one combination each for 'new rule from this window' and the
    window switcher. reload() re-registers them after the settings change.
    Posts ('hotkey', id, foreground_hwnd) tuples to the given queue; ids listed in `direct` call
    direct[id](foreground_hwnd) immediately instead."""

    IDS = 12

    def __init__(self, cfg, events: "queue.Queue", direct: dict | None = None):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.events = events
        self.direct = direct or {}
        self.failed: list = []
        self._tid = 0
        self._ready = threading.Event()
        self._reloaded = threading.Event()

    def _register(self) -> None:
        import hotkeys
        for n in range(1, self.IDS + 1):
            user32.UnregisterHotKey(None, n)
        self.failed = []
        with self.cfg.lock:
            digits = parse_modifiers(self.cfg.data["hotkey_modifiers"])
            combos = [(11, "new rule", self.cfg.data["hotkey_new_rule"]),
                      (12, "window switcher", self.cfg.data["hotkey_switcher"])]
        for n in range(1, 10):
            if not user32.RegisterHotKey(None, n, digits, 0x30 + n):
                self.failed.append(f"modifier+{n}")
        if not user32.RegisterHotKey(None, 10, digits, 0x30):
            self.failed.append("modifier+0")
        for ident, name, spec in combos:
            try:
                mods, vk = hotkeys.parse_combo(spec)
            except ValueError:
                self.failed.append(name)
                continue
            if not user32.RegisterHotKey(None, ident, mods | MOD_NOREPEAT, vk):
                self.failed.append(f"{name} ({hotkeys.display_combo(spec)})")

    def run(self):
        self._tid = kernel32.GetCurrentThreadId()
        self._register()
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_APP:
                self._register()
                self._reloaded.set()
            elif msg.message == WM_HOTKEY:
                ident, fg = int(msg.wParam), get_foreground()
                if ident in self.direct:
                    self.direct[ident](fg)
                else:
                    self.events.put(("hotkey", ident, fg))
        for n in range(1, self.IDS + 1):
            user32.UnregisterHotKey(None, n)

    def reload(self, timeout: float = 1.5) -> list:
        """Re-register after the config changed. Returns the hotkeys Windows would not give us."""
        self._ready.wait(2)
        self._reloaded.clear()
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_APP, 0, 0)
            self._reloaded.wait(timeout)
        return list(self.failed)

    def stop(self):
        self._ready.wait(2)
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)


# --------------------------------------------------------------------------
# switcher helpers: focusing a window, finding the monitor, styling a popup
# --------------------------------------------------------------------------
user32.IsIconic.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HANDLE
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


def focus_window(hwnd: int) -> bool:
    """Bring a window to the front and give it keyboard focus, restoring it if it is minimized."""
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    if _try_foreground(hwnd):
        return True
    # Windows refuses focus changes from a process that did not just receive input. Sending one
    # harmless key press makes this process the most recent input source.
    send_dummy_key()
    if _try_foreground(hwnd):
        return True
    # Last resort: share input state with the current foreground window's thread for the call.
    fg = user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    fg_tid = user32.GetWindowThreadProcessId(fg, ctypes.byref(pid)) if fg else 0
    me = kernel32.GetCurrentThreadId()
    attached = bool(fg_tid) and fg_tid != me and user32.AttachThreadInput(me, fg_tid, True)
    try:
        return _try_foreground(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(me, fg_tid, False)


def _try_foreground(hwnd: int) -> bool:
    user32.BringWindowToTop(hwnd)
    ok = bool(user32.SetForegroundWindow(hwnd))
    return ok and get_foreground() == hwnd


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
INJECT_MARK = 0x57544E54   # alttab_hook ignores key presses carrying this tag


def send_dummy_key() -> None:
    """Press and release an unassigned key. It does nothing visible; it only counts as input."""
    items = (_INPUT * 2)()
    for i, flags in enumerate((0, 2)):
        items[i].type = 1
        items[i].u.ki = _KEYBDINPUT(0xE8, 0, flags, 0, INJECT_MARK)
    user32.SendInput(2, items, ctypes.sizeof(_INPUT))


GWL_EXSTYLE_ = -20
WS_EX_NOACTIVATE = 0x08000000
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t


def set_noactivate(widget_id: int, on: bool) -> None:
    """Make a popup show without taking keyboard focus (on=True) or restore normal behavior."""
    try:
        hwnd = user32.GetParent(widget_id) or widget_id
        style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE_)
        style = (style | WS_EX_NOACTIVATE) if on else (style & ~WS_EX_NOACTIVATE)
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE_, style)
    except Exception:
        pass


def cursor_work_area():
    """(left, top, right, bottom) of the usable area of the monitor the mouse is on, or None."""
    try:
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        mon = user32.MonitorFromPoint(pt, 2)  # MONITOR_DEFAULTTONEAREST
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if user32.GetMonitorInfoW(mon, ctypes.byref(info)):
            r = info.rcWork
            return (r.left, r.top, r.right, r.bottom)
    except Exception:
        pass
    return None


def system_uses_dark_apps() -> bool:
    """True when Windows is set to dark mode for apps (Settings > Personalization > Colors)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except Exception:
        return False


def style_titlebar(widget_id: int, dark: bool) -> None:
    """Dark or light title bar for a normal tkinter window, to match the app theme."""
    try:
        hwnd = user32.GetParent(widget_id) or widget_id
        v = ctypes.c_int(1 if dark else 0)
        dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(v), 4)       # DWMWA_USE_IMMERSIVE_DARK_MODE
    except Exception:
        pass


def repaint(widget_id: int) -> None:
    """Force a redraw of a popup and its children. A hidden window that is shown again can come back
    with its last picture and no repaint request, which would show the previous session's cards."""
    try:
        hwnd = user32.GetParent(widget_id) or widget_id
        user32.RedrawWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
        user32.RedrawWindow(hwnd, None, None, 0x1 | 0x4 | 0x80 | 0x100)   # INVALIDATE|ERASE|ALLCHILDREN|UPDATENOW
    except Exception:
        pass


def style_popup(widget_id: int) -> None:
    """Rounded corners, dark title-less frame and a drop shadow for a borderless tkinter popup."""
    try:
        hwnd = user32.GetParent(widget_id) or widget_id
        for attr, value in ((20, 1), (33, 2)):   # immersive dark mode, rounded corners
            v = ctypes.c_int(value)
            dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), 4)
        margins = (ctypes.c_int * 4)(1, 1, 1, 1)  # a 1px glass edge is what gives a borderless window a shadow
        dwmapi.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.c_void_p]
        dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
    except Exception:
        pass


# --------------------------------------------------------------------------
# window geometry and live DWM thumbnails (for the switcher's cards)
# --------------------------------------------------------------------------
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWM_TNP_RECTDESTINATION, DWM_TNP_OPACITY, DWM_TNP_VISIBLE, DWM_TNP_SOURCECLIENTAREAONLY = 0x1, 0x4, 0x8, 0x10


def window_geometry(hwnd: int):
    """(width / height, is_minimized) of a window, for sizing its switcher card."""
    if user32.IsIconic(hwnd):
        return 1.6, True
    r = wintypes.RECT()
    if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    return (w / h if w > 0 and h > 0 else 1.6), False


class _DWM_THUMBNAIL_PROPERTIES(ctypes.Structure):
    _fields_ = [("dwFlags", wintypes.DWORD), ("rcDestination", wintypes.RECT), ("rcSource", wintypes.RECT),
                ("opacity", ctypes.c_ubyte), ("fVisible", wintypes.BOOL), ("fSourceClientAreaOnly", wintypes.BOOL)]


dwmapi.DwmRegisterThumbnail.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.POINTER(ctypes.c_void_p)]
dwmapi.DwmUnregisterThumbnail.argtypes = [ctypes.c_void_p]
dwmapi.DwmUpdateThumbnailProperties.argtypes = [ctypes.c_void_p, ctypes.POINTER(_DWM_THUMBNAIL_PROPERTIES)]


class ThumbnailSet:
    """Live previews of other windows drawn inside one of our windows. Only the changes are applied."""

    def __init__(self):
        self.dest = 0
        self.items: dict = {}          # source hwnd -> (thumbnail handle, rect)

    def apply(self, dest: int, rects: dict) -> None:
        """rects: {source hwnd: (left, top, right, bottom)} in the destination's client pixels."""
        if dest != self.dest:
            self.clear()
            self.dest = dest
        for src in [s for s in self.items if s not in rects]:
            self._drop(src)
        for src, rect in rects.items():
            have = self.items.get(src)
            if have is not None and have[1] == rect:
                continue
            handle = have[0] if have else self._register(dest, src)
            if handle is None:
                continue
            props = _DWM_THUMBNAIL_PROPERTIES()
            props.dwFlags = DWM_TNP_RECTDESTINATION | DWM_TNP_VISIBLE | DWM_TNP_OPACITY
            props.rcDestination = wintypes.RECT(*rect)
            props.opacity, props.fVisible = 255, True
            dwmapi.DwmUpdateThumbnailProperties(handle, ctypes.byref(props))
            self.items[src] = (handle, rect)

    def _register(self, dest: int, src: int):
        handle = ctypes.c_void_p()
        try:
            if dwmapi.DwmRegisterThumbnail(dest, src, ctypes.byref(handle)) == 0 and handle.value:
                self.items[src] = (handle, None)
                return handle
        except Exception:
            pass
        return None

    def _drop(self, src: int) -> None:
        have = self.items.pop(src, None)
        if have:
            try:
                dwmapi.DwmUnregisterThumbnail(have[0])
            except Exception:
                pass

    def clear(self) -> None:
        for src in list(self.items):
            self._drop(src)


# --------------------------------------------------------------------------
# single instance, DPI, autostart
# --------------------------------------------------------------------------
_mutex_handle = None


def acquire_single_instance() -> bool:
    global _mutex_handle
    _mutex_handle = kernel32.CreateMutexW(None, False, "Local\\WindowTintSingleton")
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


def enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
    except Exception:
        pass


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _autostart_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    interpreter = pyw if pyw.exists() else exe
    script = Path(__file__).resolve().with_name("main.py")
    return f'"{interpreter}" "{script}"'


def autostart_enabled() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            winreg.QueryValueEx(k, "WindowTint")
            return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, "WindowTint", 0, winreg.REG_SZ, _autostart_command())
        else:
            try:
                winreg.DeleteValue(k, "WindowTint")
            except FileNotFoundError:
                pass
