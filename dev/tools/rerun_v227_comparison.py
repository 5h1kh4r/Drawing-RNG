#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from drawing_rng.v22_wrapper import analyze_enrollment_v22, verify_redraw_v22

try:
    from supabase import create_client
except Exception as exc:
    raise SystemExit(f"Install dev requirements first; Supabase import failed: {exc}")

SECRET_KEYS = {
    "demo_password",
    "seed_hex",
    "secret_hex",
    "secret_hex_for_demo_only",
    "canonical_seed_material",
}


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_json(value: Any, fallback: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return value if value is not None else fallback


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[redacted]" if key in SECRET_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def code_hash() -> str:
    digest = hashlib.sha256()
    package = SRC / "drawing_rng"
    for source_path in sorted(package.glob("*.py")):
        digest.update(source_path.name.encode("utf-8"))
        digest.update(source_path.read_bytes())
    return digest.hexdigest()[:20]


def fetch_all(client: Any, table: str, page_size: int = 500) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    start = 0
    while True:
        response = client.table(table).select("*").range(start, start + page_size - 1).execute()
        page = list(response.data or [])
        rows.extend(page)
        if len(page) < page_size:
            break
        start += page_size
    return rows


def history_list(row: Dict[str, Any]) -> List[Any]:
    value = parse_json(row.get("algorithm_history"), [])
    return list(value) if isinstance(value, list) else []


def canonical_attempt_type(value: Any) -> str:
    attempt_type = str(value or "bad_sample").strip()
    aliases = {
        "wrong_shape": "true_wrong_shape",
        "concept_variant": "near_miss",
        "ambiguous": "bad_sample",
    }
    return aliases.get(attempt_type, attempt_type)


def preserve_enrollment_secret_material(old_result: Dict[str, Any], new_result: Dict[str, Any]) -> None:
    for key in (
        "canonical_seed_material",
        "fuzzy_helper",
        "outputs",
        "fallback_canonical_outputs",
        "public_salt",
    ):
        if key in old_result and old_result.get(key) is not None:
            new_result[key] = old_result.get(key)


def enrollment_update_payload(
    row: Dict[str, Any],
    result: Dict[str, Any],
    algorithm_version: str,
    algorithm_code_hash: str,
    config_version: str,
    generated_at: str,
) -> Dict[str, Any]:
    history = history_list(row)
    history.append({
        "archived_at": generated_at,
        "algorithm_version": row.get("algorithm_version"),
        "algorithm_code_hash": row.get("algorithm_code_hash"),
        "config_version": row.get("config_version"),
        "accepted_for_demo": row.get("accepted_for_demo"),
        "analysis_result": row.get("analysis_result"),
    })
    seed_quality = result.get("seed_quality") or {}
    return {
        "algorithm_version": algorithm_version,
        "algorithm_code_hash": algorithm_code_hash,
        "config_version": config_version,
        "analysis_result": redact(result),
        "accepted_for_demo": result.get("accepted_for_demo"),
        "stability_score": result.get("stability_score"),
        "recommended_profile": result.get("recommended_profile"),
        "seed_quality_score": result.get("seed_quality_score", seed_quality.get("quality_score")),
        "seed_quality_label": result.get("seed_quality_label", seed_quality.get("quality_label")),
        "seed_quality_hard_reject": result.get("seed_quality_hard_reject", seed_quality.get("hard_reject")),
        "complexity_class": result.get("complexity_class"),
        "scene_stability_score": result.get("scene_stability_score"),
        "timing_stability_score": result.get("timing_stability_score"),
        "public_salt": result.get("public_salt") or row.get("public_salt"),
        "algorithm_history": history,
    }


def verification_update_payload(
    row: Dict[str, Any],
    result: Dict[str, Any],
    algorithm_version: str,
    algorithm_code_hash: str,
    config_version: str,
    generated_at: str,
) -> Dict[str, Any]:
    history = history_list(row)
    history.append({
        "archived_at": generated_at,
        "algorithm_version": row.get("algorithm_version"),
        "algorithm_code_hash": row.get("algorithm_code_hash"),
        "config_version": row.get("config_version"),
        "accepted": row.get("accepted"),
        "attempt_type": row.get("attempt_type"),
        "verification_result": row.get("verification_result"),
    })
    geometry = result.get("geometry_scores") or {}
    scene = result.get("scene_scores") or {}
    timing = result.get("timing_scores") or {}
    fuzzy = result.get("fuzzy_recovery") or {}
    return {
        "algorithm_version": algorithm_version,
        "algorithm_code_hash": algorithm_code_hash,
        "config_version": config_version,
        "attempt_type": canonical_attempt_type(row.get("attempt_type")),
        "verification_result": redact(result),
        "accepted": result.get("accepted"),
        "primary_accepted": result.get("primary_accepted", result.get("accepted")),
        "profile": result.get("profile"),
        "final_score": result.get("final_score", result.get("score")),
        "token_score": result.get("token_score"),
        "token_score_weighted": result.get("token_score_weighted"),
        "token_bigram_score": result.get("token_bigram_score"),
        "geometry_final": geometry.get("geometry_final"),
        "component_count_score": geometry.get("count"),
        "layout_score": geometry.get("layout"),
        "relation_score": geometry.get("relation"),
        "topology_score": geometry.get("topology"),
        "curve_score": geometry.get("curve"),
        "stroke_shape_score": geometry.get("stroke_shape"),
        "closed_style_score": geometry.get("closed_style"),
        "complex_scene_mode": result.get("complex_scene_mode"),
        "scene_final": scene.get("scene_final"),
        "scene_assignment": scene.get("scene_assignment"),
        "scene_raster": scene.get("scene_raster"),
        "scene_relation": scene.get("scene_relation"),
        "timing_final": timing.get("timing_final") if isinstance(timing, dict) else None,
        "step_up_required": False,
        "step_up_passed": None,
        "component_score": None,
        "fuzzy_ok": fuzzy.get("ok") if isinstance(fuzzy, dict) else None,
        "fuzzy_mode": fuzzy.get("ecc_mode") if isinstance(fuzzy, dict) else None,
        "fuzzy_hamming_distance": fuzzy.get("hamming_distance") if isinstance(fuzzy, dict) else None,
        "fuzzy_max_correctable_bits": fuzzy.get("max_correctable_bits") if isinstance(fuzzy, dict) else None,
        "gate_trace": result.get("gate_trace"),
        "failure_reasons": result.get("failure_reasons") or [],
        "geometry_failure_reasons": result.get("geometry_failure_reasons_diagnostic") or [],
        "scene_failure_reasons": result.get("scene_failure_reasons") or [],
        "structural_score": result.get("structural_score"),
        "structural_gate_pass": result.get("structural_gate_pass"),
        "structural_failure_reasons": result.get("structural_failure_reasons") or [],
        "shape_lock_baseline_score": result.get("shape_lock_baseline_score"),
        "shape_lock_baseline_threshold": result.get("shape_lock_baseline_threshold"),
        "shape_lock_baseline_pass": result.get("shape_lock_baseline_pass"),
        "algorithm_history": history,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Regenerate v2 enrollments and reverify raw redraws through Draw2Seed v2.2.5 Hough line-style gate."
    )
    parser.add_argument("--enrollment-table", default="draw2seed_v2_enrollments")
    parser.add_argument("--verification-table", default="draw2seed_v2_verifications")
    parser.add_argument("--algorithm-version", default="draw2seed-v2.2.7-hough-residual")
    parser.add_argument("--config-version", default="v2.2.7-hough-residual-2026-08-01")
    parser.add_argument("--out", default=str(ROOT / "results" / "v227_hough_comparison"))
    parser.add_argument("--apply", action="store_true", help="Overwrite source rows after archiving prior values in algorithm_history.")
    parser.add_argument("--include-bad-samples", action="store_true")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    supabase_url = os.environ.get("SUPABASE_URL")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not service_key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required in dev/.env or the shell.")

    client = create_client(supabase_url, service_key)
    enrollments = fetch_all(client, args.enrollment_table)
    verifications = fetch_all(client, args.verification_table)
    algorithm_code_hash = code_hash()
    generated_at = datetime.now(timezone.utc).isoformat()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loaded {len(enrollments)} enrollments and {len(verifications)} verifications.")
    print(f"Algorithm code hash: {algorithm_code_hash}")
    print("Mode:", "APPLY IN PLACE" if args.apply else "DRY RUN")

    regenerated: Dict[str, Dict[str, Any]] = {}
    enrollment_errors: List[Dict[str, Any]] = []
    for index, row in enumerate(enrollments, start=1):
        try:
            attempts = parse_json(row.get("attempts"), [])
            if not isinstance(attempts, list) or len(attempts) < 3:
                raise ValueError("fewer than three raw enrollment attempts")
            old_result = parse_json(row.get("analysis_result"), {})
            result = analyze_enrollment_v22(
                attempts=attempts,
                domain=str((old_result or {}).get("domain") or "example.com"),
                salt=row.get("public_salt") or (old_result or {}).get("public_salt"),
            )
            preserve_enrollment_secret_material(old_result if isinstance(old_result, dict) else {}, result)
            regenerated[str(row["id"])] = result
            if args.apply:
                payload = enrollment_update_payload(
                    row,
                    result,
                    args.algorithm_version,
                    algorithm_code_hash,
                    args.config_version,
                    generated_at,
                )
                client.table(args.enrollment_table).update(payload).eq("id", row["id"]).execute()
        except Exception as exc:
            enrollment_errors.append({"enrollment_id": row.get("id"), "error": str(exc)})
        if index % 10 == 0:
            print(f"Regenerated {index}/{len(enrollments)} enrollments...")

    verification_rows: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for index, row in enumerate(verifications, start=1):
        attempt_type = canonical_attempt_type(row.get("attempt_type"))
        if attempt_type == "bad_sample" and not args.include_bad_samples:
            skipped.append({"verification_id": row.get("id"), "reason": "bad_sample excluded"})
            continue
        enrollment_result = regenerated.get(str(row.get("enrollment_id")))
        redraw_strokes = parse_json(row.get("redraw_strokes"), [])
        if not enrollment_result:
            skipped.append({"verification_id": row.get("id"), "reason": "enrollment regeneration unavailable"})
            continue
        if not isinstance(redraw_strokes, list) or not redraw_strokes:
            skipped.append({"verification_id": row.get("id"), "reason": "missing raw redraw strokes"})
            continue
        try:
            result = verify_redraw_v22(enrollment_result, redraw_strokes)
            original_accepted = bool(row.get("accepted"))
            new_accepted = bool(result.get("accepted"))
            baseline_pass = bool(result.get("shape_lock_baseline_pass"))
            verification_rows.append({
                "verification_id": row.get("id"),
                "enrollment_id": row.get("enrollment_id"),
                "attempt_type": attempt_type,
                "original_accepted": original_accepted,
                "draw2seed_accepted": new_accepted,
                "transition": f"{'accept' if original_accepted else 'reject'}_to_{'accept' if new_accepted else 'reject'}",
                "token_score": result.get("token_score"),
                "geometry_final": (result.get("geometry_scores") or {}).get("geometry_final"),
                "curve_score": (result.get("geometry_scores") or {}).get("curve"),
                "hough_style_score": (result.get("geometry_scores") or {}).get("hough_style_score"),
                "hough_style_applicable": (result.get("geometry_scores") or {}).get("hough_style_applicable"),
                "hough_style_pass": (result.get("geometry_scores") or {}).get("hough_style_pass"),
                "hough_style_raw_pass": (result.get("geometry_scores") or {}).get("hough_style_raw_pass"),
                "hough_style_violation_count": (result.get("geometry_scores") or {}).get("hough_style_violation_count"),
                "hough_style_strong_violation_count": (result.get("geometry_scores") or {}).get("hough_style_strong_violation_count"),
                "hough_style_critical_violation_count": (result.get("geometry_scores") or {}).get("hough_style_critical_violation_count"),
                "hough_style_medium_violation_count": (result.get("geometry_scores") or {}).get("hough_style_medium_violation_count"),
                "hough_style_bend_violation_count": (result.get("geometry_scores") or {}).get("hough_style_bend_violation_count"),
                "hough_style_severe_bend_violation_count": (result.get("geometry_scores") or {}).get("hough_style_severe_bend_violation_count"),
                "hough_style_lost_line_failure": (result.get("geometry_scores") or {}).get("hough_style_lost_line_failure"),
                "hough_style_bend_failure": (result.get("geometry_scores") or {}).get("hough_style_bend_failure"),
                "hough_style_hard_gate_reliable": (result.get("geometry_scores") or {}).get("hough_style_hard_gate_reliable"),
                "hough_style_hard_gate_enabled": (result.get("geometry_scores") or {}).get("hough_style_hard_gate_enabled"),
                "hough_style_coverage_failure": (result.get("geometry_scores") or {}).get("hough_style_coverage_failure"),
                "hough_line_coverage": (result.get("geometry_scores") or {}).get("hough_line_coverage"),
                "hough_long_line_coverage": (result.get("geometry_scores") or {}).get("hough_long_line_coverage"),
                "hough_candidate_line_count": (result.get("geometry_scores") or {}).get("hough_candidate_line_count"),
                "hough_stable_line_count": (result.get("geometry_scores") or {}).get("hough_stable_line_count"),
                "structural_score": result.get("structural_score"),
                "structural_gate_applicable": result.get("structural_gate_applicable"),
                "structural_gate_pass": result.get("structural_gate_pass"),
                "structural_second_reference_support": result.get("structural_second_reference_support"),
                "shape_lock_baseline_score": result.get("shape_lock_baseline_score"),
                "shape_lock_baseline_threshold": result.get("shape_lock_baseline_threshold"),
                "shape_lock_baseline_pass": baseline_pass,
                "draw2seed_vs_shapelock": (
                    "draw2seed_only_accept" if new_accepted and not baseline_pass
                    else "shapelock_only_accept" if baseline_pass and not new_accepted
                    else "both_accept" if baseline_pass and new_accepted
                    else "both_reject"
                ),
                "failure_reasons": json.dumps(result.get("failure_reasons") or []),
            })
            if args.apply:
                payload = verification_update_payload(
                    row,
                    result,
                    args.algorithm_version,
                    algorithm_code_hash,
                    args.config_version,
                    generated_at,
                )
                client.table(args.verification_table).update(payload).eq("id", row["id"]).execute()
        except Exception as exc:
            skipped.append({"verification_id": row.get("id"), "reason": str(exc)})
        if index % 25 == 0:
            print(f"Re-evaluated {index}/{len(verifications)} verifications...")

    csv_path = out_dir / "verification_comparison.csv"
    fieldnames = list(verification_rows[0].keys()) if verification_rows else ["verification_id"]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(verification_rows)

    (out_dir / "enrollment_errors.json").write_text(json.dumps(enrollment_errors, indent=2), encoding="utf-8")
    (out_dir / "skipped_rows.json").write_text(json.dumps(skipped, indent=2), encoding="utf-8")

    summary: Dict[str, Any] = {
        "generated_at": generated_at,
        "mode": "apply" if args.apply else "dry_run",
        "algorithm_version": args.algorithm_version,
        "algorithm_code_hash": algorithm_code_hash,
        "config_version": args.config_version,
        "source_enrollments": len(enrollments),
        "regenerated_enrollments": len(regenerated),
        "source_verifications": len(verifications),
        "re_evaluated_verifications": len(verification_rows),
        "skipped_rows": len(skipped),
        "enrollment_errors": len(enrollment_errors),
        "by_attempt_type": {},
    }
    for attempt_type in sorted({row["attempt_type"] for row in verification_rows}):
        rows = [row for row in verification_rows if row["attempt_type"] == attempt_type]
        summary["by_attempt_type"][attempt_type] = {
            "n": len(rows),
            "original_accepts": sum(bool(row["original_accepted"]) for row in rows),
            "draw2seed_accepts": sum(bool(row["draw2seed_accepted"]) for row in rows),
            "shape_lock_accepts": sum(bool(row["shape_lock_baseline_pass"]) for row in rows),
            "draw2seed_only_accepts": sum(row["draw2seed_vs_shapelock"] == "draw2seed_only_accept" for row in rows),
            "shape_lock_only_accepts": sum(row["draw2seed_vs_shapelock"] == "shapelock_only_accept" for row in rows),
        }
    summary["transitions"] = {}
    for row in verification_rows:
        transition = str(row.get("transition") or "unknown")
        summary["transitions"][transition] = summary["transitions"].get(transition, 0) + 1
    hough_rows = [row for row in verification_rows if bool(row.get("hough_style_applicable"))]
    summary["hough_style_diagnostics"] = {
        "applicable_rows": len(hough_rows),
        "failed_rows": sum(not bool(row.get("hough_style_pass")) for row in hough_rows),
        "accepted_despite_failure": sum(
            bool(row.get("draw2seed_accepted")) and not bool(row.get("hough_style_pass"))
            for row in hough_rows
        ),
        "mean_line_coverage": (
            sum(float(row.get("hough_line_coverage") or 0.0) for row in hough_rows) / len(hough_rows)
            if hough_rows else 0.0
        ),
        "mean_long_line_coverage": (
            sum(float(row.get("hough_long_line_coverage") or 0.0) for row in hough_rows) / len(hough_rows)
            if hough_rows else 0.0
        ),
    }

    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    print(f"Outputs: {out_dir}")
    if not args.apply:
        print("Dry run complete. Re-run with --apply only after reviewing summary.json and verification_comparison.csv.")


if __name__ == "__main__":
    main()
