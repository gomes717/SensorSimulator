"""Overnight test of the Users app on the real board: the whole frontend, the three sensors, the
board's own model against the app's, and the CSV loop — cycle after cycle, with seeded variety.

One cycle is a 25-minute run at x60 (25 simulated hours, so a 24 h CSV window wraps once), with
the setup before it and a run-state check after it:

  setup     connect the three sensors from the Bluetooth window; create three users from + New
            (every page, avatar, Preview, Save); Send to… each sensor — plain, "save before send"
            answered Save, and answered Send-without-saving then saved afterwards — checking the
            board holds what was sent; + Read from… every case (matches, unknown -> draft,
            differs -> Overwrite, differs -> Create "Name#2" which renames the board's user)
  run       Start; frontend work while it runs (create/delete an unused user, change an avatar and
            see the tab picture, open Configuration); Insert Food / Exercise / PISA from each
            sensor's Commands panel; a mid-run change of one model sensor (new meals and
            exercise, or model <-> CSV) sent to the board; a sensor tab closed and reconnected
  scored    per sensor: BLE complete, every BLE value a board push, the board's model against the
            host model, the app's expected model against the board's, the sensor-noise model, the
            events took effect; per CSV sensor: it plays its window and LOOPS past 24 h
  after     Pause holds the clock, Resume continues, Stop resets, Start streams again

    uv run python scripts/overnight_users.py --until 08:00

Run it with the app closed (it uses COM10 and the board) and the 3-sensor firmware flashed. It works
in a private data folder, so your own users and layout are not touched. Output goes to
``test-artifacts/overnight-users-<stamp>/``: SUMMARY.md (after every cycle), one folder per cycle
(REPORT.md, screenshots of every failure, the board's console), and serial.log for the night.
``--smoke`` runs one short cycle to see that everything is wired; ``--no-board`` runs only the
frontend flows (no hardware).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

import e2e_long_3sensor as lg
import overnight_analysis as oa
from ble_overnight import deadline_from, keep_awake, minutes_left
from e2e import SERIAL_PORT, SerialTap, Stream, Tee
from overnight_flows import Flows, Recorder, UserSpec, random_csv_window, random_spec, wait_until
from PyQt6.QtCore import QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox

from gui import run_controller
from gui.user_profile_window import UserProfileWindow
from models import app_settings, board_layout, profile_store, user_sim, user_store
from models.types import ModelId, SensorId, User

SLOTS = 3
LETTERS = "ABC"
SETTLE_S = 4.0  # after a reconnect: the app reads the slot back, and must not collide with a send


# ---- the environment -------------------------------------------------------------------------
@dataclass
class Epoch:
    """One rebuild of the app's expected-model engines."""

    index: int
    wall: float


@dataclass
class Issued:
    """An instant event that was sent, with when (host clock) and the slot's sim time."""

    inst: lg.Instant
    wall: float


@dataclass
class Assignment:
    """The user a slot was running from *since* (host clock) on."""

    user: User
    since: float


@dataclass
class Env:
    """Everything a cycle needs: the window, the flows, the logs and what was recorded."""

    window: object
    flows: Flows
    t0: float
    run_dir: Path
    serial: Stream | None
    has_board: bool
    speed: float = 60.0
    ble: dict[int, list[tuple[float, float]]] = field(
        default_factory=lambda: {i: [] for i in range(SLOTS)}
    )
    silent: list[tuple[int, int, bool]] = field(default_factory=list)
    hooked: set[int] = field(default_factory=set)
    epoch: int = -1
    app_ticks: dict[tuple[int, int], list[tuple[float, float]]] = field(default_factory=dict)
    modals: list[str] = field(default_factory=list)

    def now(self) -> float:
        return time.monotonic() - self.t0


def sandbox(data: Path) -> None:
    """Point every persistence module at a private data folder (like tests/conftest.py)."""
    data.mkdir(parents=True, exist_ok=True)
    for module, attr, name in (
        (app_settings, "_SETTINGS_FILE", "settings.json"),
        (board_layout, "_LAYOUT_FILE", "board_layout.json"),
        (profile_store, "_PROFILES_FILE", "profiles.json"),
        (user_store, "_USERS_FILE", "users.json"),
    ):
        module._DATA_DIR = data
        setattr(module, attr, data / name)
    user_store._USERS_DIR = data / "users"


def latest_tick(env: Env, slot: int) -> lg.Tick | None:
    """The board's most recent console tick for *slot* (None without a console or a tick)."""
    if env.serial is None:
        return None
    for line in reversed(env.serial.tail(900)):
        match = lg._TICK.match(line)
        if match and int(match["slot"]) == slot:
            return lg.Tick(
                float(match["t"]),
                float(match["t_sim"]),
                float(match["dt"]),
                float(match["g"]),
                float(match["r"]),
                float(match["pisa"]),
                float(match["carbs"]),
                float(match["ex"]),
            )
    return None


