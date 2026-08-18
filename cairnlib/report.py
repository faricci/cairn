"""Render the guidance-enriched, trend-aware agent summary for `cairn check`.

The summary is the harness's primary self-correction surface: for each failing
sensor it prints the configured guidance (a positive prompt injection) plus the
concrete findings, and a trend arrow versus the last snapshot.
"""
from __future__ import annotations

from .config import Config, Sensor
from .sensors import SensorResult

WORSE, BETTER, SAME, NEW = "\u25b2", "\u25bc", "=", "*"


def trend_symbol(result: SensorResult, prev: dict | None) -> str:
    """Direction-aware trend vs the previous snapshot for this sensor."""
    if prev is None:
        return NEW
    prev_status = prev.get("status")
    # Status transitions dominate.
    if prev_status != result.status:
        return WORSE if result.status == "fail" else BETTER
    # Same status: compare score if both available and a direction is known.
    prev_score = prev.get("score")
    if result.score is None or prev_score is None or result.direction is None:
        return SAME
    if result.score == prev_score:
        return SAME
    lower_better = result.direction == "lower-is-better"
    improved = result.score < prev_score if lower_better else result.score > prev_score
    return BETTER if improved else WORSE


def render(
    config: Config,
    results: list[SensorResult],
    snapshot: dict[str, dict],
) -> str:
    by_id = {s.id: s for s in config.sensors}
    lines: list[str] = []
    for result in results:
        sensor = by_id.get(result.sensor)
        lines.extend(_render_one(result, sensor, snapshot.get(result.sensor)))
    if config.global_guidance:
        lines.append(config.global_guidance)
    return "\n".join(lines)


def _headline(result: SensorResult, trend: str) -> str:
    status = "PASS" if result.ok else "FAIL"
    score = "" if result.score is None else f"  {_fmt(result.score)}"
    thr = "" if result.threshold is None else f" (threshold {_fmt(result.threshold)})"
    return f"[cairn] {result.sensor:<12} {status}{score}{thr}  {trend}"


def _finding_line(finding) -> str:
    loc = finding.file or ""
    if finding.line is not None:
        loc = f"{loc}:{finding.line}"
    return f"  {loc}  {finding.message}".rstrip()


def _render_one(
    result: SensorResult, sensor: Sensor | None, prev: dict | None
) -> list[str]:
    out = [_headline(result, trend_symbol(result, prev))]
    if result.error:
        out.append(f"  ! {result.error}")
        return out
    if result.ok:
        return out
    if sensor and sensor.guidance:
        out.append("  \u2192 " + _indent(sensor.guidance))
    out.extend(_finding_line(f) for f in result.findings)
    return out


def _indent(text: str) -> str:
    return "\n    ".join(text.strip().splitlines())


def _fmt(n: float) -> str:
    return str(int(n)) if float(n).is_integer() else str(n)
