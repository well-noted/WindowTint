"""Browser tab strip reading and drawing, against a fake UI Automation tree."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import overlay  # noqa: E402
import uia  # noqa: E402
from test_uia import FakeAuto, Node, Rect  # noqa: E402


def node(ctype, name="", rect=None, kids=()):
    n = Node(ctype, name, "")
    if rect:
        n.BoundingRectangle = Rect(*rect)
    n.children = list(kids)
    return n


def browser(titles, strip_rect=(0, 0, 1000, 40)):
    tabs = [node("TabItemControl", t, (i * 200, 5, i * 200 + 190, 40)) for i, t in enumerate(titles)]
    strip = node("TabControl", "Tab bar", strip_rect, tabs)
    # a page that has its own tab widget must not be mistaken for the browser's tab strip
    page_tabs = node("TabControl", "page tabs", (0, 100, 500, 140), [node("TabItemControl", "Docs", (0, 100, 90, 140))])
    page = node("DocumentControl", "Some page", (0, 60, 1000, 800), [page_tabs])
    return node("WindowControl", "Chrome", (0, 0, 1000, 800), [page, node("PaneControl", "toolbar", None, [strip])]), tabs


def vivaldi(titles):
    """Vivaldi keeps its tab bar inside the web area, next to page content that has tabs of its own."""
    items = [node("TabItemControl", t, (i * 170, 9, i * 170 + 172, 54)) for i, t in enumerate(titles)]
    strip = node("TabControl", "Tabs", (0, 9, 1700, 54), items)
    strip.ClassName = "tab-strip"
    bar = node("ToolBarControl", "Tabs", (0, 9, 2400, 54), [strip])
    addons = node("TabControl", "", (2316, 220, 2401, 1369), [node("TabItemControl", "Calendar", (2316, 220, 2401, 304))])
    addons.ClassName = "brC-bsf-aT5-aOt"
    doc = node("DocumentControl", "Inbox", (0, 0, 2400, 1529), [node("GroupControl", "", None, [bar, addons])])
    return node("WindowControl", "Vivaldi", None, [node("PaneControl", "RootView", None, [doc])]), items


class VivaldiTests(unittest.TestCase):
    def test_finds_tab_bar_inside_web_area_by_class_not_the_page_tabs(self):
        root, _ = vivaldi(["a", "b", "c"])
        strip = uia.find_tabstrip(root)
        self.assertEqual(strip.ClassName, "tab-strip")

    def test_snapshot_reads_vivaldi_tabs(self):
        root, _ = vivaldi(["a", "b"])
        snap = uia.TabTracker(FakeAuto(root)).update(1, 0.0)
        self.assertEqual([t for t, _r in snap.items], ["a", "b"])

    def test_page_tabs_alone_are_never_taken_for_a_tab_bar(self):
        root, _ = vivaldi(["a"])
        strip = root.children[0].children[0].children[0].children[0].children[0]
        strip.ClassName = ""      # no tab-strip class anywhere: nothing should be picked
        self.assertIsNone(uia.find_tabstrip(root))


class FindStripTests(unittest.TestCase):
    def test_finds_browser_strip_not_page_tabs(self):
        root, _tabs = browser(["a", "b"])
        strip = uia.find_tabstrip(root)
        self.assertEqual(strip.Name, "Tab bar")

    def test_no_strip_returns_none(self):
        self.assertIsNone(uia.find_tabstrip(node("WindowControl", "x", None, [node("ButtonControl", "ok")])))

    def test_is_browser(self):
        self.assertTrue(uia.is_browser("chrome.exe"))
        self.assertTrue(uia.is_browser("Vivaldi.EXE"))
        self.assertFalse(uia.is_browser("claude.exe"))


class TrackerTests(unittest.TestCase):
    def test_snapshot_lists_tab_titles_and_rects(self):
        root, _ = browser(["repo-a - GitHub", "repo-b - GitHub"])
        tr = uia.TabTracker(FakeAuto(root))
        snap = tr.update(1, 0.0)
        self.assertEqual([t for t, _r in snap.items], ["repo-a - GitHub", "repo-b - GitHub"])
        self.assertEqual(snap.items[1][1], (200, 5, 390, 40))
        self.assertEqual(snap.clip, (0, 0, 1000, 40))

    def test_new_tab_appears_after_rescan_and_move_triggers_reread(self):
        root, tabs = browser(["a", "b"])
        tr = uia.TabTracker(FakeAuto(root))
        tr.update(1, 0.0)
        strip = root.children[1].children[0]
        strip.children.append(node("TabItemControl", "c", (400, 5, 590, 40)))
        self.assertEqual(len(tr.update(1, 0.2).items), 2)        # between scans, nothing moved
        self.assertEqual(len(tr.update(1, 1.5).items), 3)        # rescan finds it
        tabs[1].BoundingRectangle = Rect(100, 5, 290, 40)        # a tab was dragged
        snap = tr.update(1, 2.2)
        self.assertEqual(snap.items[1][1], (100, 5, 290, 40))

    def test_offscreen_and_collapsed_tabs_are_skipped(self):
        root, tabs = browser(["a", "b", "c"])
        tabs[1].IsOffscreen = True
        tabs[2].BoundingRectangle = Rect(0, 0, 0, 0)
        snap = uia.TabTracker(FakeAuto(root)).update(1, 0.0)
        self.assertEqual([t for t, _ in snap.items], ["a"])

    def test_failed_scan_is_not_repeated_every_cycle(self):
        root = node("WindowControl", "x", (0, 0, 10, 10), [])
        tr = uia.TabTracker(FakeAuto(root))
        self.assertIsNone(tr.update(1, 0.0))
        Node.calls = 0
        self.assertIsNone(tr.update(1, 0.15))
        self.assertEqual(Node.calls, 0)
        root.children.append(node("TabControl", "t", (0, 0, 100, 30), [node("TabItemControl", "a", (0, 0, 50, 30))]))
        self.assertIsNotNone(tr.update(1, 1.2))      # found on the next scheduled scan

    def test_missing_strip_gives_none(self):
        root = node("WindowControl", "x", (0, 0, 10, 10), [])
        self.assertIsNone(uia.TabTracker(FakeAuto(root)).update(1, 0.0))


class NameTests(unittest.TestCase):
    def test_memory_usage_hover_text_is_removed(self):
        self.assertEqual(uia.tab_title("Jupyter Server - Memory usage - 22.7 MB"), "Jupyter Server")
        self.assertEqual(uia.tab_title("Canvas - Module 7 - Memory usage - 1,294 MB"), "Canvas - Module 7")
        self.assertEqual(uia.tab_title("New Tab"), "New Tab")


class MinimizedTests(unittest.TestCase):
    def test_minimized_window_gives_no_marks(self):
        root, tabs = browser(["a", "b"])
        root.children[1].children[0].BoundingRectangle = Rect(-31941, -32000, -31198, -31938)
        self.assertIsNone(uia.TabTracker(FakeAuto(root)).update(1, 0.0))


class RenderTests(unittest.TestCase):
    def test_tab_style_has_top_bar_and_light_fill(self):
        data = overlay.render_bgra(200, 50, [((0, 5, 190, 45), "#1e88e5")], style="tab")

        def px(x, y):
            i = (y * 200 + x) * 4
            return tuple(data[i:i + 4])
        b, g, r, a = px(100, 5)                       # on the top bar
        self.assertEqual((r, g, b, a), (0x1e, 0x88, 0xe5, 255))
        self.assertEqual(px(100, 30)[3], overlay.FILL_ALPHA)   # tinted body
        self.assertEqual(px(100, 48)[3], 0)                    # below the tab stays clear
        self.assertEqual(px(1, 5)[3] < 255, True)              # corner is not part of the solid bar


if __name__ == "__main__":
    unittest.main()
