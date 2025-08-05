### ℹ️ Smappee EV usage and Entity Overview in Home Assistant

This integration exposes a variety of entities, buttons, and services that allow you to control and monitor your **Smappee EV Wallbox** via Home Assistant, or to be used in other third party EMS systems, such as [EVCC](https://github.com/myny-git/smappee_ev/blob/main/docs/EVCC.md), [emhass](https://github.com/myny-git/smappee_ev/blob/main/docs/emhass.md), and [openEMS](https://github.com/myny-git/smappee_ev/blob/main/docs/openEMS.md). Below you'll find a detailed explanation of each component and how to use them effectively in your automations, scripts, or dashboards.

These entities are based on the [Smappee API](https://smappee.atlassian.net/wiki/spaces/DEVAPI/overview). 

### 🛠️ Services

This integration firstly creates several services, which can be called directly in automations, scripts, or the Developer Tools → Actions (UI) in Home Assistant.

<img width="821" height="336" alt="image" src="https://github.com/user-attachments/assets/050c0e51-ad84-4f23-a352-4cefdb2b339d" />


- **`smappee_ev.set_charging_mode`**  
Sets the desired charging mode. You must provide a `mode` parameter with one of the following values: `SMART`, `SOLAR` or `NORMAL`.
At first, there are in fact only 3 modes in your app or on the dashboard: standard (link to NORMAL), smart, solar. The standard mode does not have smart features, you can only set the charging speed or current.. In this mode, you can set the current limit (in A) in integer values. Take care, this is NOT the max current at your connector, as that's a fixed setting, depending on your setup. The programmed current is the max current you allow in THIS mode.

<img width="434" height="391" alt="image" src="https://github.com/user-attachments/assets/83f792df-efdb-45e6-b28d-c2eac2a43019" />

- **`smappee_ev.start_charging`**  
Starts a charging session using a **current limit** parameter.  Requesting this multiple times with different percentage levels has an impact, but you need to refresh your screen, or switch to another tab (like Smart) and return.

- **`smappee_ev.pause_charging`**  
Pauses the currently active charging session and returns to the basic standard screen. 
Like in the Smappee app, pressing Pause Charging changes the mode to NORMAL.
To resume smart charging, manually set the mode again (e.g., SMART) and press Set Charging Mode.

- **`smappee_ev.stop_charging`**  
Stops the charging session entirely. 

- **`smappee_ev.set_available`**  
Makes the Wallbox available for charging again (if it was marked unavailable).

- **`smappee_ev.set_unavailable`**  
Makes the Wallbox unavailable (e.g., for manual control or maintenance purposes or perhaps holiday mode).

- **`smappee_ev.set_brightness`**  
Sets the LED brightness on the Wallbox. Requires a `brightness` parameter (0–100) in percentage. Take care, you don't see an immediate update in your app, you have to exit this screen (e.g. by going to the home page) and return, or refresh the screen. Afterwards, your programmed LED brightness value will appear.

<img width="338" height="170" alt="image" src="https://github.com/user-attachments/assets/2fb91c12-55fd-404b-be3c-0ba28e947d12" />

- **`smappee_ev.reload`**  
Reloads all Smappee EV Wallbox entries without requiring a restart of Home Assistant. Useful when reloading config or fixing communication.

### 🔘 Buttons and numbers
This integration also created a few buttons and number entities, which make use of aforementioned services. You can use them in your Home Assistant dashboards.

- **`select.smappee_ev_wallbox_charging_mode`**  
Allows you to choose between the following charging modes:
  - `SMART`: Balances grid and solar energy
  - `SOLAR`: Charges using solar power only
  - `NORMAL`: Charges at a fixed current limit (with the `max_charging_speed` entity)

- **`number.smappee_ev_wallbox_max_charging_speed`**  
Defines the current (in Amps) to be used when operating in `NORMAL` mode (aka Standard). Adjust this to match your desired charging speed. Some examples:
#### 🚗 Charging Power Table

| Current (A) | Power (1 phase, kW) | Power (3 phases, kW) |
|-------------|----------------------|------------------------|
| 6 A         | 1.38 kW              | 4.15 kW                |
| 8 A         | 1.84 kW              | 5.54 kW                |
| 10 A        | 2.30 kW              | 6.92 kW                |
| 16 A        | 3.68 kW              | 11.07 kW               |
| 24 A        | 5.52 kW              | 16.61 kW               |
| 32 A        | 7.36 kW              | 22.14 kW               |

- **`number.smappee_ev_wallbox_led_brightness`**  
Sets the desired brightness level for the Wallbox LEDs, from 0 to 100%.
   
- **`button.smappee_ev_wallbox_set_charging_mode`**  
Applies the currently selected `charging mode` from the select entity, and in case of NORMAL, it uses the current limit of the corresponding charging speed entity. Use this after changing the mode to activate it on the Wallbox.

- **`button.smappee_ev_wallbox_start_charging`**  
Starts a charging session using the value set in `number.smappee_ev_wallbox_max_charging_speed`. Pressing multiple times with different current levels has an impact, but you need to refresh your screen!

- **`button.smappee_ev_wallbox_pause_charging`**  
Pauses the ongoing charging session. Charging can later be resumed.
Like in the Smappee app, pressing Pause Charging changes the mode to NORMAL.
To resume smart charging, manually set the mode again (e.g., SMART) and press Set Charging Mode.

- **`button.smappee_ev_wallbox_stop_charging`**  
Stops the current charging session entirely. Useful for ending sessions manually or through automations. 

- **`button.smappee_ev_wallbox_set_led_brightness`**  
Applies the brightness level set in `number.smappee_ev_wallbox_led_brightness` to the Wallbox LEDs.

- **`button.smappee_ev_wallbox_set_available`**  
Marks the Wallbox as available for use. Required before charging can start if the Wallbox was previously marked as unavailable.

- **`button.smappee_ev_wallbox_set_unavailable`**  
Marks the Wallbox as unavailable. This can be used to disable charging when not in use or during maintenance.

### 📈 Sensor Entities

**`sensor.session_state`**  
Reports the current session state. Possible values include:
- `CHARGING`: An active charging session is ongoing.
- `SUSPENDED`: Charging is suspended, e.g., due to insufficient solar power or limits, or when you PAUSED the charging.

**`sensor.evcc_state`**  
Displays the EVCC (Electric Vehicle Communication Controller) state of the Wallbox, following IEC 61851:
- `A`: No vehicle connected
- `B`: Vehicle connected but not ready
- `C`: Vehicle connected and ready for charging
- `E`: Error state

### EVCC specific entity

**`switch.smappee_ev_wallbox_evcc_charging_control`**  
The integration of EVCC requires a button, which:
- `turn_on`: call internally the service start_charging with current limit 6A.
- `turn_off`: call internally the service pause_charging.
Please see the [EVCC](https://github.com/myny-git/smappee_ev/blob/main/docs/EVCC.md) documentation for the usage.
