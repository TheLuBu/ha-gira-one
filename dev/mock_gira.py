"""Small HTTPS Gira simulator derived from the official v2/v3 specifications.

Only implements the integration's existing platforms, not a real Gira device.
Credentials and tokens are synthetic and restricted to the development network.
"""

import os
import tempfile
from collections import defaultdict, deque
from collections.abc import Callable
from pathlib import Path
from time import monotonic

import aiohttp
from aiohttp import web

from dev.tls import create_tls_context

USERNAME = "test-user"
PASSWORD = "test-password"  # noqa: S105
TOKEN = "GiraDevelopmentToken0000000000001"  # noqa: S105


class GiraSimulator:
    """Keep state across actual HTTP requests from Home Assistant."""

    def __init__(
        self, version: str = "v2", clock: Callable[[], float] = monotonic
    ) -> None:
        """Initialize the synthetic switch and record requests for assertions."""
        self.version = version
        self.registered = False
        self.callbacks: dict = {}
        self.values = {"aaab": "0"}
        self.clock = clock
        self.global_events: deque[float] = deque()
        self.point_events: dict[str, deque[float]] = defaultdict(deque)
        self.suppressed: set[str] = set()
        self.global_suppression = False
        self.requests: list[tuple[str, str, dict]] = []
        self.error_status: int | None = None
        self.error_code = "generic"
        self.reject_function_reads = False
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
            if uid == "aaaa" and request.method == "GET" and self.reject_function_reads:
                return self.error(422, "unprocessable")
            if uid == "aaaa" and request.method == "GET":
                uid = "aaab"
            if uid not in self.values:
                return self.error(401, "invalidAuth")
            if request.method == "PUT":
                await self.publish_values([{"uid": uid, "value": body["value"]}])
                return web.json_response({})
            return web.json_response(
                {"values": [{"uid": uid, "value": self.values[uid]}]}
            )
        if path == "values" and request.method == "PUT":
            updates = body["values"]
            if any(item["uid"] not in self.values for item in updates):
                return self.error(401, "invalidAuth")
            await self.publish_values(updates)
            return web.json_response({})
        return self.error(404, "resourceNotFound")

    def project_changed(self) -> None:
        """Simulate a project download clearing per-datapoint suppression."""
        self.point_events.clear()
        self.suppressed.clear()

    def restart(self) -> None:
        """Simulate rebooting the development device."""
        self.project_changed()
        self.global_events.clear()
        self.global_suppression = False

    def _allow_event(self, uid: str) -> bool:
        if self.version != "v3":
            return True
        if self.global_suppression:
            return False
        now = self.clock()
        for window in (self.global_events, self.point_events[uid]):
            while window and window[0] <= now - 5:
                window.popleft()
            window.append(now)
        if len(self.global_events) > 500:
            self.global_suppression = True
        if len(self.point_events[uid]) > 50:
            self.suppressed.add(uid)
        return not self.global_suppression and uid not in self.suppressed

    async def publish_values(self, updates: list[dict]) -> None:
        """Apply simulated bus events before filtering and batching their callbacks."""
        events = []
        for update in updates:
            uid = update["uid"]
            self.values[uid] = str(update["value"])
            if self._allow_event(uid):
                events.append({"uid": uid, "value": self.values[uid]})
        if not events or not self.callbacks.get("valueCallback"):
            return
        if self.callbacks.get("batchEvents"):
            await self._deliver(events)
        else:
            for event in events:
                await self._deliver([event])

    async def _deliver(self, events: list[dict]) -> None:
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                self.callbacks["valueCallback"],
                json={"token": TOKEN, "events": events},
                ssl=False,
            ) as response,
        ):
            await response.read()

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
