"""Overnight test of switching the board between standard BLE (3 sensors) and Dexcom G6 (1 sensor).

    uv run python scripts/overnight_modeswitch.py --until 07:00
    uv run python scripts/overnight_modeswitch.py --minutes 20     # a short trial

Run it with the board-mode firmware, COM10 free and the app closed. It starts from Multi mode and
repeats, for hours::

    Multi -> Dexcom -> Multi -> ...

switching the way the app does (a write of the board-mode characteristic over BLE). Cycle kinds
rotate (``KINDS``): a *full* cycle reads a G6 reading in the first window; a *quick* one flips back
as soon as the new adverts are seen; a *dwell* one stays in Dexcom for a minute; a *long* one waits
for the second reading (5 minutes) and checks the window opens and closes.

After every switch it checks, against the radio and the board's console:

* Dexcom mode: ``Dexcom00`` and ``Nordic Glucose Sensor 1`` advertise, Sensors 2 and 3 do not; the
  console logged the switch; a G6 client authenticates and reads the reading the board logged; the
  app's identity reads the mode as Dexcom;
* Multi mode: exactly three sensors advertise and no G6; all three connect and stream CGMS
  notifications; the three slots' stored configuration (person, sensor, data source, name) is
  byte-identical to what it was before the first switch;
* always: the console shows exactly the switches that were asked for, no reboot, no fault.

REPORT.md is rewritten after every cycle; the board is left in Multi mode. Pure scoring:
``overnight_modeswitch_analysis.py`` (unit-tested).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
import time
from datetime import datetime
from pathlib import Path

import overnight_dexcom_analysis as oda_dex
import overnight_modeswitch_analysis as oma
from ble_overnight import deadline_from, keep_awake, minutes_left
from hw_common import (
    ARTIFACTS,
    SerialCapture,
    config_uuid,
    connect,
    pick_config_device,
    retry_ble,
    select_slot,
)
from hw_dexcom_mode import (
    APP_NAME,
    G6_NAME,
    board_readings,
    g6_session,
    read_comm_profile,
    scan,
    switch_mode,
)

from api import ble_uuids
from services import ble_session

# One name per kind, repeated over the night. "full" is the baseline; the others vary the dwell.
KINDS = ("full", "quick", "full", "quick", "dwell", "quick", "long", "full")
SLOTS = (0, 1, 2)
FINGERPRINT = {
    "person": ble_uuids.PERSON_CONFIG_UUID,
    "sensor": ble_uuids.SENSOR_CONFIG_UUID,
    "data_source": config_uuid(0x11),
    "name": config_uuid(0x16),
}
SWITCH_TIMEOUT_S = 45.0  # the new adverts must be visible this long after the write
NOTIFY_WAIT_S = 30.0  # each Multi sensor must deliver a notification within this
DWELL_S = 60.0
WINDOW_PROBE_S = 75.0  # the G6 window (30 s) is long shut by then
MODE_WRITE_TRIES = 3
CONSOLE_IDLE_S = (
    20.0  # the running board prints about once a second: this much silence = a dead reader
)
MAX_CONSECUTIVE_BAD = 4  # cycles in a row that fail before the run gives up
SUBSCRIBE_NOTIFICATIONS = 2


def stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def say(text: str) -> None:
    print(f"[{stamp()}] {text}", flush=True)


# ---- the radio -----------------------------------------------------------------------------------


async def wait_for_mode(expect: str) -> tuple[float | None, dict[str, object], str]:
    """Scan until the adverts show *expect*; ``(seconds, {name: device}, detail)``."""
    start = time.monotonic()
    found: dict[str, object] = {}
    detail = ""
    while time.monotonic() - start < SWITCH_TIMEOUT_S:
        found = await scan(4.0)
        mode, detail = oma.classify_adverts(found, G6_NAME)
        if mode == expect:
            return time.monotonic() - start, found, ""
    return None, found, detail


def config_device(found: dict[str, object]):
    """The identity to write the mode through in Multi mode (identity 1, as the other harnesses)."""
    named = sorted((n, d) for n, d in found.items() if n.startswith(oma.SENSOR_PREFIX))
    return pick_config_device(named)


def own_measurement(client, slot: int):
    """*slot*'s own CGM Measurement characteristic (one CGMS service instance per slot)."""
    instance = -1
    for service in client.services:
        if service.uuid.lower() != ble_session.CGM_SERVICE_UUID:
            continue
        instance += 1
        if instance == slot:
            return next(
                (
                    c
                    for c in service.characteristics
                    if c.uuid.lower() == ble_session.CGM_MEASUREMENT_UUID
                ),
                None,
            )
    return None


