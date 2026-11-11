# State reconciliation audit

Baseline: `762f519`, with a clean working tree. Decision: **keep production
reconciliation unchanged**. No protection below was demonstrated redundant.
This is not a claim of general last-started-read ordering: a reproduced limit
of the existing value-diff approach is documented below.

## Writer inventory

`S` means `StationState`, `C` means one `ConnectorState`, and `I` means
`IntegrationData`. Constructors also initialize all omitted dataclass fields to
their defaults. The inventory includes synchronous helper writers as well as
their asynchronous entry points; it excludes entity-local restored values,
client metadata, routing diagnostics, and freshness clocks outside these three
dataclasses (see `mqtt_freshness_ownership.md` for those).

Protection abbreviations:

- **Live merge**: REST replaces selected fields on the latest published objects,
  rather than replaying a snapshot retained across network awaits.
- **Poll diff**: `StateChanges.between(refresh_start, current)` overlays all
  station/connector field differences accumulated since this poll started.
- **Confirmation**: `StateConfirmations.since(version, current)` replays fields
  confirmed after I/O began, including repeated values and explicit clearing.
- **Dashboard replay**: a forced Dashboard refresh stages its response, captures
  live differences and confirmations, then applies staged changes followed by
  those two live overlays, synchronously.
- **Session precedence**: connected transport plus fresh per-connector
  `charger_state` prevents REST from replacing live MQTT session state.

