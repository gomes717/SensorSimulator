"""Overnight test of the board in Dexcom G6 mode, with the PC app connected at the same time.

    uv run python scripts/overnight_dexcom.py --until 08:00
    uv run python scripts/overnight_dexcom.py --minutes 30       # a short trial
    uv run python scripts/overnight_dexcom.py --minutes 120 --no-xdrip --app-read-s 60
                                                                  # closed-object A/B (no G6 client)

Run it with the board flashed with the board-mode firmware, COM10 free and the app closed. It
switches the board to Dexcom mode over BLE and keeps it there at real time (one reading every five
minutes), with two clients, the way a phone and the PC would use it:

* an **xDrip-like client**: when the board publishes a reading it scans for the ``Dexcom00``
  advertisement, connects, authenticates with the transmitter ID and reads the glucose; a minute
  later it scans again and checks the transmitter is NOT connectable outside the window;
* an **app-like link** on ``Nordic Glucose Sensor 1``, subscribed to the CGMS stream, reconnecting
  if it drops. Windows sometimes closes the GATT objects of a live link ("the object was closed");
  such a drop is recorded with where it happened and how long the link took to come back, and is
  tolerated if that is quick and rare (``overnight_dexcom_analysis.LinkDrop``).

The board's console (COM10) is the reference: every reading it logs must be read over G6 once and
correctly, soon after publication; must reach the app with the same value; seq must advance by one
and ts by 300 s; and the board must not reset, fault or see an unknown opcode. REPORT.md is
rewritten every minute, so a run cut short still leaves a verdict; the board is switched back to
Multi mode at the end. Pure scoring: ``overnight_dexcom_analysis.py`` (unit-tested).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import overnight_dexcom_analysis as oda
from ble_overnight import deadline_from, keep_awake, minutes_left
from bleak import BleakClient, BleakScanner
from hw_common import ARTIFACTS, SerialCapture, config_uuid, connect, retry_ble, write
from hw_dexcom_mode import APP_NAME, G6_NAME, TX_ID, scan, switch_mode

from services import ble_session
from services.dexcom_g6_client import G6Client

COMM_PROFILE = config_uuid(0x14)
READ_ATTEMPT_S = 90.0  # how long the xDrip-like client keeps trying after a publication
PROBE_AFTER_S = 75.0  # the window (30 s) is long shut by then
MIN_COVERAGE = 0.98


@dataclass(frozen=True)
class Options:
    """What the command line chose."""

    xdrip: bool = True  # False: only the app-like link (A/B runs)
    app_read_s: float = 600.0  # how often the app-like link reads the board mode over its link
    trace_log: Path | None = None  # where the app-like link's tracebacks go


@dataclass
class State:
    """Everything the tasks record."""

    started: float = field(default_factory=time.time)
    pubs: list[oda.Published] = field(default_factory=list)
    reads: list[oda.G6Read] = field(default_factory=list)
    app_values: list[oda.AppValue] = field(default_factory=list)
    probes: list[tuple[float, bool]] = field(default_factory=list)  # (wall, G6 visible?)
    read_failures: list[str] = field(default_factory=list)
    drops: list[oda.LinkDrop] = field(default_factory=list)
    app_connected: bool = False
    app_device: object | None = None  # the app identity's device, so a reconnect needs no scan
    app_step: str = ""  # what the app-like link is doing, for the drop record
    last_xdrip_connect: float | None = None
    options: Options = field(default_factory=Options)
    boots: int = 0
    anomalies: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def stamp_of(wall: float) -> str:
    return datetime.fromtimestamp(wall).strftime("%H:%M:%S")


def say(text: str) -> None:
    print(f"[{stamp()}] {text}", flush=True)


# ---- the console: the reference ------------------------------------------------------------------


async def watch_console(state: State, log: Path) -> None:
    """Follow the board's console: publications (with the time we first saw them), boots, faults."""
    offset = 0
    while True:
        await asyncio.sleep(1.0)
        with log.open("rb") as fh:
            fh.seek(offset)
            chunk = fh.read()
            offset += len(chunk)
        text = chunk.decode("utf-8", errors="replace")
        if not text:
            continue
        now = time.time()
        readings, boots, anomalies = oda.parse_console(text)
        state.pubs.extend(oda.Published(s, t, g, now) for s, t, g in readings)
        state.boots += boots
        state.anomalies.extend(anomalies)


