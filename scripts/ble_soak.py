"""Overnight BLE soak: how reliably does the board keep streaming?

Connects to every advertised sensor identity, subscribes to its CGM Measurement
characteristic, and then just watches — recording every notification, every
dropout and every reconnect until the run time is up. The point is to measure
flakiness that is invisible in a short session: a link that dies after two
hours, a sensor that stops notifying while staying connected, a reconnect that
never succeeds.

Deliberately does NOT use the app's BleSession: this is meant to characterise
the *link*, so a bug in the Qt/session layer must not be able to masquerade as
BLE flakiness. It talks to bleak directly and mirrors only the app's subscribe
policy — one identity's own CGMS instance, nothing else (see ble_session.py's
_FUNCTIONAL_NOTIFY_UUIDS comment on why subscribing broadly saturates the
peripheral's TX buffers).

Run it INSTEAD of the app: the board accepts CONFIG_BT_MAX_CONN (4) links, and
the app holds all of them.

    uv run python scripts/ble_soak.py --hours 8
    uv run python scripts/ble_soak.py --minutes 2 --out .scratch/soak

Writes <out>/soak-<stamp>.jsonl (one event per line) and <out>/soak-<stamp>.txt
(the summary, also printed). Ctrl-C stops early and still writes the summary.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from bleak import BleakClient, BleakScanner

# Standard Bluetooth SIG CGM Service characteristics — fixed by the SIG, not by
# this project (the app spells them out the same way in ble_session.py).
CGM_SERVICE_UUID = "0000181f-0000-1000-8000-00805f9b34fb"
CGM_MEASUREMENT_UUID = "00002aa7-0000-1000-8000-00805f9b34fb"

DEFAULT_NAME_PREFIX = "Nordic Glucose Sensor"
RECONNECT_BACKOFF_S = [2, 5, 10, 20, 30, 60]


def _now() -> float:
    return time.monotonic()


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class SensorStats:
    """Everything one identity did during the run."""

    name: str
    address: str
    notifications: int = 0
    connects: int = 0
    connect_failures: int = 0
    subscribe_failures: int = 0
    silent_subscribes: int = 0
    disconnects: int = 0
    connected_seconds: float = 0.0
    intervals: list[float] = field(default_factory=list)
    gaps: list[tuple[str, float]] = field(default_factory=list)
    last_notify: float | None = None

    def note_notification(self, gap_threshold: float, log) -> None:
        now = _now()
        if self.last_notify is not None:
            delta = now - self.last_notify
            self.intervals.append(delta)
            if delta > gap_threshold:
                self.gaps.append((_stamp(), delta))
                log("gap", self.name, seconds=round(delta, 1))
        self.last_notify = now
        self.notifications += 1


class EventLog:
    """Append-only JSONL of everything that happened, flushed as it goes."""

    def __init__(self, path: Path) -> None:
        self._fh = path.open("w", encoding="utf-8")

    def __call__(self, kind: str, sensor: str, **fields) -> None:
        record = {"t": _stamp(), "kind": kind, "sensor": sensor, **fields}
        self._fh.write(json.dumps(record) + "\n")
        self._fh.flush()
        if kind != "notify":
            detail = " ".join(f"{k}={v}" for k, v in fields.items())
            print(f"[{record['t']}] {kind:16} {sensor:26} {detail}", flush=True)

    def close(self) -> None:
        self._fh.close()


def _own_instance_measurement(client: BleakClient, slot: int):
    """The CGM Measurement characteristic of *slot*'s own CGMS instance.

    A multi-sensor board exposes one CGMS service instance per slot, all sharing
    the same UUID, so they are told apart only by the order they enumerate in —
    the same assumption the app makes.
    """
    instance = -1
    for service in client.services:
        if service.uuid.lower() != CGM_SERVICE_UUID:
            continue
        instance += 1
        if instance != slot:
            continue
        for char in service.characteristics:
            if char.uuid.lower() == CGM_MEASUREMENT_UUID:
                return char
    return None


async def _subscribe(client: BleakClient, stats: SensorStats, slot: int, args, log) -> bool:
    """Subscribe to this identity's own measurement characteristic."""
    char = _own_instance_measurement(client, slot)
    if char is None:
        stats.subscribe_failures += 1
        log("no_measurement_char", stats.name, slot=slot)
        return False

    def on_notify(_char, _data: bytearray) -> None:
        stats.note_notification(args.gap_seconds, log)

    try:
        await client.start_notify(char, on_notify)
    except (Exception, asyncio.CancelledError) as exc:
        stats.subscribe_failures += 1
        log("subscribe_failed", stats.name, error=str(exc) or type(exc).__name__)
        return False
    log("subscribed", stats.name, slot=slot)
    return True


