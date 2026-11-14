"""Fail when any integration module is not covered above the quality-scale threshold.

Home Assistant's ``test-coverage`` rule asks for more than 95% in every module,
so a high total cannot hide a poorly tested module.
"""

import argparse
import json
from pathlib import Path
import sys

THRESHOLD = 95.0


def modules_below(report: dict, threshold: float = THRESHOLD) -> dict[str, float]:
    """Return modules whose statement coverage is not above the threshold."""
    return {
        Path(name).as_posix(): data["summary"]["percent_covered"]
        for name, data in report["files"].items()
        if data["summary"]["num_statements"] and data["summary"]["percent_covered"] <= threshold
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="coverage JSON report (pytest --cov-report=json)")
    args = parser.parse_args(argv)
    report = json.loads(args.report.read_text(encoding="utf-8"))
    failing = modules_below(report)
    for name, percent in sorted(failing.items()):
        sys.stdout.write(f"{name}: {percent:.2f}% (must be above {THRESHOLD:.0f}%)\n")
    if failing:
        return 1
    sys.stdout.write(f"All {len(report['files'])} modules are covered above {THRESHOLD:.0f}%.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
