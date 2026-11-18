"""Light brightness regressions from the actual Gira callback values."""

import unittest
from unittest.mock import AsyncMock, Mock

from custom_components.gira_one.light import GiraLight


class GiraLightTests(unittest.IsolatedAsyncioTestCase):
    """Cover brightness feedback and commands observed on actual hardware."""

    def setUp(self) -> None:
        """Create a dimmable light with an observable API boundary."""
        self.api = Mock(
            set_multiple_values=AsyncMock(return_value=True),
            set_value=AsyncMock(return_value=True),
        )
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

    async def test_brightness_requests_do_not_retrigger_switch_on(self) -> None:
        """Brightness alone avoids the extra ON command and its 100% excursion."""
        for state in (False, True, None):
            for brightness, percentage in ((64, 25), (191, 75), (255, 100)):
                with self.subTest(state=state, percentage=percentage):
                    self.light._attr_is_on = state
                    self.api.set_multiple_values.reset_mock()
                    await self.light.async_turn_on(brightness=brightness)
                    self.api.set_multiple_values.assert_awaited_once_with(
                        [{"uid": "brightness", "value": percentage}]
                    )

    async def test_brightness_only_switch_on_waits_for_hardware_feedback(self) -> None:
        """Replay the observed feedback; commands must not invent an ON state."""
        self.light._update_state_from_dp_value("onoff", "0")
        self.light._update_state_from_dp_value("brightness", "0")
        await self.light.async_turn_on(brightness=64)
        self.api.set_multiple_values.assert_awaited_once_with(
            [{"uid": "brightness", "value": 25}]
        )
        self.assertFalse(self.light.is_on)
        self.assertEqual(self.light.brightness, 0)

        # Actual callback sequence captured when starting the kitchen light at 25%.
        for uid, value in (
            ("brightness", "25"),
            ("onoff", "1"),
            ("brightness", "25.098039"),
        ):
            self.light._update_state_from_dp_value(uid, value)
        self.assertTrue(self.light.is_on)
        self.assertEqual(self.light.brightness, 64)

    async def test_plain_turn_on_keeps_the_switch_command(self) -> None:
        """A plain ON request must still reach both dimmers and binary lights."""
        for has_brightness in (True, False):
            if not has_brightness:
                self.light._data_points.pop("Brightness")
            for state in (False, True, None):
                with self.subTest(has_brightness=has_brightness, state=state):
                    self.light._attr_is_on = state
                    self.api.set_multiple_values.reset_mock()
                    await self.light.async_turn_on()
                    self.api.set_multiple_values.assert_awaited_once_with(
                        [{"uid": "onoff", "value": 1}]
                    )

    async def test_unsupported_brightness_does_not_suppress_switch_on(self) -> None:
        """An ON/OFF-only light must still switch on if brightness is passed."""
        self.light._data_points.pop("Brightness")
        await self.light.async_turn_on(brightness=64)
        self.api.set_multiple_values.assert_awaited_once_with(
            [{"uid": "onoff", "value": 1}]
        )

    async def test_turn_off_keeps_the_switch_command(self) -> None:
        """Dimmers and binary lights still turn off through their OnOff DP."""
        for has_brightness in (True, False):
            with self.subTest(has_brightness=has_brightness):
                if not has_brightness:
                    self.light._data_points.pop("Brightness")
                self.api.set_value.reset_mock()
                await self.light.async_turn_off()
                self.api.set_value.assert_awaited_once_with("onoff", 0)
        self.api.set_multiple_values.assert_not_awaited()
