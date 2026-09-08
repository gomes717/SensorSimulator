# BLE timeslot contention between the sensor identities

Status: ready
Track: A
Phase: 1
Blocked by: —

## Problem

`docs/TODO.md`: "Arrumar o timeslot do BLE entre os 'sensores' (multi-identidade)."
Multi-sensor is a load-bearing contribution (`PROTOCOL_SPEC.md:697-772`,
`docs/architecture_flows.tex:370-439`). With up to 4 BLE identities advertising +
connected on one radio, notifications from different slots appear to collide /
drop — `scripts/e2e_4sensor.py` F2 (per-identity demux) already SKIPs "when WinRT
drops the notify subscription across 4 same-UUID service instances"
(`docs/E2E_TEST_PLAN.md:413-443`).

## What to do

Diagnose whether the contention is:
- firmware radio scheduling — connection interval / event length / advertising
  interval per identity (Kconfig + adv params in `main.c`), or
- the Windows/bleak side dropping subscriptions under multi-connection load.

Tune the firmware connection parameters so 4 slots stream without collision, or
document the Windows-stack limit as a measured result if it is not fixable on the
board side (consistent with how the pairing limitation is already written up).

## Done when

- 4 slots stream concurrently for a sustained window with no dropped/interleaved
  notifications attributable to timeslot contention.
- `e2e_4sensor.py` F2 runs instead of SKIPping, or the plan documents exactly why
  it can't on Windows.
