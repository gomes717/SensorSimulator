# Scenarios

Timed-action scripts for the **Scenario** window (toolbar → Scenario). Load a
`.json`, press **Run**, and the actions fire on a wall-clock timeline
(`at_s` = seconds from Run). Runner: `src/models/scenario.py`.

## Action schema

```json
{
  "name": "human readable",
  "actions": [
    { "at_s": <number>, "kind": "<kind>", "args": { ... } }
  ]
}
```

| kind | args | effect |
|---|---|---|
| `speed` | `{ "multiplier": 1..1000 }` | sets the simulation speed slider |
| `run_state` | `{ "state": "start" \| "stop" \| "pause" \| "resume" }` | drives Start/Stop/Pause |
| `person` | `{ "person": "<profile name>" }` | selects that patient (no-op if not found) |
| `data_source` | `{ "person": "<profile name>" }` | selects the patient + pushes its model/CSV source to the board |
| `comm_profile` | `{ "profile": "sig" \| "dexcom" }` | switches the board's BLE profile (reconnect after) |
| `insert_food` | `{ "carbs_g": <g>, "duration_min": <min> }` | one-shot carb bolus (no reset) |
| `insert_exercise` | `{ "duration_min": <min>, "intensity_pct": 0..100 }` | one-shot exercise bout |
| `inject_fault` | `{ "fault": "pisa", "duration_min": <min>, "depth_frac": 0..1 }` | sensor fault; PISA = transient false low |

Notes:
- `at_s` is **wall-clock** — under a high speed multiplier the *simulation*
  compresses but the schedule does not, so keep offsets small (x60 → 1 s ≈ 1
  sim-minute).
- Model/CSV-specific scenarios expect the matching patient to exist. Create it
  in **Person Configuration** first (or the `person` action is a no-op and the
  scenario runs against whatever patient is active). The default seeded patient
  is *Sample Patient* (Cambridge).
- These are also what `scripts/e2e.py` and the loop-runner replay.

## Files

| file | what it exercises |
|---|---|
| `demo_pisa.json` | Cambridge + meal + a PISA false low, shaded on the graph |
| `cambridge_meal.json` | rate-fed meal response (Cambridge / UVA-Padova) |
| `deichmann_exercise.json` | HR-driven exercise drop (needs a *Deichmann Patient*) |
| `royparker_exercise.json` | exercise term (needs a *Roy Parker Patient*) |
| `alerts_low_high.json` | big meal → HIGH badge, then PISA → LOW badge |
| `speed_sweep.json` | x1 → x10 → x60 → x300 while streaming |
| `csv_playback.json` | replay a CSV-backed patient (needs a *CSV Patient* with a window assigned) |
| `full_demo.json` | speed + meal + exercise + PISA + comm-profile switch, ~2 min |
