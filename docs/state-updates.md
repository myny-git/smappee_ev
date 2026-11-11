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

## Publishing

Only a successful REST poll replaces the coordinator data, through Home
Assistant's normal refresh. Every other writer changes the current data in place
and calls `async_update_listeners()`: MQTT, confirmed commands, restored values,
session refreshes and the forced Dashboard refresh. `async_set_updated_data()`
would also mark the coordinator as successfully updated and restart its poll
timer, so a local change could report a failed REST connection as recovered.

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

`StateConfirmations` also records field confirmations from successful commands
and live MQTT merges. A confirmed value can equal the value at refresh start,
so comparing snapshots alone cannot detect every newer update. Each refresh
captures a confirmation version before I/O and preserves the current values of
fields confirmed afterwards. Staging merges do not record live confirmations.
Requests started after a confirmation can reconcile those fields normally.

The same rule applies to the forced Dashboard refresh after a write. Recent
sessions are requested once per physical station, even with multiple connectors.

A confirmation protects against requests that were already in flight; it is not
a lock. A poll that starts after a command reports what the cloud holds and wins,
so a change made outside Home Assistant can always be adopted.

## Field ownership between the two cloud reads

The smart-device poll and the Dashboard station details are separate requests
without a revision or timestamp, so their answers cannot be ordered reliably.
Prefer one remote owner per field over ordering logic.

- **LED brightness.** The smart-device list is scanned for the whole service
  location and is not tied to one station; the Dashboard station details are.
  Once the Dashboard has identified the station's LED
  (`dashboard_led_device_id`), it owns the brightness together with MQTT and
  commands. The poll only supplies a value while none is known, or while the
  Dashboard has not identified an LED.
- **Connector minimum/maximum current, minimum surplus and grid support.** Both
  reads report these per connector. When they overlap, the value that changed
  the published state last is kept (`StateChanges`), and a forced Dashboard
  refresh is always followed by a forced poll.
- **Session state.** While MQTT reports charger state live (transport connected
  and a fresh `charger_state`), a poll does not replace it. Once that state is
  stale, the poll takes over, including starting and ending sessions.
- **Everything else** has a single source: MQTT telemetry and status, Dashboard
  station configuration, or the session query.

## MQTT connection changes

MQTT sends configuration only when it changes, so a change made during an outage
is not repeated after the reconnect. A lost and a restored connection therefore
each force one REST poll at once, serialized behind a poll already in flight.
At most one immediate read per direction is started per
`MQTT_RESYNC_MIN_INTERVAL`; further changes inside that interval leave the
forced read pending for the coordinator's next regular tick, so a flapping
broker cannot become one REST request per flap. The initial connection needs no
extra poll, and cached monitoring-only runtimes never poll.

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

- `tests/test_state_reconciliation_contract.py` states the intended outcome of
  each interleaving in its docstrings; start there.
- `tests/test_state_concurrency.py` exercises writes while the live snapshot is
  replaced, success/error/auth/cancellation paths, refresh interleaving and shared
  session requests.
- `tests/test_led_brightness_ownership.py`, `tests/test_mqtt_resync.py` and
  `tests/test_mqtt_liveness_contract.py` cover the ownership, resync and liveness
  rules above. See `mqtt_freshness_ownership.md` for the liveness clocks.
- `tests/test_measurement_freshness.py` routes actual MQTT callbacks and verifies
  stale, invalid, partial, unchanged and zero measurements.
- `mqtt_smoke/test_transport.py` uses real `aiomqtt` and a local Mosquitto broker
  in a separate environment; see the contribution guide for the command.

## Evidence exposed to external control automations

Connector power exposes `measurement_received` from its own accepted power
field, including repeated zero readings. EVCC exposes `measurement_received`
only for a complete control packet, together with `control_observation` from
that same packet. A partial packet can change the merged current state while
leaving that observation and its clock unchanged. Consumers must compare the
observation with the current fields before using it as command confirmation.
These receive clocks and observations are excluded from Recorder attributes.

