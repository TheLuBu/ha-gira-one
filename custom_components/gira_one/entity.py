"""Base entity for the Gira One integration."""

import logging
from abc import ABC, abstractmethod
from typing import Any, Self

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from . import SIGNAL_DATA_UPDATE
from .api import GiraApiClient, GiraApiClientError
from .const import (
    DATA_API_CLIENT,
    DATA_HUB_DEVICE_ID,
    DATA_LOCATION_MAP,
    DATA_UI_CONFIG,
    DOMAIN,
    GIRA_FUNCTION_TYPE_TO_HA_PLATFORM,
)

_LOGGER = logging.getLogger(__name__)


class GiraOneEntity(Entity, ABC):
    """Base class for Gira One entities."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        config_entry: ConfigEntry,
        api_client: GiraApiClient,
        function_data: dict[str, Any],
        hub_device_id: str,
        suggested_area: str | None = None,
    ) -> None:
        """Initialize the base Gira One entity."""
        self._config_entry_id = config_entry.entry_id
        self._api = api_client
        self._function_data = function_data
        self._attr_suggested_area = suggested_area

        self._attr_unique_id = function_data["uid"]
        # Do NOT set _attr_name here. With _attr_has_entity_name = True and
        # _attr_name = None, HA treats this as the primary entity of the device
        # and the entity_id becomes "{platform}.{device_name}" (no duplication).
        self._display_name = (
            function_data.get("displayName") or f"Gira Entity {function_data['uid']}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.unique_id)},
            name=self._display_name,
            manufacturer="Gira",
            model=function_data.get("functionType", "Unknown Gira Function"),
            # 'via_device_id' links this functional device with the main server
            # device (bridge). The identifier-based 'via_device' is deprecated
            # since HA 2026.8 because identifiers are only unique per config entry.
            via_device_id=hub_device_id,
        )

        self._data_points: dict[str, dict[str, Any]] = {
            dp["name"]: dp for dp in function_data.get("dataPoints", [])
        }

    def _get_dp_uid(self, dp_name: str) -> str | None:
        """Get the UID for a data point by its name."""
        return self._data_points.get(dp_name, {}).get("uid")

    def _has_dp(self, dp_name: str) -> bool:
        """Check if a data point exists."""
        return dp_name in self._data_points

    def _can_write_dp(self, dp_name: str) -> bool:
        """Check if a data point is writable."""
        return self._data_points.get(dp_name, {}).get("canWrite", False)

    async def _send_command(self, dp_name: str, value: Any) -> None:
        """Helper to send a command to a data point."""
        dp_uid = self._get_dp_uid(dp_name)
        if not dp_uid:
            raise HomeAssistantError(f"Gira datapoint {dp_name} is missing")

        if self._data_points[dp_name].get("canWrite") is False:
            raise HomeAssistantError(f"Gira datapoint {dp_name} is read-only")

        _LOGGER.debug(
            "Sending command for '%s': DP '%s' (UID %s) -> value '%s'",
            self._display_name,
            dp_name,
            dp_uid,
            value,
        )
        try:
            accepted = await self._api.set_value(dp_uid, value)
        except GiraApiClientError as err:
            raise HomeAssistantError(f"Gira command {dp_name} failed: {err}") from err
        if not accepted:
            raise HomeAssistantError(f"Gira rejected command {dp_name}")

    async def async_added_to_hass(self) -> None:
        """Register for callbacks and fetch initial state."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_DATA_UPDATE}_{self._config_entry_id}",
                self._handle_value_update,
            )
        )
        await self._fetch_initial_state()

    @callback
    def _handle_value_update(self, dp_uid: str, value: Any) -> None:
        """Handle a value update from the dispatcher."""
        if self._update_state_from_dp_value(dp_uid, value):
            self.async_write_ha_state()

    @abstractmethod
    async def _fetch_initial_state(self) -> None:
        """Fetch the initial state of the entity's data points."""
        raise NotImplementedError

    @abstractmethod
    def _update_state_from_dp_value(self, dp_uid_updated: str, value: Any) -> bool:
        """
        Update internal state from a single data point update.
        Must be implemented by subclasses.
        Returns True if the state changed.
        """
        raise NotImplementedError


class GiraStateEntity(GiraOneEntity):
    """Shared discovery and initial reads for function-level state entities."""

    @classmethod
    def discover(
        cls, hass: HomeAssistant, entry: ConfigEntry, platform: str
    ) -> list[Self]:
        """Build entities only for supported functions and usable datapoints."""
        data = hass.data[DOMAIN][entry.entry_id]
        entities = []
        for function in data[DATA_UI_CONFIG].get("functions", []):
            if (
                GIRA_FUNCTION_TYPE_TO_HA_PLATFORM.get(function.get("functionType"))
                != platform
            ):
                continue
            entity = cls(
                entry,
                data[DATA_API_CLIENT],
                function,
                data[DATA_HUB_DEVICE_ID],
                data.get(DATA_LOCATION_MAP, {}).get(function["uid"]),
            )
            if entity.supported:
                entities.append(entity)
        return entities

    @property
    @abstractmethod
    def supported(self) -> bool:
        """Whether this function exposes the required datapoints."""

    async def _fetch_initial_state(self) -> None:
        """Initialize the same attributes updated by push and reconciliation."""
        try:
            data = await self._api.get_function_values(self._function_data)
        except GiraApiClientError as err:
            _LOGGER.debug("Initial Gira state unavailable: %s", err)
            return
        for point in data.get("values", []):
            self._update_state_from_dp_value(point["uid"], point.get("value"))
