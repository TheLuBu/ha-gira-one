"""Bootstrap an independent, persistent Home Assistant hardware-test instance."""

import ipaddress
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from dev.tls import create_tls_context


def prepare_config(config: Path, callback_url: str) -> None:
    """Initialize only a fresh test directory and preserve it across restarts."""
    url = urlsplit(callback_url)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or url.path not in ("", "/")
        or url.query
        or url.fragment
        or url.port == 0
    ):
        raise ValueError(
            "GIRA_TEST_URL must be an absolute HTTPS origin without credentials or a path"
        )
    try:
        address = ipaddress.ip_address(url.hostname)
    except ValueError:
        if url.hostname == "localhost":
            raise ValueError("Gira cannot call back to localhost") from None
    else:
        if address.is_loopback or address.is_unspecified or address.is_multicast:
            raise ValueError(
                "GIRA_TEST_URL must be reachable from the real Gira server"
            )

    marker = config / ".gira-hardware-test"
    configuration = config / "configuration.yaml"
    if not marker.exists() and (
        configuration.exists() or (config / ".storage").exists()
    ):
        raise ValueError(
            "Refusing an existing HA configuration without the hardware-test marker"
        )
    if marker.exists() and marker.read_text(encoding="utf-8") != callback_url:
        raise ValueError(
            "Callback URL changed: adjust this test instance through Settings > System > Network"
        )

    config.mkdir(parents=True, exist_ok=True)
    tls = config / "tls"
    certificate = tls / "cert.pem"
    key = tls / "key.pem"
    if certificate.exists() != key.exists():
        raise ValueError(
            "Test TLS certificate/key pair is incomplete; no files were replaced"
        )
    if not certificate.exists():
        create_tls_context(tls, (url.hostname, "localhost", "127.0.0.1"))
    if not configuration.exists():
        configuration.write_text(
            "homeassistant:\n"
            "  name: Gira hardware test\n"
            f"  internal_url: {json.dumps(callback_url)}\n"
            "http:\n"
            "  ssl_certificate: /config/tls/cert.pem\n"
            "  ssl_key: /config/tls/key.pem\n"
            "frontend:\n"
            "config:\n",
            encoding="utf-8",
        )
    marker.write_text(callback_url, encoding="utf-8")


if __name__ == "__main__":
    prepare_config(Path("/config"), os.environ.get("GIRA_TEST_URL", ""))
    os.execvp("python", ["python", "-m", "homeassistant", "--config", "/config"])  # noqa: S606, S607
