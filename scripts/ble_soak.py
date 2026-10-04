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
class Churn:
    """--churn-s bookkeeping for one identity, plus the live link it drops."""

    drops: int = 0
    recovered: int = 0
    failed: int = 0
    recovery_s: list[float] = field(default_factory=list)
    link: BleakClient | None = None


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
    resubscribes: int = 0
    polls_with_data: int = 0
    disconnects: int = 0
    connected_seconds: float = 0.0
    intervals: list[float] = field(default_factory=list)
    gaps: list[tuple[str, float]] = field(default_factory=list)
    last_notify: float | None = None
    churn: Churn = field(default_factory=Churn)

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


def _notify_handler(stats: SensorStats, args, log):
    def on_notify(_char, _data: bytearray) -> None:
        stats.note_notification(args.gap_seconds, log)
        if args.log_notifications:
            # Wall-clock to the millisecond (the usual "t" field is whole
            # seconds): lets an offline pass see how the sensors' notifications
            # are phased against each other inside one interval.
            log("notify", stats.name, ts=round(time.time(), 3))

    return on_notify


async def _subscribe(client: BleakClient, stats: SensorStats, slot: int, args, log):
    """Subscribe to this identity's own measurement characteristic.

    Returns the characteristic (so a later silent stretch can re-arm the same
    subscription) or None on failure.
    """
    char = _own_instance_measurement(client, slot)
    if char is None:
        stats.subscribe_failures += 1
        log("no_measurement_char", stats.name, slot=slot)
        return None

    try:
        await client.start_notify(char, _notify_handler(stats, args, log))
    except (Exception, asyncio.CancelledError) as exc:
        stats.subscribe_failures += 1
        log("subscribe_failed", stats.name, error=str(exc) or type(exc).__name__)
        return None
    log("subscribed", stats.name, slot=slot)
    return char


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


async def _hold_until(
    client: BleakClient, char, stats: SensorStats, args, log, deadline: float
) -> None:
    """Idle while the link stays up, re-arming the subscription if it goes quiet.

    The dominant failure mode measured on identity 4 (soak-20260930-221218: 36
    of ~1440 expected notifications over 2h, repeated multi-minute silences,
    ZERO disconnects) is not a dropped link — the connection and subscription
    both report healthy throughout, notifications just stop arriving for
    minutes at a time. A disconnect/reconnect (the only recovery this script
    had before) is the wrong tool for that: it throws away a connection that
    isn't actually broken. A stop_notify/start_notify re-arm on the SAME
    connection is the minimal kick that might restart a stuck WinRT
    notification pipe without that cost.
    """
    last_resubscribe_at = 0.0
    last_poll_at = 0.0
    while _now() < deadline and client.is_connected:
        await asyncio.sleep(1.0)
        last = stats.last_notify
        if last is None:
            continue
        silent_s = _now() - last

        # Diagnostic + fallback: an explicit read while "silent" tells us
        # whether the LINK is actually fine (a read succeeds, just notify
        # delivery is stuck — a WinRT notification-pipe bug) or the radio
        # itself is the problem (reads fail too). A successful read also
        # recovers data the test would otherwise have missed entirely.
        poll_due = args.poll_after_s > 0 and silent_s > args.poll_after_s
        if poll_due and _now() - last_poll_at > args.poll_after_s:
            last_poll_at = _now()
            try:
                data = await client.read_gatt_char(char)
            except (Exception, asyncio.CancelledError) as exc:
                log("poll_failed", stats.name, error=str(exc) or type(exc).__name__)
            else:
                stats.polls_with_data += 1
                log(
                    "polled_while_silent",
                    stats.name,
                    silent_s=round(silent_s, 1),
                    n_bytes=len(data),
                )

        # A resubscribe attempt does not itself update stats.last_notify (only
        # a real notification does), so without this cooldown every 1s tick
        # past the threshold would fire another attempt back-to-back,
        # hammering the radio instead of giving one re-arm time to work.
        resub_due = args.resubscribe_after_s > 0 and silent_s > args.resubscribe_after_s
        if resub_due and _now() - last_resubscribe_at > args.resubscribe_after_s:
            last_resubscribe_at = _now()
            log("resubscribing_after_silence", stats.name, silent_s=round(silent_s, 1))
            try:
                await client.stop_notify(char)
                await client.start_notify(char, _notify_handler(stats, args, log))
            except (Exception, asyncio.CancelledError) as exc:
                log("resubscribe_failed", stats.name, error=str(exc) or type(exc).__name__)
            else:
                stats.resubscribes += 1


