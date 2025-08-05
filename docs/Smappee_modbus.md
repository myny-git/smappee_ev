# ⚡ Smappee Modbus Integration Guide (Smappee EV with Infinity series + Home Assistant)

This document explains how to connect your **Smappee EV Wall (Infinity series)** to **Home Assistant** using **Modbus TCP**, enabling real-time access to **power and current** data from your EV charger, grid, and PV system.

> ✅ This setup is designed for advanced users and integrators who want fine-grained control for monitoring, automation, or integration with systems like [EVCC](https://github.com/evcc-io/evcc).

---

## 🧱 Prerequisites

- ✅ Smappee Infinity system (with Modbus support) --> Smappee connect (not the Smappee Genius, this has MQTT and can be manually enabled)
- ✅ IP address of your Smappee module (e.g. `192.168.XX.XX`)
- ✅ Port 502 accessible in your local network
- ✅ Home Assistant with Modbus integration enabled
- Send an email to support@smappee.com with a request to open modbus on your Connect system
- Afterwards, when you receive the confirmation from Smappee Support, you may have to reboot the charger (put fuse off and on).

## 🛠️ Home Assistant Configuration

**Usage instructions:**
I created in the docs a modbus.yaml file. 

1. Replace `192.168.x.x` under `host:` with the IP address of your Smappee gateway.
2. Replace every instance of `YOURSERIAL` with a unique string (e.g., your Smappee serial number).
   This will be used for generating unique entity IDs in Home Assistant.
3. Add the content of the file to your `configuration.yaml`, or include it via `!include` in a separate YAML file.


```
## 🗂️ Register Map (From Smappee Register Excel)

The energy register map is in the discussions, and already converted in the modbus.yaml file.

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
```

## Template Sensors in Home Assistant
Next, use template sensors to combine all three phases to one power sensor:

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
You can use these in your EVCC implementation.
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

## Energy Template sensors
Next, use template sensors to realize kWh sensors combining again L1/L2/L3. I only show the most relevant here:

```yaml
######################################
###
### Smappee modbus
###
######################################
template:
  sensor:  ## You can also add YOURSERIAL in the naming
    - name: smappee_energy_import_car  ## energy delivered to the car
      unit_of_measurement: kWh
      device_class: energy
      state_class: total_increasing
      state: >
        {{
          (states('sensor.smappee_modbus_energy_L1_import_car') | float +
          states('sensor.smappee_modbus_energy_L2_import_car') | float +
          states('sensor.smappee_modbus_energy_L3_import_car') | float) / 1000
        }}

    - name: smappee_energy_import_grid  ## energy imported from the grid - aka consumption
      unit_of_measurement: kWh
      device_class: energy
      state_class: total_increasing
      state: >
        {{ 
          (states('sensor.smappee_modbus_energy_L1_import_grid') | float +
          states('sensor.smappee_modbus_energy_L2_import_grid') | float +
          states('sensor.smappee_modbus_energy_L3_import_grid') | float) / 1000
        }}

    - name: smappee_energy_export_grid  ## energy exported to the grid - aka grid feed-in
      unit_of_measurement: kWh
      device_class: energy
      state_class: total_increasing
      state: >
        {{ 
          (states('sensor.smappee_modbus_energy_L1_export_grid') | float +
          states('sensor.smappee_modbus_energy_L2_export_grid') | float +
          states('sensor.smappee_modbus_energy_L3_export_grid') | float) / 1000 
        }}

    - name: smappee_energy_import_pv  ## PV-generated energy or production
      unit_of_measurement: Wh
      device_class: energy
      state_class: total_increasing
      state: >
        {{ 
          (states('sensor.smappee_modbus_energy_L1_import_PV') | float +
          states('sensor.smappee_modbus_energy_L2_import_PV') | float +
          states('sensor.smappee_modbus_energy_L3_import_PV') | float) / 1000
        }}


```
**You can also use these in your EVCC implementation, if you want.**

An additional option can be to make **utility-meters** in home assistant. 
```yaml
######################################
###
### Smappee utility
###
######################################
utility_meter:
   smappee_modbus_energy_import_grid_15m:  # quarterly hour peak
      source: sensor.smappee_energy_import_grid
      cycle: quarter-hourly
      unique_id: smappee_energy_import_grid_consumption_15m  
  smappee_modbus_energy_import_grid_day:  # daily consumption
      source: sensor.smappee_energy_import_grid
      cycle: daily
      unique_id: smappee_energy_import_grid_consumption_day
```



## **Notes**
- Make sure to replace <your_long_lived_token_here> with your actual Home Assistant long-lived access token.
- Configure your Home Assistant sensors using the Modbus integration and map the register addresses to match the sensors in the template section.
- You can validate your setup by monitoring the Home Assistant developer tools and EVCC live status dashboard.

Here you go! Feel free to reach out if you found bugs, have suggestions, ...
