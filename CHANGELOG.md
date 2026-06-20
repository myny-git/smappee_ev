# Changelog

All notable changes for stable releases are documented here.

Non-stable versions are intentionally omitted.

References point to the related GitHub issues, pull requests or discussions
where the bug report, testing notes or design discussion can be found.

## [2026.6.5] - 2026-06-12

- Fixed connector-to-station mapping bugs reported in
  [#122](https://github.com/myny-git/smappee_ev/issues/122).
- Added `ConnectorSessionEnergySensor` for current or most recent charging session energy.
- Added session details as sensor attributes.
- Improved code quality for Home Assistant integration standards.

References:
[PR #184](https://github.com/myny-git/smappee_ev/pull/184),
[Discussion #177](https://github.com/myny-git/smappee_ev/discussions/177).

## [2026.6.4] - 2026-06-07

- Promoted the June 2026 testing work to a stable release.
- Added charging current control with 0.1 A precision.
- Added the `set_current` action to set charging current directly in Ampere.
- Cleaned up Smappee API endpoint handling for smartdevices and chargingstations.
- Improved service/action behavior for charging mode and start/pause flows.
- Improved Home Assistant reauthentication, reconfigure, restore and config entry lifecycle handling.
- Improved MQTT state handling, station availability, MQTT connection updates and heartbeat updates.
- Added typed runtime data support and `py.typed`.
- Improved diagnostics redaction and safer OAuth logging.
- Updated translations in English, Dutch, French and German.
- Renamed `pause_charging_smartdevices` to `pause_charging_chargingstations`.
- Normal mode is shown as Standard where applicable.

References:
[Discussion #175](https://github.com/myny-git/smappee_ev/discussions/175),
[Discussion #163](https://github.com/myny-git/smappee_ev/discussions/163),
[Discussion #108](https://github.com/myny-git/smappee_ev/discussions/108).

## [2026.5.4] - 2026-05-30

- Reintroduced the former pause charging behavior as Home Assistant services/actions.
- Updated documentation.
- Added a link to the Discord server.

References:
[Discussion #163](https://github.com/myny-git/smappee_ev/discussions/163).

## [2026.5.3] - 2026-05-29

- Added individual current and voltage values.

## [2026.5.2] - 2026-05-29

- Restored the `start_charging` button.
- Fixed [#170](https://github.com/myny-git/smappee_ev/issues/170).

## [2026.5.1] - 2026-05-25

- Unified charging mode handling through the chargingstations endpoint.
- Preserved backward compatibility with the existing `set_charging_mode` service.
- Updated UI controls to use the stable endpoint and avoid session timeouts.
- Added grid sensor support.
- Restored charging mode after restart.
- Removed stale button entities from previous versions.
- Potentially fixed [#143](https://github.com/myny-git/smappee_ev/issues/143),
  [#103](https://github.com/myny-git/smappee_ev/issues/103),
  [#40](https://github.com/myny-git/smappee_ev/issues/40) and
  [#38](https://github.com/myny-git/smappee_ev/issues/38).

References:
[Discussion #169](https://github.com/myny-git/smappee_ev/discussions/169).

## [2026.5.0] - 2026-05-03

- Fixed charging mode reverting to Normal after Home Assistant restart.
- Fixed incorrect energy sensor values after connector replacement.
- Added rank-based index mapping for power and energy arrays.
- Included contributions from
  [PR #135](https://github.com/myny-git/smappee_ev/pull/135) and
  [PR #148](https://github.com/myny-git/smappee_ev/pull/148).

## [2026.4.0] - 2026-04-04

- Added `smappee_ev.set_charging_mode_chargingstations`.
- Added direct connector mode changes with `NORMAL`, `SMART` and `PAUSED`.
- Added optional limit support for `NORMAL` mode with `AMPERE` or `PERCENTAGE` units.
- Documented the difference between the regular charging mode flow and the chargingstations endpoint.
- Updated README and Home Assistant integration documentation.

References:
[Discussion #2](https://github.com/myny-git/smappee_ev/discussions/2).

## [2026.1.0] - 2026-01-07

- Added Home Assistant 2026.1 compatibility.
- Changed the `aiomqtt` dependency from a fixed version to a supported version range.
- Fixed [#89](https://github.com/myny-git/smappee_ev/issues/89).

## [2025.11.1] - 2025-11-16

- Improved start charging logic and validation.
- Added handling for missing current values by inferring them from the connector client.
- Fixed [#62](https://github.com/myny-git/smappee_ev/issues/62).

## [2025.10.1] - 2025-10-31

- Improved OAuth token refresh error handling.
- Added better exception handling for connection issues.
- Improved logging during token refresh failures.
- Fixed [#47](https://github.com/myny-git/smappee_ev/issues/47).

## [2025.9.2] - 2025-09-04

- Fixed EVSE status and EVCC state sensors showing `None` after Home Assistant restart.
- Added state restoration for EVCC and EVSE sensors.
- Added pytest coverage for the integration.
- Fixed [#26](https://github.com/myny-git/smappee_ev/issues/26).

## [2025.9.1] - 2025-09-03

- Removed the user-facing update interval option.
- Disabled MQTT last seen sensor by default.
- Added async service registration and unregistration.
- Added runtime data based service targeting.
- Added optional `config_entry_id` service parameter.
- Improved diagnostics redaction and MQTT error logging.
- Updated README and translations.
- Included [PR #24](https://github.com/myny-git/smappee_ev/pull/24) and fixed
  [#23](https://github.com/myny-git/smappee_ev/issues/23).

References:
[Discussion #25](https://github.com/myny-git/smappee_ev/discussions/25).

## [2025.8.14] - 2025-08-28

- Standardized entity names across all platforms.
- Added multi-station support.
- Made energy sensors monotonic for Home Assistant Energy Dashboard compatibility.
- Stabilized unique ID generation.
- Improved charging mode select behavior.
- Refactored platform setup for stations and connectors.
- Filtered stations/connectors using metering configuration to avoid duplicate or ghost devices.
- Fixed MQTT energy values being three times too high.
- Disabled MQTT last seen sensor by default.
- Documented required entity and automation review after upgrade.

References:
[#20](https://github.com/myny-git/smappee_ev/issues/20),
[#21](https://github.com/myny-git/smappee_ev/issues/21).

## [2025.8.12] - 2025-08-20

- Moved power, current and energy values to MQTT updates.
- Reduced the need for Modbus for live values.
- Removed unused services and buttons.
- Changed `set_charging_mode` so current is no longer required.
- Improved EVCC state handling, including state `F` and unknown states.
- Added real connector state and heartbeat sensors.
- Improved timestamp display.

References:
[Discussion #4](https://github.com/myny-git/smappee_ev/discussions/4).

## [2025.8.11] - 2025-08-20

- Added live updates from `mqtt.smappee.net`.
- Used the API mainly for initial values, with MQTT providing live updates.
- Added additional linting.
- Fixed max current equal to min current edge case.
- Added EVCC state `F` support and unknown state handling.
- Added real connector state and heartbeat sensors.
- Improved human-readable MQTT update times.
- Removed unused services.

References:
[Discussion #4](https://github.com/myny-git/smappee_ev/discussions/4).

## [2025.8.10] - 2025-08-14

- Fixed the charging speed slider not updating immediately after EVCC changes.
- Mirrored EVCC current changes locally so Home Assistant, EVCC and the Smappee app stay consistent.

## [2025.8.9] - 2025-08-13

- Fixed dynamic slider range and max-current handling.
- Fixed [#14](https://github.com/myny-git/smappee_ev/issues/14).
- Cleaned code with ruff and linting.

## [2025.8.8] - 2025-08-12

- Added `data.py` and `coordinator.py` for better Home Assistant architecture alignment.
- Centralized entity state through the coordinator.
- Improved entity rename compatibility.
- Improved EVCC switch logic.
- Fixed charging current slider persistence.
- Fixed missing `_refresh` method errors.
- Fixed setup unpacking errors.
- Disabled switching to Normal when pausing.
- Cleaned up service registration.
- Updated README, EVCC and Home Assistant integration docs.

## [2025.8.7] - 2025-08-11

- Added support for single and dual connector chargers.
- Generated entities per connector.
- Renamed entities to include connector number.
- Optimized services and removed reload.
- Switched to Home Assistant aiohttp sessions.
- Included minor fixes.

References:
[Discussion #7](https://github.com/myny-git/smappee_ev/discussions/7).

## [2025.8.6] - 2025-08-06

- Restored unavailable state handling.
- Fixed [#9](https://github.com/myny-git/smappee_ev/issues/9).

## [2025.8.5] - 2025-08-06

- Added minimum surplus percentage slider.
- Allowed control of the required solar surplus percentage before charging starts.
- Documented known Smappee app/dashboard refresh behavior for the new setting.

References:
[Discussion #1](https://github.com/myny-git/smappee_ev/discussions/1).

## [2025.8.4] - 2025-08-05

- Added EVCC switch integration.
- Added `switch.charging_control` to start and pause charging from Home Assistant.
- Grouped the switch under the Smappee EV Wallbox device.

## [2025.8.3] - 2025-08-04

- Replaced percentage-based charging logic with unified current-based control.
- Added `SmappeeCombinedCurrentSlider`.
- Updated `start_charging` to accept current in Amps only.
- Updated the start charging button to use the combined slider.
- Removed legacy percentage/current limit entity dependencies.
- Added callbacks to sync percentage and current values.
- Updated translations and service definitions.
- Cleaned up services, entities and buttons.

## [2025.8.2] - 2025-08-01

- Updated polling behavior.
- Fixed sensor names and related issues.
- Referenced
  [gvnuland/smappee_ev#16](https://github.com/gvnuland/smappee_ev/issues/16).

References:
[Discussion #5](https://github.com/myny-git/smappee_ev/discussions/5).

## [2025.8.1] - 2025-08-01

- Fixed reload so services are reloaded correctly.

## [2025.7.11] - 2025-07-30

- Added HACS availability.
- Added state synchronization for number and select entities.
- Made button entities call services for consistent automation and UI behavior.
- Added callback mechanism for backend updates.
- Cleaned API state handling and polling behavior.
- Added central service registration through `services.py`.
- Refactored callback and state logic.

## [2025.7.10] - 2025-07-27

- Completed a major async refactor and modernization.
- Reworked the config flow.
- Added configurable update interval.
- Made controls and sensors integer-only.
- Removed the energy counter sensor.
- Improved entity naming.
- Improved service handling.
- Aligned API usage with Smappee documentation.
- Required removing and re-adding the integration because of breaking changes.

## [2025.7.9] - 2025-07-27

- Refactored the integration structure.
- Added configurable update interval.
- Made controls and sensors integer-only.
- Reworked config flow.
- Removed the total kWh energy counter.
- Improved entity naming.
- Added more API calls for charging operations.
- Required removing and re-adding the integration because of breaking changes.

## [2025.7.8] - 2025-07-25

- Expanded charging mode selection with `SMART`, `SOLAR`, `NORMAL` and `NORMAL_PERCENTAGE`.
- Improved start, pause and stop handling.
- Added LED brightness control.
- Added charger available/unavailable handling.
- Improved error logging and robustness.
- Added EVCC state sensor.
- Added button entities for charging profile changes.

## [2025.7.6] - 2025-07-24

- Extended charging mode selection with `SMART`, `SOLAR`, `NORMAL` and `NORMAL_PERCENTAGE`.
- Improved pause and stop charging behavior with automatic mode reset.
- Improved error handling and logging.
- Added EVCC state sensor.
- Added charging profile button.

## [2025.7.4] - 2025-07-24

- Extended charging mode selection with `SMART`, `SOLAR`, `NORMAL` and `NORMAL_PERCENTAGE`.
- Improved pause and stop charging behavior with automatic mode reset.
- Improved error handling and logging.
- Added EVCC state sensor.
- Added charging profile button.

## [2025.7.3] - 2025-07-24

- Extended charging mode selection with `SMART`, `SOLAR`, `NORMAL` and `NORMAL_PERCENTAGE`.
- Improved pause and stop charging behavior with automatic mode reset.
- Improved error handling and logging.
- Added charging profile button.

## [2025.7.2] - 2025-07-24

- Extended charging mode selection with `SMART`, `SOLAR`, `NORMAL` and `NORMAL_PERCENTAGE`.
- Improved pause charging behavior with automatic mode reset.
- Improved error handling and logging.
- Added charging profile button.

## [2025.7.1] - 2025-07-23

- Initial HACS release of the Smappee EV integration for Home Assistant.
- Added charging mode selection for `SMART`, `SOLAR` and `STANDARD`.
- Added support for EV Wall Home with one cable.
- Added charging profile button.
- Added number entities for current and percentage limits.

References:
[Discussion #6](https://github.com/myny-git/smappee_ev/discussions/6).
