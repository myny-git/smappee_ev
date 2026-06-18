# Smappee EV usage and entity overview in Home Assistant

This integration exposes entities, buttons and services to control and monitor a Smappee installation, including the EV charger to Home Assistant. It can also provide data for third-party energy-management systems such as EVCC, emhass and openEMS.

> Important: the Smappee app can be slow to reflect changes. Use the online Smappee Dashboard to verify charger behavior when in doubt.

Live state data is received from `mqtt.smappee.net`. Therefore, internet is required for this integration. Control and configuration are handled through the Smappee Dashboard REST API v10/v11.

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
| `sensor.smappee_ev_YOURSERIAL_charging_state_1` | Current session state, for example `STARTED`, `SUSPENDED` or `STOPPED`. |
| `sensor.smappee_ev_YOURSERIAL_evcc_state_1` | EVCC state following IEC 61851, for example A/B/C/E. |
| `sensor.smappee_ev_YOURSERIAL_status_current_1` | Charger state similar to the Dashboard. |
| `sensor.smappee_ev_YOURSERIAL_session_energy_1` | Current or most recent Smappee charging session energy in kWh. |
| `binary_sensor.smappee_ev_YOURSERIAL_mqtt_connected` | MQTT connectivity state. |
| `sensor.smappee_ev_YOURSERIAL_mqtt_last_seen` | Timestamp of the last MQTT update. Disabled as it gives a lot of noise to Home Assistant. |
| `sensor.smappee_ev_YOURSERIAL_offline_failsafe_current` | The failsafe current that will be used when the Smappee is offline and offline_charging is enabled. |
| `number.smappee_ev_YOURSERIAL_overload_maximum_load` | The main circuit breaker limit in amperes. |
| `number.smappee_ev_YOURSERIAL_capacity_maximum_power` | The maximum peak capacity power in kW. Interesting for Belgian users.|

The session energy sensor exposes session metadata such as id, serial number, connector, start time, end time, status, smart mode, priority and configured amperage values.

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
| `pv_current` | Total PV current; phase currents are exposed as attributes. |
| `pv_energy_import` | Total PV energy import. |
| `pv_power` | Live PV power. |
