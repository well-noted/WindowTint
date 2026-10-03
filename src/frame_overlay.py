"""Thicker window borders.

Windows draws a window's colored border one pixel wide and offers no way to change that. When the
border thickness setting is above 1, WindowTint also draws a frame just OUTSIDE the window with a
click-through layered window owned by it (the same mechanism as the tab marks). The owner keeps it in
the right place in the z-order, hides it when minimized, and it follows the window as it moves.

render_frame() is plain Pillow and is tested anywhere; the rest needs Windows."""
from __future__ import annotations

import os
import sys
import threading
import time

CORNER_DIP = 8          # Windows 11's default window corner radius at 100% scaling
POLL_SECONDS = 0.05


def render_frame(width: int, height: int, ring: int, radius: float, color: str) -> bytes:
    """A rounded ring `ring` px thick around a (width x height) hole's outside: the image is the
    outer size, the transparent hole inside is the window. Returns premultiplied BGRA, top-down."""
    from PIL import Image, ImageChops, ImageDraw

    width, height, ring = max(1, width), max(1, height), max(1, ring)
    k = 4
    outer = Image.new("L", (width * k, height * k), 0)
    ImageDraw.Draw(outer).rounded_rectangle((0, 0, width * k - 1, height * k - 1), (radius + ring) * k, fill=255)
    inner = Image.new("L", (width * k, height * k), 0)
    if width > 2 * ring and height > 2 * ring:
        ImageDraw.Draw(inner).rounded_rectangle((ring * k, ring * k, (width - ring) * k - 1, (height - ring) * k - 1),
                                                max(0.0, radius) * k, fill=255)
    alpha = ImageChops.subtract(outer, inner).resize((width, height), Image.LANCZOS)
    rgb = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
    img = Image.new("RGBA", (width, height), rgb + (0,))
    img.putalpha(alpha)
    r, g, b, a = img.convert("RGBa").split()
    return Image.merge("RGBA", (b, g, r, a)).tobytes()


class FrameOverlay(threading.Thread):
    """Keeps one frame window per colored window. set_targets({hwnd: '#rrggbb'}) says which."""

    def __init__(self, thickness: int = 2):
        super().__init__(daemon=True, name="WindowTintFrames")
        self.thickness = thickness
        self._targets: dict = {}
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()

    def set_targets(self, targets: dict, thickness: int | None = None) -> None:
        with self._lock:
            self._targets = dict(targets)
            if thickness is not None:
                self.thickness = thickness

    def stop(self) -> None:
        self._stop_evt.set()

    def run(self) -> None:
        if sys.platform != "win32":
            return
        import ctypes
        from ctypes import wintypes

        import overlay
        from uia import log_once
        try:
            w = overlay._win32()
        except Exception as e:
            log_once("frames-init", f"frame overlay unavailable: {e!r}")
            return
        u = w.user32
        u.IsZoomed.argtypes = [wintypes.HWND]
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.MsgWaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD,
                                                wintypes.DWORD]
        dwm = ctypes.WinDLL("dwmapi")
        user = ctypes.WinDLL("user32")
        user.GetDpiForWindow.argtypes = [wintypes.HWND]
        user.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE, ctypes.c_void_p,
                                         wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
        user.SetWinEventHook.restype = wintypes.HANDLE
        user.UnhookWinEvent.argtypes = [wintypes.HANDLE]
        own_pid = os.getpid()
        marks: dict = {}
        last: dict = {}      # hwnd -> (size, color, px, left, top) as drawn

        def bounds(hwnd):
            r = wintypes.RECT()
            if dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:   # EXTENDED_FRAME_BOUNDS
                return None
            return r.left, r.top, r.right, r.bottom

        def is_ours(hwnd):
            pid = wintypes.DWORD(0)
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            return pid.value == own_pid

        def update(hwnd, color, px):
            mark = marks.get(hwnd)
            if mark is None:
                mark = marks[hwnd] = overlay.MarkWindow(hwnd)
            ring = px - 1                  # Windows already draws the first pixel
            if ring < 1 or mark.owner_hidden() or u.IsZoomed(hwnd):
                actual = mark.actual_position()
                if mark.visible or (actual and actual[2]):
                    mark.hide()
                last.pop(hwnd, None)
                return
            rect = bounds(hwnd)
            if rect is None:
                mark.hide()
                last.pop(hwnd, None)
                return
            left, top, right, bottom = rect
            width, height = right - left + 2 * ring, bottom - top + 2 * ring
            x, y = left - ring, top - ring
            prev = last.get(hwnd)
            actual = mark.actual_position()
            drifted = actual is None or actual[2] != mark.visible or (prev is not None and actual[:2] != (prev[3], prev[4]))
            if prev and prev[:3] == ((width, height), color, px) and not drifted:
                if (x, y) != (prev[3], prev[4]):
                    mark.move(x, y)                      # dragging: no redraw, just move
                    last[hwnd] = prev[:3] + (x, y)
                if not mark.visible:
                    mark.show()
                return
            dpi = user.GetDpiForWindow(hwnd) or 96
            mark.draw(x, y, width, height, render_frame(width, height, ring, CORNER_DIP * dpi / 96, color))
            last[hwnd] = ((width, height), color, px, x, y)

        def current():
            with self._lock:
                return dict(self._targets), self.thickness

        def on_event(hook, event, hwnd, id_object, id_child, thread, ms):
            # A window moved or resized: follow it right now instead of waiting for the next poll.
            try:
                if id_object == 0 and hwnd:
                    targets, px = current()
                    if hwnd in targets:
                        update(hwnd, targets[hwnd], px)
            except Exception:
                pass

        callback_type = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG,
                                           wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
        callback = callback_type(on_event)      # keep a reference
        hook = user.SetWinEventHook(0x800B, 0x800B, None, callback, 0, 0, 0)   # LOCATIONCHANGE, out of context
        next_poll = 0.0
        try:
            while not self._stop_evt.is_set():
                w.pump()
                now = time.monotonic()
                if now >= next_poll:
                    next_poll = now + POLL_SECONDS
                    targets, px = current()
                    for hwnd, color in targets.items():
                        try:
                            if is_ours(hwnd):
                                continue       # our own windows are drawn by Windows' border only
                            update(hwnd, color, px)
                        except Exception as e:
                            log_once("frames-update", f"frame update failed: {e!r}")
                    for hwnd in [h for h in marks if h not in targets or is_ours(h) or not u.IsWindow(h)]:
                        marks.pop(hwnd).destroy()
                        last.pop(hwnd, None)
                u.MsgWaitForMultipleObjects(0, None, False, 8, 0x04FF)    # wake on any message, at most 8 ms
        finally:
            if hook:
                user.UnhookWinEvent(hook)
            for mark in marks.values():
                mark.destroy()
