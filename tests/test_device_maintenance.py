"""Check the maintenance dialog against a loopback-only device website."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import ThreadedResolver, web
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.gira_one.api import GiraApiConnectionError
from custom_components.gira_one.config_flow import GiraOneConfigFlowHandler
from custom_components.gira_one.device_website import GiraDeviceSession
from dev.tls import create_tls_context

FLOW_LOGGER = "custom_components.gira_one.config_flow"


class DeviceMaintenanceTests(unittest.IsolatedAsyncioTestCase):
    """Run real HA forms and cookie handling against a disposable HTTPS server."""

    async def asyncSetUp(self) -> None:
        """Expose only the login, information, logout and simulated restart calls."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.commands = []
        self.version = "GDS_1"
        self.reject_restart = False
        self.device_id = "simulated"
        app = web.Application()
        app.router.add_post("/webservice", self.website)
        runner = web.AppRunner(app)
        await runner.setup()
        self.addAsyncCleanup(runner.cleanup)
        site = web.TCPSite(
            runner, "127.0.0.1", 0, ssl_context=create_tls_context(Path(directory.name))
        )
        await site.start()
        host, port = runner.addresses[0]
        self.hass = HomeAssistant(directory.name)
        resolver = ThreadedResolver()
        self.addAsyncCleanup(resolver.close)
        self.addAsyncCleanup(self.hass.async_stop, force=True)
        self.enterContext(
            patch(
                "homeassistant.helpers.aiohttp_client._async_get_or_create_resolver",
                return_value=resolver,
            )
        )
        self.entry = Mock(data={"host": f"{host}:{port}"}, options={"existing": 1})
        self.hass.config_entries = Mock()
        self.hass.config_entries.async_get_known_entry.return_value = self.entry
        self.flow = GiraOneConfigFlowHandler.async_get_options_flow(self.entry)
        self.flow.hass = self.hass
        self.flow.handler = "test-entry"
        self.flow.flow_id = "test-flow"
        self.addCleanup(self.flow.async_remove)

    async def website(self, request: web.Request) -> web.Response:
        """Record commands. A simulated restart only adds a name to the list."""
        body = await request.json()
        command = body["command"]
        self.commands.append(command)
        if command == "getPasswordSalt" and body["data"] == {"username": "device"}:
            response = web.json_response(
                {
                    "data": {
                        "salt": "fixture-salt",
                        "sessionSalt": "nonce",
                        "version": self.version,
                    }
                }
            )
            response.set_cookie("test_session", "challenge", secure=True)
            return response
        if command == "doAuthenticateSession":
            expected = {
                "GDS_1": "30270F35173F991BCD8BB16A22A72D8DF71C233C8891E56FE2324ABD33E0AB97",
                "1": "4176E2C853147E1625DAEE4375B711254FA6EE00988929CFE64FF02D50C07596",
            }
            if request.cookies.get("test_session") == "challenge" and body["data"] == {
                "username": "device",
                "token": expected[self.version],
            }:
                response = web.json_response({"data": {}})
                response.set_cookie("test_session", "verified", secure=True)
                return response
            return web.json_response({"id": "220", "error": "denied"})
        if command == "doCloseSession":
            return web.json_response({"data": {}})
        if request.cookies.get("test_session") == "verified":
            if command == "getDeviceInfo":
                return web.json_response({"data": {"DeviceId": self.device_id}})
            if command == "reboot" and not self.reject_restart:
                self.assertEqual(body, {"command": "reboot", "keepAlive": True})
                return web.json_response({"data": {}})
        return web.json_response({"id": "235", "error": "denied"})

    async def login(self) -> None:
        """Submit a synthetic device key and reach the second form."""
        result = await self.flow.async_step_init({"password": "sample-key"})
        self.assertEqual(result.get("step_id"), "confirm_restart", result)
        self.assertEqual(result["data_schema"]({}), {"restart": False})
        self.assertNotIn("sample-key", repr(result))
        self.assertNotIn("reboot", self.commands)

    async def test_check_only(self) -> None:
        """Finishing with the default selection only checks the login."""
        form = await self.flow.async_step_init()
        self.assertEqual(form["step_id"], "init")
        self.assertFalse(self.commands)
        with self.assertLogs(FLOW_LOGGER, level="DEBUG") as logs:
            await self.login()
            result = await self.flow.async_step_confirm_restart({"restart": False})
        self.assertEqual(
            [record.getMessage() for record in logs.records],
            [
                "Gira device login verified",
                "Gira device login check finished without requesting a restart",
            ],
        )
        self.assertEqual(result["type"], FlowResultType.ABORT)
        self.assertEqual(result["reason"], "login_verified")
        self.assertEqual(
            self.commands,
            [
                "getPasswordSalt",
                "doAuthenticateSession",
                "getDeviceInfo",
                "doCloseSession",
            ],
        )

    async def test_wrong_key(self) -> None:
        """Reject a wrong key without redisplaying it or offering a reboot."""
        with self.assertLogs(FLOW_LOGGER, level="DEBUG") as logs:
            result = await self.flow.async_step_init({"password": "wrong-key"})
        self.assertEqual(
            logs.output,
            [f"WARNING:{FLOW_LOGGER}:Gira device login could not be verified"],
        )
        self.assertEqual(result["errors"], {"base": "invalid_device_key"})
        self.assertNotIn("wrong-key", repr(result))
        self.assertNotIn("reboot", self.commands)

    async def test_no_device_info(self) -> None:
        """An apparently successful login still needs an authenticated read."""
        self.device_id = ""
        result = await self.flow.async_step_init({"password": "sample-key"})
        self.assertEqual(result["step_id"], "init")
        self.assertIn("base", result["errors"])
        self.assertNotIn("reboot", self.commands)

    async def test_confirm_once(self) -> None:
        """Accept one explicit confirmation, including on legacy login firmware."""
        self.version = "1"
        with self.assertLogs(FLOW_LOGGER, level="DEBUG") as logs:
            await self.login()
            result = await self.flow.async_step_confirm_restart({"restart": True})
        self.assertEqual(
            logs.output,
            [
                f"INFO:{FLOW_LOGGER}:Gira device login verified",
                f"INFO:{FLOW_LOGGER}:Gira One Server accepted the restart request",
            ],
        )
        self.assertEqual(result["type"], FlowResultType.ABORT)
        self.assertEqual(result["reason"], "restart_requested")
        again = await self.flow.async_step_confirm_restart({"restart": True})
        self.assertEqual(again["reason"], "session_expired")
        self.assertEqual(self.commands.count("reboot"), 1)

    async def test_expired_session(self) -> None:
        """Discard the session when the confirmation deadline passes."""
        await self.login()
        self.flow._expire()
        await self.hass.async_block_till_done()
        result = await self.flow.async_step_confirm_restart({"restart": True})
        self.assertEqual(result["reason"], "session_expired")
        self.assertEqual(self.commands[-1], "doCloseSession")
        self.assertNotIn("reboot", self.commands)

    async def test_dismissed_dialog(self) -> None:
        """Closing the dialog after login ends the session without a restart."""
        await self.login()
        self.flow.async_remove()
        await self.hass.async_block_till_done()
        self.assertEqual(self.commands[-1], "doCloseSession")
        self.assertNotIn("reboot", self.commands)

    async def test_restart_denied(self) -> None:
        """A successful login is independent of the server's restart authorization."""
        self.reject_restart = True
        await self.login()
        with self.assertLogs(FLOW_LOGGER, level="DEBUG") as logs:
            result = await self.flow.async_step_confirm_restart({"restart": True})
        self.assertEqual(
            logs.output,
            [f"WARNING:{FLOW_LOGGER}:Gira One Server denied the restart request"],
        )
        self.assertEqual(result["reason"], "restart_denied")
        self.assertEqual(self.commands.count("reboot"), 1)

    async def test_lost_reply_is_not_logged_as_acceptance(self) -> None:
        """An ambiguous response records neither acceptance nor private error details."""
        await self.login()
        with (
            patch.object(
                GiraDeviceSession,
                "restart",
                new=AsyncMock(side_effect=GiraApiConnectionError("private-test-value")),
            ),
            self.assertLogs(FLOW_LOGGER, level="DEBUG") as logs,
        ):
            result = await self.flow.async_step_confirm_restart({"restart": True})
        self.assertEqual(result["reason"], "restart_unconfirmed")
        self.assertEqual(
            logs.output,
            [
                f"WARNING:{FLOW_LOGGER}:Gira One Server did not confirm the restart request"
            ],
        )
        self.assertNotIn("reboot", self.commands)
