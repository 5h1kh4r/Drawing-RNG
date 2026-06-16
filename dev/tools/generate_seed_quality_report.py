#!/usr/bin/env python3
from __future__ import annotations

"""Generate a Seed Quality report by joining enrollment quality with verification outcomes.

Run after tools/run_seed_quality_backfill.py. This script can read directly from
Supabase or from the generated CSV/JSON files. It does not print seed material,
canonical secrets, or demo passwords.

Usage from dev/:
  python tools/generate_seed_quality_report.py
  python tools/generate_seed_quality_report.py --quality-csv results/seed_quality_backfill.csv --source supabase
  python tools/generate_seed_quality_report.py --verifications-json results/exported_verifications.json --quality-csv results/seed_quality_backfill.csv
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
CORE_NEGATIVE_TYPES = {"blind_impostor", "informed_forgery", "true_wrong_shape"}
STRESS_NEGATIVE_TYPES = {"wrong_shape", "near_miss", "concept_variant"}
EXCLUDED_TYPES = {"bad_sample", "ambiguous", "step_up_component"}
KNOWN_TYPES = POSITIVE_TYPES | CORE_NEGATIVE_TYPES | STRESS_NEGATIVE_TYPES


def _jsonish(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


def safe_float(v: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return default
    return n if math.isfinite(n) else default


def safe_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in {"true", "t", "1", "yes", "y"}
    return bool(v)


def nested(row: Dict[str, Any], *path: str) -> Any:
    cur: Any = row
    for key in path:
        cur = _jsonish(cur)
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return _jsonish(cur)


def load_json_rows(path: Path) -> List[Dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    for key in ("rows", "data", "enrollments", "verifications"):
        if isinstance(data.get(key), list):
            return data[key]
    raise ValueError(f"Could not find row list in {path}")


def load_csv_rows(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_supabase_table(table: str, order_by: Optional[str] = None) -> List[Dict[str, Any]]:
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
        q = client.table(table).select("*")
        if order_by:
            q = q.order(order_by)
        res = q.range(start, start + page - 1).execute()
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


def quality_from_enrollment(row: Dict[str, Any]) -> Dict[str, Any]:
    analysis = _jsonish(row.get("analysis_result"))
    sq = nested(row, "analysis_result", "seed_quality") if isinstance(analysis, dict) else None
    sq = sq if isinstance(sq, dict) else {}
    return {
        "enrollment_id": row.get("id") or row.get("enrollment_id"),
        "participant_id": row.get("participant_id"),
        "seed_label": row.get("seed_label"),
        "quality_score": safe_float(row.get("seed_quality_score"), safe_float(sq.get("quality_score"))),
        "quality_label": row.get("seed_quality_label") or sq.get("quality_label") or "unknown",
        "quality_hard_reject": safe_bool(row.get("seed_quality_hard_reject") or sq.get("hard_reject")),
        "complexity_class": row.get("complexity_class") or nested(row, "analysis_result", "complexity_class") or "unknown",
        "scene_stability_score": safe_float(row.get("scene_stability_score"), safe_float(nested(row, "analysis_result", "scene_stability_score"))),
        "timing_stability_score": safe_float(row.get("timing_stability_score"), safe_float(nested(row, "analysis_result", "timing_stability_score"))),
        "warnings": row.get("warnings") or ";".join((sq.get("warnings") or []) if isinstance(sq.get("warnings"), list) else []),
        "recommendations": row.get("recommendations") or ";".join((sq.get("recommendations") or []) if isinstance(sq.get("recommendations"), list) else []),
    }


def summarize_attempts(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    owner = [r for r in rows if r.get("attempt_type") in POSITIVE_TYPES]
    core = [r for r in rows if r.get("attempt_type") in CORE_NEGATIVE_TYPES]
    stress = [r for r in rows if r.get("attempt_type") in STRESS_NEGATIVE_TYPES]
    informed = [r for r in rows if r.get("attempt_type") == "informed_forgery"]
    blind = [r for r in rows if r.get("attempt_type") == "blind_impostor"]
    true_wrong = [r for r in rows if r.get("attempt_type") == "true_wrong_shape"]
    def accepted(xs: List[Dict[str, Any]]) -> int:
        return sum(safe_bool(x.get("accepted")) for x in xs)
    def rate(xs: List[Dict[str, Any]]) -> Optional[float]:
        return accepted(xs) / len(xs) if xs else None
    return {
        "verification_attempts": len(rows),
        "owner_attempts": len(owner),
        "owner_accepted": accepted(owner),
        "owner_tpr": rate(owner),
        "owner_frr": 1 - rate(owner) if rate(owner) is not None else None,
        "core_negative_attempts": len(core),
        "core_negative_accepted": accepted(core),
        "core_far": rate(core),
        "informed_forgery_attempts": len(informed),
        "informed_forgery_accepted": accepted(informed),
        "informed_forgery_far": rate(informed),
        "blind_impostor_attempts": len(blind),
        "blind_impostor_accepted": accepted(blind),
        "blind_impostor_far": rate(blind),
        "true_wrong_shape_attempts": len(true_wrong),
        "true_wrong_shape_accepted": accepted(true_wrong),
        "true_wrong_shape_far": rate(true_wrong),
        "stress_attempts": len(stress),
        "stress_accepted": accepted(stress),
        "stress_accept_rate": rate(stress),
    }


def risk_label(q: Dict[str, Any], s: Dict[str, Any]) -> str:
    if s.get("informed_forgery_accepted", 0) > 0:
        return "forgeable_template"
    if s.get("core_negative_accepted", 0) > 0:
        return "core_false_accept_risk"
    if q.get("quality_hard_reject"):
        return "quality_reject_candidate"
    if (q.get("quality_score") or 0) < 45:
        return "weak_seed_candidate"
    if s.get("owner_attempts", 0) >= 2 and (s.get("owner_tpr") is not None) and s.get("owner_tpr") < 0.60:
        return "owner_unstable"
    if s.get("stress_accepted", 0) > 0:
        return "concept_variant_sensitive"
    if (q.get("quality_score") or 0) >= 70 and (s.get("owner_tpr") or 0) >= 0.80:
        return "stable_good_seed"
    return "needs_more_data"


def bucket_label(score: Optional[float], label: str) -> str:
    if label and label != "unknown":
        return label
    if score is None:
        return "unknown"
    if score >= 80:
        return "excellent"
    if score >= 65:
        return "good"
    if score >= 50:
        return "borderline"
    return "weak"


def fmt(v: Any) -> str:
    if v is None:
        return "n/a"
    try:
        return f"{float(v):.3f}"
    except Exception:
        return str(v)


def frac(num: Any, den: Any) -> str:
    try:
        return f"{int(num)}/{int(den)}"
    except Exception:
        return "n/a"


def evidence_note(attempts: int, rate_name: str) -> str:
    if attempts == 0:
        return f"no {rate_name} attempts"
    if attempts < 5:
        return f"low evidence: {attempts} {rate_name} attempts"
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["supabase", "files"], default="supabase")
    ap.add_argument("--enrollment-table", default=os.environ.get("ENROLLMENT_TABLE", "drawing_seed_enrollments"))
    ap.add_argument("--verification-table", default=os.environ.get("VERIFICATION_TABLE", "drawing_seed_verifications"))
    ap.add_argument("--quality-csv", type=Path, help="CSV from run_seed_quality_backfill.py")
    ap.add_argument("--enrollments-json", type=Path, help="JSON/JSONL enrollment export")
    ap.add_argument("--verifications-json", type=Path, help="JSON/JSONL verification export")
    ap.add_argument("--out", type=Path, default=Path("results") / "seed_quality")
    args = ap.parse_args()

    if args.quality_csv:
        quality_rows = [dict(r) for r in load_csv_rows(args.quality_csv)]
    elif args.source == "files" and args.enrollments_json:
        quality_rows = [quality_from_enrollment(r) for r in load_json_rows(args.enrollments_json)]
    else:
        quality_rows = [quality_from_enrollment(r) for r in load_supabase_table(args.enrollment_table, order_by="created_at")]

    if args.source == "files" and args.verifications_json:
        verification_rows = load_json_rows(args.verifications_json)
    else:
        verification_rows = load_supabase_table(args.verification_table, order_by="created_at")

    quality_by_id: Dict[str, Dict[str, Any]] = {}
    for q in quality_rows:
        eid = str(q.get("id") or q.get("enrollment_id") or "")
        if eid:
            # Normalize CSV field names from backfill script.
            quality_by_id[eid] = {
                "enrollment_id": eid,
                "participant_id": q.get("participant_id"),
                "seed_label": q.get("seed_label"),
                "quality_score": safe_float(q.get("quality_score"), safe_float(q.get("seed_quality_score"))),
                "quality_label": q.get("quality_label") or q.get("seed_quality_label") or "unknown",
                "quality_hard_reject": safe_bool(q.get("quality_hard_reject") or q.get("seed_quality_hard_reject")),
                "complexity_class": q.get("complexity_class") or "unknown",
                "scene_stability_score": safe_float(q.get("scene_stability_score")),
                "timing_stability_score": safe_float(q.get("timing_stability_score")),
                "warnings": q.get("warnings"),
                "recommendations": q.get("recommendations"),
            }

    ver_by_enrollment: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in verification_rows:
        eid = str(r.get("enrollment_id") or "")
        if eid:
            ver_by_enrollment[eid].append(r)

    enrollment_report: List[Dict[str, Any]] = []
    all_ids = sorted(set(quality_by_id) | set(ver_by_enrollment))
    for eid in all_ids:
        q = quality_by_id.get(eid, {"enrollment_id": eid, "quality_label": "unknown"})
        s = summarize_attempts(ver_by_enrollment.get(eid, []))
        rec = {**q, **s}
        rec["risk_label"] = risk_label(q, s)
        enrollment_report.append(rec)

    # Aggregate by quality label and complexity class.
    def aggregate(group_field: str) -> List[Dict[str, Any]]:
        groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for rec in enrollment_report:
            key = str(rec.get(group_field) or "unknown")
            if group_field == "quality_label":
                key = bucket_label(rec.get("quality_score"), key)
            groups[key].append(rec)
        out: List[Dict[str, Any]] = []
        for key, xs in sorted(groups.items()):
            attempts = []
            # Summary by enrollment-level counts, not raw row reload.
            owner_attempts = sum(int(x.get("owner_attempts") or 0) for x in xs)
            owner_acc = sum(int(x.get("owner_accepted") or 0) for x in xs)
            core_attempts = sum(int(x.get("core_negative_attempts") or 0) for x in xs)
            core_acc = sum(int(x.get("core_negative_accepted") or 0) for x in xs)
            inf_attempts = sum(int(x.get("informed_forgery_attempts") or 0) for x in xs)
            inf_acc = sum(int(x.get("informed_forgery_accepted") or 0) for x in xs)
            scores = [x.get("quality_score") for x in xs if x.get("quality_score") is not None]
            blind_attempts = sum(int(x.get("blind_impostor_attempts") or 0) for x in xs)
            blind_acc = sum(int(x.get("blind_impostor_accepted") or 0) for x in xs)
            true_wrong_attempts = sum(int(x.get("true_wrong_shape_attempts") or 0) for x in xs)
            true_wrong_acc = sum(int(x.get("true_wrong_shape_accepted") or 0) for x in xs)
            stress_attempts = sum(int(x.get("stress_attempts") or 0) for x in xs)
            stress_acc = sum(int(x.get("stress_accepted") or 0) for x in xs)
            out.append({
                group_field: key,
                "enrollments": len(xs),
                "quality_score_mean": sum(scores) / len(scores) if scores else None,
                "owner_attempts": owner_attempts,
                "owner_accepted": owner_acc,
                "owner_tpr": owner_acc / owner_attempts if owner_attempts else None,
                "core_negative_attempts": core_attempts,
                "core_negative_accepted": core_acc,
                "core_far": core_acc / core_attempts if core_attempts else None,
                "informed_forgery_attempts": inf_attempts,
                "informed_forgery_accepted": inf_acc,
                "informed_forgery_far": inf_acc / inf_attempts if inf_attempts else None,
                "blind_impostor_attempts": blind_attempts,
                "blind_impostor_accepted": blind_acc,
                "blind_impostor_far": blind_acc / blind_attempts if blind_attempts else None,
                "true_wrong_shape_attempts": true_wrong_attempts,
                "true_wrong_shape_accepted": true_wrong_acc,
                "true_wrong_shape_far": true_wrong_acc / true_wrong_attempts if true_wrong_attempts else None,
                "stress_attempts": stress_attempts,
                "stress_accepted": stress_acc,
                "stress_accept_rate": stress_acc / stress_attempts if stress_attempts else None,
                "reject_candidates": sum(1 for x in xs if x.get("risk_label") in {"forgeable_template", "quality_reject_candidate", "weak_seed_candidate", "owner_unstable"}),
                "evidence_note": "; ".join(n for n in [evidence_note(inf_attempts, "informed-forgery"), evidence_note(core_attempts, "core-negative")] if n),
            })
        return out

    by_quality = aggregate("quality_label")
    by_complexity = aggregate("complexity_class")

    args.out.mkdir(parents=True, exist_ok=True)
    enrollment_fields = [
        "enrollment_id", "participant_id", "seed_label", "quality_score", "quality_label", "quality_hard_reject",
        "complexity_class", "scene_stability_score", "timing_stability_score", "owner_attempts", "owner_accepted",
        "owner_tpr", "owner_frr", "core_negative_attempts", "core_negative_accepted", "core_far",
        "informed_forgery_attempts", "informed_forgery_accepted", "informed_forgery_far", "blind_impostor_attempts",
        "blind_impostor_accepted", "true_wrong_shape_attempts", "true_wrong_shape_accepted", "stress_attempts", "stress_accepted",
        "stress_accept_rate", "risk_label", "warnings", "recommendations",
    ]
    write_csv(args.out / "seed_quality_by_enrollment.csv", enrollment_report, enrollment_fields)
    write_csv(args.out / "seed_quality_by_label.csv", by_quality, list(by_quality[0].keys()) if by_quality else ["quality_label"])
    write_csv(args.out / "seed_quality_by_complexity.csv", by_complexity, list(by_complexity[0].keys()) if by_complexity else ["complexity_class"])

    lines = [
        "# Drawing-RNG Seed Quality Report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "This report joins enrollment Seed Quality Score with verification outcomes. It is intended to answer: are false accepts and false rejects concentrated in weak templates?",
        "",
        "Rates are shown with denominators because small buckets can otherwise look misleadingly extreme; e.g. 1/1 informed forgery is reported as `1/1 (1.000)`, not just `1.000`.",
        "",
        "## By quality label",
        "",
        "| Quality | Enrollments | Mean quality | Owner | Core non-owner | Informed forgery | Blind | True wrong | Reject candidates | Evidence note |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in by_quality:
        lines.append(
            f"| {r['quality_label']} | {r['enrollments']} | {fmt(r['quality_score_mean'])} | "
            f"{frac(r['owner_accepted'], r['owner_attempts'])} ({fmt(r['owner_tpr'])}) | "
            f"{frac(r['core_negative_accepted'], r['core_negative_attempts'])} ({fmt(r['core_far'])}) | "
            f"{frac(r['informed_forgery_accepted'], r['informed_forgery_attempts'])} ({fmt(r['informed_forgery_far'])}) | "
            f"{frac(r['blind_impostor_accepted'], r['blind_impostor_attempts'])} ({fmt(r['blind_impostor_far'])}) | "
            f"{frac(r['true_wrong_shape_accepted'], r['true_wrong_shape_attempts'])} ({fmt(r['true_wrong_shape_far'])}) | "
            f"{r['reject_candidates']} | {r.get('evidence_note') or ''} |"
        )
    lines.extend([
        "",
        "## By complexity class",
        "",
        "| Complexity | Enrollments | Mean quality | Owner | Core non-owner | Informed forgery | Blind | True wrong | Reject candidates | Evidence note |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ])
    for r in by_complexity:
        lines.append(
            f"| {r['complexity_class']} | {r['enrollments']} | {fmt(r['quality_score_mean'])} | "
            f"{frac(r['owner_accepted'], r['owner_attempts'])} ({fmt(r['owner_tpr'])}) | "
            f"{frac(r['core_negative_accepted'], r['core_negative_attempts'])} ({fmt(r['core_far'])}) | "
            f"{frac(r['informed_forgery_accepted'], r['informed_forgery_attempts'])} ({fmt(r['informed_forgery_far'])}) | "
            f"{frac(r['blind_impostor_accepted'], r['blind_impostor_attempts'])} ({fmt(r['blind_impostor_far'])}) | "
            f"{frac(r['true_wrong_shape_accepted'], r['true_wrong_shape_attempts'])} ({fmt(r['true_wrong_shape_far'])}) | "
            f"{r['reject_candidates']} | {r.get('evidence_note') or ''} |"
        )
    risky = [r for r in enrollment_report if r.get("risk_label") in {"forgeable_template", "core_false_accept_risk", "quality_reject_candidate", "weak_seed_candidate", "owner_unstable"}]
    lines.extend([
        "",
        "## Highest-priority enrollments to inspect",
        "",
        "| Enrollment | Participant | Quality | Label | Complexity | Owner TPR | Informed accepts | Risk |",
        "|---|---|---:|---|---|---:|---:|---|",
    ])
    for r in sorted(risky, key=lambda x: (x.get("risk_label") != "forgeable_template", -(x.get("informed_forgery_accepted") or 0), x.get("quality_score") or 999))[:20]:
        lines.append(f"| {str(r.get('enrollment_id'))[:8]} | {r.get('participant_id') or ''} | {fmt(r.get('quality_score'))} | {r.get('quality_label')} | {r.get('complexity_class')} | {fmt(r.get('owner_tpr'))} | {r.get('informed_forgery_accepted') or 0} | {r.get('risk_label')} |")
    lines.extend([
        "",
        "## Output files",
        "",
        "- seed_quality_by_enrollment.csv",
        "- seed_quality_by_label.csv",
        "- seed_quality_by_complexity.csv",
        "",
    ])
    report_path = args.out / "seed_quality_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(report_path.read_text(encoding="utf-8"))
    print(f"Outputs written to: {args.out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
