"""Click-through color marks drawn over the Claude sidebar's chat rows.

One transparent layered window is created per Claude window and OWNED by it. Owned windows
stay directly above their owner in z-order, are hidden when the owner is minimized, and are
covered by any window that covers the owner, so the marks never float above other apps.

Everything that touches Win32 is loaded lazily, so this module imports on any OS and the
drawing function can be tested anywhere.

    python overlay.py --demo      draws a red test mark for 6 seconds (checks the mechanism alone)
"""
from __future__ import annotations

import sys
import threading
import time

FILL_ALPHA = 38   # translucent tint over the whole row (0-255)
BAR_WIDTH = 4     # solid bar on the left edge of the row
TAB_INSET = 10      # horizontal inset of a tab mark
TAB_BAR_HEIGHT = 3  # solid bar on the top edge of a browser tab


def render_bgra(width: int, height: int, items: list, style: str = "row") -> bytes:
    """items: [((left, top, right, bottom) in image coordinates, '#rrggbb')].
    Returns premultiplied BGRA bytes, top-down, as UpdateLayeredWindow wants."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for (left, top, right, bottom), color in items:
        rgb = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
        if style == "tab":
            left, right = left + TAB_INSET, right - TAB_INSET   # Chromium tab rectangles overlap their neighbors
            if right - left < 4 or bottom - top < 4:
                continue
            draw.rounded_rectangle((left, top, right - 1, bottom - 1), radius=6, fill=rgb + (FILL_ALPHA,))
            draw.rectangle((left + 2, top, right - 3, min(bottom - 1, top + TAB_BAR_HEIGHT - 1)), fill=rgb + (255,))
            continue
        top, bottom = top + 1, bottom - 2  # a little air between neighboring rows
        if right - left < 2 or bottom - top < 2:
            continue
        draw.rounded_rectangle((left, top, right - 1, bottom), radius=6, fill=rgb + (FILL_ALPHA,))
        draw.rounded_rectangle((left, top, min(right - 1, left + BAR_WIDTH - 1), bottom), radius=2, fill=rgb + (255,))
    r, g, b, a = img.convert("RGBa").split()  # premultiplied alpha
    return Image.merge("RGBA", (b, g, r, a)).tobytes()  # byte order B, G, R, A


# ---------------------------------------------------------------------------
# Win32 plumbing (Windows only, loaded on first use)
# ---------------------------------------------------------------------------
class _Win32:
    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self.ctypes, self.wt = ctypes, wintypes
        u = self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        g = self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        k = self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        LRESULT = ctypes.c_ssize_t
        H = wintypes.HANDLE

        self.WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", self.WNDPROC),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", H),
                        ("hIcon", H), ("hCursor", H), ("hbrBackground", H), ("lpszMenuName", wintypes.LPCWSTR),
                        ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", H)]

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD)]

        class BLENDFUNCTION(ctypes.Structure):
            _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                        ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]

        self.WNDCLASSEXW, self.BITMAPINFOHEADER, self.BLENDFUNCTION = WNDCLASSEXW, BITMAPINFOHEADER, BLENDFUNCTION

        u.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.DefWindowProcW.restype = LRESULT
        u.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
        u.RegisterClassExW.restype = wintypes.ATOM
        u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                      wintypes.HWND, H, H, ctypes.c_void_p]
        u.CreateWindowExW.restype = wintypes.HWND
        u.DestroyWindow.argtypes = [wintypes.HWND]
        u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u.IsWindow.argtypes = [wintypes.HWND]
        u.IsIconic.argtypes = [wintypes.HWND]
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        u.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        u.GetWindow.restype = wintypes.HWND
        u.GetForegroundWindow.restype = wintypes.HWND
        u.GetDC.argtypes = [wintypes.HWND]
        u.GetDC.restype = H
        u.ReleaseDC.argtypes = [wintypes.HWND, H]
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wintypes.UINT]
        u.UpdateLayeredWindow.argtypes = [wintypes.HWND, H, ctypes.POINTER(wintypes.POINT),
                                          ctypes.POINTER(wintypes.SIZE), H, ctypes.POINTER(wintypes.POINT),
                                          wintypes.COLORREF, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
        u.UpdateLayeredWindow.restype = wintypes.BOOL
        u.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
                                   wintypes.UINT]
        u.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        u.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        g.CreateCompatibleDC.argtypes = [H]
        g.CreateCompatibleDC.restype = H
        g.CreateDIBSection.argtypes = [H, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), H,
                                       wintypes.DWORD]
        g.CreateDIBSection.restype = H
        g.SelectObject.argtypes = [H, H]
        g.SelectObject.restype = H
        g.DeleteObject.argtypes = [H]
        g.DeleteDC.argtypes = [H]
        k.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        k.GetModuleHandleW.restype = H

        self._proc = self.WNDPROC(lambda h, m, w, l: u.DefWindowProcW(h, m, w, l))  # keep a reference
        self.class_name = "WindowTintSidebarMarks"
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._proc
        wc.hInstance = k.GetModuleHandleW(None)
        wc.lpszClassName = self.class_name
        if not u.RegisterClassExW(ctypes.byref(wc)) and ctypes.get_last_error() != 1410:  # 1410: already exists
            raise OSError(f"RegisterClassExW failed ({ctypes.get_last_error()})")

    def pump(self) -> None:
        msg = self.wt.MSG()
        while self.user32.PeekMessageW(self.ctypes.byref(msg), None, 0, 0, 1):
            self.user32.TranslateMessage(self.ctypes.byref(msg))
            self.user32.DispatchMessageW(self.ctypes.byref(msg))


_W32 = None


_W32_LOCK = threading.Lock()


def _win32() -> _Win32:
    global _W32
    with _W32_LOCK:   # the sidebar and tab overlays start on separate threads; register the class once
        if _W32 is None:
            _W32 = _Win32()
        return _W32


WS_POPUP = 0x80000000
WS_EX_TOOLWINDOW, WS_EX_TRANSPARENT, WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x80, 0x20, 0x80000, 0x08000000
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
GW_OWNER = 4
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x2, 0x10, 0x40
ULW_ALPHA = 2


class MarkWindow:
    """One transparent, click-through layered window owned by `owner`."""

    def __init__(self, owner: int):
        w = self.w = _win32()
        self.owner = owner
        self.visible = False
        self.hwnd = w.user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
            w.class_name, "WindowTint marks", WS_POPUP, 0, 0, 1, 1, owner, None,
            w.kernel32.GetModuleHandleW(None), None)
        if not self.hwnd:
            self.hwnd = w.user32.CreateWindowExW(
                WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | 0x8,  # | TOPMOST
                w.class_name, "WindowTint marks", WS_POPUP, 0, 0, 1, 1, None, None,
                w.kernel32.GetModuleHandleW(None), None)
            self.owned = False
        else:
            self.owned = bool(w.user32.GetWindow(self.hwnd, GW_OWNER))
        if not self.hwnd:
            raise OSError(f"CreateWindowExW failed ({w.ctypes.get_last_error()})")

    def draw(self, left: int, top: int, width: int, height: int, bgra: bytes) -> None:
        w, ct, wt = self.w, self.w.ctypes, self.w.wt
        screen_dc = w.user32.GetDC(None)
        mem_dc = w.gdi32.CreateCompatibleDC(screen_dc)
        bmi = w.BITMAPINFOHEADER(ct.sizeof(w.BITMAPINFOHEADER), width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0)
        bits = ct.c_void_p()
        bitmap = w.gdi32.CreateDIBSection(mem_dc, ct.byref(bmi), 0, ct.byref(bits), None, 0)
        try:
            if not bitmap or not bits.value:
                raise OSError("CreateDIBSection failed")
            ct.memmove(bits, bgra, width * height * 4)
            old = w.gdi32.SelectObject(mem_dc, bitmap)
            pos, size, src = wt.POINT(left, top), wt.SIZE(width, height), wt.POINT(0, 0)
            blend = w.BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, per-pixel alpha
            w.user32.UpdateLayeredWindow(self.hwnd, screen_dc, ct.byref(pos), ct.byref(size), mem_dc,
                                         ct.byref(src), 0, ct.byref(blend), ULW_ALPHA)
            w.gdi32.SelectObject(mem_dc, old)
        finally:
            if bitmap:
                w.gdi32.DeleteObject(bitmap)
            w.gdi32.DeleteDC(mem_dc)
            w.user32.ReleaseDC(None, screen_dc)
        if not self.visible:
            self.show()

    def show(self) -> None:
        self.w.user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        if not self.owned:
            self.w.user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                       SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        self.visible = True

    def hide(self) -> None:
        # Always call ShowWindow: the cached flag can be wrong (Windows re-shows owned windows on its own).
        self.w.user32.ShowWindow(self.hwnd, SW_HIDE)
        self.visible = False

    def move(self, left: int, top: int) -> None:
        """Reposition without redrawing."""
        self.w.user32.SetWindowPos(self.hwnd, 0, left, top, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE | 0x4)   # | NOZORDER

    def actual_position(self):
        """(left, top, visible) as Windows has it, to catch a mark that drifted from what we think."""
        w = self.w
        r = w.wt.RECT()
        if not w.user32.GetWindowRect(self.hwnd, w.ctypes.byref(r)):
            return None
        return r.left, r.top, bool(w.user32.IsWindowVisible(self.hwnd))

    def owner_is_front(self) -> bool:
        u = self.w.user32
        return bool(u.GetForegroundWindow() == self.owner and not u.IsIconic(self.owner))

    def owner_hidden(self) -> bool:
        """True while the window the marks belong to is minimized, hidden, or on another virtual desktop.
        Marks must not be shown then: Windows hides them with their owner, but an explicit ShowWindow
        on a stale mark would put them back on screen, floating over whatever is there."""
        w = self.w
        try:
            if not w.user32.IsWindow(self.owner) or w.user32.IsIconic(self.owner) \
                    or not w.user32.IsWindowVisible(self.owner):
                return True
            cloaked = w.wt.DWORD(0)
            w.ctypes.WinDLL("dwmapi").DwmGetWindowAttribute(
                self.owner, 14, w.ctypes.byref(cloaked), w.ctypes.sizeof(cloaked))     # DWMWA_CLOAKED
            return bool(cloaked.value)
        except Exception:
            return False

    def destroy(self) -> None:
        try:
            self.w.user32.DestroyWindow(self.hwnd)
        except Exception:
            pass


class OverlayManager(threading.Thread):
    """Draws the sidebar marks for the windows named by set_targets().

    snapshot_for(hwnd)  -> uia.Snapshot | None   (positions, from the UI Automation thread)
    color_for(hwnd, text) -> '#rrggbb' | None     (rules decide which chats get which color)
    """

    def __init__(self, snapshot_for, color_for, interval: float = 0.06, style: str = "row"):
        super().__init__(daemon=True, name="WindowTintOverlay")
        self.snapshot_for, self.color_for, self.interval, self.style = snapshot_for, color_for, interval, style
        self._targets: set = set()
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()

    def set_targets(self, hwnds) -> None:
        with self._lock:
            self._targets = set(hwnds)

    def stop(self) -> None:
        self._stop_evt.set()

    def run(self) -> None:
        from uia import log_once
        try:
            w = _win32()
        except Exception as e:
            log_once("overlay-init", f"overlay unavailable: {e!r}")
            return
        marks: dict = {}   # owner hwnd -> MarkWindow
        last: dict = {}    # owner hwnd -> last drawn key
        while not self._stop_evt.is_set():
            w.pump()
            with self._lock:
                targets = set(self._targets)
            for owner in targets:
                try:
                    self._update(owner, marks, last)
                except Exception as e:
                    log_once("overlay-update", f"overlay update failed: {e!r}")
            for owner in [o for o in marks if o not in targets or not w.user32.IsWindow(o)]:
                marks.pop(owner).destroy()
                last.pop(owner, None)
            self._stop_evt.wait(self.interval)
        for mark in marks.values():
            mark.destroy()

    def _update(self, owner: int, marks: dict, last: dict) -> None:
        mark = marks.get(owner)
        if mark is not None and mark.owner_hidden():
            mark.hide()
            last.pop(owner, None)
            return
        snap = self.snapshot_for(owner)
        items = []
        if snap is not None:
            for text, rect in snap.items:
                color = self.color_for(owner, text)
                if color:
                    items.append((rect, color))
        if not items:
            if mark:
                mark.hide()
                last.pop(owner, None)
            return
        if mark is None:
            mark = marks[owner] = MarkWindow(owner)
            if mark.owner_hidden():
                return
        if not mark.owned and not mark.owner_is_front():
            mark.hide()          # fallback mode: only show while Claude is the active window
            last.pop(owner, None)
            return
        key = (snap.clip, tuple(items), mark.visible)
        if last.get(owner) == key:
            return
        left, top, right, bottom = snap.clip
        width, height = right - left, bottom - top
        local = [((l - left, t - top, r - left, b - top), c) for (l, t, r, b), c in items]
        mark.draw(left, top, width, height, render_bgra(width, height, local, self.style))
        last[owner] = key


def _demo() -> None:
    """Draw a red mark with no owner for a few seconds, to verify the drawing mechanism."""
    w = _win32()
    left, top, width, height = 120, 160, 360, 140
    mark = MarkWindow(None)
    mark.owned = False
    mark.draw(left, top, width, height,
              render_bgra(width, height, [((0, 0, width, 60), "#e53935"), ((0, 70, width, 130), "#1e88e5")]))
    print("Two colored marks should be visible near the top-left of your main screen for 6 seconds.")
    end = time.time() + 6
    while time.time() < end:
        w.pump()
        time.sleep(0.05)
    mark.destroy()


if __name__ == "__main__" and "--demo" in sys.argv:
    _demo()
