"""API client for Gira IoT REST API."""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import API_FALLBACK_VERSION, API_VERSION

_LOGGER = logging.getLogger(__name__)


class GiraApiClientError(Exception):
    """Generic API client error."""


class GiraApiAuthError(GiraApiClientError):
    """Authentication error."""


class GiraApiConnectionError(GiraApiClientError):
    """Connection error."""


class GiraApiRequestError(GiraApiClientError):
    """Invalid request or unexpected response."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        """Keep the HTTP status available for version negotiation."""
        super().__init__(message)
        self.status_code = status_code


class GiraApiClient:
    """Gira IoT REST API Client."""

    def __init__(
        self,
        host: str,
        username: str | None,
        password: str | None,
        hass: HomeAssistant,
        auth_error_callback: Callable[[], None] | None = None,
    ) -> None:
        """Initialize the API client."""
        self._host = host
        self._username = username
        self._password = password
        self._session = async_get_clientsession(
            hass, verify_ssl=False
        )  # Gira IoT API uses self-signed certs
        self._base_url = f"https://{self._host}/api"
        self._token: str | None = None
        self._client_id: str | None = None
        self._auth_error_callback = auth_error_callback
        self._api_version = API_VERSION
        self._rate_limit_until = 0.0

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        requires_auth: bool = True,
        is_registration: bool = False,
        token_in_path: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        """Make a generic API request."""
        if asyncio.get_running_loop().time() < self._rate_limit_until:
            raise GiraApiRequestError(
                "Gira API is temporarily rate limited; try again later.",
                status_code=429,
            )
        url = f"{self._base_url}{path}"
        log_url = url.replace(self._token, "<redacted>") if self._token else url
        headers = {"Accept": "application/json"}
        auth = None

        if requires_auth:
            if is_registration:
                if not self._username or not self._password:
                    msg = "Username or password missing for registration."
                    raise GiraApiAuthError(msg)
                auth = aiohttp.BasicAuth(self._username, self._password)
            elif self._token and not token_in_path:
                if params is None:
                    params = {}
                params["token"] = self._token
            elif not self._token:
                msg = f"Token missing for authenticated request to {path}"
                raise GiraApiAuthError(msg)

        _LOGGER.debug("Request: %s %s", method, log_url)

        try:
            async with asyncio.timeout(15):
                response = await self._session.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json_data,
                    auth=auth,
                )
        except aiohttp.ClientError as err:
            _LOGGER.debug("Request failed for %s %s", method, log_url)
            msg = "Request failed while communicating with Gira."
            raise GiraApiConnectionError(msg) from err
        except TimeoutError as err:
            _LOGGER.debug("Request timed out for %s %s", method, log_url)
            msg = f"Request timed out: {log_url}"
            raise GiraApiConnectionError(msg) from err

        status_code = response.status
        _LOGGER.debug("Response status from %s %s: %s", method, log_url, status_code)

        try:
            response_data = await response.json()
        except (ValueError, aiohttp.ContentTypeError, aiohttp.ClientResponseError):
            response_text = await response.text()
            if not response_text:
                # Empty response body with 200 OK often means success but no data for Gira APIs
                response_data = {}
            else:
                _LOGGER.warning("Non-JSON response from %s", log_url)
                response_data = {}

        if response_data is None:
            response_data = {}
        if not isinstance(response_data, dict):
            raise GiraApiRequestError(
                "Expected a JSON object from Gira.", status_code=status_code
            )

        if not (200 <= status_code < 300):
            error_info = response_data.get("error", {})
            if not isinstance(error_info, dict):
                error_info = {}
            error_code = error_info.get("code", "unknown_api_error")
            error_message = error_info.get("message", "Unknown API error")
            level = (
                logging.DEBUG
                if status_code == 404
                and path == f"/{API_VERSION}/"
                and not requires_auth
                else logging.ERROR
            )
            _LOGGER.log(
                level,
                "API request %s %s failed with status %s: [%s] %s",
                method,
                log_url,
                status_code,
                error_code,
                error_message,
            )
            if status_code in [401, 403]:
                # v3 also uses invalidAuth for a UID hidden from a valid client.
                if error_code == "invalidAuth" and not is_registration:
                    raise GiraApiRequestError(
                        f"Gira denied access to the requested resource: {error_message}",
                        status_code=status_code,
                    )
                # If we have a callback for auth errors, trigger it.
                if self._auth_error_callback:
                    self._auth_error_callback()
                msg = f"Authentication failed: {error_message} (Code: {error_code})"
                raise GiraApiAuthError(msg)
            if status_code == 423:
                msg = f"Device locked: {error_message} (Code: {error_code})"
                raise GiraApiRequestError(msg, status_code=status_code)
            if status_code == 429:
                # Failed-auth quotas refill within five seconds. Never replay writes.
                self._rate_limit_until = asyncio.get_running_loop().time() + 5
            msg = f"API Error {status_code}: [{error_code}] {error_message}"
            raise GiraApiRequestError(msg, status_code=status_code)

        return status_code, response_data

    def set_auth_error_callback(self, callback: Callable[[], None] | None) -> None:
        """Set or clear the auth error callback."""
        self._auth_error_callback = callback

    def disable_auth_error_callback(self) -> None:
        """Disable the auth error callback to prevent reload loops during cleanup."""
        _LOGGER.debug("Disabling auth error callback.")
        self._auth_error_callback = None

    async def check_api_availability(self) -> dict[str, Any]:
        """Prefer v3; fall back only when that version is not supported."""
        for version in (API_VERSION, API_FALLBACK_VERSION):
            try:
                status, data = await self._request(
                    "GET", f"/{version}/", requires_auth=False
                )
            except GiraApiRequestError as err:
                if version == API_VERSION and err.status_code == 404:
                    continue
                raise
            if status == 200 and data.get("info") == "GDS-REST-API":
                self._api_version = version
                _LOGGER.info("Using Gira IoT REST API %s", version)
                return data
            raise GiraApiConnectionError("API availability check failed.")
        raise GiraApiConnectionError("No supported Gira API version found.")

    async def register_client(self, client_id: str) -> str:
        """Register a client or refresh its token using username/password."""
        self._client_id = client_id
        payload = {"client": self._client_id}
        _LOGGER.info("Registering/refreshing client with ID: %s", self._client_id)
        status, data = await self._request(
            "POST",
            f"/{self._api_version}/clients",
            json_data=payload,
            is_registration=True,
        )
        if status == 201 and "token" in data:
            self._token = data["token"]
            _LOGGER.info("Client token acquired successfully.")
            return self._token
        msg = "Client registration/token refresh failed."
        raise GiraApiRequestError(msg)

    async def unregister_client(self) -> None:
        """Unregister this client from the Gira device."""
        if not self._token:
            _LOGGER.warning("No token available, cannot unregister client.")
            return
        _LOGGER.info("Unregistering client.")
        try:
            status, _ = await self._request(
                "DELETE",
                f"/{self._api_version}/clients/{self._token}",
                token_in_path=True,
            )
            if status == 204:
                _LOGGER.info("Client unregistered successfully.")
                self._token = None
        except GiraApiAuthError:
            _LOGGER.warning("Failed to unregister: token was already invalid.")
            self._token = None
        except GiraApiRequestError as e:
            _LOGGER.exception("Failed to unregister client: %s", e)

    async def get_server_details(self) -> dict[str, Any]:
        """Get the server details from info endpoint."""
        status, data = await self._request("GET", f"/{self._api_version}/")
        return data

    async def get_ui_config(self) -> dict[str, Any]:
        """Get the UI configuration."""
        params = {"expand": "dataPointFlags,locations,trades,parameters"}
        status, data = await self._request(
            "GET", f"/{self._api_version}/uiconfig", params=params
        )
        return data

    async def get_value(self, uid: str) -> dict[str, Any]:
        """Get value(s) for a specific UID (datapoint or function)."""
        status, data = await self._request("GET", f"/{self._api_version}/values/{uid}")
        return data

    async def get_function_values(self, function: dict[str, Any]) -> dict[str, Any]:
        """Read individual readable DPs; some firmware rejects function-UID reads."""
        values = []
        for datapoint in function.get("dataPoints", []):
            if not datapoint.get("canRead"):
                continue
            data = await self.get_value(datapoint["uid"])
            values.extend(data.get("values", []))
        return {"values": values}

    async def set_value(self, uid: str, value: Any) -> bool:
        """Set a single value for a data point UID."""
        payload = {"value": value}
        status, _ = await self._request(
            "PUT", f"/{self._api_version}/values/{uid}", json_data=payload
        )
        return status == 200

    async def set_multiple_values(self, values_payload: list[dict[str, Any]]) -> bool:
        """Set multiple values."""
        payload = {"values": values_payload}
        status, _ = await self._request(
            "PUT", f"/{self._api_version}/values", json_data=payload
        )
        return status == 200

    async def register_callbacks(
        self, service_callback_url: str, value_callback_url: str
    ) -> bool:
        """Register callback URLs with the Gira device."""
        payload = {
            "serviceCallback": service_callback_url,
            "valueCallback": value_callback_url,
            "testCallbacks": False,
        }
        if self._api_version == API_VERSION:
            payload["batchEvents"] = True
        status, _ = await self._request(
            "POST",
            f"/{self._api_version}/clients/{self._token}/callbacks",
            json_data=payload,
            token_in_path=True,
        )
        return status == 200

    async def remove_callbacks(self) -> bool:
        """Remove callback URLs from the Gira device."""
        if not self._token:
            _LOGGER.warning("No token, cannot remove callbacks.")
            return False
        try:
            status, _ = await self._request(
                "DELETE",
                f"/{self._api_version}/clients/{self._token}/callbacks",
                token_in_path=True,
            )
            return status == 200
        except GiraApiClientError as e:
            _LOGGER.exception("Failed to remove callbacks during unload: %s", e)
            return False

    @property
    def api_version(self) -> str:
        """Return the API version selected by the availability check."""
        return self._api_version

    @property
    def token(self) -> str | None:
        """Return the current access token."""
        return self._token

    @property
    def client_id(self) -> str | None:
        """Return the current client ID."""
        return self._client_id

    def set_credentials(self, token: str, client_id: str) -> None:
        """Set the token and client_id for an existing client."""
        self._token = token
        self._client_id = client_id
