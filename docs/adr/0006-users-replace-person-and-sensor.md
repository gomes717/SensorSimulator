# 0006 — A User replaces Person + Sensor, and can be read from the board

Status: accepted (2026-10-06) · implemented (2026-10-07, all ten slices of
[`.scratch/users-screen/spec.md`](../../.scratch/users-screen/spec.md)) · Extends
[0002](0002-board-is-the-authority.md) and [0005](0005-start-pushes-the-app-profile.md)

## Context

What a sensor runs was split across a **Person** profile (model, parameters, schedules,
data source), a **Sensor** profile (noise model) and a slot→(person, sensor) record, each
edited in its own window reached from Configuration. Putting a simulated person on a
sensor meant choosing two profiles and a slot in two places; the board could not tell the
app *who* it was running, so a board that already held a configuration could not be
turned back into something the app recognized; and a CSV user was a path to a file that
might not exist on another machine.

## Decision

- **One `User`** holds everything: identity (name, picture, height, weight), a mode
  (`model` or `csv`), the model + sensor-noise parameters and schedules for model mode,
  and the 24 h CSV window **copied in** for CSV mode. The inputs of the mode not in use
  are kept, so switching modes loses nothing.
- **Weight is the model's `BW`; height is app-only** and feeds nothing yet.
- **The board holds the user's name** in a new per-slot characteristic. That name is the
  only key used to recognize a user read back from a board (name-only matching, decided
  2026-10-06).
- **Read from the board** builds a *board user* from one slot and compares it with the
  saved user of the same name. Unknown name → a new unsaved user. Same name, same
  content → open it. Same name, different content → the user chooses *Overwrite* or
  *Create `Name#2`*; creating also writes the new name to the board in the same action.
  Comparison is on what the board can hold, after rounding floats to float32 (the wire
  format); the picture, height and id are never compared.
- **A name write does not reset the simulation.** It is saved to flash but skips
  `model_thread_apply_config()` — metadata must not restart a run.
- **The advertised BLE name is unchanged** ("Nordic Glucose Sensor N"); the app maps
  identity → slot from it and the pairing work depends on it.
- **Three sensors.** The board runs 3 (`CONFIG_APP_SENSOR_COUNT=3`); the app models 3 slots
  (`board_layout.MAX_SLOTS`). The storage layout still reserves a fourth.
- **The old profile and layout files are migrated once and left as a backup.** The migration
  reads each slot's old sensor from the legacy layout file, so a migrated user keeps the sensor
  its slot used.
- **The engine and the board push still run on `PersonProfile` / `SensorProfile`,** but those are
  derived from a user on demand (`models/user_sim.py`), never saved: a user is the only thing
  edited. A CSV user's window travels inside the profile and is replayed from there.
- **A slot records the user's name** (`data/board_layout.json`); renaming a user moves its slot
  along and deleting one leaves it. Names are unique, which Save and Send enforce.
- **Send writes in a fixed order:** the name first (it does not reset the simulation), then the
  model and sensor noise with both schedules cleared and rewritten — or, for a CSV user, the
  recording is uploaded and **only then** is the slot switched to the CSV source (switching first
  left the slot playing its old model with the CSV source already on; found on hardware).

## Consequences

- Extends [0002](0002-board-is-the-authority.md): reading a slot back and offering to adopt
  it is the same "ask the board" principle, now producing a saved user.
- Keeps [0005](0005-start-pushes-the-app-profile.md): Start still writes the app's
  profile for each live slot and the expected line is built from the same list; "profile"
  now means the user. Start additionally writes each user's name first — skipped, never failed,
  when the board does not list `user_name` (Windows can show a stale cached services list right
  after a firmware update) or the name is longer than 30 bytes (an older profile's). CSV is still
  uploaded separately from Start — now by **Send to…** with the user.
- The board's config image moved to v6; a v5 image is migrated (slots kept, names empty),
  not wiped, because the names were appended after `slots[]`.
- The board can send its CSV recording back (added 2026-10-07: `CSV_OP_READ` and the `csv_read`
  characteristic). A board in CSV mode reads as a CSV user holding its recording, and the
  comparison includes it — samples, interval and meals. A board that holds no recording reads as
  a CSV user with no window; one that could not send it (older firmware) is not compared on it.
- Sending a user to a sensor that already has another user overwrites it, name included,
  without a warning.
- `scripts/scenario_dispatch.py`, `scenarios/*.json` and the e2e harnesses address people
  and sensors by name and must move to users when the old windows are removed.
