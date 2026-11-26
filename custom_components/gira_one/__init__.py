"""Gira One integration."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from .api import (
    GiraApiAuthError,
    GiraApiClient,
    GiraApiClientError,
    GiraApiConnectionError,
    GiraApiRequestError,
)
from .callback_url import detect_callback_url, normalize_callback_url
from .const import (
    API_VERSION,
    CONF_CALLBACK_URL,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    DATA_API_CLIENT,
    DATA_HUB_DEVICE_ID,
    DATA_LOCATION_MAP,
    DATA_UI_CONFIG,
    DOMAIN,
    EVENT_TYPE_PROJECT_CONFIG_CHANGED,
    EVENT_TYPE_RESTART,
    EVENT_TYPE_STARTUP,
    EVENT_TYPE_TEST,
    EVENT_TYPE_UI_CONFIG_CHANGED,
    GIRA_FUNCTION_TYPE_TO_HA_PLATFORM,
    PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)

SERVICE_CALLBACK_PATH = f"/api/{DOMAIN}/service_callback"
VALUE_CALLBACK_PATH = f"/api/{DOMAIN}/value_callback"

# Used to signal entities that new data is available
SIGNAL_DATA_UPDATE = f"{DOMAIN}_data_update"


def _build_location_map(locations: list[dict[str, Any]]) -> dict[str, str]:
    """Recursively build a map of function UID to location name (suggested area).

    Only the leaf location's display name is used, which typically maps well
    to a Home Assistant Area (e.g. a room name).
    """
    location_map: dict[str, str] = {}
    for location in locations:
        name = location.get("displayName", "Unknown")

        for func in location.get("functions", []):
            uid: str | None = None
            if isinstance(func, str):
                uid = func
            elif isinstance(func, dict):
                uid = func.get("uid")

            if uid:
                location_map[uid] = name

        if sub_locations := location.get("locations"):
            location_map.update(_build_location_map(sub_locations))

    return location_map


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Gira One from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    host = entry.data[CONF_HOST]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    client_id = entry.data["client_id"]
    access_token = entry.data["access_token"]

    @callback
    def _handle_auth_error() -> None:
        """Handle an authentication error reported by the API client.

        This starts a reauth flow so the user can re-enter credentials
        without having to delete and re-add the integration.
        """
        _LOGGER.warning(
            "Gira API reported an authentication error; starting reauth flow"
        )
        entry.async_start_reauth(hass)

    api_client = GiraApiClient(host, username, password, hass)
    api_client.set_credentials(token=access_token, client_id=client_id)

    try:
        await api_client.check_api_availability()
        _LOGGER.debug("Attempting to fetch UI config with stored token")
        ui_config = await api_client.get_ui_config()
    except GiraApiAuthError:
        _LOGGER.info(
            "Stored token is invalid or expired, attempting to re-register client"
        )
        try:
            new_token = await api_client.register_client(client_id)
        except GiraApiAuthError as reauth_err:
            raise ConfigEntryAuthFailed(
                f"Authentication with Gira One Server failed: {reauth_err}"
            ) from reauth_err
        except (GiraApiConnectionError, GiraApiRequestError) as retry_err:
            raise ConfigEntryNotReady(
                f"Could not re-register client with Gira One Server: {retry_err}"
            ) from retry_err

        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "access_token": new_token}
        )
        api_client.set_credentials(token=new_token, client_id=client_id)
        try:
            ui_config = await api_client.get_ui_config()
        except GiraApiClientError as retry_err:
            raise ConfigEntryNotReady(
                f"Failed to fetch UI config after re-registration: {retry_err}"
            ) from retry_err
    except (GiraApiConnectionError, GiraApiRequestError) as err:
        raise ConfigEntryNotReady(
            f"Failed to communicate with Gira One Server: {err}"
        ) from err

    location_map = _build_location_map(ui_config.get("locations", []))

    _LOGGER.info("Successfully connected to Gira IoT API and fetched UI config")

    # Activate auth error callback only after setup succeeds.
    # During setup, token refresh is handled inline — firing the callback
    # would start a spurious reauth flow.
    api_client.set_auth_error_callback(_handle_auth_error)

    # The hub device must exist before any child device references it via
    # 'via_device_id', so register it first and keep its registry id around.
    hub_device_id = await _async_register_device(hass, entry, api_client)

    refresh = _StateRefresh(hass, entry.entry_id, api_client)
    changes = _ConfigChange(hass, entry.entry_id, api_client, ui_config.get("uid"))
    hass.data[DOMAIN][entry.entry_id] = {
        "changes": changes,
        "refresh": refresh,
        DATA_API_CLIENT: api_client,
        DATA_UI_CONFIG: ui_config,
        DATA_LOCATION_MAP: location_map,
        DATA_HUB_DEVICE_ID: hub_device_id,
    }

    # Register callback views with HA and callback URLs with the Gira device
    if not await _async_register_callbacks(hass, entry, api_client):
        await changes.stop()
        await refresh.stop()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise ConfigEntryNotReady(
            "Could not register Gira One callback URLs. Ensure Home Assistant "
            "is reachable from the Gira One Server via an HTTPS URL"
        )

    # Register callbacks before loading platforms, so a failed attempt can retry cleanly.
    _async_cleanup_stale_devices(hass, entry, ui_config)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if api_client.api_version == API_VERSION:

        async def _refresh_states(_now: datetime) -> None:
            # A startup callback can itself be lost. Confirm readiness before
            # re-enabling value reads on the regular five-minute interval.
            if refresh.waiting_for_startup:
                try:
                    await api_client.get_server_details()
                except GiraApiClientError:
                    return
                if refresh.waiting_for_startup:
                    refresh.startup()
            else:
                refresh.request()

        # v3 may suppress callbacks after an event storm without notifying clients.
        entry.async_on_unload(
            async_track_time_interval(
                hass,
                _refresh_states,
                timedelta(minutes=5),
            )
        )

    async def _async_stop(_event: Event) -> None:
        await _async_cleanup_resources(hass, entry)

    # Remove callbacks on shutdown while preserving this test/client's token.
    entry.async_on_unload(
        hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP,
            _async_stop,
        )
    )

    return True


class _ConfigChange:
    """Coalesce UI/project events and avoid reloading a locked REST server."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, client: GiraApiClient, uid: str | None
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.client = client
        self.uid = uid
        self.timer: asyncio.TimerHandle | None = None
        self.task: asyncio.Task | None = None
        self.reloading = False
        self.check_again = False
        self.stopped = False

    @callback
    def request(self, *, project: bool = False) -> None:
        """A project download takes precedence over an immediate UI check."""
        if self.stopped or self.reloading:
            return
        if project:
            if self.timer:
                self.timer.cancel()
            if self.task and not self.task.done():
                self.task.cancel()
        elif self.timer:
            return
        elif self.task and not self.task.done():
            self.check_again = True
            return
        self._schedule(10 if project else 0, 0)

    def _schedule(self, delay: int, retries: int) -> None:
        self.timer = self.hass.loop.call_later(delay, self._start, retries)

    def _start(self, retries: int) -> None:
        self.timer = None
        self.check_again = False
        self.task = self.hass.async_create_task(self._check(retries))

    async def _check(self, retries: int) -> None:
        try:
            current = await self.client.get_ui_config_uid()
        except GiraApiRequestError as err:
            if err.status_code in (423, 429):
                if retries < 2:
                    self._schedule(10, retries + 1)
                else:
                    _LOGGER.warning(
                        "Gira configuration check deferred after repeated HTTP %s",
                        err.status_code,
                    )
                return
        except GiraApiClientError:
            pass
        else:
            if current and self.uid == current:
                if self.check_again:
                    self._schedule(0, 0)
                return
        self.reloading = True
        # Reload unloads this coordinator. Keep it outside the cancellable read.
        self.hass.async_create_task(self._reload())

    async def _reload(self) -> None:
        try:
            if not self.stopped:
                await self.hass.config_entries.async_reload(self.entry_id)
        finally:
            self.reloading = False

    async def stop(self) -> None:
        """Cancel pending reads before unloading the entry."""
        self.stopped = True
        if self.timer:
            self.timer.cancel()
        if self.task and not self.task.done():
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task


