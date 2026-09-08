# Python tooling & coding standard

Status: ready
Track: A (infra)
Phase: 1 — do first, pairs with issue 10
Blocked by: —

Prose standard: `docs/CODING_STANDARDS.md`. Decided in the 2026-09-08 grill.
`pyproject.toml` becomes the config source of truth (today it holds only
`[tool.pylint.*]`, no `[project]` table).

## Step 1 — one isolated "chore: python tooling" commit

- **uv / packaging.** Add `[project]` to `pyproject.toml`:
  `name`, `version`, `requires-python = ">=3.12"`,
  `dependencies = ["PyQt6", "matplotlib", "bleak"]` (pin to the installed
  majors: PyQt6 6.11, bleak 3.0, matplotlib current). Add
  `[dependency-groups]` `dev = ["ruff", "pylint", "pyright", "pytest"]`.
  `uv lock`, commit `uv.lock`. Delete `requirements.txt`. Update the README
  setup section (`uv sync`, `uv run`).
- **ruff.** `[tool.ruff]` `line-length = 100`, `target-version = "py312"`,
  `extend-exclude = [".venv", "firmware", "cgmsim"]`.
  `[tool.ruff.lint]` `select = ["E","F","W","I","UP","B","SIM","C4","PTH","RUF"]`.
  Leave `PL*` and `D` unselected.
- **pylint.** Rewrite `[tool.pylint.*]` to: keep `source-roots = ["src"]` and
  `generated-members = ["PyQt6.*"]`; `messages_control` → `disable = ["all"]`,
  `enable = ["R0901","R0902","R0903","R0904","R0911","R0912","R0913","R0914","R0915","R0801"]`
  (design + duplicate-code); keep `max-attributes = 15`.
- **pyright.** `[tool.pyright]` `pythonVersion = "3.12"`, `typeCheckingMode =
  "standard"`, `include = ["src","scripts","tests"]`,
  `exclude = ["**/__pycache__",".venv","firmware","cgmsim"]`.
- **hook.** `scripts/hooks/pre-commit` (bash, executable) running the four
  `uv run` commands from `docs/CODING_STANDARDS.md`; non-zero exit blocks the
  commit. Document `git config core.hooksPath scripts/hooks`.
- Run `uv run ruff format .` then `uv run ruff check --fix .` (safe fixes only —
  no `--unsafe-fixes`). Everything above + these mechanical edits = the one
  commit.

## Step 2 — clear the residue

- Fix remaining `ruff check` findings in `src/models/`, `src/api/`,
  `src/scripts/` (small; Phase 1 touches them anyway).
- `[tool.ruff.lint.per-file-ignores]` for the leftovers in `src/graphic/` with a
  `# see issue 18` comment. No ignores outside `graphic/`.
- Get `pyright` to zero in `src/models/` + `src/api/`. Downgrade the noisy
  reports in `src/graphic/` + `src/services/` to `"warning"` via
  `[tool.pyright]` overrides; record which in issue 18.
- `pylint` should already be near-silent (design-only); note anything it flags
  in issues 18–19 rather than fixing it here.

## Done when

- `uv sync` from a clean clone works; `requirements.txt` is gone.
- `scripts/hooks/pre-commit` blocks a commit that fails any of the four tools.
- `ruff check .` and `ruff format --check .` pass repo-wide.
- `pyright` passes for `src/models/` + `src/api/`; the rest is warnings with a
  baseline noted in issue 18.
- README + `docs/CODING_STANDARDS.md` agree on the commands.