def install_taps(env: Env) -> None:
    """Record the BLE stream, the link-silent notices and the app's expected model; and close
    any modal question the harness did not answer (it would stop the night)."""
    w = env.window

    def on_message(m: dict) -> None:
        g = m.get("glucose_value")
        slot = next(
            (
                s
                for s, sess in env.flows.sessions_by_slot().items()
                if sess._address == m.get("dev_id")
            ),
            None,
        )
        if g is not None and slot is not None:
            env.ble[slot].append((env.now(), float(g)))

    w._ble_log.new_message.connect(on_message)

    pool = w.sim.engines
    original_rebuild = pool.rebuild

    def rebuilt(profiles, speed, *, paused):
        env.epoch += 1
        original_rebuild(profiles, speed, paused=paused)

    pool.rebuild = rebuilt

    def on_expected(slot: int, _ts: str, glucose: float, _carbs: float, _ex: float) -> None:
        env.app_ticks.setdefault((slot, env.epoch), []).append((env.now(), glucose))

    pool.expected_reading.connect(on_expected)

    def close_stray_modal() -> None:
        modal = QApplication.activeModalWidget()
        if modal is not None:
            title = modal.windowTitle()
            text = modal.text() if isinstance(modal, QMessageBox) else ""
            env.modals.append(f"{type(modal).__name__}: {title} {text}")
            env.flows.rec.check(
                "frontend",
                "no unexpected modal dialog",
                False,
                f"{type(modal).__name__} {title!r} {text!r}",
            )
            modal.close()

    timer = QTimer(w)
    timer.timeout.connect(close_stray_modal)
    timer.start(2000)
    env._modal_timer = timer  # type: ignore[attr-defined]


def hook_silence(env: Env) -> None:
    for slot, session in env.flows.sessions_by_slot().items():
        if id(session) not in env.hooked:
            env.hooked.add(id(session))
            session.link_silent.connect(
                lambda _a, silent, n=slot: env.silent.append((round(env.now()), n + 1, silent))
            )


# ---- one cycle -------------------------------------------------------------------------------
def plan_cycle(n: int, rng: random.Random) -> dict[int, UserSpec]:
    """Who runs where this cycle: slot n%3 is the CSV that loops and is never touched again; the
    others are a model or a CSV (at least one model); sensors and models rotate through all."""
    loop_slot = n % SLOTS
    others = [s for s in range(SLOTS) if s != loop_slot]
    modes = {loop_slot: "csv"}
    for s in others:
        modes[s] = rng.choice(("model", "csv"))
    if all(modes[s] == "csv" for s in others):
        modes[rng.choice(others)] = "model"
    specs: dict[int, UserSpec] = {}
    for slot in range(SLOTS):
        spec = random_spec(rng, modes[slot])
        spec.sensor_id = SensorId((n + slot) % 3)
        spec.model_id = ModelId((n + 2 * slot) % 4)
        spec.jitter = {"BW": 1.0}  # weight is the profile's; the rest are left at their defaults
        specs[slot] = spec
    return specs


def cycle_setup(env: Env, rec: Recorder, n: int, rng: random.Random) -> dict[int, Assignment]:
    """Connect, create the users, send them, read them back every way; who is on each slot."""
    f = env.flows
    w = env.window
    f.rec = rec
    users: dict[int, User] = {}
    assignments: dict[int, Assignment] = {}
    specs = plan_cycle(n, rng)

    if env.has_board:
        with rec.guard("bluetooth", "connect"):
            live = f.connect_sensors(list(range(SLOTS)))
            rec.check(
                "bluetooth",
                "all three sensors connect from the Bluetooth window",
                live == list(range(SLOTS)),
                f"live: {[s + 1 for s in live]}",
            )
            QTest.qWait(int(SETTLE_S * 1000))
            hook_silence(env)
            rec.check(
                "bluetooth",
                "each connected sensor has its tab",
                len(w.tabs.tab_keys()) >= len(live),
                f"{len(w.tabs.tab_keys())} tabs",
            )

    for slot in range(SLOTS):
        name = f"ON{n}{LETTERS[slot]}"
        with rec.guard("users", f"create {name}"):
            user = f.create_user(name, specs[slot])
            if user is not None:
                users[slot] = user
    if not env.has_board:
        return assignments

    live = f.sessions_by_slot()
    # -- Send to…: plain / save-before-send answered Save / answered Send then saved --------------
    variants = ["plain", "save", "send"]
    for k, slot in enumerate(range(SLOTS)):
        user = users.get(slot)
        if user is None or slot not in live:
            continue
        variant = variants[(k + n) % 3]
        label = f"{user.name} -> sensor {slot + 1} ({variant})"
        with rec.guard("send", label):
            weight_before = f.saved(user.name).weight_kg
            delta = 0.0 if variant == "plain" else 3.0
            screen, ok, message = f.send_from_screen(
                user, slot, unsaved_delta=delta, choice="send" if variant == "send" else "save"
            )
            rec.check("send", f"Send to… succeeds [{label}]", ok, message)
            rec.check(
                "send",
                f"'save before send' is asked only when something is unsaved [{label}]",
                f.asked_before_send == (1 if delta else 0),
                f"asked {f.asked_before_send}x",
            )
            sent = screen.user
            if variant == "send":
                rec.check(
                    "send",
                    f"sending without saving leaves the screen unsaved [{label}]",
                    screen.is_dirty,
                )
                rec.check(
                    "send",
                    f"... and the saved user unchanged [{label}]",
                    f.saved(user.name).weight_kg == weight_before,
                )
            elif variant == "save":
                rec.check(
                    "send",
                    f"Save and send saved the edit [{label}]",
                    not screen.is_dirty
                    and abs((f.saved(user.name).weight_kg or 0.0) - (sent.weight_kg or 0.0)) < 1e-6,
                )
            ok_board, detail = f.matches_board(sent, slot)
            rec.check("send", f"the board holds what was sent [{label}]", ok_board, detail)
            if variant == "send":
                rec.check(
                    "send",
                    f"saving afterwards stores it [{label}]",
                    screen.save()
                    and abs((f.saved(user.name).weight_kg or 0.0) - (sent.weight_kg or 0.0)) < 1e-6,
                )
            rec.check(
                "send",
                f"the slot records the user [{label}]",
                w.state.board_layout.slots[slot].person == user.name,
            )
            f.close_screen(screen)
            snap = f.snapshot_user(user.name)
            if snap is not None:
                users[slot] = snap
                assignments[slot] = Assignment(snap, env.now())

    # -- Read from…: matches / unknown / differs->Overwrite / differs->Create ------------------
    order = [(n + i) % SLOTS for i in range(SLOTS)]
    cases = ["matches", "unknown", "overwrite"]
    for case, slot in zip(cases, order, strict=True):
        user = users.get(slot)
        if user is None or slot not in f.sessions_by_slot():
            continue
        with rec.guard("read", f"{case} on sensor {slot + 1}"):
            read_case(env, rec, case, slot, user, users, assignments)
    # Create "Name#2" last, on the first slot again (its user may have changed above)
    slot = order[0]
    if users.get(slot) is not None and slot in f.sessions_by_slot():
        with rec.guard("read", f"create on sensor {slot + 1}"):
            read_case(env, rec, "create", slot, users[slot], users, assignments)

    # Reading does not move a slot's record: put the layout back in step with the board, the way a
    # user would (a send), so Start does not write a different user over the board's.
    for slot, user in users.items():
        if slot in f.sessions_by_slot() and w.state.board_layout.slots[slot].person != user.name:
            rec.note(
                f"after the reads the slot record for sensor {slot + 1} named "
                f"{w.state.board_layout.slots[slot].person!r}, the board runs {user.name!r}"
            )
            with rec.guard("send", f"re-send {user.name}"):
                screen, ok, message = f.send_from_screen(user, slot)
                rec.check("send", f"re-sending {user.name} works", ok, message)
                f.close_screen(screen)
                assignments[slot] = Assignment(user, env.now())
    return assignments