Session energy exposes `session_queried_at`, `session_started_at` and
`session_ended_at`. Query success belongs to each connector whose station-scoped
request succeeded, even when no sessions are returned. All three timestamps use
ISO 8601 UTC; session start/end are exposed only after a confirmed connector
match. Invalid response shapes are protocol errors and cannot advance query
success. Explicit `update_entity` refreshes recent sessions without issuing a
charger command, with a shared ten-second minimum between attempts, including
failed attempts. Concurrent callers await the same in-flight request; cancelling
one caller does not cancel that shared request. Scheduled lifecycle refreshes
retain their existing throttle and forced final-refresh behavior. Receive/query
clocks are runtime evidence and are not restored as proof of freshness after restart.

Live tracking requests use the physical Connect serial discovered for each
service location, even when MQTT credentials are shared. Missing or conflicting
mappings are skipped rather than requesting another location's gateway. A parent
relationship or one known serial does not prove that two locations share a
Connect. Alternate observations for the same location ID may resolve missing
metadata; explicit conflicts remain unresolved and produce a warning. Missing
metadata is logged at debug after resolution.

Cached bootstrap data persists the mapping. Older snapshots are upgraded in
memory without requiring Dashboard access, retaining their entities and MQTT
routes. Only the old site gateway's ownership can be recovered reliably; inherited
station/client serials are not used as evidence for a different control location.
Unknown child gateways remain untracked until online discovery establishes their
identity. Malformed current metadata is still rejected rather than migrated.

## Checking that a protection is still needed

`tests/reconciliation_mutations.py` is an opt-in pytest plugin that disables one
protection in memory, without editing a source file. A protection is still
needed when its witness test fails under the mutation:

```text
python -m pytest -p tests.reconciliation_mutations --reconciliation-mutant=NAME tests/TEST_NODE -q
```

| Mutation | Protection disabled | Witness test |
| --- | --- | --- |
| `rest_diff` | Poll keeps changes made while it was in flight | `test_reconciliation_audit.py::test_restoration_during_rest_poll_needs_snapshot_diff_without_confirmation` |
| `rest_confirmations` | Poll keeps repeated command values | `test_state_concurrency.py::test_stale_rest_current_limit_preserves_confirmation` |
| `rest_dashboard_overlay` | Poll applies its own Dashboard enrichment | `test_state_concurrency.py::test_rest_refresh_preserves_mqtt_sessions_and_new_commands` |
| `dashboard_diff` | Dashboard refresh keeps changes made while it was in flight | `test_state_reconciliation_contract.py::test_poll_that_started_later_wins_over_an_older_dashboard_refresh` |
| `dashboard_confirmations` | Dashboard refresh keeps repeated command values | `test_state_concurrency.py::test_forced_dashboard_refresh_preserves_in_flight_commands` |
| `staging_copy` | Remote answers are staged on a copy | `test_reconciliation_audit.py::test_cancelled_dashboard_staging_never_leaks_into_live_state` |
| `mqtt_precedence` | Live MQTT session state over a poll | `test_session_transitions.py::test_rest_cannot_contradict_live_mqtt_charger_state` |
| `reconnect_followup` | Follow-up read after an edge during a poll | `test_mqtt_resync.py::test_reconnect_during_older_poll_coalesces_one_fresh_followup` |
| `session_current_snapshot` | Session refresh writes to the newest snapshot | `test_reconciliation_audit.py::test_session_query_updates_the_snapshot_a_real_poll_replaced` |
| `local_publication` | Local changes do not report REST recovery | `test_state_reconciliation_contract.py::test_local_publication_does_not_report_a_failed_poll_as_recovered` |
| `rest_station_fields`, `rest_connector_fields` | Poll merges into current state | `test_coordinator.py::TestSmappeeCoordinator::test_update_data_preserves_mqtt_only_state` |
| `unchanged_mqtt_confirmation` | Repeated MQTT values count as confirmation | `test_state_concurrency.py::test_stale_rest_current_limit_preserves_confirmation` |
| `rest_led_owner` | Poll leaves a Dashboard-owned LED alone | `test_led_brightness_ownership.py::test_poll_does_not_write_a_led_the_dashboard_owns` |
