# 📐 Automation Blueprints

## Smappee: Forgot to Scan RFID Badge

Sends a push notification to your phone when your EV has been plugged into the Smappee charger for a configurable amount of time without starting a charging session — a reminder to scan your RFID badge.

### What it does

- **Monitors charger status:** Watches for the `cable_connected` state on your Smappee EVSE sensor.
- **Configurable delay:** Waits a set amount of time (default: 5 minutes) before alerting, to allow for normal badge scanning.
- **Mobile notification:** Sends a push notification to your phone via the Home Assistant Companion app.

### Easy Import

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint URL pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fmyny-git%2Fsmappee_ev%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fforgot_to_scan_rfid_badge.yaml)

### Manual Setup

1. Download `blueprints/automation/forgot_to_scan_rfid_badge.yaml`.
2. Place it in your Home Assistant config directory:

   ```bash
   config/blueprints/automation/forgot_to_scan_rfid_badge.yaml
   ```

3. Go to **Settings → Automations & Scenes → Blueprints**, click **Reload Blueprints**, then **Create Automation**.

---

## Smappee: Charger Status and Sun-Based LED Brightness

Adjusts the Smappee charger LED brightness based on charger availability (2% when available) and sun state with configurable offsets.

### Features

- **Charger-aware:** Sets LED to 2% brightness when the charger status is "available".
- **Sun-triggered fallback:** When not available, adjusts brightness based on sunrise and sunset.
- **Configurable offsets:** Shift the trigger time before or after sunrise/sunset (e.g. `-00:30:00` to trigger 30 minutes early).
- **Configurable brightness:** Set independent brightness percentages for daytime and nighttime.

### Import

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint URL pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fmyny-git%2Fsmappee_ev%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fsun_charger_state_led.yaml)

### Setup

1. Download `blueprints/automation/sun_charger_state_led.yaml`.
2. Place it in your Home Assistant config directory:

   ```bash
   config/blueprints/automation/sun_charger_state_led.yaml
   ```

3. Go to **Settings → Automations & Scenes → Blueprints**, click **Reload Blueprints**, then **Create Automation**.

---

## Smappee: Module Offline Warning

Sends a notification when a Smappee sensor goes offline, indicating a probable loss of connection (e.g. P1 module or network issue).

### What it does

- **Monitors sensor availability:** Watches any Smappee integration sensor for an `unavailable` or `unknown` state.
- **Configurable delay:** Waits a set amount of time (default: 5 minutes) before alerting, to avoid false positives from brief dropouts.
- **Mobile notification:** Sends a push notification with a customisable title and message. Use `{{ delay_minutes }}` in the message body to include the configured delay dynamically.

### Import

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint URL pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fmyny-git%2Fsmappee_ev%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fmodule_offline_warning.yaml)

### Manual Setup

1. Download `blueprints/automation/module_offline_warning.yaml`.
2. Place it in your Home Assistant config directory:

   ```bash
   config/blueprints/automation/module_offline_warning.yaml
   ```

3. Go to **Settings → Automations & Scenes → Blueprints**, click **Reload Blueprints**, then **Create Automation**.
