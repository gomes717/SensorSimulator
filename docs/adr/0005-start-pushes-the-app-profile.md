# 0005 — Start writes the app's profile to the board; the expected model uses the same one

Status: accepted (2026-10) · Supersedes the "expected model from the board's Person
Config" decision in [0002](0002-board-is-the-authority.md)

## Context

[0002](0002-board-is-the-authority.md) built the local expected model from what the
board reported back (Person Config plus the meal and exercise lists, read when a
sensor connects), on the theory that the app's record of what it sent can be stale.
In use the two lines still drifted: the expected line climbed after a meal while the
sensor stayed flat. The comparison depended on a *readback* — a GATT read per
slot, serialized behind a shared cursor, decoded through float32 — matching a
*schedule the board was actually running*, and nothing made the board run what the
user had configured in the app. Whatever an earlier session left on the board (an old
meal list, another patient's parameters) was simply what the board ran.

## Decision

**The app is the authority for what a run simulates; the board is the authority for
what it is doing.**

- **Start writes the profiles, then starts.** For every live sensor that is not
  replaying a CSV, `StartPush` writes the app's person (model + parameters), sensor
  profile, `data_source = model`, and the person's meal and exercise schedules
  (cleared first, then each event) to that slot, through one session, one slot after
  another — the Sensor-select cursor is a single value on the board shared by every
  connection. Only when the board has taken every write does the run begin
  (`RUN_STATE_STOPPED` → `RUNNING`, which also re-zeroes every slot's clock together).
  The toolbar button reads "Starting…" meanwhile. A failed push leaves the run
  stopped and says so; a run whose inputs the board never received would be exactly
  the mismatch this exists to prevent.
- **One list, two consumers.** `AppState.board_plan(live_slots)` is the single answer
  to "what does each live sensor run": the slot's assigned patient and sensor (or the
  active ones when nothing was ever assigned). A CSV patient and an unassigned slot
  are left out. Start writes exactly this list and `SimulationCoordinator` builds the
  engines from exactly this list, so the expected line and the sensor start from the
  same inputs by construction.
- **CSV sensors are ignored by Start.** Their recording is uploaded separately
  (Send CSV to Board); there is no model to write and no expected line to draw.
- **The readback stays for display only.** `BoardMode` still reads Data Source and
  the model name per slot to title the graph and hide the food/exercise graph for a
  CSV sensor, and is re-asked after Start's push so those follow the board's answer.
  It no longer reads the meal / exercise lists and nothing is rebuilt from it, so a
  late readback can no longer reset the graphs in the middle of a run.

## Consequences

- Editing a patient's parameters or schedules and pressing Start is enough; no
  separate Send to Board is needed for the run to use them. Send to Board remains for
  assigning a patient to a slot and for CSV.
- Start takes longer: each write is paced (~80 ms) and applied and saved to flash by
  the board, so the delay grows with the number of sensors and scheduled events.
  (Not yet timed on hardware.)
- A sensor with no patient assigned is not touched by Start and gets no expected
  line (unless nothing at all is assigned, when the active patient stands in).
- A sensor that connects mid-run is not configured until the next Start.
- Mid-run edits to a profile do not reach the board; they apply at the next Start.
- The board's live Food/Exercise Status is still fed to the expected model (so it
  follows instant events), but only from the sensor that has that model. Feeding the
  one running engine every connected sensor's status made sensor 1's expected line
  climb on the other sensors' default meals.