def read_case(
    env: Env,
    rec: Recorder,
    case: str,
    slot: int,
    user: User,
    users: dict[int, User],
    assignments: dict[int, Assignment],
) -> None:
    f = env.flows
    w = env.window
    name = user.name
    label = f"{case} [{name} on sensor {slot + 1}]"
    if case == "matches":
        outcome, status = f.read_from(slot)
        rec.check(
            "read",
            f"a board user equal to the saved one opens it [{label}]",
            outcome == "opened" and f.opened[-1][0].id == f.saved(name).id,
            f"{outcome}: {status}",
        )
    elif case == "unknown":
        rec.check("read", f"the user can be deleted [{label}]", f.delete_user(name))
        outcome, status = f.read_from(slot)
        rec.check(
            "read",
            f"an unknown board user opens as a draft [{label}]",
            outcome == "draft" and f.opened[-1][0].name == name,
            f"{outcome}: {status}",
        )
        if outcome == "draft":
            screen = f.screen_for(f.opened[-1][0])
            rec.check("read", f"the draft is not saved until Save [{label}]", f.saved(name) is None)
            rec.check("read", f"the draft saves [{label}]", screen.save(), screen.error.text())
            saved = f.saved(name)
            rec.check(
                "read",
                f"the saved draft matches the board [{label}]",
                saved is not None and f.matches_board(saved, slot)[0],
            )
            f.close_screen(screen)
            if saved is not None:
                users[slot] = f.snapshot_user(name) or saved
                assignments[slot] = Assignment(users[slot], env.now())
    elif case in ("overwrite", "create"):
        screen = f.open_user(f.saved(name))
        f.edit_weight(screen, 2.0)
        rec.check(
            "read",
            f"the saved user is edited so it differs from the board [{label}]",
            screen.save(),
        )
        f.close_screen(screen)
        edited = f.snapshot_user(name)
        f.answers["differs"] = case
        outcome, status = f.read_from(slot)
        rec.check(
            "read", f"a differing board user opens a user [{label}]", outcome == "opened", status
        )
        if case == "overwrite":
            now_saved = f.saved(name)
            rec.check(
                "read",
                f"Overwrite makes the saved user what the board runs [{label}]",
                now_saved is not None
                and f.matches_board(now_saved, slot)[0]
                and edited is not None
                and now_saved.weight_kg != edited.weight_kg,
                f"weight {edited and edited.weight_kg} -> {now_saved and now_saved.weight_kg}",
            )
            users[slot] = f.snapshot_user(name) or users[slot]
        else:
            copy = next((u for u in w.state.users if u.name.startswith(f"{name}#")), None)
            rec.check(
                "read",
                f"Create adds a copy named {name}#N [{label}]",
                copy is not None,
                f"{[u.name for u in w.state.users if u.name.startswith(name)]}",
            )
            rec.check(
                "read",
                f"... and leaves the edited original [{label}]",
                f.saved(name) is not None and f.saved(name).weight_kg == edited.weight_kg,
            )
            if copy is not None:
                reading, error = f.board_read(slot)
                rec.check(
                    "read",
                    f"... and renames the board's user to the copy [{label}]",
                    reading is not None and reading.name == copy.name,
                    f"board says {reading and reading.name!r} {error}",
                )
                users[slot] = f.snapshot_user(copy.name) or copy
                assignments[slot] = Assignment(users[slot], env.now())
        # leave the harness's answers as they were, and close what the read opened
        f.answers["differs"] = "overwrite"
    for screen in list(w.findChildren(UserProfileWindow)):
        f.close_screen(screen)


