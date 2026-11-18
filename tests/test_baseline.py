"""Exercise the original integration over actual HTTPS, without real hardware."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from aiohttp import ClientSession, TCPConnector, web
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.gira_one import (
    GiraServiceCallbackView,
    GiraValueCallbackView,
    button,
)
from custom_components.gira_one.api import GiraApiAuthError, GiraApiClient
from custom_components.gira_one.const import (
    DATA_API_CLIENT,
    DATA_HUB_DEVICE_ID,
    DATA_LOCATION_MAP,
    DATA_UI_CONFIG,
    DOMAIN,
)
from dev.mock_gira import PASSWORD, TOKEN, USERNAME, GiraSimulator
from dev.tls import create_tls_context


class GiraBaselineTests(unittest.IsolatedAsyncioTestCase):
    """Baseline shared by both API versions; production code is not mocked."""

    async def asyncSetUp(self) -> None:
        """Start an isolated real HTTP server with a disposable certificate."""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.simulator = GiraSimulator("v2")
        self.runner = web.AppRunner(self.simulator.app)
        await self.runner.setup()
        self.addAsyncCleanup(self.runner.cleanup)
        site = web.TCPSite(
            self.runner,
            "127.0.0.1",
            0,
            ssl_context=create_tls_context(Path(self.directory.name)),
        )
        await site.start()
        host, port = self.runner.addresses[0]
        self.session = ClientSession(connector=TCPConnector(ssl=False))
        self.addAsyncCleanup(self.session.close)
        self.hass = HomeAssistant(self.directory.name)
        self.addAsyncCleanup(self.hass.async_stop)
        # Only session ownership is adapted; requests still use the real network.
        with patch(
            "custom_components.gira_one.api.async_get_clientsession",
            return_value=self.session,
        ):
            self.client = GiraApiClient(f"{host}:{port}", USERNAME, PASSWORD, self.hass)

    def test_simulator_defaults_to_v3(self) -> None:
        """An unconfigured simulator advertises firmware 4."""
        self.assertEqual(GiraSimulator().version, "v3")

    async def test_registration_values_and_cleanup(self) -> None:
        """Authenticate, read, write, register HTTPS callbacks and remove client."""
        info = await self.client.check_api_availability()
        self.assertEqual(info["info"], "GDS-REST-API")
        self.assertEqual(
            await self.client.register_client("urn:homeassistant:test"), TOKEN
        )
        config = await self.client.get_ui_config()
        self.assertEqual(config["functions"][0]["uid"], "aaaa")
        self.assertTrue(await self.client.set_value("aaab", 1))
        self.assertEqual(
            (await self.client.get_value("aaaa"))["values"][0]["value"], "1"
        )
        self.assertTrue(await self.register_test_callbacks())
        self.assertTrue(await self.client.remove_callbacks())
        await self.client.unregister_client()
        self.assertIsNone(self.client.token)
        self.assertFalse(self.simulator.registered)

    async def test_trigger_command_on_negotiated_api(self) -> None:
        """One press results in exactly one PUT even if the hub rejects the request."""
        await self.client.check_api_availability()
        await self.client.register_client("urn:homeassistant:test")
        entry = Mock(entry_id="entry")
        self.hass.data[DOMAIN] = {
            "entry": {
                DATA_API_CLIENT: self.client,
                DATA_UI_CONFIG: await self.client.get_ui_config(),
                DATA_HUB_DEVICE_ID: "hub",
                DATA_LOCATION_MAP: {},
            }
        }
        add = Mock()
        await button.async_setup_entry(self.hass, entry, add)
        [entity] = add.call_args.args[0]
        self.simulator.requests.clear()
        await entity.async_press()
        path = f"/api/{self.simulator.version}/values/ccca"
        self.assertEqual(self.simulator.requests, [("PUT", path, {"value": 1})])
        await button.async_setup_entry(self.hass, entry, add)
        [reloaded] = add.call_args.args[0]
        self.assertEqual(reloaded.unique_id, entity.unique_id)
        self.simulator.error_status = 429
        self.simulator.requests.clear()
        with self.assertRaises(HomeAssistantError):
            await reloaded.async_press()
        self.assertEqual(self.simulator.requests, [("PUT", path, {"value": 1})])

    async def test_invalid_credentials(self) -> None:
        """Wrong credentials raise the integration's authentication exception."""
        self.client._password = "wrong"  # noqa: S105
        await self.client.check_api_availability()
        with self.assertRaises(GiraApiAuthError):
            await self.client.register_client("urn:homeassistant:test")

    async def test_batched_value_callback(self) -> None:
        """A real HA view authenticates and dispatches every event in a batch."""
        url = await self.callback_url()
        events = [{"uid": "aaab", "value": "1"}, {"uid": "aaac", "value": "0"}]
        with patch("custom_components.gira_one.async_dispatcher_send") as dispatch:
            async with self.session.post(
                url, json={"token": TOKEN, "events": events}
            ) as response:
                self.assertEqual(response.status, 200)
            self.assertEqual(dispatch.call_count, 2)
            self.assertEqual(dispatch.call_args_list[0].args[-2:], ("aaab", "1"))

    async def register_test_callbacks(self) -> bool:
        """Register and probe both actual HA views over local HTTPS."""
        url = await self.callback_url()
        return await self.client.register_callbacks(
            url.rsplit("/", 1)[0] + "/service", url
        )

    async def callback_url(self) -> str:
        """Expose the integration's actual callback view on a temporary port."""
        self.client.set_credentials(TOKEN, "urn:homeassistant:test")
        self.hass.data[DOMAIN] = {"test-entry": {DATA_API_CLIENT: self.client}}
        app = web.Application()
        view = GiraValueCallbackView(self.hass, "test-entry")
        app.router.add_post(
            "/service", GiraServiceCallbackView(self.hass, "test-entry").post
        )
        app.router.add_post("/callback", view.post)
        runner = web.AppRunner(app)
        await runner.setup()
        self.addAsyncCleanup(runner.cleanup)
        await web.TCPSite(
            runner,
            "127.0.0.1",
            0,
            ssl_context=create_tls_context(Path(self.directory.name)),
        ).start()
        host, port = runner.addresses[0]
        return f"https://{host}:{port}/callback"


if __name__ == "__main__":
    unittest.main()
