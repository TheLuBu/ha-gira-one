"""Exercise the original integration over actual HTTPS, without real hardware."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import ClientSession, TCPConnector, web
from homeassistant.core import HomeAssistant

from custom_components.gira_one import GiraValueCallbackView
from custom_components.gira_one.api import GiraApiAuthError, GiraApiClient
from custom_components.gira_one.const import DATA_API_CLIENT, DOMAIN
from dev.mock_gira import PASSWORD, TOKEN, USERNAME, GiraSimulator
from dev.tls import create_tls_context


class GiraBaselineTests(unittest.IsolatedAsyncioTestCase):
    """Baseline shared by both API versions; production code is not mocked."""

    async def asyncSetUp(self) -> None:
        """Start an isolated real HTTP server with a disposable certificate."""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.simulator = GiraSimulator()
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
        self.assertTrue(
            await self.client.register_callbacks(
                "https://homeassistant/service", "https://homeassistant/value"
            )
        )
        self.assertTrue(await self.client.remove_callbacks())
        await self.client.unregister_client()
        self.assertIsNone(self.client.token)
        self.assertFalse(self.simulator.registered)

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

    async def callback_url(self) -> str:
        """Expose the integration's actual callback view on a temporary port."""
        self.client.set_credentials(TOKEN, "urn:homeassistant:test")
        self.hass.data[DOMAIN] = {"test-entry": {DATA_API_CLIENT: self.client}}
        app = web.Application()
        view = GiraValueCallbackView(self.hass, "test-entry")
        app.router.add_post("/callback", view.post)
        runner = web.AppRunner(app)
        await runner.setup()
        self.addAsyncCleanup(runner.cleanup)
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        host, port = runner.addresses[0]
        return f"http://{host}:{port}/callback"


if __name__ == "__main__":
    unittest.main()
