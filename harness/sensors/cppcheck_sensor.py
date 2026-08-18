#!/usr/bin/env python3
"""Cairn sensor: lint-cpp (maintainability, C++).

Wraps `cppcheck` and emits the Cairn Sensor JSON Contract on stdout. The wrapper
script is Python, but it analyses C++ -- it only shells out to the native tool
and translates its output. This is the pattern for any non-Python stack: pick a
tool, run it, map its findings to the one contract.

cppcheck can emit machine-readable XML on stderr with
`--xml --xml-version=2`, which is what we parse. The wrapper owns the pass/fail
decision: any error/warning-severity diagnostic -> fail.

Usage:
    python cppcheck_sensor.py [PATH ...]

If `cppcheck` is not installed, a valid `fail` result with an actionable finding
is emitted (still contract-compliant JSON).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import xml.etree.ElementTree as ET

SENSOR_ID = "lint-cpp"

# cppcheck severities we count as failures; "information"/"style" are advisory.
FAIL_SEVERITIES = {"error", "warning"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", default=["."])
    parser.add_argument(
        "--enable",
        default="warning",
        help="cppcheck --enable value, e.g. warning,style,performance",
    )
    args = parser.parse_args(argv)
    paths = args.paths or ["."]

    cmd = [
        "cppcheck",
        "--enable=" + args.enable,
        "--xml",
        "--xml-version=2",
        "--quiet",
        *paths,
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        print(json.dumps(_tool_missing("cppcheck not on PATH")))
        return 0
    except Exception as exc:  # noqa: BLE001 - report any launch failure as a finding
        print(json.dumps(_tool_missing(str(exc))))
        return 0

    # cppcheck writes findings as XML on stderr.
    xml_text = (proc.stderr or "").strip()
    if not xml_text:
        print(json.dumps(_translate([])))
        return 0

    try:
        errors = _parse_xml(xml_text)
    except ET.ParseError as exc:
        print(json.dumps(_tool_missing(f"could not parse cppcheck output: {exc}")))
        return 0

    print(json.dumps(_translate(errors)))
    return 0


def _parse_xml(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    out = []
    for error in root.iter("error"):
        location = error.find("location")
        out.append(
            {
                "file": location.get("file") if location is not None else None,
                "line": _to_int(location.get("line")) if location is not None else None,
                "severity": error.get("severity", "warning"),
                "message": error.get("msg", ""),
                "id": error.get("id", "?"),
            }
        )
    return out


def _translate(errors: list[dict]) -> dict:
    findings = []
    fails = 0
    for e in errors:
        sev = e["severity"]
        is_fail = sev in FAIL_SEVERITIES
        fails += int(is_fail)
        findings.append(
            {
                "file": e["file"],
                "line": e["line"],
                "message": f"{e['id']}: {e['message']}",
                "severity": "warning" if is_fail else "info",
            }
        )
    return {
        "sensor": SENSOR_ID,
        "status": "fail" if fails else "pass",
        "score": fails,
        "threshold": 0,
        "direction": "lower-is-better",
        "findings": findings,
    }


def _to_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _tool_missing(detail: str) -> dict:
    return {
        "sensor": SENSOR_ID,
        "status": "fail",
        "findings": [
            {
                "file": None,
                "line": None,
                "message": (
                    f"cppcheck not available: {detail}. Install it from "
                    "https://cppcheck.sourceforge.io/ or your package manager."
                ),
                "severity": "error",
            }
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
