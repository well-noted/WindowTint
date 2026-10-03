"""Grid layout for the window switcher: big cards in rows, like Windows' own Alt+Tab view.
Pure arithmetic (no tkinter, Pillow or Windows), so it is tested directly.

Everything is computed in device pixels: dimensions given in dip are multiplied by `scale`."""
from __future__ import annotations

from dataclasses import dataclass, field

MARGIN, GAP, CARD_PAD, HEADER = 26, 14, 8, 36   # dip
SEARCH_H, FOOTER_H = 60, 32
MIN_PANEL_W, MIN_CARD_W = 520, 150
THUMB_HEIGHTS = (230, 200, 172, 148, 128, 110, 94, 80, 68)
ASPECT_MIN, ASPECT_MAX = 0.72, 1.9


@dataclass
class Card:
    index: int            # position in the entry list
    row: int              # row number within the whole grid
    x: int
    y: int
    w: int
    h: int
    header_h: int
    pad: int
    aspect: float

    @property
    def thumb_box(self) -> tuple:
        """(left, top, right, bottom) of the area under the header where the window preview goes."""
        return (self.x + self.pad, self.y + self.header_h, self.x + self.w - self.pad, self.y + self.h - self.pad)

    def preview_rect(self) -> tuple:
        """The window's preview, as large as fits the thumbnail area while keeping its shape."""
        l, t, r, b = self.thumb_box
        bw, bh = r - l, b - t
        a = max(0.1, self.aspect)
        w, h = (bw, round(bw / a)) if bw / bh < a else (round(bh * a), bh)
        w, h = min(w, bw), min(h, bh)
        x, y = l + (bw - w) // 2, t + (bh - h) // 2
        return (x, y, x + w, y + h)


@dataclass
class GridLayout:
    width: int
    height: int
    cards: list                      # visible Card objects
    scale: float
    search_h: int = 0
    footer_h: int = 0
    thumb_h: int = 0
    rows_total: int = 0
    first_row: int = 0
    centers: dict = field(default_factory=dict)   # entry index -> (row, center_x), for ALL entries

    def card_for(self, index: int):
        return next((c for c in self.cards if c.index == index), None)

    def card_at(self, x: int, y: int):
        for c in self.cards:
            if c.x <= x < c.x + c.w and c.y <= y < c.y + c.h:
                return c
        return None


def _u(v: float, scale: float) -> int:
    return round(v * scale)


def _card_size(aspect: float, thumb_h: int, scale: float):
    a = min(ASPECT_MAX, max(ASPECT_MIN, aspect))
    pad = _u(CARD_PAD, scale)
    w = max(_u(MIN_CARD_W, scale), round(thumb_h * a) + 2 * pad)
    return w, _u(HEADER, scale) + thumb_h + pad


def _pack(aspects: list, thumb_h: int, avail_w: int, scale: float) -> list:
    """Greedy left-to-right rows: [[(entry index, card width), ...], ...]."""
    gap, rows, cur, cur_w = _u(GAP, scale), [], [], 0
    for i, a in enumerate(aspects):
        w, _h = _card_size(a, thumb_h, scale)
        if cur and cur_w + gap + w > avail_w:
            rows.append(cur)
            cur, cur_w = [], 0
        cur_w += (gap if cur else 0) + w
        cur.append((i, w))
    if cur:
        rows.append(cur)
    return rows


def compute_layout(aspects: list, selected: int, scale: float, max_w: int, max_h: int,
                   with_search: bool = False) -> GridLayout:
    """Pick the largest card size at which every window fits on screen, then position the cards.
    If even the smallest size overflows, only the rows around the selection are shown."""
    top = _u(SEARCH_H, scale) if with_search else 0
    bottom = _u(FOOTER_H, scale) if with_search else 0
    margin, gap = _u(MARGIN, scale), _u(GAP, scale)
    avail_w = max_w - 2 * margin
    if not aspects:
        w, h = _u(MIN_PANEL_W, scale), top + bottom + 2 * margin + _u(60, scale)
        return GridLayout(w, h, [], scale, top, bottom)

    chosen = THUMB_HEIGHTS[-1]
    for th in THUMB_HEIGHTS:
        th_px = _u(th, scale)
        rows = _pack(aspects, th_px, avail_w, scale)
        card_h = _card_size(1.0, th_px, scale)[1]
        total = top + bottom + 2 * margin + len(rows) * card_h + (len(rows) - 1) * gap
        chosen = th_px
        if total <= max_h:
            break
    rows = _pack(aspects, chosen, avail_w, scale)
    card_h = _card_size(1.0, chosen, scale)[1]

    row_widths = [sum(w for _i, w in r) + gap * (len(r) - 1) for r in rows]
    panel_w = max(_u(MIN_PANEL_W, scale), max(row_widths) + 2 * margin)
    room = max_h - top - bottom - 2 * margin
    visible_rows = max(1, min(len(rows), (room + gap) // (card_h + gap)))

    row_of = {i: r for r, row in enumerate(rows) for i, _w in row}
    sel_row = row_of.get(selected, 0)
    first = max(0, min(sel_row - visible_rows // 2, len(rows) - visible_rows))

    centers, cards = {}, []
    pad, header_h = _u(CARD_PAD, scale), _u(HEADER, scale)
    for r, row in enumerate(rows):
        x = (panel_w - row_widths[r]) // 2
        for i, w in row:
            centers[i] = (r, x + w // 2)
            if first <= r < first + visible_rows:
                y = top + margin + (r - first) * (card_h + gap)
                cards.append(Card(i, r, x, y, w, card_h, header_h, pad, aspects[i]))
            x += w + gap
    height = top + bottom + 2 * margin + visible_rows * card_h + (visible_rows - 1) * gap
    return GridLayout(panel_w, height, cards, scale, top, bottom, chosen, len(rows), first, centers)


def neighbor(layout: GridLayout, selected: int, direction: str, count: int) -> int:
    """Selection after an arrow key. Left/right walk the cards in order (wrapping); up/down jump to
    the card in the next row whose center is nearest to the current one."""
    if count <= 0:
        return 0
    if direction == "right":
        return (selected + 1) % count
    if direction == "left":
        return (selected - 1) % count
    if selected not in layout.centers:
        return selected
    row, cx = layout.centers[selected]
    step = 1 if direction == "down" else -1
    target = (row + step) % layout.rows_total
    if layout.rows_total == 1:
        return selected
    candidates = [(abs(c - cx), i) for i, (r, c) in layout.centers.items() if r == target]
    return min(candidates)[1] if candidates else selected
