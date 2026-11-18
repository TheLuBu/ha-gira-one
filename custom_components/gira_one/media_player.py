"""Gira Audio and Sonos.Audio players."""

from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import GiraStateEntity

_CHANNELS = {
    "de.gira.schema.functions.Audio": {
        "de.gira.schema.channels.AudioWithPlaylist",
        "de.gira.schema.channels.AudioWithCover",
    },
    "de.gira.schema.functions.Sonos.Audio": {"de.gira.schema.channels.Sonos.Audio"},
}
_FEATURES = {
    "Play": MediaPlayerEntityFeature.PLAY | MediaPlayerEntityFeature.PAUSE,
    "Volume": MediaPlayerEntityFeature.VOLUME_SET,
    "Mute": MediaPlayerEntityFeature.VOLUME_MUTE,
    "Previous": MediaPlayerEntityFeature.PREVIOUS_TRACK,
    "Next": MediaPlayerEntityFeature.NEXT_TRACK,
}
_ATTRIBUTES = {
    "Play": "state",
    "Volume": "volume_level",
    "Mute": "is_volume_muted",
    "Title": "media_title",
    "Artist": "media_artist",
    "Album": "media_album_name",
    "PlaylistName": "media_playlist",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add players with a supported channel and its required points."""
    async_add_entities(GiraAudio.discover(hass, entry, "media_player"))


class GiraAudio(GiraStateEntity, MediaPlayerEntity):
    """Represent the controls and metadata exposed by one Gira audio function."""

    _attr_name = None

    @property
    def supported(self) -> bool:
        """Recognize only the matching channel and function pair."""
        return bool(
            self._function_data.get("channelType")
            in _CHANNELS.get(self._function_data["functionType"], ())
            and self._get_dp_uid("Play")
            and self._get_dp_uid("Volume")
        )

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Advertise controls granted write access by the project."""
        result = MediaPlayerEntityFeature(0)
        for name, feature in _FEATURES.items():
            if self._can_write_dp(name):
                result |= feature
        return result

    def _update_state_from_dp_value(self, dp_uid_updated: str, value: Any) -> bool:
        for name, attr in _ATTRIBUTES.items():
            point = self._data_points.get(name, {})
            if point.get("uid") != dp_uid_updated or not (
                point.get("canRead") or point.get("canEvent")
            ):
                continue
            if name == "Play":
                value = {
                    "0": MediaPlayerState.PAUSED,
                    "1": MediaPlayerState.PLAYING,
                }.get(str(value))
            elif name == "Mute":
                value = {"0": False, "1": True}.get(str(value))
            elif name == "Volume":
                try:
                    percent = float(value)
                    value = percent / 100 if 0 <= percent <= 100 else None
                except (TypeError, ValueError):
                    value = None
            elif not isinstance(value, str):
                value = None
            if getattr(self, attr) == value:
                return False
            setattr(self, "_attr_" + attr, value)
            return True
        return False

    async def async_media_play(self) -> None:
        """Resume playback."""
        await self._send_command("Play", 1)

    async def async_media_pause(self) -> None:
        """Pause playback."""
        await self._send_command("Play", 0)

    async def async_set_volume_level(self, volume: float) -> None:
        """Write the Gira volume percentage."""
        await self._send_command("Volume", round(volume * 100))

    async def async_mute_volume(self, mute: bool) -> None:
        """Set the mute datapoint."""
        await self._send_command("Mute", int(mute))

    async def async_media_next_track(self) -> None:
        """Advance by one track."""
        await self._send_command("Next", 1)

    async def async_media_previous_track(self) -> None:
        """Return to the previous track."""
        await self._send_command("Previous", 1)