# ---- the xDrip-like client -----------------------------------------------------------------------


async def read_once(state: State, pub: oda.Published) -> bool:
    """One scan + connect + authenticate + read; True if a reading was obtained."""
    found = await scan(3.0)
    device = found.get(G6_NAME)
    if device is None:
        return False
    gatt = BleakClient(device, timeout=15.0, winrt={"use_cached_services": False})
    try:
        state.last_xdrip_connect = time.time()
        await gatt.connect()
        client = G6Client(gatt, TX_ID, timeout_s=10.0)
        await client.open()
        await client.authenticate()
        reading = await client.read_glucose()
        state.reads.append(
            oda.G6Read(time.time(), reading.seq, reading.ts_s, reading.glucose, attempts=1)
        )
        return True
    finally:
        with contextlib.suppress(Exception):
            await gatt.disconnect()


async def fetch_reading(state: State, pub: oda.Published) -> None:
    """Keep trying for READ_ATTEMPT_S after *pub* was published, as xDrip does."""
    deadline = pub.wall + READ_ATTEMPT_S
    attempts = 0
    last_error = ""
    while time.time() < deadline:
        attempts += 1
        try:
            if await read_once(state, pub):
                return
        except Exception as exc:  # BLE raises many unrelated types
            last_error = f"{type(exc).__name__}: {exc}"
        await asyncio.sleep(2.0)
    state.read_failures.append(
        f"seq {pub.seq}: no G6 read in {READ_ATTEMPT_S:.0f} s ({attempts} attempts) {last_error}"
    )


async def probe_closed(state: State, pub: oda.Published) -> None:
    """After the window: the transmitter must not advertise."""
    await asyncio.sleep(max(0.0, pub.wall + PROBE_AFTER_S - time.time()))
    try:
        found = await BleakScanner.discover(timeout=6.0, return_adv=True)
    except Exception as exc:
        state.notes.append(f"probe scan failed: {exc!r}")
        return
    names = {(adv.local_name or dev.name or "") for dev, adv in found.values()}
    state.probes.append((time.time(), G6_NAME in names))


async def xdrip_client(state: State) -> None:
    """Read every publication over G6, and probe that the window closes."""
    handled = 0
    while True:
        await asyncio.sleep(1.0)
        if handled >= len(state.pubs):
            continue
        pub = state.pubs[handled]
        handled += 1
        probe = asyncio.create_task(probe_closed(state, pub))
        await fetch_reading(state, pub)
        await probe
        say(
            f"xdrip: seq {pub.seq} glucose {pub.glucose} -> read {len(state.reads)} "
            f"fail {len(state.read_failures)}"
        )


# ---- the app-like link ---------------------------------------------------------------------------


async def app_session(state: State) -> None:
    """One connection to "Nordic Glucose Sensor 1" until it drops."""
    device = state.app_device
    if device is None:
        state.app_step = "scan"
        device = (await scan(6.0)).get(APP_NAME)
        if device is None:
            raise ConnectionError(f"{APP_NAME} is not advertising")
        state.app_device = device
    gone = asyncio.Event()
    client = BleakClient(
        device, timeout=15.0, disconnected_callback=lambda _c: gone.set(),
        winrt={"use_cached_services": False},
    )  # fmt: skip
    state.app_step = "connect"
    await client.connect()
    try:
        measurement = sorted(
            (c for s in client.services for c in s.characteristics
             if c.uuid.lower() == ble_session.CGM_MEASUREMENT_UUID),
            key=lambda c: c.handle,
        )  # fmt: skip

        def on_value(_c, data) -> None:
            glucose = ble_session._decode_cgm_measurement(data)["glucose_value"]
            state.app_values.append(oda.AppValue(time.time(), float(glucose)))

        state.app_step = "subscribe"
        await client.start_notify(measurement[0], on_value)
        state.app_connected = True
        state.app_step = "idle"
        now = time.time()
        for drop in state.drops:
            if drop.recovered_wall is None:
                drop.recovered_wall = now
        while not gone.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(gone.wait(), state.options.app_read_s)
            if gone.is_set():
                break
            state.app_step = "mode read"
            mode = (await client.read_gatt_char(COMM_PROFILE))[0]
            state.app_step = "idle"
            if mode != 1:
                state.notes.append(f"{stamp()}: the app link read board mode {mode}, not Dexcom")
    finally:
        state.app_connected = False
        with contextlib.suppress(Exception):
            await client.disconnect()


