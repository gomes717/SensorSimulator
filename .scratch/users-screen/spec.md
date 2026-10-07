# Users screen — one "User" replaces Person + Sensor, read from the board, preview, save, send

Status (2026-10-07): **all ten slices done and committed**, hardware-verified. The six open questions were answered on 2026-10-06 (see *Decisions you made*); none left. The board has **3** sensors, not 4 — see *Three sensors*. What remains is listed under *Follow-ups*.

## What changes, in one paragraph

Today the app has separate **Person** profiles (model, parameters, food, exercise, data source) and **Sensor** profiles (noise model), edited in four windows reached from Configuration, and a slot→(person, sensor) map in `data/board_layout.json`. This plan replaces all of that with a single **User**, edited in one **User Profile** screen, listed in a **Users** screen reached from a new toolbar button (next to Configuration and Debug). A user can be created by **reading it from a sensor on the board**, and can be **sent to a sensor**. The Person/Sensor group is removed from Configuration.

## Three sensors

The board runs **3** sensors (`CONFIG_APP_SENSOR_COUNT=3`, Kconfig `range 1 3`); the app models 3 slots (`board_layout.MAX_SLOTS = 3`). The firmware's storage still reserves a fourth slot so the flash layout never moved, but nothing uses it. Wherever this plan said four slots it now means three; a `Sensor select` of `3` is clamped to slot `0` by the firmware — a hardware script that addresses "slot 4" silently writes to slot 1.

## The User

| Field | Notes |
|---|---|
| `id`, `name` | `id` is stable (picture folder, CSV files); `name` is what the board is told |
| `picture` | image file copied into `data/users/<id>/`; falls back to the generated initials disc (`gui/avatar.py`) |
| `height_cm`, `weight_kg` | weight is written into the model's `BW` parameter (all four models have one). Height is **app-only, display only** — it feeds nothing yet |
| `mode` | `"model"` or `"csv"` (replaces `data_source`) |
| model | `model_id` + params (the old Person) — only used when `mode == "model"` |
| sensor model | `sensor_id` + params (the old Sensor) — only used when `mode == "model"` |
| food, exercise | recurring daily event lists — only when `mode == "model"` |
| csv | the 24 h glucose track (+ food log) **stored with the user**, not a path to the original file — the board will later send it back and the user must be rebuildable from it |

Storage: `data/users.json` + `data/users/<id>/` (picture, csv). The old `profiles.json` / `board_layout.json` are read once to migrate (each person becomes a user, paired with the sensor profile its slot used, else the default `Ideal`), then left alone as a backup. The slot→user map stays in `board_layout.json` but holds user ids.

`AppState.board_plan` (ADR 0005) becomes slot → `User`; Start still pushes exactly that list, and the expected-line engines are still built from the same list.

## Screens

### Toolbar
New **Users** button on the right with Configuration and Debug (`build_toolbar`, `main_window._build_run_controls`).

### Users screen (`users_window.py`)
- List of users: picture + name (+ which sensor it is on, if any).
- **+ Read from…** — choose a connected sensor (menu of live links), read that slot, then follow the flow below.
- **+ New** — manual user with defaults (not every user will be on a board; also what Model Only mode needs). Opens the profile screen on an unsaved user.
- Double-click / Open → User Profile screen. Delete.

### Read-from flow (`user_reader.py` + `user_match.py`)
1. Read the whole slot from the board, serialized under the Sensor-select cursor (one value shared by every connection — same constraint as `BoardMode`): **name**, data source, person config (model + params), sensor config, food list, exercise list. CSV later (below).
2. Build a *board user* from that.
3. Look up a saved user by **name**:
   - **No such name** (or the board has no name) → the user is *unknown*: open the profile screen on a new unsaved user built from the board, named "Unknown" (editable; nothing is written until Save).
   - **Same name, everything matches** → open the profile screen on the saved user.
   - **Same name, something differs** → popup: *"The board's "Ana" differs from the saved "Ana". Overwrite the saved one with the board's parameters, or create a new user?"* — **Overwrite** / **Create "Ana#2"** / Cancel. Create uses the next free `#N`, saves the new user and **writes the new name to the board** so the two agree.
