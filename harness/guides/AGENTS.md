# AGENTS.md

This repository is governed by a **Cairn harness**: guides (this file) plus
deterministic **sensors** that give you fast, self-correcting feedback.

## Working agreement

- You MUST run the exact command `cairn check` after each change and before
  reporting a task done. Do NOT run the `.cairn/sensors/*.py` scripts directly -
  only `cairn check` records to the ledger and applies thresholds, guidance, and
  trend deltas.
- Sensor messages include guidance - follow it to self-correct, then re-run
  `cairn check` until every sensor is PASS.
- A pre-commit **gate** reruns the sensors; a commit is blocked if they fail.
- Prefer fixing the code over raising thresholds. If a threshold change is truly
  warranted, raise it slightly (never suppress) so the sensor re-fires later.

## Scout rule

Leave the code cleaner than you found it.

## Cognitive framework

Follow the shared agent behaviour in `.cairn/guides/cognitive-framework.md`
(learn from memory, reflect before acting, deterministic first, security by
default).
