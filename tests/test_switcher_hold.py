"""Alt+Tab session handling in the Switcher, with tkinter, Pillow's ImageTk and winapi replaced by fakes."""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import switcher_logic as L  # noqa: E402


class FakeRoot:
    def __init__(self):
        self.jobs, self.next_id = {}, 1

    def after(self, ms, fn=None, *args):
        self.jobs[self.next_id] = (ms, fn, args)
        self.next_id += 1
        return self.next_id - 1

    def after_cancel(self, job):
        self.jobs.pop(job, None)

    def run_due(self):
        jobs, self.jobs = self.jobs, {}
        for ms, fn, args in jobs.values():
            fn(*args)

    def winfo_fpixels(self, _):
        return 96.0

    def winfo_screenwidth(self):
        return 1920

    def winfo_screenheight(self):
        return 1080


class FakeWin:
    def __init__(self):
        self.shown = False
        self.alpha = None

    def attributes(self, *a):
        if a and a[0] == "-alpha" and len(a) > 1:
            self.alpha = a[1]

    def after(self, ms, fn, *args):
        return 0

    def after_cancel(self, j):
        pass

    def update_idletasks(self): pass
    def deiconify(self): self.shown = True
    def withdraw(self): self.shown = False
    def lift(self): pass
    def focus_force(self): pass
    def geometry(self, g): pass
    def winfo_id(self): return 99
    def focus_displayof(self): return None


class FakeEngine:
    def __init__(self, entries):
        self.entries = entries

    def switcher_entries(self):
        return list(self.entries)


class HoldTests(unittest.TestCase):
    def setUp(self):
        self.saved = {k: sys.modules.get(k) for k in ("tkinter", "PIL.ImageTk", "winapi", "switcher")}
        self.calls = []
        fake_tk = types.ModuleType("tkinter")
        fake_tk.Toplevel = fake_tk.Label = object
        sys.modules["tkinter"] = fake_tk
        imagetk = types.ModuleType("PIL.ImageTk")
        imagetk.PhotoImage = lambda img: img
        sys.modules["PIL.ImageTk"] = imagetk
        import PIL
        PIL.ImageTk = imagetk
        api = types.ModuleType("winapi")
        api.fg = 10
        api.get_foreground = lambda: api.fg
        api.focus_window = lambda h: self.calls.append(("focus", h)) or True
        api.set_noactivate = lambda w, on: self.calls.append(("noactivate", on))
        api.style_popup = lambda w: None
        api.cursor_work_area = lambda: (0, 0, 1920, 1080)
        api.user32 = types.SimpleNamespace(GetParent=lambda w: w)
        self.api = api
        sys.modules["winapi"] = api
        sys.modules.pop("switcher", None)
        import switcher
        self.mod = switcher
        self.root = FakeRoot()
        es = [L.Entry(h, f"w{h}", "x.exe", "X") for h in (10, 11, 12, 13)]
        self.sw = switcher.Switcher(self.root, FakeEngine(es))
        self.sw.win = FakeWin()                 # skip real window creation
        self.sw.label = types.SimpleNamespace(configure=lambda **k: None)
        self.sw._ensure_window = lambda: None
        self.sw._redraw = lambda reposition=False: setattr(self.sw, "drawn", True)

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    def focused(self):
        return [c[1] for c in self.calls if c[0] == "focus"]

    def test_quick_tap_switches_to_previous_window_without_drawing(self):
        self.sw.begin_hold()
        self.assertFalse(self.sw.visible)
        self.sw.commit()
        self.assertEqual(self.focused(), [11])
        self.root.run_due()                      # the delayed first draw was cancelled
        self.assertFalse(self.sw.visible)

    def test_holding_long_enough_shows_the_ui_without_taking_focus(self):
        self.sw.begin_hold()
        self.root.run_due()
        self.assertTrue(self.sw.visible)
        self.assertIn(("noactivate", True), self.calls)
        self.assertEqual(self.focused(), [])
        self.sw.step(1)
        self.sw.commit()
        self.assertEqual(self.focused(), [12])
        self.assertFalse(self.sw.visible)
        self.assertFalse(self.sw.hold_active)

    def test_second_tab_press_shows_the_ui_immediately(self):
        self.sw.begin_hold()
        self.sw.step(1)
        self.assertTrue(self.sw.visible)
        self.sw.commit()
        self.assertEqual(self.focused(), [12])

    def test_shift_start_selects_the_last_window_and_steps_wrap(self):
        self.sw.begin_hold(shift=True)
        self.assertEqual(self.sw.selected, 3)
        self.sw.step(1)
        self.assertEqual(self.sw.selected, 0)
        self.sw.commit()
        self.assertEqual(self.focused(), [10])

    def test_cancel_switches_nothing(self):
        self.sw.begin_hold()
        self.root.run_due()
        self.sw.cancel_hold()
        self.assertEqual(self.focused(), [])
        self.assertFalse(self.sw.visible)
        self.sw.commit()                         # the Alt release that follows is ignored
        self.assertEqual(self.focused(), [])

    def test_arrow_keys_move_across_the_grid_and_wrap(self):
        self.sw._area = (0, 0, 1920, 1080)
        self.sw.begin_hold()
        self.sw.arrow("right")
        self.assertEqual(self.sw.selected, 2)
        self.assertTrue(self.sw.visible)
        self.sw.arrow("left")
        self.sw.arrow("left")
        self.assertEqual(self.sw.selected, 0)
        self.sw.arrow("left")
        self.assertEqual(self.sw.selected, 3)
        self.sw.commit()
        self.assertEqual(self.focused(), [13])

    def test_arrow_without_a_session_is_ignored(self):
        self.sw.mode = "hold"
        self.sw.items = list(self.sw.engine.entries)
        self.sw.arrow("right")
        self.assertEqual(self.sw.selected, 0)

    def test_fewer_than_two_windows_does_nothing(self):
        self.sw.engine = FakeEngine([L.Entry(10, "only", "x.exe", "X")])
        self.sw.begin_hold()
        self.assertFalse(self.sw.hold_active)
        self.sw.commit()
        self.assertEqual(self.focused(), [])

    def test_foreground_not_in_list_starts_on_the_first_entry(self):
        self.api.fg = 999
        self.sw.begin_hold()
        self.assertEqual(self.sw.selected, 0)


if __name__ == "__main__":
    unittest.main()
