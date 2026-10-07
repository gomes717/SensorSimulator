# 0004 — Decompose `MainWindow` into owners of one concern each

Status: accepted (2026-10) · Completes issue 18 (MainWindow god object)

## Context

`MainWindow` held the app state, the run state machine, the engine pool, the
per-sensor history and the shared-graph rebinding, window creation, and the glue
between all of them: ~1,180 lines and a standing `too-many-instance-attributes`
waiver. Tests and hardware harnesses reached into its private attributes.

## Decision

Each concern moves to a piece that owns it and talks to the others through
constructor-supplied callbacks and Qt signals, never by reaching into the window:

| Concern | Owner |
|---|---|
| Profiles, board layout, settings, Model/CGMS-only flags | `AppState` |
| Run state machine, toolbar Start/Stop, the "blocked until a sensor is live" rule | `RunController` |
| The shared plot timeline | `RunClock` |
| Engine pool, the single restart point, expected-line / PISA routing | `SimulationCoordinator` |
| Slot ⇄ session ⇄ label ⇄ live lookups | `SensorDirectory` |
| Tab strip, start screen, page stack, tab alerts | `SensorTabs` (`SensorPages`, `SensorPage`) |
| Selection, titles, CSV view, command wiring, BLE data routing | `SensorController` |
| One-shot events fan-out | `InstantEvents` |
| Lazy secondary windows | `ChildWindows` |
| Toolbar construction | `toolbar.build_toolbar` |

`MainWindow` keeps composition and the few actions that cut across pieces (speed,
mode toggles, profile / person / sensor changes). It is ~370 lines, the
`too-many-instance-attributes` waiver is gone, and no `# pylint: disable` was added
(a project rule: fix the finding or leave the gate red).

## Consequences

- Each piece is unit-testable without the window (`test_run_controller`,
  `test_sensor_tabs`, `test_sensor_pages`, `test_commands_panel`, `test_alerts`,
  `test_commands_routing`).
- The compatibility properties that kept the hardware scripts working
  (`window._graph_y`, `window._user_items`, …) were deleted; scripts and tests
  read through the owners (`window.sensors.current_page()`, `window.tabs`,
  `window.sim`, `window.state`, `window.directory`).
- Order of construction matters where pieces refer to each other lazily
  (`BoardMode` ↔ `SimulationCoordinator` ↔ `SensorController`); the lambdas in
  `MainWindow.__init__` are intentional late bindings.
