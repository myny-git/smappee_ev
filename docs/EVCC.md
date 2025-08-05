# 🚗 EVCC Custom Charger and a Smappee EV Wallbox

Welcome! 🎉 This page helps you integrate your **Smappee Wallbox** into [EVCC](https://evcc.io) using data from **Home Assistant**. It's still a work in progress, and I welcome **all suggestions and feedback**!

Thanks to [@marq24](https://github.com/marq24) for the pioneering guide on this topic:  


## 🚀 Step-by-Step Setup
All details can be found in following link: 🔗 [Home Assistant as EVCC Source](https://github.com/marq24/ha-evcc/blob/main/HA_AS_EVCC_SOURCE.md)

### ✅ Step 1: Make Home Assistant Data Accessible
- Create a **long-lived access token** in Home Assistant.
- Make sure Home Assistant is reachable over your LAN (e.g. `http://192.168.x.x:8123`).

### ✅ Step 2: Collect Sensor Names
- Identify all relevant `sensor.*`, `switch.*` and `number.*` entities created by your Smappee integration.

### ✅ Step 3: Define Your Charger in `evcc.yaml`

Below, you can find a full example for a Smappee EV Wallbox. The configuration was recently set up and is currently undergoing testing. The main idea is following:

We do not use the smart functions of the Smappee app, in contrary, we use it in Standard mode, with specific current targets.

🔌 Key Item: Charging Enable Control

Below is the full yaml for the charger.

```yaml
# see https://docs.evcc.io/docs/devices/chargers
chargers:
  - name: smappee
    type: custom
    status: # charger status A..F --> the evcc_state integration does the job!
      source: http
      uri: http://HAlocalIP:8123/api/states/sensor.evcc_state
      method: GET
      headers:
        - Authorization: Bearer long_lived_TOKEN
        - Content-Type: application/json
      insecure: true
      jq: .state[0:1]
      timeout: 2s # timeout in golang duration format, see https://golang.org/pkg/time/#ParseDuration
    enabled: # also mandatory
      source: http
      uri: http://HAlocalIP:8123/api/states/switch.smappee_ev_wallbox_evcc_charging_control
      method: GET
      headers:
        - Authorization: Bearer long_lived_TOKEN
        - Content-Type: application/json
      insecure: true
      jq: '.state == "on"'
      timeout: 2s # timeout in golang duration format, see https://golang.org/pkg/time/#ParseDuration
    enable: # also mandatory, this is to enable the charging mode. I created an entry to two services.
      source: http
      uri: http://192.168.50.163:8123/api/services/switch/{{ if .enable }}turn_on{{ else }}turn_off{{ end }}
      method: POST
      headers:
        - Authorization: Bearer long_lived_TOKEN
        - Content-Type: application/json
      body: >
        {
          "entity_id": "switch.smappee_ev_wallbox_evcc_charging_control"
        }
      timeout: 2s # timeout in golang duration format, see https://golang.org/pkg/time/#ParseDuration
    maxcurrent: # set charging mode to normal and provide the current.
      source: http
      uri: http://HAlocalIP:8123/api/services/number/set_value
      method: POST
      headers:
        - Authorization: Bearer long_lived_TOKEN
        - Content-Type: application/json
      body: >
       {
         "entity_id": "number.smappee_ev_wallbox_max_charging_speed",
         "value": {{ .maxcurrent }}
       }
      insecure: true  
      ## POWER / CURRENTS / ENERGY from the modbus (or the official Smappee) integration
    power: # not mandatory, but take the power sensor of the smappee charger.(see the Smappee_modbus.md for more info)
      source: http
      uri: http://HAlocalIP:8123/api/states/sensor.smappee_modbus_power_total_car
      method: GET
      headers:
        - Authorization: Bearer long_lived_TOKEN
        - Content-Type: application/json
      insecure: true
      jq: .state | tonumber
      timeout: 2s # timeout in golang duration format, see https://golang.org/pkg/time/#ParseDuration
    #tos: true
    #Phases1p3p: # not mandatory, I am testing this, with a fake switch which I created in home assistant
    currents:
      - source: http
        uri: http://HAlocalIP:8123/api/states/sensor.smappee_modbus_current_l1_car
        method: GET
        headers:
          - Authorization: Bearer long_lived_TOKEN
          - Content-Type: application/json
        insecure: true
        jq: .state | tonumber
        timeout: 2s # timeout in golang duration format, see https://golang.org/pkg/time/#ParseDuration
      - source: http
        uri: http://HAlocalIP:8123/api/states/sensor.smappee_modbus_current_l2_car
        method: GET
        headers:
          - Authorization: Bearer long_lived_TOKEN
          - Content-Type: application/json
        insecure: true
        jq: .state | tonumber
        timeout: 2s # timeout in golang duration format, see https://golang.org/pkg/time/#ParseDuration
      - source: http
        uri: http://HAlocalIP:8123/api/states/sensor.smappee_modbus_current_l3_car
        method: GET
        headers:
          - Authorization: Bearer long_lived_TOKEN
          - Content-Type: application/json
        insecure: true
        jq: .state | tonumber
        timeout: 2s # timeout in golang duration format, see https://golang.org/pkg/time/#ParseDuration                
    energy: ## this is a template sensor, combining [energy_import_car(L1 + L2 + L3) / 1000]
      source: http
      uri: http://HAlocalIP:8123/api/states/sensor.smappee_energy_import_car
      method: GET
      headers:
        - Authorization: Bearer long_lived_TOKEN
        - Content-Type: application/json
      jq: .state | tonumber
      timeout: 2s
      insecure: true        
```
## 🔌 YAML Example: circuits configuration for peak shaving / load balancing ??

```yaml
circuits:
- name: main
  title: main circuit
  maxCurrent: 75 # on my home circuit breaker3 x 25A 
  maxPower: 4500 # max power I like to allow - I have it configured in Smappee as 5kW
  meter: Grid_smappee
```

You still have to include a loadpoint:
```yaml
loadpoints:
    - title: Carport # display name for UI
      charger: smappee # charger
      vehicle: Ford_explorer # default vehicle
      circuit: main
      phases: 0  ## to allow for 1 and 3 phases - to be evaluated
```

## ⚠️ EVCC requires high update rates for sensors

The original Smappee integration (with Smappee Infinity/Connect) does not provide sufficiently fast updates.

Therefore, I integrated this via Modbus.

The Smappee Infinity/Genius also supports MQTT, which is nicely explained by Smappee.

👉 Please see the new [document](./Smappee_modbus.md) for details on using Modbus sensors in Home Assistant with Smappee Connect.

Other options to consider as input for EVCC include:
- Your P1 meter
- Direct sensor data from your PV system
All those integrations are nicely explained on the EVCC website documentation.

