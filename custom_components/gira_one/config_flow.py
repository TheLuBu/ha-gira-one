"""Config flow for the Gira One integration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.instance_id import async_get as async_get_instance_id
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    GiraApiAuthError,
    GiraApiClient,
    GiraApiConnectionError,
    GiraApiRequestError,
)
from .const import CLIENT_URN_PREFIX, DOMAIN
from .device_website import GiraDeviceSession

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

STEP_REAUTH_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def _async_validate_input(
    hass: HomeAssistant, data: dict[str, Any]
) -> dict[str, Any]:
    """Validate the user input allows us to connect and return server info."""
    host = data[CONF_HOST]
    username = data[CONF_USERNAME]
    password = data[CONF_PASSWORD]

    # Generate a unique client ID for this HA instance.
    ha_instance_id = await async_get_instance_id(hass)
    client_id = f"{CLIENT_URN_PREFIX}:{ha_instance_id}"

    api_client = GiraApiClient(host, username, password, hass, auth_error_callback=None)

    # Step 1: Check if the host is a Gira device (unauthenticated)
    await api_client.check_api_availability()

    # Step 2: Register and get a token (authenticated with user/pass)
    token = await api_client.register_client(client_id)

    # Step 3: Fetch detailed server info
    server_info = await api_client.get_server_details()

    # Step 4: Build a friendly title, including the host for clarity.
    device_name = server_info.get("deviceName")
    if device_name and device_name.lower() != host.lower():
        title = f"{device_name} ({host})"
    else:
        title = host

    # Prefer a stable device identifier over the host for the unique_id.
    # Fall back to the host if none is advertised by the server.
    unique_id = (
        server_info.get("deviceId")
        or server_info.get("serialNumber")
        or server_info.get("uid")
        or host
    )

    return {
        "title": title,
        "unique_id": str(unique_id),
        "client_id": client_id,
        "access_token": token,
        "server_info": server_info,
    }


class GiraOneConfigFlowHandler(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Gira One."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:  # noqa: ARG004
        """Open the device maintenance dialog for this entry."""
        return GiraDeviceMaintenanceFlow()

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._reauth_entry_data: dict[str, Any] | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial user step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                validated = await _async_validate_input(self.hass, user_input)
            except GiraApiAuthError:
                _LOGGER.debug("Authentication failed during setup")
                errors["base"] = "invalid_auth"
            except GiraApiConnectionError:
                _LOGGER.debug("Connection error during setup")
                errors["base"] = "cannot_connect"
            except GiraApiRequestError as err:
                _LOGGER.debug("API request error during setup: %s", err)
                if "locked" in str(err).lower():
                    errors["base"] = "device_locked"
                else:
                    errors["base"] = "api_error"
            except Exception:
                _LOGGER.exception("Unexpected exception during Gira One setup")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(validated["unique_id"])
                self._abort_if_unique_id_configured(
                    updates={CONF_HOST: user_input[CONF_HOST]}
                )
                return self.async_create_entry(
                    title=validated["title"],
                    data={
                        **user_input,
                        "client_id": validated["client_id"],
                        "access_token": validated["access_token"],
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start a reauthentication flow when credentials become invalid."""
        self._reauth_entry_data = dict(entry_data)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauthentication with new credentials."""
        assert self._reauth_entry_data is not None
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            combined = {
                CONF_HOST: self._reauth_entry_data[CONF_HOST],
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            try:
                validated = await _async_validate_input(self.hass, combined)
            except GiraApiAuthError:
                errors["base"] = "invalid_auth"
            except GiraApiConnectionError:
                errors["base"] = "cannot_connect"
            except GiraApiRequestError as err:
                if "locked" in str(err).lower():
                    errors["base"] = "device_locked"
                else:
                    errors["base"] = "api_error"
            except Exception:
                _LOGGER.exception("Unexpected exception during Gira One reauth")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data={
                        **entry.data,
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        "client_id": validated["client_id"],
                        "access_token": validated["access_token"],
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_DATA_SCHEMA,
            errors=errors,
            description_placeholders={"host": self._reauth_entry_data[CONF_HOST]},
        )


class GiraDeviceMaintenanceFlow(OptionsFlow):
    """Check a one-time device key, then offer an explicit restart."""

    def __init__(self) -> None:
        """Start without an authenticated website session."""
        self._device: GiraDeviceSession | None = None
        self._expiry: asyncio.TimerHandle | None = None
        self._removed = False

    @callback
    def _take_session(self) -> GiraDeviceSession | None:
        if self._expiry is not None:
            self._expiry.cancel()
            self._expiry = None
        session, self._device = self._device, None
        return session

    @callback
    def _expire(self) -> None:
        if session := self._take_session():
            self.hass.async_create_task(session.close())

    @callback
    def async_remove(self) -> None:
        """Release the session when the user dismisses the dialog."""
        self._removed = True
        self._expire()

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Verify the key without writing it to flow results or the entry."""
        host = self.config_entry.data[CONF_HOST]
        errors: dict[str, str] = {}
        if user_input is not None:
            session = GiraDeviceSession(self.hass, host)
            try:
                await session.login(user_input[CONF_PASSWORD])
                _LOGGER.info("Gira device login verified")
                if self._removed:
                    return self.async_abort(reason="session_expired")
                self._expire()
                self._device = session
                self._expiry = self.hass.loop.call_later(120, self._expire)
                return await self.async_step_confirm_restart()
            except GiraApiAuthError:
                _LOGGER.warning("Gira device login could not be verified")
                errors["base"] = "invalid_device_key"
            except GiraApiConnectionError:
                _LOGGER.warning("Gira device login unconfirmed: connection failed")
                errors["base"] = "cannot_connect"
            except GiraApiRequestError:
                _LOGGER.warning("Gira device login unconfirmed: device website error")
                errors["base"] = "device_error"
            finally:
                if self._device is not session:
                    await session.close()
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            description_placeholders={"host": host},
            errors=errors,
        )

    async def async_step_confirm_restart(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The default submission finishes after checking the login."""
        if self._device is None:
            return self.async_abort(reason="session_expired")
        if user_input is None:
            return self.async_show_form(
                step_id="confirm_restart",
                data_schema=vol.Schema({vol.Required("restart", default=False): bool}),
                description_placeholders={"host": self.config_entry.data[CONF_HOST]},
                last_step=True,
            )
        session = self._take_session()
        assert session is not None
        try:
            if user_input.get("restart") is True:
                await session.restart()
                _LOGGER.info("Gira One Server accepted the restart request")
            else:
                _LOGGER.info(
                    "Gira device login check finished without requesting a restart"
                )
        except GiraApiAuthError:
            _LOGGER.warning("Gira One Server denied the restart request")
            return self.async_abort(reason="restart_denied")
        except (GiraApiConnectionError, GiraApiRequestError):
            _LOGGER.warning("Gira One Server did not confirm the restart request")
            return self.async_abort(reason="restart_unconfirmed")
        finally:
            await session.close()
        # Options flows render a generic message when saving options. Finish with
        # a translated reason instead: this action has no options to persist.
        return self.async_abort(
            reason="restart_requested"
            if user_input.get("restart") is True
            else "login_verified",
        )