async def _one_connection(
    stats: SensorStats,
    slot: int,
    deadline: float,
    args,
    log,
    release: asyncio.Event | None = None,
) -> None:
    """One connect → subscribe → watch cycle. Returns when the link ends."""
    connected_at = _now()
    winrt_args = {"use_cached_services": False} if args.no_cache else {}
    async with BleakClient(stats.address, timeout=args.connect_timeout, winrt=winrt_args) as client:
        stats.connects += 1
        stats.churn.link = client
        log("connected", stats.name, address=stats.address)
        try:
            char = await _subscribe(client, stats, slot, args, log)
            if char is not None and await _await_first(
                client=client, stats=stats, args=args, log=log
            ):
                # Release right here — the moment real data is confirmed —
                # not after this connection eventually ends: _hold_until below
                # keeps the link open until the deadline, so gating on "the
                # connection cycle finished" would have meant --serialize-first
                # never released until the --serialize-cap-s watchdog did it
                # instead, silently testing the cap's timing rather than the
                # isolation itself.
                if release is not None and not release.is_set():
                    release.set()
                await _hold_until(client, char, stats, args, log, deadline)
        finally:
            stats.churn.link = None
            stats.connected_seconds += _now() - connected_at
            stats.disconnects += 1
            log("disconnected", stats.name, held_s=round(_now() - connected_at, 1))


async def soak_one(
    stats: SensorStats,
    slot: int,
    deadline: float,
    args,
    log,
    start_delay: float = 0.0,
    wait_for: asyncio.Event | None = None,
    release: asyncio.Event | None = None,
) -> None:
    """Keep *stats*' identity connected until the deadline, reconnecting as needed.

    *start_delay* only staggers the very first connection attempt — reconnects
    after that run on each identity's own schedule, same as before. The point
    is to test whether opening all 4 identities' connections at once (today's
    behaviour) is itself a source of the WinRT-side races already seen in this
    harness's own discovery/connect errors, by spacing out just the initial
    rush.

    *wait_for*/*release* implement --serialize-first: the designated first
    identity holds *release* and sets it once it has delivered real data (or
    gives up after --serialize-cap-s); everyone else holds *wait_for* and does
    not even attempt a connection until it is set. This is a stronger
    isolation than --connect-order/--stagger-s alone, which only change
    scheduling order within one shared asyncio.gather — it keeps the radio
    free of the other 3 identities entirely during identity 4's critical
    first-subscribe window.
    """
    if start_delay:
        await asyncio.sleep(start_delay)
    if wait_for is not None:
        await wait_for.wait()
    attempt = 0
    while _now() < deadline:
        try:
            before = stats.notifications
            await _one_connection(stats, slot, deadline, args, log, release=release)
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


async def _scan_once(args) -> list[SensorStats]:
    found = await BleakScanner.discover(timeout=args.scan_seconds)
    sensors = [
        SensorStats(name=d.name, address=d.address)
        for d in found
        if d.name and d.name.startswith(args.name_prefix)
    ]
    return sorted(sensors, key=lambda x: x.name)


async def discover(args, log) -> list[SensorStats]:
    """Scan for the board's advertised identities, retrying an empty scan.

    One empty scan must not end an overnight run. It is routinely transient: a
    peripheral stops advertising an identity while that identity is connected,
    so right after a previous client goes away the board stays quiet until its
    supervision timeout (~4 s here) expires and it re-advertises.
    """
    for attempt in range(args.discover_attempts):
        log("scanning", "-", seconds=args.scan_seconds, attempt=attempt + 1)
        sensors = await _scan_once(args)
        if sensors:
            for s in sensors:
                log("found", s.name, address=s.address)
            return sensors
        wait = RECONNECT_BACKOFF_S[min(attempt, len(RECONNECT_BACKOFF_S) - 1)]
        log("scan_empty", "-", retry_in_s=wait)
        await asyncio.sleep(wait)
    return []


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
            f"  resubscribes     : {s.resubscribes}   (re-armed notify on a silent connected link)",
            f"  polls with data  : {s.polls_with_data}   (explicit read succeeded while silent)",
            f"  disconnects      : {s.disconnects}",
            f"  gaps over thresh : {len(s.gaps)}",
        ]
        if s.churn.drops:
            lines.append(
                f"  churn            : {s.churn.drops} drops, {s.churn.recovered} recovered,"
                f" {s.churn.failed} failed, recovery s {s.churn.recovery_s}"
            )
        lines += [f"      {when}  {secs:.1f}s" for when, secs in s.gaps[:20]]
        if len(s.gaps) > 20:
            lines.append(f"      ... {len(s.gaps) - 20} more")
        lines.append("")
    return "\n".join(lines)


