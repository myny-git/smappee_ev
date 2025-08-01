# ⚡ Smappee Modbus Integration Guide (Smappee EV with Infinity series + Home Assistant)

This document explains how to connect your **Smappee EV Wall (Infinity series)** to **Home Assistant** using **Modbus TCP**, enabling real-time access to **power and current** data from your EV charger, grid, and PV system.

> ✅ This setup is designed for advanced users and integrators who want fine-grained control for monitoring, automation, or integration with systems like [EVCC](https://github.com/evcc-io/evcc).

---

## 🧱 Prerequisites

- ✅ Smappee Infinity system (with Modbus support) --> Smappee connect (not the Smappee Genius, this has MQTT and can be manually enabled)
- ✅ IP address of your Smappee module (e.g. `192.168.XX.XX`)
- ✅ Port 502 accessible in your local network
- ✅ Home Assistant with Modbus integration enabled
- send an email to support@smappee.com with a request to open modbus on your Connect ststem

## 🛠️ Home Assistant Configuration

Add the following to your `configuration.yaml`. The first sensors are Power Sensors (in W) and the second series are Current sensors (in A).You may need to add a unique identity with each sensor.

```yaml
modbus:
  - name: smappee_modbus
    type: tcp
    host: 192.168.XX.XX
    port: 502
    delay: 0
    message_wait_milliseconds: 30
    timeout: 5
    sensors:
      - name: smappee_modbus_power_L1_car
        slave: 61
        address: 256
        input_type: holding
        data_type: float32
        device_class: power
        unit_of_measurement: W
        swap: word
        scan_interval: 5
        precision: 2
      - name: smappee_modbus_power_L2_car
        slave: 61
        address: 260
        input_type: holding
        data_type: float32
        device_class: power
        unit_of_measurement: W
        swap: word
        scan_interval: 5
        precision: 2
      - name: smappee_modbus_power_L3_car

        slave: 61
        address: 264
        input_type: holding
        data_type: float32
        device_class: power
        unit_of_measurement: W
        swap: word
        scan_interval: 5
        precision: 2
      - name: smappee_modbus_power_L1_grid
        slave: 61
        address: 268
        input_type: holding
        data_type: float32
        device_class: power
        unit_of_measurement: W
        swap: word
        scan_interval: 5
        precision: 2
      - name: smappee_modbus_power_L2_grid
        slave: 61
        address: 272
        input_type: holding
        data_type: float32
        device_class: power
        unit_of_measurement: W
        swap: word
        scan_interval: 5
        precision: 2
      - name: smappee_modbus_power_L3_grid
        slave: 61
        address: 276
        input_type: holding
        data_type: float32
        device_class: power
        unit_of_measurement: W
        swap: word
        scan_interval: 5
        precision: 2

      - name: smappee_modbus_power_L1_PV
        slave: 61
        address: 280
        input_type: holding
        data_type: float32
        device_class: power
        unit_of_measurement: W
        swap: word
        scan_interval: 5
        precision: 2
      - name: smappee_modbus_power_L2_PV
        slave: 61
        address: 284
        input_type: holding
        data_type: float32
        device_class: power
        unit_of_measurement: W
        swap: word
        scan_interval: 5
        precision: 2
      - name: smappee_modbus_power_L3_PV
        slave: 61
        address: 288
        input_type: holding
        data_type: float32
        device_class: power
        unit_of_measurement: W
        swap: word
        scan_interval: 5
        precision: 2

      - name: smappee_modbus_current_L1_car
        slave: 61
        address: 128
        input_type: holding
        data_type: float32
        device_class: current
        unit_of_measurement: A
        swap: word
        scan_interval: 5
        precision: 3
      - name: smappee_modbus_current_L2_car
        slave: 61
        address: 132
        input_type: holding
        data_type: float32
        device_class: current
        unit_of_measurement: A
        swap: word
        scan_interval: 5
        precision: 3
      - name: smappee_modbus_current_L3_car
        slave: 61
        address: 136
        input_type: holding
        data_type: float32
        device_class: current
        unit_of_measurement: A
        swap: word
        scan_interval: 5
        precision: 3

      - name: smappee_modbus_current_L1_grid
        slave: 61
        address: 140
        input_type: holding
        data_type: float32
        device_class: current
        unit_of_measurement: A
        swap: word
        scan_interval: 5
        precision: 3
      - name: smappee_modbus_current_L2_grid
        slave: 61
        address: 144
        input_type: holding
        data_type: float32
        device_class: current
        unit_of_measurement: A
        swap: word
        scan_interval: 5
        precision: 3
      - name: smappee_modbus_current_L3_grid
        slave: 61
        address: 148
        input_type: holding
        data_type: float32
        device_class: current
        unit_of_measurement: A
        swap: word
        scan_interval: 5
        precision: 3

      - name: smappee_modbus_current_L1_PV
        slave: 61
        address: 152
        input_type: holding
        data_type: float32
        device_class: current
        unit_of_measurement: A
        swap: word
        scan_interval: 5
        precision: 3
      - name: smappee_modbus_current_L2_PV
        slave: 61
        address: 156
        input_type: holding
        data_type: float32
        device_class: current
        unit_of_measurement: A
        swap: word
        scan_interval: 5
        precision: 3
      - name: smappee_modbus_current_L3_PV
        slave: 61
        address: 160
        input_type: holding
        data_type: float32
        device_class: current
        unit_of_measurement: A
        swap: word
        scan_interval: 5
        precision: 3
```
## 🗂️ Register Map (From Smappee Register Excel)

There are also other sensors available, like energy, which can also be provided to EVCC. That's for later and if there is interest.

| Measurement | Type     | Phase | Source | Address |
|-------------|----------|-------|--------|---------|
| Power       | float32  | L1    | Car    | 256     |
| Power       | float32  | L2    | Car    | 260     |
| Power       | float32  | L3    | Car    | 264     |
| Power       | float32  | L1    | Grid   | 268     |
| Power       | float32  | L2    | Grid   | 272     |
| Power       | float32  | L3    | Grid   | 276     |
| Power       | float32  | L1    | PV     | 280     |
| Power       | float32  | L2    | PV     | 284     |
| Power       | float32  | L3    | PV     | 288     |
| Current     | float32  | L1    | Car    | 128     |
| Current     | float32  | L2    | Car    | 132     |
| Current     | float32  | L3    | Car    | 136     |
| Current     | float32  | L1    | Grid   | 140     |
| Current     | float32  | L2    | Grid   | 144     |
| Current     | float32  | L3    | Grid   | 148     |
| Current     | float32  | L1    | PV     | 152     |
| Current     | float32  | L2    | PV     | 156     |
| Current     | float32  | L3    | PV     | 160     |

## Template Sensors in Home Assistant
Next, as EVCC requires 3-phase power, a template sensor in home assistant can do the job!

```yaml
######################################
###
### Smappee modbus
###
######################################
template:
  sensor:
    - name: "smappee_modbus_power_total_car"
      unit_of_measurement: "W"
      device_class: power
      state_class: measurement
      state: >
        {% set l1 = states('sensor.smappee_modbus_power_l1_car') | float(0) %}
        {% set l2 = states('sensor.smappee_modbus_power_l2_car') | float(0) %}
        {% set l3 = states('sensor.smappee_modbus_power_l3_car') | float(0) %}
        {{ (l1 + l2 + l3) | round(2) }}

    - name: "smappee_modbus_power_total_grid"
      unit_of_measurement: "W"
      device_class: power
      state_class: measurement
      state: >
        {% set l1 = states('sensor.smappee_modbus_power_l1_grid') | float(0) %}
        {% set l2 = states('sensor.smappee_modbus_power_l2_grid') | float(0) %}
        {% set l3 = states('sensor.smappee_modbus_power_l3_grid') | float(0) %}
        {{ (l1 + l2 + l3) | round(2) }}

    - name: "smappee_modbus_power_total_pv"
      unit_of_measurement: "W"
      device_class: power
      state_class: measurement
      state: >
        {% set l1 = states('sensor.smappee_modbus_power_l1_pv') | float(0) %}
        {% set l2 = states('sensor.smappee_modbus_power_l2_pv') | float(0) %}
        {% set l3 = states('sensor.smappee_modbus_power_l3_pv') | float(0) %}
        {{ (l1 + l2 + l3) | round(2) }}
```

```yaml
meters:
  - name: Grid_smappee
    type: custom
    power:
      source: http
      uri: http://<HAlocalIP>:8123/api/states/sensor.smappee_modbus_power_total_grid
      method: GET
      headers:
        - Authorization: Bearer <long_lived_TOKEN>
        - Content-Type: application/json
      insecure: true
      jq: .state | tonumber
      timeout: 2s
    currents:
      - source: http
        uri: http://<HAlocalIP>:8123/api/states/sensor.smappee_modbus_current_l1_grid
        method: GET
        headers:
          - Authorization: Bearer <long_lived_TOKEN>
          - Content-Type: application/json
        insecure: true
        jq: .state | tonumber
        timeout: 2s

      - source: http
        uri: http://<HAlocalIP>:8123/api/states/sensor.smappee_modbus_current_l2_grid
        method: GET
        headers:
          - Authorization: Bearer <long_lived_TOKEN>
          - Content-Type: application/json
        insecure: true
        jq: .state | tonumber
        timeout: 2s

      - source: http
        uri: http://<HAlocalIP>:8123/api/states/sensor.smappee_modbus_current_l3_grid
        method: GET
        headers:
          - Authorization: Bearer <long_lived_TOKEN>
          - Content-Type: application/json
        insecure: true
        jq: .state | tonumber
        timeout: 2s

  - name: PV_smappee
    type: custom
    power:
      source: http
      uri: http://<HAlocalIP>:8123/api/states/sensor.smappee_modbus_power_total_pv
      method: GET
      headers:
        - Authorization: Bearer <long_lived_TOKEN>
        - Content-Type: application/json
      insecure: true
      jq: .state | tonumber
      timeout: 2s
    currents:
      - source: http
        uri: http://<HAlocalIP>:8123/api/states/sensor.smappee_modbus_current_l1_pv
        method: GET
        headers:
          - Authorization: Bearer <long_lived_TOKEN>
          - Content-Type: application/json
        insecure: true
        jq: .state | tonumber
        timeout: 2s

      - source: http
        uri: http://<HAlocalIP>:8123/api/states/sensor.smappee_modbus_current_l2_pv
        method: GET
        headers:
          - Authorization: Bearer <long_lived_TOKEN>
          - Content-Type: application/json
        insecure: true
        jq: .state | tonumber
        timeout: 2s

      - source: http
        uri: http://<HAlocalIP>:8123/api/states/sensor.smappee_modbus_current_l3_pv
        method: GET
        headers:
          - Authorization: Bearer <long_lived_TOKEN>
          - Content-Type: application/json
        insecure: true
        jq: .state | tonumber
        timeout: 2s
```
## **Notes**
- Make sure to replace <your_long_lived_token_here> with your actual Home Assistant long-lived access token.
- Configure your Home Assistant sensors using the Modbus integration and map the register addresses to match the sensors in the template section.
- You can validate your setup by monitoring the Home Assistant developer tools and EVCC live status dashboard.

Here you go! Feel free to reach out if you found bugs, have suggestions, ...
