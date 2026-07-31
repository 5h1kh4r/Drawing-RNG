#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

DEV_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = DEV_ROOT.parent
SRC = DEV_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from drawing_rng.enrollment import analyze_enrollment, verify_redraw

SECRET_KEYS = {
    "demo_password",
    "seed_hex",
    "secret_hex",
    "secret_hex_for_demo_only",
    "canonical_seed_material",
}


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and value[0:1] == value[-1:] and value.startswith(("'", '"')):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def jsonish(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def clean_json(value: Any, *, redact: bool = False) -> Any:
    if isinstance(value, dict):
        output: Dict[str, Any] = {}
        for key, item in value.items():
            if redact and str(key) in SECRET_KEYS:
                output[str(key)] = "[redacted]"
            else:
                output[str(key)] = clean_json(item, redact=redact)
        return output
    if isinstance(value, (list, tuple)):
        return [clean_json(item, redact=redact) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def load_table(client: Any, table: str) -> List[Dict[str, Any]]:
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


def algorithm_code_hash() -> str:
    digest = hashlib.sha256()
    for source_path in sorted((SRC / "drawing_rng").glob("*.py")):
        digest.update(source_path.name.encode("utf-8"))
        digest.update(source_path.read_bytes())
    return digest.hexdigest()[:20]


def infer_domain(enrollment: Dict[str, Any]) -> str:
    old = jsonish(enrollment.get("analysis_result"))
    if not isinstance(old, dict):
        return "example.com"
    candidates = [
        old.get("domain"),
        (old.get("outputs") or {}).get("domain") if isinstance(old.get("outputs"), dict) else None,
        (old.get("fallback_canonical_outputs") or {}).get("domain")
        if isinstance(old.get("fallback_canonical_outputs"), dict)
        else None,
    ]
    return str(next((item for item in candidates if item), "example.com"))


def infer_salt(enrollment: Dict[str, Any]) -> str | None:
    if enrollment.get("public_salt"):
        return str(enrollment["public_salt"])
    old = jsonish(enrollment.get("analysis_result"))
    if isinstance(old, dict) and old.get("public_salt"):
        return str(old["public_salt"])
    return None


def transition(original: Any, rerun: Any) -> str:
    before = bool(original)
    after = bool(rerun)
    if before and after:
        return "accept_to_accept"
    if before and not after:
        return "accept_to_reject"
    if not before and after:
        return "reject_to_accept"
    return "reject_to_reject"


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
                fields.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(clean_json(row), ensure_ascii=False) + "\n")


def chunks(rows: List[Dict[str, Any]], size: int = 100) -> Iterable[List[Dict[str, Any]]]:
    for index in range(0, len(rows), size):
        yield rows[index:index + size]


def upsert_rows(client: Any, table: str, rows: List[Dict[str, Any]], conflict: str) -> None:
    for batch in chunks(rows):
        client.table(table).upsert(
            clean_json(batch),
            on_conflict=conflict,
        ).execute()


def rate(rows: List[Dict[str, Any]], key: str) -> float | None:
    if not rows:
        return None
    return sum(bool(row.get(key)) for row in rows) / len(rows)


def fmt_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{100.0 * value:.1f}%"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate Draw2Seed v2 enrollments and rerun linked verifications with the installed v2.1 algorithm."
    )
    parser.add_argument("--enrollment-table", default="draw2seed_v2_enrollments")
    parser.add_argument("--verification-table", default="draw2seed_v2_verifications")
    parser.add_argument("--enrollment-rerun-table", default="draw2seed_v21_enrollment_reruns")
    parser.add_argument("--verification-rerun-table", default="draw2seed_v21_verification_reruns")
    parser.add_argument("--algorithm-version", default="draw2seed-v2.1-multiref-dtw")
    parser.add_argument("--config-version", default="v2.1-owner-tolerant-2026-07-31")
    parser.add_argument("--out", type=Path, default=DEV_ROOT / "results" / "v21_on_v2")
    parser.add_argument("--write-supabase", action="store_true")
    parser.add_argument("--include-bad-samples", action="store_true")
    args = parser.parse_args()

    load_env_file(REPO_ROOT / ".env")
    load_env_file(DEV_ROOT / ".env")

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SystemExit(
            "Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY. "
            "Put them in dev/.env or export them in this local WSL shell."
        )

    marker_path = SRC / "drawing_rng" / "enrollment.py"
    source_text = marker_path.read_text(encoding="utf-8")
    required_markers = [
        "verification_references",
        "single_open_recovery_pass",
        "single_open_dtw_score",
    ]
    missing = [marker for marker in required_markers if marker not in source_text]
    if missing:
        raise SystemExit(
            "The dev v2.1 patch is not installed. Missing markers: " + ", ".join(missing)
        )

    from supabase import create_client

    client = create_client(url, key)
    enrollments = load_table(client, args.enrollment_table)
    verifications = load_table(client, args.verification_table)

    code_hash = algorithm_code_hash()
    generated_at = datetime.now(timezone.utc).isoformat()
    args.out.mkdir(parents=True, exist_ok=True)

    print(f"Loaded {len(enrollments)} enrollments and {len(verifications)} verifications.")
    print(f"Algorithm code hash: {code_hash}")

    regenerated: Dict[str, Dict[str, Any]] = {}
    enrollment_rows: List[Dict[str, Any]] = []
    verification_rows: List[Dict[str, Any]] = []
    skipped_rows: List[Dict[str, Any]] = []

    for index, enrollment in enumerate(enrollments, start=1):
        source_id = str(enrollment.get("id") or "")
        attempts = jsonish(enrollment.get("attempts"))
        if not source_id:
            skipped_rows.append({"kind": "enrollment", "reason": "missing_id"})
            continue
        if not isinstance(attempts, list) or len(attempts) < 3:
            skipped_rows.append({
                "kind": "enrollment",
                "source_enrollment_id": source_id,
                "reason": "missing_or_invalid_three_attempts",
            })
            continue
        try:
            result = analyze_enrollment(
                attempts=attempts,
                domain=infer_domain(enrollment),
                salt=infer_salt(enrollment),
            )
            regenerated[source_id] = result
            enrollment_rows.append({
                "source_enrollment_id": source_id,
                "participant_id": enrollment.get("participant_id"),
                "source_algorithm_version": enrollment.get("algorithm_version"),
                "rerun_algorithm_version": args.algorithm_version,
                "rerun_algorithm_code_hash": code_hash,
                "rerun_config_version": args.config_version,
                "attempt_count": len(attempts),
                "accepted_for_demo": result.get("accepted_for_demo"),
                "stability_score": result.get("stability_score"),
                "recommended_profile": result.get("recommended_profile"),
                "verification_reference_count": result.get("verification_reference_count"),
                "seed_quality_score": result.get("seed_quality_score"),
                "complexity_class": result.get("complexity_class"),
                "regenerated_analysis_result": clean_json(result, redact=True),
                "generated_at": generated_at,
            })
        except Exception as exc:
            skipped_rows.append({
                "kind": "enrollment",
                "source_enrollment_id": source_id,
                "reason": "analyze_enrollment_error",
                "error": f"{type(exc).__name__}: {exc}",
            })
        if index % 10 == 0:
            print(f"Regenerated {index}/{len(enrollments)} enrollments...")

    excluded = {"bad_sample", "step_up_component"}
    for index, verification in enumerate(verifications, start=1):
        source_verification_id = str(verification.get("id") or "")
        source_enrollment_id = str(verification.get("enrollment_id") or "")
        attempt_type = str(verification.get("attempt_type") or "unknown")
        if not args.include_bad_samples and attempt_type in excluded:
            skipped_rows.append({
                "kind": "verification",
                "source_verification_id": source_verification_id,
                "source_enrollment_id": source_enrollment_id,
                "reason": f"excluded_{attempt_type}",
            })
            continue
        enrollment_result = regenerated.get(source_enrollment_id)
        if not enrollment_result:
            skipped_rows.append({
                "kind": "verification",
                "source_verification_id": source_verification_id,
                "source_enrollment_id": source_enrollment_id,
                "reason": "regenerated_enrollment_unavailable",
            })
            continue
        strokes = jsonish(verification.get("redraw_strokes"))
        if not isinstance(strokes, list) or not strokes:
            skipped_rows.append({
                "kind": "verification",
                "source_verification_id": source_verification_id,
                "source_enrollment_id": source_enrollment_id,
                "reason": "missing_or_invalid_redraw_strokes",
            })
            continue
        try:
            result = verify_redraw(
                enrollment_result=enrollment_result,
                redraw_strokes=strokes,
                fuzzy_required=False,
            )
            geometry = result.get("geometry_scores") or {}
            rerun_accepted = bool(result.get("accepted"))
            original_accepted = bool(verification.get("accepted"))
            verification_rows.append({
                "source_verification_id": source_verification_id,
                "source_enrollment_id": source_enrollment_id,
                "attempt_type": attempt_type,
                "source_algorithm_version": verification.get("algorithm_version"),
                "rerun_algorithm_version": args.algorithm_version,
                "rerun_algorithm_code_hash": code_hash,
                "rerun_config_version": args.config_version,
                "original_accepted": original_accepted,
                "rerun_accepted": rerun_accepted,
                "decision_transition": transition(original_accepted, rerun_accepted),
                "profile": result.get("profile"),
                "final_score": result.get("final_score"),
                "token_score": result.get("token_score"),
                "token_score_weighted": result.get("token_score_weighted"),
                "geometry_final": geometry.get("geometry_final"),
                "layout_score": geometry.get("layout"),
                "relation_score": geometry.get("relation"),
                "topology_score": geometry.get("topology"),
                "curve_score": geometry.get("curve"),
                "stroke_shape_score": geometry.get("stroke_shape"),
                "selected_reference_attempt": result.get("selected_reference_attempt"),
                "verification_reference_count": result.get("verification_reference_count"),
                "second_reference_support": result.get("second_reference_support"),
                "single_open_dtw_score": result.get("single_open_dtw_score"),
                "single_open_recovery_pass": result.get("single_open_recovery_pass"),
                "failure_reasons": clean_json(result.get("failure_reasons") or []),
                "overridden_failure_reasons": clean_json(result.get("overridden_failure_reasons") or []),
                "rerun_result": clean_json(result, redact=True),
                "generated_at": generated_at,
            })
        except Exception as exc:
            skipped_rows.append({
                "kind": "verification",
                "source_verification_id": source_verification_id,
                "source_enrollment_id": source_enrollment_id,
                "reason": "verify_redraw_error",
                "error": f"{type(exc).__name__}: {exc}",
            })
        if index % 25 == 0:
            print(f"Re-evaluated {index}/{len(verifications)} verifications...")

    transition_counts: Counter[Tuple[str, str]] = Counter(
        (str(row["attempt_type"]), str(row["decision_transition"]))
        for row in verification_rows
    )
    transition_rows = [
        {"attempt_type": attempt_type, "decision_transition": change, "count": count}
        for (attempt_type, change), count in sorted(transition_counts.items())
    ]

    labels = sorted({str(row.get("attempt_type") or "unknown") for row in verification_rows})
    metric_rows: List[Dict[str, Any]] = []
    for label in labels:
        rows = [row for row in verification_rows if row.get("attempt_type") == label]
        metric_rows.append({
            "attempt_type": label,
            "attempts": len(rows),
            "original_accept_rate": rate(rows, "original_accepted"),
            "rerun_accept_rate": rate(rows, "rerun_accepted"),
            "changed_decisions": sum(row.get("decision_transition") not in {"accept_to_accept", "reject_to_reject"} for row in rows),
            "single_open_recoveries": sum(bool(row.get("single_open_recovery_pass")) for row in rows),
        })

    summary = {
        "generated_at": generated_at,
        "source_enrollment_table": args.enrollment_table,
        "source_verification_table": args.verification_table,
        "source_enrollments": len(enrollments),
        "source_verifications": len(verifications),
        "regenerated_enrollments": len(enrollment_rows),
        "re_evaluated_verifications": len(verification_rows),
        "skipped_rows": len(skipped_rows),
        "rerun_algorithm_version": args.algorithm_version,
        "rerun_algorithm_code_hash": code_hash,
        "rerun_config_version": args.config_version,
        "attempt_type_counts": dict(Counter(str(row.get("attempt_type") or "unknown") for row in verification_rows)),
        "decision_transition_counts": {
            f"{label}:{change}": count
            for (label, change), count in sorted(transition_counts.items())
        },
    }

    write_jsonl(args.out / "enrollment_reruns.jsonl", enrollment_rows)
    write_jsonl(args.out / "verification_reruns.jsonl", verification_rows)
    write_csv(args.out / "verification_reruns.csv", [
        {key: (json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value)
         for key, value in row.items() if key != "rerun_result"}
        for row in verification_rows
    ])
    write_csv(args.out / "transition_summary.csv", transition_rows)
    write_csv(args.out / "metrics_by_attempt_type.csv", metric_rows)
    write_csv(args.out / "skipped_rows.csv", skipped_rows)
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    report_lines = [
        "# Draw2Seed v2 → v2.1 retrospective rerun",
        "",
        f"- Source enrollments: {len(enrollments)}",
        f"- Regenerated enrollments: {len(enrollment_rows)}",
        f"- Source verifications: {len(verifications)}",
        f"- Re-evaluated verifications: {len(verification_rows)}",
        f"- Skipped rows: {len(skipped_rows)}",
        f"- Algorithm: `{args.algorithm_version}`",
        f"- Code hash: `{code_hash}`",
        "",
        "## Rates by attempt type",
        "",
        "| Attempt type | N | Original accept | v2.1 accept | Changed | Single-open recoveries |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in metric_rows:
        report_lines.append(
            f"| {row['attempt_type']} | {row['attempts']} | "
            f"{fmt_rate(row['original_accept_rate'])} | {fmt_rate(row['rerun_accept_rate'])} | "
            f"{row['changed_decisions']} | {row['single_open_recoveries']} |"
        )
    report_lines.extend([
        "",
        "Historical rows were not overwritten. These are retrospective v2.1 evaluations of the same raw gestures.",
    ])
    report = "\n".join(report_lines) + "\n"
    (args.out / "report.md").write_text(report, encoding="utf-8")

    if args.write_supabase:
        if enrollment_rows:
            upsert_rows(
                client,
                args.enrollment_rerun_table,
                enrollment_rows,
                "source_enrollment_id,rerun_algorithm_code_hash,rerun_config_version",
            )
        if verification_rows:
            upsert_rows(
                client,
                args.verification_rerun_table,
                verification_rows,
                "source_verification_id,rerun_algorithm_code_hash,rerun_config_version",
            )
        print(
            f"Saved derived reruns to {args.enrollment_rerun_table} and {args.verification_rerun_table}."
        )

    print(report)
    print(f"Outputs: {args.out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
