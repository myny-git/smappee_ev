# Smappee EV 
Smappee EV Home Assistant Integration (HACS)

## Credits
This is a fork of gvnuland / smappee_ev, so credits for the initial working version of @gvnuland.

[![hacs_badge](https://img.shields.io/badge/HACS-Default-blue.svg?style=flat-square)](https://hacs.xyz)
[![hainstall](https://img.shields.io/badge/dynamic/json?style=for-the-badge&logo=home-assistant&logoColor=ccc&label=usage&suffix=%20installs&cacheSeconds=15600&url=https://analytics.home-assistant.io/custom_integrations.json&query=$.smappee_ev.total)](https://my.home-assistant.io/redirect/config_flow_start/?domain=smappee_ev)
<!--
> [!NOTE]  
[![GitHub](https://img.shields.io/badge/Source-GitHub-black?logo=github&style=flat-square)](https://github.com/sponsors/myny-git) // to be set!
[![BuyMeACoffee](https://img.shields.io/badge/Buy%20me%20a%20coffee-donate-yellow?logo=buymeacoffee&style=flat-square)](https://www.buymeacoffee.com/YOURUSERNAME)  // to be set
[![PayPal](https://img.shields.io/badge/Donate-PayPal-blue?logo=paypal&style=flat-square)](https://www.paypal.me/YOURUSERNAME) 
-->
> [!NOTE]
As the original Home Assistant integration of Smappee does not allow to control the EV charger, I updated the fork to include following charging modes to home assistant:
> **SOLAR / SMART and STANDARD**
>  
>  PS: I own an EV Wall Home, with 1 cable. This is what I used for testing.
>

> [!IMPORTANT]
> This is a HACS custom integration. Don't try to add this repository as an add-on in Home Assistant.

## Installation Instructions (3 Steps)
### Step 1. HACS add the Integration

[![Open your Home Assistant instance and adding repository to HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=myny-git&repository=smappee_ev&category=integration)

1. In HA HACS, you need to add a new custom repository (via the 'three dots' menu in the top right corner).
2. Enter https://github.com/marq24/ha-fordpass as the repository URL (and select  the type `Integration`).
3. After adding the new repository, you can search for `fordpass` in the search bar.
4. Important there is already a default HACS fordpass integration — Please make sure to select the 'correct' one with the description: _FordPass integration for Home Assistant [fork optimized for EV's & EVCC]_.
5. Install the 'correct' (aka 'this') fordpass integration (v2025.5.0 or higher).
6. Restart HA.
