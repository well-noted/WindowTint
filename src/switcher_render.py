"""Draws the window switcher (a grid of window cards) with Pillow. Pure drawing: takes entries, a
GridLayout and state, returns an image. The live window previews are NOT drawn here: Windows composites
them over the dark preview area each card leaves empty."""
from __future__ import annotations

from collections import OrderedDict
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFont

PANEL = (28, 28, 33)
PANEL_BORDER = (62, 62, 70)
TEXT = (244, 244, 246)
MUTED = (150, 150, 160)
NEUTRAL_TILE = (58, 58, 66)
NEUTRAL_SELECT = (46, 46, 54)
DIVIDER = (48, 48, 55)

CARD = (40, 40, 47)
CARD_SELECTED = (54, 54, 63)
PREVIEW_BG = (22, 22, 26)
SELECT_NEUTRAL = (205, 205, 215)

_FONT_FILES = {
    "regular": ["segoeui.ttf", "DejaVuSans.ttf"],
    "semibold": ["seguisb.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf"],
}


@lru_cache(maxsize=32)
def font(kind: str, px: int):
    for name in _FONT_FILES[kind]:
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    try:
        return ImageFont.load_default(px)
    except TypeError:
        return ImageFont.load_default()


def rgb(hex_color: str):
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def blend(a, b, t: float):
    return tuple(round(a[i] * (1 - t) + b[i] * t) for i in range(3))


def contrast(color):
    return (20, 20, 24) if (0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2]) / 255 > 0.62 else (255, 255, 255)


def rrect(img: Image.Image, box, radius: float, fill=None, outline=None, outline_w: float = 1.0, alpha: int = 255):
    """Anti-aliased rounded rectangle: the shape is drawn 4x larger as a mask and scaled down."""
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    w, h = x1 - x0, y1 - y0
    if w < 2 or h < 2:
        return
    k = 4
    outer = Image.new("L", (w * k, h * k), 0)
    ImageDraw.Draw(outer).rounded_rectangle((0, 0, w * k - 1, h * k - 1), radius * k, fill=255)
    if fill is not None:
        mask = outer.resize((w, h), Image.LANCZOS)
        if alpha < 255:
            mask = mask.point(lambda v: v * alpha // 255)
        img.paste(Image.new("RGB", (w, h), fill), (x0, y0), mask)
    if outline is not None:
        inset = max(1, round(outline_w * k))
        inner = Image.new("L", (w * k, h * k), 0)
        ImageDraw.Draw(inner).rounded_rectangle((inset, inset, w * k - 1 - inset, h * k - 1 - inset),
                                                max(0.0, radius * k - inset), fill=255)
        ring = ImageChops.subtract(outer, inner).resize((w, h), Image.LANCZOS)
        if alpha < 255:
            ring = ring.point(lambda v: v * alpha // 255)
        img.paste(Image.new("RGB", (w, h), outline), (x0, y0), ring)


def fit(draw: ImageDraw.ImageDraw, text: str, fnt, max_w: float) -> str:
    if draw.textlength(text, font=fnt) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=fnt) > max_w:
        text = text[:-1]
    return text.rstrip() + "…"



_CARD_CACHE: "OrderedDict" = OrderedDict()
CARD_CACHE_MAX = 240


def _icon_tile(img, d, app: str, box, base, s):
    """Fallback for windows without a readable icon: a rounded tile with the program's first letter."""
    fill = base if base else NEUTRAL_TILE
    rrect(img, box, (box[2] - box[0]) * 0.28, fill=fill)
    d.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2 - 1), (app[:1] or "?").upper(),
           font=font("semibold", max(8, round((box[3] - box[1]) * 0.55))), fill=contrast(fill) if base else TEXT,
           anchor="mm")


def _draw_icon(img, d, entry, icon, box, base, s):
    size = max(2, round(box[2] - box[0]))
    if icon is not None:
        pic = icon.resize((size, size), Image.LANCZOS)
        img.paste(pic, (int(box[0]), int(box[1])), pic)
    else:
        _icon_tile(img, d, entry.app, box, base, s)


def render_card(entry, card, selected: bool, icon, scale: float) -> Image.Image:
    """One card (header with icon, title and project, plus the preview area), on the panel color."""
    s = scale
    w, h = card.w, card.h
    img = Image.new("RGB", (w, h), PANEL)
    d = ImageDraw.Draw(img)
    base = rgb(entry.color) if entry.color else None
    radius = 12 * s
    body = CARD_SELECTED if selected else CARD
    if base:
        body = blend(body, base, 0.26 if selected else 0.15)
    rrect(img, (0, 0, w, h), radius, fill=body)
    if selected:
        rrect(img, (0, 0, w, h), radius, outline=base or SELECT_NEUTRAL, outline_w=2.6 * s)
    elif base:
        rrect(img, (0, 0, w, h), radius, outline=base, outline_w=1.2 * s, alpha=120)
    pad = card.pad
    icon_px = round(22 * s)
    cy = card.header_h / 2 + 1 * s
    ibox = (pad + 4 * s, cy - icon_px / 2, pad + 4 * s + icon_px, cy + icon_px / 2)
    _draw_icon(img, d, entry, icon, ibox, base, s)
    f_title, f_chip = font("semibold", round(13 * s)), font("semibold", round(11 * s))
    right = w - pad - 4 * s
    if entry.project and base:
        label = fit(d, entry.project, f_chip, w * 0.34)
        cw = d.textlength(label, font=f_chip) + 18 * s
        chip = (right - cw, cy - 10 * s, right, cy + 10 * s)
        rrect(img, chip, 10 * s, fill=base)
        d.text(((chip[0] + chip[2]) / 2, (chip[1] + chip[3]) / 2), label, font=f_chip, fill=contrast(base), anchor="mm")
        right = chip[0] - 8 * s
    tx = ibox[2] + 9 * s
    d.text((tx, cy), fit(d, entry.title, f_title, right - tx), font=f_title, fill=TEXT, anchor="lm")
    px0, py0, px1, py1 = card.preview_rect()
    box = (px0 - card.x, py0 - card.y, px1 - card.x, py1 - card.y)
    rrect(img, box, 5 * s, fill=PREVIEW_BG)
    if entry.minimized:
        big = min(box[2] - box[0], box[3] - box[1]) * 0.5
        bx, by = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2 - 8 * s
        _draw_icon(img, d, entry, icon, (bx - big / 2, by - big / 2, bx + big / 2, by + big / 2), base, s)
        d.text((bx, by + big / 2 + 14 * s), "Minimized", font=font("regular", round(12 * s)), fill=MUTED, anchor="mm")
    return img


