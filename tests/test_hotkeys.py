import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config  # noqa: E402
import hotkeys as h  # noqa: E402


class ParseTests(unittest.TestCase):
    def test_parse_letters_digits_function_and_special_keys(self):
        self.assertEqual(h.parse_combo("ctrl+alt+w"), (h.MOD_CONTROL | h.MOD_ALT, ord("W")))
        self.assertEqual(h.parse_combo("Ctrl+Shift+F5"), (h.MOD_CONTROL | h.MOD_SHIFT, 0x74))
        self.assertEqual(h.parse_combo("alt+space"), (h.MOD_ALT, 0x20))
        self.assertEqual(h.parse_combo("ctrl+win+7"), (h.MOD_CONTROL | h.MOD_WIN, ord("7")))

    def test_invalid_combinations(self):
        for bad in ("", "w", "ctrl", "ctrl+", "ctrl+alt+escape", "banana+w", "ctrl+f25", "+w"):
            self.assertFalse(h.is_valid(bad), bad)

    def test_normalize_orders_modifiers_and_lowercases(self):
        self.assertEqual(h.normalize_combo("Alt+Ctrl+W"), "ctrl+alt+w")
        self.assertEqual(h.normalize_combo("SHIFT+ctrl+Space"), "ctrl+shift+space")

    def test_display(self):
        self.assertEqual(h.display_combo("ctrl+alt+w"), "Ctrl+Alt+W")
        self.assertEqual(h.display_combo("ctrl+shift+f5"), "Ctrl+Shift+F5")
        self.assertEqual(h.display_combo("ctrl+alt+space"), "Ctrl+Alt+Space")
        self.assertEqual(h.display_combo("ctrl+pagedown"), "Ctrl+PageDown")
        self.assertEqual(h.display_combo(""), "")


class CaptureTests(unittest.TestCase):
    def test_event_to_combo(self):
        self.assertEqual(h.combo_from_event(h.STATE_CONTROL | h.STATE_ALT, "w"), "ctrl+alt+w")
        self.assertEqual(h.combo_from_event(h.STATE_CONTROL | h.STATE_SHIFT, "F5"), "ctrl+shift+f5")
        self.assertEqual(h.combo_from_event(h.STATE_ALT, "space"), "alt+space")
        self.assertEqual(h.combo_from_event(h.STATE_CONTROL | h.STATE_SHIFT, "ISO_Left_Tab"), "ctrl+shift+tab")

    def test_modifier_only_and_bare_keys_are_not_hotkeys(self):
        self.assertIsNone(h.combo_from_event(h.STATE_CONTROL, "Control_L"))
        self.assertIsNone(h.combo_from_event(0, "w"))
        self.assertIsNone(h.combo_from_event(h.STATE_CONTROL, "Escape"))
        self.assertIsNone(h.combo_from_event(h.STATE_CONTROL, "exclam"))

    def test_captured_combo_round_trips(self):
        combo = h.combo_from_event(h.STATE_CONTROL | h.STATE_ALT, "k")
        self.assertTrue(h.is_valid(combo))
        self.assertEqual(h.normalize_combo(combo), combo)


class ConfigTests(unittest.TestCase):
    def test_defaults_do_not_use_claudes_own_hotkey(self):
        d = config.normalize({})
        self.assertEqual((d["hotkey_new_rule"], d["hotkey_switcher"]), ("ctrl+alt+r", "ctrl+alt+w"))
        self.assertNotEqual(d["hotkey_switcher"], "ctrl+alt+space")

    def test_custom_values_are_normalized_and_bad_ones_fall_back(self):
        d = config.normalize({"hotkey_switcher": "Alt+Ctrl+K", "hotkey_new_rule": "nonsense"})
        self.assertEqual(d["hotkey_switcher"], "ctrl+alt+k")
        self.assertEqual(d["hotkey_new_rule"], "ctrl+alt+r")

    def test_duplicate_combinations_reset_to_defaults(self):
        d = config.normalize({"hotkey_switcher": "ctrl+alt+r", "hotkey_new_rule": "ctrl+alt+r"})
        self.assertEqual((d["hotkey_new_rule"], d["hotkey_switcher"]), ("ctrl+alt+r", "ctrl+alt+w"))


if __name__ == "__main__":
    unittest.main()
