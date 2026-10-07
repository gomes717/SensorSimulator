# 0002 — The board is the source of truth for per-sensor state

Status: accepted (2026-09, extended 2026-10) · the expected-model bullet below is
superseded by [0005](0005-start-pushes-the-app-profile.md)

## Context

The app keeps a saved profile per patient and a record of which patient it last
sent to which slot. That is the app's *intent*, not what the board is running. A
CSV uploaded in an earlier session, a configuration changed from elsewhere, or a
send that silently failed (the write was queued on a dropped link) all leave the
two disagreeing. Showing the intent as if it were the fact produced a graph named
after a model for a sensor that was replaying a CSV, a food/exercise panel and a
title that contradicted each other, and an "expected" model line drawn behind a
recording.

## Decision

Wherever the board can be asked, ask it and display the answer.

- `BoardMode` reads each slot's **Data Source** and **Person Config**
  characteristics back, and attributes each answer to the session that delivered
  it — never to "whatever sensor is selected now" (that misattribution was a real
  bug).
- Every per-sensor display decision follows it: the graph title ("CSV replay" or
  the model's name), the food/exercise graph, whether the Commands panel is shown,
  and whether an expected line exists. Until the board answers, the title says
  "waiting for the board to confirm…" rather than guessing.
- ~~**The local expected model is built from the board's own Person Config**~~
  *(superseded by [0005](0005-start-pushes-the-app-profile.md): Start now writes the
  app's profile to the board and the expected model is built from that same profile.)*
  Reads stay serialized (one slot at a time) because the Sensor-select cursor is one
  value on the board shared by every connection.
- Choosing a patient in a config window is not a send and does not reset the graphs;
  only what changes the run does (Start, a Send to Board, speed, Model Only).
- Expected-model ticks for a slot the board reports as CSV are dropped, and an
  expected line already drawn is discarded the moment the board reveals CSV.
- Model Only and the empty page have no board to ask, so they read the saved
  profile directly.
- Writes that go nowhere say so: run-state broadcasts report "reached N of M
  sensors", one-shot commands report "NOT sent — no live link", and a sensor tab
  whose link has dropped is greyed and has its commands disabled.

## Consequences

- A profile saved as CSV but never sent to the board does not flip any display —
  correct, because the board is not replaying anything.
- Display state can lag a mode change by one read round-trip.
- New per-sensor UI should ask `BoardMode` (or the link state), not the profile.