async def multi_streams(found: dict[str, object]) -> dict[str, str]:
    """Connect to each of the three sensors and wait for notifications: ``{name: problem}``."""
    problems: dict[str, str] = {}
    clients = []
    counts: dict[str, int] = {}
    try:
        for name in sorted(oma.MULTI_NAMES):
            device = found.get(name)
            if device is None:
                problems[name] = "not advertising"
                continue
            client = await connect(device)
            if client is None:
                problems[name] = "could not connect"
                continue
            clients.append(client)
            slot = int(name.rsplit(" ", 1)[-1]) - 1
            char = own_measurement(client, slot)
            if char is None:
                problems[name] = "no CGM Measurement characteristic"
                continue
            counts[name] = 0

            def on_value(_c, _d, key=name) -> None:
                counts[key] += 1

            await client.start_notify(char, on_value)
        deadline = time.monotonic() + NOTIFY_WAIT_S
        while time.monotonic() < deadline and any(
            c < SUBSCRIBE_NOTIFICATIONS for c in counts.values()
        ):
            await asyncio.sleep(1.0)
        for name, count in counts.items():
            if count < SUBSCRIBE_NOTIFICATIONS:
                problems[name] = f"{count} notification(s) in {NOTIFY_WAIT_S:.0f} s"
    finally:
        for client in clients:
            with contextlib.suppress(Exception):
                await client.disconnect()
    return problems


async def fingerprint(device) -> dict[int, dict[str, str]]:
    """Each slot's stored configuration as hex, read through one connection."""
    client = await connect(device)
    if client is None:
        raise ConnectionError("no connection to read the slots' configuration")
    try:
        prints: dict[int, dict[str, str]] = {}
        for slot in SLOTS:
            await select_slot(client, slot)
            prints[slot] = {}
            for key, uuid in FINGERPRINT.items():
                data = await retry_ble(lambda uuid=uuid: client.read_gatt_char(uuid))
                prints[slot][key] = bytes(data).hex()
        return prints
    finally:
        with contextlib.suppress(Exception):
            await client.disconnect()


async def read_mode(device) -> int | None:
    client = await connect(device)
    if client is None:
        return None
    try:
        return await read_comm_profile(client)
    finally:
        with contextlib.suppress(Exception):
            await client.disconnect()


async def g6_read(device, deadline: float):
    """Authenticate and read a reading, retrying until *deadline* (monotonic); None on failure."""
    last: Exception | None = None
    while time.monotonic() < deadline:
        gatt = None
        try:
            gatt, client = await g6_session(device)
            if gatt is None or client is None:
                continue
            await client.authenticate()
            return await client.read_glucose()
        except Exception as exc:  # BLE raises many unrelated types
            last = exc
            await asyncio.sleep(2.0)
        finally:
            if gatt is not None:
                with contextlib.suppress(Exception):
                    await gatt.disconnect()
    if last is not None:
        say(f"   G6 read failed: {type(last).__name__}: {last}")
    return None


# ---- one cycle -----------------------------------------------------------------------------------


class Run:
    """The state of the run: the board's believed mode, the console, the results."""

    def __init__(self, serial: SerialCapture) -> None:
        self.serial = serial
        self.results = oma.Results()
        self.mode = oma.MULTI
        self.expected_switches: list[str] = []
        self.baseline: dict[int, dict[str, str]] | None = None
        self.cycle = 0
        self.kind = ""
        self.console_restarts = 0
        self.mark = 0  # console offset at the start of the current Dexcom phase

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        if not ok:
            say(f"   FAIL {name} {detail}")
        return self.results.add(self.cycle, self.kind, name, ok, detail)

    async def set_mode(self, device, dexcom: bool) -> None:
        """Write the mode, trying again if the link drops under the write (the board ignores a
        write of the mode it is already in, so a repeat is harmless)."""
        for attempt in range(MODE_WRITE_TRIES):
            try:
                await switch_mode(device, dexcom=dexcom)
                break
            except Exception as exc:  # BLE raises many unrelated types
                say(f"   mode write failed ({type(exc).__name__}: {exc}); attempt {attempt + 1}")
                if attempt + 1 == MODE_WRITE_TRIES:
                    raise
                await asyncio.sleep(3.0)
        target = oma.DEXCOM if dexcom else oma.MULTI
        if target != self.mode:
            self.expected_switches.append(target)
            self.mode = target


async def into_dexcom(run: Run, found: dict[str, object]) -> dict[str, object] | None:
    """Multi -> Dexcom; returns the adverts seen, or None if they never showed."""
    mark = len(run.serial.text())
    await run.set_mode(config_device(found), dexcom=True)
    seconds, seen, detail = await wait_for_mode(oma.DEXCOM)
    if not run.check("Dexcom adverts appear after the switch", seconds is not None, detail):
        return None
    run.results.to_dexcom_s.append(seconds or 0.0)
    run.mark = mark
    return seen


