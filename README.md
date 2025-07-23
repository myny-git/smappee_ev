# Smappee EV Home Assistant Integration (HACS)

## 🧠 Credits
This is a fork of [`gvnuland/smappee_ev`](https://github.com/gvnuland/smappee_ev), so credits for the initial working version goes to ""@gvnuland"".

[![hacs_badge](https://img.shields.io/badge/HACS-Default-blue.svg?style=flat-square)](https://hacs.xyz)
[![hainstall](https://img.shields.io/badge/dynamic/json?style=for-the-badge&logo=home-assistant&logoColor=ccc&label=usage&suffix=%20installs&cacheSeconds=15600&url=https://analytics.home-assistant.io/custom_integrations.json&query=$.smappee_ev.total)](https://my.home-assistant.io/redirect/config_flow_start/?domain=smappee_ev)
[![GitHub release](https://img.shields.io/github/v/release/myny-git/smappee_ev?style=flat-square)](https://github.com/myny-git/smappee_ev/releases)

<!--
> [!NOTE]  
[![GitHub](https://img.shields.io/badge/Source-GitHub-black?logo=github&style=flat-square)](https://github.com/sponsors/myny-git) // to be set!
[![BuyMeACoffee](https://img.shields.io/badge/Buy%20me%20a%20coffee-donate-yellow?logo=buymeacoffee&style=flat-square)](https://www.buymeacoffee.com/YOURUSERNAME)  // to be set
[![PayPal](https://img.shields.io/badge/Donate-PayPal-blue?logo=paypal&style=flat-square)](https://www.paypal.me/YOURUSERNAME) 
-->

## 🔧 Features
The original Home Assistant Smappee integration does **not** allow control over the EV charger. This fork adds support for selecting charging modes:

- `SOLAR`
- `SMART`
- `STANDARD` (also known as "normal" mode), where you can set a percentage or a current.

✅ Tested on: **Smappee EV Wall Home**, single cable version.

---

> ## ⚠️ [!IMPORTANT]
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

## 💡 Notes

I built this fork because I own a **Smappee EV Wall Home** and wanted deeper control through Home Assistant.  
The goal is to offer reliable support for charging mode switching and eventually more smart charging controls.
I am also looking into EVCC integration.

Contributions, feedback, or bug reports are very welcome! I am not a programmer, but I'll do my best.

## ☕ Support

If this integration is useful to you, feel free to support its development:

- Buy Me a Coffee: _Coming soon_
- PayPal: _Coming soon_