async def _await_first(stats: SensorStats, client: BleakClient, args, log) -> bool:
    """Wait for the first notification after a successful subscribe.

    start_notify can report success and then never deliver anything: WinRT is
    unreliable across several same-UUID CGMS instances (the app carries the same
    note in scripts/e2e_4sensor.py and defends with retries). Silence here is a
    real failure mode, distinct from a refused subscribe, so it is counted
    separately and forces a reconnect rather than idling all night on a link
    that will never produce data.
    """
    before = stats.notifications
    limit = _now() + args.first_notify_timeout
    while _now() < limit and client.is_connected:
        if stats.notifications > before:
            return True
        await asyncio.sleep(0.5)
    if client.is_connected:
        stats.silent_subscribes += 1
        log("silent_after_subscribe", stats.name, waited_s=args.first_notify_timeout)
    return False


async def _hold_until(client: BleakClient, deadline: float) -> None:
    """Idle while the link stays up, so notifications can arrive."""
    while _now() < deadline and client.is_connected:
        await asyncio.sleep(1.0)


async def _one_connection(stats: SensorStats, slot: int, deadline: float, args, log) -> None:
    """One connect → subscribe → watch cycle. Returns when the link ends."""
    connected_at = _now()
    async with BleakClient(stats.address, timeout=args.connect_timeout) as client:
        stats.connects += 1
        log("connected", stats.name, address=stats.address)
        try:
            if await _subscribe(client, stats, slot, args, log) and await _await_first(
                client=client, stats=stats, args=args, log=log
            ):
                await _hold_until(client, deadline)
        finally:
            stats.connected_seconds += _now() - connected_at
            stats.disconnects += 1
            log("disconnected", stats.name, held_s=round(_now() - connected_at, 1))


async def soak_one(stats: SensorStats, slot: int, deadline: float, args, log) -> None:
    """Keep *stats*' identity connected until the deadline, reconnecting as needed."""
    attempt = 0
    while _now() < deadline:
        try:
            before = stats.notifications
            await _one_connection(stats, slot, deadline, args, log)
            # Only a connection that actually delivered data counts as healthy.
            # A silent subscribe returns normally too, so resetting here would
            # retry every couple of seconds for the whole run: that hammers the
            # radio and starves the identities that ARE streaming, which means
            # the harness would degrade the very thing it is measuring.
            if stats.notifications > before:
                attempt = 0
        except asyncio.CancelledError:
            # Raised by the WinRT backend when the link drops mid-discovery, and
            # by Ctrl-C. Treat the former as a failed attempt; if the run is over
            # the loop condition below ends it anyway.
            stats.connect_failures += 1
            log("cancelled", stats.name)
        except Exception as exc:
            stats.connect_failures += 1
            log("connect_failed", stats.name, error=str(exc) or type(exc).__name__)
        if _now() >= deadline:
            return
        backoff = RECONNECT_BACKOFF_S[min(attempt, len(RECONNECT_BACKOFF_S) - 1)]
        attempt += 1
        log("retry_in", stats.name, seconds=backoff)
        await asyncio.sleep(backoff)


