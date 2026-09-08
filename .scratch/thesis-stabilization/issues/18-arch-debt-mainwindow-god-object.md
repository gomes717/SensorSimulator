# Architectural debt: MainWindow god object + window/state seam leaks

Status: (see below)
Track: B
Phase: 3 attempt
Blocked by: —

## 2026-09-08 status

**Partial (2026-09-08).** Done: `EnginePool` (issue 04) pulled the multi-engine logic out; `gui/board_link.py` `BoardLink` (commit 7b12c70) concentrates the ~6 scattered BLE fan-out loops + `_send_instant` + the connected-guards, tested; `_inject_instant_food/_exercise` dedup the 3 Insert-Now handlers vs `_scenario_dispatch`; `decode_notification` (20) + `build_csv_uploads` (19) extracted two more seams; name->slot is one canonical function (19). MainWindow 1622 -> 1583 lines, and ~120 lines of scattered fan-out/dup are now in tested modules.

**Still deferred:** the `ConfigurationWindow` <-> `MainWindow` private-member coupling (a shared controller/state object both talk to). That is a ~1-2 day MVC refactor whose only end-to-end check is a hardware `ui_smoke` pass — the BLE stack is currently wedged. With the defense moved to 2026-10-30 there is now time to do it *after* a hardware `ui_smoke` pass is possible again — no longer a pre-defense risk, just gated on the BLE stack.

## 2026-09-08 update 2 — ConfigController seam done

BLE stack recovered (scanner sees 3/4 identities). Did the `ConfigurationWindow`
<-> `MainWindow` decoupling:

- New `gui/config_controller.py` — `ConfigController(QObject)`, a typed Qt signal
  surface, no behaviour. Widget events go app-ward (`person_selected`,
  `speed_change_requested`, `model_only_toggled`, `comm_profile_toggled`,
  `editor_requested`, `thresholds_saved`, `data_source_edited`); app state
  changes go widget-ward (`profiles_changed`, `speed_display_changed`,
  `controls_locked`, …).
- `ConfigurationWindow.__init__` was `(main, on_thresholds_changed)` reaching
  **13 private members of `MainWindow`** → now `(controller, bluetooth_provider)`
  and reaches **zero**. Combo (re)population moved into the window (it owns the
  widgets); the comm-profile BLE write + reconnect moved to
  `MainWindow._on_comm_profile_toggled` (needs the Bluetooth window).
- `MainWindow` dropped every `self._cfg.<widget>` poke (`_refresh_person_combo`
  / `_refresh_sensor_combo` deleted, `_scenario_dispatch` / `_on_cgms_only`
  / `_set_locked_for_cgms_only` go through the controller). `_on_person_selected`
  / `_on_sensor_selected` now take the profile from the signal.