def card_image(entry, card, selected: bool, icon, scale: float) -> Image.Image:
    pr = card.preview_rect()
    key = (entry.hwnd, entry.title, entry.color, entry.project, entry.minimized, entry.app, selected,
           card.w, card.h, pr[2] - pr[0], pr[3] - pr[1], icon is not None, round(scale, 2))
    hit = _CARD_CACHE.get(key)
    if hit is not None:
        _CARD_CACHE.move_to_end(key)
        return hit
    img = render_card(entry, card, selected, icon, scale)
    _CARD_CACHE[key] = img
    while len(_CARD_CACHE) > CARD_CACHE_MAX:
        _CARD_CACHE.popitem(last=False)
    return img


def preview_rects(entries: list, layout) -> dict:
    """{hwnd: rect in panel pixels} where live previews belong (minimized windows have none)."""
    return {entries[c.index].hwnd: c.preview_rect() for c in layout.cards if not entries[c.index].minimized}


def render(entries: list, layout, selected: int, query: str = "", mode: str = "search", total: int | None = None,
           icons: dict | None = None) -> Image.Image:
    """entries: the (filtered) list the layout was computed for. icons: {hwnd: RGBA image or None}.
    mode 'search' adds the type-to-filter field and a hint footer; 'hold' (Alt+Tab) is just the cards."""
    s = layout.scale
    icons = icons or {}
    w, h = layout.width, layout.height
    img = Image.new("RGB", (w, h), PANEL)
    d = ImageDraw.Draw(img)
    for c in layout.cards:
        e = entries[c.index]
        img.paste(card_image(e, c, c.index == selected, icons.get(e.hwnd), s), (c.x, c.y))
    if not layout.cards:
        d.text((w / 2, layout.search_h + (h - layout.search_h - layout.footer_h) / 2), "No matching windows",
               font=font("regular", round(15 * s)), fill=MUTED, anchor="mm")
    if mode == "search":
        _draw_search(d, w, layout, query, len(entries), total, s)
        fy = h - layout.footer_h
        d.line((0, fy, w, fy), fill=DIVIDER, width=1)
        shown_rows = len({c.row for c in layout.cards})
        extra = (f"      Rows {layout.first_row + 1}-{layout.first_row + shown_rows} of {layout.rows_total}"
                 if layout.rows_total > shown_rows else "")
        d.text((22 * s, fy + layout.footer_h / 2), "Arrows / Tab Select      Enter Switch      Esc Close" + extra,
               font=font("regular", round(12 * s)), fill=MUTED, anchor="lm")
    d.rectangle((0, 0, w - 1, h - 1), outline=PANEL_BORDER, width=1)
    return img


def _draw_search(d, w, layout, query, shown, total, s):
    f_search, f_small = font("regular", round(17 * s)), font("regular", round(12 * s))
    cy, cx = layout.search_h / 2, 26 * s
    count = f"{shown}" if not query else f"{shown} of {total if total is not None else shown}"
    count_w = d.textlength(count, font=f_small)
    r_ = 7 * s
    d.ellipse((cx - r_, cy - r_ - 1 * s, cx + r_, cy + r_ - 1 * s), outline=MUTED, width=max(1, round(2 * s)))
    d.line((cx + r_ * 0.7, cy + r_ * 0.7 - 1 * s, cx + r_ * 1.6, cy + r_ * 1.6 - 1 * s), fill=MUTED,
           width=max(1, round(2 * s)))
    tx = 50 * s
    if query:
        text = fit(d, query, f_search, w - tx - count_w - 40 * s)
        d.text((tx, cy), text, font=f_search, fill=TEXT, anchor="lm")
        caret = tx + d.textlength(text, font=f_search) + 2 * s
        d.line((caret, cy - 10 * s, caret, cy + 10 * s), fill=TEXT, width=max(1, round(1.5 * s)))
    else:
        d.line((tx - 4 * s, cy - 10 * s, tx - 4 * s, cy + 10 * s), fill=TEXT, width=max(1, round(1.5 * s)))
        d.text((tx + 4 * s, cy), "Search windows", font=f_search, fill=MUTED, anchor="lm")
    d.text((w - 22 * s, cy), count, font=f_small, fill=MUTED, anchor="rm")
    d.line((0, layout.search_h, w, layout.search_h), fill=DIVIDER, width=1)
