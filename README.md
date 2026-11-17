# Gira One Integration for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/tbsv/ha-gira-one?display_name=tag&sort=semver)](https://github.com/tbsv/ha-gira-one/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Validate](https://github.com/tbsv/ha-gira-one/actions/workflows/validate.yml/badge.svg)](https://github.com/tbsv/ha-gira-one/actions/workflows/validate.yml)

This integration connects Home Assistant to a **Gira One Server** via its local REST API. It is based on the [Gira IoT REST API v3 specification](https://partner.gira.de/data3/Gira_IoT_REST_API_v3_DE.pdf), with automatic fallback to [v2](https://partner.gira.de/data3/Gira_IoT_REST_API_v2_DE.pdf) when the v3 endpoint is unavailable (HTTP 404).
It exposes lights, covers, and room thermostats as native Home Assistant entities and receives real-time status updates via callbacks (Local Push), so state changes made at the physical switch or in the Gira app are immediately reflected in Home Assistant.

> [!IMPORTANT]
> **Home Assistant must be reachable from the Gira One Server via an HTTPS URL.** The Gira One Server pushes state updates back to Home Assistant via HTTP callbacks and explicitly rejects non-HTTPS targets. Either an *internal* or an *external* URL works, as long as it uses `https://` and the Gira Server can reach it on the network. The integration will refuse to set up if no HTTPS URL is configured.

## API v3 compatibility

- The API version is detected during initial configuration, integration setup/reload and removal. Existing config entries, client IDs and entity unique IDs are preserved.
- v3 callback registration enables `batchEvents`; every event in a callback batch is processed. v2 registration omits that option.
- Gira scenes appear under their function device when the server supplies a scene number and writable recall datapoint. Each activation sends the published number once.
- v3 can suppress value callbacks after an event storm. A read-only reconciliation every five minutes recovers current states while callbacks are missing. It does not reset the server's protection or recover intermediate events.
- HTTP 429 starts a five-second request cooldown. Commands are not automatically replayed. Invalid-token responses start reauthentication; access denied to an individual UID does not.

See [Local development](docs/DEVELOPMENT.md) for the reproducible HA runtime, HTTPS simulator, tests and the hardware checks still required before release.

## Features

| Platform | Capabilities |
|---|---|
| **Switch** | On/off |
| **Light** | On/off, brightness (dimming), color temperature (tunable white), RGB/W color |
| **Cover** | Open, close, stop, set position, set tilt (slat) position — for roller shutters and venetian blinds |
| **Climate** | Current temperature, target temperature, HVAC mode, preset modes (Comfort, Eco/Night, Away/Standby, Protection) |
| **Scene** | Activate a numbered Gira scene |
| **Sensor** | Current and target temperature exposed as dedicated sensor entities per Gira climate function (for graphing and automations) |

### Supported Gira function types

Behind the scenes, the following Gira function types are mapped automatically:

- `de.gira.schema.functions.Switch` → Switch
- `de.gira.schema.functions.KNX.Light` → Light
- `de.gira.schema.functions.ColoredLight` → Light
- `de.gira.schema.functions.TunableLight` → Light
- `de.gira.schema.functions.Covering` → Cover
- `de.gira.schema.functions.KNX.HeatingCooling` → Climate
- `de.gira.schema.functions.KNX.FanCoil` → Climate
- `de.gira.schema.functions.SaunaHeating` → Climate

### Gira icons

Switches with a radiator (11), plug (15) or musical note (72) use corresponding
on/off icons in Home Assistant. Scenes also recognize the crescent moon (3),
star (4) and heart (76). These are entity defaults and HA icon customizations
take precedence. Other IDs, including `-1`, and v2 entries without an icon
use the platform defaults. Covers, lights and climate entities retain HA's
state-aware presentation. Location names suggest HA areas. Gira trade and
location icons are project metadata, while HA areas are user-managed and may
be shared by multiple integrations.

The numbers come from the [GPA project interface pictogram table, page 15](https://partner.gira.com/data3/GPA-Projektschnittstelle_Dokumentation.pdf#page=15).

### Scene discovery

`FunctionScene` and legacy `SceneSet` recall through `Execute`. Legacy
`SceneControl` recalls through `Scene`. Only a writable recall point is used.
HA never sends a teach command. v3 supplies scene names and numbers in
`scenes`. When that list is missing or empty, a configured GPA `Scene`
parameter can supply the number (also on v2). A function without either
source is skipped. Renaming a scene preserves its identity as long as its
function UID and scene number are stable.

See the [REST API channel definitions, sections 5.2 and 10](https://partner.gira.de/data3/Gira_IoT_REST_API_v3_DE.pdf)
and [GPA project interface, scene parameters](https://partner.gira.com/data3/GPA_Project_Interface_Documentation.pdf).

## Prerequisites

1. A **Gira One Server** accessible on your local network.
2. A **user account** on the Gira One Server with permissions to control the target devices.
3. **Home Assistant reachable via an HTTPS URL** that the Gira One Server can hit (internal or external — see important note above).

## Installation

### Via HACS (recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=tbsv&repository=ha-gira-one&category=integration)

1. Make sure [HACS](https://hacs.xyz/) is installed.
2. Click the badge above (or in HACS → *Integrations*, click the three-dot menu → *Custom repositories* and add this repository URL with *Integration* as category).
3. Install **Gira One** and restart Home Assistant.

### Manual

1. Copy the `custom_components/gira_one` folder into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.

## Configuration

After installation, add the integration via the UI:

1. Go to **Settings → Devices & Services**.
2. Click **+ Add Integration** and search for *Gira One*.
3. Enter:
   - **Host**: IP address or hostname of your Gira One Server.
   - **Username** / **Password**: A Gira One user account with sufficient permissions.
4. Submit.

Compatible devices will be added automatically and grouped by room (using the Gira project's location names as suggested areas).

## Troubleshooting

**Setup fails with "Cannot determine an HTTPS URL".**
Home Assistant has neither an internal nor external HTTPS URL configured. Set one under *Settings → System → Network → Home Assistant URL* and make sure it uses `https://`. A local HTTPS URL is fine as long as the Gira One Server can reach it and accepts the TLS certificate (self-signed certificates may be rejected — use a private CA or a trusted certificate in that case).

**Setup fails with "invalid_auth".**
Double-check the Gira One username and password. Note that some Gira user roles cannot register new API clients.

**Setup fails with "device_locked" (HTTP 423).**
The Gira One Server is currently being configured from the Gira project planner or is otherwise locked. Wait until the lock is released and retry.

**State changes from the Gira side don't show up in Home Assistant.**
The Gira Server cannot reach Home Assistant on the callback URL. Verify:
- Your external URL uses HTTPS with a certificate trusted by the Gira Server.
- Port forwarding / reverse proxy passes through to Home Assistant.
- No firewall rule is blocking outbound HTTPS from the Gira Server to Home Assistant.

**Password changed on the Gira side.**
The integration will detect the invalid token and start a reauth flow automatically — a notification will appear in Home Assistant prompting for new credentials.

## Support & Contribution

This is a community-developed integration and is **not officially supported by Gira**.

If you encounter bugs or have feature requests, please open an issue on GitHub. Pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## Trademarks

Gira® and the Gira logo are trademarks of Gira Giersiepen GmbH & Co. KG.
This project is not affiliated with or endorsed by Gira. See [NOTICE.md](NOTICE.md)
for details on the brand assets included in this repository.
