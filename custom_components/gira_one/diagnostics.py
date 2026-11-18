"""Local, allow-listed config-entry diagnostics."""

from collections import Counter

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import (
    DATA_API_CLIENT,
    DATA_HUB_DEVICE_ID,
    DATA_UI_CONFIG,
    DOMAIN,
    GIRA_FUNCTION_TYPE_TO_HA_PLATFORM,
)

# Include documented function types even when HA has no entity platform for them.
_PUBLIC_TYPES = set(GIRA_FUNCTION_TYPE_TO_HA_PLATFORM) | {
    f"de.gira.schema.functions.{name}"
    for name in (
        "Camera",
        "Camera2",
        "DecimalValue",
        "GiraOne.WeatherStationVisu",
        "Hue.Light",
        "KNX.HeatingCoolingRelative",
        "KNX.WeatherInformation",
        "Link",
        "LinkOverlay",
        "NumericSignedStatus",
        "NumericUnsignedStatus",
        "Onvif.Camera",
        "PercentValue",
        "PressAndHold",
        "RA.RemoteAccess",
        "Signed8BitValue",
        "SignedValue",
        "TemperatureValue",
        "TextStatus",
        "ThresholdSetting",
        "Unsigned8BitValue",
        "UnsignedValue",
        "iot.Trigger",
        "multi.Status",
    )
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict:
    """Summarize local runtime and registry data without exporting the project."""
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    client = data.get(DATA_API_CLIENT)
    hub_id = data.get(DATA_HUB_DEVICE_ID)
    hub = dr.async_get(hass).async_get(hub_id) if hub_id else None
    functions = data.get(DATA_UI_CONFIG, {}).get("functions")
    counts = Counter()
    for function in functions or ():
        kind = function.get("functionType")
        counts[kind if kind in _PUBLIC_TYPES else "other"] += 1
    return {
        "api_version": client.api_version if client else None,
        "last_successful_request": client.last_success.isoformat()
        if client and client.last_success
        else None,
        "server_model": hub.model if hub else None,
        "firmware_version": hub.sw_version if hub else None,
        "function_counts": dict(counts) if functions is not None else None,
    }
