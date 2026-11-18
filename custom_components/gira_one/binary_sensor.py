"""Gira binary status functions."""

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import GiraStateEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up readable BinaryStatus points."""
    async_add_entities(GiraBinaryStatus.discover(hass, entry, "binary_sensor"))


class GiraBinaryStatus(GiraStateEntity, BinarySensorEntity):
    """Represent a generic binary value without guessing its physical meaning."""

    _attr_name = None

    @property
    def supported(self) -> bool:
        """Require the readable Binary point."""
        return bool(
            self._get_dp_uid("Binary") and self._data_points["Binary"].get("canRead")
        )

    def _update_state_from_dp_value(self, dp_uid_updated: str, value: Any) -> bool:
        """Map documented 0/1 values, leaving invalid data unknown."""
        if dp_uid_updated != self._get_dp_uid("Binary"):
            return False
        state = {"0": False, "1": True}.get(str(value))
        if state == self.is_on:
            return False
        self._attr_is_on = state
        return True
