"""Record the board's console while a phone (xDrip+) uses it as a Dexcom G6 transmitter.

    uv run python scripts/xdrip_session.py               # until Ctrl-C (or --minutes N)

Run it with the board in Dexcom mode (``8SIM00`` by default), COM10 free and the PC app closed. It
writes ``test-artifacts/xdrip-session-<stamp>/serial.log`` (the whole console) and ``SUMMARY.md``
(rewritten every minute): the readings the board published, every connection of the G6 identity
(0), the authentication steps and how each ended, the control requests the board refused, and the
connect windows. The firmware logs authentication, refusals and connects, but not the control
requests it answers, so a clean session shows as authentications followed by silence.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime

from ble_overnight import keep_awake
from hw_common import ARTIFACTS, SerialCapture

READING = re.compile(r"dexcom reading seq=(\d+) ts=(\d+)s glucose=(\d+)")
CONNECT = re.compile(r"^Connected \S+ \((?:public|random)\) \(identity (\d+)\)")
DISCONNECT = re.compile(
    r"^Disconnected from \S+ \((?:public|random)\) \(identity (\d+)\), reason (0x[0-9a-f]+)"
)
AUTH = re.compile(r"dexcom: auth op 0x([0-9a-f]{2}) -> (\d+) bytes \(authenticated=(\d)\)")
SUBSCRIPTION = re.compile(r"dexcom: subscription value (0x[0-9a-f]+)")
PROBLEMS = (
    "unknown auth write op",
    "control write before authentication",
    "bad crc on control op",
    "unknown op",
    "indication failed",
    "indicate failed",
)
MODE = re.compile(r"board mode -> (\w+)")


def parse_session(text: str) -> dict:
    """What the console says about the G6 identity: a plain dict, for the summary and tests."""
    out: dict = {
        "readings": [], "g6_links": [], "auth": [], "problems": [], "windows_closed": 0,
        "subscriptions": [], "modes": [], "app_links": 0, "boots": 0,
    }  # fmt: skip
    open_link: dict | None = None
    for line in text.splitlines():
        line = line.strip()
        if m := READING.search(line):
            out["readings"].append(tuple(int(x) for x in m.groups()))
        elif m := CONNECT.match(line):
            if m.group(1) == "0":
                open_link = {"auth_ops": [], "authenticated": False, "reason": None}
                out["g6_links"].append(open_link)
            else:
                out["app_links"] += 1
        elif m := DISCONNECT.match(line):
            if m.group(1) == "0" and open_link is not None:
                open_link["reason"] = m.group(2)
                open_link = None
        elif m := AUTH.search(line):
            step = (m.group(1), int(m.group(2)), m.group(3) == "1")
            out["auth"].append(step)
            if open_link is not None:
                open_link["auth_ops"].append(step[0])
                open_link["authenticated"] = open_link["authenticated"] or step[2]
        elif m := SUBSCRIPTION.search(line):
            out["subscriptions"].append(m.group(1))
        elif "dexcom: connect window closed" in line:
            out["windows_closed"] += 1
        elif m := MODE.search(line):
            out["modes"].append(m.group(1))
        elif "Starting Bluetooth Peripheral" in line:
            out["boots"] += 1
        if any(p in line for p in PROBLEMS):
            out["problems"].append(line)
    return out


def render(info: dict, started: float) -> str:
    links = info["g6_links"]
    authed = sum(1 for link in links if link["authenticated"])
    lines = [
        f"# xDrip session — {(time.time() - started) / 60:.0f} min",
        "",
        f"- readings published by the board: {len(info['readings'])}",
        f"- G6 connections (identity 0): {len(links)}; authenticated: {authed}; "
        f"never authenticated: {len(links) - authed}",
        f"- connect windows closed: {info['windows_closed']}; "
        f"app-identity links: {info['app_links']}",
        f"- board mode lines: {info['modes'] or 'none'}; reboots seen: {info['boots']}",
        f"- refused or malformed requests: {len(info['problems'])}",
        "",
        "## Readings (seq, ts s, glucose mg/dL)",
        *([f"- {r}" for r in info["readings"][-30:]] or ["- none yet"]),
        "",
        "## G6 connections",
        *(
            [
                f"- #{i + 1}: auth ops {link['auth_ops'] or 'none'}, "
                f"authenticated={link['authenticated']}, ended {link['reason'] or 'still open'}"
                for i, link in enumerate(links[-40:], start=max(0, len(links) - 40))
            ]
            or ["- none yet"]
        ),
        "",
        "## Refused or malformed requests",
        *([f"- `{p}`" for p in info["problems"][:40]] or ["- none"]),
    ]
    return "\n".join(lines) + "\n"


def main(minutes: float | None) -> int:
    keep_awake()
    run_dir = ARTIFACTS.parent / f"xdrip-session-{datetime.now():%Y%m%d-%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    end = None if minutes is None else started + minutes * 60
    print(f"recording to {run_dir} (Ctrl-C to stop)", flush=True)
    last = ""
    with SerialCapture(run_dir / "serial.log") as serial:
        try:
            while end is None or time.time() < end:
                time.sleep(15.0)
                if serial.idle_s() > 30.0:  # a stalled reader, not a quiet board
                    print("console silent; reopening the port", flush=True)
                    serial.restart()
                info = parse_session(serial.text())
                (run_dir / "SUMMARY.md").write_text(render(info, started), encoding="utf-8")
                now = (
                    f"readings {len(info['readings'])} g6 links {len(info['g6_links'])} "
                    f"authed {sum(1 for x in info['g6_links'] if x['authenticated'])} "
                    f"problems {len(info['problems'])}"
                )
                if now != last:
                    print(f"[{datetime.now():%H:%M:%S}] {now}", flush=True)
                    last = now
        except KeyboardInterrupt:
            pass
        finally:
            info = parse_session(serial.text())
            (run_dir / "SUMMARY.md").write_text(render(info, started), encoding="utf-8")
    print(f"summary: {run_dir / 'SUMMARY.md'}", flush=True)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--minutes", type=float, help="stop after this many minutes")
    sys.exit(main(parser.parse_args().minutes))