- `tests/test_config_controller.py` — 7 tests (repopulate keeps selection &
  stays silent, user combo change emits, mode toggles + editor buttons reach the
  app, speed display doesn't echo, controls_locked). Full gate green except the
  intentional `C0302`; `ui_smoke --no-board C,D,E,F` = 4/4 PASS (all drive the
  person combo + speed slider + model toggle through the new seam). `ui_smoke B`
  (single-sensor board) FAILs identically on master — board is in 4-sensor mode.

## 2026-09-08 update 3 — GlucoseGraph extracted

- New `gui/glucose_graph.py` — `GlucoseGraph` owns both matplotlib canvases,
  the plot buffers (`_Buffers` dataclass) and every draw decision (figure build,
  theming, range bands, per-category recolour, rolling-window time-axis sync,
  y-limit fitting, PISA shading, redraw). Artists bundled into `_GlucosePlot` /
  `_FoodExPlot`. Interface: `elapsed_seconds` / `reset` / `bind_buffers` /
  `set_thresholds` / `set_view_window` / `set_glucose_title` / `add_pisa_span` /
  `rebuild_for_theme` / `redraw_glucose` / `redraw_food_ex` / `in_view`.
- `MainWindow` keeps the per-user `_history` and aliases the panel's live
  buffers via `bind_buffers`. Title branching is now one `_set_graph_title`.
  Read-only compat properties (`_graph_x/_y`, `_expected_*`, `_pisa_*`,
  `_visible_xlim`, `_in_view`) keep `scripts/e2e.py` + `scripts/ui_smoke.py`
  unchanged. `test_fe_graph_title.py` moved to the new API.
- Also collapsed the 12 `_open_*` window methods to `_lazy_window` + `_raise`.

## 2026-09-08 update 4 — MainWindow under 1000 lines, C0302 gate GREEN

Two more extractions + a prose trim:

- `gui/user_tree.py` — `UserTree(QTreeWidget)` owns the sensor-list rows, the
  four row dicts and the offline state; `note_message(msg)` /
  `mark_device_offline(address)` / `set_thresholds()`, emits
  `user_selected(user_id)`. `_user_items` / `_offline_users` kept as compat
  properties for the issue-06 test + `e2e_4sensor.py`.
- `gui/instant_events.py` — `InstantEvents` fans a one-shot food / exercise /
  PISA event out to the engine pool, the board and the graph shading, and owns
  the three prompt handlers + slot picker. MainWindow keeps thin
  `_open_insert_*` / `inject_fault` wrappers.
- `gui/range_stats.py` — `RangeStatsPanel(QGroupBox)` is the "Time in range"
  grid; MainWindow feeds it the in-view series. `_stat_value_labels` compat prop.
- Toolbar built from a `_TOOLBAR` table; the verbose `__init__` comment blocks
  and a handful of over-long method docstrings condensed.

**`main_window.py`: 1583 → 998 lines** (−585, −37%). The full pre-commit hook
(`ruff` / `pylint` 10.00 incl. `C0302` / `pyright` / 125 tests) **passes with no
`--no-verify`**. `ui_smoke --no-board C,D,E,F` 4/4 PASS, identical numbers.

Status: **substantially done.** The god object is now a coordinator wiring
`UserTree` + `GlucoseGraph` + `RangeStatsPanel` + `ConfigController` + `BoardLink`
+ `EnginePool` + `InstantEvents`. Left for a future pass (not blocking): the
`_scenario_dispatch` interpreter (carries the `src/gui/*` `C901` ignore) and the
loosely-typed `new_message` dict still lack their own seam.

---

(orig) The full break-up (MainWindow → a controller/model; ConfigurationWindow ↔ MainWindow decoupling; typed `new_message` record) is days of work with a wide regression surface. Deferred while the BLE stack was wedged; defense moved to 2026-10-30 so there is room to do it once ui_smoke is runnable. Kept as the future-maintainer map. Small pieces already landed elsewhere: `EnginePool` (issue 04) pulled the multi-engine logic out; `decode_notification` (issue 20) and `build_csv_uploads` (issue 19) extracted two seams.

---

Not scheduled for the defense. Recorded so a future student inherits the map
instead of rediscovering it. Source: architecture review, 2026-09-08.

## MainWindow is a god object

`src/graphic/main_window.py` — 1456 lines, one class, `__init__(self)` binds ~55
attributes (`main_window.py:71-192`), ~90 methods. `pyproject.toml` raised
pylint `max-attributes` to 15 specifically to silence this. Responsibilities
fused into the one class: toolbar (`:228`), tree widget (`:259`), two matplotlib
graphs incl. theming / recolor / range-bands / PISA-shading (`:267-412`,
`:1335-1430`), run-state machine (`:1173-1210`), engine lifecycle
(`:1094-1141`), BLE broadcast fan-out (`:940-1028`, `:1146-1171`), scenario
dispatch (`:776-840`), instant-event dialogs (`:695-770`), per-user history
buffering (`:1069-1092`), stats panel (`:457-503`).

## Seam leaks

- **`ConfigurationWindow` ⇄ `MainWindow`** share a mutable object graph, no
  interface between them. `ConfigurationWindow.__init__(self, main, ...)` takes
  the whole `MainWindow` and wires every signal to a `_`-private method
  (`configuration_window.py:77`, `:84`, `:98`, `:107`, `:162`, `:185`, `:307`).
  `MainWindow` pokes back into `self._cfg.<widget>` for ~11 widgets and the
  private `_speed_to_slider` (`main_window.py:784`, `:980-993`, `:887`, `:910`).
- **Config windows encode wire bytes themselves.** `person_config_window`,
  `board_layout_window`, `configuration_window` all import `api.protocol` and
  build char-key + payload programs inline (`board_layout_window.py:238-251` is a
  24-write BLE program). The wire format is behind no seam.
- **CSV-upload dict assembly duplicated** near-verbatim in
  `configuration_window._send_csv_to_board` and
  `board_layout_window._build_slots`.
- **GUI windows sequence board run-state** — inline `from graphic.device_target
  import restart_board` + `queue_write` + `reconnect` in
  `configuration_window.py:380-391`, `:203-204`, `person_config_window.py:282`.
- **`MainWindow` → engine / BleSession internals** — `add_instant_*`,
  `pause/resume/set_paused`, manual `expected_reading.disconnect(...)` before
  `stop()` with a 12-line race comment (`main_window.py:1108-1115`); broadcast
  fan-out re-does `for session in self._bluetooth_window.sessions().values()` in
  ~6 methods.
- **Loosely-typed `new_message` dict** — key set defined only by what
  `BleSession._handle_notification` happens to `.update()` (`ble_session.py:561-601`),
  consumed by string key in `main_window`, `ble_message_log`, `debug_window`. No
  schema. (Deferred out of Phase 1 by grill decision Q9 — extraction #1 only.)
- **"Advertised name → slot" duplicated** — trailing-number regex in
  `ble_session.__init__` (`:171`) and `board_layout.slot_of` (`:21`); 1-based vs
  0-based convention restated in three docstrings.

## Concepts that force cross-module bouncing

"How does one config value reach the board" = 7 modules. "Which patient/slot is
this BLE identity" = 5 sites. "Run state" = 3 hand-synced representations
(`_run_state` string, `engine._paused`, `protocol.RUN_STATE_*`).
"Expected vs received timeline" = 6 `MainWindow` methods + engine emit.

## Manual fan-out on profile edit

`_on_profiles_changed` (`main_window.py:858-878`) is a ~20-line hand-written
fan-out: persist → refresh 2 combos → `self._cfg.reload_data_source()` →
`_board_layout_window.reload_profiles()` → `_person_config_window.reload()` →
both food/exercise windows' `.refresh()` → `_restart_engine()`. Every new window
that observes profiles has to be wired in here by hand.

## If it is ever picked up

Introduce an interface between the config windows and app state (a small
controller/model object, not the whole `MainWindow`); put char-key + payload
construction behind one seam owned by `BleSession` or a session facade; give
`new_message` a typed record. None of this is needed for the defense.
