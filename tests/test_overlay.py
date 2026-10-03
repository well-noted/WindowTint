import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import overlay  # noqa: E402


def pixel(data, width, x, y):
    i = (y * width + x) * 4
    return tuple(data[i:i + 4])  # B, G, R, A (premultiplied)


class RenderTests(unittest.TestCase):
    def test_size_and_transparent_background(self):
        data = overlay.render_bgra(40, 30, [])
        self.assertEqual(len(data), 40 * 30 * 4)
        self.assertEqual(set(data), {0})

    def test_bar_is_opaque_and_in_bgr_order(self):
        data = overlay.render_bgra(100, 40, [((0, 0, 100, 30), "#e53935")])
        b, g, r, a = pixel(data, 100, 1, 15)   # inside the left bar
        self.assertEqual(a, 255)
        self.assertEqual((r, g, b), (0xe5, 0x39, 0x35))

    def test_fill_is_translucent_and_premultiplied(self):
        data = overlay.render_bgra(100, 40, [((0, 0, 100, 30), "#e53935")])
        b, g, r, a = pixel(data, 100, 60, 15)  # inside the row, away from the bar
        self.assertEqual(a, overlay.FILL_ALPHA)
        self.assertLessEqual(r, a)             # premultiplied: color channel cannot exceed alpha
        self.assertGreater(r, 0)

    def test_outside_rows_stays_clear_and_tiny_rows_are_skipped(self):
        data = overlay.render_bgra(100, 60, [((0, 0, 100, 20), "#1e88e5"), ((0, 30, 100, 31), "#43a047")])
        self.assertEqual(pixel(data, 100, 50, 50), (0, 0, 0, 0))
        self.assertEqual(pixel(data, 100, 50, 30), (0, 0, 0, 0))


class FakeMark:
    created = []

    def __init__(self, owner):
        self.owner, self.owned, self.visible, self.hidden_owner = owner, True, False, False
        self.draws = 0
        FakeMark.created.append(self)

    def owner_hidden(self):
        return self.hidden_owner

    def owner_is_front(self):
        return True

    def draw(self, *a):
        self.draws += 1
        self.visible = True

    def hide(self):
        self.visible = False


class StaleMarkTests(unittest.TestCase):
    """A minimized browser must not get its tab marks shown again from a stale snapshot."""

    def setUp(self):
        import types
        self.saved = overlay.MarkWindow
        overlay.MarkWindow = FakeMark
        FakeMark.created = []
        snap = types.SimpleNamespace(clip=(0, 0, 400, 60), items=[("Tab one", (10, 5, 150, 50))])
        self.mgr = overlay.OverlayManager(lambda hwnd: snap, lambda hwnd, text: "#43a047", style="tab")
        self.marks, self.last = {}, {}

    def tearDown(self):
        overlay.MarkWindow = self.saved

    def test_marks_hide_while_the_owner_is_minimized_and_return_with_it(self):
        self.mgr._update(7, self.marks, self.last)
        mark = self.marks[7]
        self.assertTrue(mark.visible)
        mark.hidden_owner = True
        self.mgr._update(7, self.marks, self.last)          # snapshot is still the old one
        self.assertFalse(mark.visible)
        draws = mark.draws
        self.mgr._update(7, self.marks, self.last)
        self.assertEqual(mark.draws, draws)                 # and nothing redraws them
        mark.hidden_owner = False
        self.mgr._update(7, self.marks, self.last)
        self.assertTrue(mark.visible)


if __name__ == "__main__":
    unittest.main()
