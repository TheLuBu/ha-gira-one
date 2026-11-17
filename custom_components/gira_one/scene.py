"""Gira scene recall through the execution datapoint advertised by the channel."""

from typing import Any

from homeassistant.components.scene import Scene
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

_RECALL_POINTS = {
    (
        "de.gira.schema.functions.FunctionScene",
        "de.gira.schema.channels.FunctionScene",
    ): "Execute",
    ("de.gira.schema.functions.Scene", "de.gira.schema.channels.SceneSet"): "Execute",
    ("de.gira.schema.functions.Scene", "de.gira.schema.channels.SceneControl"): "Scene",
}


def _scene_names(function: dict[str, Any]) -> dict[int, str | None]:
    """Index scenes by their published number, also accepting legacy GPA parameters."""
    names = {}
    for scene in function.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        number = scene.get("number")
        if type(number) is int and 0 <= number <= 64:
            names.setdefault(number, scene.get("name") or f"Scene {number}")
    if names:
        return names
    for param in function.get("parameters") or []:
        if param.get("key") == "Scene":
            value = str(param.get("value"))
            if (
                value.isascii()
                and value.isdecimal()
                and len(value) <= 2
                and 1 <= int(value) <= 64
            ):
                names[int(value)] = None
    return names


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Discover only scenes with an identified number and a writable recall point."""
    data = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for function in data[DATA_UI_CONFIG].get("functions", []):
        point = _RECALL_POINTS.get(
            (function.get("functionType"), function.get("channelType"))
        )
        if not point:
            continue
        uid = next(
            (
                dp.get("uid")
                for dp in function.get("dataPoints", [])
                if dp.get("name") == point and dp.get("canWrite")
            ),
            None,
        )
        if not uid:
            continue
        for number, name in _scene_names(function).items():
            entities.append(
                GiraScene(
                    data[DATA_API_CLIENT],
                    function,
                    uid,
                    number,
                    name,
                    data[DATA_HUB_DEVICE_ID],
                    data[DATA_LOCATION_MAP].get(function["uid"]),
                )
            )
    async_add_entities(entities)


class GiraScene(Scene):
    """Recall one configured scene without subscribing to write-only datapoints."""

    _attr_has_entity_name = True

    def __init__(
        self,
        client: GiraApiClient,
        function: dict,
        point_uid: str,
        number: int,
        name: str | None,
        hub_id: str,
        area: str | None,
    ) -> None:
        """Bind the recall to its function device and fixed scene number."""
        self._client = client
        self._point_uid = point_uid
        self._number = number
        self._attr_name = name
        self._attr_unique_id = f"{function['uid']}_scene_{number}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, function["uid"])},
            name=function.get("displayName") or function["uid"],
            model=function["functionType"],
            manufacturer="Gira",
            via_device_id=hub_id,
            suggested_area=area,
        )

    async def async_activate(self, **kwargs: Any) -> None:
        """Send exactly one recall and surface a rejected command to the caller."""
        try:
            accepted = await self._client.set_value(self._point_uid, self._number)
        except GiraApiClientError as err:
            raise HomeAssistantError(f"Gira scene recall failed: {err}") from err
        if not accepted:
            raise HomeAssistantError("Gira did not accept the scene recall")
