"""Load and validate ``.cairn/cairn.toml``.

The config declares the harness for a repo: a list of sensors, each with a
shell ``run`` command that emits the Sensor JSON Contract (see ``sensors.py``),
a ``category``, the lifecycle ``stage``s it runs in, an optional ``threshold``,
and self-correction ``guidance`` injected into the agent summary.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import tomllib

CONFIG_RELPATH = Path(".cairn") / "cairn.toml"

# ${name} placeholder used in sensor `run` commands, resolved from [vars].
_VAR_PATTERN = re.compile(r"\$\{([^}]*)\}")

VALID_CATEGORIES = {"maintainability", "architecture", "quality"}
VALID_STAGES = {"in-session", "pre-commit", "ci"}
VALID_DIRECTIONS = {"lower-is-better", "higher-is-better"}


class ConfigError(Exception):
    """Raised when ``cairn.toml`` is missing or invalid."""


@dataclass(frozen=True)
class Threshold:
    metric: str
    value: float
    direction: str


@dataclass(frozen=True)
class Sensor:
    id: str
    run: str
    category: str
    stage: tuple[str, ...]
    guidance: str = ""
    threshold: Threshold | None = None

    def runs_in(self, stage: str) -> bool:
        return stage in self.stage


@dataclass(frozen=True)
class Config:
    version: int
    global_guidance: str
    sensors: tuple[Sensor, ...]
    path: Path

    def sensors_for_stage(self, stage: str) -> tuple[Sensor, ...]:
        return tuple(s for s in self.sensors if s.runs_in(stage))


def config_path(target: str | Path) -> Path:
    return Path(target) / CONFIG_RELPATH


def load_config(target: str | Path) -> Config:
    """Load and validate the harness config for ``target``.

    Raises ``ConfigError`` with an actionable message on any problem.
    """
    path = config_path(target)
    if not path.is_file():
        raise ConfigError(
            f"no harness config at {path}. Run `cairn apply --target {target}` first."
        )
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc

    version = raw.get("version")
    if version != 1:
        raise ConfigError(f"{path}: unsupported or missing `version` (expected 1)")

    global_guidance = str(raw.get("global_guidance", "") or "")

    variables = _parse_vars(raw.get("vars"), path)

    raw_sensors = raw.get("sensors")
    if not isinstance(raw_sensors, list) or not raw_sensors:
        raise ConfigError(f"{path}: at least one [[sensors]] entry is required")

    sensors: list[Sensor] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw_sensors):
        sensors.append(_parse_sensor(entry, i, path, variables))
        if sensors[-1].id in seen:
            raise ConfigError(f"{path}: duplicate sensor id '{sensors[-1].id}'")
        seen.add(sensors[-1].id)

    return Config(
        version=version,
        global_guidance=global_guidance,
        sensors=tuple(sensors),
        path=path,
    )


def _parse_vars(raw: object, path: Path) -> dict[str, str]:
    """Parse the optional ``[vars]`` table into a name->string mapping.

    Values may be strings, ints, or floats (coerced to string). This lets a
    repo define its layout once (e.g. ``backend = "backend"``) and reference it
    from every sensor `run` command, so paths and thresholds are not duplicated.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: [vars] must be a table")
    variables: dict[str, str] = {}
    for name, value in raw.items():
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            raise ConfigError(
                f"{path}: [vars] '{name}' must be a string or number"
            )
        variables[name] = str(value)
    return variables


def _interpolate(run: str, variables: dict[str, str], where: str) -> str:
    """Replace ``${name}`` tokens in ``run`` using ``variables``; fail-fast."""
    def _replace(match: re.Match[str]) -> str:
        name = match.group(1).strip()
        if name not in variables:
            raise ConfigError(
                f"{where} references undefined var '${{{name}}}'; "
                f"define it under [vars]"
            )
        return variables[name]

    return _VAR_PATTERN.sub(_replace, run)


def _require_text(value: object, where: str, field: str) -> str:
    """Return a non-empty string field, or raise with an actionable message."""
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where} requires a non-empty string `{field}`")
    return value


def _require_stages(value: object, where: str) -> tuple[str, ...]:
    """Return the validated, non-empty list of lifecycle stages."""
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{where} `stage` must be a non-empty list")
    unknown = [st for st in value if st not in VALID_STAGES]
    if unknown:
        raise ConfigError(
            f"{where} invalid stage '{unknown[0]}'; allowed {sorted(VALID_STAGES)}"
        )
    return tuple(value)


def _require_category(value: object, where: str) -> str:
    if value not in VALID_CATEGORIES:
        raise ConfigError(
            f"{where} `category` must be one of {sorted(VALID_CATEGORIES)}"
        )
    return str(value)


def _parse_sensor(
    entry: object, index: int, path: Path, variables: dict[str, str]
) -> Sensor:
    where = f"{path}: sensors[{index}]"
    if not isinstance(entry, dict):
        raise ConfigError(f"{where} must be a table")

    sid = _require_text(entry.get("id"), where, "id")
    where = f"{where} ('{sid}')"

    run = _require_text(entry.get("run"), where, "run")

    return Sensor(
        id=sid,
        run=_interpolate(run, variables, where),
        category=_require_category(entry.get("category"), where),
        stage=_require_stages(entry.get("stage"), where),
        guidance=str(entry.get("guidance", "") or "").strip(),
        threshold=_parse_threshold(entry.get("threshold"), where),
    )


def _parse_threshold(raw: object, where: str) -> Threshold | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} `threshold` must be a table")
    metric = raw.get("metric")
    value = raw.get("value")
    direction = raw.get("direction")
    if not isinstance(metric, str) or not metric:
        raise ConfigError(f"{where} threshold.metric must be a string")
    if not isinstance(value, (int, float)):
        raise ConfigError(f"{where} threshold.value must be a number")
    if direction not in VALID_DIRECTIONS:
        raise ConfigError(
            f"{where} threshold.direction must be one of "
            f"{sorted(VALID_DIRECTIONS)}"
        )
    return Threshold(metric=metric, value=float(value), direction=direction)
