# AGENTS.md

This repository is governed by a **Cairn harness**: guides (this file) plus
deterministic **sensors** that give you fast, self-correcting feedback.

## Working agreement

- Before wrapping up a change, run `cairn check` and address failing sensors.
- Sensor messages include guidance — follow it to self-correct.
- A pre-commit **gate** reruns the sensors; a commit is blocked if they fail.
- Prefer fixing the code over raising thresholds. If a threshold change is truly
  warranted, raise it slightly (never suppress) so the sensor re-fires later.

## Scout rule

Leave the code cleaner than you found it.
