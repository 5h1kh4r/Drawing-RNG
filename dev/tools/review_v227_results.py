#!/usr/bin/env python3
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "results" / "v227_hough_comparison" / "verification_comparison.csv"


def as_bool(value: object) -> bool:
    return str(value).lower() in {"true", "1", "yes"}


def main() -> None:
    if not PATH.exists():
        raise SystemExit(f"Missing {PATH}. Run rerun_v227_comparison.py first.")
    with PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    print("Rows:", len(rows))
    print("Transitions:", dict(Counter(row["transition"] for row in rows)))
    print("\nBy attempt type")
    for attempt_type in sorted({row["attempt_type"] for row in rows}):
        subset = [row for row in rows if row["attempt_type"] == attempt_type]
        draw2seed = sum(as_bool(row["draw2seed_accepted"]) for row in subset)
        shapelock = sum(as_bool(row["shape_lock_baseline_pass"]) for row in subset)
        print(
            f"  {attempt_type:20s} n={len(subset):3d} "
            f"Draw2Seed={draw2seed:3d} ({100*draw2seed/max(1,len(subset)):.1f}%) "
            f"ShapeLock={shapelock:3d} ({100*shapelock/max(1,len(subset)):.1f}%)"
        )

    applicable = [row for row in rows if as_bool(row.get("hough_style_applicable"))]
    failed = [row for row in applicable if not as_bool(row.get("hough_style_pass"))]
    print("\nHough line-style gate")
    print("  applicable:", len(applicable))
    print("  failed:", len(failed))
    for row in failed:
        print(
            f"  {row['attempt_type']:20s} {row['verification_id']} "
            f"score={float(row.get('hough_style_score') or 0):.3f} "
            f"coverage={float(row.get('hough_line_coverage') or 0):.3f} "
            f"long={float(row.get('hough_long_line_coverage') or 0):.3f} "
            f"violations={row.get('hough_style_violation_count')} "
            f"strong={row.get('hough_style_strong_violation_count')} "
            f"critical={row.get('hough_style_critical_violation_count')} "
            f"medium={row.get('hough_style_medium_violation_count')} "
            f"bend={row.get('hough_style_bend_violation_count')} "
            f"severe_bend={row.get('hough_style_severe_bend_violation_count')} "
            f"lost_line={row.get('hough_style_lost_line_failure')} "
            f"bend_failure={row.get('hough_style_bend_failure')} "
            f"reliable={row.get('hough_style_hard_gate_reliable')} "
            f"{row['transition']}"
        )

    print("\nChanged decisions")
    changed = [row for row in rows if row["transition"] not in {"accept_to_accept", "reject_to_reject"}]
    for row in changed:
        print(
            f"  {row['attempt_type']:20s} {row['verification_id']} {row['transition']} "
            f"hough={float(row.get('hough_style_score') or 0):.3f} "
            f"ShapeLock={row.get('shape_lock_baseline_pass')} "
            f"reasons={row.get('failure_reasons')}"
        )

    print("\nSecurity-sensitive accepted rows")
    attacks = {"blind_impostor", "informed_forgery", "near_miss", "true_wrong_shape"}
    for row in rows:
        if row["attempt_type"] in attacks and as_bool(row["draw2seed_accepted"]):
            print(
                f"  {row['attempt_type']:20s} {row['verification_id']} "
                f"token={float(row.get('token_score') or 0):.3f} "
                f"geometry={float(row.get('geometry_final') or 0):.3f} "
                f"hough={float(row.get('hough_style_score') or 0):.3f} "
                f"ShapeLock={row.get('shape_lock_baseline_pass')}"
            )


if __name__ == "__main__":
    main()
