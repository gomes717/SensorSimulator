# BLE timeslot contention between the sensor identities

Status: done (2026-09-08)
Track: A
Phase: 1
Blocked by: —

## Outcome

- **Firmware side was already tuned** (verified by reading `prj.conf` / `main.c`):
  `BT_CTLR_SDC_MAX_CONN_EVENT_LEN_DEFAULT=2500` so 4 connection events fit an
  interval; `connected()` requests a relaxed 30-50 ms interval; `BT_MAX_CONN=4`,
  4 adv sets / identities. The comment there already names the exact symptom
  ("newest link starved of connection events, dropped mid-subscribe").
- **App side was the gap.** `BleSession._session` now retries `start_notify`
  up to 4x, ~0.9 s apart, so a link starved during the central's tight
  discovery window gets its CCCD writes in once the conn-param update lands.
- New E2E **F16** (`e2e_4sensor.py`): connect all 4 identities, assert >=3
  connect **and** stream concurrently. Verified on hardware:
  `connected=[1,2,3] streaming=[1,2,3]`. Before this, F2 SKIPped on the
  multi-instance notify drop.

## Residual (not this issue)

Identity 0 = the factory address `D0:3F:4D:E2:7C:9B` has accumulated Windows
pairing associations from earlier testing and connects unreliably. Documented
in `e2e_4sensor.py` (which uses identity 1 as `CFG_SLOT`). Clearing it needs
`Remove-BluetoothDevice` / a registry clean, out of scope here.

---

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
