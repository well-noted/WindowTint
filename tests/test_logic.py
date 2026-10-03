import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from config import Config, normalize  # noqa: E402
from rules import FORCE_NONE, WinInfo, cwd_matches, resolve_project, rule_matches  # noqa: E402

W = WinInfo(hwnd=1, pid=10, title="repo-a - Visual Studio Code", exe="Code.exe")
NAMES = ["A", "B"]
NO_CWD = lambda: []  # noqa: E731


class RuleTests(unittest.TestCase):
    def test_title_regex_case_insensitive(self):
        self.assertTrue(rule_matches({"title": r"REPO-A\b"}, W, NO_CWD))
        self.assertFalse(rule_matches({"title": "repo-b"}, W, NO_CWD))

    def test_bad_regex_does_not_crash(self):
        self.assertFalse(rule_matches({"title": "("}, W, NO_CWD))

    def test_process_glob_with_and_without_exe(self):
        self.assertTrue(rule_matches({"process": "code"}, W, NO_CWD))
        self.assertTrue(rule_matches({"process": "Code.exe"}, W, NO_CWD))
        self.assertTrue(rule_matches({"process": "co*"}, W, NO_CWD))
        self.assertFalse(rule_matches({"process": "chrome"}, W, NO_CWD))

    def test_conditions_are_anded(self):
        self.assertTrue(rule_matches({"title": "repo-a", "process": "code"}, W, NO_CWD))
        self.assertFalse(rule_matches({"title": "repo-a", "process": "chrome"}, W, NO_CWD))

    def test_empty_rule_never_matches(self):
        self.assertFalse(rule_matches({"title": "", "process": "", "cwd": ""}, W, NO_CWD))

    def test_cwd_prefix_boundaries(self):
        self.assertTrue(cwd_matches(r"C:\code\a", r"C:\code\a"))
        self.assertTrue(cwd_matches(r"C:\code\a", r"c:\CODE\a\src"))
        self.assertTrue(cwd_matches("C:/code/a/", r"C:\code\a\src"))
        self.assertFalse(cwd_matches(r"C:\code\a", r"C:\code\ab"))
        self.assertTrue(cwd_matches("C:\\", r"C:\anything"))

    def test_cwd_rule_uses_any_descendant(self):
        cwds = lambda: [r"C:\Users\x", r"C:\code\a\sub"]  # noqa: E731
        self.assertTrue(rule_matches({"cwd": r"C:\code\a"}, W, cwds))
        self.assertFalse(rule_matches({"cwd": r"C:\code\b"}, W, cwds))

    def test_ui_text_rule(self):
        w = WinInfo(2, 11, "Claude", "claude.exe", ui="Netlogo Improvements / NetLogo UX improvements")
        self.assertTrue(rule_matches({"process": "claude", "ui": r"^Netlogo Improvements /"}, w, NO_CWD))
        self.assertFalse(rule_matches({"process": "claude", "ui": r"^WYSIWYG"}, w, NO_CWD))
        self.assertFalse(rule_matches({"ui": "("}, w, NO_CWD))
        # Window text not read yet (probe still starting): never matches by accident.
        self.assertFalse(rule_matches({"ui": "Netlogo"}, WinInfo(2, 11, "Claude", "claude.exe"), NO_CWD))

    def test_cwd_not_evaluated_when_cheaper_fields_fail(self):
        def boom():
            raise AssertionError("cwds should not be computed")
        self.assertFalse(rule_matches({"process": "chrome", "cwd": "C:\\x"}, W, boom))


class ResolveTests(unittest.TestCase):
    rules = [
        {"project": "A", "title": "repo-a", "process": "", "cwd": ""},
        {"project": "B", "title": "repo", "process": "", "cwd": ""},
        {"project": "GONE", "title": "repo", "process": "", "cwd": ""},
    ]

    def test_first_matching_rule_wins(self):
        self.assertEqual(resolve_project(NAMES, self.rules, W, {}, NO_CWD), ("A", "rule"))

    def test_manual_beats_rules(self):
        self.assertEqual(resolve_project(NAMES, self.rules, W, {1: "B"}, NO_CWD), ("B", "manual"))

    def test_force_none(self):
        self.assertEqual(resolve_project(NAMES, self.rules, W, {1: FORCE_NONE}, NO_CWD), (None, "manual"))

    def test_manual_for_deleted_project_falls_back_to_rules(self):
        self.assertEqual(resolve_project(NAMES, self.rules, W, {1: "ZZZ"}, NO_CWD), ("A", "rule"))

    def test_rule_for_unknown_project_skipped(self):
        rules = [self.rules[2]]
        self.assertEqual(resolve_project(NAMES, rules, W, {}, NO_CWD), (None, ""))


class ConfigTests(unittest.TestCase):
    def test_normalize_drops_bad_entries(self):
        data = normalize({
            "poll_ms": 5,
            "hotkey_modifiers": "bogus",
            "projects": [
                {"name": "ok", "color": "#AABBCC"},
                {"name": "ok", "color": "#112233"},
                {"name": "", "color": "#112233"},
                {"name": "badcolor", "color": "red"},
                "junk",
            ],
            "rules": [
                {"project": "ok", "title": "x"},
                {"project": "ok"},
                {"title": "x"},
            ],
        })
        self.assertEqual(data["poll_ms"], 100)
        self.assertEqual(data["hotkey_modifiers"], "ctrl+alt")
        self.assertEqual(data["projects"], [{"name": "ok", "color": "#aabbcc"}])
        self.assertEqual(len(data["rules"]), 1)

    def test_roundtrip_and_rename_delete(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(Path(d) / "c.json")
            cfg.load()
            self.assertTrue((Path(d) / "c.json").exists())
            cfg.rules.append({"project": "Project A", "title": "x", "process": "", "cwd": ""})
            cfg.rename_project("Project A", "Alpha")
            cfg.save()
            cfg2 = Config(Path(d) / "c.json")
            cfg2.load()
            self.assertIn("Alpha", cfg2.project_names())
            self.assertEqual(cfg2.rules[0]["project"], "Alpha")
            cfg2.delete_project("Alpha")
            self.assertEqual(cfg2.rules, [])

    def test_corrupt_file_recovers(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            p.write_text("{not json", encoding="utf-8")
            cfg = Config(p)
            cfg.load()
            self.assertEqual(len(cfg.projects), 5)
            self.assertTrue(p.with_suffix(".json.bad").exists())
            json.loads(p.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
