# Coding standards — Python

`pyproject.toml` is the source of truth for tool config; this file is the prose.
Decided 2026-09-08 (grilling session). Migration tracked in
`.scratch/thesis-stabilization/issues/21-python-tooling-and-standard.md`.

Scope: `src/`, `scripts/`, `tests/`. Not `firmware/` or `cgmsim/` (C).

## Environment & packaging — uv

- `uv` manages the environment and dependencies. Install: see <https://docs.astral.sh/uv/>.
- Dependencies live in `pyproject.toml` (`[project].dependencies` for runtime,
  `[dependency-groups].dev` for tooling). `uv.lock` is committed.
- `uv sync` to set up. `uv run <cmd>` to run anything in the environment.
- No `requirements.txt`.
- `requires-python = ">=3.12"` (dev on 3.13).

## Lint — ruff

- `uv run ruff check .` — the only style/correctness linter (flake8 not used).
- Enabled rule groups: `E`, `F`, `W`, `I` (import order), `UP` (pyupgrade),
  `B` (bugbear), `SIM`, `C4`, `PTH`, `RUF`.
- `PL*` is left to pylint; `D` (pydocstyle) is intentionally **off** — the dense
  "explain the why" docstring style is deliberate.
- Line length 100. `target-version = "py312"`.
- Residual violations in `src/graphic/` are `per-file-ignores`d with a pointer to
  issue 18 (the `MainWindow` god object); don't add new ignores elsewhere.

## Format — ruff format

- `uv run ruff format .` — Black-compatible, 100 cols. `--check` in the hook.

## Design smells — pylint

- `uv run pylint` — trimmed to `disable=all` plus the design checks (`R09*`,
  `max-attributes = 15`) and `similarities` / duplicate-code (`R0801`).
- It is an architecture-smell detector, not a style linter. Expect it to flag
  the god object and the duplicated CSV-upload / instant-event paths — that is
  the point (issues 18–19).

## Types — pyright

- `uv run pyright` — `standard` mode, `pythonVersion = "3.12"`.
- `src/models/` and `src/api/` must be error-free.
- Known-noisy reports in `src/graphic/` and `src/services/` are downgraded to
  `warning` (not silenced) with a baseline note in issue 18.
- Ratchet: code you add or change must be pyright-clean.

## Enforcement

- `scripts/hooks/pre-commit` runs, on every commit:
  `uv run ruff format --check .` · `uv run ruff check .` · `uv run pylint` ·
  `uv run pyright`.
- Enable once per clone: `git config core.hooksPath scripts/hooks`.
- The same four commands are the manual "full gate" — run them before marking a
  `.scratch/thesis-stabilization` issue done.
- No CI (solo project); a future maintainer who wants it starts here.
