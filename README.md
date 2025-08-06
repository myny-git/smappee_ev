# Smappee EV Home Assistant Integration (HACS)

---

> ## ⚠️ BREAKING CHANGE (July 2025)
> **This integration has been completely refactored.**
> - **Not compatible with old configs or entities from the original fork**
> - Please remove any previous Smappee EV integrations and re-add/configure from scratch
> - All entity names, services, and options have been improved

---

> [!IMPORTANT]
> This is a personal project developed by me and is not affiliated with, maintained, authorized, or endorsed by Smappee in any way. Use at your own risk.

## 🧠 Credits
This is a fork of [`gvnuland/smappee_ev`](https://github.com/gvnuland/smappee_ev), so credits for the initial working version goes to ""@gvnuland"". 

The codebase has since been completely refactored, resulting in a **new and independent integration**. It is not compatible with configurations or entities from the original fork.

This integration is designed to be **complementary to the official Smappee integration**, offering additional control features for Smappee EV charging.
<div align="center">

[![HACS][hacs-shield]][hacs-url]
[![Release][release-shield]][release-url]
[![Issues][issues-shield]][issues-url]
[![Usage][usage-shield]][usage-url]
[![Hassfest][hassfest-shield]][hassfest-url]
[![License][license-shield]][license-url]
[![Commits][commits-shield]][commits-url]
[![Stars][stars-shield]][stars-url]
[![Pull Requests][pulls-shield]][pulls-url]

</div>

## 🔧 Features

This custom integration unlocks **more control over your Smappee** charger and connects it directly to Home Assistant. It goes far beyond the official integration, which lacks support for the full EV charger API. It is based on the [Smappee API](https://smappee.atlassian.net/wiki/spaces/DEVAPI/overview). 

The main ambition is to integrate these sensors in other energy management systems. The howto is written below. 

### ✅ Charging Mode Control
- Switch between all official Smappee charging modes:
  - `SMART` – Dynamic smart charging based on usage and pricing
  - `SOLAR` – Charge using only excess solar energy
  - `STANDARD` (also called `NORMAL` according to the API) – Fixed current charging
- Set mode via select entity or dedicated service
- Apply selected mode with **Set Charging Mode** button

### ✅ Direct Charger Control
- Start, Pause, or Stop charging sessions from Home Assistant
- Set fixed charging **currents** (in Amps)
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

#### ⚡️ Advanced / Developer Notes
- Polling interval (`update_interval`) can be set in both config flow and options (default 30s)
- All values for currents/brightnesses are always **integers** (no floats in UI)
- Integration tested on:  
  - **Smappee EV Wall Home** (single cable)
  - Should work similarly on other Smappee chargers using the same API

## 📘 Integration into other energy management systems
- [EVCC integration](./docs/EVCC.md) – Learn how to use these Home Assistant sensors for EVCC.
- [openEMS integration](./docs/openEMS.md) - Learn how to use these Home Assistant sensors for openEMS. (under construction)
- [emhass integration](./docs/emhass.md) - Learn how to use these Home Assistant sensors for emhass. (under construction)

> ## ⚠️ Important
> This is a HACS custom integration.
> Do **not** try to add this repository as an **add-on** in Home Assistant - it won't work that way.

## 📦 Installation Instructions
### Step 1. Add the Integration via HACS

> [!NOTE]  
> 🚀 Great news! The integration has been **officially approved by HACS**, no need to add it manually anymore! 🎉

[![Add to my Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=myny-git&repository=smappee_ev&category=integration)

### Method 1: Install via HACS (Recommended)

1. In Home Assistant, go to **HACS** → **Integrations**.
2. Search for `Smappee EV`.
3. Click the **download** button in the right bottom side
4. Restart Home Assistant.

### Method 2: Manual Installation

1. Download the latest release from GitHub.
2. Copy the `smappee_ev` folder to your Home Assistant `custom_components` directory.
3. Restart Home Assistant.

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
More information on the usage of the entities/buttons/services can be found in the [docs](https://github.com/myny-git/smappee_ev/blob/main/docs/HA_integration.md). 

#### Controls

| Entity                                   | Type     | Description                                                                  |
|-------------------------------------------|----------|------------------------------------------------------------------------------|
| `button.smappee_ev_wallbox_set_charging_mode`          | Button   | Apply the selected charging mode                             |
| `button.smappee_ev_wallbox_start_charging`             | Button   | Starts charging using the set percentage                      |
| `button.smappee_ev_wallbox_pause_charging`              | Button   | Pauses the current charging session                           |
| `button.smappee_ev_wallbox_stop_charging`              | Button   | Stops the current charging session                           |
| `button.smappee_ev_wallbox_set_led_brightness`          | Button   | Apply the set LED brightness level                         |
| `select.smappee_ev_wallbox_charging_mode`   | Select   | Choose between `SMART`, `SOLAR`, `NORMAL`              |
| `number.smappee_ev_wallbox_max_charging_speed`  | Number   | Set current in Amps for `NORMAL` mode                                        |         |
| `number.smappee_ev_wallbox_led_brightness`  | Number   | Brightness percentage used in Set LED Brightness                             |
| `number.smappee_ev_wallbox_min_surplus_percentage`  | Number   | Minimum  surplus (%) before enabling charging                             |
| `button.smappee_ev_wallbox_set_available`             | Button   | Make the Wallbox available for use                                |
| `button.smappee_ev_wallbox_set_unavailable`             | Button   | Make the Wallbox unavailable for use                        |
| `switch.smappee_ev_wallbox_evcc_charging_control`             | Button   | EVCC switch to enable/disable charging               |

#### Sensors

| Entity                                   | Type    | Description                                                                  |
|-------------------------------------------|---------|------------------------------------------------------------------------------|
| `sensor.session_state`           | Sensor  | Current session state (`CHARGING`, `PAUSED`, `SUSPENDED`, ...)              |
| `sensor.evcc_state`              | Sensor  | EVCC state of the charger (`A`, `B`, `C`, `E`)                              |
<!--
| `sensor.total_counter`                    | Sensor  | Total energy delivered in kWh (currently disabled, see docs)                |
-->                      
### 🛠️ Services

| Service                                   | Description                                                                 |
|--------------------------------------------|-----------------------------------------------------------------------------|
| `smappee_ev.set_charging_mode`             | Set mode: `SMART`, `SOLAR`, `NORMAL`               |
| `smappee_ev.start_charging`                | Starts charging with a current limit                                      |
| `smappee_ev.pause_charging`                | Pauses this charging session                                             |
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

[![BuyMeACoffee][coffee-shield]][coffee-url]
[![PayPal][paypal-shield]][paypal-url]

<!-- Shields -->

[hacs-shield]: https://img.shields.io/badge/HACS-Default-blue.svg?style=flat-square
[hacs-url]: https://hacs.xyz

[release-shield]: https://img.shields.io/github/v/release/myny-git/smappee_ev?color=green&style=flat-square
[release-url]: https://github.com/myny-git/smappee_ev/releases

[issues-shield]: https://img.shields.io/github/issues/myny-git/smappee_ev?style=flat-square
[issues-url]: https://github.com/myny-git/smappee_ev/issues

[usage-shield]: https://img.shields.io/badge/dynamic/json?style=flat-square&logo=home-assistant&logoColor=ccc&label=usage&suffix=%20installs&cacheSeconds=15600&url=https://analytics.home-assistant.io/custom_integrations.json&query=$.smappee_ev.total
[usage-url]: https://my.home-assistant.io/redirect/config_flow_start/?domain=smappee_ev

[hassfest-shield]: https://img.shields.io/github/actions/workflow/status/myny-git/smappee_ev/validate.yaml?label=Hassfest&style=flat-square
[hassfest-url]: https://github.com/myny-git/smappee_ev/actions/workflows/validate.yaml

[license-shield]: https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square
[license-url]: https://opensource.org/licenses/MIT

[commits-shield]: https://img.shields.io/github/commit-activity/t/myny-git/smappee_ev?style=flat-square
[commits-url]: https://github.com/myny-git/smappee_ev/commits/main

[stars-shield]: https://img.shields.io/github/stars/myny-git/smappee_ev?style=flat-square
[stars-url]: https://github.com/myny-git/smappee_ev/stargazers

[pulls-shield]: https://img.shields.io/github/issues-pr/myny-git/smappee_ev?style=flat-square
[pulls-url]: https://github.com/myny-git/smappee_ev/pulls

[coffee-shield]: https://img.shields.io/badge/Buy%20me%20a%20coffee-donate-yellow?logo=buymeacoffee&style=flat-square
[coffee-url]: https://www.buymeacoffee.com/mynygit

[paypal-shield]: https://img.shields.io/badge/Donate-PayPal-blue?logo=paypal&style=flat-square
[paypal-url]: https://www.paypal.me/mynygit

