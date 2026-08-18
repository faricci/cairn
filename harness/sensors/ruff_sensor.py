#!/usr/bin/env python3
"""Cairn sensor: lint (maintainability).

Wraps `ruff` and emits the Cairn Sensor JSON Contract on stdout. Ruff already
speaks JSON (`ruff check --output-format json`), so this is a thin translation.
The wrapper owns the pass/fail decision: any reported diagnostic -> fail.

Usage:
    python ruff_sensor.py [PATH ...] [-- <extra ruff args>]

If `ruff` is not installed, a valid `fail` result with an actionable finding is
emitted (still contract-compliant JSON).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

SENSOR_ID = "lint"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", default=["."])
    parser.add_argument(
        "--select", default=None, help="ruff rule selection, e.g. E,F,I"
    )
    args = parser.parse_args(argv)
    paths = args.paths or ["."]

    cmd = [sys.executable, "-m", "ruff", "check", "--output-format", "json"]
    if args.select:
        cmd += ["--select", args.select]
    cmd += paths

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except Exception as exc:  # noqa: BLE001 - report any launch failure as a finding
        print(json.dumps(_tool_missing(str(exc))))
        return 0

    out = (proc.stdout or "").strip()
    # ruff exits non-zero when it finds issues; that is expected. Only treat an
    # empty stdout with a non-zero exit as a real tool failure.
    if not out:
        if proc.returncode not in (0, 1):
            print(json.dumps(_tool_missing(proc.stderr.strip() or "ruff failed")))
            return 0
        out = "[]"

    try:
        diagnostics = json.loads(out)
    except json.JSONDecodeError as exc:
        print(json.dumps(_tool_missing(f"could not parse ruff output: {exc}")))
        return 0

    print(json.dumps(_translate(diagnostics)))
    return 0


def _translate(diagnostics: list) -> dict:
    findings = []
    for d in diagnostics:
        if not isinstance(d, dict):
            continue
        code = d.get("code") or "?"
        message = d.get("message", "")
        location = d.get("location") or {}
        findings.append(
            {
                "file": d.get("filename"),
                "line": location.get("row"),
                "message": f"{code}: {message}",
                "severity": "warning",
            }
        )
    return {
        "sensor": SENSOR_ID,
        "status": "fail" if findings else "pass",
        "score": len(findings),
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
                    f"ruff not available: {detail}. "
                    "Install with `pip install ruff`."
                ),
                "severity": "error",
            }
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