class _StateRefresh:
    """Serialize interval and delivery-failure reads for one loaded entry."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, client: GiraApiClient
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.client = client
        self.task: asyncio.Task | None = None
        self.pending = False
        self.waiting_for_startup = False
        self.stopped = False

    @callback
    def request(self) -> None:
        """Coalesce requests, retaining failures received after a pass started."""
        if self.stopped or self.waiting_for_startup:
            return
        self.pending = True
        if self.task is None or self.task.done():
            self.task = self.hass.async_create_task(self._run())

    async def restart(self) -> None:
        """Suspend reads while Gira is restarting."""
        self.waiting_for_startup = True
        self.pending = False
        previous, self.task = self.task, None
        if previous and not previous.done():
            previous.cancel()
            with suppress(asyncio.CancelledError):
                await previous

    def startup(self) -> None:
        """Reconcile when the Gira service is ready, even without prior notice."""
        self.waiting_for_startup = False
        self.request()

    async def _run(self) -> None:
        while self.pending:
            self.pending = False
            await _async_refresh_states(self.hass, self.entry_id, self.client)

    async def stop(self) -> None:
        """Reject further requests and finish cancellation before cleanup."""
        self.stopped = True
        if self.task and not self.task.done():
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task


async def _async_refresh_states(
    hass: HomeAssistant, entry_id: str, api_client: GiraApiClient
) -> None:
    """Reconcile readable states without writing values or generating Gira events."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry_id)
    if not entry_data:
        return
    try:
        for function in entry_data[DATA_UI_CONFIG].get("functions", []):
            if function.get("functionType") not in GIRA_FUNCTION_TYPE_TO_HA_PLATFORM:
                continue
            if not any(dp.get("canRead") for dp in function.get("dataPoints", [])):
                continue
            data = await api_client.get_function_values(function)
            for datapoint in data.get("values", []):
                async_dispatcher_send(
                    hass,
                    f"{SIGNAL_DATA_UPDATE}_{entry_id}",
                    datapoint["uid"],
                    datapoint["value"],
                )
    except GiraApiClientError as err:
        # Stop this pass on errors, including 429, instead of hammering the server.
        _LOGGER.warning("Could not reconcile Gira states: %s", err)


