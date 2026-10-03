import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import frame_overlay as fo  # noqa: E402


def px(data, width, x, y):
    i = (y * width + x) * 4
    return tuple(data[i:i + 4])      # B, G, R, A (premultiplied)


class RenderFrameTests(unittest.TestCase):
    def test_ring_is_solid_in_the_band_and_transparent_inside(self):
        w, h, ring = 120, 80, 4
        data = fo.render_frame(w, h, ring, 8, "#1e88e5")
        self.assertEqual(len(data), w * h * 4)
        b, g, r, a = px(data, w, w // 2, 2)                  # top edge, middle: inside the band
        self.assertGreaterEqual(a, 250)
        for got, want in ((r, 0x1e), (g, 0x88), (b, 0xe5)):
            self.assertAlmostEqual(got, want, delta=3)
        self.assertEqual(px(data, w, w // 2, h // 2)[3], 0)    # the window itself stays clear
        self.assertLessEqual(px(data, w, w // 2, ring + 3)[3], 2)  # just inside the band
        self.assertGreaterEqual(px(data, w, 2, h // 2)[3], 250)  # left edge band

    def test_outer_corners_are_rounded(self):
        data = fo.render_frame(100, 60, 3, 8, "#e53935")
        self.assertEqual(px(data, 100, 0, 0)[3], 0)            # the very corner pixel is cut off
        self.assertGreater(px(data, 100, 50, 0)[3], 200)

    def test_thicker_ring_covers_more_pixels(self):
        def covered(ring):
            d = fo.render_frame(100, 80, ring, 8, "#43a047")
            return sum(1 for i in range(3, len(d), 4) if d[i] > 128)
        self.assertGreater(covered(6), covered(2))

    def test_tiny_sizes_do_not_crash(self):
        fo.render_frame(1, 1, 1, 8, "#000000")
        fo.render_frame(10, 10, 20, 0, "#000000")


if __name__ == "__main__":
    unittest.main()
