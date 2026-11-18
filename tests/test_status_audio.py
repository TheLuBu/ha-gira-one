"""Test audio controls and read-only GPA status functions."""

import unittest
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.components.media_player import MediaPlayerEntityFeature as Feature
from homeassistant.components.media_player import MediaPlayerState
from homeassistant.exceptions import HomeAssistantError

from custom_components.gira_one import (
    _async_refresh_states,
    binary_sensor,
    media_player,
    sensor,
)
from custom_components.gira_one.const import (
    DATA_API_CLIENT,
    DATA_HUB_DEVICE_ID,
    DATA_UI_CONFIG,
    DOMAIN,
)


def function(kind: str, channel: str, points: dict[str, str]) -> dict:
    """Build a project description with independently varied access flags."""
    return {
        "uid": "aaaa",
        "functionType": f"de.gira.schema.functions.{kind}",
        "channelType": f"de.gira.schema.channels.{channel}",
        "dataPoints": [
            {
                "uid": name + "-uid",
                "name": name,
                "canRead": "r" in flags,
                "canWrite": "w" in flags,
                "canEvent": "e" in flags,
            }
            for name, flags in points.items()
        ],
    }


class StatusAudioTests(unittest.IsolatedAsyncioTestCase):
    """Use the HA platforms and the normal dispatcher entry point."""

    async def entities(self, platform: object, fn: dict) -> list:
        """Discover from cached config without touching hardware."""
        self.client = Mock(
            set_value=AsyncMock(return_value=True), get_function_values=AsyncMock()
        )
        self.hass = Mock(
            data={
                DOMAIN: {
                    "entry": {
                        DATA_API_CLIENT: self.client,
                        DATA_UI_CONFIG: {"functions": [fn]},
                        DATA_HUB_DEVICE_ID: "hub",
                    }
                }
            }
        )
        add = Mock()
        await platform.async_setup_entry(self.hass, Mock(entry_id="entry"), add)
        return add.call_args.args[0]

    async def test_status_discovery_callbacks_and_reconciliation(self) -> None:
        """Both statuses recover from stale values and remain unknown on invalid input."""
        for platform, kind, point, initial, updates, expected in (
            (
                sensor,
                "NumericFloatStatus",
                "Float",
                "-1.25",
                ["0", "4.5", "NaN"],
                [-1.25, 0.0, 4.5, None],
            ),
            (
                binary_sensor,
                "BinaryStatus",
                "Binary",
                "1",
                ["0", "1", "invalid"],
                [True, False, True, None],
            ),
        ):
            fn = function(kind, point, {point: "re"})
            fn["parameters"] = [{"key": "Unit", "value": "kWh"}]
            [entity] = await self.entities(platform, fn)
            self.assertEqual(entity.unique_id, fn["uid"])
            self.assertEqual(entity.device_info["via_device_id"], "hub")
            uid = point + "-uid"
            state_attr = "native_value" if platform is sensor else "is_on"
            if platform is sensor:
                self.assertEqual(entity.native_unit_of_measurement, "kWh")
            self.assertIsNone(getattr(entity, state_attr))
            self.client.get_function_values.return_value = {
                "values": [{"uid": uid, "value": initial}]
            }
            await entity._fetch_initial_state()
            self.assertEqual(getattr(entity, state_attr), expected[0])
            with (
                patch.object(entity, "async_write_ha_state"),
                patch(
                    "custom_components.gira_one.async_dispatcher_send",
                    side_effect=lambda _h, _signal, key, value, receive=entity._handle_value_update: (
                        receive(key, value)
                    ),
                ),
            ):
                entity._handle_value_update(uid, updates[0])
                self.assertEqual(getattr(entity, state_attr), expected[1])
                self.client.get_function_values.return_value = {
                    "values": [{"uid": uid, "value": updates[1]}]
                }
                await _async_refresh_states(self.hass, "entry", self.client)
                self.assertEqual(getattr(entity, state_attr), expected[2])
                entity._handle_value_update(uid, updates[2])
                self.assertIsNone(getattr(entity, state_attr))
                self.client.set_value.assert_not_awaited()
            self.assertFalse(
                await self.entities(platform, function(kind, point, {point: "w"}))
            )
            self.assertFalse(await self.entities(platform, function(kind, point, {})))

    async def test_audio_discovery_reads_and_commands(self) -> None:
        """Exercise all supported channel contracts, transport, metadata and optional writes."""
        for kind, channel in (
            ("Audio", "AudioWithCover"),
            ("Audio", "AudioWithPlaylist"),
            ("Sonos.Audio", "Sonos.Audio"),
        ):
            points = {
                "Play": "rwe",
                "Volume": "rwe",
                "Next": "w",
                "Previous": "w",
                "Mute": "rwe",
                "Title": "re",
                "Artist": "re",
                "Album": "re",
                "PlaylistName": "re",
            }
            fn = function(kind, channel, points)
            [player] = await self.entities(media_player, fn)
            [reloaded] = await self.entities(media_player, fn)
            self.assertEqual(player.unique_id, reloaded.unique_id)
            self.assertEqual(player.device_info, reloaded.device_info)
            client = player._api
            client.get_function_values.return_value = {
                "values": [
                    {"uid": p + "-uid", "value": v}
                    for p, v in {
                        "Play": "1",
                        "Volume": 50,
                        "Mute": "0",
                        "Title": "Title",
                        "Artist": "Artist",
                        "Album": "Album",
                        "PlaylistName": "List",
                    }.items()
                ]
            }
            await player._fetch_initial_state()
            self.assertEqual(
                (player.state, player.volume_level, player.is_volume_muted),
                (MediaPlayerState.PLAYING, 0.5, False),
            )
            self.assertEqual(
                (
                    player.media_title,
                    player.media_artist,
                    player.media_album_name,
                    player.media_playlist,
                ),
                ("Title", "Artist", "Album", "List"),
            )
            with patch.object(player, "async_write_ha_state"):
                player._handle_value_update("Volume-uid", "75")
                self.assertEqual(player.volume_level, 0.75)
            for fn_cmd, args, dp, value in (
                (player.async_set_volume_level, [0.5], "Volume", 50),
                (player.async_media_pause, [], "Play", 0),
                (player.async_media_play, [], "Play", 1),
                (player.async_mute_volume, [True], "Mute", 1),
                (player.async_media_previous_track, [], "Previous", 1),
                (player.async_media_next_track, [], "Next", 1),
            ):
                client.set_value.reset_mock()
                await fn_cmd(*args)
                client.set_value.assert_awaited_once_with(dp + "-uid", value)
            client.set_value.reset_mock()
            client.set_value.return_value = False
            with self.assertRaises(HomeAssistantError):
                await player.async_media_next_track()
            client.set_value.assert_awaited_once_with("Next-uid", 1)
        [minimal] = await self.entities(
            media_player,
            function(
                "Audio", "AudioWithPlaylist", {"Play": "r", "Volume": "rw", "Mute": "r"}
            ),
        )
        self.assertEqual(minimal.supported_features, Feature.VOLUME_SET)
        self.assertFalse(
            await self.entities(
                media_player, function("Audio", "Other", {"Play": "rw", "Volume": "rw"})
            )
        )
