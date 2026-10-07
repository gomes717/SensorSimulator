"""The app flows the overnight test drives (``scripts/overnight_users.py``).

Everything goes through the real windows — the Users window, the profile screen and its pages, the
Bluetooth window, the sensor tabs and their Commands panel, the run buttons — with the modal
questions answered by the harness (``Flows.answers``) instead of a message box. Each flow records
what it checked in a :class:`Recorder`; a flow that raises is recorded as a failed check and the
night goes on.
"""

from __future__ import annotations

import contextlib
import copy
import random
import time
import traceback
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import QTime
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from gui import run_controller
from gui.avatar_picker import AVATAR_DIR, AvatarDialog, avatar_files
from gui.user_profile_window import UserProfileWindow
from gui.user_reader import UserReader
from gui.users_window import UsersWindow
from models import board_layout, dexcom_csv, user_board, user_store
from models.types import ModelId, SensorId, User

DATASET = Path(__file__).resolve().parent.parent / "dataset"


def wait_until(predicate: Callable[[], bool], timeout_s: float, step_ms: int = 100) -> bool:
    """Pump the Qt event loop until *predicate* is true; False on timeout."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        QTest.qWait(step_ms)
    return predicate()


# ---- recording ------------------------------------------------------------------------------


@dataclass
class Check:
    area: str
    name: str
    ok: bool
    detail: str = ""


class Recorder:
    """The checks of one cycle, echoed as they happen; a failure also keeps screenshots."""

    def __init__(self, shots_dir: Path | None = None) -> None:
        self.checks: list[Check] = []
        self.shots_dir = shots_dir
        self.notes: list[str] = []

    def check(self, area: str, name: str, ok: bool, detail: str = "") -> bool:
        ok = bool(ok)
        self.checks.append(Check(area, name, ok, str(detail)))
        print(
            f"    {'PASS' if ok else 'FAIL'}  [{area}] {name}" + (f"  — {detail}" if detail else "")
        )
        if not ok:
            self._shoot(f"{area}-{name}")
        return ok

    def note(self, text: str) -> None:
        self.notes.append(text)
        print(f"    note: {text}")

    @contextlib.contextmanager
    def guard(self, area: str, name: str) -> Iterator[None]:
        """Run a flow; an exception is a failed check (with the traceback), not the end."""
        try:
            yield
        except Exception as exc:  # the flow is lost, the night is not
            tb = traceback.extract_tb(exc.__traceback__)[-1]
            self.check(
                area,
                name,
                False,
                f"{type(exc).__name__}: {exc} ({Path(tb.filename).name}:{tb.lineno})",
            )

    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]

    def _shoot(self, label: str) -> None:
        if self.shots_dir is None:
            return
        self.shots_dir.mkdir(parents=True, exist_ok=True)
        safe = "".join(ch if ch.isalnum() else "_" for ch in label)[:60]
        with contextlib.suppress(Exception):
            for i, widget in enumerate(w for w in QApplication.topLevelWidgets() if w.isVisible()):
                widget.grab().save(str(self.shots_dir / f"{len(self.checks):03d}-{safe}-{i}.png"))


# ---- what a user is built from ----------------------------------------------------------------


@dataclass
class UserSpec:
    """Everything one created user is given, chosen up front so a cycle is reproducible."""

    height: int = 172
    weight: float = 70.0
    avatar: int = 0
    mode: str = "model"
    model_id: ModelId = ModelId.CAMBRIDGE
    sensor_id: SensorId = SensorId.IDEAL
    jitter: dict[str, float] = field(default_factory=dict)  # model param -> factor
    meals: list[tuple[int, float, int]] = field(default_factory=list)  # (minute, carbs, spread)
    exercises: list[tuple[int, int, float]] = field(default_factory=list)  # (minute, dur, pct)
    csv: tuple[Path, datetime] | None = None


def random_csv_window(rng: random.Random) -> tuple[Path, datetime]:
    """A Dexcom file from the dataset and a start inside it (so the window has readings)."""
    files = sorted(DATASET.glob("Dexcom_*.csv"))
    path = rng.choice(files)
    rows = dexcom_csv.read_egv(path)
    days = max(1, (rows[-1][0] - rows[0][0]).days - 1)
    return path, rows[0][0] + timedelta(days=rng.randrange(days))


def random_spec(rng: random.Random, mode: str) -> UserSpec:
    """A varied but safe user: 1-2 lightly jittered parameters, 2-3 meals, 1-2 bouts."""
    spec = UserSpec(
        height=rng.randint(150, 195),
        weight=round(rng.uniform(55, 95), 1),
        avatar=rng.randrange(6),
        mode=mode,
        model_id=rng.choice(list(ModelId)),
        sensor_id=rng.choice(list(SensorId)),
    )
    spec.meals = sorted(
        (rng.randrange(60, 1380, 30), float(rng.choice((40, 50, 60, 70))), rng.choice((15, 30, 45)))
        for _ in range(rng.randint(2, 3))
    )
    spec.exercises = sorted(
        (rng.randrange(360, 1200, 60), rng.choice((20, 30, 45)), float(rng.choice((20, 30, 40))))
        for _ in range(rng.randint(1, 2))
    )
    if mode == "csv":
        spec.csv = random_csv_window(rng)
    return spec


# ---- the flows -------------------------------------------------------------------------------
class Flows:
    """Drives one MainWindow. *tick_of(slot)* returns the board's latest console tick (or None)."""

    def __init__(self, window, rec: Recorder, rng: random.Random, has_board: bool) -> None:
        self.w = window
        self.rec = rec
        self.rng = rng
        self.has_board = has_board
        # What the harness answers when the app asks (see _hook_screen / users_window).
        self.answers: dict = {
            "unsaved": "discard",
            "save_before_send": "save",
            "differs": "overwrite",
            "delete": True,
            "slot": 0,
        }
        self.asked_before_send = 0
        self.opened: list[tuple[User, bool]] = []
        self.restarts = 0
        self._hooked_window: int | None = None
        original_restart = self.w.sim.restart

        def counted_restart() -> None:
            self.restarts += 1
            original_restart()

        self.w.sim.restart = counted_restart

    # -- windows --------------------------------------------------------------------------------

    def users_window(self) -> UsersWindow:
        self.w.windows.ensure("users")
        window = self.w.windows.get("users")
        assert isinstance(window, UsersWindow)
        if self._hooked_window != id(window):
            self._hooked_window = id(window)
            window._ask_differs = lambda _outcome: self.answers["differs"]
            window._confirm_delete = lambda _user: self.answers["delete"]
            window.open_requested.connect(lambda user, draft: self.opened.append((user, draft)))
        window.show()
        QTest.qWait(50)
        return window

    def _hook_screen(self, screen: UserProfileWindow) -> UserProfileWindow:
        def ask_before_send() -> str:
            self.asked_before_send += 1
            return self.answers["save_before_send"]

        screen._dialogs.ask_unsaved = lambda: self.answers["unsaved"]
        screen._dialogs.ask_save_before_send = ask_before_send
        screen._dialogs.choose_sensor = self._choose_sensor
        return screen

    def _choose_sensor(self, sessions: list, _describe):
        wanted = self.answers["slot"]
        return next((s for s in sessions if s.slot_index == wanted), None)

    def screen_for(self, user: User) -> UserProfileWindow:
        screens = [s for s in self.w.findChildren(UserProfileWindow) if s.user.id == user.id]
        assert screens, f"no profile screen is open for {user.name!r}"
        return self._hook_screen(screens[-1])

    def open_user(self, user: User, draft: bool = False) -> UserProfileWindow:
        self.users_window().open_requested.emit(user, draft)
        QTest.qWait(100)
        return self.screen_for(user)

    def close_screen(self, screen: UserProfileWindow, answer: str = "discard") -> None:
        self.answers["unsaved"] = answer
        screen.close()
        QTest.qWait(50)

    def saved(self, name: str) -> User | None:
        return next((u for u in self.w.state.users if u.name == name), None)

    @staticmethod
    def menu_state(screen: UserProfileWindow) -> dict[str, bool]:
        from PyQt6.QtCore import Qt

        out = {}
        for i in range(screen.menu.count()):
            item = screen.menu.item(i)
            if item is not None:
                out[item.text()] = bool(item.flags() & Qt.ItemFlag.ItemIsEnabled)
        return out

    # -- creating and editing a user -----------------------------------------------------------
    def create_user(self, name: str, spec: UserSpec, *, area: str = "users") -> User | None:
        """+ New, then fill every page, preview it, save it, and check what was stored."""
        rec = self.rec
        uw = self.users_window()
        before = len(self.w.state.users)
        self.opened.clear()
        uw.new_user()
        QTest.qWait(100)
        rec.check(
            area,
            f"+ New adds a user and opens its screen [{name}]",
            len(self.w.state.users) == before + 1 and bool(self.opened),
        )
        if not self.opened:
            return None
        screen = self.screen_for(self.opened[-1][0])
        page = screen.profile_page

        page.name_edit.setText(name)
        page.name_edit.textEdited.emit(name)
        page.height_spin.setValue(spec.height)
        page.weight_spin.setValue(spec.weight)
        (page.csv_radio if spec.mode == "csv" else page.model_radio).setChecked(True)
        rec.check(area, f"the header follows the name [{name}]", screen.name_label.text() == name)
        menu = self.menu_state(screen)
        gated = (
            menu.get("CSV") and not any(menu.get(k) for k in ("Food", "Exercise", "Model"))
            if spec.mode == "csv"
            else not menu.get("CSV") and all(menu.get(k) for k in ("Food", "Exercise", "Model"))
        )
        rec.check(area, f"the menu is gated by the {spec.mode} mode [{name}]", gated, str(menu))

        mp = screen.model_page
        mp.model_combo.setCurrentIndex(mp.model_combo.findData(spec.model_id))
        mp.sensor_combo.setCurrentIndex(mp.sensor_combo.findData(spec.sensor_id))
        for param, factor in spec.jitter.items():
            spin = mp.model_form.spins.get(param)
            if spin is not None:
                spin.setValue(min(spin.maximum(), max(spin.minimum(), spin.value() * factor)))
        for minute, carbs, spread in spec.meals:
            self._add_event(screen.pages["food"], minute, carbs, spread)
        for minute, duration, pct in spec.exercises:
            self._add_event(screen.pages["exercise"], minute, duration, pct)
        rec.check(
            area,
            f"the schedules hold what was added [{name}]",
            len(screen.user.food_events) == len(spec.meals)
            and len(screen.user.exercise_events) == len(spec.exercises),
            f"{len(screen.user.food_events)} meals, {len(screen.user.exercise_events)} bouts",
        )
        if len(spec.meals) >= 3:  # Remove selected
            food = screen.pages["food"]
            food.table.selectRow(0)
            food.remove_button.click()
            rec.check(
                area,
                f"a meal can be removed [{name}]",
                len(screen.user.food_events) == len(spec.meals) - 1,
            )
        if spec.csv is not None:
            path, start = spec.csv
            screen.pages["csv"]._apply_picked(str(path), start)
            track = screen.user.csv
            rec.check(
                area,
                f"a CSV window is copied into the user [{name}]",
                track is not None and len(track.samples) == 288 and track.interval_s == 300,
                f"{path.name} {start:%Y-%m-%d}: {track and len(track.samples)} samples",
            )
        self.pick_avatar(screen, spec.avatar)

        preview = screen.preview()
        minutes, glucose = preview.minutes, preview.glucose
        want = 288 if spec.mode == "csv" else 1440
        rec.check(
            area,
            f"Preview draws 24 h without the sensor noise [{name}]",
            len(glucose) == want and (max(glucose) - min(glucose) > 1.0 or spec.mode == "model"),
            f"{len(minutes)} points, {min(glucose):.0f}-{max(glucose):.0f} mg/dL"
            if glucose
            else "empty",
        )
        preview.close()

        rec.check(area, f"the screen shows unsaved changes [{name}]", screen.is_dirty)
        saved_ok = screen.save()
        rec.check(area, f"Save succeeds [{name}]", saved_ok, screen.error.text())
        on_disk = next((u for u in user_store.load() if u.name == name), None)
        rec.check(
            area,
            f"the saved user is on disk with its data [{name}]",
            on_disk is not None
            and on_disk.mode == spec.mode
            and on_disk.model_id == spec.model_id
            and on_disk.sensor_id == spec.sensor_id
            and (spec.csv is None or (on_disk.csv is not None and len(on_disk.csv.samples) == 288)),
        )
        self.close_screen(screen)
        return self.saved(name)

    @staticmethod
    def _add_event(page, minute: int, first: float, second: float) -> None:
        page.time_edit.setTime(QTime(minute // 60, minute % 60))
        page.first_spin.setValue(first)
        page.second_spin.setValue(second)
        page.add_button.click()

    def pick_avatar(self, screen: UserProfileWindow, index: int) -> Path | None:
        """Choose avatar *index* in the real gallery dialog and hand it to the screen."""
        files = avatar_files(AVATAR_DIR)
        self.rec.check(
            "avatar",
            "the gallery lists the bundled avatars",
            len(files) >= 6,
            f"{len(files)} files",
        )
        if not files:
            return None
        dialog = AvatarDialog()
        dialog.list.setCurrentRow(index % len(files))
        dialog.accept()
        screen._picture_chosen(dialog.selected)
        return dialog.selected

    def edit_weight(self, screen: UserProfileWindow, delta: float) -> float:
        """Change the weight on screen without saving (it is also the model's BW)."""
        spin = screen.profile_page.weight_spin
        spin.setValue(min(spin.maximum(), spin.value() + delta))
        return spin.value()

    # -- the board -----------------------------------------------------------------------------
    def sessions_by_slot(self) -> dict:
        bt = self.w.windows.bluetooth
        if bt is None:
            return {}
        return {
            s.slot_index: s
            for s in bt.sessions().values()
            if s.is_live and s.slot_index is not None
        }

    def board_read(self, slot: int, timeout_s: float = 120.0):
        """The slot's user as the board reports it (a fresh reader, so the app's is not used)."""
        session = self.sessions_by_slot().get(slot)
        if session is None:
            return None, f"sensor {slot + 1} is not connected"
        box: list = []
        reader = UserReader()
        if not reader.read(session, lambda reading, error: box.append((reading, error))):
            return None, "the reader was busy"
        if not wait_until(lambda: bool(box), timeout_s):
            return None, "the read timed out"
        return box[0]

    def matches_board(self, user: User, slot: int) -> tuple[bool, str]:
        """True when the board's slot holds *user* (compared as a read-from would)."""
        reading, error = self.board_read(slot)
        if reading is None:
            return False, error
        outcome = user_board.classify([user], reading)
        if isinstance(outcome, user_board.Matches):
            return True, f"{reading.name!r} matches"
        if isinstance(outcome, user_board.Differs):
            return False, f"differs in {outcome.differences}" + self._csv_diff(user, reading)
        return False, f"the board holds {reading.name!r}, expected {user.name!r}"

    @staticmethod
    def _csv_diff(user: User, reading) -> str:
        """How a CSV window that came back differs from the one sent (for the report)."""
        sent, got = user.csv, reading.csv
        if sent is None or got is None:
            return f"; window sent: {sent is not None}, board's: {got is not None}"
        first = next(
            (i for i, (a, b) in enumerate(zip(sent.samples, got.samples, strict=False)) if a != b),
            None,
        )
        return (
            f"; samples {len(sent.samples)} sent / {len(got.samples)} back, first difference at "
            f"{first}, interval {sent.interval_s}/{got.interval_s}s, meals "
            f"{len(sent.foodlog)}/{len(got.foodlog)}, start {sent.start_iso}/{got.start_iso}"
        )

    def send_from_screen(
        self,
        user: User,
        slot: int,
        *,
        unsaved_delta: float = 0.0,
        choice: str = "save",
        timeout_s: float = 300.0,
    ) -> tuple[UserProfileWindow, bool, str]:
        """Send to…: choose sensor *slot*; with *unsaved_delta* the user is edited first, and the
        'save before send' question is answered *choice* ('save' or 'send')."""
        screen = self.open_user(user)
        if unsaved_delta:
            self.edit_weight(screen, unsaved_delta)
        self.answers["slot"] = slot
        self.answers["save_before_send"] = choice
        self.asked_before_send = 0
        screen.send()
        wait_until(lambda: not screen.error.text().startswith("Sending"), timeout_s)
        ok = "2e7d32" in screen.error.styleSheet()
        return screen, ok, screen.error.text()

    def read_from(self, slot: int, timeout_s: float = 150.0) -> tuple[str, str]:
        """+ Read from… the slot's sensor. Returns (outcome, status): 'opened' (a saved user),
        'draft', or 'none'; the Users window's own status line is the detail."""
        uw = self.users_window()
        session = self.sessions_by_slot().get(slot)
        if session is None:
            return "none", f"sensor {slot + 1} is not connected"
        self.opened.clear()
        uw.read_from(session)
        wait_until(
            lambda: (
                bool(self.opened)
                or (uw.read_button.isEnabled() and not uw.status.text().startswith("Reading"))
            ),
            timeout_s,
        )
        QTest.qWait(150)
        if self.opened:
            return ("draft" if self.opened[-1][1] else "opened"), uw.status.text()
        return "none", uw.status.text()

    def delete_user(self, name: str) -> bool:
        uw = self.users_window()
        user = self.saved(name)
        if user is None:
            return False
        uw.list.setCurrentRow(self.w.state.users.index(user))
        self.answers["delete"] = True
        uw.delete_selected()
        QTest.qWait(100)
        return True

    # -- bluetooth -----------------------------------------------------------------------------
    def scan(self, timeout_s: float = 60.0) -> dict[int, tuple[str, str]]:
        """Scan from the Bluetooth window; {slot: (address, advertised name)} of every identity."""
        bt = self.w.windows.ensure_bluetooth()
        bt._start_scan()
        wait_until(lambda: bt._rescan_btn.isEnabled(), timeout_s)
        found = {}
        for address, advertised in bt._advertised.items():
            slot = board_layout.slot_of(advertised or "")
            if slot is not None:
                found[slot] = (address, advertised)
        return found

    def connect_sensors(self, slots: list[int], attempts: int = 4) -> list[int]:
        """Connect every slot in *slots* that is not live, the way the Connect button does,
        retrying from a fresh scan; returns the slots that are live afterwards."""
        bt = self.w.windows.ensure_bluetooth()
        for _attempt in range(attempts):
            missing = [s for s in slots if s not in self.sessions_by_slot()]
            if not missing:
                break
            found = self.scan()
            for slot in missing:
                if slot not in found:
                    continue
                address, advertised = found[slot]
                if address in bt._sessions:
                    bt._stop_session(address)
                bt._open_session(address, advertised)
                wait_until(lambda s=slot: s in self.sessions_by_slot(), 60.0)
            QTest.qWait(1500)
        return [s for s in slots if s in self.sessions_by_slot()]

    def close_tab(self, slot: int) -> bool:
        """The tab's close button for *slot*'s sensor: the tab goes and the sensor disconnects."""
        session = self.sessions_by_slot().get(slot)
        if session is None:
            return False
        key = session.user_id
        self.w.sensors.on_tab_close_requested(key)
        return wait_until(
            lambda: slot not in self.sessions_by_slot() and key not in self.w.tabs.tab_keys(), 20.0
        )

    # -- the run -------------------------------------------------------------------------------
    def start_run(self, speed: float, timeout_s: float = 240.0) -> bool:
        """Set the speed and press Start; True once the run is RUNNING."""
        self.w._controller.speed_change_requested.emit(float(speed))
        QTest.qWait(300)
        self.w._run.start()
        return wait_until(lambda: self.w._run.state == run_controller.RUNNING, timeout_s)

    def run_state(self) -> str:
        return self.w._run.state

    def instant(self, slot: int, kind: str, args: tuple) -> bool:
        """Insert food / exercise / PISA through the slot's own Commands panel."""
        session = self.sessions_by_slot().get(slot)
        if session is None:
            return False
        page = self.w.sensors.page_of_user(session.user_id)
        commands = page.commands
        if kind == "food":
            duration, carbs = args
            return commands.send_food(carbs, int(duration))
        if kind == "exercise":
            duration, intensity = args
            return commands.send_exercise(int(duration), intensity)
        duration, depth = args
        return commands.send_pisa(int(duration), depth * 100.0)

    def tab_key(self, slot: int) -> str | None:
        """The key of the tab showing sensor *slot*, if it has one."""
        for key in self.w.tabs.tab_keys():
            if self.w.sensors._directory.slot_of_user(key) == slot:
                return key
        return None

    def tab_shows_picture(self, slot: int) -> bool | None:
        """Whether sensor *slot*'s tab header shows the user's picture; None without a tab."""
        key = self.tab_key(slot)
        header = None if key is None else self.w.tabs.header(key)
        return None if header is None else header.shows_picture

    def snapshot_user(self, name: str) -> User | None:
        user = self.saved(name)
        return copy.deepcopy(user) if user is not None else None
