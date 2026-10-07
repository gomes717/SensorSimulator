# Tabbed sensor UI — independent pages, a slim MainWindow, toolbar run controls, per-tab commands

Status: implemented in code (slices 1-6) and documented; hardware verification of the tab flow still pending. Revision 5 (all open questions answered).

## Decisions taken (yours, from the review)

- **Avatars: deferred.** Out of scope until you've looked at the profile design. The
  tab keeps using the existing generated initials disc; the tab header has one
  `avatar_icon(...)` call site so a profile avatar can slot in later.
- **"Maintain blocked" = disabled until a sensor is connected.** Applies to Start/Stop
  *and* each tab's commands panel (per tab: that sensor's link must be live). Model Only
  mode has no board, so it stays enabled there.
- **Send Commands lives inside each sensor's tab**, not in a separate window, and acts
  only on that tab's sensor — no target selector.
- **Tab × disconnects** the sensor and closes its tab — no confirmation, whether the tab
  is live or offline. (Consequence accepted: the page's graph history is discarded.)
- **Model Only = one synthetic page** ("Model — <person>") in the same tab UI, fed by the
  engine, with no sensor tabs while the mode is on.
- **Critical blink stops once you've seen it:** a critical tab blinks until you open it,
  then stays solid red until the sensor recovers (see *Tab alerts*).
- **Independent pages, not a shared graph.** Every sensor tab owns its own graph,
  history and stats panel, even though that is slower than rebinding one graph.
- **Fix the 1k+ line `main_window.py`** as part of this, not after it.

## Why MainWindow is 1176 lines (what the refactor has to dismantle)

It is the app's state + five controllers + the glue between them:

| Concern living in `main_window.py` | ~lines | New home |
|---|---|---|
| Window/toolbar plumbing (`_open_*`, `_lazy_window`, `_raise`, `closeEvent` loop) | 130 | `WindowRegistry` + `toolbar.py` |
| Run state machine (start/pause/stop, run-state broadcast + status text, CGMS-only lock) | 150 | `RunController` |
| Engine/slot coordination (`_engine_slots`, `_restart_engine`, `_person_by_name`, expected-reading routing) | 160 | `SimulationCoordinator` |
| Per-user history + the shared-graph rebinding (`_history`, `_hist`, `_bind_selected_history`, `_per_slot_expected`, `_record_pisa_span`, `_slot_user_id`) | 170 | **disappears** into `SensorPage` |
| Selection-dependent view logic (CSV hide, titles, `_selected_slot`, `_selected_person`) | 140 | `SensorPage` (it knows its own slot) |
| Compatibility properties for the scripts (`_graph_x`, `_user_items`, …) | 55 | deleted; scripts updated |
| Profiles, speed, thresholds, board-layout persistence (`_on_profiles_changed`, `record_slot_assignment`, …) | 150 | `AppState` |
| BLE message routing | 40 | `SensorTabs.route(msg)` |

Target: `main_window.py` ≤ ~300 lines — construct the pieces, wire signals, nothing else.
The `too-many-instance-attributes` pylint waiver is deleted, not re-added (project rule:
fix, don't silence).

## Why independent pages simplify things

The shared graph is the root of most of the tangle: buffers are *aliased* onto the
selected user's lists (`bind_buffers`), so every handler must ask "is this user the
selected one?" before redrawing, expected-line routing has a `_per_slot_expected`
branch, PISA shading needs `_record_pisa_span` to avoid bleeding onto other sensors
(an open `docs/TODO.md` item), and history is keyed by a `user_id` string that goes
stale when a slot is re-assigned (the `_slot_user_id` workaround).

With one page per sensor, none of that exists: a message goes to its page, the page
appends to its own buffers and redraws itself. Open TODO items that fall out for free:
"PISA shadow on every sensor's graph" and "food/exercise title uses the saved profile,
not the board's answer" (the page asks the board about *its own* slot).

## New structure

```
MainWindow (≤300 lines: composition + signal wiring)
 ├─ toolbar:  [Start][Stop] ... View Configuration Scenario Bluetooth Debug
 ├─ SensorTabs (QStackedWidget)
 │    ├─ page 0: EmptyState  — centered [Connect Bluetooth] + "Connect a sensor to begin"
 │    └─ page 1: QTabBar + QStackedWidget of SensorPage
 │                 SensorPage = GlucoseGraph (own canvases) + own history buffers
 │                              + RangeStatsPanel + title/CSV-view logic for its slot
 │                              + CommandsPanel (Food / Exercise / PISA → this sensor)
 ├─ RunController        run state, Start/Pause/Stop, broadcasts, enabled rule
 ├─ SimulationCoordinator EnginePool, slot→person, restart, expected line → page by slot
 ├─ AppState             profiles, active person/sensor, board layout, speed, thresholds
 ├─ WindowRegistry       lazy child windows + close-all
```

Key rules:
- **Page identity** = board slot when the session is a numbered identity, else the
  device address. Not the display label — so a re-assigned slot keeps its page and
  a reconnect after a board power-cycle revives the same page and history.
- **Shared timeline:** a small `RunClock` (graph_t0 + speed multiplier) owned by
  `RunController`, read by every page, so all pages stay on the same origin and
  "Start" re-anchors them together. `GlucoseGraph` stops owning `graph_t0`/`speed_mult`.
- **`GlucoseGraph` owns its buffers directly** (`append_received`, `append_expected`,
  `append_food_ex`, `add_pisa_span`); `bind_buffers` and the aliasing go away.
- **Model Only** shows one synthetic page ("Model — <person>"), fed by the engine; no
  sensor tabs while it is on. It has no board, so it has no link state, no alerts-from-BLE
  and no × (leave it by turning Model Only off).
- **Tab states:** live · "offline" (greyed, history kept, shown on link drop) · closed
  (× → disconnect + discard the page, no confirmation). Tab shows
  initials disc + name + latest value + alert icon (see *Tab alerts*), and appears when
  the session *connects* (shows "—" until data), not on first glucose message.
- **Performance** (the accepted cost): N canvases instead of one. Mitigation that costs
  little: only the *visible* page redraws; hidden pages mark themselves dirty and
  redraw when their tab is shown. At x1000 this must be measured (see risks).

## Tab alerts (warning icon + critical blink)

Each tab reports its sensor's latest glucose against the same thresholds the graph
bands use (`app_settings`: 54 / 70 / 180 / 250 mg/dL by default):

| Level | Condition | Tab shows |
|---|---|---|
| normal | 70 ≤ g ≤ 180 | no icon |
| **warning** | 54 ≤ g < 70 or 180 < g ≤ 250 | amber ⚠ triangle + ▼ (below) / ▲ (above) |
| **critical** | g < 54 or g > 250 | red ⚠ triangle + ▼/▲, tab **blinks red** |
| offline / no data | session down or link silent | greyed, alert cleared (a stale reading must not keep alarming) |

- **Not colour alone:** the direction arrow and the triangle carry the meaning too
  (colour-blind users, and the blink may be off-screen). Tooltip on the tab spells it
  out: "Sensor 2 — 262 mg/dL, critically high".
- **Blink acknowledgement (decided):** a critical tab blinks only while it is
  *unacknowledged*. Opening the tab — or it already being the selected tab when the
  sensor goes critical — acknowledges it: the tab turns solid red and stays solid, even
  if you switch away, until the glucose returns to a non-critical level. Going critical
  *again* afterwards starts a fresh, unacknowledged blink. A sensor parked at 399 therefore
  blinks once for attention, not all session. State per page: `ack_critical: bool`,
  cleared when the level leaves *critical*.
- **Blink mechanics:** one shared `QTimer` (~500 ms) owned by `SensorTabs` toggles a
  phase flag; every critical tab repaints from it. Per-tab timers would multiply with
  the sensor count. The timer runs only while at least one tab is critical.
- **One definition of the levels.** The graph (`GlucoseGraph._category`: r/y/g) and
  the sensor list (`UserTree._update_alert`) each re-implement this today. Both move to
  one pure function (`alert_level(glucose, thresholds) -> normal|warning|critical` plus
  direction) that the tab, the graph colouring and the tests share, so the tab's
  amber/red can never disagree with the line's yellow/red.
- **Thresholds edited in Configuration** re-evaluate every tab immediately
  (`_on_thresholds_changed` already exists; tabs subscribe).
- **Behaviour change to note:** today's badge text ("▲ HIGH"/"▼ LOW") appears from the
  first threshold (180/70); the new warning level starts at the same point, so nothing
  alerts earlier or later than now — critical is the new escalation.
- Optional (default off, not in this plan unless you want it): sound on critical.

## Commands panel (per tab)

Each `SensorPage` contains a collapsible **Commands** panel under its graphs, with three
compact sections side by side — **Food**, **Exercise**, **PISA** — each with its own
Send button and a one-line result ("✓ Food 45 g sent to Sensor 2"). Everything it sends
goes to *the page's own slot*:

- **No Target selector, no "which row is selected?" lookup.** The page hands its slot to
  `InstantEvents`; today `InstantEvents` takes a `selected_slot` callable and asks the
  window — that indirection (and its misattribution risk) goes away. "All sensors"
  disappears with it; if you ever need a broadcast it is a separate, deliberate control.
- **PISA shading** is drawn on this page's own graph, which is also exactly where the
  command went.
- **Blocked rule, per tab:** a panel's Send buttons are enabled only while *that*
  sensor's link is live (and the run is running, as the Insert buttons are today). An
  offline tab shows the panel disabled with "Sensor offline" — you can't fire a command
  at a sensor that isn't there. Model Only's synthetic page is always enabled.
- **CSV-replay sensors: the whole Commands panel is hidden** (not disabled). A CSV
  sensor replays a recording — nothing the panel sends (food, exercise, PISA) is
  meaningful there — so the page shows only its graph and stats. Same rule and same
  trigger as the existing CSV handling: the **board's confirmed answer** for that slot
  (`BoardMode`, "CSV replay"), never the app's saved profile, and visible again as soon as
  the board reports model mode. Until the board has answered, the panel is shown (same
  "don't guess" default as the food graph), disabled by the usual link rule.
- Reuses the existing one-shot code (`InstantEvents`, `inject_fault`); no protocol
  change. The spin rows from `instant_event_dialog.py` are lifted into the panel; the
  modal dialogs and the three bottom Insert buttons go. The toolbar gets no Send
  Commands button.
- **Proposed: expanded by default** (commands are a core action of the page), collapsible
  so the graphs can take the room back.

## Slices — each ends with the full suite green and a runnable app

Behaviour-preserving extractions go first, so the 213 existing tests are the safety net
while code moves; UI changes ride on top of already-extracted pieces.

**1. WindowRegistry + toolbar module.** Pure move of window plumbing out of
`MainWindow`. No behaviour change. (−~130 lines)

**2. RunController + toolbar Start/Stop + blocked rule.** Extract the run state machine;
Start/Stop move to the toolbar (keep attribute names `_start_pause_btn`/`_stop_btn`);
enabled only with ≥1 live sensor, or Model Only. Introduces `RunClock`.
*Tests:* state-machine tests without Qt widgets; enabled-rule widget test.

**3. CommandsPanel widget (still one shared panel).** Build the panel as a standalone
widget that takes the sensor it targets, and host it where the Insert buttons are now,
bound to the currently selected row. Remove the three Insert buttons and the modal
dialogs; `_open_insert_*` wrappers go (scripts updated). Behaviour is unchanged for the
user, but the panel is already self-contained — so slice 4 just *moves* it.
*Tests:* existing instant-event tests; panel sends to the slot it was given.

**4. SensorPage (independent graphs), UI still tree-driven.** Build `SensorPage`, each
owning its own `CommandsPanel` (fixed slot; `InstantEvents` no longer asks the window
which row is selected); make
`GlucoseGraph` own its buffers; one page per sensor shown in a `QStackedWidget` driven
by the *existing* `UserTree` selection. Visually unchanged, but now independent:
`_history`, `_hist`, `_bind_selected_history`, `bind_buffers`, `_per_slot_expected` and
`_record_pisa_span` are deleted. Page-keying by slot lands here.
*Tests:* port `test_multi_slot_engines`, `test_board_mode_fixes`, `test_fe_graph_title`
to pages; new: a message for sensor A never touches sensor B's buffers; PISA on one slot
shades only that page.

**5. Tabs + empty state.** `SensorTabs` replaces `UserTree` (same state + offline
logic, now a tab bar); empty-state page; tab created on `connected`, greyed on drop,
× disconnects (no confirmation). `BluetoothWindow` gets session-opened/closed signals.
Includes the alert icons + critical blink (*Tab alerts*): `alert_level()` extracted and
reused by `GlucoseGraph`, custom tab painting for the icon, shared blink timer.
*Tests:* headless tab-state tests; `alert_level` boundary table (54/70/180/250 exact
values, both directions, thresholds changed); acknowledgement rules (opening a critical tab stops its blink; recovering then going critical again blinks anew); blink-phase test driven by calling the
timer slot directly (no real waiting); offline clears the alert; ui_smoke screenshots of
both states plus normal/warning/critical/offline tabs.

**6. SimulationCoordinator + AppState; delete compat properties.** Move the engine/slot
and profile/settings logic out; update `scripts/e2e*.py`, `ui_smoke.py` and tests to read
from the current page instead of `window._graph_y` etc. Check the line budget.
*Done when:* `main_window.py` ≤ ~300 lines, waiver removed, pylint 10/10.

Slices 1–3 are independent and small; 4 is the structural one; 5 is the visible one; 6
is the clean-up that makes the file small. Stop after any slice and the app still works.

## What this touches outside `src/`

- `scripts/e2e.py`, `e2e_4sensor.py`, `ui_smoke.py` read `window._graph_y`, `_user_items`,
  `_pisa_*`, `_visible_xlim`, `_start_pause_btn`, `_insert_*` directly (≈60 references).
  They move to `window.tabs.current_page()` / page accessors in slice 6 (and slice 3 for
  the insert calls).
- Tests with references to rewrite: `test_multi_slot_engines`, `test_board_mode_fixes`,
  `test_fe_graph_title`, `test_disconnect_row`, `test_scenario_dispatch`, `test_csv_replay`.
- `docs/TODO.md`: close the two items above when slice 4 lands.

## Risks

- **Slice 4 is the real risk.** It changes where every received/expected/PISA sample
  goes. Keep the tree-driven UI for that slice precisely so behaviour can be compared
  before and after (same ui_smoke run, same 3-sensor harness) — don't combine with tabs.
- **Redraw cost at x1000.** N pages × per-tick redraw. Visible-page-only redraw is the
  mitigation; measure with the 3-sensor harness at x1000 (`scripts/e2e_long_3sensor.py`)
  before and after, and report the number rather than assuming.
- **Reconnect races** (just fixed in `BluetoothWindow`): page creation/greying must key
  on the *current* session only; a late signal from a replaced session must not grey a
  live tab. Slot-keyed pages make this easier, but test it.
- **Matplotlib canvases per page** also multiply theme-rebuild work
  (`rebuild_for_theme`) — `SensorTabs` must fan the rebuild out to every page.
- **Blink load:** a blinking tab repaints twice a second — cheap with one shared timer,
  and bounded because acknowledged tabs stop blinking; the timer stops when no tab is
  unacknowledged-critical.
- **Close-while-running:** × on a sensor mid-run discards its history with no confirm
  (your decision); an accidental click loses that sensor's graph. If that proves
  annoying in practice, a one-line undo toast is the cheap fix — not in this plan.
- **Commands to the right sensor** is the whole point of this change, so it gets its own
  test: with three pages open, a Food/Exercise/PISA command from tab B reaches only slot
  B's engine, board writes and graph — never the "selected row" or another tab. Plus the
  hardware check after slice 5: send a command on each of three tabs and confirm only
  that sensor's trace reacts.
- **CSV panel hiding** follows the board's answer, so it appears/disappears a moment
  after a mode change (when the read comes back). Test: a page flips to CSV → panel
  hidden, flips back → shown; and a profile saved as CSV but never sent leaves it shown.
- Hardware check after slice 5 also covers alerts: drive a sensor high/low (Insert Food
  / PISA) and see warning → critical on its own tab only, with the others unaffected.
- Hardware check after slice 5: three sensors connect → three tabs appear on connect;
  switching tabs shows the right trace with no rebinding; power-cycle the board → tabs
  grey, reconnect → same tabs revive with history; × → sensor actually disconnects.

## Deferred

- Patient avatars (picker, `PersonProfile.avatar`, bundled shareicon set + attribution).
  Needs your design review first; asset licensing must be checked per file.
- Custom avatar upload.

## Open questions

None outstanding.
