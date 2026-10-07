"""The 24 h curves behind the Food and Exercise pages: what a user's recurring daily schedule
amounts to, minute by minute.

Both schedules recur every simulated day at the same time of day, and an event that runs past
midnight carries on at the start of the day (the firmware's ``in_daily_window`` does the same).
A meal is drawn spread evenly over its duration as grams per minute — what a rate-fed model
(Cambridge, UVA/Padova) is fed; an impulse-fed model (Roy & Parker, Deichmann) takes the whole
meal at its start time, which a one-minute-resolution graph cannot show apart, so the page says so.
"""

from __future__ import annotations

from models.types import ExerciseEvent, FoodEvent

MINUTES_PER_DAY = 1440
MAX_EVENTS = 32  # what the board's food and exercise lists each hold


def food_rate_series(events: list[FoodEvent]) -> list[float]:
    """Carbohydrate rate in g/min at each minute of the day (overlapping meals add up)."""
    rate = [0.0] * MINUTES_PER_DAY
    for event in events:
        duration = max(event.duration_min, 1)
        per_minute = event.carbs_g / duration
        for offset in range(duration):
            rate[(event.time_of_day_min + offset) % MINUTES_PER_DAY] += per_minute
    return rate


def exercise_series(events: list[ExerciseEvent]) -> list[float]:
    """Exercise intensity (%) at each minute of the day. Where bouts overlap the stronger one
    counts — the board feeds its model the matching bout, not their sum."""
    level = [0.0] * MINUTES_PER_DAY
    for event in events:
        for offset in range(max(event.duration_min, 1)):
            minute = (event.time_of_day_min + offset) % MINUTES_PER_DAY
            level[minute] = max(level[minute], event.intensity_pct)
    return level