async def _async_register_device(
    hass: HomeAssistant, entry: ConfigEntry, api_client: GiraApiClient
) -> str:
    """Register the main Gira One hub device and return its registry id."""
    try:
        server_details = await api_client.get_server_details()
    except GiraApiClientError as err:
        _LOGGER.warning("Could not fetch detailed server info: %s", err)
        server_details = {}

    device_registry = dr.async_get(hass)
    hub_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.unique_id or entry.data[CONF_HOST])},
        name=entry.title,
        manufacturer="Gira",
        model=server_details.get("deviceType"),
        sw_version=server_details.get("deviceVersion"),
    )
    return hub_device.id


@callback
def _async_cleanup_stale_devices(
    hass: HomeAssistant, entry: ConfigEntry, ui_config: dict[str, Any]
) -> None:
    """Remove devices for Gira functions that no longer exist in the project."""
    device_registry = dr.async_get(hass)
    current_uids: set[str] = {
        function["uid"]
        for function in ui_config.get("functions", [])
        if function.get("uid")
        and function.get("functionType") in GIRA_FUNCTION_TYPE_TO_HA_PLATFORM
    }
    hub_identifier = entry.unique_id or entry.data[CONF_HOST]

    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        gira_identifiers = [
            identifier for domain, identifier in device.identifiers if domain == DOMAIN
        ]
        if not gira_identifiers:
            continue
        # Keep the hub device itself
        if hub_identifier in gira_identifiers:
            continue
        if not any(identifier in current_uids for identifier in gira_identifiers):
            _LOGGER.info(
                "Removing stale Gira device %s (no longer in project config)",
                device.name,
            )
            device_registry.async_update_device(
                device.id, remove_config_entry_id=entry.entry_id
            )


