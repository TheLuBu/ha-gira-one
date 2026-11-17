"""Regressions for real HTTPS callback origins and failed setup retries."""

import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.gira_one import _async_register_callbacks, async_setup_entry
from custom_components.gira_one.api import GiraApiClient
from custom_components.gira_one.const import DOMAIN


class CallbackSetupTests(unittest.IsolatedAsyncioTestCase):
    """Use HA's actual URL selection, with only the outbound registration mocked."""

    async def asyncSetUp(self) -> None:
        """Prepare an independent HA config with native HTTPS on a LAN IP."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.hass = HomeAssistant(directory.name)
        self.addAsyncCleanup(self.hass.async_stop)
        self.hass.config.internal_url = "https://192.0.2.20:18124"
        self.hass.config.api = SimpleNamespace(use_ssl=True, port=8123)
        self.entry = Mock(
            entry_id="test-entry",
            data={
                "host": "192.0.2.10",
                "username": "test-user",
                "password": "test-password",
                "client_id": "urn:homeassistant:test",
                "access_token": "synthetic-test-token",
            },
        )
        self.api = AsyncMock(spec=GiraApiClient)
        self.api.get_ui_config.return_value = {"functions": [], "locations": []}
        self.api.register_callbacks.return_value = True

    async def test_https_lan_ip_is_a_valid_callback_origin(self) -> None:
        """An explicit HTTPS LAN IP must reach Gira callback registration."""
        with patch.object(self.hass, "http", Mock(), create=True):
            self.assertTrue(
                await _async_register_callbacks(self.hass, self.entry, self.api)
            )
        self.api.register_callbacks.assert_awaited_once_with(
            "https://192.0.2.20:18124/api/gira_one/service_callback",
            "https://192.0.2.20:18124/api/gira_one/value_callback",
        )

    async def test_loopback_callback_origin_is_rejected(self) -> None:
        """Enabling HTTPS IPs must not allow a callback to Gira's own loopback."""
        self.hass.config.internal_url = "https://127.0.0.1:18124"
        with patch.object(self.hass, "http", Mock(), create=True):
            self.assertFalse(
                await _async_register_callbacks(self.hass, self.entry, self.api)
            )
        self.api.register_callbacks.assert_not_awaited()

    async def test_unsuccessful_registration_is_not_reported_as_success(self) -> None:
        """A client returning False must stop integration setup."""
        self.hass.config.internal_url = "https://ha-test.example:18124"
        self.api.register_callbacks.return_value = False
        with patch.object(self.hass, "http", Mock(), create=True):
            self.assertFalse(
                await _async_register_callbacks(self.hass, self.entry, self.api)
            )

    async def test_failed_callback_registration_does_not_set_up_platforms(self) -> None:
        """Automatic retries must not encounter platforms left loaded by failure."""
        entries = Mock(async_forward_entry_setups=AsyncMock())
        with (
            patch("custom_components.gira_one.GiraApiClient", return_value=self.api),
            patch(
                "custom_components.gira_one._async_register_device",
                new=AsyncMock(return_value="hub"),
            ),
            patch("custom_components.gira_one._async_cleanup_stale_devices"),
            patch(
                "custom_components.gira_one._async_register_callbacks",
                new=AsyncMock(return_value=False),
            ),
            patch.object(self.hass, "config_entries", entries, create=True),
        ):
            for _ in range(2):
                with self.assertRaises(ConfigEntryNotReady):
                    await async_setup_entry(self.hass, self.entry)
        entries.async_forward_entry_setups.assert_not_awaited()
        self.assertNotIn("test-entry", self.hass.data.get(DOMAIN, {}))