# ---- the run ---------------------------------------------------------------------------------
def model_slots(assignments: dict[int, Assignment]) -> list[int]:
    return [s for s, a in assignments.items() if a.user.mode == "model"]


def frontend_batch(
    env: Env, rec: Recorder, n: int, rng: random.Random, assignments: dict[int, Assignment]
) -> None:
    """Frontend work while the run is going: nothing here may disturb the graphs or the board."""
    f = env.flows
    w = env.window
    with rec.guard("frontend", "create and delete an unused user while running"):
        before = f.restarts
        spec = random_spec(rng, "model")
        name = f"ON{n}tmp"
        user = f.create_user(name, spec, area="frontend")
        rec.check("frontend", "a user created while running is listed", user is not None)
        if user is not None:
            rec.check("frontend", "deleting an unused user works", f.delete_user(name))
            rec.check(
                "frontend",
                "...removes it from the list and from disk",
                f.saved(name) is None and not any(u.name == name for u in user_store.load()),
            )
            rec.check(
                "frontend",
                "...without restarting the running graphs",
                f.restarts == before,
                f"{f.restarts - before} restart(s)",
            )
    with rec.guard("frontend", "change an avatar and see it on the tab"):
        slots = [s for s in assignments if s in f.sessions_by_slot()]
        if slots:
            slot = rng.choice(slots)
            user = f.saved(assignments[slot].user.name)
            screen = f.open_user(user)
            path = f.pick_avatar(screen, (n + slot + 1) % 6)
            rec.check(
                "frontend",
                "the new avatar is shown before saving",
                screen.is_dirty and path is not None,
            )
            rec.check("frontend", "the avatar saves", screen.save(), screen.error.text())
            QTest.qWait(300)
            rec.check(
                "frontend",
                f"the picture is on sensor {slot + 1}'s tab",
                f.tab_shows_picture(slot) is True,
            )
            f.close_screen(screen)
    with rec.guard("frontend", "Configuration window"):
        w.windows.open("configuration")
        QTest.qWait(300)
        cfg = w.windows.get("configuration")
        rec.check(
            "frontend",
            "Configuration opens tall enough for its content",
            cfg is not None and cfg.height() >= cfg.minimumSizeHint().height(),
            f"{cfg and cfg.height()} vs {cfg and cfg.minimumSizeHint().height()}",
        )
        if cfg is not None:
            cfg.close()
    with rec.guard("frontend", "Users window and sensor tabs"):
        uw = f.users_window()
        rec.check(
            "frontend",
            "the Users list names every user and where it runs",
            uw.list.count() == len(w.state.users),
        )
        for key in list(w.tabs.tab_keys()):
            w.sensors.on_user_selected(key)
            QTest.qWait(150)
        rec.check("frontend", "every tab can be selected", True)
        screen_geometry = uw.screen().availableGeometry()
        rec.check(
            "frontend",
            "the Users window fits the screen",
            uw.width() <= screen_geometry.width() and uw.height() <= screen_geometry.height(),
        )
        uw.close()


def mid_run_change(
    env: Env,
    rec: Recorder,
    n: int,
    rng: random.Random,
    assignments: dict[int, Assignment],
    loop_slot: int,
) -> int | None:
    """Change one model sensor while running and send it: new meals/exercise (even cycles) or the
    other source, model <-> CSV (odd cycles). Never the looping CSV sensor. Returns the slot."""
    f = env.flows
    candidates = [
        s for s in model_slots(assignments) if s != loop_slot and s in f.sessions_by_slot()
    ]
    flip = n % 2 == 1
    if flip:  # any live sensor but the loop one; the last model sensor stays a model
        spare_models = len(model_slots(assignments)) > 1
        candidates = [
            s
            for s in assignments
            if s != loop_slot
            and s in f.sessions_by_slot()
            and (assignments[s].user.mode == "csv" or spare_models)
        ]
    if not candidates:
        return None
    slot = rng.choice(candidates)
    name = assignments[slot].user.name
    label = f"{name} on sensor {slot + 1} ({'flip source' if flip else 'new meals and exercise'})"
    with rec.guard("midrun", label):
        screen = f.open_user(f.saved(name))
        if flip:
            page = screen.profile_page
            to_csv = screen.user.mode == "model"
            (page.csv_radio if to_csv else page.model_radio).setChecked(True)
            if to_csv and screen.user.csv is None:
                path, start = random_csv_window(rng)
                screen.pages["csv"]._apply_picked(str(path), start)
        else:
            f._add_event(
                screen.pages["food"],
                rng.randrange(60, 1380, 30),
                float(rng.choice((40, 50, 60))),
                30,
            )
            f._add_event(screen.pages["exercise"], rng.randrange(360, 1200, 60), 30, 30.0)
        f.answers["slot"] = slot
        f.answers["save_before_send"] = "save"
        f.asked_before_send = 0
        screen.send()
        wait_until(lambda: not screen.error.text().startswith("Sending"), 300.0)
        ok = "2e7d32" in screen.error.styleSheet()
        rec.check(
            "midrun",
            f"the changed user is sent to the running sensor [{label}]",
            ok,
            screen.error.text(),
        )
        rec.check(
            "midrun", f"'save before send' was asked once [{label}]", f.asked_before_send == 1
        )
        sent = screen.user
        ok_board, detail = f.matches_board(sent, slot)
        rec.check("midrun", f"the board holds the changed user [{label}]", ok_board, detail)
        f.close_screen(screen)
        snap = f.snapshot_user(name)
        if snap is not None:
            assignments[slot] = Assignment(snap, env.now())
    return slot


