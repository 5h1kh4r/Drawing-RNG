#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
RERUN = ROOT / "tools" / "rerun_v227_comparison.py"
PRIMARY_TYPES = (
    "owner_test",
    "blind_impostor",
    "informed_forgery",
    "near_miss",
    "true_wrong_shape",
)

try:
    from supabase import create_client
except Exception as exc:
    raise SystemExit(f"Install dev requirements first; Supabase import failed: {exc}")


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
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


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(*values: Any) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(canonical_json(value).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def fetch_all(client: Any, table: str, page_size: int = 500) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    start = 0
    while True:
        response = client.table(table).select("*").range(start, start + page_size - 1).execute()
        page = list(response.data or [])
        rows.extend(page)
        if len(page) < page_size:
            return rows
        start += page_size


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}


def canonical_label(raw: Any) -> Tuple[str, str]:
    value = str(raw or "").strip()
    if value == "wrong_shape":
        return (
            "legacy_wrong_shape_mixed",
            "historical wrong_shape mixed near_miss and unrelated drawings",
        )
    aliases = {
        "concept_variant": "near_miss",
        "ambiguous": "bad_sample",
        "step_up_component": "bad_sample",
    }
    value = aliases.get(value, value or "bad_sample")
    if value not in set(PRIMARY_TYPES) | {"bad_sample"}:
        return "bad_sample", f"unknown label {value!r}"
    return value, ""


def read_csv(path: Path) -> List[Dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
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


def wilson(k: int, n: int, z: float = 1.959963984540054) -> Tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = k / n
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, center - radius), min(1.0, center + radius)


def mcnemar_exact(d2s_only: int, sl_only: int) -> float:
    n = d2s_only + sl_only
    if n == 0:
        return 1.0
    tail = min(d2s_only, sl_only)
    p = sum(math.comb(n, i) for i in range(tail + 1)) / (2 ** n)
    return min(1.0, 2 * p)


def summarize(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows)
    d2s = sum(as_bool(r["draw2seed_accepted"]) for r in rows)
    sl = sum(as_bool(r["shape_lock_baseline_pass"]) for r in rows)
    d2s_only = sum(as_bool(r["draw2seed_accepted"]) and not as_bool(r["shape_lock_baseline_pass"]) for r in rows)
    sl_only = sum(as_bool(r["shape_lock_baseline_pass"]) and not as_bool(r["draw2seed_accepted"]) for r in rows)
    lo, hi = wilson(d2s, n)
    return {
        "n": n,
        "draw2seed_accepts": d2s,
        "draw2seed_rate": d2s / n if n else 0.0,
        "draw2seed_wilson_95": [lo, hi],
        "shape_lock_accepts": sl,
        "shape_lock_rate": sl / n if n else 0.0,
        "draw2seed_only_accepts": d2s_only,
        "shape_lock_only_accepts": sl_only,
        "mcnemar_exact_two_sided_p": mcnemar_exact(d2s_only, sl_only),
    }


def pct(value: float) -> str:
    return f"{100 * value:.1f}%"


def md_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(str(v) for v in row) + " |" for row in rows)
    return "\n".join(lines)


