"""Device-key session for the Gira One device website."""

import base64
import hashlib
import json
from contextlib import suppress
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import (
    GiraApiAuthError,
    GiraApiClientError,
    GiraApiConnectionError,
    GiraApiRequestError,
)


def _device_token(key: str, challenge: dict[str, Any]) -> str:
    """Answer the device website's password challenge for the device account."""
    salt = challenge.get("salt")
    session_salt = challenge.get("sessionSalt")
    if (
        not isinstance(salt, str)
        or not isinstance(session_salt, str)
        or not session_salt
    ):
        raise GiraApiRequestError("Invalid device login challenge")
    if challenge.get("version") == "GDS_1":
        digest = hashlib.sha256(key.encode() + salt.encode("ascii")).digest()
        password_hash = base64.b64encode(digest).decode("ascii").rstrip("=")
    elif challenge.get("version") == "1":
        first = hashlib.sha256(key.encode("latin-1")).hexdigest()
        password_hash = hashlib.sha256(f"{first}+{salt}".encode("ascii")).hexdigest()
    else:
        raise GiraApiRequestError("Unsupported device login version")
    return (
        hashlib.sha256(f"{password_hash}+{session_salt}".encode("ascii"))
        .hexdigest()
        .upper()
    )


class GiraDeviceSession:
    """Keep device-website cookies private to one administrator's dialog."""

    def __init__(self, hass: HomeAssistant, host: str) -> None:
        """Create an isolated cookie jar, including cookies from LAN IPs."""
        self._url = f"https://{host}/webservice"
        self._session = async_create_clientsession(
            hass,
            verify_ssl=False,
            auto_cleanup=False,
            cookie_jar=aiohttp.CookieJar(unsafe=True),
            timeout=aiohttp.ClientTimeout(total=10),
        )

    async def _command(
        self, command: str, data: dict[str, str] | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"command": command, "keepAlive": True}
        if data is not None:
            payload["data"] = data
        try:
            async with self._session.post(
                self._url, data=json.dumps(payload), allow_redirects=False
            ) as response:
                if response.status in (401, 403):
                    raise GiraApiAuthError("Device website denied access")
                if response.status != 200:
                    raise GiraApiRequestError("Device website rejected the request")
                result = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise GiraApiConnectionError("Device website unavailable") from err
        except ValueError as err:
            raise GiraApiRequestError("Unreadable device website response") from err
        if not isinstance(result, dict):
            raise GiraApiRequestError("Invalid device website response")
        if "error" in result:
            if str(result.get("id")) in {"215", "220", "235"}:
                raise GiraApiAuthError("Device website denied access")
            raise GiraApiRequestError("Device website returned an error")
        body = result.get("data", result)
        if not isinstance(body, dict):
            raise GiraApiRequestError("Invalid device website data")
        return body

    async def login(self, key: str) -> None:
        """Use the website's Anlagenschlüssel account and verify the session."""
        challenge = await self._command("getPasswordSalt", {"username": "device"})
        try:
            token = _device_token(key, challenge)
        except UnicodeError as err:
            raise GiraApiRequestError("Unsupported device challenge encoding") from err
        await self._command(
            "doAuthenticateSession", {"username": "device", "token": token}
        )
        if not (await self._command("getDeviceInfo")).get("DeviceId"):
            raise GiraApiAuthError("Device login could not be confirmed")

    async def restart(self) -> None:
        """Submit one reboot request using this session."""
        await self._command("reboot")

    async def close(self) -> None:
        """End the remote session and always discard local cookies."""
        try:
            if self._session.cookie_jar:
                with suppress(GiraApiClientError):
                    await self._command("doCloseSession")
        finally:
            self._session.cookie_jar.clear()
            self._session.detach()