async def _async_register_callbacks(
    hass: HomeAssistant, entry: ConfigEntry, api_client: GiraApiClient
) -> bool:
    """Register callback URLs and views with Home Assistant and the Gira device."""
    override = entry.data.get(CONF_CALLBACK_URL)
    base_url = (
        normalize_callback_url(override) if override else detect_callback_url(hass)
    )
    if not base_url:
        _LOGGER.error(
            "Cannot determine an HTTPS URL for Gira callbacks. Reconfigure the Gira One entry and enter a reachable HTTPS callback base URL"
        )
        return False

    service_callback_url = f"{base_url}{SERVICE_CALLBACK_PATH}/{entry.entry_id}"
    value_callback_url = f"{base_url}{VALUE_CALLBACK_PATH}/{entry.entry_id}"

    _LOGGER.info("Registering Gira service callback URL: %s", service_callback_url)
    _LOGGER.info("Registering Gira value callback URL: %s", value_callback_url)

    registered = hass.data.setdefault(f"{DOMAIN}_callback_views", set())
    if entry.entry_id not in registered:
        hass.http.register_view(GiraServiceCallbackView(hass, entry.entry_id))
        hass.http.register_view(GiraValueCallbackView(hass, entry.entry_id))
        registered.add(entry.entry_id)

    try:
        if not await api_client.register_callbacks(
            service_callback_url, value_callback_url
        ):
            _LOGGER.error("Gira callback registration did not succeed")
            return False
    except GiraApiClientError as err:
        _LOGGER.error("Failed to register Gira callbacks: %s", err)
        return False

    _LOGGER.info("Gira callbacks registered successfully")
    return True