| Writer / entry point | Fields written | Object ownership | Confirmation | Can overlap remote I/O? | Protection and publication |
| --- | --- | --- | --- | --- | --- |
| `StationApiMixin._fetch_station_state` | S constructor defaults; explicitly `led_brightness`, `available`, `api_available` | Isolated REST result; no live mutation | No | Yes, while MQTT/commands/Dashboard/session work runs | Nothing published here. Main poll merges after all awaits. |
| `_fetch_connector_state` | C defaults; explicitly connector number, session state, current/percentage/mode selection, current bounds, surplus, grid support, API availability; separate reported bounds on `ConnectorRestSnapshot` | Isolated result | No | Yes | Main poll consumes the result; no listener notification here. Missing bounds retain their meaning until validation/merge. |
| `_merge_station_rest_state` | Replaces `led_brightness` when supplied and `api_available`; retains the current station's other fields and LED device ID | New S based on **current** S | No | Runs synchronously after I/O | Live merge, then Dashboard overlay, poll diff and confirmations. Returned only; main coordinator publishes. |
| `_merge_connector_rest_state` | Replaces connector number, selected current/percentage/mode, validated bounds, surplus, grid support, API availability, session state subject to source policy; recomputes `paused` | New C based on **current** C; startup can reuse/repair isolated REST state | No | Final merge has no await | Live merge + session precedence + poll diff + confirmations. Retains MQTT-only status, cause, telemetry and metadata. |
| `SmappeeStationCoordinator._async_fetch_data` | New I/S/C snapshot and connector dictionary; failed connector results preserve current fields with `api_available=False`; I sessions come from the latest current I | New replacement, never an in-place remote response | Reads confirmations, does not record a remote read as a confirmation | Yes during awaits; final merge is synchronous | Orders REST merge → changed Dashboard fields → poll diff → confirmations → metadata/session transitions. Returns data to HA's normal successful-poll publication. |
| `StateChanges.apply` | Exactly the supplied S and C fields; only existing connector keys; **not** I sessions or connector collection membership | Mutates the supplied target, either staging/output or live Dashboard target | No | No await inside apply; caller may resume after remote I/O | Low-level overlay. Notification belongs to the poll or Dashboard caller. |
| Dashboard station detail/LED/capacity/overload helpers | S `dashboard_available`, features, maximum capacity, cable lock, station details payload, offline enabled/failsafe, LED ID/brightness, capacity active/maximum power, overload active/maximum load | Normal production calls use isolated `staged` I | Only if helpers are explicitly passed live objects; normal staged identities do not record | Yes, enrichment awaits station details and load management | Changed-field Dashboard overlay within a poll, or Dashboard replay for a forced refresh. Helpers do not publish independently. |
| Dashboard connector module helpers | C Dashboard ID/UUID/name, validated bounds, surplus and grid support; fallback connection/IEC/status/cause/stopped-by-cloud, raw mode/strategy, selected/UI mode, EVCC state/code | Isolated staging in production | No for staging; identity check protects against treating remote staging as a live confirmation | Yes | Same Dashboard overlay/replay. Fallback fields only fill absent/default values; they are not authoritative overwrites. |
| Dashboard load-management enrichment | C optimization strategy, selected/UI mode | Isolated staging in production | No for staging | Yes; additional awaited requests occur after other enrichment | Same changed-field overlay/replay. |
| `_async_dashboard_refresh_now` | Applies only differences between its baseline and staging, then restores live changes and confirmations | Mutates the **current** published I/S/C after the await | Reads, does not create confirmations for the cloud read | Yes with main REST, commands, MQTT, sessions | Dashboard replay. Notifies only via `async_update_listeners()` when changed, then forces a serialized smartdevice refresh without claiming REST recovery. |
| MQTT `apply_mqtt_connection_change` / `apply_mqtt_properties` | S `mqtt_connected`; properties also update `last_mqtt_rx` | Current S in place | No for connectivity/timestamp | Yes | Live REST merge retains these fields; poll diff also catches changed values. Changed state uses listener notification, never successful-poll publication. |
| MQTT `devices/updated` | C validated bounds, surplus, grid support, connector number, percentage and derived Ampere limit when strategy permits | Current C in place | Yes through `_set_if_changed`, including unchanged accepted values | Yes | Poll diff + confirmation; Dashboard replay protects overlapping configuration fields. Outer MQTT path publishes via listeners. |
| MQTT controller `/state` | C `connection_status`, `configuration_errors` | Current C in place; direct assignment | **No** | Yes | Live merge retains these MQTT-only fields; poll diff and Dashboard replay cover value changes. Outer MQTT path notifies on changes. Repeating these fields is not a new confirmation. |
| MQTT `chargingstate` primary/context/limits/availability | C session state, cause, current status, stopped-by-cloud, IEC status, percentage/current selection, connector availability; S availability | Current S/C in place | Yes for accepted non-None setters; repeated values are recorded | Yes | Poll diff + confirmations; session precedence additionally protects fresh state observed **before** poll start. Outer MQTT path updates freshness then notifies. |
| MQTT mode and derived-state helpers | C raw mode/optimization strategy (including explicit None), selected/UI mode, `paused`, EVCC state/code | Current C in place (also reused on Dashboard staging) | Modes/base selection explicitly record live identities, even repeated/cleared values. Setters confirm other accepted fields; a purely derived pause update can use `confirm=False` | Yes | Changed-value diffs cover derived updates; confirmations cover accepted repeated values. Staging helpers record none. Outer writer publishes. |
| MQTT session-end `_clear_connector_power` | C `power_total=0`, `power_phases=None`, `current_phases=None` where needed | Current C for MQTT; new merged C for REST transitions | **No** | MQTT transitions can occur during either cloud read | Latest-state REST merge retains telemetry, poll/Dashboard diffs preserve changed fields, and fresh MQTT session precedence prevents stale session reversal. Outer path publishes. |
| MQTT station properties / LED updated | S availability and LED brightness | Current S in place | Yes, accepted setters also record repeats | Yes | Poll diff + confirmations + Dashboard replay. MQTT outer path publishes. |
| Power processing (`PowerMixin`, shared pure calculations) | S grid/PV phases/totals/current/voltage/energy, household consumption and always-on; C power phases/total, current phases, energy import | Current S/C through the coordinator's setter | Yes through live `_set_if_changed` | Yes | Live merge retains telemetry, plus poll diff/confirmations and Dashboard replay. Pure calculations do not write state. MQTT outer path publishes/freshens accepted fields. Site-only storage writes `SiteState`, outside this table. |
| `update_station` / `update_connector` | Exactly the caller's field mapping | Re-reads current I synchronously after the remote command completes, then mutates current S/C | **Yes**, every supplied field, even if unchanged or None | Yes | Poll diff + confirmation + Dashboard replay. Uses `async_update_listeners()`; does not clear REST failure or reset poll scheduling. Missing current connector/data is a no-op. |
| Number commands | C current/percentage selection, maximum current plus clamped current selection, surplus; S capacity maximum power, overload maximum load, offline enabled/failsafe | Via update helpers **after** successful remote await | Yes | Yes | Update-helper protections/publication. Failed/cancelled uncertain writes only schedule reconciliation; auth errors propagate without success publication. |
| Select, light and switch commands | C selected mode; S LED brightness, availability, offline enabled/failsafe. Start switch sets selected mode to `STANDARD`; pause action does not directly change these dataclasses | Via update helpers after successful await | Yes | Yes | Same helper protections. Entity-local `_is_on`/restored caches are separate. |
| Cable lock `_set_locked` | S cable lock on changed success; unchanged success records the same field without rewriting/notifying | Re-reads current I after await | **Yes in both cases** | Yes | Confirmations are essential for unchanged success. Changed path uses update helper; both paths schedule Dashboard reconciliation. Errors/cancellation do not confirm success. |
| Number/select HA restoration | C selected Ampere/percentage limit only if both absent; surplus only if absent; selected mode only if absent and valid | Looks up current C **after** restore awaits, mutates in place | **No** | Yes with a later poll or Dashboard operation | Poll diff and Dashboard replay preserve these value changes. Notifies listeners; does not claim REST recovery. Restoration is not evidence of a successful cloud write. |
| Recent-session `_async_run_session_refresh` | I `recent_sessions` | Re-reads `self.data` after query completion and mutates that current I | **No**; sessions are outside S/C confirmation/diff fields | Yes with connector snapshot replacement | Main poll reads latest `current.recent_sessions` after its awaits. Session writer targets the newest I, preserving connector identity. Listener-only publication; query lock/task sharing and cancellation shielding remain. |
| `_apply_rest_session_transitions` | Clears power/current on the newly merged C when an authoritative REST transition ends a session; session scheduling side effects outside I | New unpublished merged snapshot | No | Called synchronously after all overlays | Runs on final merged state, skips fresh MQTT session ownership, then main poll publication. |
| Cached bootstrap `build_cached_runtime` | New I/S/C defaults, connector numbers, API unavailable, MQTT disconnected | New coordinator instances; initial seed via `async_set_updated_data()` | No | Not a competing writer to an already published coordinator | Initialization only, before runtime/topology activation; does not replay live measurements. Normal recovery prepares separate runtime objects and swaps them atomically. |
| Runtime initial/main refresh | Publishes the returned whole I | HA coordinator replaces `data` after a successful update | No | Main requests serialized by HA coordinator lock; Dashboard/session/MQTT can overlap while network awaits | Uses the existing successful-poll status and scheduling behavior. Unload cancels delayed/owned Dashboard/session tasks and suppresses new forced resync work. |