async def dexcom_checks(run: Run, seen: dict[str, object]) -> None:
    """The G6 conversation and the app identity, in the first window."""
    deadline = time.monotonic() + 60.0
    reading = await g6_read(seen[G6_NAME], deadline)
    logged = board_readings(run.serial.text()[run.mark :])
    ok = reading is not None and any(
        (s, t, g) == (reading.seq, reading.ts_s, reading.glucose) for s, t, g in logged
    )
    run.check(
        "a G6 client reads the reading the board logged",
        ok,
        f"G6={reading} board={logged[-2:]}",
    )
    app = seen.get(APP_NAME)
    mode = await read_mode(app) if app is not None else None
    run.check("the app identity reads the board mode as Dexcom", mode == 1, f"mode={mode}")


async def long_checks(run: Run, seen: dict[str, object], t_switch: float) -> None:
    """The window closes, and the next reading arrives five minutes later and can be read."""
    await asyncio.sleep(max(0.0, WINDOW_PROBE_S - (time.monotonic() - t_switch)))
    closed = await scan(8.0)
    run.check("the G6 transmitter is not connectable outside its window", G6_NAME not in closed)
    deadline = time.monotonic() + 330
    while time.monotonic() < deadline and len(board_readings(run.serial.text()[run.mark :])) < 2:
        await asyncio.sleep(5.0)
    logged = board_readings(run.serial.text()[run.mark :])
    run.check("a second reading is published five minutes later", len(logged) >= 2, str(logged))
    if len(logged) < 2:
        return
    run.check(
        "seq +1 and ts +300 s between the readings",
        logged[1][0] == logged[0][0] + 1 and logged[1][1] - logged[0][1] == 300,
        str(logged[:2]),
    )
    opened = await scan(8.0)
    run.check("the G6 transmitter is connectable again after the reading", G6_NAME in opened)
    if G6_NAME in opened:
        again = await g6_read(opened[G6_NAME], time.monotonic() + 40.0)
        run.check(
            "the second G6 read is the new reading",
            again is not None and (again.seq, again.glucose) == (logged[1][0], logged[1][2]),
            str(again),
        )


async def into_multi(run: Run, seen: dict[str, object]) -> dict[str, object] | None:
    """Dexcom -> Multi through the app's identity; returns the adverts seen, or None."""
    app = seen.get(APP_NAME)
    if app is None:
        run.check("the app identity is there to switch back through", False)
        return None
    await run.set_mode(app, dexcom=False)
    seconds, found, detail = await wait_for_mode(oma.MULTI)
    if not run.check("three sensors advertise after switching back", seconds is not None, detail):
        return None
    run.results.to_multi_s.append(seconds or 0.0)
    return found


async def multi_checks(run: Run, found: dict[str, object]) -> None:
    """All three sensors stream, and nothing the slots stored has changed."""
    problems = await multi_streams(found)
    run.check("all three sensors connect and stream", not problems, str(problems))
    after = await fingerprint(config_device(found))
    assert run.baseline is not None
    diff = oma.fingerprint_diff(run.baseline, after)
    run.check("the three slots' stored configuration is unchanged", not diff, "; ".join(diff[:4]))


async def console_watchdog(run: Run) -> None:
    """Reopen the console reader whenever it goes quiet, so a stalled reader is not mistaken for a
    board that stopped logging."""
    while True:
        await asyncio.sleep(5.0)
        if run.serial.idle_s() > CONSOLE_IDLE_S:
            run.console_restarts += 1
            say(f"   console silent for {run.serial.idle_s():.0f} s; reopening the port")
            await asyncio.to_thread(run.serial.restart)


async def cycle(run: Run, found: dict[str, object]) -> dict[str, object] | None:
    """One Multi -> Dexcom -> Multi round trip; returns the Multi adverts for the next cycle."""
    seen = await into_dexcom(run, found)
    if seen is None:
        return None
    t_switch = time.monotonic()
    if run.kind != "quick":
        await dexcom_checks(run, seen)
    if run.kind == "dwell":
        await asyncio.sleep(DWELL_S)
    elif run.kind == "long":
        await long_checks(run, seen, t_switch)
    back = await into_multi(run, seen)
    if back is None:
        return None
    await multi_checks(run, back)
    return back


async def recover(run: Run) -> dict[str, object] | None:
    """After a bad cycle: bring the board back to Multi mode and return its adverts, if possible."""
    found = await scan(8.0)
    mode, _ = oma.classify_adverts(found, G6_NAME)
    logged = len(oma.parse_mode_lines(run.serial.text()))
    if mode == oma.MULTI and logged >= len(run.expected_switches):
        run.mode = oma.MULTI
        return found
    if mode == oma.MULTI and run.mode == oma.DEXCOM:
        run.expected_switches.pop()  # the board never took the switch to Dexcom
        run.mode = oma.MULTI
        return found
    device = found.get(APP_NAME) or next(
        (d for n, d in sorted(found.items()) if n.startswith(oma.SENSOR_PREFIX)), None
    )
    if device is not None:
        with contextlib.suppress(Exception):
            await run.set_mode(device, dexcom=False)
    _, found, _ = await wait_for_mode(oma.MULTI)
    return found if oma.classify_adverts(found, G6_NAME)[0] == oma.MULTI else None