async def app_link(state: State) -> None:
    """Keep an app-like link up, recording every drop.

    A closed-object error is reconnected at once and without a scan (the board is known and is
    advertising again by then); anything else waits a few seconds first."""
    while True:
        error = ""
        try:
            await app_session(state)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            if state.options.trace_log is not None:
                with state.options.trace_log.open("a", encoding="utf-8") as fh:
                    fh.write(f"[{stamp()}] app link, step {state.app_step!r}\n")
                    fh.write(traceback.format_exc() + "\n")
        if state.app_step in ("idle", "mode read"):  # it had been up
            last = state.last_xdrip_connect
            drop = oda.LinkDrop(
                time.time(), error, state.app_step, None if last is None else time.time() - last
            )
            state.drops.append(drop)
            say(f"app link down ({len(state.drops)} drops): {drop.describe()}")
            if drop.closed_object:
                continue  # back at once, with the device already known
        else:
            state.app_device = None  # it never came up: scan again
            state.notes.append(f"{stamp()}: app link could not connect: {error or 'link lost'}")
            say(f"app link could not connect: {error or 'link lost'}")
        await asyncio.sleep(5.0)


# ---- the report ----------------------------------------------------------------------------------


def verdict(state: State) -> tuple[list[str], list[str]]:
    """``(fail lines, summary lines)`` for the run so far."""
    pubs = state.pubs
    judged_pubs = pubs[:-1] or pubs  # the newest may still be waiting for its read
    newest = {(p.seq, p.ts_s) for p in pubs[len(judged_pubs) :]}
    reads = [r for r in state.reads if (r.seq, r.ts_s) not in newest]
    read_score = oda.score_reads(judged_pubs, reads)
    excused = [p for p in judged_pubs if any(d.excuses(p.wall) for d in state.drops)]
    app_pubs = [p for p in judged_pubs if p not in excused]
    matched, app_problems = oda.score_app(app_pubs, state.app_values)
    hours = (time.time() - state.started) / 3600
    drop_problems = oda.score_drops(state.drops, hours)
    pub_problems = oda.score_publications(pubs)
    visible = [wall for wall, seen in state.probes if seen]
    fails: list[str] = []
    if state.options.xdrip and read_score.coverage < MIN_COVERAGE:
        fails.append(f"G6 read coverage {read_score.coverage:.1%} < {MIN_COVERAGE:.0%}")
    judged = max(1, len(app_pubs))
    if matched / judged < MIN_COVERAGE:
        fails.append(f"app coverage {matched / judged:.1%} < {MIN_COVERAGE:.0%}")
    if pub_problems:
        fails.append(f"{len(pub_problems)} problem(s) in the published sequence")
    if visible:
        fails.append(f"the transmitter was connectable outside its window {len(visible)} time(s)")
    if state.boots:
        fails.append(f"the board rebooted {state.boots} time(s)")
    if state.anomalies:
        fails.append(f"{len(state.anomalies)} console anomaly line(s)")
    fails.extend(drop_problems)
    closed = [d for d in state.drops if d.closed_object]
    summary = [
        f"- running {hours:.2f} h; published {len(pubs)}; G6 reads {len(state.reads)}; "
        f"app values {len(state.app_values)}",
        f"- G6 coverage {read_score.coverage:.1%}; "
        f"latency {oda.latency_summary(read_score.latencies_s)}",
        f"- app coverage {matched}/{judged}"
        + (f" ({len(excused)} excused by quick closed-object recoveries)" if excused else "")
        + f"; app link drops {len(state.drops)} ({len(closed)} closed-object)",
        f"- window probes {len(state.probes)}, connectable outside the window {len(visible)}",
        f"- read failures {len(state.read_failures)}; board reboots {state.boots}; "
        f"console anomalies {len(state.anomalies)}",
    ]
    g6_problems = [*read_score.problems, *state.read_failures] if state.options.xdrip else []
    details = [*pub_problems, *g6_problems, *app_problems, *drop_problems]
    return fails, summary + [f"  - {d}" for d in details[:40]]


