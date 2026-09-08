# Collapse the model param-order invariant to one source of truth

Status: ready
Track: A
Phase: 1
Blocked by: —

## Problem

The Python↔C parameter order that makes expected-vs-received meaningful
(`docs/ARCHITECTURE.md:50-57`, `docs/MODELS.md:61-72`) is defined independently
in four places and must also match the firmware C structs:

- each model module's `PARAM_NAMES` (`cambridge.py:29`, etc.)
- `protocol._MODEL_PARAM_NAMES` (`protocol.py:21-26`)
- `engine._ADAPTERS` (`engine.py:103`)
- `person_config_window._MODEL_MODULES` / `_rebuild_param_form`
  (`person_config_window.py:37-42`, `:237-251`)

A silent divergence corrupts every comparison in the thesis.

## What to do

- Each model module owns its `PARAM_NAMES` tuple (the ordering exists because of
  that model's state vector — natural home).
- `protocol`, `engine._ADAPTERS`, `person_config_window` import it; delete their
  local copies.
- Add `tests/param_order/<model>.golden` — the field order as it appears in the
  firmware C struct — and a pytest asserting each model's Python tuple matches
  its golden file. Hand-updated whenever a struct changes; the diff is the
  review signal.

## Done when

- One `PARAM_NAMES` per model, three consumers importing it.
- `pytest` fails loudly if Python order and the golden C-struct order diverge.

## Refs

Architecture review §7.6.
