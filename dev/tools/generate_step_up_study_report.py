#!/usr/bin/env python3
from __future__ import annotations

"""Summarize Drawing-RNG step-up component challenge behavior.

This is intentionally read-only. It uses rows already logged in
`drawing_seed_verifications` and separates two row families:

1. Initial full-redraw rows where `step_up_required=true` or a nested
   verification_result.step_up_required is true.
2. Component-challenge rows where attempt_type == step_up_component.

Usage from dev/:
  python tools/generate_step_up_study_report.py
  python tools/generate_step_up_study_report.py --source json --input results/exported_verifications.json

The goal is to answer whether step-up is actually being exercised and whether
owners/forgers pass the component challenge differently. If you have not yet
collected component-challenge rows, the report will explicitly say so.
"""

import argparse
import csv
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

POSITIVE_TYPES = {"owner_test"}
ADVERSARIAL_TYPES = {"blind_impostor", "informed_forgery", "true_wrong_shape", "wrong_shape", "near_miss", "concept_variant"}


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


def safe_bool(v: Any) -> bool:
    v = _jsonish(v)
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in {"true", "t", "1", "yes", "y"}
    return bool(v)


def safe_float(v: Any, default: Optional[float] = None) -> Optional[float]:
    v = _jsonish(v)
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def load_json_rows(path: Path) -> List[Dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    for key in ("rows", "data", "verifications"):
        if isinstance(data.get(key), list):
            return data[key]
    raise ValueError(f"Could not find row list in {path}")


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


def write_csv(path: Path, rows: Iterable[Dict[str, Any]], fields: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def attempt_type(row: Dict[str, Any]) -> str:
    return str(row.get("attempt_type") or nested(row, "verification_result", "attempt_type") or "unknown")


def step_up_required(row: Dict[str, Any]) -> bool:
    return safe_bool(row.get("step_up_required")) or safe_bool(nested(row, "verification_result", "step_up_required"))


def primary_accepted(row: Dict[str, Any]) -> bool:
    v = nested(row, "verification_result", "primary_accepted")
    return safe_bool(v if v is not None else row.get("accepted"))


def challenge_passed(row: Dict[str, Any]) -> bool:
    return safe_bool(row.get("step_up_passed")) or safe_bool(nested(row, "verification_result", "step_up_passed")) or safe_bool(row.get("accepted"))


def fmt(v: Any) -> str:
    if v is None:
        return "n/a"
    try:
        return f"{float(v):.3f}"
    except Exception:
        return str(v)


def summarize_group(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {"attempts": 0, "accepted": 0, "rate": None, "median_component_score": None, "median_timing": None}
    acc = sum(challenge_passed(r) for r in rows)
    comp_scores = sorted(x for x in (safe_float(r.get("component_score"), safe_float(nested(r, "verification_result", "component_score"))) for r in rows) if x is not None)
    timings = sorted(x for x in (safe_float(r.get("timing_final"), safe_float(nested(r, "verification_result", "timing_scores", "timing_final"))) for r in rows) if x is not None)
    def median(xs: List[float]) -> Optional[float]:
        if not xs:
            return None
        return xs[len(xs)//2]
    return {
        "attempts": len(rows),
        "accepted": acc,
        "rate": acc / len(rows),
        "median_component_score": median(comp_scores),
        "median_timing": median(timings),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["supabase", "json"], default="supabase")
    ap.add_argument("--input", type=Path, help="JSON/JSONL verification export for --source json")
    ap.add_argument("--table", default=os.environ.get("VERIFICATION_TABLE", "drawing_seed_verifications"))
    ap.add_argument("--out", type=Path, default=Path("results") / "step_up")
    args = ap.parse_args()
    if args.source == "json" and not args.input:
        raise SystemExit("--input is required with --source json")
    rows = load_json_rows(args.input) if args.source == "json" else load_supabase_rows(args.table)

    initial_rows = [r for r in rows if attempt_type(r) != "step_up_component"]
    stepup_initials = [r for r in initial_rows if step_up_required(r)]
    component_rows = [r for r in rows if attempt_type(r) == "step_up_component"]

    by_type: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in stepup_initials:
        by_type[attempt_type(r)].append(r)

    comp_by_type: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in component_rows:
        # Payload logging may preserve original intent in nested JSON; otherwise
        # component rows are a single bucket. The table still shows pass rates.
        original = nested(r, "verification_result", "initial_result", "attempt_type") or r.get("original_attempt_type") or "step_up_component"
        comp_by_type[str(original)].append(r)

    summary_rows: List[Dict[str, Any]] = []
    for key in sorted(set(by_type) | set(comp_by_type)):
        initials = by_type.get(key, [])
        comps = comp_by_type.get(key, [])
        csum = summarize_group(comps)
        summary_rows.append({
            "attempt_type": key,
            "initial_step_up_required": len(initials),
            "initial_primary_accepted": sum(primary_accepted(r) for r in initials),
            "component_attempts": csum["attempts"],
            "component_passed": csum["accepted"],
            "component_pass_rate": csum["rate"],
            "median_component_score": csum["median_component_score"],
            "median_timing_final": csum["median_timing"],
        })

    args.out.mkdir(parents=True, exist_ok=True)
    fields = ["attempt_type", "initial_step_up_required", "initial_primary_accepted", "component_attempts", "component_passed", "component_pass_rate", "median_component_score", "median_timing_final"]
    write_csv(args.out / "step_up_summary.csv", summary_rows, fields)

    owner_initials = [r for r in stepup_initials if attempt_type(r) in POSITIVE_TYPES]
    adversarial_initials = [r for r in stepup_initials if attempt_type(r) in ADVERSARIAL_TYPES]
    owner_components = [r for r in component_rows if (nested(r, "verification_result", "initial_result", "attempt_type") or "owner_test") in POSITIVE_TYPES]
    adversarial_components = [r for r in component_rows if (nested(r, "verification_result", "initial_result", "attempt_type") or "") in ADVERSARIAL_TYPES]
    owner_comp = summarize_group(owner_components)
    adv_comp = summarize_group(adversarial_components)

    lines = [
        "# Drawing-RNG Step-Up Challenge Study",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "This report checks whether the component challenge is actually being triggered and completed. It does not claim step-up improves security unless component attempts have been collected.",
        "",
        "## Overview",
        "",
        f"- Initial full-redraw rows: {len(initial_rows)}",
        f"- Initial rows requiring step-up: {len(stepup_initials)}",
        f"- Owner step-up initials: {len(owner_initials)}",
        f"- Adversarial/stress step-up initials: {len(adversarial_initials)}",
        f"- Component challenge rows: {len(component_rows)}",
        "",
        "## Component challenge outcomes",
        "",
        "| Class | Component attempts | Passed | Pass rate | Median component score | Median timing |",
        "|---|---:|---:|---:|---:|---:|",
        f"| owner | {owner_comp['attempts']} | {owner_comp['accepted']} | {fmt(owner_comp['rate'])} | {fmt(owner_comp['median_component_score'])} | {fmt(owner_comp['median_timing'])} |",
        f"| adversarial_or_stress | {adv_comp['attempts']} | {adv_comp['accepted']} | {fmt(adv_comp['rate'])} | {fmt(adv_comp['median_component_score'])} | {fmt(adv_comp['median_timing'])} |",
        "",
        "## By attempt type",
        "",
        "| Attempt type | Initial step-up required | Initial primary accepted | Component attempts | Component passed | Pass rate | Median component score |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in summary_rows:
        lines.append(f"| {r['attempt_type']} | {r['initial_step_up_required']} | {r['initial_primary_accepted']} | {r['component_attempts']} | {r['component_passed']} | {fmt(r['component_pass_rate'])} | {fmt(r['median_component_score'])} |")
    if not component_rows:
        lines.extend([
            "",
            "## Interpretation",
            "",
            "No completed component-challenge rows are present yet. For a meaningful mini-study, collect roughly 10 owner borderline challenges and 10 informed-forgery/screenshot-copy challenges, then rerun this report.",
        ])
    else:
        lines.extend([
            "",
            "## Interpretation",
            "",
            "Use this table to estimate owner rescue rate and attacker pass rate for borderline cases. The goal is high owner pass rate and low informed-forgery pass rate; do not promote step-up as a defense until both classes have enough attempts.",
        ])

    report = args.out / "step_up_study_report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    print(report.read_text(encoding="utf-8"))
    print(f"Outputs written to: {args.out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
