#!/usr/bin/env python3
from __future__ import annotations

"""Generate a layer-by-layer ablation report from logged Drawing-RNG verifications.

This script is intentionally read-only. It does not rerun the verifier; it uses
scores already stored in drawing_seed_verifications. That makes it fast enough to
run after every pilot and good enough for conference slides showing why the hard
few-gate design beats score-only thresholding.

Usage from dev/:
  python tools/run_ablation_report.py
  python tools/run_ablation_report.py --source json --input results/exported_verifications.json
  python tools/run_ablation_report.py --out results/ablation_phase4

Requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY for --source supabase.
"""

import argparse
import csv
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

POSITIVE_TYPES = {"owner_test"}
CORE_NEGATIVE_TYPES = {"blind_impostor", "informed_forgery", "true_wrong_shape"}
STRESS_NEGATIVE_TYPES = {"wrong_shape", "near_miss", "concept_variant"}
EXCLUDED_TYPES = {"bad_sample", "ambiguous", "step_up_component"}
KNOWN_TYPES = POSITIVE_TYPES | CORE_NEGATIVE_TYPES | STRESS_NEGATIVE_TYPES


def _jsonish(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def nested(row: Dict[str, Any], *path: str) -> Any:
    cur: Any = row
    for key in path:
        cur = _jsonish(cur)
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return _jsonish(cur)


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    value = _jsonish(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def safe_bool(value: Any) -> bool:
    value = _jsonish(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "t", "1", "yes", "y"}
    return bool(value)


def score(row: Dict[str, Any], key: str, fallback_path: Optional[List[str]] = None) -> Optional[float]:
    v = safe_float(row.get(key))
    if v is not None:
        return v
    if fallback_path:
        return safe_float(nested(row, *fallback_path))
    return None


def token_threshold(row: Dict[str, Any]) -> float:
    return safe_float(row.get("token_threshold"), safe_float(nested(row, "verification_result", "token_threshold"), 0.65)) or 0.65


def geometry_thresholds(row: Dict[str, Any]) -> Dict[str, float]:
    thresholds = nested(row, "verification_result", "geometry_thresholds")
    if isinstance(thresholds, dict):
        return {
            "layout": safe_float(thresholds.get("layout"), 0.62) or 0.62,
            "relation": safe_float(thresholds.get("relation"), 0.65) or 0.65,
            "curve": safe_float(thresholds.get("curve"), 0.58) or 0.58,
            "stroke_shape": safe_float(thresholds.get("stroke_shape"), 0.68) or 0.68,
            "closed_style": safe_float(thresholds.get("closed_style"), 0.62) or 0.62,
            "geometry_final": safe_float(thresholds.get("geometry_final"), 0.70) or 0.70,
        }
    return {
        "layout": 0.62,
        "relation": 0.65,
        "curve": 0.58,
        "stroke_shape": 0.68,
        "closed_style": 0.62,
        "geometry_final": 0.70,
    }


def gscore(row: Dict[str, Any], name: str) -> Optional[float]:
    column_map = {
        "layout": "layout_score",
        "relation": "relation_score",
        "curve": "curve_score",
        "stroke_shape": "stroke_shape_score",
        "geometry_final": "geometry_final",
        "closed_style": "closed_style_score",
    }
    column = column_map.get(name, name)
    v = safe_float(row.get(column))
    if v is not None:
        return v
    return safe_float(nested(row, "verification_result", "geometry_scores", name))


def scene_score(row: Dict[str, Any], name: str) -> Optional[float]:
    column_map = {
        "scene_final": "scene_final",
        "scene_assignment": "scene_assignment",
        "scene_raster": "scene_raster",
        "scene_relation": "scene_relation",
    }
    v = safe_float(row.get(column_map.get(name, name)))
    if v is not None:
        return v
    return safe_float(nested(row, "verification_result", "scene_scores", name))


def is_complex(row: Dict[str, Any]) -> bool:
    direct = row.get("complex_scene_mode")
    if direct is not None:
        return safe_bool(direct)
    return safe_bool(nested(row, "verification_result", "complex_scene_mode"))


def scene_pass_proxy(row: Dict[str, Any]) -> bool:
    if not is_complex(row):
        return False
    final = scene_score(row, "scene_final") or 0.0
    assignment = scene_score(row, "scene_assignment") or 0.0
    raster = scene_score(row, "scene_raster") or 0.0
    relation = scene_score(row, "scene_relation") or 0.0
    # Conservative proxy for the tightened phase 2.11/2.12 scene gate. The
    # authoritative decision remains recorded_hard_gate; this policy is for
    # interpretability only when rerunning old rows without full scene objects.
    return final >= 0.72 and assignment >= 0.66 and raster >= 0.54 and relation >= 0.54


def complex_geometry_floor_proxy(row: Dict[str, Any]) -> bool:
    return (
        (gscore(row, "layout") or 0.0) >= 0.50
        and (gscore(row, "relation") or 0.0) >= 0.50
        and safe_float(nested(row, "verification_result", "geometry_scores", "topology"), 0.50) >= 0.48
    )


def strict_geometry_pass_proxy(row: Dict[str, Any]) -> bool:
    th = geometry_thresholds(row)
    return (
        (gscore(row, "geometry_final") or 0.0) >= th["geometry_final"]
        and (gscore(row, "layout") or 0.0) >= th["layout"]
        and (gscore(row, "relation") or 0.0) >= th["relation"]
        and (gscore(row, "curve") or 0.0) >= th["curve"]
        and (gscore(row, "stroke_shape") or 0.0) >= th["stroke_shape"]
        and (gscore(row, "closed_style") or 0.0) >= th["closed_style"]
    )


def recorded(row: Dict[str, Any]) -> bool:
    return safe_bool(row.get("accepted"))


def primary_without_stepup(row: Dict[str, Any]) -> bool:
    v = nested(row, "verification_result", "primary_accepted")
    if v is None:
        return recorded(row)
    return safe_bool(v)


def fuzzy_required_proxy(row: Dict[str, Any]) -> bool:
    if not recorded(row):
        return False
    fuzzy = row.get("fuzzy_ok")
    if fuzzy is None:
        fuzzy = nested(row, "verification_result", "fuzzy_recovery", "ok")
    return safe_bool(fuzzy)


def step_up_final_proxy(row: Dict[str, Any]) -> bool:
    # Current logged accepted already treats step-up-required as not unlocked.
    # If a component challenge row exists, keep it out of the main attempt-type
    # classes and inspect it separately.
    return recorded(row)


def load_json_rows(path: Path) -> List[Dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    for key in ("verifications", "rows", "data"):
        if isinstance(data.get(key), list):
            return data[key]
    raise ValueError(f"Could not find a row list in {path}")


def load_supabase_rows(table: str) -> List[Dict[str, Any]]:
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SystemExit("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY.")
    client = create_client(url, key)
    rows: List[Dict[str, Any]] = []
    start = 0
    page_size = 1000
    while True:
        res = client.table(table).select("*").order("created_at").range(start, start + page_size - 1).execute()
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return rows


def write_csv(path: Path, rows: Iterable[Dict[str, Any]], fields: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)



def decision_value(decision: Callable[[Dict[str, Any]], Any], row: Dict[str, Any]) -> Optional[bool]:
    try:
        value = decision(row)
    except Exception:
        return None
    if value is None:
        return None
    return bool(value)


def token_weighted_policy(row: Dict[str, Any]) -> Optional[bool]:
    v = score(row, "token_score_weighted")
    if v is None:
        v = safe_float(nested(row, "verification_result", "token_score_weighted"))
    if v is None:
        return None
    return v >= token_threshold(row)

def metric_row(name: str, rows: List[Dict[str, Any]], decision: Callable[[Dict[str, Any]], Any]) -> Dict[str, Any]:
    labeled = [r for r in rows if str(r.get("attempt_type") or "") in KNOWN_TYPES]
    owner = [r for r in labeled if r.get("attempt_type") in POSITIVE_TYPES]
    core = [r for r in labeled if r.get("attempt_type") in CORE_NEGATIVE_TYPES]
    stress = [r for r in labeled if r.get("attempt_type") in STRESS_NEGATIVE_TYPES]
    informed = [r for r in labeled if r.get("attempt_type") == "informed_forgery"]
    blind = [r for r in labeled if r.get("attempt_type") == "blind_impostor"]
    true_wrong = [r for r in labeled if r.get("attempt_type") == "true_wrong_shape"]
    def evaluated(xs: List[Dict[str, Any]]) -> List[bool]:
        vals: List[bool] = []
        for x in xs:
            v = decision_value(decision, x)
            if v is not None:
                vals.append(v)
        return vals
    def count_true(xs: List[Dict[str, Any]]) -> int:
        return sum(evaluated(xs))
    def rate(xs: List[Dict[str, Any]]) -> Optional[float]:
        vals = evaluated(xs)
        return sum(vals) / len(vals) if vals else None
    owner_eval = len(evaluated(owner))
    core_eval = len(evaluated(core))
    informed_eval = len(evaluated(informed))
    blind_eval = len(evaluated(blind))
    true_wrong_eval = len(evaluated(true_wrong))
    stress_eval = len(evaluated(stress))
    owner_rate = rate(owner)
    return {
        "policy": name,
        "attempts": len(labeled),
        "evaluable_attempts": len(evaluated(labeled)),
        "coverage": len(evaluated(labeled)) / len(labeled) if labeled else None,
        "owner_attempts": len(owner),
        "owner_evaluable": owner_eval,
        "owner_accepted": count_true(owner),
        "owner_tpr": owner_rate,
        "owner_frr": 1 - owner_rate if owner_rate is not None else None,
        "core_negative_attempts": len(core),
        "core_negative_evaluable": core_eval,
        "core_negative_accepted": count_true(core),
        "core_far": rate(core),
        "informed_forgery_attempts": len(informed),
        "informed_forgery_evaluable": informed_eval,
        "informed_forgery_accepted": count_true(informed),
        "informed_forgery_far": rate(informed),
        "blind_impostor_attempts": len(blind),
        "blind_impostor_evaluable": blind_eval,
        "blind_impostor_accepted": count_true(blind),
        "blind_impostor_far": rate(blind),
        "true_wrong_shape_attempts": len(true_wrong),
        "true_wrong_shape_evaluable": true_wrong_eval,
        "true_wrong_shape_accepted": count_true(true_wrong),
        "true_wrong_shape_far": rate(true_wrong),
        "stress_attempts": len(stress),
        "stress_evaluable": stress_eval,
        "stress_accepted": count_true(stress),
        "stress_accept_rate": rate(stress),
    }


def fmt(v: Any) -> str:
    if v is None:
        return "n/a"
    try:
        return f"{float(v):.3f}"
    except Exception:
        return str(v)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["supabase", "json"], default="supabase")
    ap.add_argument("--input", type=Path, help="JSON/JSONL verification export for --source json")
    ap.add_argument("--table", default=os.environ.get("VERIFICATION_TABLE", "drawing_seed_verifications"))
    ap.add_argument("--out", type=Path, default=Path("results") / "ablation")
    args = ap.parse_args()

    rows = load_json_rows(args.input) if args.source == "json" else load_supabase_rows(args.table)
    if args.source == "json" and not args.input:
        raise SystemExit("--input is required with --source json")

    policies: List[tuple[str, Callable[[Dict[str, Any]], bool]]] = [
        ("recorded_phase3_hard_gate", recorded),
        ("primary_without_step_up", primary_without_stepup),
        ("final_score_threshold_0_68", lambda r: (score(r, "final_score") or 0.0) >= 0.68),
        ("token_only_flat", lambda r: (score(r, "token_score") or 0.0) >= token_threshold(r)),
        ("token_only_weighted", token_weighted_policy),
        ("geometry_final_only", lambda r: (gscore(r, "geometry_final") or 0.0) >= geometry_thresholds(r)["geometry_final"]),
        ("token_plus_geometry_final", lambda r: ((score(r, "token_score") or 0.0) >= token_threshold(r)) and ((gscore(r, "geometry_final") or 0.0) >= geometry_thresholds(r)["geometry_final"])),
        ("token_plus_strict_geometry_subgates", lambda r: ((score(r, "token_score") or 0.0) >= token_threshold(r)) and strict_geometry_pass_proxy(r)),
        ("complex_scene_proxy", lambda r: (((score(r, "token_score") or 0.0) >= max(0.53, token_threshold(r) - 0.07)) and scene_pass_proxy(r) and complex_geometry_floor_proxy(r)) if is_complex(r) else (((score(r, "token_score") or 0.0) >= token_threshold(r)) and strict_geometry_pass_proxy(r))),
        ("fuzzy_required_proxy", fuzzy_required_proxy),
        ("step_up_final_recorded", step_up_final_proxy),
    ]

    metrics = [metric_row(name, rows, fn) for name, fn in policies]
    args.out.mkdir(parents=True, exist_ok=True)
    fields = list(metrics[0].keys()) if metrics else ["policy"]
    write_csv(args.out / "ablation_summary.csv", metrics, fields)

    lines = [
        "# Drawing-RNG Ablation Report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "This report is read-only and uses scores already logged in `drawing_seed_verifications`.",
        "The authoritative production-style row is `recorded_phase3_hard_gate`; other rows are counterfactual proxies to explain why each layer exists.",
        "",
        "| Policy | Coverage | Owner TPR | Owner FRR | Core FAR | Informed FAR | Blind FAR | True wrong FAR | Stress accept |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in metrics:
        lines.append(
            f"| {row['policy']} | {fmt(row['coverage'])} | {fmt(row['owner_tpr'])} | {fmt(row['owner_frr'])} | {fmt(row['core_far'])} | "
            f"{fmt(row['informed_forgery_far'])} | {fmt(row['blind_impostor_far'])} | {fmt(row['true_wrong_shape_far'])} | {fmt(row['stress_accept_rate'])} |"
        )
    lines.extend([
        "",
        "## How to read this",
        "",
        "- `recorded_phase3_hard_gate` is the current verifier result in your dataset.",
        "- `primary_without_step_up` estimates what would have happened if suspicious accepts were allowed without the component challenge.",
        "- `final_score_threshold_0_68` is intentionally included to show why blended-score-only unlocking is unsafe.",
        "- `fuzzy_required_proxy` is not the recommended current gate; it shows how strict the experimental fuzzy layer would be if required.",
        "- `complex_scene_proxy` is a score-only approximation of scene mode and may differ from the true verifier because full stroke objects are not rerun here.",
        "- `Coverage` is the fraction of logged rows that had enough stored fields to evaluate that proxy. A low-coverage row, especially `token_only_weighted`, should not be used as evidence until those scores are backfilled or logged on new attempts.",
        "",
    ])
    report_path = args.out / "ablation_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(report_path.read_text(encoding="utf-8"))
    print(f"Outputs written to: {args.out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
