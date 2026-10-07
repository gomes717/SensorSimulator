"""The app's persisted choices and run-wide modes, held in one place.

Profiles, the slot → patient/sensor record, the active person/sensor, simulation
speed, range thresholds, the graph's view window, and the two mutually exclusive
display modes (Model Only, CGMS Only). Pure state plus the little logic that is
only about it (which person runs on which slot, saving to data/); nothing here
knows about windows, graphs or the board.
"""

from __future__ import annotations

from collections.abc import Iterable

from models import app_settings, board_layout, cambridge, profile_store, user_store
from models import sensors as sensor_defaults
from models.types import ModelId, PersonProfile, SensorId, SensorProfile, User


class AppState:
    """Profiles, board layout, settings and modes — loaded once, saved on change."""

    def __init__(self) -> None:
        self.person_profiles, self.sensor_profiles = profile_store.load()
        self._seed_default_profiles()
        # The users (see docs/adr/0006): migrated from the profiles above on the first run.
        self.users: list[User] = user_store.load_or_migrate()
        # slot -> (person, sensor) for the multi-sensor board (data/board_layout.json).
        self.board_layout = board_layout.load()
        # The Configuration window's Person/Sensor combos are gone, so the first
        # saved profile is active until the user picks another in the editors.
        self.active_person: PersonProfile | None = (
            self.person_profiles[0] if self.person_profiles else None
        )
        self.active_sensor: SensorProfile | None = (
            self.sensor_profiles[0] if self.sensor_profiles else None
        )
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
    # Profiles
    # ------------------------------------------------------------------

    def _seed_default_profiles(self) -> None:
        """First run (no saved profiles yet): add one default person/sensor.

        Without this, Model Only mode and the selector bar start empty and
        show nothing until the user manually opens Configure and builds a
        profile from scratch — seeding one makes the graph show data
        immediately.
        """
        changed = False
        if not self.person_profiles:
            self.person_profiles.append(
                PersonProfile(
                    name="Sample Patient",
                    model_id=ModelId.CAMBRIDGE,
                    params=cambridge.default_params(),
                )
            )
            changed = True
        if not self.sensor_profiles:
            self.sensor_profiles.append(
                SensorProfile(
                    name="Sample Sensor",
                    sensor_id=SensorId.IDEAL,
                    params=sensor_defaults.ideal_default_params(),
                )
            )
            changed = True
        if changed:
            self.save_profiles()

    def save_profiles(self) -> None:
        """Persist the person and sensor profiles."""
        profile_store.save(self.person_profiles, self.sensor_profiles)

    def save_users(self) -> None:
        """Persist the users."""
        user_store.save(self.users)

    def person_by_name(self, name: str | None) -> PersonProfile | None:
        """The saved person called *name*, if any."""
        if not name:
            return None
        return next((p for p in self.person_profiles if p.name == name), None)

    def sensor_by_name(self, name: str | None) -> SensorProfile | None:
        """The saved sensor called *name*, if any."""
        if not name:
            return None
        return next((s for s in self.sensor_profiles if s.name == name), None)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def board_plan(
        self, live_slots: Iterable[int]
    ) -> dict[int, tuple[PersonProfile, SensorProfile | None]]:
        """slot -> (person, sensor) the app puts on each live board slot at Start.

        This is the ONE answer to "what does each sensor run" for a board run: Start
        writes exactly these profiles (model, parameters, meal and exercise
        schedules) to the board, and the local expected model is built from exactly
        these, so the two cannot disagree. A slot that replays a CSV is left out — its
        recording is uploaded separately and there is no model behind it — and so is a
        slot with no patient assigned (nothing to write, nothing to model). Only when
        no slot has ever been assigned does the active patient stand in, as in
        :meth:`engine_slots`. A slot whose sensor was never recorded gets no sensor
        profile: the board keeps the noise model it already has.
        """
        nothing_assigned = self.board_layout.assigned_count() == 0
        plan: dict[int, tuple[PersonProfile, SensorProfile | None]] = {}
        for slot in sorted(set(live_slots)):
            if not 0 <= slot < len(self.board_layout.slots):
                continue
            assignment = self.board_layout.slots[slot]
            person = self.person_by_name(assignment.person)
            sensor = self.sensor_by_name(assignment.sensor)
            if nothing_assigned:
                person, sensor = self.active_person, self.active_sensor
            if person is None or getattr(person, "data_source", "model") == "csv":
                continue
            plan[slot] = (person, sensor)
        return plan

    def engine_slots(self) -> dict[int, PersonProfile]:
        """slot -> profile for the engine pool. Per-slot when a multi-sensor board
        layout has assignments (and not in Model Only); otherwise a single
        slot 0 for the active person."""
        if not self.model_only:
            assigned = {
                i: self.person_by_name(s.person)
                for i, s in enumerate(self.board_layout.slots)
                if s.person
            }
            assigned = {i: p for i, p in assigned.items() if p is not None}
            if assigned:
                return assigned
        if self.active_person is not None:
            return {0: self.active_person}
        return {}

    def record_slot_assignment(
        self, slot: int | None, *, person: str | None = None, sensor: str | None = None
    ) -> bool:
        """Remember which profiles a just-sent config put on *slot*.

        The slot -> profile map still drives the per-slot engines, the sensor
        tabs' names and the CSV-vs-model view, but there is no Board Layout
        screen to edit it any more: it is now a record of what the individual
        "Send to Board" actions actually pushed. Returns False for a slot that
        does not exist (nothing recorded).
        """
        if slot is None or not 0 <= slot < board_layout.MAX_SLOTS:
            return False
        assignment = self.board_layout.slots[slot]
        if person is not None:
            assignment.person = person
        if sensor is not None:
            assignment.sensor = sensor
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
