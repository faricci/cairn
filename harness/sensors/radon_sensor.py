#!/usr/bin/env python3
"""Cairn sensor: cyclomatic complexity (maintainability).

Wraps `radon` and emits the Cairn Sensor JSON Contract on stdout. The wrapper
owns the pass/fail decision (status fails when any code block exceeds --max-cc),
so the underlying tool's exit code is irrelevant.

Usage:
    python radon_sensor.py [--max-cc N] [PATH ...]

If `radon` is not installed, a valid `fail` result carrying an actionable
`error`-severity finding is emitted (still contract-compliant JSON).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

SENSOR_ID = "complexity"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max-cc", type=int, default=10)
    parser.add_argument("paths", nargs="*", default=["."])
    args = parser.parse_args(argv)
    paths = args.paths or ["."]

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "radon", "cc", "-j", *paths],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except Exception as exc:  # noqa: BLE001 - report any launch failure as a finding
        print(json.dumps(_tool_missing(str(exc))))
        return 0

    if proc.returncode != 0 and not (proc.stdout or "").strip():
        print(json.dumps(_tool_missing(proc.stderr.strip() or "radon failed")))
        return 0

    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        print(json.dumps(_tool_missing(f"could not parse radon output: {exc}")))
        return 0

    print(json.dumps(_translate(data, args.max_cc)))
    return 0


def _translate(data: dict, max_cc: int) -> dict:
    findings = []
    worst = 0
    for filename, blocks in data.items():
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            cc = block.get("complexity", 0)
            worst = max(worst, cc)
            if cc > max_cc:
                name = block.get("name", "?")
                findings.append(
                    {
                        "file": filename,
                        "line": block.get("lineno"),
                        "message": (
                            f"cyclomatic complexity {cc} in "
                            f"'{name}' (max {max_cc})"
                        ),
                        "severity": "error",
                    }
                )
    status = "fail" if findings else "pass"
    return {
        "sensor": SENSOR_ID,
        "status": status,
        "score": worst,
        "threshold": max_cc,
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
                    f"radon not available: {detail}. "
                    "Install with `pip install radon`."
                ),
                "severity": "error",
            }
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
