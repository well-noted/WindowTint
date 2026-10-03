import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import switcher_logic as L  # noqa: E402
import switcher_layout as Lo  # noqa: E402
import switcher_render as R  # noqa: E402
from rules import WinInfo  # noqa: E402


def e(hwnd, title, exe="chrome.exe", project="", color=""):
    return L.Entry(hwnd, title, exe, L.app_name(exe), project, color)


class NameTests(unittest.TestCase):
    def test_app_names(self):
        self.assertEqual(L.app_name("chrome.exe"), "Chrome")
        self.assertEqual(L.app_name("WindowsTerminal.exe"), "Windows Terminal")
        self.assertEqual(L.app_name("MyCoolApp.exe"), "My Cool App")
        self.assertEqual(L.app_name("some_tool.exe"), "Some Tool")

    def test_in_app_text_replaces_generic_title(self):
        w = WinInfo(1, 2, "Claude", "claude.exe", ui="Netlogo Improvements / NetLogo UX")
        entry = L.make_entry(w, "Netlogo", "#e53935")
        self.assertEqual(entry.title, "Netlogo Improvements / NetLogo UX")
        self.assertEqual(entry.subtitle, "Netlogo  ·  Claude")
        self.assertEqual(L.make_entry(WinInfo(1, 2, "Untitled - Notepad", "notepad.exe"), "", "").title,
                         "Untitled - Notepad")


class FilterTests(unittest.TestCase):
    def setUp(self):
        self.es = [e(1, "Inbox - Gmail"), e(2, "repo-a - Visual Studio Code", "Code.exe", "Alpha"),
                   e(3, "Gmail settings", "vivaldi.exe"), e(4, "notes", "notepad.exe", "Alpha")]

    def test_empty_query_keeps_everything_in_order(self):
        self.assertEqual([x.hwnd for x in L.filter_entries(self.es, "")], [1, 2, 3, 4])

    def test_all_words_must_match_across_title_app_and_project(self):
        self.assertEqual([x.hwnd for x in L.filter_entries(self.es, "alpha code")], [2])
        self.assertEqual([x.hwnd for x in L.filter_entries(self.es, "zzz")], [])

    def test_prefix_matches_rank_first_then_recency(self):
        # 'gmail' starts window 3's title; it only appears mid-title in window 1
        self.assertEqual([x.hwnd for x in L.filter_entries(self.es, "gmail")], [3, 1])


class SelectionTests(unittest.TestCase):
    def test_starts_on_previous_window_like_alt_tab(self):
        es = [e(10, "a"), e(11, "b"), e(12, "c")]
        self.assertEqual(L.initial_selection(es, 10), 1)
        self.assertEqual(L.initial_selection(es, 99), 0)
        self.assertEqual(L.initial_selection(es[:1], 10), 0)

    def test_move_wraps(self):
        self.assertEqual(L.move(2, 1, 3), 0)
        self.assertEqual(L.move(0, -1, 3), 2)
        self.assertEqual(L.move(0, 1, 0), 0)

    def test_scroll_offset_keeps_selection_visible(self):
        self.assertEqual(L.scroll_offset(0, 0, 8, 5), 0)
        self.assertEqual(L.scroll_offset(8, 0, 8, 20), 1)
        self.assertEqual(L.scroll_offset(2, 5, 8, 20), 2)
        self.assertEqual(L.scroll_offset(19, 0, 8, 20), 12)
        self.assertEqual(L.scroll_offset(10, 11, 8, 20), 10)