# ---- the report ----------------------------------------------------------------------------------


def write_report(run: Run, out: Path, started: float, final: bool = False) -> list[str]:
    results = run.results
    console = run.serial.text()
    switches = oma.parse_mode_lines(console)
    _, boots, anomalies = oda_dex.parse_console(console)
    fails = oma.verdict(results)
    fails += oma.score_switches(run.expected_switches, switches)
    if boots:
        fails.append(f"the board rebooted {boots} time(s)")
    if anomalies:
        fails.append(f"{len(anomalies)} console anomaly line(s)")
    hours = (time.time() - started) / 3600
    per_kind = {k: sum(1 for c in results.checks if c.kind == k) for k in sorted(set(KINDS))}
    lines = [
        f"# Overnight mode-switch test — {'FINAL' if final else 'running'} "
        f"({'FAIL' if fails else 'PASS so far'})",
        "",
        f"- running {hours:.2f} h; {results.cycles} cycle(s) done "
        f"({len(results.failed_cycles())} with a failed check); {len(results.checks)} checks",
        f"- to Dexcom (write -> adverts): {oma.timing_summary(results.to_dexcom_s)}",
        f"- to Multi (write -> adverts): {oma.timing_summary(results.to_multi_s)}",
        f"- console reader restarted {run.console_restarts} time(s)",
        f"- switches asked for {len(run.expected_switches)}, logged by the board {len(switches)}; "
        f"reboots {boots}; console anomalies {len(anomalies)}",
        f"- checks per cycle kind: {per_kind}",
        "",
        "## Failures",
        *([f"- {f}" for f in fails] or ["- none"]),
        "",
        "## Console anomalies",
        *([f"- `{a}`" for a in anomalies[:30]] or ["- none"]),
    ]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return fails


# ---- the run -------------------------------------------------------------------------------------


async def main(args: argparse.Namespace) -> int:
    keep_awake()
    run_dir = ARTIFACTS.parent / f"overnight-modeswitch-{datetime.now():%Y%m%d-%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    deadline = deadline_from(args.until) if args.until else None
    end = time.time() + (args.minutes * 60 if args.minutes else minutes_left(deadline) * 60)
    started = time.time()
    say(f"output: {run_dir}; running until {datetime.fromtimestamp(end):%H:%M}")
    with SerialCapture(run_dir / "serial.log") as serial:
        await asyncio.sleep(3.0)
        run = Run(serial)
        watchdog = asyncio.create_task(console_watchdog(run))
        try:
            _, found, detail = await wait_for_mode(oma.MULTI)
            if oma.classify_adverts(found, G6_NAME)[0] != oma.MULTI:
                run.results.aborted = f"the board does not start in Multi mode: {detail}"
                return 1
            run.baseline = await fingerprint(config_device(found))
            bad_in_a_row = 0
            while time.time() < end and not run.results.aborted:
                run.cycle += 1
                run.kind = KINDS[(run.cycle - 1) % len(KINDS)]
                before = len(run.results.failures)
                say(f"cycle {run.cycle} ({run.kind})")
                try:
                    next_found = await cycle(run, found)
                except Exception as exc:  # BLE raises many unrelated types
                    run.check("the cycle ran to the end", False, f"{type(exc).__name__}: {exc}")
                    next_found = None
                run.results.cycles += 1
                if next_found is None:
                    next_found = await recover(run)
                found = next_found or found
                bad_in_a_row = bad_in_a_row + 1 if len(run.results.failures) > before else 0
                if next_found is None or bad_in_a_row >= MAX_CONSECUTIVE_BAD:
                    run.results.aborted = (
                        f"{bad_in_a_row} bad cycle(s) in a row"
                        if next_found is not None
                        else "the board could not be brought back to Multi mode"
                    )
                fails = write_report(run, run_dir, started)
                say(f"cycle {run.cycle} done; {len(fails)} failure line(s) so far")
        finally:
            watchdog.cancel()
            if run.mode != oma.MULTI:
                say("switching the board back to Multi mode")
                await recover(run)
            fails = write_report(run, run_dir, started, final=True)
    say(f"done: {'FAIL' if fails else 'PASS'}; report {run_dir / 'REPORT.md'}")
    return 1 if fails else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--until", help="stop at this local time (HH:MM, the next occurrence)")
    group.add_argument("--minutes", type=float, help="run for this many minutes")
    sys.exit(asyncio.run(main(parser.parse_args())))
