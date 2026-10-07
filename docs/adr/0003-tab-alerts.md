# 0003 — Tab alerts: one definition of "out of range", blink until seen

Status: accepted (2026-10)

## Context

With one tab per sensor, a user watching one tab can miss a sensor going out of
range on another. Two places already classified a reading against the clinical
thresholds independently (the graph's red/yellow/green line, and the old list's
LOW/HIGH badge), so they could drift apart.

## Decision

- **One definition.** `models/alerts.py: alert_for(glucose, thresholds)` returns a
  level (normal / warning / critical) and a direction. The graph colouring and the
  tabs both use it, so a tab's amber/red always matches the line's yellow/red.

  | Level | Condition (defaults) |
  |---|---|
  | normal | 70 ≤ g ≤ 180 mg/dL |
  | warning | 54 ≤ g < 70 or 180 < g ≤ 250 |
  | critical | g < 54 or g > 250 |

- **Icon, not number.** The tab shows no value (it is a status, not a readout): a
  warning is an amber triangle, critical a red one. The tooltip gives the value and
  direction ("262 mg/dL — critically high"), so the level is never conveyed by
  colour alone.
- **Critical blinks until seen.** A critical tab blinks red until the user opens
  it, then stays solid red until the reading leaves the critical range. If it is
  already the open tab when it goes critical, it goes straight to solid.
  Recovering and going critical again blinks afresh. So a sensor parked at a
  critical value flags once for attention instead of blinking for the whole
  session.
- **Offline clears the alert.** A stale reading must not keep alarming; the tab
  greys out.
- **One shared blink timer** (500 ms), running only while some tab is
  unacknowledged-critical.
- Changing the thresholds re-evaluates every tab immediately.

## Consequences

- The warning level starts at the same thresholds as the old LOW/HIGH badge, so
  nothing alerts earlier or later than before; critical is the new escalation.
- Acknowledgement is per tab and in-memory; it is not persisted and there is no
  audible alert (deliberately out of scope).
