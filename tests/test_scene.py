"""Discovery, identity and write-only recall contracts for Gira scenes."""

import unittest
from unittest.mock import AsyncMock, Mock

from homeassistant.exceptions import HomeAssistantError

from custom_components.gira_one.api import GiraApiConnectionError
from custom_components.gira_one.const import (
    DATA_API_CLIENT,
    DATA_HUB_DEVICE_ID,
    DATA_LOCATION_MAP,
    DATA_UI_CONFIG,
    DOMAIN,
)
from custom_components.gira_one.scene import GiraScene, async_setup_entry
from dev.mock_gira import GiraSimulator


class SceneTests(unittest.IsolatedAsyncioTestCase):
    """Drive the real platform setup with versioned UI descriptions."""

    async def discover(self, function: dict) -> list[GiraScene]:
        """Return the entities Home Assistant would receive for this config."""
        self.api = AsyncMock()
        entry = Mock(entry_id="entry")
        data = {
            DATA_API_CLIENT: self.api,
            DATA_UI_CONFIG: {"functions": [function]},
            DATA_HUB_DEVICE_ID: "hub",
            DATA_LOCATION_MAP: {function["uid"]: "Kitchen"},
        }
        add = Mock()
        await async_setup_entry(Mock(data={DOMAIN: {"entry": data}}), entry, add)
        return add.call_args.args[0]

    async def test_v3_names_identity_recall_and_duplicate_numbers(self) -> None:
        """Equal labels stay distinct, duplicate numbers collapse and Teach stays idle."""
        fn = GiraSimulator.ui_config("v3")["functions"][1]
        fn["dataPoints"].insert(
            0, {"name": "Teach", "uid": "bbbb-teach", "canWrite": True}
        )
        fn["scenes"] = [
            {"number": 2, "name": "Movie"},
            {"number": 4, "name": "Movie"},
            {"number": 2, "name": "Duplicate"},
            {"number": 128, "name": "Invalid"},
            None,
        ]
        fn["iconID"] = 76
        first, second = await self.discover(fn)
        self.assertEqual(first.icon, "mdi:heart-outline")
        self.assertEqual(first.name, second.name)
        self.assertEqual(
            [first.unique_id, second.unique_id], ["bbbb_scene_2", "bbbb_scene_4"]
        )
        self.assertEqual(first.device_info["identifiers"], {(DOMAIN, "bbbb")})
        await second.async_activate()
        self.api.set_value.assert_awaited_once_with("bbba", 4)
        fn["scenes"] = [{"number": 4, "name": "New name"}]
        [reloaded] = await self.discover(fn)
        self.assertEqual(reloaded.unique_id, second.unique_id)
        self.assertEqual(reloaded.name, "New name")

    async def test_v2_parameter_and_scene_control_recall(self) -> None:
        """Both legacy channels carry their configured number to the correct DP."""
        for channel, point in (("SceneSet", "Execute"), ("SceneControl", "Scene")):
            fn = GiraSimulator.ui_config("v2")["functions"][1]
            fn["channelType"] = f"de.gira.schema.channels.{channel}"
            fn["dataPoints"][0]["name"] = point
            fn["scenes"] = []
            [scene] = await self.discover(fn)
            await scene.async_activate()
            self.api.set_value.assert_awaited_once_with("bbba", 2)
            self.api.set_value.reset_mock()
            self.api.set_value.side_effect = GiraApiConnectionError("offline")
            with self.assertRaises(HomeAssistantError):
                await scene.async_activate()
            self.api.set_value.assert_awaited_once_with("bbba", 2)

    async def test_skip_ambiguous_or_unwritable_scene(self) -> None:
        """A missing number, wrong function or teach-only channel yields no scene."""
        fn = GiraSimulator.ui_config("v3")["functions"][1]
        for changes in (
            {"scenes": []},
            {"scenes": None},
            {"dataPoints": [{"name": "Teach", "uid": "teach", "canWrite": True}]},
            {"dataPoints": [{"name": "Execute", "uid": "execute", "canWrite": False}]},
            {"functionType": "de.gira.schema.functions.Switch"},
        ):
            self.assertEqual(await self.discover({**fn, **changes}), [])
