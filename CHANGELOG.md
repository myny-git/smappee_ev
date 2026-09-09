# Changelog

All notable changes for stable releases are documented here.

Non-stable versions are intentionally omitted.

References point to the related GitHub issues, pull requests or discussions
where the bug report, testing notes or design discussion can be found.

## [2026.9.1] - 2026-09-09

This release keeps MQTT monitoring available during temporary Smappee Dashboard
outages and restores normal operation automatically when Dashboard recovers.
It also improves charging-session status, connector targeting and measurement
handling.

### Monitoring during Dashboard outages

- After successful setup, the integration saves MQTT connection settings and
  device mappings locally. A later reload or Home Assistant restart can use this
  configuration to resume MQTT monitoring during Dashboard maintenance,
  connection failures, timeouts, HTTP 429 rate limits or server errors.
- Added a diagnostic **Connection mode** sensor. In `mqtt_only` mode, live MQTT
  measurements remain available while Dashboard controls and REST-only entities
  are unavailable. Service actions explain why control is temporarily blocked.
- Cached metadata is not presented as live telemetry. In `mqtt_only` mode,
  measurements require fresh messages and become unavailable after a broker
  disconnect or five minutes without matching data.
- Dashboard recovery runs in the background with increasing retry delays. Once
  discovery and REST access to the expected stations and connectors recover, an
  automatic integration reload restores normal operation. This briefly
  interrupts MQTT. Recovery probes do not start duplicate MQTT clients.
- Temporary failures of individual live REST endpoints no longer prevent saving
  valid bootstrap configuration. Incomplete discovery preserves an existing
  valid cache, and recovery checks use the expected connector topology.
- HTTP 408 and 429 are handled as transient failures. Dashboard requests and
  recovery attempts respect `Retry-After`, including delays over ten minutes.
  Genuine authentication failures retain Home Assistant's reauthentication flow.

### Bug fixes

- Fixed stale `PAUSED` charging modes overriding explicit session status. Active,
  paused and finished detection now use the same priority: session state, then
  status/cause, then the charging mode and fallback state.
- Resumed sessions return to the five-minute refresh interval. Explicit finished
  or idle states override stale pause flags, stop active-session polling when no
  other sessions remain active, clear stale power/current readings and schedule
  final session refreshes after 30 seconds, two minutes and five minutes.
- Unknown charging modes no longer default to `standard`. Explicitly unknown
  MQTT modes clear the previous selection; partial messages without mode fields
  preserve the last known selection.
- Continuous MQTT updates no longer postpone REST polling, allowing REST-only
  connector settings to recover without a manual reload.
- Fixed a disconnect race where a REST refresh could restore an outdated MQTT
  connection status. The disconnect state is now applied before starting the
  fallback refresh.
- Power, current, voltage and energy measurements are processed independently,
  including separately configured measurement topics. Missing, invalid or
  incomplete mapped groups preserve previous values instead of introducing
  synthetic zeros; valid single-phase voltage readings remain supported.
- Connector services now reject ambiguous matches instead of silently selecting
  the first station. Added optional `station_serial` targeting for start, pause,
  stop, resume, charging-mode and current-limit actions.
- Capacity and overload settings now use stable site-level unique IDs independent
  of station ordering. Existing registry entries are migrated while preserving
  entity IDs and user customizations. Pre-existing duplicates are retained with
  a warning rather than deleted automatically.
- Failed Dashboard writes now raise an error when authentication is unavailable,
  preventing settings from appearing successfully changed when no write occurred.
- Current-range validation now rejects negative minimums and nonpositive
  maximums, including invalid previous ranges. Valid `0-32 A` and fixed positive
  ranges such as `6-6 A` remain supported.
- Repaired German and French connector-selection translations.

### Upgrade notes

- Outage startup requires a valid configuration saved by a successful setup with
  this version, plus a reachable MQTT broker. Without that cache, Home Assistant
  retries setup normally. Expired MQTT credentials or changed device mappings
  can prevent monitoring until Dashboard returns; missed data is not backfilled.
- Automations targeting connector numbers shared by multiple stations must add
  `station_serial`. Automations reading the charging-mode select should handle
  `unknown` when no supported mode can be determined.

### Documentation and maintenance

- Documented outage monitoring, recovery, connector targeting and measurement
  handling. Consolidated transient API errors under a shared exception class.
- Expanded regression coverage for cached startup, recovery, rate limiting,
  service targeting, registry migration, partial measurements and session state.
- Added a reconnect regression covering the MQTT transport, routing, coordinators
  and Home Assistant entities: disconnect, cloud/network recovery, reconnect and
  restoration without reload. It verifies both preservation and refresh of
  `min_surpluspct`, using simulated broker and cloud responses.
- Validation: 904 tests passed, together with Ruff, mypy and formatting checks.

## [2026.9.0] - 2026-09-06

This release improves charging-current control, compatibility with Home Assistant
and error reporting during Smappee Dashboard maintenance. It includes all changes
tested in `2026.8.0-beta.0` through `2026.8.0-beta.2`, plus subsequent fixes.

