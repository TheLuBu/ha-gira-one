"""Check diagnostics privacy including nested configuration values."""

import json
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from custom_components.gira_one.const import DOMAIN
from custom_components.gira_one.diagnostics import async_get_config_entry_diagnostics


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    """Use synthetic private markers in every non-exportable structure."""

    async def test_local_allowlist(self) -> None:
        """Only model, firmware, versions, timestamps and counts are published."""
        marker = "private-marker"
        kind = "de.gira.schema.functions.Switch"
        camera = "de.gira.schema.functions.Camera2"
        last = datetime(2026, 11, 17, tzinfo=UTC)
        data = {
            "ui_config": {
                "functions": [
                    {"functionType": kind, "parameters": [marker], "uid": marker},
                    {"functionType": camera},
                    {"functionType": marker},
                ],
                "locations": [marker],
            },
            "api_client": SimpleNamespace(
                api_version="v3", last_success=last, token=marker
            ),
            "hub_device_id": marker,
        }
        hass = Mock(data={DOMAIN: {"entry": data}})
        entry = Mock(entry_id="entry", data={"host": marker, "password": marker})
        hub = Mock(model="Gira One Server", sw_version="4.0", name=marker)
        with patch("custom_components.gira_one.diagnostics.dr.async_get") as registry:
            registry.return_value.async_get.return_value = hub
            report = await async_get_config_entry_diagnostics(hass, entry)
        self.assertNotIn(marker, json.dumps(report))
        self.assertEqual(report["function_counts"], {kind: 1, camera: 1, "other": 1})
        self.assertEqual(report["firmware_version"], "4.0")
        self.assertEqual(report["last_successful_request"], last.isoformat())
        self.assertEqual(report["server_model"], "Gira One Server")
        self.assertEqual(report["api_version"], "v3")
        hass.data.clear()
        self.assertTrue(
            all(
                value is None
                for value in (
                    await async_get_config_entry_diagnostics(hass, entry)
                ).values()
            )
        )