def reconnect_check(env: Env, rec: Recorder, slot: int) -> None:
    f = env.flows
    with rec.guard("bluetooth", f"close sensor {slot + 1}'s tab and reconnect"):
        before = latest_tick(env, slot)
        rec.check("bluetooth", f"closing sensor {slot + 1}'s tab disconnects it", f.close_tab(slot))
        QTest.qWait(6000)
        marker = env.now()
        live = f.connect_sensors([slot])
        rec.check("bluetooth", f"sensor {slot + 1} reconnects", slot in live)
        QTest.qWait(int(SETTLE_S * 1000))
        hook_silence(env)
        got = wait_until(lambda: any(t >= marker for t, _ in env.ble[slot]), 60.0)
        rec.check("bluetooth", f"sensor {slot + 1} streams again after reconnecting", got)
        after = latest_tick(env, slot)
        rec.check(
            "bluetooth",
            "the board kept simulating through the disconnect (no reset)",
            before is not None and after is not None and after.t_sim > before.t_sim,
            f"t_sim {before and before.t_sim} -> {after and after.t_sim}",
        )
        rec.check("bluetooth", f"sensor {slot + 1}'s tab is back", f.tab_key(slot) is not None)


def run_phase(
    env: Env,
    rec: Recorder,
    n: int,
    rng: random.Random,
    assignments: dict[int, Assignment],
    minutes: float,
    speed: float,
) -> dict:
    """Start, run the timeline, return what the analysis needs."""
    f = env.flows
    w = env.window
    loop_slot = n % SLOTS
    started = f.start_run(speed)
    rec.check("run", "Start pushes the configuration and the run begins", started, f.run_state())
    if not started:
        return {"t_start": env.now(), "t_end": env.now(), "issued": [], "reconnected": None}
    t_start = env.now()
    QTest.qWait(3000)
    rec.check(
        "run",
        "the app's expected model runs for every model sensor",
        set(w.sim.engines.slots) >= set(model_slots(assignments)),
        f"engines {w.sim.engines.slots}, model slots {model_slots(assignments)}",
    )

    start = time.monotonic()
    end = start + minutes * 60.0
    scale = minutes / 25.0
    issued: list[Issued] = []
    reconnected: tuple[int, float, float] | None = None
    stalled_since: float | None = None
    longest_stall = 0.0

    def fire_instant(kind: str, args: tuple) -> None:
        slots = [s for s in model_slots(assignments) if s in f.sessions_by_slot()]
        if not slots:
            return
        slot = slots[len(issued) % len(slots)]
        tick = latest_tick(env, slot)
        if tick is None:
            rec.check(
                "events",
                f"{kind} {args} has a console tick to anchor on [sensor {slot + 1}]",
                False,
            )
            return
        accepted = f.instant(slot, kind, args)
        rec.check(
            "events", f"{kind} {args} accepted by sensor {slot + 1}'s Commands panel", accepted
        )
        if accepted:
            issued.append(Issued(lg.Instant(slot, kind, args, tick.t_sim), env.now()))

    plan = [
        (2.0, "frontend", lambda: frontend_batch(env, rec, n, rng, assignments)),
        (4.0, "food", lambda: fire_instant("food", (45, 60.0))),
        (6.0, "pisa", lambda: fire_instant("pisa", (12, 0.45))),
        (8.0, "exercise", lambda: fire_instant("exercise", (30, 30.0))),
        (10.0, "midrun", lambda: mid_run_change(env, rec, n, rng, assignments, loop_slot)),
        (13.5, "reconnect", None),
        (16.0, "food", lambda: fire_instant("food", (30, 40.0))),
        (17.5, "exercise", lambda: fire_instant("exercise", (30, 30.0))),
        (19.0, "pisa", lambda: fire_instant("pisa", (12, 0.45))),
    ]
    todo = sorted(plan, key=lambda p: p[0])
    while time.monotonic() < end:
        now = time.monotonic()
        if todo and (now - start) >= todo[0][0] * 60.0 * scale:
            _, kind, action = todo.pop(0)
            if kind == "reconnect":
                slot = (n + 1) % SLOTS
                m = env.now()
                reconnect_check(env, rec, slot)
                reconnected = (slot, m, env.now())
            elif action is not None:
                with rec.guard("run", f"the {kind} step"):
                    action()
        if env.serial is not None:
            if env.serial.fresh(15.0):
                stalled_since = None
            else:
                stalled_since = stalled_since or now
                longest_stall = max(longest_stall, now - stalled_since)
        QTest.qWait(500)
    t_end = env.now()
    rec.check(
        "run",
        "the console never went silent for a minute",
        longest_stall < 60.0,
        f"longest {longest_stall:.0f}s",
    )
    return {
        "t_start": t_start,
        "t_end": t_end,
        "issued": issued,
        "reconnected": reconnected,
        "speed": speed,
    }


