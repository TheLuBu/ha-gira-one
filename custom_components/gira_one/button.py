"""GPA-configured momentary trigger actions."""

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import GiraApiClient, GiraApiClientError
from .const import (
    DATA_API_CLIENT,
    DATA_HUB_DEVICE_ID,
    DATA_LOCATION_MAP,
    DATA_UI_CONFIG,
    DOMAIN,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Expose unambiguous On/Off commands on writable trigger channels."""
    data = hass.data[DOMAIN][entry.entry_id]
    buttons = []
    for fn in data[DATA_UI_CONFIG].get("functions", []):
        if fn.get("functionType") != "de.gira.schema.functions.Trigger" or fn.get(
            "channelType"
        ) not in (
            "de.gira.schema.channels.Trigger",
            "de.gira.schema.channels.Trigger2",
        ):
            continue
        commands = [
            p.get("value")
            for p in fn.get("parameters", [])
            if p.get("key") == "Command"
        ]
        point = next(
            (
                dp["uid"]
                for dp in fn.get("dataPoints", [])
                if dp.get("name") == "Trigger" and dp.get("canWrite") and dp.get("uid")
            ),
            None,
        )
        if point and len(commands) == 1 and commands[0] in ("On", "Off"):
            buttons.append(
                GiraButton(
                    data[DATA_API_CLIENT],
                    fn,
                    point,
                    int(commands[0] == "On"),
                    data[DATA_HUB_DEVICE_ID],
                    data[DATA_LOCATION_MAP].get(fn["uid"]),
                )
            )
    async_add_entities(buttons)


class GiraButton(ButtonEntity):
    """Send a single configured trigger value on press."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(
        self,
        client: GiraApiClient,
        fn: dict,
        point: str,
        command: int,
        hub: str,
        area: str | None,
    ) -> None:
        """Bind the button to its function and command."""
        self._client = client
        self._point = point
        self._command = command
        self._attr_unique_id = fn["uid"]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, fn["uid"])},
            name=fn.get("displayName") or fn["uid"],
            manufacturer="Gira",
            model=fn["functionType"],
            via_device_id=hub,
            suggested_area=area,
        )

    async def async_press(self) -> None:
        """Trigger the configured action without replaying failed requests."""
        try:
            accepted = await self._client.set_value(self._point, self._command)
        except GiraApiClientError as err:
            raise HomeAssistantError("Gira trigger failed") from err
        if not accepted:
            raise HomeAssistantError("Gira rejected the trigger")