Buttons and services perform remote actions and schedule Dashboard reconciliation;
they do not optimistically write I/S/C. `_sync_dashboard_client_metadata` writes
command clients, not state. `mqtt_setup` writes transport/freshness clocks and
delegates state changes to the MQTT apply methods above. Site coordinator
writers affect `SiteData`/`SiteState`, not a station's I/S/C. Topology and runtime
assembly create runtime buckets and handles, not additional live state writers.

## Why the protections are not substitutes

1. **Field-source merging** retains MQTT-only state observed before a poll
   starts. It cannot distinguish new commands to REST-owned fields.
2. **Poll diffs** retain unconfirmed writers (restore, Dashboard publication,
   derived MQTT changes) and do not require every existing writer to record a
   confirmation. They also see changed live state while Dashboard I/O runs.
3. **Confirmations** retain post-start writes that repeat their original value,
   and ABA changes that return to the baseline value. They are per connector
   and per field, and expire logically when the next read captures a newer
   version; they are not persistent value locks.
4. **Dashboard replay** needs both overlays for the same reasons. They must be
   captured before applying remote changes to current state, then replayed after
   those remote changes. Recomputing them after mutation loses the original
   live values.
5. **Independent staging copies** prevent remote enrichment from mutating the
   published snapshot before the request finishes or if it is cancelled.
