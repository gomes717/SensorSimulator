"""Custom 128-bit BLE UUIDs for the simulator config service.

These literal UUIDs must match the firmware's src/config_service.c exactly —
both sides just hardcode the same values rather than looking them up any
other way.
"""

CONFIG_SERVICE_UUID = "5b2c0001-0d6d-4a3a-8c1e-3f9b6e7a1a00"
# Read + write: reading returns the currently applied config in the same
# shape the write accepts, so the app can confirm/recover what's on the board.
PERSON_CONFIG_UUID = "5b2c0002-0d6d-4a3a-8c1e-3f9b6e7a1a00"
SENSOR_CONFIG_UUID = "5b2c0003-0d6d-4a3a-8c1e-3f9b6e7a1a00"
MODE_CONFIG_UUID = "5b2c0004-0d6d-4a3a-8c1e-3f9b6e7a1a00"
# Write-only: each write appends one event (or clears all, see protocol.py's
# sentinel encoding) — reading the full list back uses the *_EVENTS_READBACK
# characteristics below instead, since "add one" and "read all" are different
# shapes.
FOOD_EVENT_UUID = "5b2c0005-0d6d-4a3a-8c1e-3f9b6e7a1a00"
EXERCISE_EVENT_UUID = "5b2c0006-0d6d-4a3a-8c1e-3f9b6e7a1a00"
# Notify-only: the board reports what it is actually feeding its model right now
# (post food/exercise-schedule evaluation), so the app can plot ground truth from
# the MCU itself rather than just the app's own copy of the schedule.
FOOD_EXERCISE_STATUS_UUID = "5b2c0007-0d6d-4a3a-8c1e-3f9b6e7a1a00"
# Read-only: the full stored event list (count + up to 32 entries), distinct
# from the write-only "add one event" characteristics above.
FOOD_EVENTS_READBACK_UUID = "5b2c0008-0d6d-4a3a-8c1e-3f9b6e7a1a00"
EXERCISE_EVENTS_READBACK_UUID = "5b2c0009-0d6d-4a3a-8c1e-3f9b6e7a1a00"
# Read + write, NOT persisted to flash (a live session control, not
# configuration) — the board defaults to running at boot and stays fully
# autonomous; this exists only so the app can align its local
# SimulationEngine's clock with the board's when both are connected. See
# PROTOCOL_SPEC.md's "Run state" section.
RUN_STATE_UUID = "5b2c000a-0d6d-4a3a-8c1e-3f9b6e7a1a00"
# Notify-only, 1-byte generation counter (value itself unused, only its
# arrival time matters). Fires the instant ANY config write (person, sensor,
# mode, food/exercise event, or a run-state STOPPED reset) actually takes
# effect on the board. Used for two things: (1) true clock sync — anchor the
# app's t=0 to this instead of to whenever it sent the write that caused it,
# eliminating BLE round-trip + processing latency as a visible phase shift;
# (2) "the board actually applied what I just sent" confirmation for the
# config windows' Send to Board buttons.
RESET_SYNC_UUID = "5b2c000b-0d6d-4a3a-8c1e-3f9b6e7a1a00"
# Write-only, one-shot: unlike FOOD_EVENT_UUID/EXERCISE_EVENT_UUID above,
# writing here does NOT reset the board's simulation clock or model state —
# it's added directly to model_thread's live "instant event" slots and decays
# on its own over duration_min, so it can be injected mid-run without
# disrupting the run already in progress. Never persisted to flash, never
# triggers RESET_SYNC_UUID. See PROTOCOL_SPEC.md.
FOOD_INSTANT_UUID = "5b2c000c-0d6d-4a3a-8c1e-3f9b6e7a1a00"
EXERCISE_INSTANT_UUID = "5b2c000d-0d6d-4a3a-8c1e-3f9b6e7a1a00"
# Read + write, NOT persisted to flash (live session control, like
# RUN_STATE_UUID). While enabled, the board streams only standard CGM
# Measurement notifications: no Food/Exercise Status notifications, and
# every write to person/sensor/mode/food/exercise/instant-event
# characteristics is rejected (BT_ATT_ERR_WRITE_NOT_PERMITTED) — only this
# characteristic and RUN_STATE_UUID stay writable. Enabling does not reset
# anything (just ensures the simulation is running); disabling always
# resets and stops, waiting for an explicit RUNNING write. See
# PROTOCOL_SPEC.md's "CGMS Only mode" section.
CGMS_ONLY_UUID = "5b2c000e-0d6d-4a3a-8c1e-3f9b6e7a1a00"
