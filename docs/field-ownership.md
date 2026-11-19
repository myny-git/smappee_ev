# Field ownership

Which source writes each published state field, and what happens when sources
disagree. This is an inventory of the code as it stands; it changes no behavior.
The rules that apply to every field are in [state-updates.md](state-updates.md),
the freshness clocks in [mqtt_freshness_ownership.md](mqtt_freshness_ownership.md).

## The general rule

There is no priority order between sources. A field holds the value of the
writer that changed it last, with one protection: a remote answer that was
already in flight when a newer value arrived does not overwrite that value
(`StateChanges` and `StateConfirmations` in `state_updates.py`).

A field needs its own rule only when two remote reads report it and cannot be
ordered. Those rules are marked **rule** below. Everything else is either
written by one source or follows the general rule.

Sources:

| Source | Where it writes | Typical delay |
| --- | --- | --- |
| MQTT | `coordinators/mqtt_apply.py`, `coordinators/power.py`, `coordinators/storage.py` | Seconds |
| Poll | Smart-device REST poll, `coordinators/api_state.py` | 5 or 30 minutes |
| Dashboard | Dashboard station details and load management, `coordinators/dashboard_merge.py` | 30 minutes, or shortly after a write |
| Command | `update_station` / `update_connector` after a confirmed write | Immediate |
| Restore | Entity restore in `number.py` and `select.py`, only into an empty field | Once at startup |
| Session query | `coordinators/session_tracking.py` | While a session is active |

## Site and station measurements

`SiteState` and the measurement fields of `StationState` carry the same names
and are written the same way.

| Field | Writers | Notes |
| --- | --- | --- |
| `grid_power_total`, `grid_power_phases` | MQTT | Complete configured group only. |
| `grid_current_phases`, `grid_voltage_phases` | MQTT | |
| `grid_energy_import_kwh`, `grid_energy_export_kwh` | MQTT | Sensors hold totals against glitches and confirm a meter reset. |
| `pv_power_phases`, `pv_current_phases`, `pv_energy_import_kwh` | MQTT | |
| `pv_power_total` | MQTT, twice per message | **Rule:** the scalar `solarPower` is applied after the phase sum and replaces it. |
| `house_consumption_power`, `always_on_power` | MQTT | Scalars `consumptionPower` and `alwaysOn`. |
| `storage_power_total`, `storage_charged_energy_kwh`, `storage_discharged_energy_kwh` | MQTT | Site only. Published once every battery topic has reported. Available only while every battery topic is fresh. |
| `mqtt_connected` | MQTT connection callbacks, any delivered payload | Cached bootstrap seeds `False`. |
| `last_mqtt_rx` | Any delivered payload | Diagnostic only. |

None of these has a REST fallback or a command. Availability follows the
per-field freshness clock.

## Station configuration

| Field | Writers | Notes |
| --- | --- | --- |
| `led_brightness` | MQTT, Dashboard, Command, Poll | **Rule:** once the Dashboard has identified the station's LED, the poll no longer writes it. |
| `dashboard_led_device_id` | Dashboard | Decides the LED rule above. |
| `available` | MQTT station properties, MQTT connector charging state, Command | `None` until one reports. Never written by a remote read. |
| `dashboard_available` | Dashboard | Shown by the availability switch only while `available` is `None`. |
| `api_available` | Poll | Cached bootstrap seeds `False`. |
| `cable_locked` | Dashboard, Command | Dashboard writes only when the key is present. |
| `offline_charging_enabled`, `offline_failsafe_current_a` | Dashboard, Command | The write carries both; a command reads both from Smappee first and changes only its own. |
| `capacity_maximum_power_kw`, `overload_maximum_load_a` | Dashboard, Command | |
| `capacity_protection_active`, `overload_protection_active` | Dashboard, Command | A limit command reads the flag from Smappee first and sends it back unchanged. |
| `station_features`, `maximum_capacity_a`, `dashboard_charging_station_details` | Dashboard | |

## Connector