6. **MQTT session precedence** covers fresh state received before poll start,
   which neither a post-start diff nor post-start confirmation would preserve
   against an authoritative-looking but lagging REST session value.
7. **Reconnect resync** is a request-freshness guarantee, not a merge overlay.
   A connection edge during a running poll must keep the force flag pending for
   a fresh serialized follow-up. The flag is consumed at poll start, and the
   task loop coalesces later edges without parallel main polls.
8. **Current snapshot lookup and listener-only publication** protect object
   identity and REST availability/scheduling respectively. Neither can be
   replaced by a field-level conflict detector.

Some paths are covered by more than one protection, but no whole mechanism is
redundant across the full writer inventory. Removing one would require changing
other writers or expanding another mechanism's responsibilities. That would
increase coupling and goes beyond a demonstrated safe simplification.

## Characterization coverage

The existing reconciliation/concurrency, session-transition and resync tests
already exercise MQTT during REST, unchanged commands, both Dashboard/REST
completion orders, Dashboard/command overlap, reconnect during an older poll,
session refresh and snapshot replacement, stale REST after confirmations, and
failed poll publication status. `test_reconciliation_audit.py` adds seven cases:

- Actual unconfirmed HA restoration while a REST connector read is pending.
- Independent fields/confirmations on two connectors, followed by a newer
  authoritative REST read.
- Cancelled and shutdown-cancelled owned Dashboard tasks with isolated staging.
- REST cancellation during Dashboard enrichment, preserving newer MQTT data.
- Session-query completion after a real coordinator poll replaces connectors.
- The unchanged-read ordering limitation described below.

## Mutation results

All 13 mutations were **killed by assertion failures** in otherwise passing
witnesses. No production source file was edited for mutation testing. The opt-in
plugin replaces function code only in a pytest process and restores it with
`monkeypatch`, including references imported before fixture setup. Normal suite
runs do not load the plugin. These are targeted witnesses, not an exhaustive
mutation score or proof of all possible interleavings.

Reproduce a row using the existing test environment:

```text
python -m pytest -p tests.reconciliation_mutations --reconciliation-mutant=NAME TEST_NODE -q -p no:cacheprovider --tb=short
```

The expected mutant exit status is 1, with the listed assertion failure; the
unmutated witness exits 0. Test node paths below are relative to `tests/`.

| Name | Test node | Observed failure |
| --- | --- | --- |
| `rest_diff` | `test_reconciliation_audit.py::test_restoration_during_rest_poll_needs_snapshot_diff_without_confirmation` | Restored surplus 40 overwritten by REST 20. |
| `rest_confirmations` | `test_state_concurrency.py::test_stale_rest_current_limit_preserves_confirmation[command-20]` | Repeated 20 A command overwritten by stale 8 A. |
| `rest_dashboard_overlay` | `test_state_concurrency.py::test_rest_refresh_preserves_mqtt_sessions_and_new_commands` | Dashboard capacity 4.0 lost. |
| `dashboard_diff` | `test_state_reconciliation_contract.py::test_poll_that_started_later_wins_over_an_older_dashboard_refresh` | Older Dashboard brightness 50 overwrites published REST 75. |
| `dashboard_confirmations` | `test_state_concurrency.py::test_forced_dashboard_refresh_preserves_in_flight_commands[4.0]` | Unchanged 4.0 command overwritten by Dashboard 3.0. |
| `staging_copy` | `test_reconciliation_audit.py::test_cancelled_dashboard_staging_never_leaks_into_live_state[cancel]` | Unpublished brightness 99 leaks into current brightness 10. |
| `mqtt_precedence` | `test_session_transitions.py::test_rest_cannot_contradict_live_mqtt_charger_state` | Live `Started` replaced by lagging `Stopped`. |
| `reconnect_followup` | `test_mqtt_resync.py::test_reconnect_during_older_poll_coalesces_one_fresh_followup[disconnect]` | One REST request instead of the required two. |
| `session_current_snapshot` | `test_reconciliation_audit.py::test_session_query_updates_the_snapshot_a_real_poll_replaced` | Published snapshot loses newly fetched sessions. |
| `local_publication` | `test_state_reconciliation_contract.py::test_local_publication_does_not_report_a_failed_poll_as_recovered[command]` | Failed REST status incorrectly becomes successful. |
| `rest_station_fields` | `test_coordinator.py::TestSmappeeCoordinator::test_update_data_preserves_mqtt_only_state` | Published MQTT connectivity lost. |
| `rest_connector_fields` | Same node as above | Published IEC status lost. |
| `unchanged_mqtt_confirmation` | `test_state_concurrency.py::test_stale_rest_current_limit_preserves_confirmation[mqtt-20]` | Repeated MQTT 20 A overwritten by stale 8 A. |

