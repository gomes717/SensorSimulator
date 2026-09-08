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
- Residual violations in `src/gui/` are `per-file-ignores`d with a pointer to
  issue 18 (the `MainWindow` god object); don't add new ignores elsewhere.

## Format — ruff format

- `uv run ruff format .` — Black-compatible, 100 cols. `--check` in the hook.

## Design smells — pylint

- `uv run pylint src` — `disable=all` plus only `R0902` (too-many-instance-
  attributes) and `R0904` (too-many-public-methods). The wider `R09xx` checks
  (too-many-locals / -statements / -arguments / -branches) fire on the numerical
  model ports and Qt boilerplate, where they are noise, so they stay off.
- `R0801` (duplicate-code) is off too — its current hits (person/sensor config
  windows) are tracked in issue 19 and it has no inline-disable.
- Net: pylint stays at 10.00/10; a *new* oversized class fails the gate. The
  five classes over the limit today carry an inline
  `# pylint: disable=too-many-instance-attributes  # see issue 18`.

## Types — pyright

- `uv run pyright` — `standard` mode, `pythonVersion = "3.12"`.
- `src/models/` and `src/api/` are held to full `standard` and must be
  **error-free** — a new error there fails the gate.
- `src/gui/` and `src/services/` have a baseline of pre-existing issues
  (bleak's loose typing, the god object reaching into private state — issue 18);
  their noisy reports are `executionEnvironments`-downgraded to **warning**, so
  the gate is green while the debt stays visible (~50 warnings today).
- `scripts/` type reports are off (the harnesses drive private app state on
  purpose).
- Ratchet: code you add or change should be pyright-clean.

## Enforcement

- `scripts/hooks/pre-commit` runs, on every commit:
  `uv run ruff format --check .` · `uv run ruff check .` · `uv run pylint src` ·
  `uv run pyright` · `uv run pytest -q`.
- Enable once per clone: `git config core.hooksPath scripts/hooks`.
- The same five commands are the manual "full gate" — run them before marking a
  `.scratch/thesis-stabilization` issue done.
- No CI (solo project); a future maintainer who wants it starts here.
