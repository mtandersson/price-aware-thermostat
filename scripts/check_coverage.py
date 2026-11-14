"""Enforce per-module coverage and complete config-flow line/branch coverage.

Run after pytest --cov-branch --cov-report=json:coverage.json.
"""

import json
import sys
from pathlib import Path


def main(path: str) -> int:
    report = json.loads(Path(path).read_text())
    failures = []
    for name, data in report["files"].items():
        if not name.startswith("custom_components/price_aware_thermostat/"):
            continue
        summary = data["summary"]
        if summary["percent_covered"] <= 95:
            failures.append(f"{name}: coverage must exceed 95%")
        if name.endswith("/config_flow.py"):
            if data["missing_lines"] or data.get("missing_branches"):
                failures.append(f"{name}: all lines and branches must be covered")
            if "num_branches" not in summary:
                failures.append("Branch coverage was not measured; use --cov-branch")
    if not any(name.endswith("/config_flow.py") for name in report["files"]):
        failures.append("Config-flow coverage is missing")
    for failure in failures:
        print(failure, file=sys.stderr)
    if not failures:
        print("Every integration module exceeds 95%; config flow has full line/branch coverage")
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "coverage.json"))