def run_rerun(out_dir: Path, enrollment_table: str, verification_table: str) -> None:
    cmd = [
        sys.executable,
        str(RERUN),
        "--enrollment-table", enrollment_table,
        "--verification-table", verification_table,
        "--algorithm-version", "draw2seed-v2.2.7-hough-residual",
        "--config-version", "v2.2.7-hough-residual-2026-08-01",
        "--out", str(out_dir),
    ]
    subprocess.run(cmd, check=True, cwd=ROOT.parent)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a deduplicated retrospective paper dataset from legacy and v2 Draw2Seed tables.")
    parser.add_argument("--out", default=str(ROOT / "results" / "paper_v227_combined"))
    parser.add_argument("--skip-rerun", action="store_true")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required in dev/.env or the shell.")
    client = create_client(url, key)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    legacy_out = out / "legacy_rerun"
    v2_out = out / "v2_rerun"
    if not args.skip_rerun:
        run_rerun(legacy_out, "drawing_seed_enrollments", "drawing_seed_verifications")
        run_rerun(v2_out, "draw2seed_v2_enrollments", "draw2seed_v2_verifications")

    legacy_enroll = fetch_all(client, "drawing_seed_enrollments")
    legacy_verify = fetch_all(client, "drawing_seed_verifications")
    v2_enroll = fetch_all(client, "draw2seed_v2_enrollments")
    v2_verify = fetch_all(client, "draw2seed_v2_verifications")

    source = {
        "legacy": (legacy_enroll, legacy_verify, legacy_out),
        "v2": (v2_enroll, v2_verify, v2_out),
    }

    combined: List[Dict[str, Any]] = []
    raw_maps: Dict[str, Dict[str, Dict[str, Any]]] = {}
    enrollment_fp: Dict[Tuple[str, str], str] = {}

    for cohort, (enrollments, verifications, rerun_out) in source.items():
        raw_maps[cohort] = {str(r["id"]): r for r in verifications}
        for row in enrollments:
            attempts = parse_json(row.get("attempts"), [])
            enrollment_fp[(cohort, str(row["id"]))] = fingerprint(attempts)

        rerun_rows = read_csv(rerun_out / "verification_comparison.csv")
        for result in rerun_rows:
            raw = raw_maps[cohort].get(str(result["verification_id"]), {})
            label, label_note = canonical_label(raw.get("attempt_type", result.get("attempt_type")))
            e_fp = enrollment_fp.get((cohort, str(result["enrollment_id"])), "")
            redraw = parse_json(raw.get("redraw_strokes"), [])
            row = dict(result)
            row.update({
                "source_cohort": cohort,
                "attempt_type_raw": raw.get("attempt_type"),
                "attempt_type": label,
                "label_note": label_note,
                "created_at": raw.get("created_at"),
                "profile_key": f"profile_{e_fp[:20]}",
                "verification_fingerprint": fingerprint(e_fp, redraw),
                "included_primary": False,
                "duplicate_of": "",
                "exclusion_reason": "",
            })
            combined.append(row)

    # Exact raw-data deduplication. Prefer cleaned v2 rows.
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in combined:
        groups[row["verification_fingerprint"]].append(row)
    duplicate_groups = []
    for fp, group in groups.items():
        if len(group) < 2:
            continue
        winner = max(group, key=lambda r: (r["source_cohort"] == "v2", r["attempt_type"] in PRIMARY_TYPES, str(r.get("created_at") or "")))
        winner_key = f'{winner["source_cohort"]}:{winner["verification_id"]}'
        duplicate_groups.append({
            "fingerprint": fp,
            "kept": winner_key,
            "members": [f'{r["source_cohort"]}:{r["verification_id"]}' for r in group],
            "labels": sorted({r["attempt_type"] for r in group}),
        })
        for row in group:
            if row is not winner:
                row["duplicate_of"] = winner_key
                row["exclusion_reason"] = "exact raw-data duplicate"

    for row in combined:
        if row["duplicate_of"]:
            continue
        if row["attempt_type"] in PRIMARY_TYPES:
            row["included_primary"] = True
        elif row["attempt_type"] == "legacy_wrong_shape_mixed":
            row["exclusion_reason"] = "historical mixed wrong_shape category; relabel before primary FAR use"
        else:
            row["exclusion_reason"] = "bad/unknown sample"

    primary = [r for r in combined if as_bool(r["included_primary"])]
    legacy_mixed = [r for r in combined if not r["duplicate_of"] and r["attempt_type"] == "legacy_wrong_shape_mixed"]

    by_type = {t: summarize([r for r in primary if r["attempt_type"] == t]) for t in PRIMARY_TYPES}
    by_cohort = {
        cohort: {t: summarize([r for r in primary if r["source_cohort"] == cohort and r["attempt_type"] == t]) for t in PRIMARY_TYPES}
        for cohort in ("legacy", "v2")
    }

    profile_detail = []
    macro = {}
    grouped_profiles: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in primary:
        grouped_profiles[(row["profile_key"], row["attempt_type"])].append(row)
    rates_by_type: Dict[str, List[float]] = defaultdict(list)
    for (profile, attempt_type), rows in grouped_profiles.items():
        accepts = sum(as_bool(r["draw2seed_accepted"]) for r in rows)
        rate = accepts / len(rows)
        rates_by_type[attempt_type].append(rate)
        profile_detail.append({
            "profile_key": profile,
            "attempt_type": attempt_type,
            "n": len(rows),
            "draw2seed_accepts": accepts,
            "draw2seed_rate": rate,
            "shape_lock_accepts": sum(as_bool(r["shape_lock_baseline_pass"]) for r in rows),
            "source_cohorts": ",".join(sorted({r["source_cohort"] for r in rows})),
        })
    for t in PRIMARY_TYPES:
        rates = rates_by_type.get(t, [])
        macro[t] = {
            "profiles": len(rates),
            "mean_profile_accept_rate": statistics.fmean(rates) if rates else 0.0,
            "median_profile_accept_rate": statistics.median(rates) if rates else 0.0,
            "profiles_with_any_accept": sum(rate > 0 for rate in rates),
            "profiles_with_any_accept_rate": sum(rate > 0 for rate in rates) / len(rates) if rates else 0.0,
        }

    owner = by_type["owner_test"]
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "analysis_design": "retrospective pooled development-set re-evaluation",
        "algorithm_version": "draw2seed-v2.2.7-hough-residual",
        "source_counts": {
            "legacy": {"enrollments": len(legacy_enroll), "verifications": len(legacy_verify)},
            "v2": {"enrollments": len(v2_enroll), "verifications": len(v2_verify)},
        },
        "evaluated_before_deduplication": len(combined),
        "exact_duplicate_groups": len(duplicate_groups),
        "duplicate_rows_excluded": sum(bool(r["duplicate_of"]) for r in combined),
        "primary_rows": len(primary),
        "primary_profiles": len({r["profile_key"] for r in primary}),
        "legacy_mixed_wrong_shape_rows_excluded_from_primary": len(legacy_mixed),
        "by_attempt_type": by_type,
        "by_cohort_and_attempt_type": by_cohort,
        "macro_by_attempt_type": macro,
        "owner_false_reject_rate": 1 - owner["draw2seed_rate"] if owner["n"] else 0.0,
        "methodology_notes": [
            "Both cohorts were re-evaluated under the same v2.2.7 implementation.",
            "Exact raw-data duplicates were removed, preferring cleaned v2 copies.",
            "Historical wrong_shape was not silently converted to true_wrong_shape.",
            "Results are retrospective development-set estimates, not independent holdout validation.",
        ],
    }

    write_csv(out / "all_evaluated_rows.csv", combined)
    write_csv(out / "primary_analysis_rows.csv", primary)
    write_csv(out / "legacy_wrong_shape_mixed_rows.csv", legacy_mixed)
    write_csv(out / "profile_metrics.csv", profile_detail)
    (out / "duplicate_groups.json").write_text(json.dumps(duplicate_groups, indent=2), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    table_rows = []
    comparison_rows = []
    for t in PRIMARY_TYPES:
        m = by_type[t]
        lo, hi = m["draw2seed_wilson_95"]
        table_rows.append([t, m["n"], f'{m["draw2seed_accepts"]}/{m["n"]}', pct(m["draw2seed_rate"]), f'{pct(lo)}–{pct(hi)}'])
        comparison_rows.append([t, pct(m["draw2seed_rate"]), pct(m["shape_lock_rate"]), m["draw2seed_only_accepts"], m["shape_lock_only_accepts"], f'{m["mcnemar_exact_two_sided_p"]:.4f}'])

    paper = f"""# Draw2Seed v2.2.7 Combined Retrospective Results

Generated: `{summary['generated_at']}`

> These are pooled retrospective **development-set** estimates. They are not an
> independent holdout evaluation because portions of the data informed algorithm
> development and threshold tuning.

## Dataset accounting

- Legacy source: **{len(legacy_enroll)} enrollments**, **{len(legacy_verify)} verifications**
- v2 source: **{len(v2_enroll)} enrollments**, **{len(v2_verify)} verifications**
- Evaluated before deduplication: **{len(combined)}**
- Exact duplicate rows excluded: **{summary['duplicate_rows_excluded']}**
- Primary rows: **{len(primary)}**
- Primary profiles: **{summary['primary_profiles']}**
- Historical mixed `wrong_shape` rows excluded from primary FAR: **{len(legacy_mixed)}**

## Attempt-level performance

`owner_test` reports TAR. Negative categories report FAR.

{md_table(['Attempt type', 'n', 'Accepted', 'Rate', 'Wilson 95% CI'], table_rows)}

Owner FRR: **{pct(summary['owner_false_reject_rate'])}**.

## Paired ShapeLock comparison

{md_table(['Attempt type', 'Draw2Seed', 'ShapeLock', 'D2S-only', 'SL-only', 'McNemar p'], comparison_rows)}

## Interpretation boundary

The old `wrong_shape` category historically mixed near misses and unrelated
shapes. Those rows remain in `legacy_wrong_shape_mixed_rows.csv` and should only
enter primary FAR tables after manual relabelling into `near_miss` or
`true_wrong_shape`.
"""
    (out / "paper_results.md").write_text(paper, encoding="utf-8")

    tex_rows = []
    for t in PRIMARY_TYPES:
        m = by_type[t]
        lo, hi = m["draw2seed_wilson_95"]
        label = t.replace("_", r"\_")
        tex_rows.append(f"{label} & {m['n']} & {m['draw2seed_accepts']} & {100*m['draw2seed_rate']:.1f}\\% & [{100*lo:.1f}, {100*hi:.1f}]\\% \\\\")
    tex = """% Auto-generated retrospective results
\\begin{table}[t]
\\centering
\\caption{Retrospective Draw2Seed v2.2.7 attempt-level results.}
\\label{tab:v227-results}
\\begin{tabular}{lrrrr}
\\hline
Attempt type & $n$ & Accept & Rate & 95\\% CI \\\\
\\hline
""" + "\n".join(tex_rows) + """
\\hline
\\end{tabular}
\\end{table}
"""
    (out / "paper_table.tex").write_text(tex, encoding="utf-8")

    print(json.dumps(summary, indent=2))
    print(f"\nPaper outputs written to: {out}")
    print("No database rows were modified.")


if __name__ == "__main__":
    main()
