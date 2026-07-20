# Changelog

All notable changes for stable releases are documented here.

Non-stable versions are intentionally omitted.

References point to the related GitHub issues, pull requests or discussions
where the bug report, testing notes or design discussion can be found.

## [2026.7.3] - 2026-07-20

- Added `initialize` to the supported charging-state enum and its English,
  Dutch, French and German translations, preventing Home Assistant listener
  errors during charger startup or reconnects.
- Fixed connector power, current and energy sensors remaining `unknown` when a
  Dashboard measurement does not expose a direct connector identifier or
  position. Measurements can now be resolved through an exact, unambiguous
  Dashboard device-name match, with a safe single-connector fallback.
- Centralized car-charger measurement classification and made it accept common
  `CAR_CHARGER` and `CARCHARGER` category variants consistently in discovery,
  live power mapping and diagnostics.
- Reworked multi-location setup so all control locations belonging to one
  physical site are collected and merged before coordinators and MQTT clients
  are created. Physical stations are deduplicated deterministically and each
  physical site now has one routing setup using the complete station map.
- Made MQTT routing station-specific: mapped power routes take priority,
  control-location matching provides the safe fallback, and unresolved power
  measurements are no longer broadcast to every station.
- Correctly route wildcard charger and LED `/devices/updated` topics using the
  payload `deviceUUID` instead of treating `updated` as a device identifier.
- Made MQTT freshness tracking route-aware so only coordinators that received a
  relevant charger or power message are refreshed.
- Expanded diagnostics with power-mapping resolution details, multi-location
  topology and setup counts, configured and observed MQTT routes, coordinator
  identity checks, per-topic traffic, unrouted messages and delivery failures.
  Diagnostic identifiers and topics remain redacted.
- Added regression coverage for multi-location setup order, coordinator
  identity, targeted routing, `/devices/updated`, unload cleanup, ambiguous
  connector names and route-aware freshness. Also removed cyclic-import and
  static-analysis warnings introduced during the diagnostics work.

