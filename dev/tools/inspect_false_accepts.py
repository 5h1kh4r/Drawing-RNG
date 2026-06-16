#!/usr/bin/env python3
from __future__ import annotations

"""Export accepted non-owner attempts for manual review.

This is the next debugging tool after the pilot report. It creates a compact CSV
of accepted informed forgeries / true wrong shapes / stress variants with the
scores needed to inspect why they passed. It never exports secret material.

Usage from dev/:
  python tools/inspect_false_accepts.py
  python tools/inspect_false_accepts.py --include-stress
"""

import argparse
import csv
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

CORE_NEGATIVE_TYPES = {"blind_impostor", "informed_forgery", "true_wrong_shape"}
STRESS_TYPES = {"wrong_shape", "near_miss", "concept_variant"}


def _jsonish(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


def nested(row: Dict[str, Any], *path: str) -> Any:
    cur: Any = row
    for key in path:
        cur = _jsonish(cur)
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return _jsonish(cur)


def safe_float(v: Any, default: Optional[float] = None) -> Optional[float]:
    v = _jsonish(v)
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def safe_bool(v: Any) -> bool:
    v = _jsonish(v)
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in {"true", "t", "1", "yes", "y"}
    return bool(v)


def load_supabase_rows(table: str) -> List[Dict[str, Any]]:
    from supabase import create_client
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SystemExit("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY.")
    client = create_client(url, key)
    rows: List[Dict[str, Any]] = []
    start = 0
    page = 1000
    while True:
        res = client.table(table).select("*").order("created_at").range(start, start + page - 1).execute()
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < page:
            break
        start += page
    return rows


def load_json_rows(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    for key in ("rows", "data", "verifications"):
        if isinstance(data.get(key), list):
            return data[key]
    raise ValueError(f"Could not find rows in {path}")


def write_csv(path: Path, rows: Iterable[Dict[str, Any]], fields: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["supabase", "json"], default="supabase")
    ap.add_argument("--input", type=Path)
    ap.add_argument("--table", default=os.environ.get("VERIFICATION_TABLE", "drawing_seed_verifications"))
    ap.add_argument("--include-stress", action="store_true", help="also include wrong_shape/near_miss/concept_variant accepts")
    ap.add_argument("--out", type=Path, default=Path("results") / "false_accepts")
    args = ap.parse_args()
    if args.source == "json" and not args.input:
        raise SystemExit("--input is required with --source json")
    rows = load_json_rows(args.input) if args.source == "json" else load_supabase_rows(args.table)
    labels = set(CORE_NEGATIVE_TYPES)
    if args.include_stress:
        labels |= STRESS_TYPES
    out: List[Dict[str, Any]] = []
    for r in rows:
        typ = str(r.get("attempt_type") or "")
        if typ not in labels or not safe_bool(r.get("accepted")):
            continue
        vr = _jsonish(r.get("verification_result")) if isinstance(_jsonish(r.get("verification_result")), dict) else {}
        out.append({
            "id": r.get("id"),
            "created_at": r.get("created_at"),
            "enrollment_id": r.get("enrollment_id"),
            "participant_id": r.get("participant_id"),
            "attempt_type": typ,
            "profile": r.get("profile"),
            "final_score": safe_float(r.get("final_score"), safe_float(vr.get("final_score"))),
            "token_score": safe_float(r.get("token_score"), safe_float(vr.get("token_score"))),
            "token_score_weighted": safe_float(r.get("token_score_weighted"), safe_float(vr.get("token_score_weighted"))),
            "token_bigram_score": safe_float(r.get("token_bigram_score"), safe_float(vr.get("token_bigram_score"))),
            "geometry_final": safe_float(r.get("geometry_final"), safe_float(nested(r, "verification_result", "geometry_scores", "geometry_final"))),
            "layout_score": safe_float(r.get("layout_score"), safe_float(nested(r, "verification_result", "geometry_scores", "layout"))),
            "relation_score": safe_float(r.get("relation_score"), safe_float(nested(r, "verification_result", "geometry_scores", "relation"))),
            "curve_score": safe_float(r.get("curve_score"), safe_float(nested(r, "verification_result", "geometry_scores", "curve"))),
            "stroke_shape_score": safe_float(r.get("stroke_shape_score"), safe_float(nested(r, "verification_result", "geometry_scores", "stroke_shape"))),
            "complex_scene_mode": safe_bool(r.get("complex_scene_mode")) or safe_bool(vr.get("complex_scene_mode")),
            "scene_final": safe_float(r.get("scene_final"), safe_float(nested(r, "verification_result", "scene_scores", "scene_final"))),
            "timing_final": safe_float(r.get("timing_final"), safe_float(nested(r, "verification_result", "timing_scores", "timing_final"))),
            "fuzzy_ok": safe_bool(r.get("fuzzy_ok")) or safe_bool(nested(r, "verification_result", "fuzzy_recovery", "ok")),
            "step_up_required": safe_bool(r.get("step_up_required")) or safe_bool(vr.get("step_up_required")),
            "failure_reasons": ";".join(vr.get("failure_reasons") or r.get("failure_reasons") or []),
        })
    args.out.mkdir(parents=True, exist_ok=True)
    fields = list(out[0].keys()) if out else ["id", "enrollment_id", "attempt_type"]
    write_csv(args.out / "accepted_non_owner_attempts.csv", out, fields)
    lines = [
        "# Drawing-RNG Accepted Non-Owner Attempts",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        f"Accepted non-owner rows exported: {len(out)}",
        "",
        "Open `accepted_non_owner_attempts.csv` and inspect the enrollments with high token/geometry/scene scores. These are the templates to use for informed-forgery case studies and seed-quality recalibration.",
        "",
    ]
    (args.out / "accepted_non_owner_attempts.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"Outputs written to: {args.out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
