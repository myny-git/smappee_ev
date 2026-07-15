# Smappee EV usage and entity overview in Home Assistant

This integration exposes entities, buttons and services to control and monitor a Smappee installation, including the EV charger to Home Assistant. It can also provide data for third-party energy-management systems such as EVCC, emhass and openEMS.

> Important: the Smappee app can be slow to reflect changes. Use the online Smappee Dashboard to verify charger behavior when in doubt.

Live state data is received from `mqtt.smappee.net`. Therefore, internet is required for this integration. Control and configuration are handled through the Smappee Dashboard REST API v10/v11.

## Configuration

Add the integration from **Settings -> Devices & services -> Add integration** and search for **Smappee EV**.

The config flow asks for these Smappee Dashboard credentials:

| Field | Required | Purpose |
|---|---:|---|
| Username | Yes | The username used to sign in to the Smappee Dashboard. |
| Password | Yes | The Smappee Dashboard password. It is used to obtain and refresh Dashboard API tokens. |

The integration stores the Dashboard refresh token after a successful login. If the token is rejected later, Home Assistant starts the reauthentication flow and asks for fresh Dashboard credentials.

Use **Configure** on the integration entry to update the saved Dashboard credentials. Reconfiguration validates that the credentials still belong to the same Smappee account before updating the entry.

## API Usage

The integration uses Dashboard v10/v11 only for active control:

| Area | API usage |
|---|---|
| Discovery | Dashboard v11 service locations and Dashboard v10 station details |
| Connector actions | Dashboard v10 device actions such as `startCharging`, `pauseCharging`, `stopCharging`, `setChargingMode` and `setPercentageLimit` |
| Charger configuration | Dashboard v10/v11 configuration endpoints for LED brightness, min surplus percentage, connector maximum current, availability, offline charging, capacity protection and overload protection |
| Live measurements | MQTT topics for power, current, energy and fast charger state |

## Services

These services can be called from automations, scripts or Developer Tools -> Actions.

All services accept the same optional target fields:

| Field | Required | Purpose |
|---|---:|---|
| `config_entry_id` | No | Select a specific Smappee EV config entry when more than one account is configured. |
| `service_location_id` | No | Select a specific Smappee service location/site. Required when the target cannot be inferred safely. |
| `connector_id` | No | Select a connector number, for example `1` or `2`. Required when a station has multiple connectors and the service acts on a connector. |

If the target is ambiguous, the service raises a Home Assistant validation error instead of guessing. If the selected entry/site/connector is not loaded or cannot be found, the service fails with a translated Home Assistant error.

### `smappee_ev.set_charging_mode`

Sets the desired charging mode for a connector via Dashboard v10.

Supported modes:

| Mode | Meaning | MQTT topic observation |
|---|---|---|
| `STANDARD` | Charge at a fixed current limit | `setchargingmode = {"mode":"STANDARD"}` |
| `SMART` | Balance grid and solar energy | `setchargingmode = {"mode":"SMART"}` |
| `SOLAR` | Charge from solar surplus only | `setchargingmode = {"mode":"SOLAR"}` |

Use the `max_charging_speed` number entity or `smappee_ev.set_current` to control the current limit independently.

### `smappee_ev.start_charging`

Starts a charging session for the selected connector.

Use `smappee_ev.set_current` or the `max_charging_speed` number entity to set the charging current separately.

The MQTT topic observation for starting charging is: `startcharging = {"percentageLimit":100}`.

### `smappee_ev.resume_charging`

Resumes a paused charging session.

The MQTT topic observation for resuming charging is: `setchargingmode = {"mode":"XXX"}`.

### `smappee_ev.pause_charging`

Pauses the currently active charging session via Dashboard v10.

The MQTT topic observation for pausing charging is: `pausecharging = {}`.

### `smappee_ev.stop_charging`

Stops the current charging session.

The MQTT topic observation for stopping charging is: `stopcharging = {}`.

### `smappee_ev.set_current`

Sets the charging current in Ampere with 1 decimal precision. The value is translated to the nearest integer percentage of the connector's configured minimum and maximum current range and sent via Dashboard v10.

The requested current must be inside the connector's configured minimum and maximum current range. Values outside that range are rejected instead of being silently changed.

The MQTT topic observation for setting the current is: `setpercentage = {"percentageLimit":27}`.

## Buttons, Numbers and Select Entities

### Charging mode select

