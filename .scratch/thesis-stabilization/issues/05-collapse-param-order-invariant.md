# Collapse the model param-order invariant to one source of truth

Status: done (2026-09-08, commit 27a200b)
Track: A
Phase: 1
Blocked by: —

## Outcome — the "collapse" was already mostly done

`models/<m>.py::PARAM_NAMES` is already the single source: `protocol._MODEL_PARAM_NAMES`
maps `ModelId -> module.PARAM_NAMES` (the same list object, verified by an
identity test), and `person_config_window` reads `module.PARAM_NAMES` directly.
No re-typed copies exist. The architecture review's "owned by 4 consumers" was
overstated — they are thin `ModelId -> ...` dicts, not duplicate name lists.

What was genuinely missing and is now added:

- `tests/param_order/*.golden` — the field order of every model + sensor C
  parameter struct, transcribed from
  `firmware/peripheral_cgms/src/models/cgmsim_*.h`. Hand-update on struct change.
- `tests/test_param_order.py` (12 cases) — Python `PARAM_NAMES` == golden ==
  `protocol.model_param_names()`; identity check (one list, not two); counts fit
  the firmware buffers.
- Tightened the single-source comments; fixed stale `src/protocol.py` paths.

All 4 models + 3 sensors currently match the firmware exactly. No runtime
behaviour change, so no board re-test.

---


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