def run_state_checks(env: Env, rec: Recorder, assignments: dict[int, Assignment]) -> None:
    """Pause holds the clock, Resume continues it, Stop resets, Start streams again."""
    f = env.flows
    w = env.window
    slot = next(iter(model_slots(assignments)), next(iter(assignments), 0))
    run = w._run
    with rec.guard("runstate", "pause / resume / stop / start"):
        silent_before = len(env.silent)
        run.toggle()
        QTest.qWait(3500)
        held1 = latest_tick(env, slot)
        QTest.qWait(6000)
        held2 = latest_tick(env, slot)
        rec.check(
            "runstate",
            "Pause holds the board's clock",
            run.state == run_controller.PAUSED
            and held1
            and held2
            and abs(held2.t_sim - held1.t_sim) < 0.5,
            f"{held1 and held1.t_sim} -> {held2 and held2.t_sim}",
        )
        run.toggle()
        QTest.qWait(6000)
        resumed = latest_tick(env, slot)
        rec.check(
            "runstate",
            "Resume continues from the held clock",
            run.state == run_controller.RUNNING
            and held2
            and resumed
            and resumed.t_sim >= held2.t_sim - 0.05,
            f"{held2 and held2.t_sim} -> {resumed and resumed.t_sim}",
        )
        run.stop()
        QTest.qWait(25000)
        rec.check(
            "runstate",
            "Stop leaves the run stopped and the link is not reported silent",
            run.state == run_controller.STOPPED and len(env.silent) == silent_before,
            f"{env.silent[silent_before:]}",
        )
        marker = env.now()
        f.start_run(env.speed)
        got = wait_until(
            lambda: all(any(t >= marker for t, _ in env.ble[s]) for s in f.sessions_by_slot()), 60.0
        )
        rec.check("runstate", "Start after Stop streams on every sensor", got)
        back = latest_tick(env, slot)
        rec.check(
            "runstate",
            "Stop reset the model clock",
            back is not None and back.t_sim < 120.0,
            f"t_sim={back and back.t_sim}",
        )
        run.stop()
        QTest.qWait(3000)


# ---- scoring ---------------------------------------------------------------------------------
def analyse(
    env: Env, rec: Recorder, assignments: dict[int, Assignment], info: dict, serial_offset: int
) -> None:
    """Score the measured run against what was on each slot."""
    if env.serial is None:
        return
    t_start, t_end, speed = info["t_start"], info["t_end"], info.get("speed", 60.0)
    dt = speed / 60.0
    serial_log = env.run_dir / "serial.log"
    ticks = {
        s: [t for t in v if t.t <= t_end]
        for s, v in lg.read_ticks(serial_log, serial_offset).items()
    }
    pushes = lg.read_pushes(serial_log, serial_offset)
    issued: list[Issued] = info["issued"]
    reconnected = info["reconnected"]

    for slot in range(SLOTS):
        assignment = assignments.get(slot)
        if assignment is None:
            continue
        user = assignment.user
        area = f"sensor {slot + 1}"
        times = [(t, v) for t, v in env.ble[slot] if t_start <= t <= t_end]
        if reconnected and reconnected[0] == slot:
            times = [(t, v) for t, v in times if t >= reconnected[2] + 10.0]  # the link was down
        count, expected, share, gap = lg.completeness([t for t, _ in times])
        rec.check(
            area,
            "BLE is complete",
            share >= lg.MIN_COMPLETENESS and gap <= lg.MAX_GAP_S,
            f"{count}/{expected:.0f} ({share:.1%}), worst gap {gap:.1f}s",
        )
        matched, total, bad = lg.ble_match(times, pushes.get(slot, []))
        rec.check(
            area,
            "every BLE value is one the board pushed",
            total > 0 and matched / total >= lg.MIN_BLE_MATCH,
            f"{matched}/{total}" + (f" unmatched {bad}" if bad else ""),
        )

        slot_ticks = ticks.get(slot, [])
        profile = user_sim.person_profile_of(user)
        segments = oa.board_segments(slot_ticks)
        last = segments[-1] if segments else []
        mine = [e for e in issued if e.inst.slot == slot and last and e.wall >= last[0].t - 1.0]
        instants = [e.inst for e in mine]

        noise = oa.sensor_noise(user.sensor_id, slot_ticks)
        if noise["ok"] is not None:
            rec.check(
                area,
                f"the {user.sensor_id.name} sensor model behaves",
                noise["ok"],
                f"residual max {noise['max']:.2f}, std {noise['std']:.2f}",
            )

        if user.mode == "model":
            result = lg.parity(profile, slot_ticks, instants, oa.APP_SLACK_TICKS)
            if result.get("n"):
                limit = max(
                    oa.APP_VS_BOARD_P99_MIN,
                    oa.app_limit(profile, instants, speed, result["host_range"]),
                )
                rec.check(
                    area,
                    f"the board follows the host model ({user.name}, {profile.model_id.name})",
                    result["p99"] <= limit,
                    f"p99 {result['p99']:.2f} max {result['max']:.2f} mean {result['mean']:.3f} "
                    f"limit {limit:.1f} "
                    f"over {result['n']} ticks",
                )
            else:
                rec.check(
                    area, "the board's model follows the host model", False, "no console ticks"
                )
            for e in mine:
                check = lg.replay_check(e.inst, slot_ticks)
                rec.check(
                    area,
                    f"{e.inst.kind} {e.inst.args} took effect",
                    check.result == "PASS",
                    f"{check.result} {check.seen}",
                )
        else:
            _samples, _interval_s, foodlog = lg.load_csv_window(profile)
            loop = oa.csv_loop(profile, slot_ticks)
            span_min = loop.get("span_min", 0.0)
            reached = loop.get("reached_min", 0.0)
            rec.check(
                area,
                f"plays the CSV it was sent ({user.csv.source_name if user.csv else '?'})",
                loop["n"] > 0 and loop["ok_all"] / loop["n"] >= oa.CSV_LOOP_MIN_SHARE,
                f"{loop['ok_all']}/{loop['n']} within {oa.CSV_LOOP_TOL:g} mg/dL",
            )
            if reached >= span_min + 10:
                rec.check(
                    area,
                    "the CSV loops: past 24 h it replays the window from the start",
                    loop["wrapped"] and loop["ok"] / max(1, loop["after"]) >= oa.CSV_LOOP_MIN_SHARE,
                    f"{loop['ok']}/{loop['after']} ticks past the end match; "
                    f"worst {loop['worst']:.1f}; "
                    f"reached {reached:.0f} of {span_min:.0f} min",
                )
            else:
                rec.note(
                    f"{area}: the CSV was not run past its end "
                    f"(reached {reached:.0f} of {span_min:.0f} min)"
                )
            inside = [(off / 60.0, g) for off, g in foodlog if off / 60.0 + 5 < reached]
            run = lg.last_run(slot_ticks)
            hits = sum(
                any(m <= tk.t_sim <= m + 60 and tk.carbs > 0.001 for tk in run) for m, _ in inside
            )
            if inside:
                rec.check(
                    area,
                    "the CSV food log shows up as carbs",
                    hits / len(inside) >= 0.95,
                    f"{hits}/{len(inside)}",
                )

        # -- the app's expected model against the board's (epochs that began together) ------
        if user.mode == "model":
            epochs = sorted({e for (s, e) in env.app_ticks if s == slot})
            starts = [env.app_ticks[(slot, e)][0][0] for e in epochs if env.app_ticks[(slot, e)]]
            usable = [e for e in epochs if env.app_ticks[(slot, e)]]
            for ei, si in oa.pair_epochs(starts, list(segments)):
                raw = env.app_ticks[(slot, usable[ei])]
                if raw[0][0] < t_start - 1.0 or len(raw) < oa.APP_MIN_TICKS:
                    continue
                app = [oa.AppTick((k + 1) * dt, g) for k, (_t, g) in enumerate(raw)]
                seg_instants = [
                    e.inst
                    for e in issued
                    if e.inst.slot == slot
                    and segments[si]
                    and segments[si][0].t - 1.0 <= e.wall <= segments[si][-1].t + 1.0
                ]
                result = oa.app_vs_board(app, segments[si], dt, oa.masks_for(seg_instants, dt))
                if result["n"]:
                    limit = oa.app_limit(profile, seg_instants, speed, result["range"])
                    rec.check(
                        area,
                        f"the app's expected model follows the board's (epoch {usable[ei]})",
                        result["p99"] <= limit,
                        f"p99 {result['p99']:.2f} max {result['max']:.2f} over {result['n']} "
                        f"ticks, limit {limit:.1f}",
                    )

    raised = [e for e in env.silent if e[2] and t_start <= e[0] <= t_end]
    rec.check("run", "no link_silent was raised during the run", not raised, f"{raised or 'none'}")