def write_report(state: State, out: Path, final: bool = False) -> list[str]:
    """Rewrite REPORT.md; returns the failure lines."""
    fails, summary = verdict(state)
    lines = [
        f"# Overnight Dexcom-mode test — {'FINAL' if final else 'running'} "
        f"({'FAIL' if fails else 'PASS so far'})",
        "",
        *summary,
        "",
        "## Failures",
        *([f"- {f}" for f in fails] or ["- none"]),
        "",
        "## Notes",
        *([f"- {n}" for n in state.notes[-20:]] or ["- none"]),
        "",
        "## Console anomalies",
        *([f"- `{a}`" for a in state.anomalies[:30]] or ["- none"]),
        "",
        "## App link drops",
        *([f"- {stamp_of(d.wall)}: {d.describe()}" for d in state.drops[:30]] or ["- none"]),
    ]
    (out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return fails


async def report_loop(state: State, out: Path) -> None:
    while True:
        await asyncio.sleep(60.0)
        fails = write_report(state, out)
        say(
            f"pubs {len(state.pubs)} reads {len(state.reads)} app {len(state.app_values)} "
            f"drops {len(state.drops)} fails {len(fails)}"
        )


# ---- the run -------------------------------------------------------------------------------------


async def to_multi(app_name: str = APP_NAME) -> bool:
    """Switch the board back to Multi mode through its app identity."""
    found = await scan(8.0)
    device = found.get(app_name)
    if device is None:
        return False
    client = await connect(device)
    if client is None:
        return False
    try:
        await retry_ble(lambda: write(client, COMM_PROFILE, bytes([0])))
    finally:
        await asyncio.sleep(1.0)
        with contextlib.suppress(Exception):
            await client.disconnect()
    return True


async def main(args: argparse.Namespace) -> int:
    keep_awake()
    run_dir = ARTIFACTS.parent / f"overnight-dexcom-{datetime.now():%Y%m%d-%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    deadline = deadline_from(args.until) if args.until else None
    end = time.time() + (args.minutes * 60 if args.minutes else minutes_left(deadline) * 60)
    say(f"output: {run_dir}; running until {datetime.fromtimestamp(end):%H:%M}")

    state = State(
        options=Options(
            xdrip=not args.no_xdrip,
            app_read_s=args.app_read_s,
            trace_log=run_dir / "app_link.log",
        )
    )
    with SerialCapture(run_dir / "serial.log") as serial:
        await asyncio.sleep(3.0)
        task_list: list[asyncio.Task] = []
        try:
            if not args.no_switch:
                named = await scan(8.0)
                config = next((d for n, d in sorted(named.items()) if n.endswith("2")), None)
                if config is None and G6_NAME not in named:
                    config = next((d for n, d in named.items() if n.startswith("Nordic")), None)
                if config is not None:
                    await switch_mode(config, dexcom=True)
            task_list = [
                asyncio.create_task(watch_console(state, serial.log)),
                asyncio.create_task(app_link(state)),
                asyncio.create_task(report_loop(state, run_dir)),
            ]
            if state.options.xdrip:
                task_list.append(asyncio.create_task(xdrip_client(state)))
            while time.time() < end:
                await asyncio.sleep(5.0)
        finally:
            for task in task_list:
                task.cancel()
            await asyncio.gather(*task_list, return_exceptions=True)
            fails = write_report(state, run_dir, final=True)
            if not args.keep_dexcom:
                say("switching the board back to Multi mode")
                back = await to_multi()
                state.notes.append("switched back to Multi" if back else "could not switch back")
                fails = write_report(state, run_dir, final=True)
    say(f"done: {'FAIL' if fails else 'PASS'}; report {run_dir / 'REPORT.md'}")
    return 1 if fails else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--until", help="stop at this local time (HH:MM, the next occurrence)")
    group.add_argument("--minutes", type=float, help="run for this many minutes")
    parser.add_argument(
        "--no-switch", action="store_true", help="the board is already in Dexcom mode"
    )
    parser.add_argument(
        "--no-xdrip", action="store_true", help="no G6 client: only the app-like link (A/B runs)"
    )
    parser.add_argument(
        "--app-read-s", type=float, default=600.0,
        help="how often the app-like link reads the board mode over its live link (default 600)",
    )  # fmt: skip
    parser.add_argument("--keep-dexcom", action="store_true", help="leave the board in Dexcom mode")
    sys.exit(asyncio.run(main(parser.parse_args())))
