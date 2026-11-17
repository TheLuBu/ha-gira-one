"""Check the complete disposable HA/Gira stack through its real HTTP APIs."""

import asyncio

from aiohttp import ClientSession, TCPConnector

from dev.mock_gira import PASSWORD, USERNAME

BASE_URL = "https://homeassistant:8123"


async def smoke() -> None:
    """Onboard only our disposable instance, add Gira and verify push updates."""
    async with ClientSession(connector=TCPConnector(ssl=False)) as session:
        for _ in range(90):
            try:
                async with session.get(f"{BASE_URL}/api/onboarding") as response:
                    if response.status == 200:
                        break
            except OSError:
                pass
            await asyncio.sleep(1)
        else:
            raise RuntimeError("Disposable Home Assistant did not become ready")

        async with session.post(
            f"{BASE_URL}/api/onboarding/users",
            json={
                "client_id": "http://localhost/",
                "name": "Development tester",
                "username": "development",
                "password": "development-only",
                "language": "en",
            },
        ) as response:
            response.raise_for_status()
            auth_code = (await response.json())["auth_code"]
        async with session.post(
            f"{BASE_URL}/auth/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "client_id": "http://localhost/",
            },
        ) as response:
            response.raise_for_status()
            session.headers["Authorization"] = (
                f"Bearer {(await response.json())['access_token']}"
            )

        async with session.post(
            f"{BASE_URL}/api/config/config_entries/flow",
            json={"handler": "gira_one", "show_advanced_options": False},
        ) as response:
            response.raise_for_status()
            flow = await response.json()
        async with session.post(
            f"{BASE_URL}/api/config/config_entries/flow/{flow['flow_id']}",
            json={"host": "gira:8443", "username": USERNAME, "password": PASSWORD},
        ) as response:
            response.raise_for_status()
            result = await response.json()
            if result["type"] != "create_entry":
                raise RuntimeError(f"Gira config flow failed: {result.get('errors')}")

        entity_id = None
        for expected in ("off", "on", "off"):
            if entity_id is not None:
                async with session.post(
                    f"{BASE_URL}/api/services/switch/turn_{expected}",
                    json={"entity_id": entity_id},
                ) as response:
                    response.raise_for_status()
            for _ in range(30):
                async with session.get(f"{BASE_URL}/api/states") as response:
                    response.raise_for_status()
                    switches = [
                        state
                        for state in await response.json()
                        if state["entity_id"].startswith("switch.")
                    ]
                if switches and switches[0]["state"] == expected:
                    entity_id = switches[0]["entity_id"]
                    break
                await asyncio.sleep(1)
            else:
                raise RuntimeError(f"Expected switch state {expected}, got {switches}")
            print(f"PASS: {entity_id} = {expected}")
        print("PASS: real config flow, entity setup, HTTPS commands and push callbacks")
        async with session.get(f"{BASE_URL}/api/states") as response:
            response.raise_for_status()
            scenes = {
                state["entity_id"]
                for state in await response.json()
                if state["entity_id"].startswith("scene.")
            }
        if not scenes:
            raise RuntimeError("Simulator scenes missing after discovery")
        async with session.post(
            f"{BASE_URL}/api/services/scene/turn_on",
            json={"entity_id": min(scenes)},
        ) as response:
            response.raise_for_status()
        async with session.post(
            f"{BASE_URL}/api/config/config_entries/entry/{result['result']['entry_id']}/reload"
        ) as response:
            response.raise_for_status()
        async with session.get(f"{BASE_URL}/api/states") as response:
            response.raise_for_status()
            states = await response.json()
            if {
                state["entity_id"]
                for state in states
                if state["entity_id"].startswith("scene.")
            } != scenes:
                raise RuntimeError("Reload changed the scene identities")
            switches = [
                state for state in states if state["entity_id"].startswith("switch.")
            ]
        if (
            len(switches) != 1
            or switches[0]["entity_id"] != entity_id
            or switches[0]["state"] != "off"
        ):
            raise RuntimeError(
                "Reload changed the switch identity or failed to restore its state"
            )
        print(
            "PASS: unload/reload preserves the existing entity and reads its current state"
        )


if __name__ == "__main__":
    asyncio.run(smoke())
