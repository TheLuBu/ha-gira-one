# Hardware test with an independent Home Assistant instance

The `hardware` profile uses the pinned HA runtime and the checked-out integration.
It mounts only `.hardware/config` and the integration source, never production HA
configuration. `.hardware/` and `.env.hardware` are ignored by Git. Config, keys,
HA instance ID, Gira client ID and tokens survive stopping/recreating the container.

## Start

1. Copy `dev/hardware.env.example` to `.env.hardware` and set the test PC's LAN IP.
   `GIRA_TEST_URL` must be the HTTPS address reachable from the real Gira server.
2. Run from the repository root:

   ```sh
   docker compose --env-file .env.hardware --profile hardware up -d --build hardware
   ```

3. Open the configured HTTPS URL. The bootstrap generates a self-signed certificate
   with that hostname/IP in its subject alternative names. It is valid for seven
   days and is kept across restarts. Accept it in the browser only for this test
   endpoint; browser acceptance does not establish that Gira accepts the certificate.
4. Complete onboarding with a new HA account. Do not restore a backup, copy `.storage`
   or connect unrelated integrations. The bootstrap refuses an existing HA config
   without its own test marker.
5. In this test instance, add **Gira One** under **Settings > Devices & services**.
   Enter the real Gira host and preferably a dedicated Gira test user's credentials.
   Restrict that user's access to the chosen test functions where possible.

The bootstrap uses a first-start YAML import for HTTP/TLS, before an authenticated
HTTP configuration API is available. HA 2026.8+ stores subsequent HTTP settings
through **Settings > System > Network**. Bootstrap never overwrites existing config
or keys. Keep the bootstrap URL stable throughout this test. For later changes to
addresses or TLS, review the test instance's YAML/imported settings together.

If Windows Firewall blocks incoming callbacks, permit only the actual Gira IP to
the chosen TCP port on the test PC. Do not add router port forwarding. This profile
does not change firewall rules automatically. The real Gira server must be able to
route to the configured address and complete a TLS connection. If it rejects the
test certificate, use an appropriately trusted certificate/hostname for this test
instance and configure its HTTP settings accordingly.

## Docker route overlaps the real LAN

If Windows can read the real Gira API but the same request times out inside the
test container, inspect the Docker VM's route. A pre-existing Docker bridge may
cover the physical LAN even though this project's own subnet does not overlap it:

```powershell
$giraIp = '192.168.178.10'  # Replace with your actual Gira server's address.
docker run --rm --network host --entrypoint ip ha-gira-dev-hardware route get $giraIp
docker run --rm --network host --entrypoint ip ha-gira-dev-hardware route
```

In the observed case, Docker sent the Gira IP to a bridge for `192.168.176.0/20`;
the VM's external default gateway was `192.168.65.1` on `eth0`. Confirm the current
gateway and that the exact destination IP is not used by a Docker container before
adding a host-specific route. For that verified layout:

```powershell
docker run --rm --user 0:0 --cap-add NET_ADMIN --network host --entrypoint ip ha-gira-dev-hardware route add "$giraIp/32" via 192.168.65.1 dev eth0
docker run --rm --network host --entrypoint ip ha-gira-dev-hardware route get $giraIp
```

This modifies routing for only that destination in the Docker VM. It does not
remove or recreate another project's network, change Gira configuration or modify
the production HA instance. The capability is needed only by this one-shot route
command; the HA test container continues running without it. Do not replace an
existing host-specific route blindly. The route survives test-container restarts
but may disappear when Docker Desktop/its VM restarts; check it before a later
test session. Its inverse is the same command with `route del "$giraIp/32"`.

The original integration availability probe timed out twice before this route
change and returned `GDS-REST-API`, version `3`, twice afterward. No credentials,
client registration or device commands were needed for that diagnosis. This is
a host-network prerequisite, so an isolated application unit test cannot guard
against recurrence; the real container-to-device probe is the regression check.

## Test sequence

The integration accepts explicitly configured HTTPS origins with a LAN IP when
HA serves native TLS; loopback/unspecified addresses are rejected. Callback
registration completes before entity platforms load, so a failed registration
can retry cleanly. Firmware that rejects function-UID value reads is supported
by reading each data point marked `canRead` individually. Shutdown cleanup runs
as an async HA event listener and preserves the dedicated client's token.

- First verify **Using Gira IoT REST API v3**, discovery and initial states.
- Operate one physical switch. Require a prompt callback update, rather than a
  change appearing only at the five-minute reconciliation interval.
- Only then send one command to an agreed, non-critical device. Commands affect
  real devices and can trigger automations in the production installation.
- Reload the integration and restart only the test container. Check that states,
  entity IDs and callbacks return. Never restart the shared Gira server or download
  a GPA project just to exercise a test case.
- Keep rate-limit/event-storm fault injection in the simulator.

View this test container's logs:

```sh
docker compose --env-file .env.hardware --profile hardware logs --tail 100 hardware
```

Do not run `dev.smoke` against the real server: that script is specifically for
the disposable simulator environment and sends switching commands.

## Stop

```sh
docker compose --env-file .env.hardware --profile hardware stop hardware
```

Stopping HA runs this integration's callback cleanup; the dedicated Gira client
remains registered so its token can be reused on the next test start. Only remove
the test integration/client when finished permanently, and keep the persistent
test config until its client has been unregistered. Do not remove production
registrations or erase the test config while callbacks are still registered.

Specification: [Gira IoT REST API v3](https://partner.gira.de/data3/Gira_IoT_REST_API_v3_DE.pdf),
sections 3 and 4. Multiple clients are supported; callback registration replaces
the callback set for the same token. An independent instance ID and client ID are
therefore required even when an existing Gira user is used.