async def _async_cleanup_resources(
    hass: HomeAssistant, entry: ConfigEntry, *, unregister: bool = False
) -> None:
    """Remove callbacks and optionally unregister the client.

    By default the client is kept registered so the stored token remains
    valid across restarts.  Pass ``unregister=True`` only when the config
    entry is being permanently removed.
    """
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not entry_data:
        return

    api_client: GiraApiClient | None = entry_data.get(DATA_API_CLIENT)
    if api_client is None:
        return

    await entry_data["changes"].stop()
    await entry_data["refresh"].stop()

    # Prevent re-auth loop during cleanup
    api_client.disable_auth_error_callback()

    _LOGGER.info("Cleaning up Gira One resources for %s", entry.title)
    try:
        await api_client.remove_callbacks()
    except GiraApiClientError as err:
        _LOGGER.warning("Error removing Gira callbacks: %s", err)

    if unregister:
        try:
            await api_client.unregister_client()
        except GiraApiClientError as err:
            _LOGGER.warning("Error unregistering Gira client: %s", err)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Unregister the client when the config entry is permanently removed."""
    host = entry.data[CONF_HOST]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    client_id = entry.data.get("client_id")
    access_token = entry.data.get("access_token")

    if not access_token or not client_id:
        return

    api_client = GiraApiClient(host, username, password, hass)
    api_client.set_credentials(token=access_token, client_id=client_id)
    try:
        await api_client.check_api_availability()
        await api_client.unregister_client()
    except GiraApiClientError:
        _LOGGER.warning("Could not unregister client during entry removal")


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading Gira One entry: %s", entry.title)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        await _async_cleanup_resources(hass, entry)
        hass.data[DOMAIN].pop(entry.entry_id, None)
        _LOGGER.info("Gira One entry %s unloaded successfully", entry.title)

    return unload_ok


class BaseGiraCallbackView(HomeAssistantView):
    """Base view for Gira callbacks."""

    requires_auth = False
    url = "OVERRIDE_IN_SUBCLASS"
    name = "OVERRIDE_IN_SUBCLASS_WITH_API_PREFIX"
    cors_allowed = True

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the callback view."""
        self.hass = hass
        self.entry_id = entry_id
        self.url = f"{self.url}/{entry_id}"
        self.name = f"{self.name}:entry_{entry_id}"

    async def post(self, request: web.Request) -> web.Response:
        """Handle POST requests from the Gira API."""
        try:
            data = await request.json()
        except ValueError:
            _LOGGER.warning("Received invalid JSON in Gira callback %s", self.url)
            return self.json_message("Invalid JSON", status_code=400)

        if not isinstance(data, dict):
            return self.json_message("Expected a JSON object", status_code=400)

        _LOGGER.debug("Received Gira callback on %s", self.url)

        api_client: GiraApiClient | None = (
            self.hass.data.get(DOMAIN, {}).get(self.entry_id, {}).get(DATA_API_CLIENT)
        )

        if not api_client or not api_client.token:
            _LOGGER.warning(
                "Callback received for unconfigured entry %s", self.entry_id
            )
            return self.json_message("Client not configured", status_code=404)

        if data.get("token") != api_client.token:
            _LOGGER.error("Mismatched token in Gira callback; ignoring")
            return self.json_message("Token mismatch", status_code=404)

        events = data.get("events", [])
        if not isinstance(events, list) or any(
            not isinstance(event, dict) for event in events
        ):
            return self.json_message("Invalid events", status_code=400)
        await self.process_events(events, api_client)
        failures = data.get("failures", 0)
        # Firmware may encode this counter as a JSON number or a decimal string.
        if isinstance(failures, str) and failures.isascii() and failures.isdecimal():
            failures = failures.lstrip("0") or "0"
            if len(failures) < 20:
                failures = int(failures)
        if type(failures) is not int or failures < 0:
            _LOGGER.debug("Ignoring invalid Gira callback failure count")
        elif failures:
            _LOGGER.warning(
                "Gira reported %d failed callback deliveries for %s",
                failures,
                self.entry_id,
            )
            self.hass.data[DOMAIN][self.entry_id]["refresh"].request()
        return self.json({}, status_code=200)

    async def process_events(self, events: list, api_client: GiraApiClient) -> None:
        """Process the events from the callback."""
        raise NotImplementedError


class GiraServiceCallbackView(BaseGiraCallbackView):
    """View to handle Gira service callbacks."""

    url = SERVICE_CALLBACK_PATH
    name = f"api:{DOMAIN}:service_callback"

    async def process_events(self, events: list, api_client: GiraApiClient) -> None:
        """Process service events."""
        for event_data in events:
            event_type = event_data.get("event")
            _LOGGER.info("Processing Gira service event: %s", event_type)

            entry_data = self.hass.data[DOMAIN][self.entry_id]
            if event_type == EVENT_TYPE_TEST:
                continue
            if event_type == EVENT_TYPE_RESTART:
                await entry_data["refresh"].restart()
            elif event_type == EVENT_TYPE_STARTUP:
                entry_data["refresh"].startup()
            elif event_type in (
                EVENT_TYPE_PROJECT_CONFIG_CHANGED,
                EVENT_TYPE_UI_CONFIG_CHANGED,
            ):
                entry_data["changes"].request(
                    project=event_type == EVENT_TYPE_PROJECT_CONFIG_CHANGED
                )
            else:
                _LOGGER.warning("Unknown Gira service event type: %s", event_type)


class GiraValueCallbackView(BaseGiraCallbackView):
    """View to handle Gira value callbacks."""

    url = VALUE_CALLBACK_PATH
    name = f"api:{DOMAIN}:value_callback"

    async def process_events(self, events: list, api_client: GiraApiClient) -> None:
        """Process value update events."""
        for event_data in events:
            uid = event_data.get("uid")
            value = event_data.get("value")
            if uid is not None:
                _LOGGER.debug("Dispatching value update for UID %s", uid)
                async_dispatcher_send(
                    self.hass, f"{SIGNAL_DATA_UPDATE}_{self.entry_id}", uid, value
                )
