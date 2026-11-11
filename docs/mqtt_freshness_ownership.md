# MQTT freshness and liveness ownership

Several timestamps and flags have similar names but different meanings. They are
not interchangeable; this table records what each one means and who owns it.

## Inventory

| State | Meaning and writes | Owner and consumers | Safe to derive or remove? |
| --- | --- | --- | --- |
| `mqtt_transport_connected` | True only when every configured MQTT client is connected. Initially false; copied by `_sync_freshness()` at setup, connection callbacks and heartbeats. | `MqttFreshnessState.clients_connected` owns the aggregate; site/station attributes expose it to entities, station REST poll selection, session MQTT precedence, and public-status polling. | Keep the coordinator compatibility attributes. Published `mqtt_connected` cannot replace it. |
| `site.mqtt_connected` / `station.mqtt_connected` | Published, tri-state connectivity hint. Connection callbacks set it, but any delivered payload can also set it true, even without a valid measurement. | State dataclasses, updated by coordinator MQTT apply methods. Connectivity binary sensor, diagnostics (`mqtt_connected`, `mqtt_connected_any`), connector entity availability. REST merges preserve it; cached bootstrap seeds false. | Keep. It can differ from transport connectivity when only some clients are connected, or when payload delivery precedes a connection callback. |
| `last_mqtt_rx` | Epoch seconds for any payload delivered to the coordinator, including malformed or unhandled data. Site connection callbacks also update it on both connect and disconnect; station connection callbacks do not. Real heartbeats are intercepted before coordinator application, whereas direct `apply_mqtt_properties()` calls still update this field. | Published site/station state; written by MQTT apply methods and preserved by REST merging. Station diagnostics expose it. | Keep its existing meaning and diagnostic format. It is neither a valid-telemetry clock nor the heartbeat clock. |
| `last_heartbeat_rx` | UTC timestamp of a heartbeat on any configured transport. Heartbeats do not validate a measurement or prove that all clients are connected. | Runtime `MqttFreshnessState`, distributed to site/station compatibility attributes by `_sync_freshness()`. No production coordinator consumer currently uses the timestamp for availability or REST deadlines. | Keep the existing exposed attributes and heartbeat propagation. Replacing them with shared coordinator references would add coupling for little benefit. |
| Coordinator `last_real_power_rx` | Latest routed delivery accepting a real power field: grid, PV, consumption, always-on, storage total, or connector total. A station can accept both its own grid/PV fields and connector fields. | Each target coordinator, written by `_on_props()` after successful application. Monitoring-only generic site availability and the legacy site-power entity fallback consume it. | Keep. It is target-specific, includes several power sources, and is sampled after application. Deriving it from field timestamps would also change direct-application behavior. |
| Coordinator `last_valid_charger_telemetry_rx` | Latest delivery accepting **any field on a station coordinator**, including current/energy-only connector data and station grid/PV fields. The name does not mean only charging-status packets. | Station coordinator, written by `_on_props()`. `_smartdevice_poll_interval()` uses it with transport connectivity and REST reachability to select the existing 30-minute or 5-minute interval. A site also initializes the attribute for compatibility, but it is not populated there. | Keep. Charger-state freshness is narrower; power freshness excludes valid current/energy/status-only updates. |
| `last_connector_rx` | Latest routed delivery accepting any field for a particular connector. Other connectors and station/site-only fields do not advance it. | Station coordinator dictionary; `_on_props()` writes nonempty connector results. Monitoring-only connector entity fallback uses it when no field-specific freshness is available. | Keep. Using the station-wide clock would let one connector keep another alive. Inferring it from field clocks changes direct-call and timestamp semantics. |
| `MeasurementFreshness` | UTC receive times indexed by `(connector UUID or None, field)`, recorded only for accepted fields. Unchanged valid values and explicit zero renew freshness; invalid/missing fields do not. | Each coordinator owns its own instance. `apply_mqtt_properties()` calls `record()` before notifying listeners. Entity measurement availability, per-connector session precedence, public-status liveness, and sensor receive-time attributes consume it. | Keep as the owner of field freshness. It is deliberately independent of transport and broad liveness clocks. |
| `MqttFreshnessState` | Aggregate client connection state and shared heartbeat time for one `_setup_mqtt()` closure. | `_connection_callback()` and heartbeat interception write it. Connection-edge handling and compatibility synchronization read it. | Retain this existing abstraction, restricted to transport and heartbeat ownership. No new manager or cross-coordinator reference is needed. |

## Related state that must remain separate

- `_mqtt_connection_up` remembers actual connection callbacks, not the published
  connectivity hint. Reconnect resynchronization depends on this distinction.
- `MqttRoutingDiagnostics.last_message_rx`, `last_routed_rx`, and
  `last_unrouted_rx` describe raw routing activity, not accepted telemetry. Their
  counters, timestamps and exported diagnostic fields remain unchanged.
- Storage tracks complete power groups **per topic** in
  `last_power_rx_by_topic`. All battery topics must be fresh. A recent grid
  measurement or a different battery cannot replace that evidence.
- Cached bootstrap restores topology/mappings, not freshness. HA-restored energy
  totals can supply a value while the entity remains unavailable until the
  required live measurement arrives.
- Local freshness timers and cached-runtime recovery notifications notify
  listeners without fetching REST or reporting coordinator recovery. Shutdown
  cancels those timers and prevents new reconnect work.

## Boundary and ordering differences

Field freshness accepts ages from zero through the inclusive timeout. Station
REST selection and public-status liveness use a strict upper timeout; legacy
entity fallbacks have their existing age checks. Those predicates are not
consolidated. Heartbeats never extend any of these telemetry clocks.

Field clocks are sampled inside coordinator application, before listeners run.
Route-local liveness clocks are sampled afterward. Moving all recording into
one place would change what listeners observe, direct coordinator application,
and the small timestamp differences between the categories.

Normal charging-state entities retain their REST fallback. Monitoring-only
charging-state entities require a fresh `charger_state` field and connected
transport. A station may have fresh power and stale charger state: power must
not preserve MQTT session-state precedence in that case.

## Tests

Behavioral contracts live in `tests/test_mqtt_liveness_contract.py`, alongside
the existing measurement-freshness, status, storage, bootstrap, resync, power,
session-transition and reconciliation tests.
