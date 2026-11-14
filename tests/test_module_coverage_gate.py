"""The CI gate enforces Home Assistant's per-module coverage rule."""

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[1] / "script" / "check_module_coverage.py"
_spec = importlib.util.spec_from_file_location("check_module_coverage", _SCRIPT)
assert _spec is not None
assert _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def _report(**modules: tuple[int, float]) -> dict:
    return {
        "files": {
            f"custom_components\\smappee_ev\\{name}.py": {
                "summary": {"num_statements": statements, "percent_covered": percent}
            }
            for name, (statements, percent) in modules.items()
        }
    }


def _run(tmp_path: Path, report: dict) -> int:
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return gate.main([str(path)])


def test_one_weak_module_fails_despite_a_high_total(tmp_path, capsys):
    """A well-tested integration must not hide one poorly tested module."""
    report = _report(sensor=(2000, 100.0), mqtt_specs=(60, 93.3))

    assert _run(tmp_path, report) == 1
    assert "custom_components/smappee_ev/mqtt_specs.py: 93.30%" in capsys.readouterr().out


@pytest.mark.parametrize(("percent", "result"), [(95.0, 1), (95.01, 0)])
def test_threshold_requires_more_than_95_percent(tmp_path, percent, result):
    """The quality-scale rule asks for more than 95%, so exactly 95% fails."""
    assert _run(tmp_path, _report(helpers=(100, percent))) == result


def test_modules_without_statements_are_ignored(tmp_path):
    """Empty modules (like a bare __init__.py) report 0% but have nothing to test."""
    assert _run(tmp_path, _report(sensor=(10, 100.0), empty=(0, 0.0))) == 0
