# Harden the E2E suite

Status: ready
Track: A
Phase: 1
Blocked by: —

## Problem

`docs/TODO.md`: "Melhorar o E2E, que deixou passar muitos erros." `scripts/e2e.py`
reports 19/19 PASS while the TODO lists many open defects that coexist with it
(connected-after-disconnect, high-speed ODE breakage, PISA, multi-identity
timeslot). The suite structure is fine — it is under-populated, and hardware E2E
is bad at the deterministic numerical checks.

## What to do

- For **each** Track A bug fixed in issues 02–08, add the minimal case that
  would have caught it — a pytest where the logic is deterministic (PISA
  envelope, Euler sub-stepping, param order), an `e2e.py` / `e2e_4sensor.py`
  case where it needs the board.
- Close the cheap documented gaps in `docs/E2E_TEST_PLAN.md:384-411` where a
  debug hook now exists.
- Do **not** restructure the plan or add hardware CI (no bench board in CI —
  out of scope, `docs/E2E_TEST_PLAN.md:29-33`).

## Done when

Every issue 02–08 fix has a named pinning case; running `pytest` + `e2e.py`
would now fail on each of those bugs if reintroduced.
