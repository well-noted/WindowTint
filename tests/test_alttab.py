import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import alttab_hook as a  # noqa: E402
import config  # noqa: E402
import switcher_logic as L  # noqa: E402

TAB, ESC, LALT, LEFT, DOWN = 0x09, 0x1B, 0xA4, 0x25, 0x28


def key(m, vk, down=True, alt=True, ctrl=False, win=False, shift=False, alive=True):
    return m.handle(vk, down, alt, ctrl, win, shift, alive)


class MachineTests(unittest.TestCase):
    def test_alt_tab_starts_a_session_and_swallows_the_keys(self):
        m = a.AltTabMachine()
        r = key(m, TAB)
        self.assertTrue(r.suppress and r.inject)
        self.assertEqual(r.event, ("begin", False))
        self.assertTrue(m.active)
        r = key(m, TAB, down=False)                     # key-up of that Tab is swallowed too
        self.assertTrue(r.suppress)
        self.assertIsNone(r.event)

    def test_more_tabs_step_forward_and_shift_tab_steps_back(self):
        m = a.AltTabMachine()
        key(m, TAB)
        self.assertEqual(key(m, TAB).event, ("step", 1))
        self.assertEqual(key(m, TAB, shift=True).event, ("step", -1))
        self.assertFalse(key(m, TAB).inject)             # the harmless key is only sent once per session

    def test_shift_alt_tab_starts_backwards(self):
        self.assertEqual(key(a.AltTabMachine(), TAB, shift=True).event, ("begin", True))

    def test_releasing_alt_commits_and_lets_alt_through(self):
        m = a.AltTabMachine()
        key(m, TAB)
        r = key(m, LALT, down=False, alt=False)
        self.assertEqual(r.event, ("commit",))
        self.assertFalse(r.suppress)
        self.assertFalse(m.active)

    def test_escape_cancels_and_a_later_alt_release_does_nothing(self):
        m = a.AltTabMachine()
        key(m, TAB)
        r = key(m, ESC)
        self.assertEqual(r.event, ("cancel",))
        self.assertTrue(r.suppress)
        self.assertIsNone(key(m, LALT, down=False, alt=False).event)

    def test_arrow_keys_step_during_a_session_only(self):
        m = a.AltTabMachine()
        self.assertFalse(key(m, DOWN).suppress)          # no session: arrows are untouched
        key(m, TAB)
        self.assertEqual(key(m, DOWN).event, ("arrow", "down"))
        self.assertEqual(key(m, LEFT).event, ("arrow", "left"))
        self.assertTrue(key(m, DOWN, down=False).suppress)

    def test_ctrl_alt_tab_and_win_tab_and_plain_tab_are_left_to_windows(self):
        for kwargs in (dict(ctrl=True), dict(win=True), dict(alt=False)):
            m = a.AltTabMachine()
            r = key(m, TAB, **kwargs)
            self.assertFalse(r.suppress, kwargs)
            self.assertIsNone(r.event)
            self.assertFalse(m.active)

    def test_other_keys_pass_through_during_a_session(self):
        m = a.AltTabMachine()
        key(m, TAB)
        r = key(m, 0x41)
        self.assertFalse(r.suppress)
        self.assertIsNone(r.event)

    def test_when_the_app_is_not_responding_windows_gets_alt_tab_back(self):
        m = a.AltTabMachine()
        key(m, TAB)
        r = key(m, TAB, alive=False)
        self.assertFalse(r.suppress)
        self.assertFalse(m.active)
        self.assertFalse(key(m, TAB, alive=False).suppress)

    def test_new_session_after_commit(self):
        m = a.AltTabMachine()
        key(m, TAB)
        key(m, LALT, down=False, alt=False)
        self.assertEqual(key(m, TAB).event, ("begin", False))


class ConfigTests(unittest.TestCase):
    def test_alt_tab_replacement_is_off_by_default_and_must_be_a_bool(self):
        self.assertFalse(config.normalize({})["alt_tab"])
        self.assertTrue(config.normalize({"alt_tab": True})["alt_tab"])
        self.assertFalse(config.normalize({"alt_tab": "yes"})["alt_tab"])


if __name__ == "__main__":
    unittest.main()
