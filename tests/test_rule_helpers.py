import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import rule_helpers as h  # noqa: E402
from rules import WinInfo  # noqa: E402


class SuggestionTests(unittest.TestCase):
    def test_regex_escape_keeps_dashes_and_spaces_readable(self):
        self.assertEqual(h.regex_escape("repo-a v2"), "repo-a v2")
        self.assertEqual(h.regex_escape("a.b (c)"), r"a\.b \(c\)")

    def test_title_pattern_takes_the_document_part(self):
        self.assertEqual(h.suggest_title_pattern("repo-a - Visual Studio Code"), "repo-a")
        self.assertEqual(h.suggest_title_pattern("Untitled - Notepad"), "Untitled")
        self.assertEqual(h.suggest_title_pattern("Windows PowerShell"), "Windows PowerShell")
        self.assertEqual(h.suggest_title_pattern("x" * 100), "x" * 40)

    def test_title_pattern_actually_matches_its_source(self):
        for title in ("repo-a - Visual Studio Code", "a.b (c) - App", "C:\\code\\x - Terminal"):
            self.assertTrue(re.search(h.suggest_title_pattern(title), title), title)

    def test_process_stem_and_terminals(self):
        self.assertEqual(h.process_stem("claude.exe"), "claude")
        self.assertEqual(h.process_stem("Code.EXE"), "Code")
        self.assertTrue(h.is_terminal("WindowsTerminal.exe"))
        self.assertFalse(h.is_terminal("chrome.exe"))

    def test_ui_patterns_for_project_and_plain_chats(self):
        self.assertEqual(h.suggest_ui_patterns("Netlogo Improvements / NetLogo UX"),
                         ["^Netlogo Improvements /", "^Netlogo Improvements / NetLogo UX$"])
        self.assertEqual(h.suggest_ui_patterns("Window color context app"), ["^Window color context app$"])
        self.assertEqual(h.suggest_ui_patterns(""), [])

    def test_ui_patterns_match_the_text_they_came_from(self):
        text = "Proj (v2) / Chat [draft]"
        for pat in h.suggest_ui_patterns(text):
            self.assertTrue(re.search(pat, text), pat)
        self.assertFalse(re.search(h.suggest_ui_patterns(text)[0], "Other / Chat [draft]"))

    def test_describe_ui_pattern(self):
        ui = "Netlogo Improvements / NetLogo UX"
        self.assertIn("project", h.describe_ui_pattern("^Netlogo Improvements /", ui))
        self.assertIn("exact", h.describe_ui_pattern(f"^{h.regex_escape(ui)}$", ui))

    def test_cwd_candidates_prefers_specific_and_drops_home(self):
        got = h.cwd_candidates([r"C:\Users\tetuo", r"C:\Users\tetuo\code\windowtint", r"C:\Windows\System32",
                                r"C:\Users\tetuo\code", r"C:\Users\tetuo\code\windowtint"], home=r"C:\Users\tetuo")
        self.assertEqual(got, [r"C:\Users\tetuo\code\windowtint", r"C:\Users\tetuo\code"])
        self.assertEqual(h.cwd_candidates([r"C:\Users\tetuo"], home=r"C:\Users\tetuo"), [])