4. "Matches" compares floats after rounding both sides to float32 (the wire format), and ignores `height_cm`, picture (the board never has them) — so a read-back user keeps those from the saved copy when overwritten.

### User Profile screen (`user_profile_window.py`)
```
┌───────────┬───────────────────────────────────────┐
│  picture  │                                       │
│   Name    │        current page (stacked)         │
│ ───────── │                                       │
│  Profile  │                                       │
│  CSV      │                                       │
│  Food     │                                       │
│  Exercise │                                       │
│  Model    │                                       │
├───────────┴───────────────────────────────────────┤
│        [ Preview ]   [ Save ]   [ Send to… ]      │
└───────────────────────────────────────────────────┘
```
- **Profile**: picture (choose file → copy + square-crop), name, height, weight, **mode** (CSV / Model).
- **Mode gates the menu**: CSV → only *CSV* is enabled; Model → *Food*, *Exercise*, *Model* are enabled (CSV disabled). Switching mode is just enabling/disabling pages; data on the other side is kept, not deleted.
- **Model** page: the person model combo + its parameters, and the sensor model combo + its parameters. `BW` is shown read-only and driven by the Profile page's weight.
- **Food** / **Exercise** pages (one class, two configurations): rows to add an event at a time of day (carbs + duration / duration + intensity), and a 24 h graph of the events above the table. Per user.
- **CSV** page: what exists today (choose a Dexcom CSV + window start, matching Food Log), but the chosen window is **copied into the user** on pick.
- **Preview**: a 24 h graph of the simulated glucose using the user's food, exercise and glucose model **without sensor noise**, or the CSV window when mode is CSV. `ModelStepper` already runs noise-free (sensor noise is on-device only — confirm while implementing), so Preview is a pure function `preview_24h(user) -> (minutes, mg/dL)` stepping 1440 × 1 min with the same sub-stepping the board uses; run it off the UI thread if UVA/Padova is slow.
- **Save**: persist the user, including the CSV data now.
- **Send to…**: choose a connected sensor; if the screen differs from what is saved, ask **"Save before sending?"** (Save & send / Send without saving / Cancel). Send writes name, mode, model + params, sensor model + params, food + exercise (cleared first) **or** the CSV upload, records the slot assignment, and re-asks `BoardMode`. Built from `StartPush`'s per-slot push, which is generalized from "all slots" to "one slot, one user".

### Configuration window
Delete the "Patient / sensor" group (Person…, Food…, Exercise…, Sensor…). Speed, Model Only, CGMS Only, appearance and thresholds stay. Delete `person_config_window.py`, `sensor_config_window.py`, `food_config_window.py`, `exercise_config_window.py`, `ConfigController.person_selected/sensor_selected/editor_requested`, and the matching `WindowDeps` / `ChildWindows` entries once the new screens cover them. `data_source_group.py` is reused or folded into the CSV page.

## Firmware / protocol changes

1. **User name characteristic** (new, read + write, per slot via Sensor select, persisted). `struct sim_config` gains `char user_name[slot][32]` **appended after `slots[]`** (30 bytes + NUL; the app and the board both enforce 30 UTF-8 bytes) → bump `SIM_CONFIG_VERSION`; 4 slots grow the struct from ~2.85 KB to ~2.98 KB, still inside the 4 KB `sim_storage_partition`. Empty name = "no user". App side: `encode_user_name` / `decode_user_name` in `api/protocol.py`, UUID in `ble_uuids.py`, `PROTOCOL_SPEC.md` row. **The advertised BLE name stays "Nordic Glucose Sensor N"** — the app maps identity → slot from that name and the pairing/identity work (see memory: identity-1 storm, no-auth tradeoff) must not be disturbed.
2. **CSV readback — later**, not in this build. The plan only leaves the seam: the reader treats "board is in CSV mode" as a user in CSV mode whose data is *not available from the board yet*; matching on a CSV user compares everything except the track. When the firmware gains a read op (e.g. `CSV_OP_READ` + notify chunks mirroring the upload, CRC-checked), the reader fills the track and the match includes it.
3. Nothing is added for picture or height — the board has no use for them.

