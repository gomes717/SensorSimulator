"""Hardware check of the app's silent-link watchdog, through the real BleSession.

Opens one BleSession per advertised sensor (exactly what the Bluetooth window
does), runs for a few minutes, and reports per sensor how many measurements
arrived and every link_silent event the watchdog raised. Run it with the app
closed — the board has four links.

    uv run python scripts/watchdog_check.py --minutes 4

Expected on the shipping firmware (notification stagger on): every sensor
streams and no link_silent events. On a build with CONFIG_APP_CGMS_STAGGER_NOTIFY
off, Sensor 4 is usually silent: the watchdog should re-arm it and, if that does
not help, raise link_silent(True) about a minute in.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import threading
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from bleak import BleakScanner
from PyQt6.QtCore import QCoreApplication

from services.ble_session import BleSession

PREFIX = "Nordic Glucose Sensor"


def scan(seconds: float = 10.0) -> dict[str, str]:
    """name -> address. On a worker thread: WinRT's scanner refuses the Qt main thread."""
    found: dict[str, str] = {}

    def go() -> None:
        async def inner() -> None:
            for dev, adv in (
                await BleakScanner.discover(timeout=seconds, return_adv=True)
            ).values():
                name = adv.local_name or dev.name or ""
                if name.startswith(PREFIX):
                    found[name] = dev.address

        asyncio.run(inner())

    thread = threading.Thread(target=go)
    thread.start()
    thread.join()
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--minutes", type=float, default=4.0)
    ap.add_argument(
        "--no-watchdog", action="store_true", help="disable the watchdog, to measure without it"
    )
    args = ap.parse_args()

    app = QCoreApplication(sys.argv)
    found = scan()
    if not found:
        print("no sensors found — is the board powered, and is the app closed?")
        return 1

    t0 = time.monotonic()
    counts: dict[str, int] = {}
    first: dict[str, float] = {}
    events: list[tuple[float, str, bool]] = []
    sessions: dict[str, BleSession] = {}

    def on_message(msg: dict) -> None:
        if msg.get("glucose_value") is None:
            return  # the board's other notifications also arrive as messages
        dev = msg.get("dev_id", "?")
        counts[dev] = counts.get(dev, 0) + 1
        first.setdefault(dev, time.monotonic() - t0)

    for name, address in sorted(found.items()):
        session = BleSession(address, name, silence_limit_s=1e9 if args.no_watchdog else None)
        session.new_message.connect(on_message)
        session.link_silent.connect(
            lambda addr, silent, n=name: events.append((time.monotonic() - t0, n, silent))
        )
        session.connected.connect(
            lambda _a, subscribed, total, err, n=name: print(
                f"[check] {n} connected: subscribed {subscribed}/{total} {err}"
            )
        )
        session.connect_failed.connect(lambda _a, err, n=name: print(f"[check] {n} FAILED: {err}"))
        session.disconnected.connect(lambda _a, n=name: print(f"[check] {n} disconnected"))
        session.start()
        sessions[name] = session

    end = t0 + args.minutes * 60
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.05)

    for session in sessions.values():
        session.stop()
    for session in sessions.values():
        session.wait(15000)

    print("\n=== watchdog check ===")
    for name, address in sorted(found.items()):
        got = counts.get(address, 0)
        lat = f"{first[address]:.0f}s" if address in first else "never"
        print(f"{name}: {got} measurements, first at {lat}")
    print(f"link_silent events: {[(round(t), n[-1], s) for t, n, s in events] or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