| Field | Writers | Notes |
| --- | --- | --- |
| `session_state` | MQTT, Poll | **Rule:** while MQTT reports charger state live, the poll does not write it. The poll never writes `Initialize`. |
| `status_current`, `session_cause`, `stopped_by_cloud`, `iec_status`, `raw_charging_mode` | MQTT, Dashboard | Dashboard fills these only while empty. |
| `optimization_strategy` | MQTT, Dashboard load management, Dashboard station details | Load management writes whenever it reports a value; station details only while empty. |
| `ui_mode_base`, `selected_mode` | Derived, Command, Restore | Derived from `raw_charging_mode` and `optimization_strategy` whenever either is written. A command sets `selected_mode` only. |
| `paused` | Derived | Recomputed after each MQTT charging-state message and each poll. |
| `evcc_state`, `evcc_state_code` | Derived | From `iec_status`. |
| `selected_percentage_limit` | MQTT, Poll, Command, Restore | MQTT applies it only while the strategy is `NONE`. The current slider and the `set_current` action publish the confirmed value at once. |
| `selected_current_limit` | Derived, Command, Restore | From the percentage and the current range, only while the strategy is `NONE`. |
| `min_current`, `max_current` | MQTT, Poll, Dashboard, Command | Every writer goes through `resolve_connector_current_range`. The command writes the maximum only. |
| `min_surpluspct` | MQTT, Poll, Dashboard, Command, Restore | |
| `support_grid` | MQTT, Poll, Dashboard | No command. |
| `connector_number` | Poll, MQTT | |
| `connection_status` | MQTT, Dashboard | Dashboard only while empty. |
| `configuration_errors` | MQTT | |
| `available` | MQTT | Read by diagnostics only. |
| `api_available` | Poll | |
| `rest_session_state` | Poll | `chargingState` as the last poll reported it, `None` when left out or `Initialize`. Never written by MQTT. While MQTT is down the charging state sensor stays available on REST only when this equals `session_state`. |
| `dashboard_device_id`, `dashboard_device_uuid`, `dashboard_device_name` | Dashboard | |
| `power_total`, `power_phases`, `current_phases` | MQTT | Cleared when a session ends. The poll ends a session only while MQTT is not live. |
| `energy_import_kwh` | MQTT | |

`recent_sessions` is written by the session query alone and carried across
polls.

## Observations

Places where the inventory shows something worth a decision. None is a
confirmed defect.

1. **Station availability follows the last connector message.** Every
   connector's charging-state message mirrors its `available` to
   `station.available`. With two connectors that report different values, the
   switch shows whichever arrived last. Whether Smappee reports this value per
   connector or per station is not established. The connector's own `available`
   is stored but no entity reads it.
2. **Scalar power values are filtered on the site, not on the station.** The
   site coordinator ignores `consumptionPower`, `solarPower` and `alwaysOn` when
   the payload's `serviceLocationId` belongs to another location. The station
   coordinator applies them without that check.
3. **Most actions do not publish their confirmed value.** The current-limit
   number entity and the `set_current` action apply the confirmed value at once
   through `update_connector`; `start_charging` resends that stored percentage.
   The other actions in `services.py` only schedule a refresh, so entities show
   the old value until MQTT or the refresh reports. Measured on one station on
   2026-11-16: MQTT reported each change 70 to 170 ms after the write, before
   the REST call had returned. The delay only shows while MQTT is down.
4. **Restored values are not recorded as confirmed.** `number.py` and
   `select.py` write a restored value straight into an empty field. A poll in
   flight is kept from overwriting it by the snapshot comparison alone (mutation
   `rest_diff`).
5. **`optimization_strategy` has two remote writers.** MQTT and the Dashboard
   load-management read both write it without an owner rule. The in-flight
   protection applies; a load-management answer that starts after an MQTT update
   wins. Measured on one station on 2026-11-16: the refresh two minutes after a
   mode change did not move the mode back.

## Keeping this current

A new writer for an existing field belongs in the table. If that makes two
remote reads report the same field, decide its owner first and add a witness to
`tests/reconciliation_mutations.py`.
