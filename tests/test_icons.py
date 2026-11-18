"""Check that only documented, supported icons change platform defaults."""

import unittest
from unittest.mock import Mock

from custom_components.gira_one.switch import GiraSwitch
from dev.mock_gira import GiraSimulator


class IconTests(unittest.TestCase):
    """Check icons on actual entities built with v2/v3 discovery payloads."""

    def test_switch_icons_follow_state_and_fallback(self) -> None:
        """A known pictogram tracks the switch, unsupported IDs defer to HA."""
        for icon_id, off, on in (
            (15, "mdi:power-plug-off", "mdi:power-plug"),
            (11, "mdi:radiator-off", "mdi:radiator"),
            (72, "mdi:music-note-off", "mdi:music-note"),
            (-1, None, None),
            (9999, None, None),
            (None, None, None),
        ):
            fn = GiraSimulator.ui_config("v2" if icon_id is None else "v3")[
                "functions"
            ][0]
            if icon_id is not None:
                fn["iconID"] = icon_id
            with self.subTest(icon_id=icon_id):
                switch = GiraSwitch(Mock(entry_id="entry"), Mock(), fn, "hub")
                self.assertIsNone(switch.icon)
                switch._update_state_from_dp_value("aaab", 0)
                self.assertEqual(switch.icon, off)
                switch._update_state_from_dp_value("aaab", 1)
                self.assertEqual(switch.icon, on)