class RenderTests(unittest.TestCase):
    def entries(self, n=3):
        return [e(i, f"Window {i}", project="P" if i % 2 else "", color="#1e88e5" if i % 2 else "") for i in range(n)]

    def lay(self, es, selected=0, scale=1.0, search=False, w=1600, h=900):
        return Lo.compute_layout([x.aspect for x in es], selected, scale, w, h, search)

    def test_image_matches_layout_size_in_both_modes(self):
        es = self.entries(5)
        for search in (False, True):
            lay = self.lay(es, search=search)
            img = R.render(es, lay, 0, mode="search" if search else "hold")
            self.assertEqual(img.size, (lay.width, lay.height))

    def test_project_color_appears_on_colored_cards_only(self):
        es = self.entries(2)
        lay = self.lay(es)
        img = R.render(es, lay, 0, mode="hold")
        blue = (0x1e, 0x88, 0xe5)

        def has_blue(card):
            return any(img.getpixel((x, y)) == blue for x in range(card.x, card.x + card.w)
                       for y in range(card.y, card.y + card.header_h))
        self.assertFalse(has_blue(lay.cards[0]))
        self.assertTrue(has_blue(lay.cards[1]))     # the project chip

    def test_selected_card_differs_from_unselected(self):
        es = self.entries()
        lay = self.lay(es)
        self.assertNotEqual(R.render(es, lay, 0, mode="hold").tobytes(), R.render(es, lay, 1, mode="hold").tobytes())

    def test_hold_and_search_modes_differ(self):
        es = self.entries(4)
        lay_h, lay_s = self.lay(es), self.lay(es, search=True)
        self.assertNotEqual(R.render(es, lay_h, 1, mode="hold").size, R.render(es, lay_s, 1, mode="search").size)

    def test_previews_skip_minimized_windows_and_stay_inside_their_cards(self):
        es = self.entries(3)
        es[1].minimized = True
        lay = self.lay(es)
        rects = R.preview_rects(es, lay)
        self.assertEqual(set(rects), {0, 2})
        for c in lay.cards:
            if c.index in rects:
                l, t, r, b = rects[c.index]
                self.assertTrue(c.x <= l < r <= c.x + c.w and c.y + c.header_h <= t < b <= c.y + c.h)

    def test_odd_inputs_render(self):
        R.render([], self.lay([]), 0, "zzz", "search")
        long = [e(1, "x" * 400, project="P" * 80, color="#43a047")]
        long[0].minimized = True
        R.render(long, self.lay(long, scale=2.0), 0, mode="hold", icons={1: None})
        many = [e(i, f"w{i}") for i in range(60)]
        R.render(many, self.lay(many, 20, w=1500, h=700, search=True), 20, "w", "search", total=60)

    def test_icon_is_drawn_when_given(self):
        from PIL import Image
        es = self.entries(1)
        lay = self.lay(es)
        red = Image.new("RGBA", (64, 64), (255, 0, 0, 255))
        img = R.render(es, lay, 0, mode="hold", icons={0: red})
        c = lay.cards[0]
        self.assertTrue(any(img.getpixel((x, y)) == (255, 0, 0) for x in range(c.x, c.x + 60)
                            for y in range(c.y, c.y + c.header_h)))


class LayoutTests(unittest.TestCase):
    def test_empty_layout(self):
        lay = Lo.compute_layout([], 0, 1.0, 1600, 900)
        self.assertEqual(lay.cards, [])

    def test_everything_fits_and_cards_stay_inside_the_panel(self):
        lay = Lo.compute_layout([1.7] * 12, 3, 1.5, 2160, 1200, True)
        self.assertEqual(len(lay.cards), 12)
        self.assertLessEqual(lay.height, 1200)
        for c in lay.cards:
            self.assertTrue(0 <= c.x and c.x + c.w <= lay.width and c.y >= lay.search_h and c.y + c.h <= lay.height - lay.footer_h)

    def test_cards_never_overlap(self):
        lay = Lo.compute_layout([1.7, 1.0, 0.8, 2.4, 1.6, 1.6, 1.6, 1.2, 1.9], 0, 1.0, 1200, 800)
        for a in lay.cards:
            for b in lay.cards:
                if a is not b:
                    self.assertTrue(a.x + a.w <= b.x or b.x + b.w <= a.x or a.y + a.h <= b.y or b.y + b.h <= a.y)

    def test_more_windows_means_smaller_cards(self):
        few = Lo.compute_layout([1.7] * 3, 0, 1.0, 1600, 900)
        many = Lo.compute_layout([1.7] * 30, 0, 1.0, 1600, 900)
        self.assertGreater(few.thumb_h, many.thumb_h)

    def test_too_many_windows_scroll_by_rows_and_keep_selection_visible(self):
        lay = Lo.compute_layout([1.7] * 80, 70, 1.0, 1500, 600, True)
        self.assertGreater(lay.rows_total, len({c.row for c in lay.cards}))
        self.assertIsNotNone(lay.card_for(70))
        self.assertLessEqual(lay.height, 600)

    def test_hit_testing_and_preview_shape(self):
        lay = Lo.compute_layout([2.0, 1.0], 0, 1.0, 1600, 900)
        c = lay.cards[0]
        self.assertIs(lay.card_at(c.x + 5, c.y + 5), c)
        self.assertIsNone(lay.card_at(-5, -5))
        l, t, r, b = c.preview_rect()
        self.assertAlmostEqual((r - l) / (b - t), 2.0, delta=0.1)

    def test_neighbor_moves_across_and_between_rows(self):
        lay = Lo.compute_layout([1.7] * 8, 0, 1.0, 900, 900)
        self.assertGreater(lay.rows_total, 1)
        self.assertEqual(Lo.neighbor(lay, 0, "right", 8), 1)
        self.assertEqual(Lo.neighbor(lay, 0, "left", 8), 7)
        first_row = [i for i, (r, _c) in lay.centers.items() if r == 0]
        down = Lo.neighbor(lay, 0, "down", 8)
        self.assertEqual(lay.centers[down][0], 1)
        self.assertEqual(Lo.neighbor(lay, down, "up", 8), 0)
        self.assertIn(0, first_row)

    def test_single_row_up_down_stays_put(self):
        lay = Lo.compute_layout([1.7] * 3, 1, 1.0, 1600, 900)
        self.assertEqual(Lo.neighbor(lay, 1, "down", 3), 1)


if __name__ == "__main__":
    unittest.main()
