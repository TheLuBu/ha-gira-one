"""Verify v3 negotiation, backwards compatibility and failure behavior over HTTPS."""

import asyncio
from datetime import UTC, datetime, timedelta
from inspect import iscoroutinefunction
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.const import EVENT_HOMEASSISTANT_STOP

import tests.test_baseline as baseline
from custom_components.gira_one import (
    GiraServiceCallbackView,
    GiraValueCallbackView,
    _async_refresh_states,
    _StateRefresh,
    async_setup_entry,
)
from custom_components.gira_one.api import GiraApiAuthError, GiraApiRequestError
from custom_components.gira_one.const import DATA_API_CLIENT, DATA_UI_CONFIG, DOMAIN
from custom_components.gira_one.light import GiraLight
from custom_components.gira_one.switch import GiraSwitch
from dev.mock_gira import TOKEN


class GiraV3Tests(baseline.GiraBaselineTests):
    """Run the baseline again on v3, plus the migration's specific guarantees."""

    async def asyncSetUp(self) -> None:
        """Select simulated firmware 4.0+ before any requests are made."""
        await super().asyncSetUp()
        self.simulator.version = "v3"

    async def test_v3_is_preferred_and_batches_are_requested(self) -> None:
        """New metadata preserves function identities; callbacks opt into batching."""
        info = await self.client.check_api_availability()
        self.assertEqual(self.client.api_version, "v3")
        self.assertEqual(info["uidLength"], 4)
        await self.client.register_client("urn:homeassistant:test")
        config = await self.client.get_ui_config()
        self.assertEqual(config["functions"][0]["uid"], "aaaa")
        self.assertIn("scenes", config["functions"][1])
        self.assertTrue(
            await self.client.register_callbacks(
                "https://homeassistant/service", "https://homeassistant/value"
            )
        )
        self.assertIs(self.simulator.callbacks["batchEvents"], True)
        self.assertTrue(
            all(path.startswith("/api/v3/") for _, path, _ in self.simulator.requests)
        )

    async def test_fallback_to_v2_omits_batch_option(self) -> None:
        """An old device is probed once for v3, then all operations use v2."""
        self.simulator.version = "v2"
        await self.client.check_api_availability()
        self.assertEqual(self.client.api_version, "v2")
        await self.client.register_client("urn:homeassistant:test")
        await self.client.register_callbacks(
            "https://homeassistant/service", "https://homeassistant/value"
        )
        self.assertNotIn("batchEvents", self.simulator.callbacks)
        self.assertEqual(self.simulator.requests[0][1], "/api/v3/")
        self.assertTrue(
            all(
                path.startswith("/api/v2/")
                for _, path, _ in self.simulator.requests[1:]
            )
        )

    async def test_errors_other_than_404_do_not_trigger_fallback(self) -> None:
        """Auth, locks, rate limits and server faults must remain visible."""
        for status in (401, 423, 429, 500):
            with self.subTest(status=status):
                self.client._rate_limit_until = 0
                self.simulator.error_status = status
                self.simulator.requests.clear()
                with self.assertRaises((GiraApiAuthError, GiraApiRequestError)):
                    await self.client.check_api_availability()
                self.assertEqual(len(self.simulator.requests), 1)
                self.assertEqual(self.simulator.requests[0][1], "/api/v3/")

    async def test_429_does_not_repeat_writes_or_start_reauth(self) -> None:
        """A rejected write stops, and further requests respect a short cooldown."""
        await self.client.check_api_availability()
        await self.client.register_client("urn:homeassistant:test")
        auth_error = Mock()
        self.client.set_auth_error_callback(auth_error)
        self.simulator.requests.clear()
        self.simulator.error_status = 429
        self.simulator.error_code = "tooManyRequests"
        with self.assertRaises(GiraApiRequestError) as error:
            await self.client.set_value("aaab", 1)
        self.assertEqual(error.exception.status_code, 429)
        with self.assertRaises(GiraApiRequestError):
            await self.client.set_value("aaab", 1)
        self.assertEqual(len(self.simulator.requests), 1)
        auth_error.assert_not_called()
        # Once the cooldown expires, later user requests can succeed normally.
        self.client._rate_limit_until = 0
        self.simulator.error_status = None
        self.assertTrue(await self.client.set_value("aaab", 1))

    async def test_invalid_token_starts_reauth_but_hidden_uid_does_not(self) -> None:
        """v3 uses HTTP 401 for both expired tokens and inaccessible data points."""
        await self.client.check_api_availability()
        await self.client.register_client("urn:homeassistant:test")
        auth_error = Mock()
        self.client.set_auth_error_callback(auth_error)
        with self.assertRaises(GiraApiRequestError):
            await self.client.get_value("hidden")
        auth_error.assert_not_called()
        self.simulator.registered = False
        with self.assertRaises(GiraApiAuthError):
            await self.client.get_ui_config()
        auth_error.assert_called_once()

    async def test_callback_rejects_malformed_payloads_and_wrong_tokens(self) -> None:
        """Validate the whole batch before dispatching its first event."""
        url = await self.callback_url()
        for payload, status in (
            ([], 400),
            ({"token": TOKEN, "events": {}}, 400),
            ({"token": TOKEN, "events": [{"uid": "aaab", "value": "1"}, None]}, 400),
            ({"token": "incorrect", "events": []}, 404),
            ({"token": TOKEN, "events": []}, 200),
        ):
            with (
                self.subTest(payload=payload),
                patch("custom_components.gira_one.async_dispatcher_send") as dispatch,
            ):
                async with self.session.post(url, json=payload) as response:
                    self.assertEqual(response.status, status)
                dispatch.assert_not_called()

    async def test_delivery_failure_recovers_state_for_only_reporting_entry(
        self,
    ) -> None:
        """Authenticate both routes and preserve ordinary events alongside failures."""
        await self.client.register_client("urn:homeassistant:test")
        config = await self.client.get_ui_config()
        self.simulator.values["aaab"] = "1"
        for view_type, events in (
            (GiraServiceCallbackView, []),
            (GiraValueCallbackView, [{"uid": "aaab", "value": "0"}]),
        ):
            entry_id = "reporting"
            refresh = _StateRefresh(self.hass, entry_id, self.client)
            other_refresh = Mock()
            self.hass.data[DOMAIN] = {
                entry_id: {
                    DATA_API_CLIENT: self.client,
                    DATA_UI_CONFIG: config,
                    "refresh": refresh,
                },
                "other": {"refresh": other_refresh},
            }
            view = view_type(self.hass, entry_id)
            request = Mock(json=AsyncMock())
            self.simulator.requests.clear()
            with (
                self.subTest(view=view_type),
                patch("custom_components.gira_one.async_dispatcher_send") as dispatch,
            ):
                for fields in (
                    {},
                    {"failures": 0},
                    {"failures": -1},
                    {"failures": "broken"},
                    {"failures": True},
                    {"failures": None},
                ):
                    request.json.return_value = {"token": TOKEN, "events": [], **fields}
                    self.assertEqual((await view.post(request)).status, 200)
                request.json.return_value = {
                    "token": "wrong",
                    "failures": 4,
                    "events": [],
                }
                self.assertEqual((await view.post(request)).status, 404)
                self.assertIsNone(refresh.task)
                request.json.return_value = {
                    "token": TOKEN,
                    "failures": "2" if events else 2,
                    "events": events,
                }
                self.assertEqual((await view.post(request)).status, 200)
                await self.hass.async_block_till_done()
                self.assertEqual(
                    [call.args[-1] for call in dispatch.call_args_list],
                    ["0", "1"] if events else ["1"],
                )
                self.assertTrue(
                    all(
                        call.args[1].endswith("_reporting")
                        for call in dispatch.call_args_list
                    )
                )
                self.assertEqual(
                    [(method, path) for method, path, _ in self.simulator.requests],
                    [("GET", "/api/v3/values/aaab")],
                )
                other_refresh.request.assert_not_called()

    async def test_empty_failure_callback_repairs_a_stale_switch(self) -> None:
        """An authenticated HTTP callback reaches the subscribed entity via a GET."""
        await self.client.register_client("urn:homeassistant:test")
        url = await self.callback_url()
        config = await self.client.get_ui_config()
        refresh = _StateRefresh(self.hass, "test-entry", self.client)
        self.hass.data[DOMAIN]["test-entry"].update(
            {DATA_UI_CONFIG: config, "refresh": refresh}
        )
        switch = GiraSwitch(
            Mock(entry_id="test-entry"), self.client, config["functions"][0], "hub"
        )
        switch.hass = self.hass
        with (
            patch.object(switch, "async_on_remove", side_effect=self.addCleanup),
            patch.object(switch, "async_write_ha_state") as write_state,
        ):
            await switch.async_added_to_hass()
            self.assertFalse(switch.is_on)
            self.simulator.values["aaab"] = "1"
            self.simulator.requests.clear()
            async with self.session.post(
                url, json={"token": TOKEN, "events": [], "failures": 1}
            ) as response:
                self.assertEqual(response.status, 200)
            await self.hass.async_block_till_done()
            self.assertTrue(switch.is_on)
            write_state.assert_called_once_with()
            self.assertEqual(
                self.simulator.requests, [("GET", "/api/v3/values/aaab", {})]
            )

    async def test_overlapping_failures_and_unload(self) -> None:
        """One follow-up covers losses mid-read; unload cancels that read."""
        refresh = _StateRefresh(self.hass, "test-entry", self.client)
        started = asyncio.Event()
        release = asyncio.Event()
        second = asyncio.Event()

        async def read(*_args: object) -> None:
            if started.is_set():
                second.set()
                await asyncio.Event().wait()
            started.set()
            await release.wait()

        with patch(
            "custom_components.gira_one._async_refresh_states", side_effect=read
        ) as reads:
            refresh.request()
            await started.wait()
            for _ in range(10):
                refresh.request()
            self.assertEqual(reads.await_count, 1)
            release.set()
            await second.wait()
            await refresh.stop()
            refresh.request()
            self.assertTrue(refresh.task.cancelled())
            self.assertEqual(reads.await_count, 2)

    async def test_read_only_reconciliation_recovers_a_missed_event(self) -> None:
        """Periodic reconciliation obtains current states even without callbacks."""
        await self.client.check_api_availability()
        await self.client.register_client("urn:homeassistant:test")
        self.hass.data[DOMAIN] = {
            "test-entry": {
                DATA_API_CLIENT: self.client,
                DATA_UI_CONFIG: await self.client.get_ui_config(),
            }
        }
        self.simulator.values["aaab"] = "1"
        self.simulator.reject_function_reads = True
        self.simulator.requests.clear()
        with patch("custom_components.gira_one.async_dispatcher_send") as dispatch:
            await _async_refresh_states(self.hass, "test-entry", self.client)
            self.assertEqual(dispatch.call_args.args[-2:], ("aaab", "1"))
        self.assertEqual(
            [(method, path) for method, path, _ in self.simulator.requests],
            [("GET", "/api/v3/values/aaab")],
        )

    async def test_point_suppression_keeps_state_readable(self) -> None:
        """The 51st event sticks locally; other points still deliver in the batch."""
        await self.client.register_client("urn:homeassistant:test")
        await self.client.register_callbacks(
            "https://demo/service", "https://demo/value"
        )
        self.hass.data[DOMAIN] = {
            "test-entry": {DATA_UI_CONFIG: await self.client.get_ui_config()}
        }
        clock = Mock(return_value=0)
        self.simulator.clock = clock
        with patch.object(
            self.simulator, "_deliver", new_callable=AsyncMock
        ) as deliver:
            await self.simulator.publish_values(
                [{"uid": "aaab", "value": n % 2} for n in range(51)]
                + [{"uid": "aaaa-other", "value": 1}]
            )
            self.assertEqual(len(deliver.await_args.args[0]), 51)
            self.assertEqual(
                deliver.await_args.args[0][-2:],
                [{"uid": "aaab", "value": "1"}, {"uid": "aaaa-other", "value": "1"}],
            )
            clock.return_value = 10
            await self.simulator.publish_values([{"uid": "aaab", "value": 0}])
            deliver.assert_awaited_once()
            self.simulator.requests.clear()
            with patch("custom_components.gira_one.async_dispatcher_send") as dispatch:
                await _async_refresh_states(self.hass, "test-entry", self.client)
            self.assertEqual(dispatch.call_args.args[-2:], ("aaab", "0"))
            self.assertEqual(
                [method for method, _, _ in self.simulator.requests], ["GET"]
            )
            self.simulator.project_changed()
            await self.simulator.publish_values([{"uid": "aaab", "value": 1}])
            self.assertEqual(deliver.await_count, 2)

    async def test_global_storm_counts_events_before_batching(self) -> None:
        """500 may deliver together; the next event blocks all UIDs until restart."""
        self.simulator.clock = Mock(return_value=0)
        self.simulator.callbacks = {
            "valueCallback": "https://demo/value",
            "batchEvents": True,
        }
        burst = [{"uid": str(n % 20), "value": n % 2} for n in range(500)]
        with patch.object(
            self.simulator, "_deliver", new_callable=AsyncMock
        ) as deliver:
            await self.simulator.publish_values(burst)
            self.assertEqual(len(deliver.await_args.args[0]), 500)
            await self.simulator.publish_values([{"uid": "other", "value": 1}])
            self.simulator.clock.return_value = 10
            self.simulator.project_changed()
            await self.simulator.publish_values(burst[:1])
            deliver.assert_awaited_once()
            self.simulator.restart()
            await self.simulator.publish_values(burst[:1])
            self.assertEqual(deliver.await_count, 2)

    async def test_window_expires_and_v2_delivers_individually(self) -> None:
        """Old events fall out of the sliding window and v2 has no storm limits."""
        self.simulator.clock = Mock(return_value=0)
        self.simulator.callbacks = {"valueCallback": "https://demo/value"}
        events = [{"uid": "aaab", "value": n % 2} for n in range(50)]
        with patch.object(
            self.simulator, "_deliver", new_callable=AsyncMock
        ) as deliver:
            await self.simulator.publish_values(events)
            self.simulator.clock.return_value = 5
            await self.simulator.publish_values(events)
            self.assertEqual(deliver.await_count, 100)
            self.simulator.version = "v2"
            await self.simulator.publish_values(events)
            self.assertEqual(deliver.await_count, 150)

    async def test_switch_and_dimmer_command_trace(self) -> None:
        """One on, off and brightness request produce exactly three point events."""
        await self.client.register_client("urn:homeassistant:test")
        await self.client.register_callbacks(
            "https://demo/service", "https://demo/value"
        )
        function = (await self.client.get_ui_config())["functions"][0]
        switch = GiraSwitch(Mock(entry_id="test-entry"), self.client, function, "hub")
        function["dataPoints"].append(
            {"name": "Brightness", "uid": "aaac", "canWrite": True}
        )
        light = GiraLight(Mock(entry_id="test-entry"), self.client, function, "hub")
        self.simulator.values["aaac"] = "0"
        self.simulator.requests.clear()
        with patch.object(
            self.simulator, "_deliver", new_callable=AsyncMock
        ) as deliver:
            await switch.async_turn_on()
            await switch.async_turn_off()
            await light.async_turn_on(brightness=128)
            self.assertEqual(
                [call.args[0] for call in deliver.await_args_list],
                [
                    [{"uid": "aaab", "value": "1"}],
                    [{"uid": "aaab", "value": "0"}],
                    [{"uid": "aaac", "value": "50"}],
                ],
            )
            self.assertEqual(
                [method for method, _, _ in self.simulator.requests], ["PUT"] * 3
            )

    async def test_initial_state_reads_datapoints_when_function_reads_are_unsupported(
        self,
    ) -> None:
        """Actual firmware can reject a function UID while exposing readable DPs."""
        await self.client.register_client("urn:homeassistant:test")
        config = await self.client.get_ui_config()
        function = config["functions"][0]
        function["dataPoints"].append(
            {"uid": "command", "name": "Command", "canRead": False}
        )
        self.simulator.reject_function_reads = True
        self.simulator.requests.clear()
        entity = GiraSwitch(Mock(entry_id="test-entry"), self.client, function, "hub")
        await entity._fetch_initial_state()
        self.assertIs(entity.is_on, False)
        self.assertEqual(
            [(method, path) for method, path, _ in self.simulator.requests],
            [("GET", "/api/v3/values/aaab")],
        )

    async def test_debug_logs_do_not_contain_access_token(self) -> None:
        """Do not leak query or callback-registration path tokens into logs."""
        await self.client.check_api_availability()
        await self.client.register_client("urn:homeassistant:test")
        with self.assertLogs("custom_components.gira_one.api", level="DEBUG") as logs:
            await self.client.get_ui_config()
            await self.client.register_callbacks(
                "https://homeassistant/service", "https://homeassistant/value"
            )
            await self.client.remove_callbacks()
        self.assertNotIn(TOKEN, "\n".join(logs.output))

    async def test_scheduled_reconciliation_is_async_and_cancellable(self) -> None:
        """HA must await the installed periodic job and cancel it on unload."""
        await self.client.register_client("urn:homeassistant:test")
        entry = Mock(
            entry_id="test-entry",
            data={
                "host": self.client._host,
                "username": "test-user",
                "password": "test-password",
                "client_id": "urn:homeassistant:test",
                "access_token": TOKEN,
            },
        )
        cancel = Mock()
        with (
            patch("custom_components.gira_one.GiraApiClient", return_value=self.client),
            patch(
                "custom_components.gira_one._async_register_device",
                new=AsyncMock(return_value="hub"),
            ),
            patch("custom_components.gira_one._async_cleanup_stale_devices"),
            patch(
                "custom_components.gira_one._async_register_callbacks",
                new=AsyncMock(return_value=True),
            ),
            patch.object(
                self.hass,
                "config_entries",
                Mock(async_forward_entry_setups=AsyncMock()),
                create=True,
            ),
            patch(
                "custom_components.gira_one.async_track_time_interval",
                return_value=cancel,
            ) as timer,
        ):
            self.assertTrue(await async_setup_entry(self.hass, entry))
        scheduled_action = timer.call_args.args[1]
        self.assertTrue(iscoroutinefunction(scheduled_action))
        self.assertEqual(timer.call_args.args[2], timedelta(minutes=5))
        entry.async_on_unload.assert_any_call(cancel)
        self.simulator.values["aaab"] = "1"
        with patch("custom_components.gira_one.async_dispatcher_send") as dispatch:
            await scheduled_action(datetime.now(UTC))
            await self.hass.async_block_till_done()
            self.assertEqual(dispatch.call_args.args[-2:], ("aaab", "1"))
        self.simulator.callbacks = {"serviceCallback": "https://homeassistant/service"}
        self.hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
        await self.hass.async_block_till_done()
        self.assertFalse(self.simulator.callbacks)
        self.assertTrue(self.simulator.registered)

    async def test_reconciliation_stops_on_rate_limit(self) -> None:
        """A failed refresh must not continue querying the remaining functions."""
        await self.client.register_client("urn:homeassistant:test")
        config = await self.client.get_ui_config()
        config["functions"].append({**config["functions"][0], "uid": "aabb"})
        self.hass.data[DOMAIN] = {"test-entry": {DATA_UI_CONFIG: config}}
        self.simulator.error_status = 429
        self.simulator.error_code = "tooManyRequests"
        self.simulator.requests.clear()
        with patch("custom_components.gira_one.async_dispatcher_send") as dispatch:
            await _async_refresh_states(self.hass, "test-entry", self.client)
        dispatch.assert_not_called()
        self.assertEqual(len(self.simulator.requests), 1)