`select.smappee_ev_YOURSERIAL_charging_mode_1`

Allows you to choose the connector charging mode:

| Option | Meaning |
|---|---|
| `STANDARD` | Charge at the fixed current limit from the charging-current number entity |
| `SMART` | Balance grid and solar energy |
| `SOLAR` | Charge using solar power only |

### Charging current limit

`number.smappee_ev_YOURSERIAL_current_1`

Defines the current in Ampere used for `STANDARD` mode. This number is available per connector.

| Current (A) | Power, 1 phase (kW) | Power, 3 phases (kW) |
|---:|---:|---:|
| 6 | 1.38 | 4.15 |
| 8 | 1.84 | 5.54 |
| 10 | 2.30 | 6.92 |
| 16 | 3.68 | 11.07 |
| 24 | 5.52 | 16.61 |
| 32 | 7.36 | 22.14 |

This current can be set with 1 decimal precision. The integration converts the requested current to the nearest supported percentage for the connector's configured range.

### Minimum surplus percentage

`number.smappee_ev_YOURSERIAL_min_surpluspct_1`

Sets how much of the minimum required current must be covered by surplus solar production before charging starts.

| Slider value | Charging behavior |
|---:|---|
| 0% | The charger always charges at minimum speed and increases speed when more surplus solar is available. |
| 25% | 25% must come from solar panels and 75% may come from the grid. |
| 50% | 50% must come from solar panels and 50% may come from the grid. |
| 100% | 100% must come from solar panels. |

The online Dashboard may update this value faster than the mobile app.

The `sensor.smappee_ev_YOURSERIAL_support_grid_1` sensor provides information about the maximum grid assistance current available.

### Maximum connector current

`number.smappee_ev_YOURSERIAL_connector_max_current_1`

Defines the maximum current in Ampere for the connector. This number is used to calculate the percentages and your Smappee Wallbox will not allow charging above this value. This number is available per connector.

### Other controls

| Entity | Purpose |
|---|---|
| `light.smappee_ev_YOURSERIAL_led` | Control for the Wallbox LED. |
| `button.smappee_ev_YOURSERIAL_start_charging_1` | Start charging for the connector. |
| `button.smappee_ev_YOURSERIAL_pause_charging_1` | Pause charging for the connector. |
| `button.smappee_ev_YOURSERIAL_resume_charging_1` | Resume charging for the connector. |
| `button.smappee_ev_YOURSERIAL_stop_charging_1` | Stop charging for the connector. |
| `switch.smappee_ev_YOURSERIAL_charging_1` | EVCC-friendly switch: on sets `STANDARD`, off pauses charging. |
| `button.smappee_ev_YOURSERIAL_restart_charging_station` | Restart the charging station. |
| `switch.smappee_ev_YOURSERIAL_offline_charging` | Enable offline charging mode, in case the Smappee is offline. |

## Sensor Entities

| Entity | Explanation |
|---|---|
| `sensor.smappee_ev_YOURSERIAL_charging_state_1` | Current session state, for example `started`, `suspended` or `stopped`. |
| `sensor.smappee_ev_YOURSERIAL_evcc_state_1` | EVCC state following IEC 61851, for example A/B/C/E. |
| `sensor.smappee_ev_YOURSERIAL_status_current_1` | Charger state similar to the Dashboard. |
| `sensor.smappee_ev_YOURSERIAL_session_energy_1` | Current or most recent Smappee charging session energy in kWh. |
| `binary_sensor.smappee_ev_YOURSERIAL_mqtt_connected` | MQTT connectivity state. |
| `sensor.smappee_ev_YOURSERIAL_offline_failsafe_current` | The failsafe current that will be used when the Smappee is offline and offline_charging is enabled. |
| `number.smappee_ev_YOURSERIAL_overload_maximum_load` | The main circuit breaker limit in amperes. |
| `number.smappee_ev_YOURSERIAL_capacity_maximum_power` | The maximum peak capacity power in kW. Interesting for Belgian users.|

The session energy sensor exposes session metadata such as id, serial number, connector, start time, end time, status, smart mode, priority and configured amperage values.

### Diagnostic Sensor Values

These values are passed through from Smappee MQTT/API payloads. Smappee does not officially document all possible values, so this list is not exhaustive and may differ between firmware versions.

These are the raw Home Assistant state values to use in automations. The Home Assistant UI may display them with capitalization, for example `charging finished` may appear as `Charging finished` in the activity/history view.

