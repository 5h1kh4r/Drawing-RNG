#!/usr/bin/env python3
from __future__ import annotations

"""Recompute Seed Quality Score for existing Supabase enrollments.

Usage from dev/:
  python tools/run_seed_quality_backfill.py --dry-run
  python tools/run_seed_quality_backfill.py --update

Requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in the environment.
The script writes results/seed_quality_backfill.json and .csv.
"""

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from drawing_rng.enrollment import analyze_enrollment

try:
    from supabase import create_client
except Exception as exc:
    raise SystemExit(f"supabase package is required: {exc}")


def _as_obj(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


def _fetch_all(client: Any, table: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    start = 0
    step = 1000
    while True:
        res = client.table(table).select("*").range(start, start + step - 1).execute()
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < step:
            break
        start += step
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default=os.environ.get("ENROLLMENT_TABLE", "drawing_seed_enrollments"))
    ap.add_argument("--update", action="store_true", help="write recomputed quality columns back to Supabase")
    ap.add_argument("--dry-run", action="store_true", help="do not update Supabase; default behavior")
    args = ap.parse_args()

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY first.")
    client = create_client(url, key)

    rows = _fetch_all(client, args.table)
    out: List[Dict[str, Any]] = []
    for row in rows:
        attempts = _as_obj(row.get("attempts")) or []
        if not isinstance(attempts, list) or len(attempts) < 2:
            out.append({"id": row.get("id"), "ok": False, "error": "missing_or_insufficient_attempts"})
            continue
        try:
            analysis = analyze_enrollment(
                attempts=attempts,
                domain=((_as_obj(row.get("analysis_result")) or {}).get("outputs") or {}).get("domain") or "example.com",
                salt=row.get("public_salt"),
            )
            seed_quality = analysis.get("seed_quality") or {}
            rec = {
                "id": row.get("id"),
                "participant_id": row.get("participant_id"),
                "seed_label": row.get("seed_label"),
                "ok": True,
                "seed_quality_score": seed_quality.get("quality_score"),
                "seed_quality_label": seed_quality.get("quality_label"),
                "seed_quality_hard_reject": seed_quality.get("hard_reject"),
                "complexity_class": analysis.get("complexity_class"),
                "scene_stability_score": analysis.get("scene_stability_score"),
                "timing_stability_score": analysis.get("timing_stability_score"),
                "warnings": ";".join(seed_quality.get("warnings") or []),
                "recommendations": ";".join(seed_quality.get("recommendations") or []),
            }
            out.append(rec)
            if args.update:
                client.table(args.table).update({
                    "analysis_result": analysis,  # contains redacted only if your logging layer redacts; use dev DB carefully
                    "seed_quality_score": rec["seed_quality_score"],
                    "seed_quality_label": rec["seed_quality_label"],
                    "seed_quality_hard_reject": rec["seed_quality_hard_reject"],
                    "complexity_class": rec["complexity_class"],
                    "scene_stability_score": rec["scene_stability_score"],
                    "timing_stability_score": rec["timing_stability_score"],
                }).eq("id", row.get("id")).execute()
        except Exception as exc:
            out.append({"id": row.get("id"), "ok": False, "error": str(exc)})

    results = ROOT / "results"
    results.mkdir(exist_ok=True)
    json_path = results / "seed_quality_backfill.json"
    csv_path = results / "seed_quality_backfill.csv"
    json_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    fields = ["id", "participant_id", "seed_label", "ok", "seed_quality_score", "seed_quality_label", "seed_quality_hard_reject", "complexity_class", "scene_stability_score", "timing_stability_score", "warnings", "recommendations", "error"]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in out:
            w.writerow({k: r.get(k) for k in fields})
    print(f"Processed {len(out)} enrollments")
    print(f"Wrote {json_path}")
    print(f"Wrote {csv_path}")
    if args.update:
        print("Updated Supabase rows with recomputed quality columns.")
    else:
        print("Dry run only. Re-run with --update to write quality columns back.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
