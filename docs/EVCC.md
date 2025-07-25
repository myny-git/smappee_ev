# ⚡ Integrate Smappee with EVCC

Welcome! 🎉 This page helps you integrate your **Smappee Wallbox** into [EVCC](https://evcc.io) using data from **Home Assistant**. It's still a work in progress, and I welcome **all suggestions and feedback**!

Thanks to [@marq24](https://github.com/marq24) for the pioneering guide on this topic:  


## 🚀 Step-by-Step Setup
All details can be found in following link: 🔗 [Home Assistant as EVCC Source](https://github.com/marq24/ha-evcc/blob/main/HA_AS_EVCC_SOURCE.md)

### ✅ Step 1: Make Home Assistant Data Accessible
- Create a **long-lived access token** in Home Assistant.
- Make sure Home Assistant is reachable over your LAN (e.g. `http://192.168.x.x:8123`).

### ✅ Step 2: Collect Sensor Names
- Identify all relevant `sensor.*` and `number.*` entities created by your Smappee integration.

### ✅ Step 3: Define Your Charger in `evcc.yaml`

This is a full example for a Smappee Wallbox:
This is an important one. It works, however, I am in search to have EVCC controlling the charging current. It now uses the MaxCurrent.
enable: # also mandatory, this is to enable the charging mode. I created an entry to two services.
      source: http
      uri: http://HAlocalIP:8123/api/services/smappee_ev/{{ if .enable }}set_charging_mode{{ else }}pause_charging{{ end }}
      method: POST
      headers:
        - Authorization: Bearer long_lived_TOKEN
        - Content-Type: application/json
      body: >
        {{ if .enable }}
        { "mode": "NORMAL" }
        {{ else }}
        {}
        {{ end }}
      timeout: 2s # timeout in golang duration format, see https://golang.org/pkg/time/#ParseDuration

```yaml
# see https://docs.evcc.io/docs/devices/chargers
chargers:
  - name: smappee
    type: custom
    status: # charger status A..F --> the evcc_state integration does the job!
      source: http
      uri: http://HAlocalIP:8123/api/states/sensor.charging_point_YOURSERIAL_evcc_state
      method: GET
      headers:
        - Authorization: Bearer long_lived_TOKEN
        - Content-Type: application/json
      insecure: true
      jq: .state[0:1]
      timeout: 2s # timeout in golang duration format, see https://golang.org/pkg/time/#ParseDuration
    enabled: # also mandatory
      source: http
      uri: http://HAlocalIP:8123/api/states/sensor.charging_point_YOURSERIAL_session_state
      method: GET
      headers:
        - Authorization: Bearer long_lived_TOKEN
        - Content-Type: application/json
      insecure: true
      jq: 'if .state == "STOPPED" then 0 else 1 end'
      timeout: 2s # timeout in golang duration format, see https://golang.org/pkg/time/#ParseDuration
    enable: # also mandatory, this is to enable the charging mode. I created an entry to two services.
      source: http
      uri: http://HAlocalIP:8123/api/services/smappee_ev/{{ if .enable }}set_charging_mode{{ else }}pause_charging{{ end }}
      method: POST
      headers:
        - Authorization: Bearer long_lived_TOKEN
        - Content-Type: application/json
      body: >
        {{ if .enable }}
        { "mode": "NORMAL" }
        {{ else }}
        {}
        {{ end }}
      timeout: 2s # timeout in golang duration format, see https://golang.org/pkg/time/#ParseDuration
    maxcurrent: # I take this value from the smappee_current_limit
      source: http
      uri: http://HAlocalIP:8123/api/services/number/set_value
      method: POST
      body: "{\"entity_id\": \"number.smappee_current_limit_YOURSERIAL\", \"value\": \"${maxcurrent}\"}"
      headers:
        - Authorization: Bearer long_lived_TOKEN
        - Content-Type: application/json
      insecure: true  
    power: # not mandatory, but take the power sensor of the smappee charger.
      source: http
      uri: http://HAlocalIP:8123/api/states/sensor.smappee_modbus_power_total_car
      method: GET
      headers:
        - Authorization: Bearer long_lived_TOKEN
        - Content-Type: application/json
      insecure: true
      jq: .state | tonumber
      timeout: 2s # timeout in golang duration format, see https://golang.org/pkg/time/#ParseDuration
    tos: true
    Phases1p3p: # not mandatory, I am testing this, with a fake switch which I created in home assistant
      source: http
      uri: http://HAlocalIP:8123/api/states/input_select.fake_phase_switch
      method: GET
      headers:
        - Authorization: Bearer long_lived_TOKEN
        - Content-Type: application/json
      jq: >
        if .state == "1" then 1 else 3 end."
```
## 🔌 YAML Example: circuits configuration for peak shaving / load balancing ??

```yaml
circuits:
- name: main
  title: main circuit
  maxCurrent: 75 # on my home circuit breaker3 x 25A 
  maxPower: 4500 # max power I like to allow
  meter: Grid_smappee
```
The peak shaving and current control do not optimally yet — this is work in progress. Feel free to experiment and suggest improvements!

## ⚠️ EVCC requires high update rates for sensors

The original Smappee integration (with Smappee Infinity/Connect) does not provide sufficiently fast updates.

Therefore, I integrated this via Modbus.

The Smappee Infinity/Genius also supports MQTT, which is nicely explained by Smappee.

👉 Please see the new [document](./Smappee_modbus.md) for details on using Modbus sensors in Home Assistant with Smappee Connect.

Other options to consider as input for EVCC include:
- Your P1 meter
- Direct sensor data from your PV system
All those integrations are nicely explained on the EVCC website documentation.

