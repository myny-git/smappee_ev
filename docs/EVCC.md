# 🚗 EVCC Custom Charger and a Smappee EV Wallbox

Welcome! 🎉 This page helps you integrate your **Smappee Wallbox** into [EVCC](https://evcc.io) using data from **Home Assistant**. It's still a work in progress, and I welcome **all suggestions and feedback**!

Thanks to [@marq24](https://github.com/marq24) for the pioneering guide on this topic:  


## 🚀 Step-by-Step Setup
All details can be found in following link: 🔗 [Home Assistant as EVCC Source](https://github.com/marq24/ha-evcc/blob/main/HA_AS_EVCC_SOURCE.md) 

### ✅ Step 1: Make Home Assistant Data Accessible
- Create a **long-lived access token** in Home Assistant.  #not sure this is still required!
- Make sure Home Assistant is reachable over your LAN (e.g. `http://192.168.x.x:8123`).

### ✅ Step 2: Collect Sensor Names
- Identify all relevant `sensor.*`, `switch.*` and `number.*` entities created by your Smappee integration.

> [!IMPORTANT]
> Entity IDs follow Home Assistant's `has_entity_name` + translation-key convention:
> `<domain>.smappee_ev_<station serial>_<metric>_<connector number>` (e.g.
> `sensor.smappee_ev_YOURSERIAL_evcc_state_1`). The **charging station** has its own
> serial number, separate from the site/service-location serial used by the grid and PV
> meter entities — grab `YOURSERIAL` from an actual `..._evcc_state_1`/`..._charging_1`
> style entity, not from the site meters. The most reliable way to confirm the exact IDs
> for your installation is **Developer Tools → States**, filtered on `smappee_ev`, since
> renaming your station in the Smappee app changes the device name and thus the entity_id
> (see the README's breaking-changes notes).

### ✅ Step 3: Define Your Charger in `evcc.yaml`

Below, you can find a full example for a Smappee EV Wallbox. The configuration was recently set up and is currently undergoing testing. The main idea is following:

We do not use the smart functions of the Smappee app, in contrary, we use it in Standard mode, with specific current targets.

🔌 Key Item: Charging Enable Control

Below is the full yaml for the charger. If you have changed the name of your wallbox, some entities will also have a different name in Home Assistant. Please always doublecheck prior to uploading the YAML. Also if you have two connectors, you should make two instances of the chargers, one per connector ID.

```yaml
##custom charger, as the main home-assistant charger does not enable decimals for currents
chargers:
  - name: smappee
    type: custom
    status:
      source: http
      uri: http://192.168.LOCALIP:8123/api/states/sensor.smappee_ev_YOURSERIAL_evcc_state_1
      method: GET
      auth:
        type: bearer
        token: YOUR BEARER TOKEN HERE
      jq: .state
      timeout: 10s

    enabled:
      source: http
      uri: http://192.168.LOCALIP:8123/api/states/switch.smappee_ev_YOURSERIAL_charging_1
      method: GET
      auth:
        type: bearer
        token: YOUR BEARER TOKEN HERE
      jq: .state == "on"
      timeout: 10s

    enable:
      source: http
      uri: 'http://192.168.LOCALIP:8123/api/services/switch/{{ if .enable }}turn_on{{ else }}turn_off{{ end }}'
      method: POST
      auth:
        type: bearer
        token: YOUR BEARER TOKEN HERE
      headers:
        - content-type: application/json
      body: '{"entity_id":"switch.smappee_ev_YOURSERIAL_charging_1"}'

    # fallback: integer A
    maxcurrent:
      source: http
      uri: http://192.168.LOCALIP:8123/api/services/number/set_value
      method: POST
      auth:
        type: bearer
        token: YOUR BEARER TOKEN HERE
      headers:
        - content-type: application/json
      body: '{"entity_id":"number.smappee_ev_YOURSERIAL_current_1","value":${maxcurrent}}'

    # fine control: decimal A, e.g. 6.4
    maxcurrentmillis:
      source: http
      uri: http://192.168.LOCALIP:8123/api/services/number/set_value
      method: POST
      auth:
        type: bearer
        token: YOUR BEARER TOKEN HERE
      headers:
        - content-type: application/json
      body: '{"entity_id":"number.smappee_ev_YOURSERIAL_current_1","value":${maxcurrentmillis}}'

    power:
      source: http
      uri: http://192.168.LOCALIP:8123/api/states/sensor.smappee_ev_YOURSERIAL_power_total_1
      method: GET
      auth:
        type: bearer
        token: YOUR BEARER TOKEN HERE
      jq: .state | tonumber
      timeout: 10s

    energy:
      source: http
      uri: http://192.168.LOCALIP:8123/api/states/sensor.smappee_ev_YOURSERIAL_energy_import_kwh_1
      method: GET
      auth:
        type: bearer
        token: YOUR BEARER TOKEN HERE
      jq: .state | tonumber
      timeout: 10s

    currents:
      - source: http
        uri: http://192.168.LOCALIP:8123/api/states/sensor.smappee_ev_YOURSERIAL_current_l1_1
        method: GET
        auth:
          type: bearer
          token: YOUR BEARER TOKEN HERE
        jq: .state | tonumber
        timeout: 10s
      - source: http
        uri: http://192.168.LOCALIP:8123/api/states/sensor.smappee_ev_YOURSERIAL_current_l2_1
        method: GET
        auth:
          type: bearer
          token: YOUR BEARER TOKEN HERE
        jq: .state | tonumber
        timeout: 10s
      - source: http
        uri: http://192.168.LOCALIP:8123/api/states/sensor.smappee_ev_YOURSERIAL_current_l3_1
        method: GET
        auth:
          type: bearer
          token: YOUR BEARER TOKEN HERE
        jq: .state | tonumber
        timeout: 10s
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
      phases: 3  ## 1 or 3 phases
      enable:  
        delay: 2m ## add these if your car does not support fast switching between on and off.
      disable:
        delay: 2m ## add these if your car does not support fast switching between on and off.
```