### Bug fixes

- Starting charging from the button or `smappee_ev.start_charging` now preserves
  the connector's configured current limit instead of resetting it to the maximum.
  The last reported percentage is preferred, with the Ampere setpoint as a
  fallback. When no setpoint is known, the existing 100% fallback is retained.
  See [PR #292](https://github.com/myny-git/smappee_ev/pull/292).
- REST polling can now correct a stale selected-current value from the reported
  percentage limit in Standard mode, while preserving optimizer-driven behavior
  in Smart and Solar modes.
- Preserved known connector settings when partial REST updates omit the minimum
  solar-surplus percentage, support-grid setting or current bounds. Dashboard
  property parsing now handles both direct values and typed value lists.
- Hardened minimum/maximum current handling across REST, Dashboard and MQTT
  updates. Missing or invalid bounds no longer replace a valid known range, and
  partial or disjoint range updates keep the connector limits coherent.
- Fixed entity ID generation to follow Home Assistant device naming conventions,
  including user-defined device names. Existing entity IDs and unique IDs remain
  unchanged; newly generated or regenerated IDs use the standard naming rules.
- Updated device relationships to use `via_device_id` where supported, with a
  `via_device` fallback for older Home Assistant registry APIs. Centralized
  site-to-station-to-connector registration and preserved device IDs across
  repeated setup. Legacy LED-device cleanup still respects shared devices on
  older Home Assistant versions.
- Recognized the explicit Smappee Dashboard maintenance notice, case-insensitively,
  before parsing login and token-refresh responses as JSON, including responses
  with HTTP 200. HTTP 502/503 alone is not treated as proof of maintenance.
- Replaced the misleading setup failure during recognized maintenance with a
  translated reason in Settings > Devices & services:
  "Smappee Dashboard under maintenance. Home Assistant will retry automatically."
  Setup continues to retry without starting reauthentication or changing stored
  credentials. Maintenance is logged once at WARNING and successful
  authentication after maintenance once at INFO, without logging response HTML
  or credentials. Existing handling of genuine authentication and other server
  errors is preserved.

### New blueprint

- Added **Smappee: Module Offline Warning**, which notifies a selected notify
  entity or group when a monitored Smappee sensor remains `unavailable` or
  `unknown` for a configurable delay. Notification text is customizable.
  See [PR #281](https://github.com/myny-git/smappee_ev/pull/281).

### Documentation and maintenance

- Added instructions for using connector energy consumption in the Home Assistant
  Energy Dashboard under **Individual devices**.
- Consolidated blueprint documentation in `docs/blueprints.md` and updated the
  start-charging action documentation to explain current-limit preservation.
- Modernized background reauthentication to use Home Assistant's supported
  config-entry helper.
- Strengthened typing and static analysis across API, coordinator, entity and
  runtime code, aligned checks with Home Assistant's mypy settings, and updated
  the Home Assistant test stack and GitHub Actions dependencies.
- Expanded regression coverage for partial connector updates, current ranges,
  charging setpoints, entity naming, device-registry compatibility, maintenance
  detection, setup retries and recovery.

Thanks to [@striekels](https://github.com/striekels) for the charging-current fix
in PR #292 and [@geertmeersman](https://github.com/geertmeersman) for the offline
warning blueprint and documentation in PR #281.

## [2026.8.0] - 2026-08-05

This release improves charging-session state handling and connector measurements
after a charging session ends.

### Bug fixes

- Fixed Home Assistant `ValueError` errors caused by restoring unsupported
  `unknown` or `unavailable` enum states. Restored EVSE states are normalized and
  validated before use.
  Fixes [#268](https://github.com/myny-git/smappee_ev/issues/268) via
  [PR #269](https://github.com/myny-git/smappee_ev/pull/269).
- Fixed connector power and current readings remaining at their last measured
  values when charging is stopped from the vehicle. Connector power is reset
  when the session ends, even without a final zero-power MQTT measurement.
  Fixes [#270](https://github.com/myny-git/smappee_ev/issues/270) via
  [PR #272](https://github.com/myny-git/smappee_ev/pull/272).
- Fixed the charging-state sensor returning to `initialize` after a completed
  session. REST updates without a meaningful charging state no longer overwrite
  the more recent MQTT state.
  Fixes [#271](https://github.com/myny-git/smappee_ev/issues/271) via
  [PR #273](https://github.com/myny-git/smappee_ev/pull/273).

### Documentation and maintenance

- Updated EVCC documentation to use the current Home Assistant entity ID naming
  scheme and clarified charging-station versus service-location serial numbers.
  See [PR #257](https://github.com/myny-git/smappee_ev/pull/257).
- Grouped related Dependabot updates for the Home Assistant test stack and
  GitHub Actions dependencies.

Thanks to [@geertmeersman](https://github.com/geertmeersman) for investigating and
fixing the charging issues in PRs #269, #272 and #273, and to
[@walterbrebels](https://github.com/walterbrebels) for the EVCC documentation.

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