class DefaultsTests(unittest.TestCase):
    def test_claude_window_uses_program_and_in_app_text(self):
        w = WinInfo(1, 2, "Claude", "claude.exe", ui="Netlogo Improvements / Some chat")
        d = h.defaults_for_window(w)
        self.assertEqual(d["process"], "claude")
        self.assertEqual(d["ui"], "^Netlogo Improvements /")
        self.assertEqual(d["use"], {"process": True, "title": False, "ui": True, "cwd": False})

    def test_terminal_uses_program_and_folder(self):
        w = WinInfo(1, 2, "Windows PowerShell", "WindowsTerminal.exe")
        d = h.defaults_for_window(w, [r"C:\Users\tetuo\code\windowtint"], home=r"C:\Users\tetuo")
        self.assertTrue(d["use"]["cwd"])
        self.assertEqual(d["cwd"], r"C:\Users\tetuo\code\windowtint")

    def test_browser_uses_program_and_title_so_tabs_get_colored(self):
        d = h.defaults_for_window(WinInfo(1, 2, "repo-a - GitHub - Google Chrome", "chrome.exe"))
        self.assertTrue(d["use"]["title"] and d["use"]["process"])
        self.assertEqual(d["title"], "repo-a")

    def test_plain_app_uses_program_only(self):
        d = h.defaults_for_window(WinInfo(1, 2, "Untitled - Notepad", "notepad.exe"))
        self.assertEqual(d["use"], {"process": True, "title": False, "ui": False, "cwd": False})
        self.assertEqual(d["title"], "Untitled")


class BuildAndValidateTests(unittest.TestCase):
    def test_build_rule_only_keeps_switched_on_fields(self):
        r = h.build_rule("A", {"process": True, "ui": True}, {"process": " claude ", "title": "x", "ui": "^P /"})
        self.assertEqual(r, {"project": "A", "title": "", "process": "claude", "cwd": "", "ui": "^P /"})

    def test_validation_messages(self):
        ok = {"project": "A", "title": "", "process": "claude", "cwd": "", "ui": "^P /"}
        self.assertEqual(h.validate_rule(ok), "")
        self.assertIn("project", h.validate_rule({**ok, "project": ""}).lower())
        self.assertIn("condition", h.validate_rule({**ok, "process": "", "ui": ""}))
        self.assertIn("regular expression", h.validate_rule({**ok, "title": "("}))
        self.assertIn("program", h.validate_rule({**ok, "process": ""}))

    def test_next_color_skips_used_ones(self):
        self.assertEqual(h.next_color([]), h.PALETTE[0])
        self.assertEqual(h.next_color([h.PALETTE[0].upper(), h.PALETTE[1]]), h.PALETTE[2])
        self.assertIn(h.next_color(h.PALETTE), h.PALETTE)


class PreviewTests(unittest.TestCase):
    wins = [
        WinInfo(1, 10, "Claude", "claude.exe", ui="Netlogo Improvements / A"),
        WinInfo(2, 11, "Claude", "claude.exe", ui="WYSIWYG Tiddlywiki / B"),
        WinInfo(3, 12, "Untitled - Notepad", "notepad.exe"),
        WinInfo(4, 13, "Windows PowerShell", "WindowsTerminal.exe"),
    ]

    def cwds(self, w):
        return [r"C:\code\windowtint"] if w.pid == 13 else []

    def test_preview_counts_matching_windows(self):
        rule = {"project": "A", "process": "claude", "ui": "^Netlogo Improvements /", "title": "", "cwd": ""}
        self.assertEqual([w.hwnd for w in h.preview_matches(rule, self.wins, self.cwds)], [1])
        rule = {"project": "A", "process": "claude", "ui": "", "title": "", "cwd": ""}
        self.assertEqual([w.hwnd for w in h.preview_matches(rule, self.wins, self.cwds)], [1, 2])

    def test_preview_folder_rule(self):
        rule = {"project": "A", "process": "WindowsTerminal", "ui": "", "title": "", "cwd": r"C:\code"}
        self.assertEqual([w.hwnd for w in h.preview_matches(rule, self.wins, self.cwds)], [4])

    def test_preview_of_empty_rule_matches_nothing(self):
        self.assertEqual(h.preview_matches({"project": "A"}, self.wins, self.cwds), [])


class DescribeRuleTests(unittest.TestCase):
    def test_words(self):
        from rule_helpers import describe_rule
        self.assertEqual(describe_rule({"process": "claude", "ui": "^X /"}),
                         'program is claude and in-app text matches "^X /"')
        self.assertEqual(describe_rule({}), "no conditions yet")


if __name__ == "__main__":
    unittest.main()