def result_dict(sensors: list[SensorStats], elapsed: float, args) -> dict:
    """Machine-readable twin of summarise(), for the A/B batch runner."""
    return {
        "label": args.label,
        "argv": {k: v for k, v in vars(args).items() if k != "duration"},
        "elapsed_s": round(elapsed, 1),
        "sensors": {
            s.name: {
                "address": s.address,
                "notifications": s.notifications,
                "connects": s.connects,
                "connect_failures": s.connect_failures,
                "subscribe_failures": s.subscribe_failures,
                "silent_subscribes": s.silent_subscribes,
                "resubscribes": s.resubscribes,
                "polls_with_data": s.polls_with_data,
                "gaps": len(s.gaps),
                "worst_gap_s": round(max((g for _, g in s.gaps), default=0.0), 1),
                "churns": s.churn.drops,
                "churn_recovered": s.churn.recovered,
                "churn_failed": s.churn.failed,
                "churn_recovery_s": s.churn.recovery_s,
                "uptime_pct": round(100.0 * s.connected_seconds / elapsed, 1) if elapsed else 0.0,
            }
            for s in sensors
        },
    }


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
    ap.add_argument("--scan-seconds", type=float, default=10.0, help="per-scan duration")
    ap.add_argument(
        "--discover-attempts",
        type=int,
        default=6,
        help="how many times an empty scan is retried before giving up (default: 6)",
    )
    ap.add_argument(
        "--first-notify-timeout",
        type=float,
        default=20.0,
        help="reconnect if no data arrives this soon after subscribing (default: 20)",
    )
    ap.add_argument("--connect-timeout", type=float, default=20.0, help="per-connect timeout")
    ap.add_argument(
        "--resubscribe-after-s",
        type=float,
        default=60.0,
        help="re-arm (stop/start_notify) a connected-but-silent link after this long;"
        " 0 disables it (default: 60)",
    )
    ap.add_argument(
        "--stagger-s",
        type=float,
        default=0.0,
        help="delay this long between starting each identity's initial connection (default: 0)",
    )
    ap.add_argument(
        "--connect-order",
        choices=["enumeration", "reverse"],
        default="enumeration",
        help="order identities connect in: scan-enumeration order, or reversed"
        " (last-enumerated first) to test whether the WinRT same-UUID defect is"
        " position-dependent (default: enumeration)",
    )
    ap.add_argument(
        "--poll-after-s",
        type=float,
        default=0.0,
        help="explicitly read the measurement characteristic after this long"
        " silent (diagnostic: tells apart a stuck notify pipe from a dead"
        " link, and recovers data either way); 0 disables it (default: 0)",
    )
    ap.add_argument(
        "--no-cache",
        action="store_true",
        help="connect with use_cached_services=False (WinRT only) — tests"
        " whether Windows' GATT cache confuses same-UUID CGMS instances",
    )
    ap.add_argument(
        "--serialize-first",
        action="store_true",
        help="fully connect + subscribe + confirm first data for the FIRST"
        " identity in --connect-order before starting any other identity's"
        " connection at all, instead of just reordering within a shared start",
    )
    ap.add_argument(
        "--serialize-cap-s",
        type=float,
        default=120.0,
        help="give up waiting on the serialized-first identity after this long"
        " and start the rest anyway (default: 120)",
    )
    ap.add_argument(
        "--log-notifications",
        action="store_true",
        help="write one millisecond-timestamped 'notify' event per notification"
        " to the JSONL (for inter-sensor phase analysis; a long run makes a big file)",
    )
    ap.add_argument(
        "--only",
        default="",
        help="comma-separated sensor numbers to connect (e.g. '4' or '3,4'); the"
        " rest are left alone. Separates 'this identity is broken' from 'the Nth"
        " concurrent connection is broken' (default: all)",
    )
    ap.add_argument(
        "--churn-s",
        type=float,
        default=0.0,
        help="every this many seconds drop ONE sensor's link (rotating) and time how"
        " long it takes to stream again, while the others keep going; 0 = off",
    )
    ap.add_argument(
        "--churn-recovery-s",
        type=float,
        default=120.0,
        help="a dropped sensor that is not streaming again after this long counts as"
        " a failed recovery (default: 120)",
    )
    ap.add_argument(
        "--label",
        default="",
        help="free-text tag stored in the JSON result (e.g. the firmware variant),"
        " so a batch can group runs without parsing file names",
    )
    ap.add_argument("--name-prefix", default=DEFAULT_NAME_PREFIX, help="advertised name prefix")
    ap.add_argument(
        "--snapshot-minutes",
        type=float,
        default=15.0,
        help="rewrite the summary this often while running (default: 15)",
    )
    ap.add_argument("--out", default=".scratch/soak", help="output directory")
    args = ap.parse_args()
    args.duration = args.hours * 3600 + args.minutes * 60 or 8 * 3600
    return args


