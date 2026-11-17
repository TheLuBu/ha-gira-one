"""Synthetic discovery fixtures from Gira REST API v3 sections 10.1 and 10.2."""

import unittest
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.components.cover import CoverEntityFeature
from homeassistant.components.light import ColorMode

from custom_components.gira_one import climate, cover, light, scene, sensor, switch
from custom_components.gira_one.const import (
    DATA_API_CLIENT,
    DATA_HUB_DEVICE_ID,
    DATA_LOCATION_MAP,
    DATA_UI_CONFIG,
    DOMAIN,
)

# Datatypes are part of the channel definition. uiconfig exposes names and flags.
_ON_OFF = {"OnOff": "RWE"}
_STATUS = {"Status": "R-E", "Lock": "RWE"}
_BLIND = {
    "Step-Up-Down": "-W-",
    "Up-Down": "-W-",
    "Movement": "R-E",
    "Position": "RWE",
    "Slat-Position": "RWE",
}
_COLOR = dict.fromkeys(("OnOff", "Brightness", "Red", "Green", "Blue", "White"), "RWE")
_CHANNELS = {
    "Switch": ("Switch", _ON_OFF),
    "Switch2": ("Switch", _ON_OFF | _STATUS),
    "BlindWithPos": ("Covering", _BLIND),
    "Blind2": (
        "Covering",
        _BLIND | {"Upper-Endstop": "R-E", "Lower-Endstop": "R-E"} | _STATUS,
    ),
    "DimmerRGBW": ("ColoredLight", _COLOR),
    "DimmerRGBW2": ("ColoredLight", _COLOR | {"RGB": "RWE", "RGBW": "RWE"}),
    "Trigger": ("Trigger", {"Trigger": "-WE"}),
    "Trigger2": ("Trigger", {"Trigger": "-WE"} | _STATUS),
    "Camera": ("Camera2", {"Camera": "RWE"}),
}


def fixture(channel: str, *, omit: tuple[str, ...] = ()) -> dict:
    """Build a UI-config function with opaque four-character UIDs."""
    function, points = _CHANNELS[channel]
    prefix = f"{list(_CHANNELS).index(channel):02x}"
    return {
        "uid": prefix + "ff",
        "displayName": channel,
        "functionType": f"de.gira.schema.functions.{function}",
        "channelType": f"de.gira.schema.channels.{channel}",
        "dataPoints": [
            {
                "name": name,
                "uid": f"{prefix}{i:02x}",
                "canRead": "R" in flags,
                "canWrite": "W" in flags,
                "canEvent": "E" in flags,
            }
            for i, (name, flags) in enumerate(points.items())
            if name not in omit
        ],
    }


class ChannelTests(unittest.IsolatedAsyncioTestCase):
    """Drive platform discovery, commands and feedback with channel fixtures."""

    async def discover(self, functions: list[dict]) -> dict:
        """Collect entities by stable UID from all configured platforms."""
        self.api = Mock(set_value=AsyncMock(), set_multiple_values=AsyncMock())
        data = {
            DATA_API_CLIENT: self.api,
            DATA_UI_CONFIG: {"functions": functions},
            DATA_HUB_DEVICE_ID: "hub",
            DATA_LOCATION_MAP: {},
        }
        hass = Mock(data={DOMAIN: {"entry": data}})
        entry = Mock(entry_id="entry", domain=DOMAIN)
        found = {}
        for platform in (switch, cover, light, climate, sensor, scene):
            add = Mock()
            await platform.async_setup_entry(hass, entry, add)
            found.update({entity.unique_id: entity for entity in add.call_args.args[0]})
        return found

    async def test_discovery_and_unsupported_channels(self) -> None:
        """Unknown channel additions coexist with supported entities on the entry."""
        functions = [fixture(channel) for channel in _CHANNELS]
        press = fixture("Trigger2")
        for index, point in enumerate(press["dataPoints"]):
            point["uid"] = f"e0{index:02x}"
        press.update(uid="f001", functionType="de.gira.schema.functions.PressAndHold")
        legacy_camera = fixture("Camera")
        legacy_camera["dataPoints"][0]["uid"] = "e100"
        legacy_camera.update(uid="f002", functionType="de.gira.schema.functions.Camera")
        no_point = fixture("Camera", omit=("Camera",))
        no_point["uid"] = "f003"
        found = await self.discover([*functions, press, legacy_camera, no_point])
        self.assertEqual(
            set(found),
            {
                fn["uid"]
                for fn in functions
                if fn["functionType"].rsplit(".", 1)[-1]
                in {"Switch", "Covering", "ColoredLight"}
            },
        )

    async def test_switch_and_cover_commands_and_feedback(self) -> None:
        """Both channel generations preserve switch and inverted cover semantics."""
        for name in ("Switch", "Switch2"):
            fn = fixture(name)
            entity = (await self.discover([fn]))[fn["uid"]]
            uid = entity._get_dp_uid("OnOff")
            await entity.async_turn_on()
            self.api.set_value.assert_awaited_once_with(uid, 1)
            entity._update_state_from_dp_value(uid, "1")
            self.assertTrue(entity.is_on)
        for name in ("BlindWithPos", "Blind2"):
            fn = fixture(name)
            entity = (await self.discover([fn]))[fn["uid"]]
            uid = entity._get_dp_uid("Position")
            with patch.object(entity, "async_write_ha_state"):
                await entity.async_set_cover_position(position=75)
                self.api.set_value.assert_awaited_once_with(uid, 25)
                await entity.async_stop_cover()
                self.api.set_value.assert_awaited_with(
                    entity._get_dp_uid("Step-Up-Down"), 1
                )
            entity._update_state_from_dp_value(uid, "40")
            self.assertEqual(entity.current_cover_position, 60)
            fn = fixture(name, omit=("Position", "Slat-Position", "Movement"))
            entity = (await self.discover([fn]))[fn["uid"]]
            self.assertEqual(
                entity.supported_features,
                CoverEntityFeature.OPEN
                | CoverEntityFeature.CLOSE
                | CoverEntityFeature.STOP,
            )

    async def test_native_rgb_and_rgbw_writes_and_feedback(self) -> None:
        """HA's advertised native color attributes work on both RGBW channels."""
        for channel in ("DimmerRGBW", "DimmerRGBW2"):
            for white in (False, True):
                with self.subTest(channel=channel, white=white):
                    fn = fixture(
                        channel, omit=() if white else ("White", "Brightness", "RGBW")
                    )
                    entity = (await self.discover([fn]))[fn["uid"]]
                    mode = ColorMode.RGBW if white else ColorMode.RGB
                    self.assertEqual(entity.supported_color_modes, {mode})
                    self.assertEqual(entity.color_mode, mode)
                    attr = "rgbw_color" if white else "rgb_color"
                    names = (
                        ("Red", "Green", "Blue", "White")
                        if white
                        else ("Red", "Green", "Blue")
                    )
                    color = (255, 0, 128, 51) if white else (255, 0, 128)
                    await entity.async_turn_on(**{attr: color})
                    updates = [{"uid": entity._get_dp_uid("OnOff"), "value": 1}]
                    for name, component in zip(names, color, strict=True):
                        uid = entity._get_dp_uid(name)
                        percent = round(component * 100 / 255)
                        updates.append({"uid": uid, "value": percent})
                        entity._update_state_from_dp_value(uid, str(percent))
                    self.api.set_multiple_values.assert_awaited_once_with(updates)
                    self.assertEqual(getattr(entity, attr), color)
                    entity._update_state_from_dp_value(entity._get_dp_uid("Blue"), "0")
                    self.assertEqual(getattr(entity, attr)[2], 0)
