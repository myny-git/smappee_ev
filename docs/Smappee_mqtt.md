#  Smappee MQTT Integration Guide (Smappee Infinity Genius + Home Assistant)

This guide explains how to connect your **Smappee Infinity Genius** to **Home Assistant** using **MQTT**, offering real-time and aggregated access to energy data (power, current, voltage, energy, events).

Take care, for Smappee Infinity Connect, you would require Modbus. See the [Modbus](https://github.com/myny-git/smappee_ev/blob/main/docs/Smappee_modbus.md) docs.

##  Prerequisites

-  Smappee Infinity Genius with MQTT functionality enabled
-  Access to the Genius Expert Portal (via `http://<IP-address>/smappee.html`)
-  Home Assistant with MQTT integration enabled
-  An MQTT broker available on the network (e.g., Mosquitto)
-  Familiarity with MQTT topics and JSON payloads

###  Enable MQTT on the Genius

1. Access the Expert Portal (same network; password “admin”)  
2. Navigate to **Advanced → MQTT**  
3. Enter broker URL:
   - `tcp://<brokerIP>:1883` for normal
   - `tls://<brokerIP>:8883` for secure
4. Apply changes and restart the monitor :contentReference[oaicite:23]{index=23}

---

##  MQTT Topic Overview

| Topic | Description |
|-------|-------------|
| `servicelocation/<uuid>/config` | Metadata about the service location (serial, version, etc.) — **retained** |
| `servicelocation/<uuid>/sensorConfig` | Metadata for gas/water sensors — **retained** |
| `servicelocation/<uuid>/channelConfig` | CT hub and channel configuration — **retained** |
| `servicelocation/<uuid>/realtime` | Real-time data—power, current, voltage, energy (Wh/Varh); published every second |
| `servicelocation/<uuid>/aggregated5min` | 5-minute aggregated energy/consumption values |
| `servicelocation/<uuid>/plug/<node id>/state` | ON/OFF state of a plug plus timestamp |
| `servicelocation/<uuid>/plug/<node id>/setstate` | Control topic to toggle plug state |

> ℹ  Replace `<uuid>` with your service location UUID, obtainable via Partner Dashboard or REST API, or using MQTT wildcard discovery methods :contentReference[oaicite:24]{index=24}

---

##  Home Assistant Configuration Examples

**Example MQTT sensor for real-time total power:**

```yaml
mqtt:
  sensor:
    - name: "Smappee Total Power"
      state_topic: "servicelocation/a02e.../realtime"
      value_template: "{{ value_json.totalPower }}"
      unit_of_measurement: "W"
      device_class: power
