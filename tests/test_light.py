"""Light brightness regressions from the actual Gira callback values."""

import unittest
from unittest.mock import AsyncMock, Mock

from custom_components.gira_one.light import GiraLight


class GiraLightTests(unittest.IsolatedAsyncioTestCase):
    """Cover brightness feedback and commands observed on actual hardware."""

    def setUp(self) -> None:
        """Create a dimmable light with an observable API boundary."""
        self.api = Mock(set_multiple_values=AsyncMock(return_value=True))
        self.light = GiraLight(
            Mock(entry_id="test-entry"),
            self.api,
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

    def test_real_callback_percentages_round_to_nearest_brightness(self) -> None:
        """Captured six-decimal device values must not lose a brightness step."""
        for percentage, expected in (
            ("0", 0),
            ("25.098039", 64),
            ("25.490196", 65),
            ("50.196078", 128),
            ("75", 191),
            ("100", 255),
        ):
            with self.subTest(percentage=percentage):
                self.light._update_state_from_dp_value("brightness", percentage)
                self.assertEqual(self.light.brightness, expected)

    async def test_dimming_an_on_light_does_not_retrigger_switch_on(self) -> None:
        """An extra ON command caused the hardware's transient 100% feedback."""
        self.light._update_state_from_dp_value("onoff", "1")
        await self.light.async_turn_on(brightness=64)
        self.api.set_multiple_values.assert_awaited_once_with(
            [{"uid": "brightness", "value": 25}]
        )

    async def test_dimming_an_off_or_unknown_light_still_turns_it_on(self) -> None:
        """Keep the explicit switch-on when the light is not known to be on."""
        for state in (False, None):
            with self.subTest(state=state):
                self.light._attr_is_on = state
                self.api.set_multiple_values.reset_mock()
                await self.light.async_turn_on(brightness=64)
                self.api.set_multiple_values.assert_awaited_once_with(
                    [
                        {"uid": "onoff", "value": 1},
                        {"uid": "brightness", "value": 25},
                    ]
                )

    async def test_plain_turn_on_keeps_the_switch_command(self) -> None:
        """A plain ON request must still reach both dimmers and binary lights."""
        for has_brightness in (True, False):
            with self.subTest(has_brightness=has_brightness):
                if not has_brightness:
                    self.light._data_points.pop("Brightness")
                self.light._update_state_from_dp_value("onoff", "1")
                self.api.set_multiple_values.reset_mock()
                await self.light.async_turn_on()
                self.api.set_multiple_values.assert_awaited_once_with(
                    [{"uid": "onoff", "value": 1}]
                )
