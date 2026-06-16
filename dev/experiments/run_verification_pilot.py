#!/usr/bin/env python3
"""Run an initial read-only pilot analysis on Drawing-RNG verifications."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, Iterable, List, Optional, Tuple


POSITIVE_TYPES = {"owner_test"}
# Core security labels: use these for the headline FAR.  Keep wrong_shape/near_miss
# as diagnostic/stress labels unless they have been cleaned into true_wrong_shape.
CORE_NEGATIVE_TYPES = {"blind_impostor", "informed_forgery", "true_wrong_shape"}
STRESS_NEGATIVE_TYPES = {"wrong_shape", "near_miss", "concept_variant"}
EXCLUDED_TYPES = {"bad_sample", "ambiguous"}
NEGATIVE_TYPES = CORE_NEGATIVE_TYPES | STRESS_NEGATIVE_TYPES
KNOWN_TYPES = POSITIVE_TYPES | NEGATIVE_TYPES


def safe_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def quantile(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    fraction = position - low
    return ordered[low] * (1 - fraction) + ordered[high] * fraction


def wilson_interval(successes: int, total: int, z: float = 1.96) -> Tuple[Optional[float], Optional[float]]:
    if total <= 0:
        return None, None
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def auc_score(positive: List[float], negative: List[float]) -> Optional[float]:
    if not positive or not negative:
        return None
    total = 0.0
    comparisons = 0
    for pos in positive:
        for neg in negative:
            total += 1.0 if pos > neg else 0.5 if pos == neg else 0.0
            comparisons += 1
    return total / comparisons


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
        response = client.table(table).select("*").order("created_at").range(start, start + page_size - 1).execute()
        batch = response.data or []
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


def summarize_type(attempt_type: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    accepted = sum(bool(row.get("accepted")) for row in rows)
    scores = [score for row in rows if (score := safe_float(row.get("final_score"))) is not None]
    ci_low, ci_high = wilson_interval(accepted, len(rows))
    return {
        "attempt_type": attempt_type,
        "attempts": len(rows),
        "accepted": accepted,
        "rejected": len(rows) - accepted,
        "acceptance_rate": accepted / len(rows) if rows else None,
        "acceptance_ci95_low": ci_low,
        "acceptance_ci95_high": ci_high,
        "scored_attempts": len(scores),
        "score_mean": mean(scores) if scores else None,
        "score_median": median(scores) if scores else None,
        "score_p10": quantile(scores, 0.10),
        "score_p90": quantile(scores, 0.90),
        "unique_enrollments": len({row.get("enrollment_id") for row in rows if row.get("enrollment_id")}),
        "unique_participants": len({row.get("participant_id") for row in rows if row.get("participant_id")}),
    }


def threshold_sweep(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    labeled = []
    for row in rows:
        attempt_type = str(row.get("attempt_type") or "")
        score = safe_float(row.get("final_score"))
        if attempt_type in KNOWN_TYPES and score is not None:
            labeled.append((score, attempt_type in POSITIVE_TYPES))
    if not labeled:
        return []

    scores = sorted({score for score, _ in labeled})
    thresholds = [scores[0] - 1e-9, *scores, scores[-1] + 1e-9]
    output = []
    for threshold in thresholds:
        tp = sum(score >= threshold and genuine for score, genuine in labeled)
        fn = sum(score < threshold and genuine for score, genuine in labeled)
        fp = sum(score >= threshold and not genuine for score, genuine in labeled)
        tn = sum(score < threshold and not genuine for score, genuine in labeled)
        tpr = tp / (tp + fn) if tp + fn else None
        fpr = fp / (fp + tn) if fp + tn else None
        tnr = tn / (tn + fp) if tn + fp else None
        fnr = fn / (fn + tp) if fn + tp else None
        balanced = (tpr + tnr) / 2 if tpr is not None and tnr is not None else None
        output.append({
            "threshold": threshold,
            "tp": tp,
            "fn": fn,
            "fp": fp,
            "tn": tn,
            "tpr": tpr,
            "fnr": fnr,
            "fpr": fpr,
            "tnr": tnr,
            "balanced_accuracy": balanced,
        })
    return output


def best_balanced_threshold(rows: List[Dict[str, Any]]) -> Optional[float]:
    sweep = threshold_sweep(rows)
    if not sweep:
        return None
    best = max(sweep, key=lambda row: row["balanced_accuracy"] or -1)
    return safe_float(best.get("threshold"))


def evaluate_threshold(rows: List[Dict[str, Any]], threshold: float) -> Dict[str, Any]:
    labeled: List[Tuple[float, bool]] = []
    for row in rows:
        attempt_type = str(row.get("attempt_type") or "")
        score = safe_float(row.get("final_score"))
        if attempt_type in KNOWN_TYPES and score is not None:
            labeled.append((score, attempt_type in POSITIVE_TYPES))
    tp = sum(score >= threshold and genuine for score, genuine in labeled)
    fn = sum(score < threshold and genuine for score, genuine in labeled)
    fp = sum(score >= threshold and not genuine for score, genuine in labeled)
    tn = sum(score < threshold and not genuine for score, genuine in labeled)
    tpr = tp / (tp + fn) if tp + fn else None
    fpr = fp / (fp + tn) if fp + tn else None
    tnr = tn / (tn + fp) if tn + fp else None
    fnr = fn / (fn + tp) if fn + tp else None
    balanced = (tpr + tnr) / 2 if tpr is not None and tnr is not None else None
    return {"attempts": len(labeled), "tp": tp, "fn": fn, "fp": fp, "tn": tn, "tpr": tpr, "fnr": fnr, "fpr": fpr, "tnr": tnr, "balanced_accuracy": balanced}


def leave_one_enrollment_out(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    labeled = [row for row in rows if str(row.get("attempt_type") or "") in KNOWN_TYPES and safe_float(row.get("final_score")) is not None and row.get("enrollment_id")]
    enrollment_ids = sorted({str(row.get("enrollment_id")) for row in labeled})
    folds: List[Dict[str, Any]] = []
    totals = {"tp": 0, "fn": 0, "fp": 0, "tn": 0}
    for enrollment_id in enrollment_ids:
        train = [row for row in labeled if str(row.get("enrollment_id")) != enrollment_id]
        test = [row for row in labeled if str(row.get("enrollment_id")) == enrollment_id]
        threshold = best_balanced_threshold(train)
        if threshold is None or not test:
            continue
        metrics = evaluate_threshold(test, threshold)
        for key in totals:
            totals[key] += int(metrics[key])
        folds.append({"heldout_enrollment_id": enrollment_id, "train_attempts": len(train), "test_attempts": metrics["attempts"], "threshold_from_other_enrollments": threshold, **{k: metrics[k] for k in ["tp", "fn", "fp", "tn", "tpr", "fnr", "fpr", "tnr", "balanced_accuracy"]}})
    tp, fn, fp, tn = totals["tp"], totals["fn"], totals["fp"], totals["tn"]
    tpr = tp / (tp + fn) if tp + fn else None
    fpr = fp / (fp + tn) if fp + tn else None
    tnr = tn / (tn + fp) if tn + fp else None
    fnr = fn / (fn + tp) if fn + tp else None
    return folds, {"folds": len(folds), "attempts": tp + fn + fp + tn, "tp": tp, "fn": fn, "fp": fp, "tn": tn, "tpr": tpr, "fnr": fnr, "fpr": fpr, "tnr": tnr, "balanced_accuracy": (tpr + tnr) / 2 if tpr is not None and tnr is not None else None}


def risk_label_for_group(owner_rate: Optional[float], informed_accepts: int, core_negative_accepts: int, stress_accepts: int) -> str:
    if informed_accepts > 0:
        return "forgeable_template"
    if core_negative_accepts > 0:
        return "core_false_accept_risk"
    if owner_rate is not None and owner_rate < 0.55:
        return "owner_unstable"
    if stress_accepts > 0:
        return "concept_variant_sensitive"
    if owner_rate is not None and owner_rate >= 0.80:
        return "stable_good_seed"
    return "needs_more_data"


def summarize_group(key_name: str, key_value: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    genuine = [row for row in rows if row.get("attempt_type") in POSITIVE_TYPES]
    core_negative = [row for row in rows if row.get("attempt_type") in CORE_NEGATIVE_TYPES]
    diagnostic_negative = [row for row in rows if row.get("attempt_type") in NEGATIVE_TYPES]
    stress = [row for row in rows if row.get("attempt_type") in STRESS_NEGATIVE_TYPES]
    owner_accepted = sum(bool(row.get("accepted")) for row in genuine)
    core_negative_accepted = sum(bool(row.get("accepted")) for row in core_negative)
    diagnostic_negative_accepted = sum(bool(row.get("accepted")) for row in diagnostic_negative)
    stress_accepted = sum(bool(row.get("accepted")) for row in stress)
    near_miss = [row for row in rows if row.get("attempt_type") == "near_miss"]
    concept_variant = [row for row in rows if row.get("attempt_type") == "concept_variant"]
    informed = [row for row in rows if row.get("attempt_type") == "informed_forgery"]
    blind = [row for row in rows if row.get("attempt_type") == "blind_impostor"]
    true_wrong = [row for row in rows if row.get("attempt_type") == "true_wrong_shape"]
    wrong = [row for row in rows if row.get("attempt_type") == "wrong_shape"]
    owner_rate = owner_accepted / len(genuine) if genuine else None
    informed_accepts = sum(bool(row.get("accepted")) for row in informed)
    return {
        key_name: key_value,
        "attempts": len(rows),
        "owner_attempts": len(genuine),
        "owner_accepted": owner_accepted,
        "owner_acceptance_rate": owner_rate,
        "owner_frr": 1 - owner_rate if owner_rate is not None else None,
        "core_negative_attempts": len(core_negative),
        "core_negative_accepted": core_negative_accepted,
        "core_negative_acceptance_rate": core_negative_accepted / len(core_negative) if core_negative else None,
        "diagnostic_negative_attempts": len(diagnostic_negative),
        "diagnostic_negative_accepted": diagnostic_negative_accepted,
        "diagnostic_negative_acceptance_rate": diagnostic_negative_accepted / len(diagnostic_negative) if diagnostic_negative else None,
        "stress_attempts": len(stress),
        "stress_accepted": stress_accepted,
        "stress_acceptance_rate": stress_accepted / len(stress) if stress else None,
        "near_miss_attempts": len(near_miss),
        "near_miss_accepted": sum(bool(row.get("accepted")) for row in near_miss),
        "concept_variant_attempts": len(concept_variant),
        "concept_variant_accepted": sum(bool(row.get("accepted")) for row in concept_variant),
        "informed_forgery_attempts": len(informed),
        "informed_forgery_accepted": informed_accepts,
        "blind_impostor_attempts": len(blind),
        "blind_impostor_accepted": sum(bool(row.get("accepted")) for row in blind),
        "true_wrong_shape_attempts": len(true_wrong),
        "true_wrong_shape_accepted": sum(bool(row.get("accepted")) for row in true_wrong),
        "wrong_shape_legacy_attempts": len(wrong),
        "wrong_shape_legacy_accepted": sum(bool(row.get("accepted")) for row in wrong),
        "risk_label": risk_label_for_group(owner_rate, informed_accepts, core_negative_accepted, stress_accepted),
    }


def group_summaries(rows: List[Dict[str, Any]], field: str, output_key: str) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value = row.get(field)
        if value:
            grouped[str(value)].append(row)
    return [summarize_group(output_key, key, grouped[key]) for key in sorted(grouped)]


def fmt(value: Any, digits: int = 3) -> str:
    return "n/a" if value is None else f"{float(value):.{digits}f}"


def build_report(summary: Dict[str, Any], type_rows: List[Dict[str, Any]]) -> str:
    lines = [
        "# Drawing-RNG Initial Verification Pilot",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "## Dataset",
        "",
        f"- Verification attempts: {summary['attempts']}",
        f"- Unique enrollments: {summary['unique_enrollments']}",
        f"- Unique participants: {summary['unique_participants']}",
        f"- Attempts with known pilot labels and scores: {summary['labeled_scored_attempts']}",
        f"- Missing final score: {summary['missing_final_score']}",
        f"- Unknown or missing attempt type: {summary['unknown_attempt_type']}",
        "",
        "## Recorded Decisions",
        "",
        f"- Genuine acceptance rate (TPR): {fmt(summary['recorded_tpr'])}",
        f"- Genuine rejection rate (FRR): {fmt(summary['recorded_frr'])}",
        f"- Core non-genuine acceptance rate (clean FAR): {fmt(summary['recorded_core_far'])}",
        f"- Diagnostic/stress non-owner acceptance rate: {fmt(summary['recorded_diagnostic_far'])}",
        f"- Stress/legacy accept rate, excluded from headline FAR: {fmt(summary['recorded_stress_accept_rate'])}",
        "",
        "## Diagnostic final_score Separation",
        "",
        "These numbers are diagnostic only. The unlock decision should remain the hard gate decision, not a single blended-score threshold.",
        "The headline FAR excludes stress/legacy labels such as near_miss, concept_variant, and uncleaned wrong_shape.",
        "",
        f"- Genuine vs non-genuine AUC: {fmt(summary['auc'])}",
        f"- Best balanced-accuracy threshold: {fmt(summary['best_threshold'])}",
        f"- Best balanced accuracy: {fmt(summary['best_balanced_accuracy'])}",
        f"- Approximate EER threshold: {fmt(summary['eer_threshold'])}",
        f"- Approximate EER: {fmt(summary['eer'])}",
        "",
        "## Attempt Categories",
        "",
        "| Type | Attempts | Accepted | Acceptance rate | Median score |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in type_rows:
        lines.append(
            f"| {row['attempt_type']} | {row['attempts']} | {row['accepted']} | "
            f"{fmt(row['acceptance_rate'])} | {fmt(row['score_median'])} |"
        )
    loo = summary.get("leave_one_enrollment_out") or {}
    lines.extend([
        "",
        "## Leave-One-Enrollment-Out Diagnostic Thresholding",
        "",
        "This evaluates final_score thresholds without choosing and testing the threshold on the same enrollment.",
        f"- Folds: {loo.get('folds', 0)}",
        f"- Held-out attempts: {loo.get('attempts', 0)}",
        f"- Held-out TPR: {fmt(loo.get('tpr'))}",
        f"- Held-out FPR: {fmt(loo.get('fpr'))}",
        f"- Held-out balanced accuracy: {fmt(loo.get('balanced_accuracy'))}",
        "",
        "## Participant / Enrollment Variance",
        "",
        "- Per-participant summaries written to participant_summary.csv",
        "- Per-enrollment summaries written to enrollment_summary.csv",
        "",
        "## Interpretation Limits",
        "",
        "- This is a descriptive pilot, not a production security evaluation.",
        "- Repeated attempts from one participant or enrollment are correlated and are not independent observations.",
        "- Attempt types are operator labels; inconsistent labeling directly changes FAR/FRR estimates.",
        "- Threshold selection and evaluation use the same pilot data, so the reported optimum is optimistic.",
        "- Report confidence intervals and per-participant results before making security claims.",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["supabase", "json"], default="supabase")
    parser.add_argument("--input", type=Path, help="JSON or JSONL verification export.")
    parser.add_argument("--table", default=os.environ.get("VERIFICATION_TABLE", "drawing_seed_verifications"))
    parser.add_argument("--out", type=Path, default=Path("results") / "verification_pilot")
    args = parser.parse_args()

    if args.source == "json":
        if not args.input:
            raise SystemExit("--input is required for --source json")
        rows = load_json_rows(args.input)
    else:
        rows = load_supabase_rows(args.table)

    args.out.mkdir(parents=True, exist_ok=True)
    by_type: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_type[str(row.get("attempt_type") or "unknown")].append(row)
    type_rows = [summarize_type(key, by_type[key]) for key in sorted(by_type)]
    participant_rows = group_summaries(rows, "participant_id", "participant_id")
    enrollment_rows = group_summaries(rows, "enrollment_id", "enrollment_id")
    loo_rows, loo_summary = leave_one_enrollment_out(rows)

    genuine = [row for row in rows if row.get("attempt_type") in POSITIVE_TYPES]
    core_negative = [row for row in rows if row.get("attempt_type") in CORE_NEGATIVE_TYPES]
    diagnostic_negative = [row for row in rows if row.get("attempt_type") in NEGATIVE_TYPES]
    stress_negative = [row for row in rows if row.get("attempt_type") in STRESS_NEGATIVE_TYPES]
    genuine_scores = [score for row in genuine if (score := safe_float(row.get("final_score"))) is not None]
    negative_scores = [score for row in diagnostic_negative if (score := safe_float(row.get("final_score"))) is not None]
    sweep = threshold_sweep(rows)
    best = max(sweep, key=lambda row: row["balanced_accuracy"] or -1) if sweep else None
    eer_row = min(sweep, key=lambda row: abs((row["fnr"] or 0) - (row["fpr"] or 0))) if sweep else None

    genuine_accepted = sum(bool(row.get("accepted")) for row in genuine)
    core_negative_accepted = sum(bool(row.get("accepted")) for row in core_negative)
    diagnostic_negative_accepted = sum(bool(row.get("accepted")) for row in diagnostic_negative)
    stress_negative_accepted = sum(bool(row.get("accepted")) for row in stress_negative)
    known_scored = sum(
        row.get("attempt_type") in KNOWN_TYPES and safe_float(row.get("final_score")) is not None
        for row in rows
    )
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "attempts": len(rows),
        "unique_enrollments": len({row.get("enrollment_id") for row in rows if row.get("enrollment_id")}),
        "unique_participants": len({row.get("participant_id") for row in rows if row.get("participant_id")}),
        "attempt_type_counts": dict(Counter(str(row.get("attempt_type") or "unknown") for row in rows)),
        "labeled_scored_attempts": known_scored,
        "missing_final_score": sum(safe_float(row.get("final_score")) is None for row in rows),
        "unknown_attempt_type": sum(str(row.get("attempt_type") or "unknown") not in KNOWN_TYPES for row in rows),
        "recorded_tpr": genuine_accepted / len(genuine) if genuine else None,
        "recorded_frr": 1 - genuine_accepted / len(genuine) if genuine else None,
        "recorded_core_far": core_negative_accepted / len(core_negative) if core_negative else None,
        "recorded_diagnostic_far": diagnostic_negative_accepted / len(diagnostic_negative) if diagnostic_negative else None,
        "recorded_stress_accept_rate": stress_negative_accepted / len(stress_negative) if stress_negative else None,
        "core_negative_attempts": len(core_negative),
        "core_negative_accepted": core_negative_accepted,
        "diagnostic_negative_attempts": len(diagnostic_negative),
        "diagnostic_negative_accepted": diagnostic_negative_accepted,
        "stress_negative_attempts": len(stress_negative),
        "stress_negative_accepted": stress_negative_accepted,
        "auc": auc_score(genuine_scores, negative_scores),
        "best_threshold": best["threshold"] if best else None,
        "best_balanced_accuracy": best["balanced_accuracy"] if best else None,
        "best_threshold_tpr": best["tpr"] if best else None,
        "best_threshold_fpr": best["fpr"] if best else None,
        "eer_threshold": eer_row["threshold"] if eer_row else None,
        "eer": ((eer_row["fnr"] + eer_row["fpr"]) / 2) if eer_row else None,
        "leave_one_enrollment_out": loo_summary,
    }

    (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_csv(args.out / "attempt_type_summary.csv", type_rows, list(type_rows[0]) if type_rows else ["attempt_type"])
    write_csv(args.out / "participant_summary.csv", participant_rows, list(participant_rows[0]) if participant_rows else ["participant_id"])
    write_csv(args.out / "enrollment_summary.csv", enrollment_rows, list(enrollment_rows[0]) if enrollment_rows else ["enrollment_id"])
    write_csv(args.out / "leave_one_enrollment_out.csv", loo_rows, list(loo_rows[0]) if loo_rows else ["heldout_enrollment_id"])
    write_csv(
        args.out / "threshold_sweep.csv",
        sweep,
        ["threshold", "tp", "fn", "fp", "tn", "tpr", "fnr", "fpr", "tnr", "balanced_accuracy"],
    )
    (args.out / "pilot_report.md").write_text(build_report(summary, type_rows), encoding="utf-8")

    print(build_report(summary, type_rows))
    print(f"Outputs written to: {args.out.resolve()}")


if __name__ == "__main__":
    main()
