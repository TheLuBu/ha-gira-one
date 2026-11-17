"""Start a disposable HA instance with HTTPS callbacks inside Compose."""

import os
from pathlib import Path

from dev.tls import create_tls_context

config = Path("/config")
create_tls_context(config / "tls")
(config / "configuration.yaml").write_text(
    "homeassistant:\n"
    "  name: Gira development\n"
    "  internal_url: https://homeassistant:8123\n"
    "http:\n"
    "  ssl_certificate: /config/tls/cert.pem\n"
    "  ssl_key: /config/tls/key.pem\n"
    "frontend:\n"
    "config:\n",
    encoding="utf-8",
)
os.execvp("python", ["python", "-m", "homeassistant", "--config", str(config)])  # noqa: S606, S607
