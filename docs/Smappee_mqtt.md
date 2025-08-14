#  Smappee MQTT Integration Guide (Smappee Infinity Genius + Home Assistant)

This guide explains how to connect your **Smappee Infinity Genius** to **Home Assistant** using **MQTT**, offering real-time and aggregated access to energy data (power, current, voltage, energy, events).

Take care, for Smappee Infinity Connect, you would require Modbus. See the [Modbus](https://github.com/myny-git/smappee_ev/blob/main/docs/Smappee_modbus.md) docs.

##  Prerequisites

-  Smappee Infinity Genius with MQTT functionality enabled
-  Access to the Genius Expert Portal (via `http://<IP-address>/smappee.html`)
-  Home Assistant with MQTT integration enabled
-  An MQTT broker available on the network (e.g., Mosquitto)
-  Familiarity with MQTT topics and JSON payloads
-  Your tablet/laptop must be connector to the same network as the Smappee monitor.

###  Enable MQTT on the Genius

1. Access the Expert Portal (same network; password “admin”)  
`http://<IP-address>/smappee.html`
2. Navigate to **Advanced → MQTT**  
3. Enter broker URL:
   - `tcp://<brokerIP>:1883` for normal
   - `tls://<brokerIP>:8883` for secure
4. Apply changes and restart the monitor

##  MQTT Topic Overview

| Topic | Description |
|-------|-------------|
| `servicelocation/<uuid>/config` | Metadata about the service location (serial, version, etc.) — **retained** |
| `servicelocation/<uuid>/sensorConfig` | Metadata for gas/water sensors — **retained** |
| `servicelocation/<uuid>/channelConfig` | CT hub and channel configuration — **retained** |
| `servicelocation/<uuid>/homeControlConfig` | switch and smartplug Actuators — **retained** |
| `servicelocation/<uuid>/realtime` | Real-time data—power, current, voltage, energy (Wh/Varh); published every second |
| `servicelocation/<uuid>/aggregated5min` | 5-minute aggregated energy/consumption values |
| `servicelocation/<uuid>/plug/<node id>/state` | ON/OFF state of a plug plus timestamp |
| `servicelocation/<uuid>/plug/<node id>/setstate` | Control topic to toggle plug state |

> ℹ  Replace `<uuid>` with your service location UUID, obtainable via Partner Dashboard or REST API, or using MQTT wildcard discovery methods

Full details can be found [here](https://support.smappee.com/hc/en-gb/article_attachments/7693176771604) on the Smappee Support page.

##  Home Assistant Configuration Examples

**Example MQTT sensor for real-time total power:**

```yaml
- platform: mqtt
    name: 'Smappee Grid'
    state_topic: "servicelocation/xxxxxxxxxxxxxxxxxxx/realtime"
    value_template: "{{ value_json.totalPower }}"
    unit_of_measurement: 'W'
    device_class: power
    icon: mdi:current-ac

  - platform: mqtt
    state_topic: "servicelocation/xxxxxxxxxxxxxxxxxxxxxxxxxxx/realtime"
    name: "Smappee Solar"
    unit_of_measurement: "W"
    value_template: "{{value_json.channelPowers[0].power}}"
    device_class: power
    icon: mdi:current-ac
```
This page will be updated when users with MQTT share their configuration.
