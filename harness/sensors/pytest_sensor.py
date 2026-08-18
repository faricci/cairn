#!/usr/bin/env python3
"""Cairn sensor: tests (quality).

Wraps `pytest` and emits the Cairn Sensor JSON Contract on stdout. This is the
worked QA example: the same deterministic sensor+gate mechanism used for lint
and complexity, applied to a test suite. The wrapper owns the pass/fail
decision (any failed or errored test -> fail); pytest's exit code is only used
to distinguish "suite ran" from "pytest could not run at all".

Usage:
    python pytest_sensor.py [PATH ...]

If `pytest` is not installed, a valid `fail` result with an actionable finding
is emitted (still contract-compliant JSON).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

SENSOR_ID = "tests"

# pytest exit codes: 0 = all passed, 1 = tests failed, 2 = interrupted,
# 3 = internal error, 4 = usage error, 5 = no tests collected.
_NO_TESTS = 5


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", default=["."])
    args = parser.parse_args(argv)
    paths = args.paths or ["."]

    cmd = [
        sys.executable, "-m", "pytest",
        "-q", "--tb=no", "-rfE", "-p", "no:cacheprovider",
        *paths,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except Exception as exc:  # noqa: BLE001 - report any launch failure as a finding
        print(json.dumps(_tool_missing(str(exc))))
        return 0

    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if "No module named pytest" in out or "No module named 'pytest'" in out:
        print(json.dumps(_tool_missing("not installed")))
        return 0

    print(json.dumps(_translate(out, proc.returncode)))
    return 0


def _count(pattern: str, text: str) -> int:
    match = re.search(pattern, text)
    return int(match.group(1)) if match else 0


def _failure_finding(line: str) -> dict:
    """Turn one pytest summary line into a finding.

    Input looks like ``FAILED tests/test_x.py::test_name - AssertionError``.
    """
    body = line.split(" ", 1)[1] if " " in line else line
    loc, _, reason = body.partition(" - ")
    file = loc.split("::", 1)[0] if "::" in loc else loc
    return {
        "file": file or None,
        "line": None,
        "message": f"{loc} - {reason}" if reason else loc,
        "severity": "error",
    }


def _translate(out: str, returncode: int) -> dict:
    failed = _count(r"(\d+) failed", out)
    errors = _count(r"(\d+) error", out)
    score = failed + errors

    if returncode == _NO_TESTS:
        return {
            "sensor": SENSOR_ID,
            "status": "pass",
            "score": 0,
            "threshold": 0,
            "direction": "lower-is-better",
            "findings": [
                {
                    "file": None,
                    "line": None,
                    "message": (
                        "no tests collected - add tests to get real "
                        "coverage of your changes"
                    ),
                    "severity": "info",
                }
            ],
        }

    findings = [
        _failure_finding(line.strip())
        for line in out.splitlines()
        if line.strip().startswith(("FAILED ", "ERROR "))
    ]

    status = "pass" if (returncode == 0 and score == 0) else "fail"
    if status == "fail" and not findings:
        findings.append(
            {
                "file": None,
                "line": None,
                "message": (
                    f"test run failed (pytest exit {returncode}); "
                    "run pytest locally for details"
                ),
                "severity": "error",
            }
        )
    return {
        "sensor": SENSOR_ID,
        "status": status,
        "score": score,
        "threshold": 0,
        "direction": "lower-is-better",
        "findings": findings,
    }


def _tool_missing(detail: str) -> dict:
    return {
        "sensor": SENSOR_ID,
        "status": "fail",
        "findings": [
            {
                "file": None,
                "line": None,
                "message": (
                    f"pytest not available: {detail}. "
                    "Install with `pip install pytest`."
                ),
                "severity": "error",
            }
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
