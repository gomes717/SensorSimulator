# Harden the E2E suite

Status: done (2026-09-08, commit 07333e4)
Track: A
Phase: 1
Blocked by: —

## Outcome

The old suite passed 19/19 with bugs open because deterministic checks ran only via Model Only. Added `tests/test_fe_graph_title.py` (issue 08 pin); renamed the firmware-PISA case to **S7-07** (S7-04 reserved in the matrix); `docs/E2E_TEST_PLAN.md` gained a Regression-pins table + a note that pytest is the faster gate under the hardware suite. Pins: 01/02/03/05/06/08. Pending: 04, 07.

---

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
