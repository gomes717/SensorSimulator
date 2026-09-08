# Architectural debt: MainWindow god object + window/state seam leaks

Status: (see below)
Track: B
Phase: 3 attempt
Blocked by: —

## 2026-09-08 status

**Partial (2026-09-08).** Done: `EnginePool` (issue 04) pulled the multi-engine logic out; `gui/board_link.py` `BoardLink` (commit 7b12c70) concentrates the ~6 scattered BLE fan-out loops + `_send_instant` + the connected-guards, tested; `_inject_instant_food/_exercise` dedup the 3 Insert-Now handlers vs `_scenario_dispatch`; `decode_notification` (20) + `build_csv_uploads` (19) extracted two more seams; name->slot is one canonical function (19). MainWindow 1622 -> 1583 lines, and ~120 lines of scattered fan-out/dup are now in tested modules.

**Still deferred:** the `ConfigurationWindow` <-> `MainWindow` private-member coupling (a shared controller/state object both talk to). That is a ~1-2 day MVC refactor whose only end-to-end check is a hardware `ui_smoke` pass — the BLE stack is wedged and the defense is 2026-09-15, so the regression risk isn't justified now. Do it post-defense (or when `ui_smoke` is runnable).

---

(orig) The full break-up (MainWindow → a controller/model; ConfigurationWindow ↔ MainWindow decoupling; typed `new_message` record) is days of work with a wide regression surface. With the defense on 2026-09-15 and Phase 1 (correctness) done, the risk isn't justified. Kept as the future-maintainer map. Small pieces already landed elsewhere: `EnginePool` (issue 04) pulled the multi-engine logic out; `decode_notification` (issue 20) and `build_csv_uploads` (issue 19) extracted two seams.

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