async def _snapshot_loop(sensors: list[SensorStats], started: float, path: Path, every_s: float):
    """Rewrite the summary every *every_s* while the run is in progress.

    The totals otherwise exist only in memory until the run finishes: an
    unattended overnight run that is killed, or whose machine sleeps, would
    leave the event log but no counts at all.
    """
    while True:
        await asyncio.sleep(every_s)
        path.write_text(summarise(sensors, _now() - started), encoding="utf-8")


async def _churn_loop(sensors: list[SensorStats], args, log, deadline: float) -> None:
    """Drop one sensor's link every --churn-s, rotating, and time its recovery.

    Mirrors what the app does when a link is lost: the harness reconnects and
    re-subscribes on its own. The point is the bystanders — a reconnect on one
    identity must not stall the others — and the dropped sensor's time back to
    its first notification. Stops early enough that the last drop can recover.
    """
    turn = 0
    while True:
        await asyncio.sleep(args.churn_s)
        if _now() > deadline - args.churn_recovery_s - 10:
            return
        target = sensors[turn % len(sensors)]
        turn += 1
        client = target.churn.link
        if client is None or not client.is_connected:
            log("churn_skipped", target.name, reason="not connected")
            continue
        target.churn.drops += 1
        log("churn", target.name)
        try:
            await client.disconnect()
        except (Exception, asyncio.CancelledError) as exc:
            log("churn_disconnect_error", target.name, error=str(exc) or type(exc).__name__)
        before = target.notifications
        dropped_at = _now()
        while _now() - dropped_at < args.churn_recovery_s and target.notifications <= before:
            await asyncio.sleep(1.0)
        took = _now() - dropped_at
        if target.notifications > before:
            target.churn.recovered += 1
            target.churn.recovery_s.append(round(took, 1))
            log("churn_recovered", target.name, seconds=round(took, 1))
        else:
            target.churn.failed += 1
            log("churn_failed", target.name, waited_s=round(took, 1))


async def _timeout_release(event: asyncio.Event, cap_s: float) -> None:
    """Unblock the other identities even if --serialize-first's gate identity
    never delivers data, so one stuck identity cannot stall the whole run."""
    await asyncio.sleep(cap_s)
    if not event.is_set():
        event.set()


async def run(args, log, summary_path: Path) -> tuple[list[SensorStats], float]:
    sensors = await discover(args, log)
    if not sensors:
        log("no_sensors_found", "-", prefix=args.name_prefix)
        return [], 0.0
    if args.only:
        wanted = {int(n) - 1 for n in args.only.split(",") if n.strip()}
        sensors = [s for s in sensors if _slot_of(s.name) in wanted]
        if not sensors:
            log("no_sensors_found", "-", prefix=args.name_prefix, only=args.only)
            return [], 0.0
    started = _now()
    deadline = started + args.duration
    connect_order = list(reversed(sensors)) if args.connect_order == "reverse" else sensors
    gate = None
    watchdog = None
    if args.serialize_first:
        gate = asyncio.Event()
        watchdog = asyncio.create_task(_timeout_release(gate, args.serialize_cap_s))
    tasks = []
    for i, s in enumerate(connect_order):
        if args.serialize_first and i == 0:
            tasks.append(soak_one(s, _slot_of(s.name), deadline, args, log, release=gate))
        else:
            delay = 0.0 if args.serialize_first else i * args.stagger_s
            tasks.append(
                soak_one(s, _slot_of(s.name), deadline, args, log, start_delay=delay, wait_for=gate)
            )
    snapshot = asyncio.create_task(
        _snapshot_loop(sensors, started, summary_path, args.snapshot_minutes * 60)
    )
    churn = None
    if args.churn_s > 0:
        churn = asyncio.create_task(_churn_loop(sensors, args, log, deadline))
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        snapshot.cancel()
        if churn is not None:
            churn.cancel()
        if watchdog is not None:
            watchdog.cancel()
    return sensors, _now() - started


def main() -> int:
    args = parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    log = EventLog(out_dir / f"soak-{tag}.jsonl")
    summary_path = out_dir / f"soak-{tag}.txt"

    sensors: list[SensorStats] = []
    elapsed = 0.0
    started = _now()
    try:
        sensors, elapsed = asyncio.run(run(args, log, summary_path))
    except KeyboardInterrupt:
        elapsed = _now() - started
        print("\ninterrupted — writing summary for what ran so far", flush=True)
    finally:
        log.close()

    if not sensors:
        print("no sensors found — is the board powered, and is the app closed?")
        return 1
    report = summarise(sensors, elapsed or (_now() - started))
    summary_path.write_text(report, encoding="utf-8")
    summary_path.with_suffix(".json").write_text(
        json.dumps(result_dict(sensors, elapsed or (_now() - started), args), indent=2),
        encoding="utf-8",
    )
    print("\n" + report)
    print(f"events: {out_dir / f'soak-{tag}.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
