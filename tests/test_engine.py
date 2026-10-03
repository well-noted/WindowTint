"""Engine test with a fake winapi module, so it runs on any OS."""
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rules import WinInfo  # noqa: E402

DEFAULT = 0xFFFFFFFF


class FakeApi(types.ModuleType):
    DWMWA_COLOR_DEFAULT = DEFAULT

    def __init__(self):
        super().__init__("winapi")
        self.windows = []
        self.alive = set()
        self.calls = []
        self.fg = 0
        self.current = {}

    class ProcCache:
        def exe(self, pid):
            return "x.exe"

        def cwds(self, pid):
            return []

    def enum_windows(self, procs):
        return list(self.windows)

    def window_info(self, hwnd, procs):
        return next((w for w in self.windows if w.hwnd == hwnd), None)

    def is_window(self, h):
        return h in self.alive

    def get_foreground(self):
        return self.fg

    def set_colors(self, h, b, c, t):
        self.calls.append(("set", h, b, c, t))
        self.current[h] = (b, c)

    def reset_colors(self, h):
        self.calls.append(("reset", h))
        self.current[h] = (DEFAULT, DEFAULT)

    def read_colors(self, h):
        return self.current.get(h, (DEFAULT, DEFAULT))

    def hex_to_colorref(self, c):
        return int(c[1:3], 16) | int(c[3:5], 16) << 8 | int(c[5:7], 16) << 16

    def contrast_colorref(self, c):
        return 0xFFFFFF


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        sys.modules["winapi"] = self.api
        sys.modules.pop("engine", None)
        import config
        self.tmp = tempfile.TemporaryDirectory()
        config.app_dir = lambda: Path(self.tmp.name)
        import engine
        engine.app_dir = lambda: Path(self.tmp.name)
        self.cfg = config.Config(Path(self.tmp.name) / "c.json")
        self.cfg.load()
        self.cfg.rules.append({"project": "Project B", "title": "repo-b", "process": "", "cwd": ""})
        self.eng = engine.Engine(self.cfg)
        self.eng._pid_of = lambda hwnd: 4242
        self.w = WinInfo(7, 99, "repo-b - shell", "x.exe")
        self.api.windows = [self.w]
        self.api.alive = {7}

    def tearDown(self):
        sys.modules.pop("winapi", None)
        self.tmp.cleanup()

    def test_rule_applies_once_then_reapplies_periodically(self):
        self.eng.tick()
        sets = [c for c in self.api.calls if c[0] == "set"]
        self.assertEqual(len(sets), 1)
        self.assertEqual(sets[0][1], 7)
        blue = 0x1e | 0x88 << 8 | 0xe5 << 16
        self.assertEqual(sets[0][2], blue)
        self.eng.tick()
        self.assertEqual(len([c for c in self.api.calls if c[0] == "set"]), 1)
        for _ in range(10):
            self.eng.tick()
        self.assertGreater(len([c for c in self.api.calls if c[0] == "set"]), 1)

    def test_color_reset_by_the_app_is_restored_on_next_tick(self):
        self.eng.tick()
        sets = lambda: len([c for c in self.api.calls if c[0] == "set"])  # noqa: E731
        self.assertEqual(sets(), 1)
        self.api.current[7] = (DEFAULT, DEFAULT)  # the app put its own colors back
        self.eng.tick()
        self.assertEqual(sets(), 2)
        self.eng.tick()
        self.assertEqual(sets(), 2)  # and once restored, no further writes

    def test_hotkey_assigns_and_zero_returns_to_auto(self):
        self.api.fg = 7
        self.eng.events.put(("hotkey", 1, 7))
        self.eng.tick()
        self.assertEqual(self.eng.project_of(7), ("Project A", "manual"))
        self.eng.events.put(("hotkey", 10, 7))
        self.eng.tick()
        self.assertEqual(self.eng.project_of(7), ("Project B", "rule"))

    def test_new_rule_hotkey_calls_back_with_window(self):
        got = []
        self.eng.on_new_rule = got.append
        self.eng.events.put(("hotkey", 11, 7))
        self.eng.tick()
        self.assertEqual(got, [7])

    def test_tab_color_uses_title_rules_only(self):
        self.cfg.rules.append({"project": "Project C", "title": "", "process": "x", "cwd": "/z", "ui": ""})
        self.eng.tick()
        self.assertEqual(self.eng.tab_color(7, "repo-b - docs"), "#1e88e5")
        self.assertIsNone(self.eng.tab_color(7, "something else"))   # cwd rule never applies to a tab
        self.assertIsNone(self.eng.tab_color(999, "repo-b"))         # unknown window

    def test_tab_color_also_applies_in_app_text_rules_that_name_a_program(self):
        self.cfg.rules.insert(0, {"project": "Project D", "title": "", "process": "x", "cwd": "", "ui": "^Tab one - Site$"})
        self.cfg.rules.insert(0, {"project": "Project E", "title": "", "process": "", "cwd": "", "ui": "^Tab one"})
        self.eng.tick()
        self.assertEqual(self.eng.tab_color(7, "Tab one - Site"), self.cfg.color_of("Project D"))
        self.assertIsNone(self.eng.tab_color(7, "Tab two"))

    def test_switcher_entries_carry_project_colors_in_window_order(self):
        other = WinInfo(8, 98, "Untitled - Notepad", "notepad.exe")
        self.api.windows = [other, self.w]            # other is on top, like EnumWindows z-order
        self.api.alive = {7, 8}
        self.eng.tick()
        entries = self.eng.switcher_entries()
        self.assertEqual([x.hwnd for x in entries], [8, 7])
        self.assertEqual((entries[0].project, entries[0].color), ("", ""))
        self.assertEqual((entries[1].project, entries[1].color), ("Project B", "#1e88e5"))
        self.eng.paused = True
        self.assertEqual(self.eng.switcher_entries()[1].color, "")

    def test_switcher_entries_use_the_current_title_without_waiting_for_a_tick(self):
        self.eng.tick()
        self.assertEqual(self.eng.switcher_entries()[0].project, "Project B")
        self.w.title = "something unrelated"          # e.g. the browser tab just changed
        self.assertEqual(self.eng.switcher_entries()[0].project, "")
        self.w.title = "repo-b - docs"
        self.assertEqual(self.eng.switcher_entries()[0].project, "Project B")

    def test_force_none_resets_colors(self):
        self.eng.tick()
        self.eng.force_none(7)
        self.eng.tick()
        self.assertEqual(self.api.calls[-1], ("reset", 7))

    def test_pause_resets_and_resume_reapplies(self):
        self.eng.tick()
        self.eng.set_paused(True)
        self.assertEqual(self.api.calls[-1], ("reset", 7))
        self.eng.tick()
        self.assertNotIn(("set", 7), [c[:2] for c in self.api.calls[-1:]])
        self.eng.set_paused(False)
        n = len(self.api.calls)
        self.eng.tick()
        self.assertEqual(self.api.calls[n][0], "set")

    def test_closed_window_is_forgotten(self):
        self.eng.assign(7, "Project C")
        self.api.windows, self.api.alive = [], set()
        self.eng.tick()
        self.assertNotIn(7, self.eng.manual)

    def test_last_active_tracks_foreground(self):
        self.api.fg = 7
        self.eng.tick()
        self.assertEqual(self.eng.last_active.hwnd, 7)

    def test_ui_rule_tints_by_in_app_text(self):
        import engine

        class FakeProbe:
            error = ""

            def __init__(self, **kwargs):
                self.targets = []
                self.text = {7: "Netlogo Improvements / NetLogo UX improvements", 8: "ignored"}

            def add_target(self, hwnd):
                if hwnd not in self.targets:
                    self.targets.append(hwnd)

            def start(self):
                pass

            def set_targets(self, hwnds):
                self.targets = list(hwnds)

            def set_tab_targets(self, hwnds):
                pass

            def get(self, hwnd):
                return self.text.get(hwnd, "")

            def stop(self):
                pass

        engine.uia.ContextProbe = FakeProbe
        self.cfg.data["rules"] = [{"project": "Project C", "title": "", "process": "claude", "cwd": "",
                                   "ui": r"^Netlogo Improvements /"}]
        self.api.windows = [WinInfo(7, 99, "Claude", "claude.exe"), WinInfo(8, 98, "Other", "notepad.exe")]
        self.api.alive = {7, 8}
        self.eng.tick()
        self.assertEqual(self.eng.project_of(7), ("Project C", "rule"))
        self.assertEqual(self.eng.project_of(8), (None, ""))
        self.assertEqual(self.eng.probe.targets, [7])  # only claude.exe windows are probed
        self.eng.probe.text[7] = "WYSIWYG Tiddlywiki / Milieu plugin redesign"
        self.eng.tick()
        self.assertEqual(self.eng.project_of(7), (None, ""))
        self.assertEqual(self.api.calls[-1], ("reset", 7))

    def test_ui_reads_do_not_wait_for_a_slow_tick(self):
        """Regression: the settings window froze ('Not responding') because reads waited for
        the whole tick, and the tick was waiting on the frozen window."""
        import threading
        gate = threading.Event()
        entered = threading.Event()
        original = self.api.enum_windows

        def slow_enum(procs):
            entered.set()
            gate.wait(5)
            return original(procs)

        self.api.enum_windows = slow_enum
        t = threading.Thread(target=self.eng.tick, daemon=True)
        t.start()
        self.assertTrue(entered.wait(2))
        result = {}

        def read():
            result["windows"] = self.eng.windows()
            result["project"] = self.eng.project_of(7)
            self.eng.assign(7, "Project C")

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        reader.join(1.0)
        finished = not reader.is_alive()
        gate.set()
        t.join(2)
        self.assertTrue(finished, "UI-facing calls blocked while a tick was in progress")

    def test_sidebar_rows_are_colored_by_the_same_rules(self):
        self.cfg.data["rules"] = [
            {"project": "Project C", "title": "", "process": "claude", "cwd": "", "ui": r"^Netlogo Improvements /"},
            {"project": "Project D", "title": "", "process": "claude", "cwd": "", "ui": r"^WYSIWYG"},
        ]
        self.api.windows = [WinInfo(7, 99, "Claude", "claude.exe")]
        self.api.alive = {7}
        import engine

        class FakeProbe:
            error = ""
            sidebar_enabled = False

            def __init__(self, **kwargs): pass
            def add_target(self, hwnd): pass
            def start(self): pass
            def set_targets(self, h): pass
            def set_tab_targets(self, h): pass
            def get(self, h): return ""
            def stop(self): pass

        engine.uia.ContextProbe = FakeProbe
        self.eng.tick()
        c = self.cfg.color_of
        self.assertEqual(self.eng.sidebar_color(7, "Netlogo Improvements / Any chat"), c("Project C"))
        self.assertEqual(self.eng.sidebar_color(7, "WYSIWYG Tiddlywiki / Milieu plugin redesign"), c("Project D"))
        self.assertIsNone(self.eng.sidebar_color(7, "Window color context app"))   # chat outside any project
        self.assertIsNone(self.eng.sidebar_color(999, "Netlogo Improvements / x"))  # unknown window

    def test_window_event_colors_a_new_window_without_waiting_for_a_poll(self):
        import time
        self.cfg.data["poll_ms"] = 5000          # the polling pass would take 5 seconds
        self.eng.start()
        try:
            time.sleep(0.3)                      # initial pass has run
            self.api.calls.clear()
            self.api.windows.append(WinInfo(9, 55, "repo-b - new window", "x.exe"))
            self.api.alive.add(9)
            self.eng.mark_dirty(9)
            deadline = time.time() + 1.0
            while time.time() < deadline and not any(c[:2] == ("set", 9) for c in self.api.calls):
                time.sleep(0.01)
            self.assertTrue(any(c[:2] == ("set", 9) for c in self.api.calls), "no immediate color on a window event")
            self.assertEqual(self.eng.project_of(9), ("Project B", "rule"))
        finally:
            self.eng.stop()

    def test_refresh_window_ignores_windows_that_do_not_qualify(self):
        self.eng.refresh_window(12345)
        self.assertEqual(self.api.calls, [])

    def test_rename_updates_manual(self):
        self.eng.assign(7, "Project C")
        self.eng.rename_project("Project C", "Gamma")
        self.assertEqual(self.eng.manual[7], "Gamma")


if __name__ == "__main__":
    unittest.main()
