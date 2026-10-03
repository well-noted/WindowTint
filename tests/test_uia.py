"""Tests the UI Automation reader against a fake tree built from a real dump of the app."""
import ast
import re
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import uia  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
LITERAL = r"""'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*\""""
LINE = re.compile(rf"^(\w+) name=({LITERAL})(?:.*? id=({LITERAL}))?")


class Rect:
    def __init__(self, left, top, right, bottom):
        self.left, self.top, self.right, self.bottom = left, top, right, bottom


class Node:
    calls = 0

    def __init__(self, ctype, name, aid):
        self.ControlTypeName, self.Name, self.AutomationId = ctype, name, aid
        self.children = []
        self.BoundingRectangle = Rect(0, 0, 0, 0)
        self.IsOffscreen = False
        self.scrollable = False
        self.rect_reads = 0

    def GetChildren(self):
        Node.calls += 1
        return list(self.children)

    def GetScrollPattern(self):
        if not self.scrollable:
            return None
        return types.SimpleNamespace(VerticallyScrollable=True)


class FakeAuto:
    def __init__(self, root):
        self.root = root

    def ControlFromHandle(self, hwnd):
        return self.root


def load(name):
    stack, root = [], None
    for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines():
        depth = (len(line) - len(line.lstrip(" "))) // 2
        m = LINE.match(line.strip())
        node = Node(m.group(1), ast.literal_eval(m.group(2)), ast.literal_eval(m.group(3)) if m.group(3) else "")
        del stack[depth:]
        if stack:
            stack[-1].children.append(node)
        else:
            root = node
        stack.append(node)
    return root


class ReaderTests(unittest.TestCase):
    def test_project_chat_returns_project_and_title(self):
        reader = uia.Reader(FakeAuto(load("claude_tree_project_chat.txt")))
        self.assertEqual(reader.read(1), "Netlogo Improvements / NetLogo UX improvements and modernization")

    def test_plain_chat_has_no_project_and_ignores_sidebar_links(self):
        reader = uia.Reader(FakeAuto(load("claude_tree_plain_chat.txt")))
        self.assertEqual(reader.read(1), "NetLogo UX improvements and modernization")

    def test_steady_state_uses_cheap_path(self):
        root = load("claude_tree_project_chat.txt")
        reader = uia.Reader(FakeAuto(root))
        reader.read(1)
        Node.calls = 0
        self.assertEqual(reader.read(1), "Netlogo Improvements / NetLogo UX improvements and modernization")
        self.assertLessEqual(Node.calls, 1)  # only the breadcrumb group is re-read

    def test_title_change_triggers_rescan(self):
        root = load("claude_tree_project_chat.txt")
        reader = uia.Reader(FakeAuto(root))
        reader.read(1)

        def find(node):
            if node.AutomationId == "RootWebArea" and node.Name:
                return node
            for c in node.children:
                hit = find(c)
                if hit:
                    return hit

        find(root).Name = "Another chat - Claude"
        self.assertEqual(reader.read(1), "Netlogo Improvements / Another chat")

    def test_title_change_does_not_search_the_whole_window_again(self):
        root = load("claude_tree_project_chat.txt")
        reader = uia.Reader(FakeAuto(root))
        reader.read(1)
        doc = reader._state[1].doc
        doc.Name = "Another chat - Claude"
        calls = []
        reader._find_doc = lambda r: calls.append(r)       # would be the slow whole-window search
        self.assertEqual(reader.read(1), "Netlogo Improvements / Another chat")
        self.assertEqual(calls, [])

    def test_no_document_returns_empty(self):
        root = Node("WindowControl", "Claude", "")
        self.assertEqual(uia.Reader(FakeAuto(root)).read(1), "")

    def test_compose_strips_suffix_only(self):
        self.assertEqual(uia.compose("Claude", ""), "Claude")
        self.assertEqual(uia.compose("Plan - Claude", "Proj"), "Proj / Plan")
        self.assertEqual(uia.compose("A - Claude notes - Claude", ""), "A - Claude notes")


def walk(node):
    yield node
    for c in node.children:
        yield from walk(c)


