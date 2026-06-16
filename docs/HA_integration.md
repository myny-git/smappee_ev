# Smappee EV usage and entity overview in Home Assistant

This integration exposes entities, buttons and services to control and monitor a Smappee EV charger from Home Assistant. It can also provide data for third-party energy-management systems such as EVCC, emhass and openEMS.

> Important: the Smappee app can be slow to reflect changes. Use the online Smappee Dashboard to verify charger behavior when in doubt.

Live state data is received from `mqtt.smappee.net`. Control and configuration are handled through the Smappee Dashboard REST API v10/v11.

## API Usage

The integration uses Dashboard v10/v11 only for active control:

| Area | API usage |
|---|---|
| Discovery | Dashboard v11 service locations and Dashboard v10 station details |
| Connector actions | Dashboard v10 device actions such as `startCharging`, `pauseCharging`, `stopCharging`, `setChargingMode` and `setPercentageLimit` |
| Charger configuration | Dashboard v10/v11 configuration endpoints for LED brightness, min surplus percentage, connector maximum current, availability, offline charging, capacity protection and overload protection |
| Live measurements | MQTT topics for power, current, energy and fast charger state |

Deprecated legacy service names from older versions are removed during setup if Home Assistant still has them registered.

## Services

These services can be called from automations, scripts or Developer Tools -> Actions.

### `smappee_ev.set_charging_mode`

Sets the desired charging mode for a connector via Dashboard v10.

Supported modes:

| Mode | Meaning |
|---|---|
| `STANDARD` | Charge at a fixed current limit |
| `SMART` | Balance grid and solar energy |
| `SOLAR` | Charge from solar surplus only |

Use the `max_charging_speed` number entity or `smappee_ev.set_current` to control the current limit independently.

### `smappee_ev.start_charging`

Starts a charging session using a current limit. The integration converts the requested current to the nearest supported percentage for the connector's configured range.

### `smappee_ev.pause_charging`

Pauses the currently active charging session via Dashboard v10.

### `smappee_ev.stop_charging`

Stops the current charging session.

### `smappee_ev.set_current`

Sets the charging current in Ampere with 1 decimal precision. The value is translated to the nearest integer percentage of the connector's configured minimum and maximum current range and sent via Dashboard v10.

## Buttons, Numbers and Select Entities

### Charging mode select

`select.smappee_ev_YOURSERIAL_charging_mode_1`

Allows you to choose the connector charging mode:

| Option | Meaning |
|---|---|
| `STANDARD` | Charge at the fixed current limit from the charging-current number entity |
| `SMART` | Balance grid and solar energy |
| `SOLAR` | Charge using solar power only |

### Charging current number

`number.smappee_ev_YOURSERIAL_max_charging_speed_1`

Defines the current in Ampere used for `STANDARD` mode. This number is available per connector.

| Current (A) | Power, 1 phase (kW) | Power, 3 phases (kW) |
|---:|---:|---:|
| 6 | 1.38 | 4.15 |
| 8 | 1.84 | 5.54 |
| 10 | 2.30 | 6.92 |
| 16 | 3.68 | 11.07 |
| 24 | 5.52 | 16.61 |
| 32 | 7.36 | 22.14 |

### Minimum surplus percentage

`number.smappee_ev_YOURSERIAL_min_surplus_percentage_1`

Sets how much of the minimum required current must be covered by surplus solar production before charging starts.

| Slider value | Charging behavior |
|---:|---|
| 0% | The charger always charges at minimum speed and increases speed when more surplus solar is available. |
| 25% | 25% must come from solar panels and 75% may come from the grid. |
| 50% | 50% must come from solar panels and 50% may come from the grid. |
| 100% | 100% must come from solar panels. |

The online Dashboard may update this value faster than the mobile app.

### Other controls

| Entity | Purpose |
|---|---|
| `number.smappee_ev_YOURSERIAL_led_brightness` | Set Wallbox LED brightness from 0 to 100%. |
| `button.smappee_ev_YOURSERIAL_set_charging_mode_1` | Apply the selected charging mode. |
| `button.smappee_ev_YOURSERIAL_start_charging_1` | Start charging for the connector. |
| `button.smappee_ev_YOURSERIAL_pause_charging_1` | Pause charging for the connector. |
| `button.smappee_ev_YOURSERIAL_stop_charging_1` | Stop charging for the connector. |
| `switch.smappee_ev_YOURSERIAL_connector_1_evcc_charging` | EVCC-friendly switch: on sets `STANDARD`, off pauses charging. |

## Sensor Entities

| Entity | Explanation |
|---|---|
| `sensor.smappee_ev_YOURSERIAL_connector_1_charging_state` | Current session state, for example `STARTED`, `SUSPENDED` or `STOPPED`. |
| `sensor.smappee_ev_YOURSERIAL_connector_1_evcc_state` | EVCC state following IEC 61851, for example A/B/C/E. |
| `sensor.smappee_ev_YOURSERIAL_connector_1_evse_status` | Charger state similar to the Dashboard. |
| `sensor.smappee_ev_YOURSERIAL_connector_1_session_energy` | Current or most recent Smappee cloud charging session energy in kWh. |
| `binary_sensor.smappee_ev_YOURSERIAL_mqtt_connected` | MQTT connectivity state. |
| `sensor.smappee_ev_YOURSERIAL_mqtt_last_seen` | Timestamp of the last MQTT update. |

The session energy sensor exposes session metadata such as id, serial number, connector, start time, end time, status, smart mode, priority and configured amperage values.

## Power, Current and Energy Sensors

These values are populated from MQTT. Depending on your installation, some sensors may remain unavailable and can be disabled manually.

| Entity | Explanation |
|---|---|
| `connector_current` | Live connector current. |
| `connector_current` per phase | Live connector phase currents. |
| `connector_energy` | Connector energy in kWh. |
| `connector_power` | Connector power in W. |
| `support_grid` | Maximum grid assistance current in A. |
| `grid_current` | Total grid current; phase currents are exposed as attributes. |
| `grid_energy_export` | Total grid export energy. |
| `grid_energy_import` | Total grid import energy. |
| `grid_power` | Total live grid power; can be negative. |
| `house_consumption_power` | Live household consumption power. |
| `pv_current` | Total PV current; phase currents are exposed as attributes. |
| `pv_energy_import` | Total PV energy import. |
| `pv_power` | Live PV power. |