## Reproduced limitation and remaining risks

Follow-up: the LED example below is corrected by the targeted change described
in `dashboard_rest_read_ordering.md`. Its test is now the desired-behavior
regression `test_unchanged_newer_rest_led_read_blocks_an_older_dashboard_value`.
This section retains the original audit finding at baseline `762f519`.

**A later cloud read that repeats the baseline is not an ordering barrier.**
The original `test_unchanged_later_poll_is_not_a_general_read_ordering_barrier` started a
Dashboard refresh at brightness 10 and holds its response of 50. A later REST
poll publishes 10 again. That read creates neither a value diff nor a live
confirmation. When the older Dashboard response finishes it publishes 50.
The audit's later-read-wins tests proved the changed-value cases, not general
last-started-read ordering. The new test records this observed limitation; it
does not label the result as the desired future policy.

Fixing that case would require an explicit remote-read ordering policy or a
different rule for remote observations. It is not evidence that either current
overlay can be removed. No production fix is attempted in this simplification
audit, and the finding should remain visible rather than being hidden by an
xfail or a weakened existing test.

Other boundaries remain: a poll started after a command may reconcile back to
the cloud's value (eventual consistency is an API assumption); state diffs do
not merge connector additions/removals or I sessions; topology changes use new
runtime objects. Shutdown cancels owned Dashboard/session tasks and blocks new
resync work, but is not a transaction-wide rollback of an already running main
poll. Deep copies assume the documented dataclass payloads remain copyable.
Final merge correctness also relies on no awaits between reading current state,
applying overlays and coordinator publication.

Dashboard enrichment also updates high-level mapping caches and command-client
identifiers before its final state publication. Staging isolates I/S/C; it is
not transactional rollback of these separate cache/client side effects on
cancellation. This distinction is another reason not to generalize the state
merge into a broader transaction abstraction in this task.

## Decision and validation

Production changes: **0 lines added, 0 removed**. Retain source-aware REST
merges, snapshot diffs, StateConfirmations, Dashboard staging/replay, MQTT
precedence, reconnect serialization, current-snapshot session updates and
listener-only local publication. Only tests, this audit and the opt-in mutation
plugin are added.

Validated on Python 3.14.6 / Home Assistant 2026.10.4:

| Check | Result |
| --- | --- |
| Original/new core concurrency baseline | 113 passed |
| Broader concurrency, resync, Dashboard, session, power and freshness run | 437 passed |
| Complete unmutated pytest suite | 1,518 passed; 3 Windows-specific socket-guard skips |
| Existing 95% coverage gate | Passed: 96.79% |
| Ruff lint and repository-wide format check | Passed |
| Configured mypy check | No issues in 49 source files |
| Targeted in-memory mutations | 13/13 killed by the documented assertions |
| Production diff against baseline | Empty; 0 lines added/removed |

Nothing was committed or pushed. No live charger/broker validation was performed.
