"""Platform for Gira One Light entities."""

import logging
from typing import Any

import homeassistant.util.color as color_util
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import GiraApiClient
from .const import (
    DATA_API_CLIENT,
    DATA_HUB_DEVICE_ID,
    DATA_LOCATION_MAP,
    DATA_UI_CONFIG,
    DP_BLUE,
    DP_BRIGHTNESS,
    DP_COLOR_TEMPERATURE,
    DP_GREEN,
    DP_ON_OFF,
    DP_RED,
    DP_WHITE,
    GIRA_FUNCTION_TYPE_TO_HA_PLATFORM,
    LIGHT,
)
from .entity import GiraOneEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gira Light entities from a config entry."""
    api_client: GiraApiClient = hass.data[config_entry.domain][config_entry.entry_id][
        DATA_API_CLIENT
    ]
    ui_config: dict[str, Any] = hass.data[config_entry.domain][config_entry.entry_id][
        DATA_UI_CONFIG
    ]
    location_map: dict[str, str] = hass.data[config_entry.domain][
        config_entry.entry_id
    ].get(DATA_LOCATION_MAP, {})
    hub_device_id: str = hass.data[config_entry.domain][config_entry.entry_id][
        DATA_HUB_DEVICE_ID
    ]

    entities = []
    for function_data in ui_config.get("functions", []):
        if (
            GIRA_FUNCTION_TYPE_TO_HA_PLATFORM.get(function_data.get("functionType"))
            == LIGHT
        ):
            suggested_area = location_map.get(function_data.get("uid"))
            entities.append(
                GiraLight(
                    config_entry,
                    api_client,
                    function_data,
                    hub_device_id,
                    suggested_area,
                )
            )
            _LOGGER.info(
                "Adding Gira Light: %s (UID: %s)",
                function_data.get("displayName"),
                function_data.get("uid"),
            )
    async_add_entities(entities)


class GiraLight(GiraOneEntity, LightEntity):
    """Representation of a Gira Light."""

    def __init__(
        self,
        config_entry: ConfigEntry,
        api_client: GiraApiClient,
        function_data: dict[str, Any],
        hub_device_id: str,
        suggested_area: str | None = None,
    ) -> None:
        """Initialize the Gira Light."""
        super().__init__(
            config_entry, api_client, function_data, hub_device_id, suggested_area
        )

        # Light-specific attributes
        self._attr_is_on = None
        self._attr_brightness = None
        self._attr_color_temp_kelvin = None
        self._color_values: dict[str, int] = {}
        self._color_points = (DP_RED, DP_GREEN, DP_BLUE)
        if self._has_dp(DP_WHITE):
            self._color_points += (DP_WHITE,)

        if all(self._has_dp(dp) for dp in self._color_points):
            mode = ColorMode.RGBW if self._has_dp(DP_WHITE) else ColorMode.RGB
        elif self._has_dp(DP_COLOR_TEMPERATURE):
            mode = ColorMode.COLOR_TEMP
        elif self._has_dp(DP_BRIGHTNESS):
            mode = ColorMode.BRIGHTNESS
        else:
            mode = ColorMode.ONOFF
        self._attr_color_mode = mode
        self._attr_supported_color_modes = {mode}

    async def _fetch_initial_state(self) -> None:
        """Fetch the initial state of the light."""
        try:
            data = await self._api.get_function_values(self._function_data)
            if data and "values" in data:
                for dp_value_info in data["values"]:
                    self._update_state_from_dp_value(
                        dp_value_info["uid"], dp_value_info["value"]
                    )
        except Exception as e:
            _LOGGER.exception(
                "Error fetching initial state for light %s: %s", self._display_name, e
            )

    def _update_state_from_dp_value(self, dp_uid_updated: str, value: Any) -> bool:
        """Update internal state. Returns True if state changed."""
        changed = False
        for dp_name, dp_info in self._data_points.items():
            if dp_info["uid"] == dp_uid_updated:
                try:
                    if dp_name == DP_ON_OFF:
                        is_on = bool(int(value))
                        if self._attr_is_on != is_on:
                            self._attr_is_on = is_on
                            changed = True
                    elif dp_name == DP_BRIGHTNESS:
                        # Gira 0-100 -> HA 0-255
                        brightness = round(float(value) * 255 / 100)
                        if self._attr_brightness != brightness:
                            self._attr_brightness = brightness
                            changed = True
                    elif dp_name == DP_COLOR_TEMPERATURE:
                        color_temp = int(float(value))
                        if self._attr_color_temp_kelvin != color_temp:
                            self._attr_color_temp_kelvin = color_temp
                            changed = True
                    elif dp_name in self._color_points:
                        percentage = float(value)
                        if not 0 <= percentage <= 100:
                            return False
                        self._color_values[dp_name] = round(percentage * 255 / 100)
                        if all(
                            point in self._color_values for point in self._color_points
                        ):
                            color = tuple(
                                self._color_values[point]
                                for point in self._color_points
                            )
                            if (
                                self.color_mode == ColorMode.RGBW
                                and self.rgbw_color != color
                            ):
                                self._attr_rgbw_color = color
                                changed = True
                            elif (
                                self.color_mode == ColorMode.RGB
                                and self.rgb_color != color
                            ):
                                self._attr_rgb_color = color
                                changed = True
                except (ValueError, TypeError):
                    return False
                return changed
        return False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        payloads = []
        has_brightness = ATTR_BRIGHTNESS in kwargs and self._has_dp(DP_BRIGHTNESS)

        # Brightness also switches on; an extra ON can recall switch-on brightness.
        if self._has_dp(DP_ON_OFF) and not has_brightness:
            payloads.append({"uid": self._get_dp_uid(DP_ON_OFF), "value": 1})

        # Brightness
        if has_brightness:
            ha_brightness = kwargs[ATTR_BRIGHTNESS]
            gira_brightness = round((ha_brightness / 255) * 100)
            payloads.append(
                {"uid": self._get_dp_uid(DP_BRIGHTNESS), "value": gira_brightness}
            )

        # Color Temperature
        if ATTR_COLOR_TEMP_KELVIN in kwargs and self._has_dp(DP_COLOR_TEMPERATURE):
            payloads.append(
                {
                    "uid": self._get_dp_uid(DP_COLOR_TEMPERATURE),
                    "value": kwargs[ATTR_COLOR_TEMP_KELVIN],
                }
            )

        # HA translates user-selected colors to the advertised native mode.
        color = kwargs.get(ATTR_RGBW_COLOR) or kwargs.get(ATTR_RGB_COLOR)
        if color is None and ATTR_HS_COLOR in kwargs:
            color = color_util.color_hs_to_RGB(*kwargs[ATTR_HS_COLOR])
        if color is not None and self.color_mode in (ColorMode.RGB, ColorMode.RGBW):
            payloads.extend(
                {"uid": self._get_dp_uid(point), "value": round(component * 100 / 255)}
                for point, component in zip(self._color_points, color, strict=False)
            )

        if payloads:
            # Use the more efficient multi-value call if available
            await self._api.set_multiple_values(payloads)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        if self._has_dp(DP_ON_OFF):
            await self._send_command(DP_ON_OFF, 0)