def find_by(root, ctype=None, name=None, aid=None):
    for n in walk(root):
        if ((ctype is None or n.ControlTypeName == ctype) and (name is None or n.Name == name)
                and (aid is None or n.AutomationId == aid)):
            return n


def give_rects(root):
    """Row-like geometry: every node gets a 250 x 18 box, 20 px apart, in tree order."""
    for i, n in enumerate(walk(root)):
        n.BoundingRectangle = Rect(100, 50 + 20 * i, 350, 68 + 20 * i)
    return root


def row_tuples(rows):
    return [(r.project, r.title) for r in rows]


class SidebarScanTests(unittest.TestCase):
    def setUp(self):
        self.root = load("claude_tree_sidebar.txt")
        self.sidebar = uia.find_sidebar(FakeAuto(self.root).ControlFromHandle(1))

    def test_sidebar_is_found(self):
        self.assertIsNotNone(self.sidebar)
        self.assertEqual(self.sidebar.AutomationId, "frame-peek-popover")

    def test_rows_get_their_project(self):
        rows, _ = uia.scan_sidebar(self.sidebar)
        got = row_tuples(rows)
        self.assertIn(("Netlogo Improvements", "NetLogo UX improvements and modernization"), got)
        self.assertIn(("Netlogo Improvements", "NetLogo 7.1.0 BehaviorSpace thread deadlock with nw extension"), got)
        self.assertIn(("WYSIWYG Tiddlywiki", "Milieu plugin redesign"), got)
        self.assertIn(("Tiddlywiki Notebooks Plugin", "Test notebooks and Milieu plugin files needed"), got)
        self.assertIn(("Arificial Anasazi", "NetLogo project file uploads needed"), got)

    def test_chats_outside_projects_have_no_project(self):
        rows, _ = uia.scan_sidebar(self.sidebar)
        got = row_tuples(rows)
        self.assertIn(("", "Window color context app"), got)
        self.assertIn(("", "Update scientific-voice skill from revision learnings"), got)
        self.assertIn(("", "AI-managed wiki system with content transfer"), got)

    def test_navigation_and_headers_are_not_rows(self):
        rows, _ = uia.scan_sidebar(self.sidebar)
        titles = {r.title for r in rows}
        for not_a_chat in ("View all", "Home", "Code", "Projects", "Artifacts", "Dispatch", "Netlogo Improvements",
                           "WYSIWYG Tiddlywiki", "Thomas", "Pinned", "Chats and tasks", "Search"):
            self.assertNotIn(not_a_chat, titles)

    def test_row_text_matches_window_level_format(self):
        rows, _ = uia.scan_sidebar(self.sidebar)
        row = next(r for r in rows if r.title == "NetLogo UX improvements and modernization")
        self.assertEqual(row.text, "Netlogo Improvements / NetLogo UX improvements and modernization")
        plain = next(r for r in rows if r.title == "Window color context app")
        self.assertEqual(plain.text, "Window color context app")

    def test_clip_defaults_to_sidebar_and_prefers_scrollable_ancestor(self):
        _, clip = uia.scan_sidebar(self.sidebar)
        self.assertIs(clip, self.sidebar)
        first_row = find_by(self.sidebar, "ButtonControl", "Mark as unread Update scientific-voice skill from revision learnings")
        holder = next(n for n in walk(self.sidebar) if first_row in n.children)
        # make the row's grandparent the scroll container
        scroll = next(n for n in walk(self.sidebar) if holder in n.children)
        scroll.scrollable = True
        _, clip = uia.scan_sidebar(self.sidebar)
        self.assertIs(clip, scroll)


