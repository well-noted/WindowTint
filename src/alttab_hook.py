"""Optional replacement for Windows' Alt+Tab: a low-level keyboard hook that turns
Alt+Tab (hold Alt, tap Tab, release Alt) into events for WindowTint's own switcher.

AltTabMachine holds the decision logic and has no Windows dependency, so it is tested directly.
AltTabHook is the thin Windows layer: it installs the hook only while the feature is switched on,
so nothing sits in the keyboard path when it is off."""
from __future__ import annotations

import queue
import sys
import threading
from dataclasses import dataclass

VK_TAB, VK_ESCAPE = 0x09, 0x1B
VK_ALTS = (0x12, 0xA4, 0xA5)                      # VK_MENU, VK_LMENU, VK_RMENU
ARROWS = {0x25: "left", 0x26: "up", 0x27: "right", 0x28: "down"}


@dataclass
class Result:
    suppress: bool = False          # swallow the key so the rest of the system never sees it
    event: tuple | None = None      # ("begin", shift) | ("step", +1/-1) | ("arrow", "left"|"right"|"up"|"down") | ("commit",) | ("cancel",)
    inject: bool = False            # send a harmless key so releasing Alt does not open an app's menu


PASS = Result()


class AltTabMachine:
    """Alt+Tab session tracking. Ctrl+Alt+Tab and Win+Tab are never touched: those stay with Windows."""

    def __init__(self):
        self.active = False

    def reset(self) -> None:
        self.active = False

    def handle(self, vk: int, down: bool, alt_flag: bool, ctrl: bool, win: bool, shift: bool,
               alive: bool = True) -> Result:
        if not alive:                  # the app stopped answering: give Alt+Tab back to Windows
            self.active = False
            return PASS
        if vk == VK_TAB:
            if down and alt_flag and not ctrl and not win:
                if not self.active:
                    self.active = True
                    return Result(True, ("begin", shift), True)
                return Result(True, ("step", -1 if shift else 1))
            if self.active:
                return Result(True)    # the matching key-up (and stray Tab presses) during a session
            return PASS
        if not self.active:
            return PASS
        if vk in VK_ALTS and not down:
            self.active = False
            return Result(False, ("commit",))      # Alt itself still goes through
        if vk == VK_ESCAPE:
            if down:
                self.active = False
                return Result(True, ("cancel",))
            return PASS
        if vk in ARROWS:
            return Result(True, ("arrow", ARROWS[vk]) if down else None)
        return PASS


# --------------------------------------------------------------------------
# Windows layer
# --------------------------------------------------------------------------
WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
LLKHF_ALTDOWN = 0x20
WM_APP_ENABLE, WM_APP_DISABLE, WM_QUIT = 0x8001, 0x8002, 0x0012
MARK = 0x57544E54                                # tags the key presses WindowTint injects itself
DUMMY_VK = 0xE8                                  # an unassigned virtual key


class AltTabHook(threading.Thread):
    def __init__(self, emit, alive=lambda: True):
        """emit(event_tuple) is called from the hook thread; keep it quick (a queue put)."""
        super().__init__(daemon=True, name="WindowTintAltTab")
        self.emit, self.alive = emit, alive
        self.machine = AltTabMachine()
        self.error = ""
        self._tid = 0
        self._ready = threading.Event()
        self._applied = threading.Event()   # set after an enable/disable request has been processed
        self._hook = None
        self._proc = None

    # -- control from other threads ------------------------------------------------------
    def set_enabled(self, enabled: bool) -> bool:
        """Switch the hook on or off. Returns whether the hook is installed afterwards."""
        if sys.platform != "win32":
            return False
        import ctypes
        self._ready.wait(2)
        if self._tid:
            self._applied.clear()
            ctypes.WinDLL("user32").PostThreadMessageW(self._tid, WM_APP_ENABLE if enabled else WM_APP_DISABLE, 0, 0)
            self._applied.wait(1.0)
        return self._hook is not None

    def stop(self) -> None:
        if sys.platform == "win32" and self._tid:
            import ctypes
            ctypes.WinDLL("user32").PostThreadMessageW(self._tid, WM_QUIT, 0, 0)

    # -- the hook thread -------------------------------------------------------------------
    def run(self) -> None:
        if sys.platform != "win32":
            return
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        LRESULT = ctypes.c_ssize_t
        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class _U(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]

        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = LRESULT
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = ctypes.c_short
        user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        def pressed(vk: int) -> bool:
            return bool(user32.GetAsyncKeyState(vk) & 0x8000)

        def inject_dummy() -> None:
            # A key pressed between Alt-down and Alt-up stops apps from treating Alt alone as "open the menu".
            items = (INPUT * 2)()
            for i, flags in enumerate((0, 2)):          # key down, key up
                items[i].type = 1                       # INPUT_KEYBOARD
                items[i].u.ki = KEYBDINPUT(DUMMY_VK, 0, flags, 0, MARK)
            user32.SendInput(2, items, ctypes.sizeof(INPUT))

        def proc(n_code, w_param, l_param):
            try:
                if n_code == 0:
                    kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    if kb.dwExtraInfo != MARK:
                        down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
                        up = w_param in (WM_KEYUP, WM_SYSKEYUP)
                        if down or up:
                            res = self.machine.handle(
                                kb.vkCode, down, bool(kb.flags & LLKHF_ALTDOWN),
                                pressed(0x11), pressed(0x5B) or pressed(0x5C), pressed(0x10), self.alive())
                            if res.inject:
                                inject_dummy()
                            if res.event:
                                self.emit(res.event)
                            if res.suppress:
                                return 1
            except Exception:
                self.machine.reset()          # never leave a half-finished session behind
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        self._proc = HOOKPROC(proc)            # keep a reference so it is not garbage-collected
        self._tid = kernel32.GetCurrentThreadId()
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_APP_ENABLE and self._hook is None:
                self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
                if not self._hook:
                    self.error = f"could not install the Alt+Tab hook (error {ctypes.get_last_error()})"
                self._applied.set()
            elif msg.message == WM_APP_DISABLE:
                if self._hook:
                    user32.UnhookWindowsHookEx(self._hook)
                    self._hook = None
                    self.machine.reset()
                self._applied.set()
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
