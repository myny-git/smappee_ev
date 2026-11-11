# SMART charging-state validation

Date: 2026-11-11. Baseline: `e6f99cc`, validated with the uncommitted SMART fix.

## Change and regression evidence

The production change adds `smart` to `SmappeeChargingStateSensor.options` and
`smart: Smart` to `strings.json`, `translations/en.json` and
`translations/nl.json`. These are the repository's existing translation source
and bundled language files; there is no separate translation generator or Dutch
source file in this checkout. All previous enum values and `raw_status` remain.
MQTT parsing, session tracking, modes, IEC/EVCC handling, availability, REST
merging, freshness and polling intervals are unchanged.

Before the fix, the focused selection had 5 failures and 60 passes: both SMART
normalization cases returned None, both HA translation lookups lacked the key,
and the MQTT-driven HA entity published `unknown`. After the fix: 65 passed.

The five added cases cover uppercase/whitespace normalization, actual HA
translation loading in English and Dutch, and real config-entry setup/reload
with MQTT routing and entity publication. The lifecycle test uses the supplied
hardware payload, repeats it, changes to an unsupported state and Started, then
returns to SMART, both before and after reload. It verifies the original
`raw_status` and stable registry identity. Existing tests still cover all four
previous enum values, default initialization, and missing, empty, non-string or
unsupported values followed by recovery to a recognized state.

## Validation results

Environment: Windows, Python 3.14.6, Home Assistant 2026.10.4,
pytest-homeassistant-custom-component 0.13.375, Ruff 0.17.4, mypy 2.4.1.
This is the repository-pinned HA version, distinct from the user's hardware
validation on HA 2026.11.1 and a Smappee EV Wall Home.

| Check | Result |
| --- | --- |
| Full pytest suite | 1,550 passed, 3 skipped, 80.14 seconds |
| Coverage gate (`--cov-fail-under=95`) | Passed: 96.79%, 8,174 statements, 262 missed |
| `ruff check .` | Passed |
| `ruff format . --check` | Passed: 140 files already formatted |
| `mypy --config-file=mypy.ini custom_components/smappee_ev` | Passed: 49 source files |
| Existing reconciliation mutations | All 14 killed by the intended assertions; each mutant exited 1 |
| `git diff --check` | Passed |

Pytest used `.local-review/pr330/.venv`; mypy used the existing sibling
`.venv-mqtt` environment containing real aiomqtt 2.5.1, matching CI's separate
dependency setup. The ordinary pytest environment has no aiomqtt installation;
running mypy there initially reported that missing dependency. No type errors
remain in the correctly provisioned environment.

On Windows, pytest was launched with empty `fcntl` and `resource` import shims
for HA's POSIX CLI imports, as in the existing local launcher. No POSIX operations
were emulated. The three skips are the Linux socket-guard cases. Ordinary tests
use the repository's aiomqtt stub; no new real-broker or hardware test was run.

The full-suite arguments were:

```text
-q -p no:cacheprovider --cov=custom_components.smappee_ev --cov-report=term-missing --cov-report=xml --cov-fail-under=95 --tb=short --show-capture=no
```

Mutation witnesses are documented in `../state_reconciliation_audit.md` and
`../dashboard_rest_read_ordering.md`. This run used the updated connector-field
`dashboard_diff` witness and the `rest_led_barrier` witness, plus the other twelve
existing witnesses. All failures were inspected as assertion failures rather
than setup/import errors. Mutations were confined to individual pytest
processes and did not edit production files.

## Release-validation review

| Area | Existing evidence and remaining limit |
| --- | --- |
| Disconnect/reconnect and forced REST resync | `test_mqtt_resync.py` covers immediate forced reads, reconnect during an older poll, coalesced follow-up, repeated connection callbacks and shutdown. `test_mqtt_reconnect_entities.py` exercises the transport loop, subscriptions, REST and actual HA entity recovery without reload. Adequate automated coverage; a controlled hardware outage remains unverified. |
| Availability during transport loss | Reconnect entity tests assert unavailable power/control states during loss and recovery afterward. Liveness tests cover measurement expiry, heartbeats, partial packets and multiple transports. This exceeds the successful reload/reconnection observation, which did not establish outage-state behavior. |
| REST failures with MQTT connected | `test_running_coordinator_keeps_mqtt_during_outage_and_recovers` exercises handled API failures: MQTT power remains usable while API controls are unavailable, then REST recovers. `test_reconnect_resync_and_notifications_cannot_hide_rest_failure` separately proves that a coordinator-level UpdateFailed is not hidden by MQTT/local publication; MQTT entities remain unavailable in that case. These are distinct existing behaviors. |
| Sessions across reload | `test_reload_during_active_session_keeps_one_loop` verifies tracking startup/idempotence on a coordinator already holding an active state. Sensor tests verify restored session energy/start time, including an actual HA restore-cache round trip. Full entry reload/registry stability is tested separately. These pieces do not constitute one complete active-session reload test with new coordinators, real session history and pending final-refresh deadlines. Unload cancels those deadlines; they are not persisted verbatim. Session continuity on active hardware remains a validation gap, not a reproduced regression. |
| Repeated MQTT status | Session-tracking tests prove repeated terminal messages neither postpone nor restart final refreshes, including interleaved connectors; concurrency tests cover unchanged MQTT confirmations against stale REST. The SMART lifecycle test also verifies repeated SMART publication. Adequate for this presentation change. |

The user confirmed startup, initial REST fetching, MQTT power, a charging-current
command, LED 21% from HA, LED 13% from the app reflected automatically in HA,
13% surviving a later REST refresh, and reload/reconnection without visible
Smappee errors. This confirms ordinary bidirectional LED synchronization and a
successful REST refresh. It does **not** reproduce the overlapping LED
Dashboard/REST ordering race. That protection has automated ordering and mutation
evidence; endpoint consistency under a real overlapping read remains unverified.

The SMART presentation fix is ready for review with all requested automated
gates passing. No additional production changes are justified by these findings.

## Single next hardware test

During an active charging session in SMART mode, temporarily interrupt only the
MQTT broker connection from HA, then restore it. Keep Dashboard/REST reachable.
Record HA states and debug logs through the interruption. Verify transport loss
changes availability as expected, reconnect restores subscriptions and triggers
a fresh REST configuration read, power/status recover without integration
reload, and session identity/energy do not reset or duplicate. Whenever the raw
charging state is SMART, verify the displayed label is Smart and `raw_status`
remains SMART. This directly exercises the remaining untested hardware failure
and recovery path; it does not establish the LED overlap race or active-session
reload continuity.
