"""Small HTTPS Gira simulator derived from the official v2/v3 specifications.

Only implements the integration's existing platforms, not a real Gira device.
Credentials and tokens are synthetic and restricted to the development network.
"""

import os
import tempfile
from pathlib import Path

import aiohttp
from aiohttp import web

from dev.tls import create_tls_context

USERNAME = "test-user"
PASSWORD = "test-password"  # noqa: S105
TOKEN = "GiraDevelopmentToken0000000000001"  # noqa: S105


class GiraSimulator:
    """Keep state across actual HTTP requests from Home Assistant."""

    def __init__(self, version: str = "v2") -> None:
        """Initialize the synthetic switch and record requests for assertions."""
        self.version = version
        self.registered = False
        self.callbacks: dict = {}
        self.value = "0"
        self.requests: list[tuple[str, str, dict]] = []
        self.error_status: int | None = None
        self.error_code = "generic"
        self.app = web.Application()
        self.app.router.add_route("*", "/api/{version}", self.handle)
        self.app.router.add_route("*", "/api/{version}/{tail:.*}", self.handle)

    async def handle(self, request: web.Request) -> web.Response:
        """Serve the subset needed by config flow, setup, commands and cleanup."""
        version = request.match_info["version"]
        path = request.match_info.get("tail", "").rstrip("/")
        body = await request.json() if request.can_read_body else {}
        self.requests.append((request.method, request.path, body))
        if version not in ("v2", self.version):
            return self.error(404, "resourceNotFound")
        if self.error_status is not None:
            return self.error(self.error_status, self.error_code)
        if not path:
            return web.json_response(
                {
                    "info": "GDS-REST-API",
                    "version": version[1:],
                    "deviceName": "Development Gira",
                    "deviceType": "Gira One Server",
                    "deviceVersion": "4.0.0.0" if self.version == "v3" else "3.0.0.0",
                    **({"uidLength": 4} if version == "v3" else {}),
                }
            )
        if path == "clients" and request.method == "POST":
            expected = aiohttp.BasicAuth(USERNAME, PASSWORD).encode()
            if request.headers.get("Authorization") != expected:
                return self.error(401, "invalidAuth")
            self.registered = True
            return web.json_response({"token": TOKEN}, status=201)
        if not self.registered or (
            request.query.get("token") != TOKEN
            and not path.startswith(f"clients/{TOKEN}")
            and request.headers.get("Authorization") != f"Bearer {TOKEN}"
        ):
            return self.error(401, "invalidToken")
        if path == f"clients/{TOKEN}/callbacks":
            if request.method == "DELETE":
                self.callbacks = {}
                return web.json_response({})
            if version == "v2" and "batchEvents" in body:
                return self.error(400, "operationNotSupported")
            if any(
                not body[key].startswith("https://")
                for key in ("serviceCallback", "valueCallback")
                if key in body
            ):
                return self.error(422, "unprocessable")
            self.callbacks = body
            return web.json_response({})
        if path == f"clients/{TOKEN}" and request.method == "DELETE":
            self.registered = False
            self.callbacks = {}
            return web.Response(status=204)
        if path == "uiconfig":
            return web.json_response(self.ui_config(version))
        if path.startswith("values/"):
            uid = path.split("/")[-1]
            if uid not in ("aaaa", "aaab"):
                return self.error(401, "invalidAuth")
            if request.method == "PUT":
                self.value = str(body["value"])
                if self.callbacks.get("valueCallback"):
                    async with (
                        aiohttp.ClientSession() as session,
                        session.post(
                            self.callbacks["valueCallback"],
                            json={
                                "token": TOKEN,
                                "events": [{"uid": "aaab", "value": self.value}],
                            },
                            ssl=False,
                        ) as response,
                    ):
                        await response.read()
                return web.json_response({})
            return web.json_response({"values": [{"uid": "aaab", "value": self.value}]})
        return self.error(404, "resourceNotFound")

    @staticmethod
    def error(status: int, code: str) -> web.Response:
        """Return the documented Gira error envelope."""
        return web.json_response(
            {"error": {"code": code, "message": code}}, status=status
        )

    @staticmethod
    def ui_config(version: str) -> dict:
        """Include new v3 metadata without changing the existing function UID."""
        return {
            "uid": "development-config",
            "functions": [
                {
                    "uid": "aaaa",
                    "displayName": "Test switch",
                    "functionType": "de.gira.schema.functions.Switch",
                    "channelType": "de.gira.schema.channels.Switch2"
                    if version == "v3"
                    else "de.gira.schema.channels.Switch",
                    "dataPoints": [
                        {
                            "uid": "aaab",
                            "name": "OnOff",
                            "canRead": True,
                            "canWrite": True,
                            "canEvent": True,
                        }
                    ],
                    **(
                        {"iconID": 1, "scenes": [{"name": "Test scene", "number": 1}]}
                        if version == "v3"
                        else {}
                    ),
                }
            ],
            "locations": [{"displayName": "Test room", "functions": ["aaaa"]}],
            "trades": [],
        }


if __name__ == "__main__":
    # The simulator is never published to a host port by Compose.
    simulator = GiraSimulator(os.environ.get("GIRA_MOCK_API_VERSION", "v2"))
    with tempfile.TemporaryDirectory() as tls_directory:
        web.run_app(
            simulator.app,
            port=8443,
            ssl_context=create_tls_context(Path(tls_directory)),
            access_log=None,
        )
