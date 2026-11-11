# Targeted Dashboard/REST LED ordering

Baseline: `77dcb5b`. Scope: the older forced Dashboard refresh overwriting an
unchanged LED observation from a newer successful main REST read.

## Overlapping fields and policy

Both paths use the Dashboard API client, but call different endpoints:
`v10/chargingstations/{serial}?includeDetails=true` versus
`v10/servicelocation/{id}/homecontrol/smart/devices`. Reconciliation uses no
server revision or observation time from either response. The code therefore
cannot establish which cloud representation is intrinsically newer. Eventual
consistency must remain possible; local request order is not proof of freshness.

| Field | Actual read sources | Authority and consistency | Policy in this change |
| --- | --- | --- | --- |
| Station `led_brightness` | LED configuration in station-details modules; LED configuration in the smartdevice list | Neither endpoint has a proven revision/authority advantage. Existing changed-value contracts already preserve a later REST publication against an older Dashboard response. | Extend that **overlap-only** protection to unchanged successful REST observations. A missing/unavailable/failed/cancelled read is not an observation barrier. Preserve subsequent MQTT/command writes. |
| Connector `min_current`, `max_current` | Station-details connector module configuration; smartdevice configuration | Both report bounds; the pair is validated together and can be partially absent. Independent per-field ordering could mix incompatible ranges. | Keep existing validation, snapshot replay and confirmations. No new source preference or unchanged-read ordering rule. |
| Connector `min_surpluspct`, `support_grid` | Station-details connector module configuration; smartdevice configuration | Neither endpoint is proven authoritative or immediately consistent with the other. | Preserve existing changed-value/confirmation policy. Generalizing the LED fix to these fields is not justified by the demonstrated LED contract. |
| Connector `selected_mode` | REST merge accepts it, but the production smartdevice reader currently supplies None; Dashboard derives/fills missing base modes from strategy/raw mode | Not two independent authoritative observations in the current production reader. | Unchanged. |
| Connector `selected_current_limit`, `paused`, EVCC/base-mode derivatives | Derived from other fields, not equivalent independent observations at both endpoints | Depend on MQTT context, percentage, validated bounds and fallback rules. | Unchanged; do not assign new ordering metadata to derived fields. |
| Other station/connector fields | REST reachability and charging state; Dashboard-only protection/offline/metadata; MQTT-only telemetry/status; separate `available` / `dashboard_available` fields | Distinct ownership/source policies, including existing fresh-MQTT session precedence | Unchanged. |

The selected LED policy is precise: **an in-flight forced Dashboard operation
may not overwrite the current LED value after a later-started main REST poll
successfully observes LED brightness and completes its merge**. Zero is valid.
This adds no preference to non-overlapping reads. It does not turn a failed
overall read, a local timer tick, a missing LED value, or station API failure
into evidence against Dashboard. A connector's independent API failure does
not itself invalidate a successfully observed station LED value.

This is not a global last-started-read-wins rule. Existing snapshot differences
still preserve concurrently changed live values, regardless of which remote
operation produced them; confirmations still protect repeated MQTT/command
writes. An older main poll that completes while a newer Dashboard operation is
pending retains that existing changed-value protection. Only the demonstrated
older-Dashboard/newer-successful-REST LED hole is corrected.

## Options considered

| Option | Assessment |
| --- | --- |
| Make one endpoint permanently own all overlapping fields | Would change successful later Dashboard updates, missing-value fallbacks, or connector range semantics. No cloud authority guarantee supports it. |
| Use only existing value diffs | Cannot detect the unchanged observation; the converted regression fails with brightness 50 instead of 10 on the baseline. |
| Record every REST observation as a `StateConfirmation` | Changes the meaning/versioning of live confirmations. Recording at completion can suppress a Dashboard read that started after an older REST poll. Not used. |
| Serialize whole Dashboard and main REST reads | Introduces blocking/latency and changes allowed concurrency, cancellation, and existing completion-order contracts. Not needed for one field. |
| Global generations or per-field timestamps | Track far more history than required and need a success/failed-read policy. Reusing only poll-attempt time and snapshot identity is also insufficient when an older poll completes and a newer one fails. |
| Operation-local overlap guards | Selected. Use coordinator-owned, transient entries only for active forced Dashboard operations. No values, generations, timestamps, persistent field locks, extra coordinator, or general state store. |

