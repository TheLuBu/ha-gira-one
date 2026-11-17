# Local development

Requires Git and Docker Desktop with Linux containers. No host Python, Gira
credentials or connection to the real installation is needed for the simulator.

The runtime is Home Assistant **2026.11.2**, pinned by image digest in `Dockerfile`.
Ruff uses the version already pinned in `requirements.txt`. Tests use Python's
standard-library `unittest` and the HA image's existing aiohttp and cryptography
packages; there is no second Home Assistant dependency tree to keep in sync.

## Run the tests

From the repository root, on Windows, Linux or macOS:

```sh
docker compose run --rm --build tests
docker compose run --rm tests ruff check custom_components tests dev
docker compose run --rm tests ruff format custom_components tests dev --check
```

The test container mounts source files read-only and runs without root. Tests
start real local HTTPS servers with disposable certificates and execute the
integration's actual API client and callback views. They do not contact hardware.

## Run a disposable Home Assistant instance

```sh
docker compose --profile demo up -d --build gira homeassistant
docker compose run --rm tests python -m dev.smoke
```

The smoke command creates a synthetic development user and configures the
integration in this disposable HA instance. It checks discovery, initial state,
on/off commands, HTTPS push callbacks and unload/reload with stable entity IDs.
Run it on a fresh instance. For another run, restart the demo services first:

```sh
docker compose --profile demo restart gira homeassistant
docker compose run --rm tests python -m dev.smoke
```

Open <https://localhost:18123> to inspect the test instance. The certificate is
self-signed and deliberately valid only for development. After the smoke test,
the HA login is `development` / `development-only`. On a fresh instance you can
also onboard manually and add **Gira One** with:

- Host: `gira:8443`
- Username: `test-user`
- Password: `test-password`

These credentials are synthetic. The simulator is available only inside the
Compose network. The HA UI binds only to the local machine, on port **18123**,
separate from the normal HA port. Its `/config` is temporary memory: restarting
or stopping the demo discards its users, tokens and configuration. Nothing is
copied from the real HA installation.

Stop the two demo services when finished:

```sh
docker compose --profile demo stop homeassistant gira
```

The project uses its own subnet `10.254.230.0/24`, because the development host's
automatic Docker address pools were exhausted. Set `GIRA_DEV_SUBNET` to another
unused subnet if it overlaps a local network. No other Docker network is removed.

## Simulate API versions

The simulator defaults to **v2**. It also supports **v3**. Select v3 in PowerShell:

```powershell
$env:GIRA_MOCK_API_VERSION = 'v3'
docker compose --profile demo up -d gira
docker compose --profile demo restart homeassistant
docker compose run --rm tests python -m dev.smoke
```

On Linux/macOS, set `export GIRA_MOCK_API_VERSION=v3` instead. To switch back,
set the same variable to `v2`, recreate `gira` using `up -d`, and restart HA.

## Original baseline

The unmodified integration from upstream commit
`490fb403fa38097afb129584dad65ca65152331e` (manifest version **1.0.5**) passed
the initial three baseline tests and the real HA/v2 simulator smoke test before
the API migration. That is local simulator evidence, not hardware certification.

Development commit `0bf0f69` adds this environment while keeping the integration
source unchanged. Check out that commit in a separate checkout to reproduce the
original baseline, then use the same commands above. The following feature commit
contains the API migration and its additional regression tests.

After the migration, the regression suite exercises v3 and v2. GitHub Actions
also runs the complete HA smoke test separately against both simulator versions.
The v3 client opts into batched callbacks and performs a read-only state refresh
every five minutes. This recovers current state if callbacks are suppressed; it
cannot restore intermediate events or disable the firmware's protection.

HTTP 429 starts a five-second client cooldown. Failed commands are surfaced and
are never replayed automatically. A hidden UID returning `invalidAuth` is kept
separate from `invalidToken`, which triggers HA reauthentication.

Contract sources:

- [Gira IoT REST API v3, 2026-08-31](https://partner.gira.de/data3/Gira_IoT_REST_API_v3_DE.pdf), sections 2.2, 2.3, 3, 4.4, 4.7, 5.2 and 6.
- [Gira IoT REST API v2](https://partner.gira.de/data3/Gira_IoT_REST_API_v2_DE.pdf) for the backwards-compatible fallback.

## Hardware check before release

Use the separate [`hardware` profile](HARDWARE_TEST.md) for the real server.
The `demo` profile and `dev.smoke` remain simulator-only.

Use a separate HA test installation with its own instance ID and Gira client ID.
Do not copy the production `.storage` directory: callbacks are registered per
Gira client, and reusing its credentials/client ID can replace production callbacks.
The real Gira server must reach the test instance over HTTPS; the container-only
`homeassistant` hostname and disposable certificate do not establish that.

On actual firmware, verify the negotiated API version, discovery of the existing
devices, a physical switch update, commands, HA reload/restart and reconnects.
Back up the normal HA installation before replacing its installed integration.
Do not commit real passwords, access tokens, HA config directories or API captures.

The v3 simulator counts bus events in a sliding five-second window before
batching. The 51st event on one datapoint silences that point until a project
change. The 501st event across points silences all value callbacks until a
device restart. Unit tests advance an injected clock and exercise both resets.
The switch on/off and dimmer brightness trace produces three writes and three
events. The dimmer sends brightness once. No automatic command replay is needed.

A positive callback `failures` counter reports failed delivery attempts since
the last success. HA logs the count and schedules a read on the affected entry.
Silence alone is also consistent with an idle installation. The firmware supplies
no positive indication of suppression. The five-minute v3 read can recover the
latest readable state in either case. It cannot reconstruct intermediate events.
The simulator covers selected documented shapes. Certificate trust, bus traffic
and user permissions still require separate verification.