async def discover(args, log) -> list[SensorStats]:
    """Scan for the board's advertised identities."""
    log("scanning", "-", seconds=args.scan_seconds)
    found = await BleakScanner.discover(timeout=args.scan_seconds)
    sensors = [
        SensorStats(name=d.name, address=d.address)
        for d in found
        if d.name and d.name.startswith(args.name_prefix)
    ]
    for s in sorted(sensors, key=lambda x: x.name):
        log("found", s.name, address=s.address)
    return sorted(sensors, key=lambda x: x.name)


def _slot_of(name: str) -> int:
    """ "... Sensor 3" -> slot 2; an unnumbered name is the only slot."""
    tail = name.rsplit(" ", 1)[-1]
    return int(tail) - 1 if tail.isdigit() else 0


def summarise(sensors: list[SensorStats], elapsed: float) -> str:
    """Human-readable verdict: uptime, throughput and every dropout."""
    lines = [
        f"BLE soak summary — {_stamp()}",
        f"run length: {elapsed / 3600:.2f} h ({elapsed:.0f} s)",
        "",
    ]
    for s in sensors:
        uptime = 100.0 * s.connected_seconds / elapsed if elapsed else 0.0
        median = statistics.median(s.intervals) if s.intervals else float("nan")
        worst = max(s.intervals) if s.intervals else float("nan")
        lines += [
            f"{s.name}  ({s.address})",
            f"  uptime           : {uptime:.1f}%  ({s.connected_seconds:.0f}s connected)",
            f"  notifications    : {s.notifications}",
            f"  interval med/max : {median:.1f}s / {worst:.1f}s",
            f"  connects         : {s.connects}   failures: {s.connect_failures}"
            f"   subscribe failures: {s.subscribe_failures}",
            f"  silent subscribes: {s.silent_subscribes}   (subscribe said OK, no data followed)",
            f"  disconnects      : {s.disconnects}",
            f"  gaps over thresh : {len(s.gaps)}",
        ]
        lines += [f"      {when}  {secs:.1f}s" for when, secs in s.gaps[:20]]
        if len(s.gaps) > 20:
            lines.append(f"      ... {len(s.gaps) - 20} more")
        lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--hours", type=float, default=0.0, help="run length in hours")
    ap.add_argument("--minutes", type=float, default=0.0, help="run length in minutes")
    ap.add_argument(
        "--gap-seconds",
        type=float,
        default=30.0,
        help="a silence longer than this counts as a dropout (default: 30)",
    )
    ap.add_argument("--scan-seconds", type=float, default=10.0, help="initial scan time")
    ap.add_argument(
        "--first-notify-timeout",
        type=float,
        default=20.0,
        help="reconnect if no data arrives this soon after subscribing (default: 20)",
    )
    ap.add_argument("--connect-timeout", type=float, default=20.0, help="per-connect timeout")
    ap.add_argument("--name-prefix", default=DEFAULT_NAME_PREFIX, help="advertised name prefix")
    ap.add_argument("--out", default=".scratch/soak", help="output directory")
    args = ap.parse_args()
    args.duration = args.hours * 3600 + args.minutes * 60 or 8 * 3600
    return args


async def run(args, log) -> tuple[list[SensorStats], float]:
    sensors = await discover(args, log)
    if not sensors:
        log("no_sensors_found", "-", prefix=args.name_prefix)
        return [], 0.0
    started = _now()
    deadline = started + args.duration
    tasks = [soak_one(s, _slot_of(s.name), deadline, args, log) for s in sensors]
    await asyncio.gather(*tasks, return_exceptions=True)
    return sensors, _now() - started


def main() -> int:
    args = parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    log = EventLog(out_dir / f"soak-{tag}.jsonl")

    sensors: list[SensorStats] = []
    elapsed = 0.0
    started = _now()
    try:
        sensors, elapsed = asyncio.run(run(args, log))
    except KeyboardInterrupt:
        elapsed = _now() - started
        print("\ninterrupted — writing summary for what ran so far", flush=True)
    finally:
        log.close()

    if not sensors:
        print("no sensors found — is the board powered, and is the app closed?")
        return 1
    report = summarise(sensors, elapsed or (_now() - started))
    (out_dir / f"soak-{tag}.txt").write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"events: {out_dir / f'soak-{tag}.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