## Implementation and lifecycle

Each forced Dashboard operation registers its own token and initially empty
set of REST-observed fields. A main poll captures only the entries already
present when it starts. After all its I/O and merge steps succeed, a valid LED
observation marks those older operations. The successful result then returns
to normal coordinator publication without an intervening await.

When a marked Dashboard operation resumes, its staged LED is reset to its
baseline solely to omit that older field from the existing Dashboard diff.
Other staged fields, live snapshot differences and `StateConfirmations` are
processed exactly as before. No confirmation is fabricated for a remote read.
The current LED value is retained, so even unchanged MQTT/command confirmations
after either read remain effective.

Tokens are removed in `finally`, including exceptions, cancellation, shutdown,
missing data and early exits. Identity-keyed tokens keep concurrent operations
independent; a cancelled operation cannot remove another operation's guard.
An in-flight main poll may retain references to removed entries until it
finishes; updating such an entry has no effect on published state or new reads.

## Validation and assumptions

The original audit characterization is now
`test_unchanged_newer_rest_led_read_blocks_an_older_dashboard_value`, expecting
brightness 10. `test_dashboard_read_ordering.py` additionally covers changed and
zero REST values; failed, missing, unavailable and cancelled observations;
unchanged and changed MQTT/real light commands before/after REST completion;
newer Dashboard operations; multiple overlapping operations; a failed second
poll; local ticks; subsequent non-overlapping reads; and cancellation/shutdown.

The old `dashboard_diff` mutation witness used LED brightness. The new LED
barrier deliberately also covers that case, so the witness now survives when
only snapshot replay is disabled. A connector-field witness demonstrates that
Dashboard snapshot replay remains essential outside LED. All other existing
mutation witnesses retain their intended protection.

Live validation remains needed to assess the endpoints' consistency window and
confirm that the existing adapter associates both reads with the same station
LED. This correction preserves local ordering; it cannot prove which numerical
cloud value is freshest. Endpoint parsing, range handling, source precedence,
poll/resync intervals and entity/publication behavior are not changed.

## Results

Validated with Python 3.14.6 and Home Assistant 2026.10.4:

- Desired-behavior regression reproduced brightness 50 instead of 10 before
  the fix, then passed after the fix.
- 27 new ordering cases; related regression selection: 464 passed.
- Complete suite: 1,545 passed, 3 Windows-specific socket-guard skips.
- Coverage: 96.79%, passing the existing 95% gate.
- Ruff lint and format checks passed; mypy passed for 49 source files.
- Original mutations: 12 witnesses killed, with the old LED `dashboard_diff`
  witness independently covered by the new barrier. The connector replacement
  witness kills `dashboard_diff`; the new `rest_led_barrier` mutation is also
  killed (brightness 50 instead of 10). No mutation changes persist on disk.
- Production diff: `coordinator.py` +12/-0; `dashboard_merge.py` +8/-0.

Reproduce the two updated mutation checks with the existing opt-in plugin:

```text
python -m pytest -p tests.reconciliation_mutations --reconciliation-mutant=dashboard_diff tests/test_dashboard_read_ordering.py::test_newer_rest_connector_changes_still_need_dashboard_snapshot_replay -q
python -m pytest -p tests.reconciliation_mutations --reconciliation-mutant=rest_led_barrier tests/test_reconciliation_audit.py::test_unchanged_newer_rest_led_read_blocks_an_older_dashboard_value -q
```

Both mutant commands should fail their otherwise passing assertion. Nothing
was committed or pushed, and no live charger/broker test was performed.
