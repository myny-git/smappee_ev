# 🚗 EVCC Custom Charger and a Smappee EV Wallbox

Welcome! 🎉 This page helps you integrate your **Smappee Wallbox** into [EVCC](https://evcc.io) using data from **Home Assistant**. It's still a work in progress, and I welcome **all suggestions and feedback**!

Thanks to [@marq24](https://github.com/marq24) for the pioneering guide on this topic:  


## 🚀 Step-by-Step Setup
All details can be found in following link: 🔗 [Home Assistant as EVCC Source](https://github.com/marq24/ha-evcc/blob/main/HA_AS_EVCC_SOURCE.md)
Since recent updates of EVCC, the configuration has changed a bit. No more long-lived access token is required, but you still need to make sure that Home Assistant is reachable over your LAN.

### ✅ Step 1: Make Home Assistant Data Accessible
- Create a **long-lived access token** in Home Assistant.  #not sure this is still required!
- Make sure Home Assistant is reachable over your LAN (e.g. `http://192.168.x.x:8123`).

### ✅ Step 2: Collect Sensor Names
- Identify all relevant `sensor.*`, `switch.*` and `number.*` entities created by your Smappee integration.

### ✅ Step 3: Define Your Charger in `evcc.yaml`

Below, you can find a full example for a Smappee EV Wallbox. The configuration was recently set up and is currently undergoing testing. The main idea is following:

We do not use the smart functions of the Smappee app, in contrary, we use it in Standard mode, with specific current targets.

🔌 Key Item: Charging Enable Control

Below is the full yaml for the charger. If you have changed the name of your wallbox, some entities will also have a different name in Home Assistant. Please always doublecheck prior to uploading the YAML. Also if you have two connectors, you should make two instances of the chargers, one per connector ID.

```yaml
# see https://docs.evcc.io/en/chargers/home-assistant-charger/
chargers:
  - name: smappee
    type: template
    template: homeassistant
    uri: http://HAlocalIP:8123
    status: sensor.evcc_state_1 # Charging status sensor, Entity ID for charging status (A=ready, B=connected, C=charging)
    enabled: switch.smappee_ev_evcc_charging_control_1 # Enabled status sensor, Entity ID for enabled state (`sensor`, `binary_sensor` or `switch` with `on`/`off` or `true`/`false` state)
    enable: switch.smappee_ev_evcc_charging_control_1 #  Enable switch, Entity ID for enable/disable control (`switch` or `input_boolean`)
    setMaxCurrent: number.smappee_ev_max_charging_speed_1 # Maximum current entity [A], Entity ID for setting maximum current in amperes (`number` or `input_number` entity)
    ## POWER / CURRENTS / ENERGY from the modbus (or the official Smappee) integration
    power: number.smappee_ev_connector_1_power # Power entity, Entity ID for instantaneous power measurement in watts (optional)
    energy: sensor.smappee_ev_connector_1_energy_import # Energy entity, Entity ID for cumulative energy measurement in kWh (optional)
    currentL1: sensor.smappee_ev_connector_1_current_l1 # L1 current entity, Entity ID for L1 current measurement in amperes (optional)
    currentL2: sensor.smappee_ev_connector_1_current_l2 # L2 current entity, Entity ID for L2 current measurement in amperes (optional)
    currentL3: sensor.smappee_ev_connector_1_current_l3 # L3 current entity, Entity ID for L3 current measurement in amperes (optional)      
    #voltageL1: sensor.charger_voltage_l1 # L1 voltage entity, Entity ID for L1 voltage measurement in volts (optional)
    #voltageL2: sensor.charger_voltage_l2 # L2 voltage entity, Entity ID for L2 voltage measurement in volts (optional)
    #voltageL3: sensor.charger_voltage_l3 # L3 voltage entity, Entity ID for L3 voltage measurement in volts (optional)
    #phaseswitch: select.charger_phases # Phase switching entity, Entity ID for 1p/3p phase switching (select entity with options "1" and "3") (optional)
    #heating: # Heating device, Shows °C instead of % (optional)
    #integrateddevice: # Integrated device, Integrated device. No charging sessions (optional)    
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
```
