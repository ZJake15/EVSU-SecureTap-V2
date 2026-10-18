"""Summarizes the capstone test mode's results (test_mode.py) for the paper.

    .venv\\Scripts\\python.exe entry-agent\\test_mode_summary.py [results.csv] [--out folder]

With no file given it reads entry-agent\\test-results\\results.csv. Writes,
next to it (or into --out):

- summary_table.csv - one row per category x distance, plus an "all" row per
  category: trials, correct, success rate (with a 95% confidence interval),
  mean / median / standard deviation of the decision time, mean confidence
  and mean liveness score
- result_breakdown.csv - how many trials ended in each result
- decision_time_vs_distance.png and success_rate_vs_distance.png

How it counts (say this in the paper's method section):

- A trial is one row with status "final" (a redone trial's earlier row is
  "redone" and left out). Skipped trials are left out and counted apart.
- Success rate = correct trials / all trials, so "Not detected", "No
  decision", "Occluded" and a card-tap request all count as NOT successful.
- Decision times are over the trials that reached a decision (Match, Spoof,
  Unknown or a card-tap request) - a timed-out trial has no decision time.
- Confidence = the face's similarity to the closest enrolled person, in %
  (for a Match, the match confidence the gate showed).
"""

import argparse
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # files only - no window
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

CATEGORY_ORDER = ["enrolled", "spoof", "unknown"]
CATEGORY_LABEL = {"enrolled": "Enrolled (should match)", "spoof": "Spoof (should be rejected)",
                  "unknown": "Unknown (should be rejected)"}
CATEGORY_COLOR = {"enrolled": "#2e7d32", "spoof": "#c62828", "unknown": "#ef6c00"}
# Each category's points are drawn slightly left/right of the distance, so
# equal values (say, all 100%) don't hide each other.
CATEGORY_OFFSET = {"enrolled": -0.06, "spoof": 0.0, "unknown": 0.06}
RESULT_ORDER = ["Match", "Spoof", "Unknown", "Tiebreak requested", "Occluded", "No decision", "Not detected"]


def wilson_interval(successes, trials, z=1.96):
    """95% confidence interval for a success rate - better than +-1.96*SE
    for small samples and rates near 0% or 100%."""
    if trials == 0:
        return None, None
    p = successes / trials
    centre = (p + z * z / (2 * trials)) / (1 + z * z / trials)
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / (1 + z * z / trials)
    return max(0.0, centre - half) * 100, min(1.0, centre + half) * 100


def load(path):
    rows = pd.read_csv(path, dtype={"correct": str, "status": str, "result": str})
    rows = rows[rows["status"] == "final"].copy()
    skipped = rows[rows["result"] == "Skipped"]
    rows = rows[rows["result"] != "Skipped"].copy()
    rows["distance_m"] = rows["distance_m"].astype(float)
    for column in ("decision_time_ms", "decision_time_from_first_face_ms", "time_to_first_detection_ms",
                   "best_similarity", "liveness_score"):
        rows[column] = pd.to_numeric(rows[column], errors="coerce")
    rows["confidence_pct"] = rows["best_similarity"] * 100
    rows["is_correct"] = rows["correct"].str.lower() == "yes"
    return rows, skipped


def _mean(series, digits=1):
    return round(series.mean(), digits) if series.notna().any() else None


def _summarize(group):
    trials = len(group)
    correct = int(group["is_correct"].sum())
    low, high = wilson_interval(correct, trials)
    times = group["decision_time_ms"].dropna()
    return {
        "trials": trials,
        "correct": correct,
        "success_rate_pct": round(correct / trials * 100, 1) if trials else None,
        "success_ci95_low_pct": None if low is None else round(low, 1),
        "success_ci95_high_pct": None if high is None else round(high, 1),
        "timed_trials": len(times),
        "decision_time_mean_ms": round(times.mean(), 1) if len(times) else None,
        "decision_time_median_ms": round(times.median(), 1) if len(times) else None,
        "decision_time_sd_ms": round(times.std(ddof=1), 1) if len(times) > 1 else None,
        "decision_time_from_first_face_mean_ms": _mean(group["decision_time_from_first_face_ms"]),
        "first_detection_mean_ms": _mean(group["time_to_first_detection_ms"]),
        "confidence_mean_pct": _mean(group["confidence_pct"]),
        "liveness_mean": _mean(group["liveness_score"], 3),
    }