## Decisions this plan relies on (and why)

- **User, not Person + Sensor.** You asked to remove person/sensor from Configuration and put both models inside the user; keeping two profile types behind one screen would just move the confusion.
- **The user is keyed by name for matching, and only by name.** It is the only identity the board can hold. A board user with an unknown name is *unknown* even if its parameters equal a saved user's.
- **Contradicts nothing in ADR 0002 or 0005, extends both.** 0002 (board is the authority for what it runs) is what read-from does; 0005 (app writes its profile on Start, one list, two consumers) still holds with "profile" = user — Start now also writes the name. ADR 0005 says CSV sensors are left out of Start and uploaded separately; that stays, but the upload moves into **Send to**. Record this as **ADR 0006**.
- **Name write on Create "#N" is part of the same action**, not a separate step, so the app and board cannot disagree afterward.
- **Preview uses the engine, not a new simulator**, so what it shows is by construction what the expected line would draw.

## Slices (each ends green: tests + pylint clean, no `# pylint: disable`)

0. **Clean base.** *(done — `5edf0e7`)* The working tree has uncommitted work (config/main window edits, staged deletion of `view_config_window.py`, modified `data/*.json`). Commit or set that aside first so this refactor starts from a known state, and so tests' private `data/` copy is the baseline.
1. **User type + store + migration** *(done — `c3c790a`)* (`models/types.py`, `models/user_store.py`). Pure; tests for round-trip and for migrating the current `profiles.json` + `board_layout.json`.
2. **Pure logic** *(done — `2046f8c`)*: `user_match.compare(saved, board_user)` (float32-aware), `next_free_name("Ana") -> "Ana#2"`, `preview_24h(user)`. Tests first (TDD).
3. **Firmware + protocol: user name** *(done; hardware 12/12 on 2026-10-06)*. Flashed; name written per slot and read back, other slots untouched, the 30-byte limit enforced (31 refused), UTF-8 round-trips, survives a J-Link reset, and a name write does not reset the sim clock. Verification script: `scripts/hw_user_name.py`. A v5 flash image is **migrated** (slots kept, names empty), not wiped.
4. **Reader + Users screen + toolbar button** *(done; hardware 9/9 on 2026-10-06)*. `models/user_board.py` (pure: `BoardReading` → user, `classify` → `Unknown` / `Matches` / `Differs`, `overwrite`, `create_copy`); `gui/user_reader.py` (one slot, six reads behind the cursor write, all-or-nothing, one at a time, answers taken only from the session asked); `gui/users_window.py` (list, **+ Read from…**, **+ New**, Open, Delete, the overwrite/create question); the **Users** toolbar button; `BoardMode.busy` so a read waits for the shared cursor. Open shows the profile screen (slice 5). Verification: `scripts/hw_user_read.py` through the real `BleSession` + `UserReader`.
5. **User Profile shell + Profile + Model pages, mode gating, Save** *(done, 2026-10-07; no board involved, so no hardware run)*. `models/user_edit.py` (pure: weight is the model's `BW`, switching model keeps the weight, `pages_for(mode)`, what Save checks — a name that is present, ≤ 30 bytes and unique); `gui/user_profile_window.py` (picture + name + menu on the left, pages on the right, **edits a copy** and writes it to the list only on Save, a draft is added by its first Save, Save / Discard / Cancel on close, one `add_page` per later slice); `gui/user_profile_page.py` (picture, name, height, weight, source) and `gui/user_model_page.py` (glucose model + sensor noise, via `gui/param_form.py`); `gui/user_picture.py` (stored as a centred 256 px square PNG on Save); `gui/user_profiles.py` (one window per user). Not yet there: **Preview** and **Send to…** (slice 8) — they are added with the code behind them, not as disabled buttons; the CSV, Food and Exercise pages (slices 6–7) appear in the menu as they are registered.
6. **Food + Exercise pages** *(done, 2026-10-07)*: one `SchedulePage` class with a `FOOD` and an `EXERCISE` configuration (`gui/user_schedule_pages.py`): a 24 h graph above a time-ordered table and an add row, 32 events each (what the board holds; the 33rd is refused with a message). The curves are pure (`models/user_schedule.py`: g/min per minute with midnight wrap, exercise = the stronger of overlapping bouts); the graph is `gui/schedule_graph.py`. Registered in the profile screen between Profile and Model.
7. **CSV page** *(done, 2026-10-07)*: `gui/user_csv_page.py` — **Choose CSV file…** opens the existing CSV Analysis window as a picker (file + 24 h region); the picked window is **copied into the user** (`models/user_csv.py`: glucose resampled to the 5-minute grid, the sibling Food Log's meals, file name and start kept; a wrong file, an unreadable one or an empty window is refused with a reason, a missing Food Log is not an error), so the original file is not needed again. The page describes the window and draws it. It is saved with the user (`data/users/<id>/csv.json`). A user read from a board that replays a recording shows that the board cannot send it back yet.
8. **Preview + Send to…** *(done, 2026-10-07; hardware 9/9)*: Preview is `gui/user_preview_window.py` (the noise-free stepper over the user on screen, meals marked, exercise shaded, thresholds drawn). Send is `gui/user_sender.py` + `models/user_send.py`: choose a connected sensor (the chooser says what each runs now), Save and send / Send without saving / Cancel when there are unsaved edits, then one push — name first, then model + sensor + both schedules cleared and rewritten, or the CSV upload followed by the switch to the CSV source. Refused before touching the board: no name, a CSV user with no window, a dead link, a sensor that does not list `user_name`. Landing records the user on that slot. Sending to a sensor that already has a user overwrites it (decided).
9. **Remove the old** *(done, 2026-10-07)*: the Person, Sensor, Food and Exercise windows, the data-source group, the Configuration window's Person/Sensor group and their controller signals are gone. `AppState` holds users; `board_plan` / `engine_slots` resolve slots by user name; the engine and the push run on profiles derived from a user (`models/user_sim.py`). A slot records only the user (a rename moves it, a delete clears it). Scenarios keep their `person` step (it now names a user; `user` is an alias). ADR 0006 and the docs describe it.
10. **Hardware pass** *(done, 2026-10-07)*: `hw_user_read.py` 9/9, `hw_user_send.py` 9/9, `hw_user_start.py` 6/6 — through the real `BleSession` and the real reader/sender/Start code. It found two things, both fixed: the CSV source was switched on before the upload finished, and Start did not write the name.

## Risks

- **Reading a whole slot is slow** (six GATT reads behind one shared cursor, ~80 ms pacing for writes). The Users screen needs a progress state and a timeout, and must not run during Start's push (same cursor).
- **Float round-trip**: params go through float32 on the wire; comparing raw doubles would flag every user as "differs". Slice 2 covers it.
- **Flash layout bump** — decided and done in slice 3: `user_name[]` is appended *after* `slots[]`, so a v5 image keeps every slot and gets empty names; nothing is wiped. (The first boot of the new firmware on your board took this path; the v5→v6 console line was not captured.)
- **Migration of scenarios and the e2e scripts** that address persons by name is easy to forget; they are the only callers outside the GUI.

## Decisions you made (answers to the review questions)

1. Height: app-only, display only, no use for now.
2. Board name: 30 characters.
3. Matching: by name only.
4. CSV: copy the 24 h window into the user; no path kept.
5. **+ New** exists alongside **+ Read from…**, because not every user will be on the board.
6. Send to a sensor that already has a different user: overwrite, no warning.

## Follow-ups

- **CSV readback** from the board (a user read from a board in CSV mode has no window).
- **The user's picture on the tab** (the single `avatar_icon` call site still draws the initials disc).
- **`scripts/ui_smoke.py`** and **`scripts/e2e_4sensor.py`** still assume the old windows and 4 slots.
- **Windows' stale GATT cache** after a firmware update hides a new characteristic until a connection refreshes it; the app explains it, a forced uncached reconnect is only an idea (`docs/TODO.md`).
