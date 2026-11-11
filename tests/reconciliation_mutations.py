"""Opt-in, in-memory reconciliation mutations; never edit production source files.

Run a witness with ``pytest -p tests.reconciliation_mutations
--reconciliation-mutant=NAME TEST_NODE``. A killed mutation produces an assertion
failure in an otherwise passing witness. This plugin is absent from normal runs.
"""

import importlib
import inspect
from textwrap import dedent

import pytest

_ROOT = "custom_components.smappee_ev."
_MUTATIONS = {
    "rest_diff": (
        "coordinator",
        "SmappeeStationCoordinator",
        "_async_fetch_data",
        (("StateChanges.between(refresh_start, current).apply(data)", "pass"),),
    ),
    "rest_confirmations": (
        "coordinator",
        "SmappeeStationCoordinator",
        "_async_fetch_data",
        (("self._state_confirmations.since(confirmation_version, current).apply(data)", "pass"),),
    ),
    "rest_dashboard_overlay": (
        "coordinator",
        "SmappeeStationCoordinator",
        "_async_fetch_data",
        (("dashboard_changes.apply(data)", "pass"),),
    ),
    "dashboard_diff": (
        "coordinators.dashboard_merge",
        "DashboardMixin",
        "_async_dashboard_refresh_now",
        (("changed |= concurrent.apply(current)", "pass"),),
    ),
    "dashboard_confirmations": (
        "coordinators.dashboard_merge",
        "DashboardMixin",
        "_async_dashboard_refresh_now",
        (("changed |= confirmed.apply(current)", "pass"),),
    ),
    "staging_copy": (
        "state_updates",
        None,
        "state_staging_copy",
        (("return deepcopy(current)", "return current"),),
    ),
    "mqtt_precedence": (
        "coordinators.session_tracking",
        "SessionTrackingMixin",
        "_mqtt_reports_charger_state",
        (("self.mqtt_transport_connected", "False"),),
    ),
    "reconnect_followup": (
        "mqtt_setup",
        None,
        "_setup_mqtt",
        (
            (
                "while coord._force_smartdevice_refresh and not coord._is_stopping:",
                "if coord._force_smartdevice_refresh and not coord._is_stopping:",
            ),
        ),
    ),
    "session_current_snapshot": (
        "coordinators.session_tracking",
        "SessionTrackingMixin",
        "_async_run_session_refresh",
        (
            (
                "recent_sessions = await self._async_get_recent_sessions()",
                "current = self.data\n            recent_sessions = await self._async_get_recent_sessions()",
            ),
            (
                "self.data.recent_sessions = recent_sessions",
                "current.recent_sessions = recent_sessions",
            ),
        ),
    ),
    "local_publication": (
        "state_updates",
        None,
        "update_connector",
        (("coordinator.async_update_listeners()", "coordinator.async_set_updated_data(current)"),),
    ),
    "rest_station_fields": (
        "coordinators.api_state",
        "StationApiMixin",
        "_merge_station_rest_state",
        (("return replace(\n        prev,", "return replace(\n        rest,"),),
    ),
    "rest_connector_fields": (
        "coordinators.api_state",
        "StationApiMixin",
        "_merge_connector_rest_state",
        (("merged = replace(\n        prev,", "merged = replace(\n        rest_state,"),),
    ),
    "unchanged_mqtt_confirmation": (
        "coordinator",
        "SmappeeStationCoordinator",
        "_set_if_changed",
        (("if confirm:", "if confirm and getattr(obj, attr, None) != value:"),),
    ),
}


def pytest_addoption(parser):
    parser.addoption("--reconciliation-mutant", choices=sorted(_MUTATIONS), default=None)


@pytest.fixture(autouse=True)
def reconciliation_mutation(request, monkeypatch):
    name = request.config.getoption("--reconciliation-mutant")
    if name is None:
        return
    module_name, class_name, method, replacements = _MUTATIONS[name]
    module = importlib.import_module(_ROOT + module_name)
    owner = getattr(module, class_name) if class_name else module
    original = getattr(owner, method)
    source = dedent(inspect.getsource(original))
    for before, after in replacements:
        assert source.count(before) == 1, (name, method, before)
        source = source.replace(before, after)
    namespace = {}
    # Compile only inspected repository code plus the fixed substitutions above.
    code = compile(
        "from __future__ import annotations\n" + source, original.__code__.co_filename, "exec"
    )
    exec(code, module.__dict__, namespace)  # noqa: S102 - controlled test-only mutation
    replacement = namespace[method]
    if isinstance(replacement, staticmethod):
        replacement = replacement.__func__
    # Preserve the function object's identity: modules/tests can already hold
    # aliases imported with `from ... import ...` before this fixture runs.
    monkeypatch.setattr(original, "__code__", replacement.__code__)
