"""Select a Home Assistant HTTPS origin for Gira callback registration."""

from ipaddress import ip_address
from urllib.parse import urlsplit

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.network import NoURLAvailableError, get_url


def normalize_callback_url(value: str) -> str | None:
    """Accept an HTTPS origin with an optional port and trailing slash."""
    try:
        url = urlsplit(cv.url(value))
        host = (url.hostname or "").rstrip(".")
        if (
            url.scheme != "https"
            or not host
            or url.path not in ("", "/")
            or url.username is not None
            or any(c in value for c in ("?", "#", "\\", "%"))
            or url.port == 0
            or url.netloc.endswith(":")
            or host == "localhost"
            or host.endswith(".localhost")
        ):
            return None
    except (vol.Invalid, ValueError):
        return None
    try:
        address = ip_address(host)
    except ValueError:
        pass  # A DNS name is allowed, including a local network hostname.
    else:
        if address.is_loopback or address.is_unspecified or address.is_multicast:
            return None
    return f"https://{url.netloc}"


def detect_callback_url(hass: HomeAssistant) -> str | None:
    """Use the same HA URL preferences in the form and at runtime."""
    try:
        url = get_url(
            hass,
            require_ssl=True,
            allow_internal=True,
            allow_external=True,
            allow_ip=True,
            prefer_external=True,
        )
    except NoURLAvailableError:
        return None
    return normalize_callback_url(url)
