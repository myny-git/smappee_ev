"""Architecture import guards for the integration package."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[1] / "custom_components" / "smappee_ev"
PACKAGE = "custom_components.smappee_ev"


def _module_imports(path: Path, module_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    package_parts = module_name.split(".")[:-1]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
            continue

        if isinstance(node, ast.ImportFrom):
            if node.level:
                base_parts = package_parts[: len(package_parts) - node.level + 1]
                if node.module:
                    base_parts.extend(node.module.split("."))
                base = ".".join(base_parts)
            else:
                base = node.module or ""
            if base:
                imports.add(base)
                imports.update(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")

    return imports


def test_state_model_has_no_imports_back_to_runtime_or_platform_layers():
    imports = _module_imports(
        PACKAGE_ROOT / "models" / "state.py",
        f"{PACKAGE}.models.state",
    )

    forbidden = {
        f"{PACKAGE}.__init__",
        f"{PACKAGE}.coordinator",
        f"{PACKAGE}.models.runtime_data",
        f"{PACKAGE}.binary_sensor",
        f"{PACKAGE}.button",
        f"{PACKAGE}.diagnostics",
        f"{PACKAGE}.entity",
        f"{PACKAGE}.light",
        f"{PACKAGE}.number",
        f"{PACKAGE}.select",
        f"{PACKAGE}.sensor",
        f"{PACKAGE}.services",
        f"{PACKAGE}.switch",
    }

    assert imports.isdisjoint(forbidden), sorted(imports & forbidden)


def test_coordinator_does_not_import_runtime_data_models():
    imports = _module_imports(PACKAGE_ROOT / "coordinator.py", f"{PACKAGE}.coordinator")

    assert f"{PACKAGE}.models.runtime_data" not in imports


def test_runtime_data_does_not_import_mqtt_setup():
    """Keep MQTT diagnostics models from recreating the CodeQL import cycle."""
    imports = _module_imports(
        PACKAGE_ROOT / "models" / "runtime_data.py",
        f"{PACKAGE}.models.runtime_data",
    )

    assert f"{PACKAGE}.mqtt_setup" not in imports
