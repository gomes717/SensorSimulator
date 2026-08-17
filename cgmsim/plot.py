#!/usr/bin/env python3
"""Plot a cgmsim CSV log produced by the C simulator.

Usage:
    python plot.py output/glucose_log.csv [--save output/glucose_log.png]
"""
import argparse
import csv

import matplotlib.pyplot as plt


def load_csv(path):
    rows = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({k: float(v) for k, v in row.items()})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", help="CSV file written by the cgmsim binary")
    parser.add_argument("--save", help="path to save the figure (PNG); shown on screen if omitted")
    args = parser.parse_args()

    rows = load_csv(args.csv_path)
    t = [r["t_min"] / 60.0 for r in rows]  # hours, easier to read
    glucose_true = [r["glucose_true_mg_dl"] for r in rows]

    sensor_t = [r["t_min"] / 60.0 for r in rows if r["sensor_valid"] > 0.5]
    sensor_v = [r["sensor_mg_dl"] for r in rows if r["sensor_valid"] > 0.5]

    # Group contiguous carbs_g_step > 0 rows into single meal events (a meal
    # is spread over several minutes for the rate-based models), summing the
    # per-step grams into the meal's true total.
    meal_events = []
    meal_start_h = None
    meal_total_g = 0.0
    for r in rows:
        if r["carbs_g_step"] > 0:
            if meal_start_h is None:
                meal_start_h = r["t_min"] / 60.0
                meal_total_g = 0.0
            meal_total_g += r["carbs_g_step"]
        elif meal_start_h is not None:
            meal_events.append((meal_start_h, meal_total_g))
            meal_start_h = None
    if meal_start_h is not None:
        meal_events.append((meal_start_h, meal_total_g))

    fig, (ax_g, ax_i) = plt.subplots(2, 1, figsize=(11, 7), sharex=True,
                                      gridspec_kw={"height_ratios": [3, 1]})

    ax_g.plot(t, glucose_true, label="plasma glucose (true)", color="#1f77b4", linewidth=1.5)
    if sensor_t:
        ax_g.scatter(sensor_t, sensor_v, label="sensor reading", color="#d62728", s=8, zorder=3)

    ax_g.axhspan(70, 180, color="#2ca02c", alpha=0.08, label="target range 70-180 mg/dl")
    ax_g.axhline(70, color="#d62728", linestyle="--", linewidth=0.8)
    ax_g.axhline(180, color="#d62728", linestyle="--", linewidth=0.8)

    for tm, grams in meal_events:
        ax_g.axvline(tm, color="#ff7f0e", linestyle=":", linewidth=1)
        ax_g.annotate(f"{grams:.0f}g", (tm, max(glucose_true) * 0.98),
                      rotation=90, fontsize=7, color="#ff7f0e", va="top")

    ax_g.set_ylabel("glucose [mg/dl]")
    ax_g.set_title(f"cgmsim — {args.csv_path}")
    ax_g.legend(loc="upper right", fontsize=8)
    ax_g.grid(alpha=0.2)

    ax_i.plot(t, [r["iir_u_per_h"] for r in rows], label="insulin infusion [U/h]", color="#9467bd")
    if any(r["exercise_pct"] for r in rows):
        ax_i.plot(t, [r["exercise_pct"] for r in rows], label="exercise [%VO2max]", color="#2ca02c")
    if any(r["hr_bpm"] for r in rows):
        ax_i.plot(t, [r["hr_bpm"] for r in rows], label="heart rate [bpm]", color="#8c564b", alpha=0.6)

    ax_i.set_xlabel("time [h]")
    ax_i.set_ylabel("insulin / exercise")
    ax_i.legend(loc="upper right", fontsize=8)
    ax_i.grid(alpha=0.2)

    fig.tight_layout()

    if args.save:
        fig.savefig(args.save, dpi=150)
        print(f"saved figure to {args.save}")
    else:
        plt.show()


if __name__ == "__main__":
    main()
