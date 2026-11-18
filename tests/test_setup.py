"""Regressions for real HTTPS callback origins and failed setup retries."""

import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.gira_one import _async_register_callbacks, async_setup_entry
from custom_components.gira_one.api import GiraApiClient
from custom_components.gira_one.config_flow import GiraOneConfigFlowHandler
from custom_components.gira_one.const import CONF_CALLBACK_URL, DOMAIN


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
            "https://192.0.2.20:18124/api/gira_one/service_callback/test-entry",
            "https://192.0.2.20:18124/api/gira_one/value_callback/test-entry",
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

    async def test_form_detects_url_and_requires_override_before_registration(
        self,
    ) -> None:
        """Display HA's selected origin and reject unusable input before registration."""
        flow = GiraOneConfigFlowHandler()
        flow.hass = self.hass
        creds = {key: self.entry.data[key] for key in ("host", "username", "password")}
        form = await flow.async_step_user()
        self.assertEqual(
            form["description_placeholders"]["detected_url"],
            self.hass.config.internal_url,
        )
        self.assertIn(CONF_CALLBACK_URL, form["data_schema"].schema)
        self.hass.config.internal_url = None
        with patch(
            "custom_components.gira_one.config_flow._async_validate_input",
            new_callable=AsyncMock,
        ) as validate:
            result = await flow.async_step_user(creds)
            self.assertEqual(
                result["errors"], {CONF_CALLBACK_URL: "callback_url_required"}
            )
            for bad in (
                "http://ha.local",
                "https://localhost:8123/",
                "https://0.0.0.0",
                "https://[::1]",
                "https://user:pass@ha.local",
                "https://ha.local/path",
                "https://ha.local?token=bad",
                "https://ha.local/#fragment",
                "https://ha.local:99999",
                "https://",
                "bad",
            ):
                with self.subTest(bad=bad):
                    result = await flow.async_step_user(
                        {**creds, CONF_CALLBACK_URL: bad}
                    )
                    self.assertEqual(
                        result["errors"], {CONF_CALLBACK_URL: "invalid_callback_url"}
                    )
            validate.assert_not_awaited()

    async def test_create_with_manual_or_automatic_url(self) -> None:
        """Save a normalized override, and keep automatically configured entries dynamic."""
        flow = GiraOneConfigFlowHandler()
        flow.hass = self.hass
        creds = {key: self.entry.data[key] for key in ("host", "username", "password")}
        validated = {
            "title": "Gira",
            "unique_id": "test",
            "client_id": "client",
            "access_token": "example",
        }
        with (
            patch(
                "custom_components.gira_one.config_flow._async_validate_input",
                new=AsyncMock(return_value=validated),
            ),
            patch.object(flow, "async_set_unique_id", new_callable=AsyncMock),
            patch.object(flow, "_abort_if_unique_id_configured"),
            patch.object(flow, "async_create_entry") as create,
        ):
            await flow.async_step_user(creds)
            self.assertNotIn(CONF_CALLBACK_URL, create.call_args.kwargs["data"])
            self.hass.config.internal_url = None
            await flow.async_step_user(
                {**creds, CONF_CALLBACK_URL: "https://ha.example:9443/"}
            )
            self.entry.data = create.call_args.kwargs["data"]
            self.assertEqual(
                self.entry.data[CONF_CALLBACK_URL], "https://ha.example:9443"
            )
        self.hass.config.internal_url = "https://elsewhere.example"
        with patch.object(self.hass, "http", Mock(), create=True):
            self.assertTrue(
                await _async_register_callbacks(self.hass, self.entry, self.api)
            )
        self.api.register_callbacks.assert_awaited_once_with(
            "https://ha.example:9443/api/gira_one/service_callback/test-entry",
            "https://ha.example:9443/api/gira_one/value_callback/test-entry",
        )

    async def test_reconfigure_preserves_client_and_can_restore_detection(self) -> None:
        """Repair an existing entry and clear the override when an automatic URL exists."""
        flow = GiraOneConfigFlowHandler()
        flow.hass = self.hass
        original = self.entry.data.copy()
        self.hass.config.internal_url = None
        with (
            patch.object(flow, "_get_reconfigure_entry", return_value=self.entry),
            patch.object(flow, "async_update_reload_and_abort") as reload_entry,
        ):
            form = await flow.async_step_reconfigure({})
            self.assertEqual(
                form["errors"], {CONF_CALLBACK_URL: "callback_url_required"}
            )
            reload_entry.assert_not_called()
            await flow.async_step_reconfigure(
                {CONF_CALLBACK_URL: "https://[fd00::1]:8443/"}
            )
            self.entry.data = reload_entry.call_args.kwargs["data"]
            self.assertEqual(
                self.entry.data,
                {**original, CONF_CALLBACK_URL: "https://[fd00::1]:8443"},
            )
            form = await flow.async_step_reconfigure()
            field = next(iter(form["data_schema"].schema))
            self.assertEqual(
                field.description["suggested_value"], self.entry.data[CONF_CALLBACK_URL]
            )
            self.hass.config.internal_url = "https://auto.example"
            await flow.async_step_reconfigure({CONF_CALLBACK_URL: ""})
            reload_entry.assert_called_with(self.entry, data=original)
