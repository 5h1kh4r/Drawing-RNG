#!/usr/bin/env python3
"""
Run Drawing-RNG tests on the manually cleaned dataset.

Supports:
  1. Supabase clean table:
       public.stroke_samples_clean

  2. Local JSONL produced by sync_filtered_dataset.py:
       filtered_sync_output/clean_records.jsonl

This script intentionally uses the CLEAN labels:
  clean_participant_id
  clean_concept
  clean_redraw_id

It recomputes tokens for strict / balanced / tolerant profiles from strokes, then outputs:
  - profile_summary.csv
  - pairwise_similarity.csv
  - clean_concept_summary_balanced.csv
  - enrollment_windows.csv
  - enrollment_summary.csv
  - graphs/*.png

Run from repository root so src/drawing_rng/stroke_token_encoder.py is importable.

Examples:

PowerShell, Supabase:
  $env:SUPABASE_URL="https://your-project.supabase.co"
  $env:SUPABASE_SERVICE_ROLE_KEY="your-service-role-key"
  python experiments/run_clean_dataset_tests.py --source supabase-clean --out results/clean_run_001

Local JSONL:
  python experiments/run_clean_dataset_tests.py --source clean-jsonl --input filtered_sync_output/clean_records.jsonl --out results/clean_run_001
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Put src/ on path.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    from drawing_rng.stroke_token_encoder import encode_json_payload
except Exception as exc:
    raise SystemExit(
        "Could not import drawing_rng.stroke_token_encoder.\n"
        "Run from the repository root and make sure src/drawing_rng/stroke_token_encoder.py exists.\n"
        f"Original error: {exc}"
    )

try:
    import matplotlib.pyplot as plt
except Exception as exc:
    raise SystemExit("matplotlib is required: pip install matplotlib") from exc


PROFILES: Dict[str, Dict[str, Any]] = {
    "strict": {
        "resample_spacing": 0.035,
        "direction_buckets": 16,
        "length_buckets": {"short_max": 0.12, "medium_max": 0.28},
        "zone_grid": 4,
        "order_mode": "drawn",
        "min_stroke_points": 2,
        "min_raw_stroke_length": 5.0,
        "min_normalized_stroke_length": 0.020,
        "jitter_run_max": 0,
        "simplify_epsilon": 0.005,
        "include_turn_tokens": True,
        "include_turn_magnitude": True,
        "include_start_zone": True,
        "include_penup_moves": True,
        "include_closed_tokens": True,
        "include_relation_tokens": True,
        "close_threshold": 0.075,
        "round_normalized": 4,
    },
    "balanced": {
        "resample_spacing": 0.05,
        "direction_buckets": 8,
        "length_buckets": {"short_max": 0.18, "medium_max": 0.40},
        "zone_grid": 3,
        "order_mode": "spatial",
        "min_stroke_points": 2,
        "min_raw_stroke_length": 5.0,
        "min_normalized_stroke_length": 0.035,
        "jitter_run_max": 1,
        "simplify_epsilon": 0.015,
        "include_turn_tokens": True,
        "include_turn_magnitude": False,
        "include_start_zone": True,
        "include_penup_moves": True,
        "include_closed_tokens": True,
        "include_relation_tokens": True,
        "close_threshold": 0.075,
        "round_normalized": 4,
    },
    "tolerant": {
        "resample_spacing": 0.08,
        "direction_buckets": 4,
        "length_buckets": {"short_max": 0.25, "medium_max": 0.60},
        "zone_grid": 2,
        "order_mode": "spatial",
        "min_stroke_points": 2,
        "min_raw_stroke_length": 5.0,
        "min_normalized_stroke_length": 0.050,
        "jitter_run_max": 2,
        "simplify_epsilon": 0.030,
        "include_turn_tokens": True,
        "include_turn_magnitude": False,
        "include_start_zone": True,
        "include_penup_moves": True,
        "include_closed_tokens": True,
        "include_relation_tokens": True,
        "close_threshold": 0.075,
        "round_normalized": 4,
    },
}


def safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def edit_distance(a: List[str], b: List[str]) -> int:
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i]
        for j, cb in enumerate(b, start=1):
            curr.append(min(
                prev[j] + 1,
                curr[j - 1] + 1,
                prev[j - 1] + (0 if ca == cb else 1),
            ))
        prev = curr
    return prev[-1]


def token_similarity(a: List[str], b: List[str]) -> float:
    denom = max(len(a), len(b), 1)
    return max(0.0, 1.0 - edit_distance(a, b) / denom)


def auc_score(pos: List[float], neg: List[float]) -> Optional[float]:
    """
    Simple Mann-Whitney AUC: probability a positive score > negative score.
    Ties count as 0.5.
    """
    if not pos or not neg:
        return None
    total = 0.0
    count = 0
    for p in pos:
        for n in neg:
            if p > n:
                total += 1.0
            elif p == n:
                total += 0.5
            count += 1
    return total / count if count else None


def quantile(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return xs[lo]
    frac = pos - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


def row_get(row: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return default


def normalize_sample(row: Dict[str, Any], idx: int) -> Optional[Dict[str, Any]]:
    strokes = row_get(row, "strokes")
    if not isinstance(strokes, list) or len(strokes) == 0:
        return None

    source_id = str(row_get(row, "source_id", "id", default=f"row_{idx}"))

    clean_participant = str(row_get(
        row,
        "clean_participant_id",
        "participant_id",
        "manual_person_id",
        default="unknown_participant",
    ) or "unknown_participant")

    clean_concept = str(row_get(
        row,
        "clean_concept",
        "concept",
        "manual_concept_label",
        default="unknown",
    ) or "unknown")

    clean_redraw = row_get(row, "clean_redraw_id", "redraw_id", "manual_redraw_group", default=None)
    try:
        clean_redraw_int = int(clean_redraw) if clean_redraw is not None and str(clean_redraw) != "" else None
    except Exception:
        clean_redraw_int = None

    return {
        "sample_id": source_id,
        "source_id": source_id,
        "clean_participant_id": clean_participant,
        "clean_concept": clean_concept,
        "clean_redraw_id": clean_redraw_int,
        "strokes": strokes,
        "created_at": str(row_get(row, "created_at", default="")),
        "original_participant_id": row_get(row, "original_participant_id", "participant_id", default=""),
        "original_concept": row_get(row, "original_concept", "concept", default=""),
        "notes": row_get(row, "notes", default=""),
    }


def load_supabase_clean(table: str) -> List[Dict[str, Any]]:
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SystemExit("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY.")

    supabase = create_client(url, key)
    rows: List[Dict[str, Any]] = []
    start = 0
    page_size = 1000
    while True:
        res = supabase.table(table).select("*").order("clean_participant_id").range(start, start + page_size - 1).execute()
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return rows


def load_clean_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def encode_samples(samples: List[Dict[str, Any]], profiles: Iterable[str]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    encoded_by_profile: Dict[str, Dict[str, Dict[str, Any]]] = {}

    for profile in profiles:
        print(f"[info] Encoding {len(samples)} clean samples with profile={profile}")
        encoded: Dict[str, Dict[str, Any]] = {}
        for s in samples:
            try:
                result = encode_json_payload({
                    "strokes": s["strokes"],
                    "params": PROFILES[profile],
                })
                tokens = result.get("tokens") or []
                serialized = result.get("serialized")
                stats = result.get("stats") or {}
                ok = True
                err = ""
            except Exception as exc:
                tokens = []
                serialized = ""
                stats = {}
                ok = False
                err = str(exc)

            encoded[s["sample_id"]] = {
                **s,
                "profile": profile,
                "tokens": tokens,
                "serialized": serialized,
                "token_count": len(tokens),
                "encode_ok": ok,
                "encode_error": err,
                "weak_seed_flags": ";".join(stats.get("weak_seed_flags") or []),
            }
        encoded_by_profile[profile] = encoded

    return encoded_by_profile


def compute_pairwise(encoded: Dict[str, Dict[str, Any]], profile: str) -> List[Dict[str, Any]]:
    ids = sorted(encoded)
    rows: List[Dict[str, Any]] = []

    for a_id, b_id in combinations(ids, 2):
        a = encoded[a_id]
        b = encoded[b_id]
        sim = token_similarity(a["tokens"], b["tokens"])

        same_participant = a["clean_participant_id"] == b["clean_participant_id"]
        same_concept = a["clean_concept"] == b["clean_concept"]

        if same_participant and same_concept:
            pair_type = "genuine_same_participant_same_concept"
        elif same_concept and not same_participant:
            pair_type = "same_concept_different_participant"
        elif same_participant and not same_concept:
            pair_type = "same_participant_different_concept"
        else:
            pair_type = "different_participant_different_concept"

        different_concept = not same_concept

        rows.append({
            "profile": profile,
            "sample_a": a_id,
            "sample_b": b_id,
            "participant_a": a["clean_participant_id"],
            "participant_b": b["clean_participant_id"],
            "concept_a": a["clean_concept"],
            "concept_b": b["clean_concept"],
            "redraw_a": a.get("clean_redraw_id"),
            "redraw_b": b.get("clean_redraw_id"),
            "same_participant": same_participant,
            "same_concept": same_concept,
            "different_concept": different_concept,
            "pair_type": pair_type,
            "similarity": sim,
            "token_count_a": a["token_count"],
            "token_count_b": b["token_count"],
        })

    return rows


def summarize_profiles(pairwise_all: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for profile in sorted({r["profile"] for r in pairwise_all}):
        pr = [r for r in pairwise_all if r["profile"] == profile]

        genuine = [safe_float(r["similarity"]) for r in pr if r["pair_type"] == "genuine_same_participant_same_concept"]
        diff = [safe_float(r["similarity"]) for r in pr if r["different_concept"]]
        same_prompt_diff_person = [safe_float(r["similarity"]) for r in pr if r["pair_type"] == "same_concept_different_participant"]

        rows.append({
            "profile": profile,
            "genuine_pairs": len(genuine),
            "different_concept_pairs": len(diff),
            "same_concept_different_participant_pairs": len(same_prompt_diff_person),
            "genuine_mean": mean(genuine) if genuine else "",
            "genuine_median": median(genuine) if genuine else "",
            "genuine_p10": quantile(genuine, 0.10) if genuine else "",
            "genuine_p25": quantile(genuine, 0.25) if genuine else "",
            "genuine_p75": quantile(genuine, 0.75) if genuine else "",
            "different_mean": mean(diff) if diff else "",
            "different_median": median(diff) if diff else "",
            "same_concept_diff_participant_mean": mean(same_prompt_diff_person) if same_prompt_diff_person else "",
            "separation_mean": (mean(genuine) - mean(diff)) if genuine and diff else "",
            "auc_genuine_vs_different": auc_score(genuine, diff) if genuine and diff else "",
        })
    return rows


def summarize_concepts_balanced(pairwise: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    concepts = sorted({r["concept_a"] for r in pairwise} | {r["concept_b"] for r in pairwise})
    out: List[Dict[str, Any]] = []

    for concept in concepts:
        genuine = [
            safe_float(r["similarity"]) for r in pairwise
            if r["pair_type"] == "genuine_same_participant_same_concept"
            and r["concept_a"] == concept
            and r["concept_b"] == concept
        ]
        same_prompt_diff_person = [
            safe_float(r["similarity"]) for r in pairwise
            if r["pair_type"] == "same_concept_different_participant"
            and r["concept_a"] == concept
            and r["concept_b"] == concept
        ]
        diff_pairs_touching_concept = [
            safe_float(r["similarity"]) for r in pairwise
            if r["different_concept"] and (r["concept_a"] == concept or r["concept_b"] == concept)
        ]

        out.append({
            "concept": concept,
            "genuine_pairs": len(genuine),
            "genuine_mean": mean(genuine) if genuine else "",
            "genuine_median": median(genuine) if genuine else "",
            "same_concept_different_participant_pairs": len(same_prompt_diff_person),
            "same_concept_different_participant_mean": mean(same_prompt_diff_person) if same_prompt_diff_person else "",
            "different_concept_pairs_touching_concept": len(diff_pairs_touching_concept),
            "different_concept_mean": mean(diff_pairs_touching_concept) if diff_pairs_touching_concept else "",
            "nearest_different_concept_similarity": max(diff_pairs_touching_concept) if diff_pairs_touching_concept else "",
            "margin_vs_different_mean": (mean(genuine) - mean(diff_pairs_touching_concept)) if genuine and diff_pairs_touching_concept else "",
        })
    return out


def enrollment_windows(encoded_by_profile: Dict[str, Dict[str, Dict[str, Any]]], window_size: int = 3) -> List[Dict[str, Any]]:
    """
    Enrollment mode:
      For each clean_participant_id + clean_concept group, sort by clean_redraw_id and/or created_at.
      Slide 3-attempt windows and score each profile by median pairwise similarity inside the window.
      Choose the best profile for each window.
    """
    base_profile = next(iter(encoded_by_profile.keys()))
    base_samples = list(encoded_by_profile[base_profile].values())

    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for s in base_samples:
        groups[(s["clean_participant_id"], s["clean_concept"])].append(s)

    rows: List[Dict[str, Any]] = []
    for (participant, concept), group in sorted(groups.items()):
        group.sort(key=lambda s: (
            s.get("clean_redraw_id") if s.get("clean_redraw_id") is not None else 999999,
            s.get("created_at") or "",
            s["sample_id"],
        ))

        if len(group) < window_size:
            continue

        for start in range(0, len(group) - window_size + 1):
            window = group[start:start + window_size]
            ids = [s["sample_id"] for s in window]

            profile_scores: Dict[str, float] = {}
            profile_pair_scores: Dict[str, List[float]] = {}

            for profile, encoded in encoded_by_profile.items():
                sims = []
                for a_id, b_id in combinations(ids, 2):
                    sims.append(token_similarity(encoded[a_id]["tokens"], encoded[b_id]["tokens"]))
                profile_scores[profile] = median(sims) if sims else 0.0
                profile_pair_scores[profile] = sims

            best_profile = max(profile_scores, key=lambda p: profile_scores[p])
            best_score = profile_scores[best_profile]

            if best_score >= 0.45:
                label = "strong_stability"
            elif best_score >= 0.30:
                label = "usable_for_demo"
            elif best_score >= 0.20:
                label = "weak_moderate"
            else:
                label = "unstable"

            rows.append({
                "participant": participant,
                "concept": concept,
                "window_start": start + 1,
                "sample_ids": ";".join(ids),
                "attempt_count": len(ids),
                "best_profile": best_profile,
                "best_stability_score": best_score,
                "stability_label": label,
                "strict_score": profile_scores.get("strict", ""),
                "balanced_score": profile_scores.get("balanced", ""),
                "tolerant_score": profile_scores.get("tolerant", ""),
                "best_pair_scores": ";".join(f"{x:.4f}" for x in profile_pair_scores[best_profile]),
            })

    return rows


def summarize_enrollment(windows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    concepts = sorted({w["concept"] for w in windows})
    for concept in concepts:
        ws = [w for w in windows if w["concept"] == concept]
        scores = [safe_float(w["best_stability_score"]) for w in ws]
        out.append({
            "concept": concept,
            "windows": len(ws),
            "mean_best_stability": mean(scores) if scores else "",
            "median_best_stability": median(scores) if scores else "",
            "strong_or_usable_count": sum(1 for w in ws if w["stability_label"] in ("strong_stability", "usable_for_demo")),
            "strong_or_usable_rate": (
                sum(1 for w in ws if w["stability_label"] in ("strong_stability", "usable_for_demo")) / len(ws)
            ) if ws else "",
            "best_profile_counts": json.dumps(dict(sorted({
                p: sum(1 for w in ws if w["best_profile"] == p)
                for p in sorted({w["best_profile"] for w in ws})
            }.items()))),
        })
    return out


def plot_profile_overview(summary: List[Dict[str, Any]], out: Path) -> None:
    profiles = [r["profile"] for r in summary]
    genuine = [safe_float(r["genuine_mean"]) for r in summary]
    diff = [safe_float(r["different_mean"]) for r in summary]

    x = range(len(profiles))
    width = 0.35
    plt.figure(figsize=(10, 5.5))
    plt.bar([i - width/2 for i in x], genuine, width, label="same participant + same concept")
    plt.bar([i + width/2 for i in x], diff, width, label="different concept")
    plt.xticks(list(x), profiles)
    plt.ylim(0, 1)
    plt.ylabel("Mean token similarity")
    plt.title("Clean dataset: stroke-token similarity by encoder profile")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out)
    plt.close()


def plot_distribution(pairwise_balanced: List[Dict[str, Any]], out: Path) -> None:
    genuine = [safe_float(r["similarity"]) for r in pairwise_balanced if r["pair_type"] == "genuine_same_participant_same_concept"]
    diff = [safe_float(r["similarity"]) for r in pairwise_balanced if r["different_concept"]]

    plt.figure(figsize=(10, 5.5))
    plt.hist(diff, bins=20, alpha=0.60, label="different concept")
    plt.hist(genuine, bins=20, alpha=0.60, label="same participant + same concept")
    plt.xlim(0, 1)
    plt.xlabel("Token similarity")
    plt.ylabel("Pair count")
    plt.title("Clean dataset: same-concept vs different-concept distributions (balanced)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out)
    plt.close()


def plot_concept_summary(concept_summary: List[Dict[str, Any]], out: Path) -> None:
    rows = [r for r in concept_summary if r["genuine_mean"] != ""]
    rows.sort(key=lambda r: safe_float(r["genuine_mean"]), reverse=True)

    concepts = [r["concept"] for r in rows]
    genuine = [safe_float(r["genuine_mean"]) for r in rows]
    diff = [safe_float(r["different_concept_mean"]) for r in rows]
    nearest = [safe_float(r["nearest_different_concept_similarity"]) for r in rows]

    x = range(len(concepts))
    width = 0.25
    plt.figure(figsize=(12, 5.8))
    plt.bar([i - width for i in x], genuine, width, label="avg same-concept redraw")
    plt.bar([i for i in x], diff, width, label="avg different concept")
    plt.bar([i + width for i in x], nearest, width, label="nearest different concept")
    plt.xticks(list(x), concepts, rotation=35, ha="right")
    plt.ylim(0, 1)
    plt.ylabel("Token similarity")
    plt.title("Clean dataset: cluster separation by concept (balanced)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out)
    plt.close()


def plot_enrollment_summary(enroll_summary: List[Dict[str, Any]], out: Path) -> None:
    rows = [r for r in enroll_summary if r["windows"]]
    rows.sort(key=lambda r: safe_float(r["median_best_stability"]), reverse=True)

    concepts = [r["concept"] for r in rows]
    med = [safe_float(r["median_best_stability"]) for r in rows]
    rate = [safe_float(r["strong_or_usable_rate"]) for r in rows]

    x = range(len(concepts))
    width = 0.35
    plt.figure(figsize=(12, 5.8))
    plt.bar([i - width/2 for i in x], med, width, label="median best enrollment stability")
    plt.bar([i + width/2 for i in x], rate, width, label="usable/strong enrollment rate")
    plt.xticks(list(x), concepts, rotation=35, ha="right")
    plt.ylim(0, 1)
    plt.ylabel("Score / rate")
    plt.title("Clean dataset: 3-attempt enrollment windows")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out)
    plt.close()


def write_readme(path: Path, n_samples: int, profile_summary: List[Dict[str, Any]], enrollment_summary: List[Dict[str, Any]]) -> None:
    lines = []
    lines.append("# Drawing-RNG clean dataset run\n")
    lines.append(f"Samples analyzed: **{n_samples}**\n")
    lines.append("\n## Profile summary\n")
    lines.append("| Profile | Genuine pairs | Different-concept pairs | Genuine mean | Different mean | Separation | AUC |\n")
    lines.append("|---|---:|---:|---:|---:|---:|---:|\n")
    for r in profile_summary:
        lines.append(
            f"| {r['profile']} | {r['genuine_pairs']} | {r['different_concept_pairs']} | "
            f"{safe_float(r['genuine_mean']):.3f} | {safe_float(r['different_mean']):.3f} | "
            f"{safe_float(r['separation_mean']):.3f} | {safe_float(r['auc_genuine_vs_different']):.3f} |\n"
        )

    lines.append("\n## Enrollment summary\n")
    lines.append("| Concept | Windows | Median best stability | Usable/strong rate |\n")
    lines.append("|---|---:|---:|---:|\n")
    for r in sorted(enrollment_summary, key=lambda x: safe_float(x["median_best_stability"]), reverse=True):
        lines.append(
            f"| {r['concept']} | {r['windows']} | "
            f"{safe_float(r['median_best_stability']):.3f} | "
            f"{safe_float(r['strong_or_usable_rate']):.3f} |\n"
        )

    lines.append("\n## Notes\n")
    lines.append("- `genuine_same_participant_same_concept` is the redraw-stability proxy after manual cleaning.\n")
    lines.append("- Enrollment windows use consecutive 3-attempt groups within each participant+concept group.\n")
    lines.append("- These scores are representation stability metrics, not cryptographic entropy estimates.\n")
    path.write_text("".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["supabase-clean", "clean-jsonl"], default="supabase-clean")
    parser.add_argument("--input", type=Path, default=Path("filtered_sync_output/clean_records.jsonl"))
    parser.add_argument("--clean-table", default="stroke_samples_clean")
    parser.add_argument("--out", type=Path, default=Path("results/clean_dataset_run"))
    parser.add_argument("--profiles", nargs="+", default=["strict", "balanced", "tolerant"], choices=sorted(PROFILES))
    parser.add_argument("--window-size", type=int, default=3)
    args = parser.parse_args()

    if args.source == "supabase-clean":
        raw_rows = load_supabase_clean(args.clean_table)
    else:
        raw_rows = load_clean_jsonl(args.input)

    samples = []
    for idx, row in enumerate(raw_rows, start=1):
        s = normalize_sample(row, idx)
        if s:
            samples.append(s)

    if not samples:
        raise SystemExit("No valid clean samples found.")

    print(f"[info] Loaded clean samples: {len(samples)}")
    print("[info] Clean participant+concept groups:")
    groups = defaultdict(int)
    for s in samples:
        groups[(s["clean_participant_id"], s["clean_concept"])] += 1
    for (p, c), n in sorted(groups.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {p} / {c}: {n}")

    args.out.mkdir(parents=True, exist_ok=True)
    graphs = args.out / "graphs"
    graphs.mkdir(parents=True, exist_ok=True)

    # Save normalized samples.
    write_csv(
        args.out / "samples_clean_normalized.csv",
        samples,
        ["sample_id", "source_id", "clean_participant_id", "clean_concept", "clean_redraw_id", "created_at", "original_participant_id", "original_concept", "notes"],
    )

    encoded_by_profile = encode_samples(samples, args.profiles)

    # Save encoded samples.
    encoded_rows = []
    for profile, encoded in encoded_by_profile.items():
        for s in encoded.values():
            encoded_rows.append({
                "profile": profile,
                "sample_id": s["sample_id"],
                "clean_participant_id": s["clean_participant_id"],
                "clean_concept": s["clean_concept"],
                "clean_redraw_id": s["clean_redraw_id"],
                "token_count": s["token_count"],
                "encode_ok": s["encode_ok"],
                "encode_error": s["encode_error"],
                "weak_seed_flags": s["weak_seed_flags"],
                "serialized": s["serialized"],
            })
    write_csv(
        args.out / "samples_clean_encoded.csv",
        encoded_rows,
        ["profile", "sample_id", "clean_participant_id", "clean_concept", "clean_redraw_id", "token_count", "encode_ok", "encode_error", "weak_seed_flags", "serialized"],
    )

    # Pairwise.
    pairwise_all: List[Dict[str, Any]] = []
    for profile, encoded in encoded_by_profile.items():
        pw = compute_pairwise(encoded, profile)
        print(f"[info] Pairwise comparisons profile={profile}: n={len(encoded)}, pairs={len(pw)}")
        pairwise_all.extend(pw)

    write_csv(
        args.out / "pairwise_similarity_clean.csv",
        pairwise_all,
        [
            "profile", "sample_a", "sample_b",
            "participant_a", "participant_b", "concept_a", "concept_b",
            "redraw_a", "redraw_b",
            "same_participant", "same_concept", "different_concept",
            "pair_type", "similarity", "token_count_a", "token_count_b",
        ],
    )

    profile_summary = summarize_profiles(pairwise_all)
    write_csv(
        args.out / "profile_summary_clean.csv",
        profile_summary,
        [
            "profile", "genuine_pairs", "different_concept_pairs", "same_concept_different_participant_pairs",
            "genuine_mean", "genuine_median", "genuine_p10", "genuine_p25", "genuine_p75",
            "different_mean", "different_median", "same_concept_diff_participant_mean",
            "separation_mean", "auc_genuine_vs_different",
        ],
    )

    balanced_pw = [r for r in pairwise_all if r["profile"] == "balanced"]
    concept_summary = summarize_concepts_balanced(balanced_pw)
    write_csv(
        args.out / "concept_summary_balanced_clean.csv",
        concept_summary,
        [
            "concept", "genuine_pairs", "genuine_mean", "genuine_median",
            "same_concept_different_participant_pairs", "same_concept_different_participant_mean",
            "different_concept_pairs_touching_concept", "different_concept_mean",
            "nearest_different_concept_similarity", "margin_vs_different_mean",
        ],
    )

    # Enrollment mode.
    windows = enrollment_windows(encoded_by_profile, window_size=args.window_size)
    write_csv(
        args.out / "enrollment_windows.csv",
        windows,
        [
            "participant", "concept", "window_start", "sample_ids", "attempt_count",
            "best_profile", "best_stability_score", "stability_label",
            "strict_score", "balanced_score", "tolerant_score", "best_pair_scores",
        ],
    )

    enrollment_summary = summarize_enrollment(windows)
    write_csv(
        args.out / "enrollment_summary.csv",
        enrollment_summary,
        [
            "concept", "windows", "mean_best_stability", "median_best_stability",
            "strong_or_usable_count", "strong_or_usable_rate", "best_profile_counts",
        ],
    )

    # Graphs.
    plot_profile_overview(profile_summary, graphs / "profile_similarity_overview_clean.png")
    plot_distribution(balanced_pw, graphs / "same_vs_different_distribution_balanced_clean.png")
    plot_concept_summary(concept_summary, graphs / "cluster_separation_by_concept_balanced_clean.png")
    plot_enrollment_summary(enrollment_summary, graphs / "enrollment_summary_clean.png")

    write_readme(args.out / "README.md", len(samples), profile_summary, enrollment_summary)

    print(f"[done] Results written to: {args.out}")
    print("[done] Key files:")
    for p in [
        args.out / "README.md",
        args.out / "profile_summary_clean.csv",
        args.out / "concept_summary_balanced_clean.csv",
        args.out / "enrollment_windows.csv",
        args.out / "enrollment_summary.csv",
        graphs / "profile_similarity_overview_clean.png",
        graphs / "same_vs_different_distribution_balanced_clean.png",
        graphs / "cluster_separation_by_concept_balanced_clean.png",
        graphs / "enrollment_summary_clean.png",
    ]:
        print(f"  - {p}")


if __name__ == "__main__":
    main()
