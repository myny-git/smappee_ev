# State update rules

REST polling, MQTT callbacks, session refreshes and control actions can run at the
same time. A response must preserve newer state that arrived while it was pending.

## Control actions

1. Validate the request and capture only the inputs needed by the API.
2. Await the write; `state_updates.async_write` handles reconciliation.
3. Apply the confirmed fields through `update_station` or `update_connector`.

The update helpers read the current coordinator data and publish synchronously.
Do not mutate an earlier station/connector reference or publish a snapshot saved
before an `await`. Keep UI state unchanged until the write succeeds.

Successful writes schedule reconciliation with Dashboard. Transport failures,
timeouts and cancellation also schedule reconciliation because the server may
have accepted the command before the response was lost. Authentication failures
propagate through Home Assistant's existing reauthentication handling.

## REST and Dashboard refreshes

Fetch remote values and enrich an isolated staging copy first. After the last
`await`, merge into the current coordinator data and return without yielding.
Preserve current telemetry and recent sessions. `StateChanges` applies individual
fields rather than replacing live nested objects with an older staging snapshot.
Changes received during the refresh take precedence over its remote responses.

The same rule applies to the forced Dashboard refresh after a write. Recent
sessions are requested once per physical station, even with multiple connectors.

## MQTT measurements

`apply_mqtt_properties` returns a `MqttApplyResult` containing the fields actually
accepted. Validation uses the complete configured measurement group. Missing,
truncated and invalid groups retain their previous values and timestamps.
Explicit zero and unchanged valid values both renew their own timestamps.

`MeasurementFreshness` tracks each field separately per site or connector. Power,
current, voltage, energy and charging state cannot renew one another. Empty
messages and heartbeats do not make an old measurement available. Monitoring
entities use these timestamps. Each site/station coordinator shares one 30-second
timer that notifies its listeners to recheck expiry, including when polling is
disabled. The tick performs no remote I/O and does not change API success/failure
state. Removing the last listener or shutting down cancels the timer.

`last_valid_charger_telemetry_rx` records any accepted charger measurement,
including current/energy-only messages. It controls REST polling cadence; it does
not replace the separate freshness timestamp for each measurement.

Charging-state sensors retain their existing REST fallback in normal mode. When
running from cached configuration without REST, they require fresh MQTT charging
state; a power measurement alone does not confirm that state.

Keep entity unique IDs, service schemas and outgoing command payloads stable
when changing these internals.

## Tests

- `tests/test_state_concurrency.py` exercises writes while the live snapshot is
  replaced, success/error/auth/cancellation paths, refresh interleaving and shared
  session requests.
- `tests/test_measurement_freshness.py` routes actual MQTT callbacks and verifies
  stale, invalid, partial, unchanged and zero measurements.
- `mqtt_smoke/test_transport.py` uses real `aiomqtt` and a local Mosquitto broker
  in a separate environment; see the contribution guide for the command.