| Sensor | Possible HA state values | Meaning |
|---|---|---|
| `charging_state` | `initialize`, `started`, `suspended`, `stopped`, rare: `smart` | Raw Smappee `chargingState` from `_fetch_connector_state`, published by the integration as a lowercase string. `initialize` is the default before the first API fetch. No fixed enum is enforced. The rare `smart` value has been observed once; its session-classification behavior is unconfirmed, so consider opening an issue if it appears repeatedly. |
| `evcc_state` | `A`, `B`, `C`, `E`, `F` | IEC 61851 notation derived from `iec_status` through `_derive_evcc_letter`. A means no vehicle connected, B means connected/not charging, C means actively charging, E/F indicate fault states. `D` is intentionally excluded. |
| `evse_status` | `available`, `cable_connected`, `charging`, `charging_finished`, `error`, `suspended_evse` | Raw Smappee `status_current`, published by the integration as a lowercase string. No fixed enum is enforced. |

## Power, Current and Energy Sensors

These values are populated from MQTT. Depending on your installation, some sensors may remain unavailable and can be disabled manually.

| Entity | Explanation |
|---|---|
| `connector_current` | Live connector current. |
| `connector_current` per phase | Live connector phase currents. |
| `connector_energy` | Connector energy in kWh. |
| `connector_power` | Connector power in W. |
| `support_grid` | Maximum grid assistance current in A during Solar or Smart mode. |
| `grid_current` | Total grid current; phase currents are exposed as attributes. |
| `grid_energy_export` | Total grid export energy. |
| `grid_energy_import` | Total grid import energy. |
| `grid_power` | Total live grid power; can be negative. |
| `house_consumption_power` | Live household consumption power. |
| `always_on_power` | Site background or standby consumption in W, populated from the MQTT `alwaysOn` value. |
| `pv_current` | Total PV current; phase currents are exposed as attributes. |
| `pv_energy_import` | Total PV energy import. |
| `pv_power` | Live PV power. |

## Removing the Integration

1. In Home Assistant, go to **Settings -> Devices & services**.
2. Open the **Smappee EV** integration entry.
3. Choose **Delete** and confirm.
4. Restart Home Assistant if Home Assistant asks for it, or when you want to make sure all custom integration code has been unloaded.

Deleting the config entry unloads the platforms and runtime clients. Service actions remain registered for the Home Assistant session, as expected by Home Assistant service-action setup rules, and validate at call time whether a loaded Smappee EV config entry is available.

When removing the integration manually, also remove `custom_components/smappee_ev` from your Home Assistant configuration directory after deleting the config entry.

## Troubleshooting

| Symptom | What to check |
|---|---|
| Setup says authentication failed | Verify the same username and password in the online Smappee Dashboard. If the account has changed, reconfigure or reauthenticate the integration. |
| Entities are unavailable | Check internet access, Smappee cloud availability, Dashboard credentials and whether `binary_sensor.*_mqtt_connected` is on. |
| MQTT values are stale | Check whether `binary_sensor.*_mqtt_connected` is on and confirm that `mqtt.smappee.net` is reachable from Home Assistant. |
| A service action asks for `service_location_id` or `connector_id` | The integration found multiple possible targets. Provide the site or connector explicitly in the service call. |
| `set_current` is rejected | The requested current is outside the connector's configured min/max current range. Check the connector max current and use a value inside the allowed range. |
| The mobile app shows different information | The Smappee mobile app may lag behind. Use the online Dashboard and Home Assistant entity history when verifying recent changes. |
| Reauthentication appears | The saved Dashboard refresh token was rejected or expired. Enter fresh Dashboard credentials for the same Smappee account. |

## Known Limitations

- Internet access is required. Live data comes from `mqtt.smappee.net`; discovery and control use Smappee Dashboard API endpoints.
- Supported behavior depends on the Dashboard API data exposed for the charger model and firmware. Some configuration entities may stay unavailable when Smappee does not expose the matching endpoint or device id.
- Dynamic addition or removal of chargers/connectors during a Home Assistant session is still a phase-2 review item. Restart or reload the integration after changing the physical Smappee topology.
- The integration validates reauth/reconfigure credentials against the existing Smappee account. Credentials for another account are rejected.
- Session energy is based on Smappee cloud session data and may update later than live MQTT power/current values.
- The old Modbus-oriented path is not used for active control by this integration.
