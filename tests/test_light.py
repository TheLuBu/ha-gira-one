"""Light brightness regressions from the actual Gira callback values."""

import unittest
from unittest.mock import Mock

from custom_components.gira_one.light import GiraLight


class BrightnessConversionTests(unittest.TestCase):
    """Preserve Gira's percentage values when mapping to HA's 0..255 scale."""

    def test_real_callback_percentages_round_to_nearest_brightness(self) -> None:
        """Captured six-decimal device values must not lose a brightness step."""
        light = GiraLight(
            Mock(entry_id="test-entry"),
            Mock(),
            {
                "uid": "test-light",
                "displayName": "Test light",
                "functionType": "de.gira.schema.functions.KNX.Light",
                "dataPoints": [
                    {"uid": "onoff", "name": "OnOff"},
                    {"uid": "brightness", "name": "Brightness"},
                ],
            },
            "test-hub",
        )
        for percentage, expected in (
            ("0", 0),
            ("25.098039", 64),
            ("25.490196", 65),
            ("50.196078", 128),
            ("75", 191),
            ("100", 255),
        ):
            with self.subTest(percentage=percentage):
                light._update_state_from_dp_value("brightness", percentage)
                self.assertEqual(light.brightness, expected)