class SidebarTrackerTests(unittest.TestCase):
    def setUp(self):
        self.root = give_rects(load("claude_tree_sidebar.txt"))
        self.auto = FakeAuto(self.root)
        self.tracker = uia.SidebarTracker(self.auto)
        self.sidebar = find_by(self.root, aid="frame-peek-popover")
        self.sidebar.BoundingRectangle = Rect(100, 0, 350, 10000)  # tall enough to show everything

    def test_snapshot_lists_visible_rows_with_text_and_clipped_rects(self):
        snap = self.tracker.update(1, 0.0)
        texts = [t for t, _ in snap.items]
        self.assertIn("Netlogo Improvements / NetLogo UX improvements and modernization", texts)
        self.assertIn("Window color context app", texts)
        for _, r in snap.items:
            self.assertGreaterEqual(r[0], snap.clip[0])
            self.assertLessEqual(r[2], snap.clip[2])
            self.assertGreater(r[3], r[1])

    def test_rows_outside_the_clip_are_dropped(self):
        full = self.tracker.update(1, 0.0)
        cut = sorted(r[3] for _, r in full.items)[len(full.items) // 2]
        self.sidebar.BoundingRectangle = Rect(100, 0, 350, cut)
        part = uia.SidebarTracker(self.auto).update(1, 0.0)
        self.assertTrue(0 < len(part.items) < len(full.items))
        self.assertTrue(all(r[3] <= cut for _, r in part.items))

    def test_offscreen_rows_are_skipped(self):
        row = find_by(self.root, "ButtonControl", "Idle Window color context app") or \
            find_by(self.root, "ButtonControl", "Mark as unread Window color context app")
        row.IsOffscreen = True
        snap = self.tracker.update(1, 0.0)
        self.assertNotIn("Window color context app", [t for t, _ in snap.items])

    def test_unchanged_layout_reuses_snapshot_cheaply(self):
        first = self.tracker.update(1, 0.0)
        Node.calls = 0
        second = self.tracker.update(1, 0.5)
        self.assertIs(second, first)
        self.assertEqual(Node.calls, 0)  # no tree walking, just two rectangles

    def test_scroll_triggers_a_full_reread(self):
        first = self.tracker.update(1, 0.0)
        first_row = None
        for n in walk(self.root):
            if n.ControlTypeName == "ButtonControl" and n.Name.endswith("Update scientific-voice skill from revision learnings"):
                first_row = n
                break
        old = first_row.BoundingRectangle
        first_row.BoundingRectangle = Rect(old.left, old.top - 40, old.right, old.bottom - 40)
        second = self.tracker.update(1, 0.5)
        self.assertIsNot(second, first)

    def test_row_with_a_different_status_glyph_is_still_a_row(self):
        """While a reply streams the status icon is not the usual image."""
        row = find_by(self.root, "ButtonControl", "Idle Window color context app") or \
            find_by(self.root, "ButtonControl", "Mark as unread Window color context app")
        row.Name = "Working Window color context app"
        for kid in row.children:
            if kid.ControlTypeName in ("ImageControl", "ButtonControl"):
                kid.ControlTypeName = "ProgressBarControl"
        rows, _ = uia.scan_sidebar(self.sidebar)
        self.assertIn(("", "Window color context app"), row_tuples(rows))

    def test_rows_that_the_app_re_rendered_are_picked_up_without_waiting(self):
        first = self.tracker.update(1, 0.0)
        self.assertIn("Window color context app", [t for t, _ in first.items])
        # the app replaces the row's elements: old ones go stale, new ones carry the same text
        old = find_by(self.root, "ButtonControl", "Idle Window color context app") or \
            find_by(self.root, "ButtonControl", "Mark as unread Window color context app")
        parent = next(n for n in walk(self.root) if old in n.children)
        fresh = Node(old.ControlTypeName, "Working Window color context app", "")
        fresh.children = [Node(k.ControlTypeName, k.Name, k.AutomationId) for k in old.children]
        for new_kid, old_kid in zip(fresh.children, old.children):
            new_kid.children = [Node(g.ControlTypeName, g.Name, g.AutomationId) for g in old_kid.children]
        parent.children = [fresh if c is old else c for c in parent.children]
        give_rects(self.root)
        self.sidebar.BoundingRectangle = Rect(100, 0, 350, 10000)
        old.BoundingRectangle = None          # stale: reading it fails
        again = self.tracker.update(1, 2.1)   # next full read (2 s) finds the stale row and rescans at once
        self.assertIn("Window color context app", [t for t, _ in again.items])

    def test_missing_sidebar_returns_none(self):
        empty = FakeAuto(Node("WindowControl", "Claude", ""))
        self.assertIsNone(uia.SidebarTracker(empty).update(1, 0.0))


if __name__ == "__main__":
    unittest.main()
