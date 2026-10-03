import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import icons  # noqa: E402


class AlphaTests(unittest.TestCase):
    def test_opaque_transparent_and_half_pixels(self):
        opaque = icons.alpha_from_renders(bytes([100, 50, 20, 255]), bytes([100, 50, 20, 255]), 1)
        self.assertEqual(opaque.getpixel((0, 0)), (20, 50, 100, 255))     # BGRA in, RGBA out
        clear = icons.alpha_from_renders(bytes([0, 0, 0, 255]), bytes([255, 255, 255, 255]), 1)
        self.assertEqual(clear.getpixel((0, 0))[3], 0)
        half = icons.alpha_from_renders(bytes([50, 25, 10, 255]), bytes([177, 152, 137, 255]), 1)
        self.assertEqual(half.getpixel((0, 0)), (20, 50, 100, 128))


if __name__ == "__main__":
    unittest.main()
