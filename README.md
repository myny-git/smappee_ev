# Smappee EV Home Assistant Integration (HACS)

## 🧠 Credits
This is a fork of [`gvnuland/smappee_ev`](https://github.com/gvnuland/smappee_ev), so credits for the initial working version goes to ""@gvnuland"".

[![hacs_badge](https://img.shields.io/badge/HACS-Default-blue.svg?style=flat-square)](https://hacs.xyz)
[![hainstall](https://img.shields.io/badge/dynamic/json?style=flat-square&logo=home-assistant&logoColor=ccc&label=usage&suffix=%20installs&cacheSeconds=15600&url=https://analytics.home-assistant.io/custom_integrations.json&query=$.smappee_ev.total)](https://my.home-assistant.io/redirect/config_flow_start/?domain=smappee_ev) [![GitHub release](https://img.shields.io/github/v/release/myny-git/smappee_ev?style=flat-square)](https://github.com/myny-git/smappee_ev/releases)

<!--
> [!NOTE]  
[![GitHub](https://img.shields.io/badge/Source-GitHub-black?logo=github&style=flat-square)](https://github.com/sponsors/myny-git) // to be set!
[![BuyMeACoffee](https://img.shields.io/badge/Buy%20me%20a%20coffee-donate-yellow?logo=buymeacoffee&style=flat-square)](https://www.buymeacoffee.com/mynygit)  
[![PayPal](https://img.shields.io/badge/Donate-PayPal-blue?logo=paypal&style=flat-square)](https://www.paypal.me/mynygit) 
-->

## 🔧 Features

This custom integration unlocks **more control over your Smappee** charger and connects it directly to Home Assistant.  
It goes far beyond the official integration, which lacks support for the full EV charger API. It is based on the [Smappee API](https://smappee.atlassian.net/wiki/spaces/DEVAPI/overview).

### ✅ Charging Mode Control
- Switch between all official Smappee charging modes:
  - `SMART` – Dynamic smart charging based on usage and pricing
  - `SOLAR` – Charge using only excess solar energy
  - `STANDARD` (also called `NORMAL`) – Fixed current or percentage charging
- Set mode via select entity or dedicated service
- Apply selected mode with **Set Charging Mode** button

### ✅ Direct Charger Control
- Start, Pause, or Stop charging sessions from Home Assistant
- Set fixed charging **percentage** or **current** (in Amps)
- Automatically switches to correct mode based on your selection
- Change Wallbox availability (set available/unavailable)
- **Reload service**: reloads all entries without restarting Home Assistant

### ✅ LED Brightness Control
- Adjust LED ring brightness (%)
- Set via number input and apply via button

### ✅ Charger State Feedback
- Real-time **Session State**:
  - `CHARGING`, `PAUSED`, `SUSPENDED`, etc.
- **EVCC State** for in-depth diagnostics (e.g. state A/B/C/E)

### ✅ Built-in Safeguards & Notes
- Charging mode resets to `NORMAL` when paused — same as in the Smappee app
- User-configurable update interval (seconds) for data refreshes

#### ⚡️ Advanced / Developer Notes
- Polling interval (`update_interval`) can be set in both config flow and options (default 30s)
- All values for current/percentage/brightness are always **integers** (no floats in UI)
- Energy sensor (total kWh counter) is **currently disabled** (can be enabled by uncommenting code in sensor.py and api_client.py)
- Integration tested on:  
  - **Smappee EV Wall Home** (single cable)
  - Should work similarly on other Smappee chargers using the same API

## 📘 More Information
- [EVCC information](./docs/EVCC.md) – Learn how to use these Home Assistant sensors for EVCC.

> ## ⚠️ Important
> This is a HACS custom integration.
> Do **not** try to add this repository as an **add-on** in Home Assistant - it won't work that way.

## 📦 Installation Instructions
### Step 1. Add the Integration via HACS

[![Open your Home Assistant instance and adding repository to HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=myny-git&repository=smappee_ev&category=integration)

1. In Home Assistant, go to **HACS** → **Integrations**.
2. Click the **three-dot menu** (⋮) in the top right → **Custom Repositories**.
3. Add this repository: https://github.com/myny-git/smappee_ev. Set the category to **Integration¨¨.
4. After adding, search for `Smappee EV` in the HACS Integrations list and install it.
5. Restart Home Assistant.

### Step 2. Configure the Integration

During setup, you will be prompted to enter:

- **Client ID** and **Client Secret**  
→ Request these by emailing [support@smappee.com](mailto:support@smappee.com)

- **Username** on the Smappee dashboard
- **Password** on the Smappee dashboard
- **Serial number** of your charging station  
→ You can find it in the Smappee dashboard (go to EV line → click to view serial number)
- **Update interval (seconds)** (optional, default: 30)  
  → Set how frequently Home Assistant fetches data from your wallbox (don't make it too fast, not necessary)

### 🧩 Entities

#### Controls

| Entity                                   | Type     | Description                                                                  |
|-------------------------------------------|----------|------------------------------------------------------------------------------|
| `button.set_charging_mode`                | Button   | Apply the selected charging mode                                             |
| `button.start_charging`                   | Button   | Starts charging using the set percentage                                     |
| `button.pause_charging`                   | Button   | Pauses the current charging session                                          |
| `button.stop_charging`                    | Button   | Stops the current charging session                                           |
| `button.set_led_brightness`               | Button   | Apply the set LED brightness level                                           |
| `select.smappee_charging_mode_<serial>`   | Select   | Choose between `SMART`, `SOLAR`, `NORMAL`, `NORMAL_PERCENTAGE`              |
| `number.smappee_current_limit_<serial>`   | Number   | Set current in Amps for `NORMAL` mode                                        |
| `number.smappee_percentage_limit_<serial>`| Number   | Set percentage limit for `NORMAL_PERCENTAGE` or Start Charging               |
| `number.smappee_led_brightness_<serial>`  | Number   | Brightness percentage used in Set LED Brightness                             |
| `button.set_available`                    | Button   | Make the Wallbox available for use                                           |
| `button.set_unavailable`                  | Button   | Make the Wallbox unavailable for use                                         |

#### Sensors

| Entity                                   | Type    | Description                                                                  |
|-------------------------------------------|---------|------------------------------------------------------------------------------|
| `sensor.session_state_<serial>`           | Sensor  | Current session state (`CHARGING`, `PAUSED`, `SUSPENDED`, ...)              |
| `sensor.evcc_state_<serial>`              | Sensor  | EVCC state of the charger (`A`, `B`, `C`, `E`)                              |
<!--
| `sensor.total_counter`                    | Sensor  | Total energy delivered in kWh (currently disabled, see docs)                |
-->                       |

### 🛠️ Services

| Service                                   | Description                                                                 |
|--------------------------------------------|-----------------------------------------------------------------------------|
| `smappee_ev.set_charging_mode`             | Set mode: `SMART`, `SOLAR`, `NORMAL`, or `NORMAL_PERCENTAGE`                |
| `smappee_ev.start_charging`                | Starts charging with a percentage limit                                      |
| `smappee_ev.pause_charging`                | Pauses current charging session                                             |
| `smappee_ev.stop_charging`                 | Stops the charging session                                                  |
| `smappee_ev.set_available`                 | Makes the Wallbox available                                                 |
| `smappee_ev.set_unavailable`               | Makes the Wallbox unavailable                                               |
| `smappee_ev.set_brightness`                | Sets LED brightness (%) on the Wallbox                                      |
| `smappee_ev.reload`                        | Reloads all Smappee EV entries (no restart required)                        |


> ⚠️ **Note**  
> Like in the Smappee app, pressing **Pause Charging** changes the mode to `NORMAL`.  
> To resume smart charging, manually set the mode again (e.g., `SMART`) and press **Set Charging Mode**.

## 💡 Notes

I built this fork because I own a **Smappee EV Wall Home** and wanted deeper control through Home Assistant.  
The goal is to offer reliable support for charging mode switching and eventually more smart charging controls.
I am also looking into EVCC integration.

Contributions, feedback, or bug reports are very welcome! I am not a programmer, but I'll do my best.

## ☕ Support

If this integration is useful to you, feel free to support its development:

[![BuyMeACoffee](https://img.shields.io/badge/Buy%20me%20a%20coffee-donate-yellow?logo=buymeacoffee&style=flat-square)](https://www.buymeacoffee.com/mynygit)  [![PayPal](https://img.shields.io/badge/Donate-PayPal-blue?logo=paypal&style=flat-square)](https://www.paypal.me/mynygit) 

