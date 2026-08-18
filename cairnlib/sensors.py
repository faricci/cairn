"""The Sensor JSON Contract and the runner that executes sensor commands.

Every sensor `run` command is a wrapper that executes an underlying tool
(radon, ruff, import-linter, ...) and prints a single JSON object matching the
contract below to stdout. Adding a sensor never requires changing the CLI.

Contract (stdout of a sensor command):

    {
      "sensor": "complexity",       # str, required
      "status": "pass" | "fail",    # required; the source of truth for status
      "score": 14,                   # number, optional
      "threshold": 10,               # number, optional
      "direction": "lower-is-better",# optional
      "findings": [                  # optional
        {"file": "a.py", "line": 42, "message": "cc=14", "severity": "error"}
      ]
    }

The wrapper's *exit code is ignored*; `status` comes from the JSON. This keeps
sensors composable with tools that exit non-zero merely because they found
issues.

The `sensor` field is informational: results are keyed by the **id configured in
cairn.toml**, so the same wrapper can back several sensors without their
guidance, snapshots, or ledger entries colliding.
"""
from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import Sensor

VALID_STATUS = {"pass", "fail"}
VALID_SEVERITY = {"error", "warning", "info"}


class SensorError(Exception):
    """Raised when a sensor command fails to run or emits an invalid result."""


@dataclass(frozen=True)
class Finding:
    file: str | None
    line: int | None
    message: str
    severity: str


@dataclass(frozen=True)
class SensorResult:
    sensor: str
    status: str
    score: float | None = None
    threshold: float | None = None
    direction: str | None = None
    findings: tuple[Finding, ...] = field(default_factory=tuple)
    error: str | None = None  # populated when the sensor itself failed to run

    @property
    def ok(self) -> bool:
        return self.status == "pass" and self.error is None


def run_sensor(sensor: Sensor, target: str | Path) -> SensorResult:
    """Execute a sensor's command in ``target`` and parse its JSON output.

    Never raises for tool-level failures: a broken sensor returns a ``fail``
    result carrying ``error`` so the harness stays observable.
    """
    try:
        proc = subprocess.run(
            shlex.split(sensor.run),
            cwd=str(target),
            capture_output=True,
            text=True,
            timeout=300,
        )
    except FileNotFoundError as exc:
        return _errored(sensor, f"command not found: {exc}")
    except subprocess.TimeoutExpired:
        return _errored(sensor, "sensor timed out after 300s")

    stdout = (proc.stdout or "").strip()
    if not stdout:
        detail = (proc.stderr or "").strip() or f"exit code {proc.returncode}"
        return _errored(sensor, f"no JSON on stdout ({detail})")

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return _errored(sensor, f"invalid JSON: {exc}")

    return parse_result(sensor.id, payload)


def parse_result(sensor_id: str, payload: object) -> SensorResult:
    if not isinstance(payload, dict):
        return _errored_id(sensor_id, "result must be a JSON object")

    status = payload.get("status")
    if status not in VALID_STATUS:
        return _errored_id(sensor_id, f"`status` must be one of {sorted(VALID_STATUS)}")

    findings = _parse_findings(payload.get("findings"), sensor_id)

    direction = payload.get("direction")
    return SensorResult(
        # The configured id wins over the payload's self-reported name. Two
        # sensors may share one wrapper (e.g. ruff with different rule sets);
        # trusting the payload would merge them - dropping their guidance and
        # colliding in the snapshot and ledger.
        sensor=sensor_id,
        status=status,
        score=_as_number(payload.get("score")),
        threshold=_as_number(payload.get("threshold")),
        direction=direction if isinstance(direction, str) else None,
        findings=findings,
    )


def _parse_findings(raw: object, sensor_id: str) -> tuple[Finding, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        return ()
    out: list[Finding] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        severity = item.get("severity")
        if severity not in VALID_SEVERITY:
            severity = "warning"
        out.append(
            Finding(
                file=item.get("file") if isinstance(item.get("file"), str) else None,
                line=item.get("line") if isinstance(item.get("line"), int) else None,
                message=str(item.get("message", "")),
                severity=severity,
            )
        )
    return tuple(out)


def _as_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _errored(sensor: Sensor, message: str) -> SensorResult:
    return _errored_id(sensor.id, message)


def _errored_id(sensor_id: str, message: str) -> SensorResult:
    return SensorResult(sensor=sensor_id, status="fail", error=message)
