"""Window icons for the switcher cards (Windows only; nothing here is needed to import the rest of the app).

An icon comes from the window itself (WM_GETICON, then its window class) and, failing that, from the
program's .exe file. The icon is drawn onto a black and onto a white 32-bit bitmap; the difference
between the two gives each pixel's transparency, so any icon format converts cleanly to RGBA."""
from __future__ import annotations

import ctypes
from ctypes import wintypes

from PIL import Image

_cache: dict = {}
SIZE = 64


def alpha_from_renders(on_black: bytes, on_white: bytes, size: int) -> Image.Image:
    """Two BGRA renders of the same icon (over black and over white) -> an RGBA image."""
    a = Image.frombuffer("RGBA", (size, size), on_black, "raw", "BGRA", 0, 1)
    b = Image.frombuffer("RGBA", (size, size), on_white, "raw", "BGRA", 0, 1)
    out = bytearray(size * size * 4)
    pa, pb = a.tobytes(), b.tobytes()
    for i in range(0, len(pa), 4):
        # over black: c*a ; over white: c*a + 255*(1-a)  =>  a = 1 - (white - black)/255
        d = ((pb[i] - pa[i]) + (pb[i + 1] - pa[i + 1]) + (pb[i + 2] - pa[i + 2])) / 3
        alpha = max(0.0, min(1.0, 1 - d / 255))
        if alpha > 0.003:
            out[i] = min(255, round(pa[i] / alpha))
            out[i + 1] = min(255, round(pa[i + 1] / alpha))
            out[i + 2] = min(255, round(pa[i + 2] / alpha))
            out[i + 3] = round(alpha * 255)
    return Image.frombuffer("RGBA", (size, size), bytes(out), "raw", "RGBA", 0, 1)


def _render(hicon: int, size: int):
    """The icon as an RGBA image, or None."""
    user32, gdi32 = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateDIBSection.restype = ctypes.c_void_p
    gdi32.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
                                       ctypes.c_void_p, wintypes.DWORD]
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
    user32.DrawIconEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
                                  ctypes.c_int, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
    renders = []
    dc = gdi32.CreateCompatibleDC(None)
    try:
        for bg in (0x00, 0xFF):
            bih = BIH(ctypes.sizeof(BIH), size, -size, 1, 32, 0, 0, 0, 0, 0, 0)
            bits = ctypes.c_void_p()
            bmp = gdi32.CreateDIBSection(dc, ctypes.byref(bih), 0, ctypes.byref(bits), None, 0)
            if not bmp or not bits.value:
                return None
            old = gdi32.SelectObject(dc, bmp)
            ctypes.memset(bits, bg, size * size * 4)
            user32.DrawIconEx(dc, 0, 0, hicon, size, size, 0, None, 0x3)     # DI_NORMAL
            renders.append(ctypes.string_at(bits.value, size * size * 4))
            gdi32.SelectObject(dc, old)
            gdi32.DeleteObject(bmp)
    finally:
        gdi32.DeleteDC(dc)
    return alpha_from_renders(renders[0], renders[1], size)


def _window_icon(hwnd: int):
    user32 = ctypes.WinDLL("user32")
    user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                           wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    user32.GetClassLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetClassLongPtrW.restype = ctypes.c_size_t
    for which in (1, 2, 0):                       # ICON_BIG, ICON_SMALL2, ICON_SMALL
        res = ctypes.c_size_t(0)
        if user32.SendMessageTimeoutW(hwnd, 0x7F, which, 0, 0x2, 40, ctypes.byref(res)) and res.value:
            return res.value
    for idx in (-14, -34):                        # GCLP_HICON, GCLP_HICONSM
        h = user32.GetClassLongPtrW(hwnd, idx)
        if h:
            return h
    return None


def _exe_icon(pid: int):
    try:
        import psutil
        path = psutil.Process(pid).exe()
    except Exception:
        return None
    shell32, user32 = ctypes.WinDLL("shell32"), ctypes.WinDLL("user32")
    shell32.ExtractIconExW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p),
                                       ctypes.POINTER(ctypes.c_void_p), wintypes.UINT]
    big = ctypes.c_void_p()
    if shell32.ExtractIconExW(path, 0, ctypes.byref(big), None, 1) < 1 or not big.value:
        return None
    return big.value, user32


def get_icon(hwnd: int, pid: int = 0):
    """RGBA icon image for a window (cached), or None so the caller can draw a letter tile instead."""
    if hwnd in _cache:
        return _cache[hwnd]
    img = None
    try:
        h = _window_icon(hwnd)
        if h:
            img = _render(h, SIZE)
        if img is None and pid:
            got = _exe_icon(pid)
            if got:
                img = _render(got[0], SIZE)
                got[1].DestroyIcon(ctypes.c_void_p(got[0]))
    except Exception:
        img = None
    if len(_cache) > 400:
        _cache.clear()
    _cache[hwnd] = img
    return img


def forget(hwnd: int) -> None:
    _cache.pop(hwnd, None)
