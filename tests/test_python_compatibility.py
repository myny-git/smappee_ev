"""Guard integration syntax for Home Assistant installations on Python 3.13."""

import ast
from pathlib import Path


def test_integration_uses_python_313_compatible_syntax():
    """Check every shipped module even when the HA test stack uses newer Python."""
    root = Path(__file__).parents[1] / "custom_components" / "smappee_ev"
    for path in sorted(root.rglob("*.py")):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 13))