def summary_table(rows):
    table = []
    for category in CATEGORY_ORDER:
        of_category = rows[rows["category"] == category]
        if of_category.empty:
            continue
        for distance in sorted(of_category["distance_m"].unique()):
            table.append({"category": category, "distance_m": f"{distance:g}",
                          **_summarize(of_category[of_category["distance_m"] == distance])})
        table.append({"category": category, "distance_m": "all", **_summarize(of_category)})
    return pd.DataFrame(table)


def result_breakdown(rows):
    counts = pd.crosstab([rows["category"], rows["distance_m"]], rows["result"])
    columns = [r for r in RESULT_ORDER if r in counts.columns] + [c for c in counts.columns if c not in RESULT_ORDER]
    return counts[columns].reset_index()


def _per_distance(table):
    per_distance = table[table["distance_m"] != "all"].copy()
    per_distance["x"] = per_distance["distance_m"].astype(float)
    return per_distance


def _finish_chart(fig, ax, per_distance, ylabel, title, path):
    ax.set_xlabel("Distance from the camera (m)")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.set_xticks(sorted(per_distance["x"].unique()))
    ax.grid(alpha=0.3)
    ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def chart_decision_time(table, path):
    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=150)
    per_distance = _per_distance(table)
    for category in CATEGORY_ORDER:
        data = per_distance[(per_distance["category"] == category) & per_distance["decision_time_mean_ms"].notna()]
        if not data.empty:
            ax.errorbar(data["x"] + CATEGORY_OFFSET[category], data["decision_time_mean_ms"],
                        yerr=data["decision_time_sd_ms"].fillna(0),
                        marker="o", capsize=4, color=CATEGORY_COLOR[category], label=CATEGORY_LABEL[category])
    ax.set_ylim(bottom=0)
    _finish_chart(fig, ax, per_distance, "Decision time (ms), mean ± SD", "Decision time vs distance", path)


def chart_success_rate(table, path):
    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=150)
    per_distance = _per_distance(table)
    for category in CATEGORY_ORDER:
        data = per_distance[per_distance["category"] == category]
        if not data.empty:
            rate = data["success_rate_pct"]
            error = [rate - data["success_ci95_low_pct"], data["success_ci95_high_pct"] - rate]
            ax.errorbar(data["x"] + CATEGORY_OFFSET[category], rate, yerr=error, marker="o", capsize=4,
                        color=CATEGORY_COLOR[category], label=CATEGORY_LABEL[category])
    ax.set_ylim(0, 105)
    _finish_chart(fig, ax, per_distance, "Success rate (%), with 95% CI", "Success rate vs distance", path)


def main(argv=None):
    default = Path(__file__).resolve().parent / "test-results" / "results.csv"
    parser = argparse.ArgumentParser(description="Summarizes the capstone test mode's results.csv.")
    parser.add_argument("results", nargs="?", default=str(default), help="the test mode's results.csv")
    parser.add_argument("--out", help="folder for the table and charts (default: next to results.csv)")
    args = parser.parse_args(argv)

    path = Path(args.results)
    if not path.exists():
        sys.exit(f"No results file at {path}")
    out = Path(args.out) if args.out else path.parent
    out.mkdir(parents=True, exist_ok=True)

    rows, skipped = load(path)
    if rows.empty:
        sys.exit("No finished trials in the file yet.")
    table = summary_table(rows)
    table.to_csv(out / "summary_table.csv", index=False)
    result_breakdown(rows).to_csv(out / "result_breakdown.csv", index=False)
    chart_decision_time(table, out / "decision_time_vs_distance.png")
    chart_success_rate(table, out / "success_rate_vs_distance.png")

    shown = table[["category", "distance_m", "trials", "correct", "success_rate_pct", "decision_time_mean_ms",
                   "decision_time_median_ms", "decision_time_sd_ms", "confidence_mean_pct", "liveness_mean"]]
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(shown.to_string(index=False))
    print(f"\n{len(rows)} trials summarized, {len(skipped)} skipped. Written to {out}:")
    for name in ("summary_table.csv", "result_breakdown.csv", "decision_time_vs_distance.png",
                 "success_rate_vs_distance.png"):
        print(f"  {name}")


if __name__ == "__main__":
    main()