References:
[Issue #251](https://github.com/myny-git/smappee_ev/issues/251) and
[Issue #252](https://github.com/myny-git/smappee_ev/issues/252).

## [2026.7.2] - 2026-07-16

- Fixed site-level MQTT routing so aggregate consumption, solar and always-on
  power values from child service locations no longer overwrite the parent
  site's values.
- Added an always-on power sensor for site background or standby consumption,
  using the MQTT `alwaysOn` value.
- Defined charging-state and EVSE-status sensors as Home Assistant enum sensors
  with explicit supported options, so their states are translated correctly.
- Added the EVSE `initialize` state and its English, Dutch, French and German
  translations.
- Removed raw MQTT protocol logging from the integration manifest to avoid
  exposing complete MQTT topic UUIDs in logs.

References:
[PR #249](https://github.com/myny-git/smappee_ev/pull/249) and
[PR #250](https://github.com/myny-git/smappee_ev/pull/250).

## [2026.7.1] - 2026-07-14

- Restored `translations/en.json` so Home Assistant can load the English
  translations bundled with this custom integration, as required by the
  [Home Assistant custom integration localization documentation](https://developers.home-assistant.io/docs/internationalization/custom_integration/).

## [2026.7.0] - 2026-07-14

- Migrated the integration from the legacy Smappee API v3 to the Smappee
  Dashboard API v10/v11. Discovery, charger control and configuration now use
  the Dashboard API, while live power, current, energy and charger state
  updates continue to use MQTT.
- Simplified setup to Smappee Dashboard username and password authentication;
  client ID and client secret are no longer required. Added refresh-token
  handling plus reauthentication and reconfiguration flows.
- Refactored the integration into typed API, discovery, runtime, topology,
  entity and coordinator modules.
- Refactored runtime lifecycle management with transactional setup, rollback on
  initialization failures, coordinated shutdown and safer background-task
  cleanup.
- Removed the legacy `set_charging_mode_chargingstations` and
  `pause_charging_chargingstations` actions. The remaining charging actions now
  use the Dashboard device-action path.
- Added `resume_charging`, which restores the selected charging mode or falls
  back to Standard when no previous mode is known.
- Replaced the LED brightness number with a Home Assistant `light` entity and
  associated the LED with the charging-station device.
- Added controls for connector maximum current, minimum solar-surplus
  percentage, site capacity limit, overload-protection limit, offline failsafe
  current and offline charging.
- Added station-level restart support and improved start, pause, stop, resume,
  availability, charging-mode and current-limit validation and error feedback.
- Added connector devices linked to their charging station, clearer connector
  device names and more reliable serial-number and entity identification.
- Added two automation blueprints: an RFID badge reminder and charger-aware,
  sun-based LED brightness control. The RFID blueprint now ignores unavailable
  state transitions and supports Home Assistant notify entities and groups.
- Added formatted charging-session duration attributes, improved session refresh
  scheduling and excluded RFID tokens from session attributes.
- Improved multi-site and multi-account support, including multiple MQTT clients
  per site and explicit validation when a service target is ambiguous.
- Improved availability handling by distinguishing Dashboard availability, MQTT
  connectivity and live data freshness.
- Corrected MQTT freshness tracking, routed aggregate updates, connection
  recovery, heartbeat handling, malformed `jsonContent` parsing and shutdown
  cleanup.
- Corrected Dashboard-derived MQTT channel mapping for grid, solar and connector
  power, current and energy, including routed topics and `activePowerData` versus
  `channelData` payloads for multi-site and multi-station installations.
- Improved API fallback behavior, delayed Dashboard refreshes after writes and
  cancellation of background work during reload or shutdown.
- Preserved Dashboard authentication failures so Home Assistant starts the
  built-in reauthentication flow instead of reporting a generic setup or update
  failure.
- Hardened diagnostics and logging redaction for credentials, UUIDs, MQTT topics
  and runtime data; added a bundled brand-icon fallback.
- Added translated entity names, states and service errors and refreshed Dutch,
  French and German translations. English now correctly uses Home Assistant's
  `strings.json` translation model.
- Standardized charging-mode, charging-state and EVSE-status raw states to
  lowercase values for Home Assistant translation support.
- Updated the entity, service, setup, MQTT and API documentation and added
  contribution, security and code-of-conduct documentation.
- Substantially improved compliance with the Home Assistant Integration Quality
  Scale, expanded the automated regression test suite and enforced a minimum CI
  coverage of 95%.

References:
[PR #186](https://github.com/myny-git/smappee_ev/pull/186),
[PR #195](https://github.com/myny-git/smappee_ev/pull/195),
[PR #196](https://github.com/myny-git/smappee_ev/pull/196),
[PR #197](https://github.com/myny-git/smappee_ev/pull/197),
[PR #199](https://github.com/myny-git/smappee_ev/pull/199),
[PR #200](https://github.com/myny-git/smappee_ev/pull/200),
[PR #201](https://github.com/myny-git/smappee_ev/pull/201),
[PR #202](https://github.com/myny-git/smappee_ev/pull/202),
[PR #204](https://github.com/myny-git/smappee_ev/pull/204),
[PR #205](https://github.com/myny-git/smappee_ev/pull/205),
[PR #209](https://github.com/myny-git/smappee_ev/pull/209),
[PR #213](https://github.com/myny-git/smappee_ev/pull/213),
[PR #214](https://github.com/myny-git/smappee_ev/pull/214),
[PR #218](https://github.com/myny-git/smappee_ev/pull/218),
[PR #226](https://github.com/myny-git/smappee_ev/pull/226),
[PR #236](https://github.com/myny-git/smappee_ev/pull/236) and
[PR #237](https://github.com/myny-git/smappee_ev/pull/237).

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
