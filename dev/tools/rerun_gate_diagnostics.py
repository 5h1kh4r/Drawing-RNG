#!/usr/bin/env python3
from __future__ import annotations

"""Rerun every stored verification against its linked enrollment.

Outputs:
- gate_attempts.csv: one row per re-evaluated verification
- gate_failure_summary.csv: pass/fail frequency by attempt type and gate
- gate_failure_combinations.csv: common failed hard-gate combinations
- enrollment_summary.csv: owner/attacker performance per enrollment
- ablation_summary.csv: counterfactual policy comparison
- skipped_rows.csv: records that could not be re-evaluated
- summary.json and report.md

The script is read-only. It never overwrites historical verification rows and
never exports derived passwords, secret_hex, seed_hex, or reusable outputs.
"""

import argparse
import csv
import json
import math
import os
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from drawing_rng.enrollment import verify_redraw
from drawing_rng.gate_trace import build_gate_trace

KNOWN_TYPES = {
    "owner_test",
    "blind_impostor",
    "informed_forgery",
    "near_miss",
    "true_wrong_shape",
    "wrong_shape",
    "concept_variant",
}
EXCLUDED_TYPES = {"bad_sample", "ambiguous", "step_up_component"}


def jsonish(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def safe_float(value: Any) -> Optional[float]:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def safe_bool(value: Any) -> bool:
    value = jsonish(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y", "t"}
    return bool(value)


def effective_label(row: Dict[str, Any]) -> str:
    return str(
        row.get("relabeled_attempt_type")
        or row.get("attempt_type")
        or "unknown"
    )


def load_json_list(path: Path, keys: Iterable[str]) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in keys:
            rows = data.get(key)
            if isinstance(rows, list):
                return rows
    raise ValueError(f"Could not find a row list in {path}")


def load_supabase_table(client: Any, table: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    start = 0
    page_size = 1000
    while True:
        response = (
            client.table(table)
            .select("*")
            .order("created_at")
            .range(start, start + page_size - 1)
            .execute()
        )
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return rows


def load_supabase(
    enrollment_table: str,
    verification_table: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SystemExit(
            "Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY. "
            "Run this only from the local dev environment."
        )
    client = create_client(url, key)
    return (
        load_supabase_table(client, enrollment_table),
        load_supabase_table(client, verification_table),
    )


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: List[str] = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def git_revision() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        value = proc.stdout.strip()
        return value if proc.returncode == 0 and value else "unknown"
    except Exception:
        return "unknown"


def gate_pass(trace: Dict[str, Any], name: str) -> Optional[bool]:
    gate = (trace.get("gates") or {}).get(name) or {}
    if not gate.get("applicable"):
        return None
    value = gate.get("passed")
    return bool(value) if value is not None else None


def all_hard_except(trace: Dict[str, Any], excluded: Iterable[str]) -> bool:
    excluded_set = set(excluded)
    gates = trace.get("gates") or {}
    for name in trace.get("hard_gate_names") or []:
        if name in excluded_set:
            continue
        gate = gates.get(name) or {}
        if gate.get("applicable") and gate.get("hard_gate") and gate.get("passed") is not True:
            return False
    return True


def policy_decisions(result: Dict[str, Any], trace: Dict[str, Any]) -> Dict[str, bool]:
    token_only = gate_pass(trace, "token") is True
    geometry_names = {
        name
        for name in (trace.get("hard_gate_names") or [])
        if name not in {"token", "step_up", "fuzzy_recovery"}
    }
    geometry_only = all(
        gate_pass(trace, name) is True for name in geometry_names
    )
    final_score = safe_float(result.get("final_score")) or 0.0

    return {
        "recorded_original": False,  # replaced by caller
        "rerun_final": safe_bool(result.get("accepted")),
        "rerun_primary_without_stepup": safe_bool(result.get("primary_accepted")),
        "token_only": token_only,
        "geometry_or_scene_only": geometry_only,
        "final_score_0_68": final_score >= 0.68,
        "full_without_topology": all_hard_except(trace, {"topology"}),
        "full_without_curve": all_hard_except(trace, {"curve"}),
        "full_without_stroke_shape": all_hard_except(trace, {"stroke_shape"}),
        "full_without_closed_style": all_hard_except(trace, {"closed_style"}),
    }


def flatten_attempt(
    verification: Dict[str, Any],
    result: Dict[str, Any],
    trace: Dict[str, Any],
    algorithm_version: str,
) -> Dict[str, Any]:
    geometry = result.get("geometry_scores") or {}
    scene = result.get("scene_scores") or {}
    fuzzy = result.get("fuzzy_recovery") or {}
    gates = trace.get("gates") or {}
    policies = policy_decisions(result, trace)
    policies["recorded_original"] = safe_bool(verification.get("accepted"))

    row: Dict[str, Any] = {
        "verification_id": verification.get("id"),
        "enrollment_id": verification.get("enrollment_id"),
        "participant_id": verification.get("participant_id"),
        "seed_label": verification.get("seed_label"),
        "attempt_type": effective_label(verification),
        "original_attempt_type": verification.get("attempt_type"),
        "recorded_accepted": safe_bool(verification.get("accepted")),
        "rerun_accepted": safe_bool(result.get("accepted")),
        "rerun_primary_accepted": safe_bool(result.get("primary_accepted")),
        "decision_changed": safe_bool(verification.get("accepted")) != safe_bool(result.get("accepted")),
        "algorithm_version": algorithm_version,
        "profile": result.get("profile"),
        "complex_scene_mode": safe_bool(result.get("complex_scene_mode")),
        "shape_case": trace.get("shape_case"),
        "token_score": result.get("token_score"),
        "token_score_weighted": result.get("token_score_weighted"),
        "token_bigram_score": result.get("token_bigram_score"),
        "token_threshold": result.get("token_threshold"),
        "final_score": result.get("final_score"),
        "geometry_final": geometry.get("geometry_final"),
        "count_score": geometry.get("count"),
        "layout_score": geometry.get("layout"),
        "relation_score": geometry.get("relation"),
        "topology_score": geometry.get("topology"),
        "curve_score": geometry.get("curve"),
        "stroke_shape_score": geometry.get("stroke_shape"),
        "closed_style_score": geometry.get("closed_style"),
        "scene_final": scene.get("scene_final"),
        "scene_assignment": scene.get("scene_assignment"),
        "scene_raster": scene.get("scene_raster"),
        "scene_relation": scene.get("scene_relation"),
        "fuzzy_ok": fuzzy.get("ok") if isinstance(fuzzy, dict) else None,
        "fuzzy_hamming_distance": fuzzy.get("hamming_distance") if isinstance(fuzzy, dict) else None,
        "step_up_required": safe_bool(result.get("step_up_required")),
        "failed_hard_gates": "|".join(trace.get("failed_hard_gates") or []),
        "failure_reasons": "|".join(str(x) for x in (result.get("failure_reasons") or [])),
        "geometry_failure_reasons": "|".join(
            str(x) for x in (result.get("geometry_failure_reasons_diagnostic") or [])
        ),
        "scene_failure_reasons": "|".join(
            str(x) for x in (result.get("scene_failure_reasons") or [])
        ),
    }

    for name, gate in gates.items():
        row[f"{name}_applicable"] = gate.get("applicable")
        row[f"{name}_hard_gate"] = gate.get("hard_gate")
        row[f"{name}_pass"] = gate.get("passed")
        row[f"{name}_score"] = gate.get("score")
        row[f"{name}_threshold"] = gate.get("threshold")

    for name, decision in policies.items():
        row[f"policy_{name}"] = decision
    return row


def summarize_gates(attempts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, str], Dict[str, int]] = defaultdict(
        lambda: {"attempts": 0, "applicable": 0, "passed": 0, "failed": 0}
    )
    gate_names = sorted({
        key[:-11]
        for row in attempts
        for key in row
        if key.endswith("_applicable")
    })
    for row in attempts:
        label = str(row.get("attempt_type") or "unknown")
        for gate in gate_names:
            stats = grouped[(label, gate)]
            stats["attempts"] += 1
            if row.get(f"{gate}_applicable") is True:
                stats["applicable"] += 1
                if row.get(f"{gate}_pass") is True:
                    stats["passed"] += 1
                elif row.get(f"{gate}_pass") is False:
                    stats["failed"] += 1

    output = []
    for (label, gate), stats in sorted(grouped.items()):
        applicable = stats["applicable"]
        output.append({
            "attempt_type": label,
            "gate": gate,
            **stats,
            "pass_rate": stats["passed"] / applicable if applicable else None,
            "failure_rate": stats["failed"] / applicable if applicable else None,
        })
    return output


def summarize_failure_combinations(attempts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counter: Counter[Tuple[str, str]] = Counter()
    for row in attempts:
        label = str(row.get("attempt_type") or "unknown")
        combo = str(row.get("failed_hard_gates") or "none")
        counter[(label, combo)] += 1
    return [
        {"attempt_type": label, "failed_hard_gates": combo, "count": count}
        for (label, combo), count in counter.most_common()
    ]


def summarize_enrollments(attempts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in attempts:
        enrollment_id = row.get("enrollment_id")
        if enrollment_id:
            groups[str(enrollment_id)].append(row)

    output = []
    for enrollment_id, rows in sorted(groups.items()):
        def category(label: str) -> List[Dict[str, Any]]:
            return [row for row in rows if row.get("attempt_type") == label]

        owner = category("owner_test")
        informed = category("informed_forgery")
        blind = category("blind_impostor")
        near = category("near_miss")
        true_wrong = category("true_wrong_shape")

        def accepted(xs: List[Dict[str, Any]]) -> int:
            return sum(bool(x.get("rerun_accepted")) for x in xs)

        owner_scores = [
            value for row in owner
            if (value := safe_float(row.get("token_score"))) is not None
        ]
        informed_scores = [
            value for row in informed
            if (value := safe_float(row.get("token_score"))) is not None
        ]

        output.append({
            "enrollment_id": enrollment_id,
            "participant_id": next((r.get("participant_id") for r in rows if r.get("participant_id")), None),
            "seed_label": next((r.get("seed_label") for r in rows if r.get("seed_label")), None),
            "attempts": len(rows),
            "owner_attempts": len(owner),
            "owner_accepted": accepted(owner),
            "owner_acceptance_rate": accepted(owner) / len(owner) if owner else None,
            "owner_median_token_score": median(owner_scores) if owner_scores else None,
            "informed_attempts": len(informed),
            "informed_accepted": accepted(informed),
            "informed_acceptance_rate": accepted(informed) / len(informed) if informed else None,
            "informed_median_token_score": median(informed_scores) if informed_scores else None,
            "blind_attempts": len(blind),
            "blind_accepted": accepted(blind),
            "near_miss_attempts": len(near),
            "near_miss_accepted": accepted(near),
            "true_wrong_shape_attempts": len(true_wrong),
            "true_wrong_shape_accepted": accepted(true_wrong),
        })
    return output


def summarize_ablation(attempts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    policy_names = sorted({
        key[len("policy_"):]
        for row in attempts
        for key in row
        if key.startswith("policy_")
    })
    output = []

    for policy in policy_names:
        evaluable = [
            row for row in attempts
            if row.get("attempt_type") in KNOWN_TYPES
            and isinstance(row.get(f"policy_{policy}"), bool)
        ]
        owner = [r for r in evaluable if r.get("attempt_type") == "owner_test"]
        informed = [r for r in evaluable if r.get("attempt_type") == "informed_forgery"]
        blind = [r for r in evaluable if r.get("attempt_type") == "blind_impostor"]
        near = [r for r in evaluable if r.get("attempt_type") == "near_miss"]
        true_wrong = [r for r in evaluable if r.get("attempt_type") == "true_wrong_shape"]

        def rate(rows: List[Dict[str, Any]]) -> Optional[float]:
            return (
                sum(bool(r.get(f"policy_{policy}")) for r in rows) / len(rows)
                if rows
                else None
            )

        output.append({
            "policy": policy,
            "evaluable_attempts": len(evaluable),
            "owner_attempts": len(owner),
            "owner_tpr": rate(owner),
            "owner_frr": 1 - rate(owner) if rate(owner) is not None else None,
            "informed_attempts": len(informed),
            "informed_far": rate(informed),
            "blind_attempts": len(blind),
            "blind_far": rate(blind),
            "near_miss_attempts": len(near),
            "near_miss_accept_rate": rate(near),
            "true_wrong_shape_attempts": len(true_wrong),
            "true_wrong_shape_far": rate(true_wrong),
        })
    return output


def format_rate(value: Any) -> str:
    number = safe_float(value)
    return "n/a" if number is None else f"{100 * number:.1f}%"


def build_report(
    attempts: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
    ablation: List[Dict[str, Any]],
    algorithm_version: str,
) -> str:
    owner = [r for r in attempts if r.get("attempt_type") == "owner_test"]
    informed = [r for r in attempts if r.get("attempt_type") == "informed_forgery"]
    blind = [r for r in attempts if r.get("attempt_type") == "blind_impostor"]

    def rate(rows: List[Dict[str, Any]]) -> Optional[float]:
        return (
            sum(bool(r.get("rerun_accepted")) for r in rows) / len(rows)
            if rows
            else None
        )

    lines = [
        "# Draw2Seed Gate Diagnostics",
        "",
        f"- Generated: {datetime.now(timezone.utc).isoformat()}",
        f"- Algorithm revision: `{algorithm_version}`",
        f"- Re-evaluated attempts: {len(attempts)}",
        f"- Skipped rows: {len(skipped)}",
        f"- Owner acceptance: {format_rate(rate(owner))} ({sum(bool(r.get('rerun_accepted')) for r in owner)}/{len(owner)})",
        f"- Informed-forgery acceptance: {format_rate(rate(informed))} ({sum(bool(r.get('rerun_accepted')) for r in informed)}/{len(informed)})",
        f"- Blind-impostor acceptance: {format_rate(rate(blind))} ({sum(bool(r.get('rerun_accepted')) for r in blind)}/{len(blind)})",
        "",
        "## Ablation",
        "",
        "| Policy | Owner TPR | Informed FAR | Blind FAR | Near-miss accept |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in ablation:
        lines.append(
            f"| {row['policy']} | {format_rate(row.get('owner_tpr'))} | "
            f"{format_rate(row.get('informed_far'))} | "
            f"{format_rate(row.get('blind_far'))} | "
            f"{format_rate(row.get('near_miss_accept_rate'))} |"
        )
    lines.extend([
        "",
        "## Interpretation",
        "",
        "- Use `gate_failure_summary.csv` for the grouped gate-failure bar chart.",
        "- Use `enrollment_summary.csv` for per-profile owner and forgery plots.",
        "- Use `ablation_summary.csv` to justify hard gating over blended-score unlocking.",
        "- A changed rerun decision means the historical row was collected under a different algorithm version or configuration; do not overwrite it.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["supabase", "json"], default="supabase")
    parser.add_argument("--enrollments-json", type=Path)
    parser.add_argument("--verifications-json", type=Path)
    parser.add_argument("--enrollment-table", default="drawing_seed_enrollments")
    parser.add_argument("--verification-table", default="drawing_seed_verifications")
    parser.add_argument("--out", type=Path, default=Path("results") / "gate_diagnostics_v1")
    parser.add_argument("--include-excluded", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    if args.source == "json":
        if not args.enrollments_json or not args.verifications_json:
            raise SystemExit(
                "--enrollments-json and --verifications-json are required with --source json"
            )
        enrollments = load_json_list(
            args.enrollments_json, ["enrollments", "rows", "data"]
        )
        verifications = load_json_list(
            args.verifications_json, ["verifications", "rows", "data"]
        )
    else:
        enrollments, verifications = load_supabase(
            args.enrollment_table, args.verification_table
        )

    enrollment_map = {
        str(row.get("id")): row
        for row in enrollments
        if row.get("id")
    }

    if args.limit is not None:
        verifications = verifications[: max(0, args.limit)]

    algorithm_version = git_revision()
    attempt_rows: List[Dict[str, Any]] = []
    skipped_rows: List[Dict[str, Any]] = []

    for index, verification in enumerate(verifications, start=1):
        label = effective_label(verification)
        if not args.include_excluded and label in EXCLUDED_TYPES:
            continue

        enrollment_id = verification.get("enrollment_id")
        enrollment = enrollment_map.get(str(enrollment_id))
        if not enrollment:
            skipped_rows.append({
                "verification_id": verification.get("id"),
                "enrollment_id": enrollment_id,
                "reason": "linked_enrollment_not_found",
            })
            continue

        enrollment_result = jsonish(enrollment.get("analysis_result"))
        redraw_strokes = jsonish(verification.get("redraw_strokes"))

        if not isinstance(enrollment_result, dict):
            skipped_rows.append({
                "verification_id": verification.get("id"),
                "enrollment_id": enrollment_id,
                "reason": "invalid_or_missing_analysis_result",
            })
            continue
        if not isinstance(redraw_strokes, list) or not redraw_strokes:
            skipped_rows.append({
                "verification_id": verification.get("id"),
                "enrollment_id": enrollment_id,
                "reason": "invalid_or_missing_redraw_strokes",
            })
            continue

        try:
            result = verify_redraw(
                enrollment_result,
                redraw_strokes,
                fuzzy_required=False,
            )
            trace = result.get("gate_trace")
            if not isinstance(trace, dict):
                trace = build_gate_trace(result)
            attempt_rows.append(
                flatten_attempt(
                    verification,
                    result,
                    trace,
                    algorithm_version,
                )
            )
        except Exception as exc:
            skipped_rows.append({
                "verification_id": verification.get("id"),
                "enrollment_id": enrollment_id,
                "reason": "verifier_error",
                "error": f"{type(exc).__name__}: {exc}",
            })

        if index % 25 == 0:
            print(f"Processed {index}/{len(verifications)} verification rows...")

    gate_summary = summarize_gates(attempt_rows)
    combinations = summarize_failure_combinations(attempt_rows)
    enrollment_summary = summarize_enrollments(attempt_rows)
    ablation = summarize_ablation(attempt_rows)

    args.out.mkdir(parents=True, exist_ok=True)
    write_csv(args.out / "gate_attempts.csv", attempt_rows)
    write_csv(args.out / "gate_failure_summary.csv", gate_summary)
    write_csv(args.out / "gate_failure_combinations.csv", combinations)
    write_csv(args.out / "enrollment_summary.csv", enrollment_summary)
    write_csv(args.out / "ablation_summary.csv", ablation)
    write_csv(args.out / "skipped_rows.csv", skipped_rows)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "algorithm_version": algorithm_version,
        "source_enrollments": len(enrollments),
        "source_verifications": len(verifications),
        "re_evaluated_attempts": len(attempt_rows),
        "skipped_rows": len(skipped_rows),
        "attempt_type_counts": dict(Counter(
            str(row.get("attempt_type") or "unknown") for row in attempt_rows
        )),
        "changed_decisions": sum(bool(row.get("decision_changed")) for row in attempt_rows),
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    report = build_report(
        attempt_rows, skipped_rows, ablation, algorithm_version
    )
    (args.out / "report.md").write_text(report, encoding="utf-8")

    print(report)
    print(f"\nOutputs written to: {args.out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