# ---- cycles, reports, main -------------------------------------------------------------------
def cleanup_cycle(env: Env, rec: Recorder, n: int) -> None:
    """Delete the cycle's users (a delete flow of its own) and close every screen."""
    f = env.flows
    f.rec = rec
    for screen in list(env.window.findChildren(UserProfileWindow)):
        f.close_screen(screen)
    if env.window._run.state != run_controller.STOPPED:
        env.window._run.stop()
        QTest.qWait(2000)
    for user in [u for u in list(env.window.state.users) if u.name.startswith(f"ON{n}")]:
        rec.check("users", f"{user.name} deletes", f.delete_user(user.name))
    rec.check(
        "users",
        "deleted users are gone from disk",
        not any(u.name.startswith(f"ON{n}") for u in user_store.load()),
    )


def write_report(
    cdir: Path, n: int, rec: Recorder, started: float, minutes: float, speed: float, seed: int
) -> None:
    cdir.mkdir(parents=True, exist_ok=True)
    failed = rec.failed()
    lines = [
        f"# Cycle {n} — {'PASS' if not failed else f'FAIL ({len(failed)})'}",
        "",
        f"{minutes:g} min at x{speed:g} = {minutes * speed / 60:.1f} simulated hours; seed {seed}; "
        f"{(time.monotonic() - started) / 60:.1f} min in all.",
        "",
    ]
    if failed:
        lines += ["## Failed", ""]
        lines += [
            f"- **[{c.area}]** {c.name}" + (f" — {c.detail}" if c.detail else "") for c in failed
        ]
        lines.append("")
    if rec.notes:
        lines += ["## Notes", ""] + [f"- {note}" for note in rec.notes] + [""]
    lines += ["## All checks", "", "| result | area | check | detail |", "|---|---|---|---|"]
    for c in rec.checks:
        detail = c.detail.replace("|", "/").replace("\n", " ")
        lines.append(f"| {'PASS' if c.ok else '**FAIL**'} | {c.area} | {c.name} | {detail} |")
    (cdir / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (cdir / "checks.json").write_text(
        json.dumps([c.__dict__ for c in rec.checks], indent=1), encoding="utf-8"
    )


def write_summary(run_dir: Path, results: list[dict], started: datetime, deadline) -> None:
    lines = [
        "# Overnight Users test",
        "",
        f"Started {started:%Y-%m-%d %H:%M}; {len(results)} cycle(s).",
        "",
        "| cycle | result | checks | failed | notes |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['cycle']} | {'PASS' if r['ok'] else 'FAIL'} | {r['checks']} "
            f"| {len(r['failed'])} | {r.get('error', '')} |"
        )
    bad = [r for r in results if not r["ok"]]
    if bad:
        lines += ["", "## Failures", ""]
        for r in bad:
            lines.append(f"### Cycle {r['cycle']}")
            lines += [f"- {name}" for name in r["failed"][:40]]
    (run_dir / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_cycle(env: Env, n: int, minutes: float, speed: float, seed: int) -> dict:
    cdir = env.run_dir / f"cycle-{n:02d}"
    rec = Recorder(cdir / "shots")
    env.flows.rec = rec
    rng = random.Random(seed * 1000 + n)
    started = time.monotonic()
    assignments: dict[int, Assignment] = {}
    error = ""
    try:
        assignments = cycle_setup(env, rec, n, rng)
        if env.has_board and assignments:
            offset = (env.run_dir / "serial.log").stat().st_size
            info = run_phase(env, rec, n, rng, assignments, minutes, speed)
            rec.check("run", "the run phase completed", True)
            with rec.guard("analysis", "score the run"):
                analyse(env, rec, assignments, info, offset)
            run_state_checks(env, rec, assignments)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        rec.check("cycle", "the cycle ran to the end", False, error)
        traceback.print_exc()
    finally:
        with contextlib.suppress(Exception):
            cleanup_cycle(env, rec, n)
    write_report(cdir, n, rec, started, minutes, speed, seed)
    return {
        "cycle": n,
        "ok": not rec.failed(),
        "checks": len(rec.checks),
        "failed": [f"[{c.area}] {c.name}" for c in rec.failed()],
        "error": error,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--until",
        default="08:00",
        help="stop starting cycles when one would run past this local time",
    )
    ap.add_argument("--cycles", type=int, default=0, help="stop after this many (0: until --until)")
    ap.add_argument(
        "--minutes", type=float, default=25.0, help="the measured run per cycle (default 25)"
    )
    ap.add_argument(
        "--sim-hours", type=float, default=25.0, help="simulated hours that run covers (default 25)"
    )
    ap.add_argument("--seed", type=int, default=int(time.time()) % 100000)
    ap.add_argument(
        "--smoke", action="store_true", help="one short cycle (6 min at x250) to see it is wired"
    )
    ap.add_argument("--no-board", action="store_true", help="only the frontend flows, no hardware")
    ap.add_argument("--out", default=str(_ROOT / "test-artifacts"))
    args = ap.parse_args()
    if args.smoke:
        args.cycles, args.minutes, args.sim_hours = 1, 6.0, 25.0
    speed = round(args.sim_hours * 60.0 / args.minutes, 3)

    keep_awake()
    started = datetime.now()
    deadline = deadline_from(args.until)
    run_dir = Path(args.out) / f"overnight-users-{datetime.now(UTC):%Y%m%d-%H%M%SZ}"
    run_dir.mkdir(parents=True, exist_ok=True)
    sandbox(run_dir / "data")
    t0 = time.monotonic()
    streams = {
        "serial": Stream("serial", run_dir / "serial.log", 4000),
        "app": Stream("app", run_dir / "app.log", 2000),
    }
    sys.stdout = Tee(sys.__stdout__, streams["app"], t0)
    sys.stderr = Tee(sys.__stderr__, streams["app"], t0)
    has_board = not args.no_board
    tap = None
    if has_board:
        tap = SerialTap(SERIAL_PORT, streams["serial"], t0)
        if not tap.available:
            print(
                f"no firmware console on {SERIAL_PORT}: close the app and other serial tools, "
                "or use --no-board"
            )
            return 1
    print(f"seed {args.seed}; {args.minutes:g} min at x{speed:g}; output {run_dir}")

    import gui.main_window as mw

    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841
    w = mw.MainWindow()
    w.show()
    QTest.qWait(400)
    w._on_model_only_toggled(False)
    rec0 = Recorder()
    env = Env(
        w,
        Flows(w, rec0, random.Random(args.seed), has_board),
        t0,
        run_dir,
        streams["serial"] if has_board else None,
        has_board,
    )
    install_taps(env)

    results: list[dict] = []
    failures_in_a_row = 0
    n = 0
    try:
        while (not args.cycles or n < args.cycles) and not (run_dir / "STOP").exists():
            if not args.cycles and minutes_left(deadline) < args.minutes + 16:
                break
            n += 1
            print(f"== cycle {n} ({minutes_left(deadline):.0f} min to the deadline)")
            result = run_cycle(env, n, args.minutes, speed, args.seed)
            failures_in_a_row = failures_in_a_row + 1 if result["error"] else 0
            results.append(result)
            write_summary(run_dir, results, started, deadline)
            print(
                f"== cycle {n}: {'PASS' if result['ok'] else 'FAIL'} "
                f"({result['checks']} checks, {len(result['failed'])} failed)"
            )
            if failures_in_a_row >= 4:
                print("four cycles in a row did not run to the end: stopping")
                break
    finally:
        with contextlib.suppress(Exception):
            w.sim.engines.stop_all()
        with contextlib.suppress(Exception):
            w.windows.bluetooth.stop_all_sessions()
        if tap is not None:
            with contextlib.suppress(Exception):
                tap.stop()
        write_summary(run_dir, results, started, deadline)
        (run_dir / "done").write_text(datetime.now().isoformat(), encoding="utf-8")
    print(f"summary: {run_dir / 'SUMMARY.md'}")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
