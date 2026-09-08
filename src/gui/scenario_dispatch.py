"""Interpret one scenario step — ``(kind, args) -> log line`` — by driving the app.

An orchestration adapter (issue 18): it deliberately reaches into MainWindow's
high-level actions (speed / run-state / person selection / comm profile) and
:class:`InstantEvents`, the same way a scripted operator would click through the
UI. ``models.scenario.ScenarioRunner`` owns the *timing*; this owns the
*vocabulary*, in one place instead of a 60-line ``if/elif`` chain on the god
object.
"""

from __future__ import annotations


class ScenarioDispatch:
    def __init__(self, host, events) -> None:
        self._host = host
        self._events = events
        self._handlers = {
            "speed": self._do_speed,
            "run_state": self._do_run_state,
            "person": self._do_person,
            "data_source": self._do_person,
            "comm_profile": self._do_comm_profile,
            "insert_food": self._do_insert_food,
            "insert_exercise": self._do_insert_exercise,
            "inject_fault": self._do_inject_fault,
        }

    def dispatch(self, kind: str, args: dict) -> str:
        """Run one scenario action on the GUI thread; return a one-line log entry."""
        handler = self._handlers.get(kind)
        return handler(args) if handler else f"(unknown action {kind!r})"

    # ------------------------------------------------------------------

    def _do_speed(self, args: dict) -> str:
        self._host._on_speed_changed(float(args.get("multiplier", 1)))
        return f"speed → x{int(self._host._speed_mult)}"

    def _do_run_state(self, args: dict) -> str:
        h = self._host
        state = str(args.get("state", "")).lower()
        if state == "start" and h._run_state == "stopped":
            h._start_run()
        elif state == "stop":
            h._on_stop_clicked()
        elif state in ("pause", "resume"):
            h._on_start_pause_clicked()
        return f"run_state → {state}"

    def _do_person(self, args: dict) -> str:
        h = self._host
        name = args.get("person")
        match = next((p for p in h._person_profiles if p.name == name), None)
        if match is not None:
            h._on_person_selected(match)
            h._controller.notify_profiles_changed()
        h._broadcast_data_source()
        src = getattr(h._active_person, "data_source", "model") if h._active_person else "?"
        return f"person → {name} ({src})"

    def _do_comm_profile(self, args: dict) -> str:
        dexcom = str(args.get("profile", "sig")).lower() == "dexcom"
        self._host._controller.set_comm_profile_display(dexcom)
        self._host._on_comm_profile_toggled(dexcom)
        return f"comm_profile → {'dexcom' if dexcom else 'sig'}"

    def _do_insert_food(self, args: dict) -> str:
        carbs_g = float(args.get("carbs_g", 50))
        duration_min = int(args.get("duration_min", 15))
        slot = args.get("slot")
        self._events.inject_food(slot, duration_min, carbs_g)
        tail = f" @slot {slot}" if slot is not None else ""
        return f"insert_food {carbs_g:g} g / {duration_min} min{tail}"

    def _do_insert_exercise(self, args: dict) -> str:
        duration_min = int(args.get("duration_min", 30))
        intensity_pct = float(args.get("intensity_pct", 50))
        slot = args.get("slot")
        self._events.inject_exercise(slot, duration_min, intensity_pct)
        tail = f" @slot {slot}" if slot is not None else ""
        return f"insert_exercise {duration_min} min / {intensity_pct:g} %{tail}"

    def _do_inject_fault(self, args: dict) -> str:
        fault = str(args.get("fault", "pisa"))
        duration_min = int(args.get("duration_min", 10))
        depth_frac = float(args.get("depth_frac", 0.4))
        self._events.inject_fault(fault, (duration_min, depth_frac), slot=args.get("slot"))
        return f"inject_fault {fault} {int(depth_frac * 100)} % / {duration_min} min"
