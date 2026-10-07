"""The app's persisted choices and run-wide modes, held in one place.

The users, the slot -> user record, the active user, simulation speed, range thresholds, the graph's
view window, and the two mutually exclusive display modes (Model Only, CGMS Only). Pure state plus
the little logic that is only about it (which user runs on which slot, saving to data/); nothing
here knows about windows, graphs or the board.

What the simulation engine and the board push run on are PersonProfile / SensorProfile, the engine's
input types, derived from a user on demand (models.user_sim), so a user is the only thing that is
edited and saved.
"""

from __future__ import annotations

from collections.abc import Iterable

from models import app_settings, board_layout, user_sim, user_store
from models.types import PersonProfile, SensorProfile, User

DEFAULT_USER_NAME = "Sample Patient"


class AppState:
    """Users, board layout, settings and modes — loaded once, saved on change."""

    def __init__(self) -> None:
        # The users (docs/adr/0006); migrated from the old profiles on the first run.
        self.users: list[User] = user_store.load_or_migrate()
        self._seed_default_user()
        # slot -> user name for the multi-sensor board (data/board_layout.json).
        self.board_layout = board_layout.load()
        # Which user Model Only runs (and a lone board runs when nothing is assigned): the
        # last one opened or saved on the profile screen, else the first.
        self.active_user: User | None = self.users[0] if self.users else None
        # Continuous sim-speed multiplier x1..x1000; applied to dt_min + sent to the board.
        self.speed_mult = float(app_settings.load_pref("speed_mult", 1.0))
        # Rolling view: show only the last N sim-seconds (0 = entire run).
        self.view_window_s = float(app_settings.load_pref("view_window_s", 3600.0))
        # Range thresholds (mg/dL) for the graph bands + the tab alerts + metrics.
        self.thresholds = app_settings.load()
        # Draw each received sample as a dot on the glucose graphs.
        self.show_points = bool(app_settings.load_pref("show_points", False))
        self.model_only = False
        self.cgms_only = False

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def _seed_default_user(self) -> None:
        """First run (no users yet): add one default user.

        Without this, Model Only mode starts empty and shows nothing until a user is
        built from scratch - seeding one makes the graph show data immediately.
        """
        if not self.users:
            self.users.append(user_store.new_user(DEFAULT_USER_NAME))
            self.save_users()

    def save_users(self) -> None:
        """Persist the users."""
        user_store.save(self.users)

    def user_by_name(self, name: str | None) -> User | None:
        """The saved user called *name*, if any (names are unique)."""
        if not name:
            return None
        return next((u for u in self.users if u.name == name), None)

    def forget_user(self, user: User) -> None:
        """Take a deleted *user* off any slot that was recorded as running it."""
        for assignment in self.board_layout.slots:
            if assignment.person == user.name:
                assignment.person = None
        if self.active_user is user:
            self.active_user = self.users[0] if self.users else None

    def rename_user_in_layout(self, old_name: str, new_name: str) -> None:
        """Follow a renamed user: the slots recorded under *old_name* now run *new_name*."""
        for assignment in self.board_layout.slots:
            if assignment.person == old_name:
                assignment.person = new_name

    @property
    def active_person(self) -> PersonProfile | None:
        """The engine profile of the active user (None when there are no users)."""
        return user_sim.person_profile_of(self.active_user) if self.active_user else None

    @property
    def active_sensor(self) -> SensorProfile | None:
        """The sensor-noise profile of the active user."""
        return user_sim.sensor_profile_of(self.active_user) if self.active_user else None

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def board_plan(
        self, live_slots: Iterable[int]
    ) -> dict[int, tuple[PersonProfile, SensorProfile | None]]:
        """slot -> (person, sensor) profiles the app puts on each live board slot at Start.

        This is the ONE answer to "what does each sensor run" for a board run: Start writes
        exactly these (model, parameters, meal and exercise schedules) to the board, and the local
        expected model is built from exactly these, so the two cannot disagree. A slot whose user
        replays a CSV is left out - its recording is uploaded separately and there is no model
        behind it - and so is a slot with no user assigned (nothing to write, nothing to model).
        Only when no slot has ever been assigned does the active user stand in, as in
        :meth:`engine_slots`.
        """
        nothing_assigned = self.board_layout.assigned_count() == 0
        plan: dict[int, tuple[PersonProfile, SensorProfile | None]] = {}
        for slot in sorted(set(live_slots)):
            if not 0 <= slot < len(self.board_layout.slots):
                continue
            user = (
                self.active_user
                if nothing_assigned
                else self.user_by_name(self.board_layout.slots[slot].person)
            )
            if user is None or user.mode == "csv":
                continue
            plan[slot] = (user_sim.person_profile_of(user), user_sim.sensor_profile_of(user))
        return plan

    def engine_slots(self) -> dict[int, PersonProfile]:
        """slot -> profile for the engine pool. Per-slot when a multi-sensor board
        layout has assignments (and not in Model Only); otherwise a single
        slot 0 for the active user."""
        if not self.model_only:
            assigned = {
                i: self.user_by_name(s.person)
                for i, s in enumerate(self.board_layout.slots)
                if s.person
            }
            profiles = {
                i: user_sim.person_profile_of(u) for i, u in assigned.items() if u is not None
            }
            if profiles:
                return profiles
        person = self.active_person
        return {0: person} if person is not None else {}

    def record_slot_assignment(self, slot: int | None, *, person: str | None = None) -> bool:
        """Remember which user a just-sent configuration put on *slot*.

        The slot -> user map still drives the per-slot engines, the sensor tabs' names and
        the CSV-vs-model view; it is a record of what Send to... actually pushed. Returns False
        for a slot that does not exist (nothing recorded).
        """
        if slot is None or not 0 <= slot < board_layout.MAX_SLOTS:
            return False
        if person is not None:
            self.board_layout.slots[slot].person = person
        return True

    def save_layout(self) -> None:
        """Persist the slot assignments."""
        board_layout.save(self.board_layout)

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def set_speed(self, multiplier: float) -> float:
        """Clamp, remember and persist the simulation-speed multiplier; returns it."""
        self.speed_mult = max(1.0, min(1000.0, float(multiplier)))
        app_settings.save_pref("speed_mult", self.speed_mult)
        return self.speed_mult

    def set_show_points(self, show: bool) -> None:
        """Remember and persist whether the graphs draw a dot per sample."""
        self.show_points = bool(show)
        app_settings.save_pref("show_points", self.show_points)

    def reload_thresholds(self) -> dict:
        """Re-read the range thresholds after a Configuration-window save."""
        self.thresholds = app_settings.load()
        return self.thresholds

    def reload_view_window(self) -> float:
        """Re-read the graph time-window preference."""
        self.view_window_s = float(app_settings.load_pref("view_window_s", 3600.0))
        return self.view_window_s
