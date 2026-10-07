# 0001 — One independent page per sensor, not one shared graph

Status: accepted (2026-10) · Supersedes the "bind the shared graph to the selected row" design

## Context

The main window had a single `GlucoseGraph`. Per-sensor history lived in a dict
keyed by `user_id`, and selecting a row *aliased* the graph's live buffers onto
that sensor's lists (`bind_buffers`). Every consequence of that showed up as a
bug:

- every handler had to ask "is this the selected sensor?" before redrawing, so
  data routing and display state were tangled together in `MainWindow`;
- one PISA span list was shared, so a fault inserted for one sensor shaded every
  sensor's graph;
- history was keyed by a label frozen at connect time, so re-assigning a slot
  split a sensor's received line from its expected line;
- the expected-line routing had a `per_slot_expected` branch just to cope with
  one sensor vs several.

The UI is also moving to one tab per sensor, which wants a per-sensor view anyway.

## Decision

Each sensor gets a `SensorPage` that owns its own two canvases (`GlucoseGraph`),
history buffers, `RangeStatsPanel` and `CommandsPanel`. A page is created the
first time its sensor is seen and discarded when its tab is closed. A message or
an expected-model tick is appended to *its* page and nowhere else. Switching tabs
brings another page forward and rebinds nothing.

The only shared state is the run timeline — `RunClock` (the run's t=0 and the
speed multiplier) — which every page reads, so Start re-anchors all of them at
once and every sensor's received and expected lines stay on one origin.

Pages are keyed by session id and carry their board slot, so the expected line
(routed by slot) and the received line (routed by session) land on the same page.

## Consequences

- One set of matplotlib canvases per sensor instead of one. Accepted: a page that
  is not on screen still records but defers its redraw until it is shown, so the
  cost is memory and a catch-up redraw on switch, not N redraws per tick.
- `bind_buffers`, `_history`, `_hist`, `per-slot expected` branching and the PISA
  routing workaround were deleted. PISA shading is per page by construction.
- Closing a tab discards that sensor's graph history. A sensor that merely drops
  keeps its page (greyed tab) and resumes on reconnect.
- Hardware harnesses read the plotted series through the page on screen
  (`window.sensors.current_page().graph.buf…`).
