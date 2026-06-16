#!/usr/bin/env python3
"""
Sync a manually-filtered Drawing-RNG rendered-image dataset back to raw JSON / Supabase.

Use case:
  You exported Supabase stroke samples into:
    rendered_drawings/images/<participant>/<concept>/<index>_<rowidprefix>.png
    rendered_drawings/raw_json/<participant>/<concept>/<index>_<rowidprefix>.json

  Then you manually deleted the bad PNGs and kept only the good ones, e.g.:
    filtered_dataset.zip
      p_aee1edd9/star/0008_ff335ec7-014.png
      p_d910eea5_new/heart/0102_89994407-27c.png
      ...

  This script:
    1. Reads the kept PNG list.
    2. Matches each PNG back to its raw JSON using the row-id prefix in the filename.
    3. Recomputes stroke tokens using your local stroke_token_encoder.py.
    4. Builds a clean manifest and clean JSON records.
    5. Optionally updates Supabase in one of two safe-ish ways:
       - insert into a separate clean table: stroke_samples_clean
       - delete non-kept rows from the original stroke_samples table

Recommended:
  Use --supabase-mode replace-clean-table first.
  Do NOT delete from the original table until you have verified the clean export.

Install:
  pip install supabase pillow

Required for token recomputation:
  Run from your repo root, with src/drawing_rng/stroke_token_encoder.py present.

Supabase env vars:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zipfile import ZipFile

# Make local src/ importable when run from repo root.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    from drawing_rng.stroke_token_encoder import encode_json_payload
except Exception as exc:
    encode_json_payload = None
    ENCODER_IMPORT_ERROR = exc
else:
    ENCODER_IMPORT_ERROR = None


# Keep these aligned with your current v0.3 frontend profiles.
PROFILES: Dict[str, Dict[str, Any]] = {
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
}


def slugify(value: Any, fallback: str = "unknown") -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_.-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or fallback


def iter_png_paths_from_zip(zip_path: Path) -> List[str]:
    with ZipFile(zip_path, "r") as z:
        return sorted([n for n in z.namelist() if n.lower().endswith(".png")])


def iter_png_paths_from_dir(filtered_dir: Path) -> List[str]:
    return sorted(str(p.relative_to(filtered_dir)).replace("\\", "/") for p in filtered_dir.rglob("*.png"))


def parse_filtered_png_path(rel_path: str) -> Optional[Dict[str, Any]]:
    """
    Expected:
      participant/concept/0057_a61e6c79-9cd.png

    The renderer produced filenames:
      {index:04d}_{row_id_first_12_slug}.png

    We use row_id_prefix to find the raw JSON and Supabase row.
    """
    parts = rel_path.replace("\\", "/").split("/")
    if len(parts) < 3:
        return None

    participant = parts[-3]
    concept = parts[-2]
    filename = parts[-1]

    m = re.match(r"^(?P<ordinal>\d+)_+(?P<row_prefix>[a-f0-9-]+)\.png$", filename, flags=re.I)
    if not m:
        return None

    return {
        "image_rel_path": rel_path,
        "clean_participant_id": participant,
        "clean_concept": concept,
        "image_filename": filename,
        "image_ordinal": int(m.group("ordinal")),
        "row_id_prefix": m.group("row_prefix").lower(),
    }


def index_raw_json(raw_json_dir: Path) -> Dict[str, Path]:
    """
    Map row_id_prefix -> raw JSON path.

    Raw JSON filenames usually look like:
      0057_a61e6c79-9cd.json
    """
    index: Dict[str, Path] = {}
    for path in raw_json_dir.rglob("*.json"):
        m = re.match(r"^\d+_+(?P<row_prefix>[a-f0-9-]+)\.json$", path.name, flags=re.I)
        if not m:
            continue
        prefix = m.group("row_prefix").lower()
        index[prefix] = path
    return index


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def get_row_id(row: Dict[str, Any], fallback_prefix: str) -> str:
    return str(row.get("id") or row.get("row_id") or fallback_prefix)


def compute_tokens(row: Dict[str, Any], profile: str) -> Dict[str, Any]:
    if encode_json_payload is None:
        return {
            "ok": False,
            "error": f"Could not import encoder: {ENCODER_IMPORT_ERROR}",
            "tokens": None,
            "serialized": row.get("serialized"),
            "stats": None,
            "seed_material_hex": None,
        }

    payload = {
        "strokes": row.get("strokes", []),
        "params": PROFILES[profile],
    }

    try:
        result = encode_json_payload(payload)
        return {
            "ok": True,
            "error": None,
            "tokens": result.get("tokens"),
            "serialized": result.get("serialized"),
            "stats": result.get("stats"),
            "seed_material_hex": result.get("seed_material_hex"),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "tokens": None,
            "serialized": row.get("serialized"),
            "stats": None,
            "seed_material_hex": None,
        }


def assign_clean_redraw_indices(entries: List[Dict[str, Any]]) -> None:
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for e in entries:
        groups[(e["clean_participant_id"], e["clean_concept"])].append(e)

    for (_participant, _concept), group in groups.items():
        group.sort(key=lambda e: (e["image_ordinal"], e["row_id_prefix"], e["image_rel_path"]))
        for idx, e in enumerate(group, start=1):
            e["clean_redraw_id"] = idx


def build_clean_record(entry: Dict[str, Any], raw_row: Dict[str, Any], token_info: Dict[str, Any], profile: str) -> Dict[str, Any]:
    source_id = get_row_id(raw_row, entry["row_id_prefix"])
    now = datetime.now(timezone.utc).isoformat()

    return {
        "source_id": source_id,
        "clean_participant_id": entry["clean_participant_id"],
        "clean_concept": entry["clean_concept"],
        "clean_redraw_id": entry["clean_redraw_id"],

        "original_participant_id": raw_row.get("participant_id"),
        "original_concept": raw_row.get("concept"),
        "original_redraw_id": raw_row.get("redraw_id"),
        "original_sample_name": raw_row.get("sample_name") or raw_row.get("name"),

        "strokes": raw_row.get("strokes", []),
        "params": PROFILES[profile],
        "tokens": token_info.get("tokens"),
        "serialized": token_info.get("serialized"),
        "stats": token_info.get("stats"),
        "seed_material_hex": token_info.get("seed_material_hex"),

        "token_profile": profile,
        "tokenize_ok": token_info.get("ok"),
        "tokenize_error": token_info.get("error"),

        "image_rel_path": entry["image_rel_path"],
        "row_id_prefix": entry["row_id_prefix"],
        "cleaned_at": now,
        "created_at": raw_row.get("created_at") or raw_row.get("saved_at_utc"),
        "notes": raw_row.get("notes", ""),
        "raw": raw_row,
    }


def write_outputs(records: List[Dict[str, Any]], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    # Full JSON.
    (out_dir / "clean_records.json").write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")

    # JSONL, easier for tooling.
    with (out_dir / "clean_records.jsonl").open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # CSV manifest.
    fields = [
        "source_id",
        "row_id_prefix",
        "clean_participant_id",
        "clean_concept",
        "clean_redraw_id",
        "original_participant_id",
        "original_concept",
        "original_redraw_id",
        "token_profile",
        "tokenize_ok",
        "tokenize_error",
        "token_count",
        "image_rel_path",
        "created_at",
    ]
    with (out_dir / "clean_manifest.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in records:
            writer.writerow({
                "source_id": r["source_id"],
                "row_id_prefix": r["row_id_prefix"],
                "clean_participant_id": r["clean_participant_id"],
                "clean_concept": r["clean_concept"],
                "clean_redraw_id": r["clean_redraw_id"],
                "original_participant_id": r["original_participant_id"],
                "original_concept": r["original_concept"],
                "original_redraw_id": r["original_redraw_id"],
                "token_profile": r["token_profile"],
                "tokenize_ok": r["tokenize_ok"],
                "tokenize_error": r["tokenize_error"],
                "token_count": len(r.get("tokens") or []),
                "image_rel_path": r["image_rel_path"],
                "created_at": r["created_at"],
            })


def get_supabase():
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SystemExit("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY.")

    return create_client(url, key)


def chunked(items: List[Any], size: int) -> Iterable[List[Any]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def supabase_insert_clean_table(records: List[Dict[str, Any]], clean_table: str, replace: bool) -> None:
    supabase = get_supabase()

    if replace:
        print(f"[warn] Deleting existing rows from clean table: {clean_table}")
        # Requires source_id column. This deletes rows where source_id is not a nonsense value.
        supabase.table(clean_table).delete().neq("clean_concept", "__never__").execute()

    rows = []
    for r in records:
        rows.append({
            "source_id": r["source_id"],
            "clean_participant_id": r["clean_participant_id"],
            "clean_concept": r["clean_concept"],
            "clean_redraw_id": r["clean_redraw_id"],
            "original_participant_id": r["original_participant_id"],
            "original_concept": r["original_concept"],
            "original_redraw_id": r["original_redraw_id"],
            "original_sample_name": r["original_sample_name"],
            "strokes": r["strokes"],
            "params": r["params"],
            "tokens": r["tokens"],
            "serialized": r["serialized"],
            "stats": r["stats"],
            "seed_material_hex": r["seed_material_hex"],
            "token_profile": r["token_profile"],
            "tokenize_ok": r["tokenize_ok"],
            "tokenize_error": r["tokenize_error"],
            "image_rel_path": r["image_rel_path"],
            "row_id_prefix": r["row_id_prefix"],
            "created_at": r["created_at"],
            "notes": r["notes"],
        })

    for batch in chunked(rows, 250):
        supabase.table(clean_table).insert(batch).execute()

    print(f"[done] Inserted {len(rows)} rows into {clean_table}")


def supabase_delete_rest_and_update_original(records: List[Dict[str, Any]], table: str, confirm: bool) -> None:
    if not confirm:
        raise SystemExit(
            "Refusing destructive delete without --confirm-delete-rest. "
            "Use --supabase-mode delete-rest --confirm-delete-rest if you are sure."
        )

    supabase = get_supabase()
    keep_ids = {str(r["source_id"]) for r in records if r.get("source_id")}
    print(f"[warn] Keeping {len(keep_ids)} IDs and deleting every other row from {table}.")

    # Fetch all IDs.
    all_rows: List[Dict[str, Any]] = []
    start = 0
    page_size = 1000
    while True:
        res = supabase.table(table).select("id").range(start, start + page_size - 1).execute()
        batch = res.data or []
        all_rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size

    all_ids = [str(r["id"]) for r in all_rows]
    delete_ids = [x for x in all_ids if x not in keep_ids]
    print(f"[warn] Deleting {len(delete_ids)} rows from {table}.")

    for row_id in delete_ids:
        supabase.table(table).delete().eq("id", row_id).execute()

    # Update kept rows to cleaned participant/concept/redraw_id + recomputed serialized if available.
    print(f"[info] Updating kept rows with clean labels/tokens.")
    for r in records:
        update = {
            "participant_id": r["clean_participant_id"],
            "concept": r["clean_concept"],
            "redraw_id": r["clean_redraw_id"],
            "sample_name": f'{r["clean_concept"]}_redraw_{int(r["clean_redraw_id"]):02d}',
            "params": r["params"],
            "serialized": r["serialized"],
        }
        supabase.table(table).update(update).eq("id", r["source_id"]).execute()

    print(f"[done] Original table cleaned: {table}")


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--filtered-zip", type=Path, help="Zip containing manually kept PNG images.")
    group.add_argument("--filtered-dir", type=Path, help="Directory containing manually kept PNG images.")

    parser.add_argument("--raw-json-dir", type=Path, default=Path("rendered_drawings/raw_json"))
    parser.add_argument("--out", type=Path, default=Path("filtered_sync_output"))
    parser.add_argument("--profile", choices=sorted(PROFILES), default="balanced")

    parser.add_argument(
        "--supabase-mode",
        choices=["none", "insert-clean-table", "replace-clean-table", "delete-rest"],
        default="none",
        help=(
            "none = local outputs only. "
            "insert-clean-table/replace-clean-table = write to separate clean table. "
            "delete-rest = destructive: delete non-kept rows from original table."
        ),
    )
    parser.add_argument("--clean-table", default="stroke_samples_clean")
    parser.add_argument("--original-table", default="stroke_samples")
    parser.add_argument("--confirm-delete-rest", action="store_true")

    args = parser.parse_args()

    # Read kept image list.
    if args.filtered_zip:
        rel_paths = iter_png_paths_from_zip(args.filtered_zip)
    else:
        rel_paths = iter_png_paths_from_dir(args.filtered_dir)

    entries = []
    bad_paths = []
    for rel in rel_paths:
        parsed = parse_filtered_png_path(rel)
        if parsed:
            entries.append(parsed)
        else:
            bad_paths.append(rel)

    if bad_paths:
        print("[warn] Some PNG paths did not match expected pattern and were ignored:")
        for p in bad_paths[:20]:
            print("  ", p)
        if len(bad_paths) > 20:
            print(f"  ... {len(bad_paths) - 20} more")

    if not entries:
        raise SystemExit("No valid filtered PNG entries found.")

    assign_clean_redraw_indices(entries)

    # Match to raw JSON.
    raw_index = index_raw_json(args.raw_json_dir)
    records: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []

    for e in entries:
        raw_path = raw_index.get(e["row_id_prefix"])
        if raw_path is None:
            missing.append(e)
            continue

        raw_row = load_json(raw_path)
        token_info = compute_tokens(raw_row, args.profile)
        clean_record = build_clean_record(e, raw_row, token_info, args.profile)
        records.append(clean_record)

    args.out.mkdir(parents=True, exist_ok=True)
    if missing:
        (args.out / "missing_raw_json.json").write_text(json.dumps(missing, indent=2), encoding="utf-8")
        print(f"[warn] Missing raw JSON for {len(missing)} kept images. See {args.out / 'missing_raw_json.json'}")

    if not records:
        raise SystemExit("No records matched raw JSON. Check --raw-json-dir path.")

    write_outputs(records, args.out)

    print(f"[done] Kept PNG entries: {len(entries)}")
    print(f"[done] Matched raw JSON records: {len(records)}")
    print(f"[done] Clean manifest: {args.out / 'clean_manifest.csv'}")
    print(f"[done] Clean records: {args.out / 'clean_records.jsonl'}")

    # Optional Supabase sync.
    if args.supabase_mode == "none":
        print("[info] Supabase mode is none. No database changes made.")
        return

    if args.supabase_mode == "insert-clean-table":
        supabase_insert_clean_table(records, args.clean_table, replace=False)
    elif args.supabase_mode == "replace-clean-table":
        supabase_insert_clean_table(records, args.clean_table, replace=True)
    elif args.supabase_mode == "delete-rest":
        supabase_delete_rest_and_update_original(records, args.original_table, confirm=args.confirm_delete_rest)


if __name__ == "__main__":
    main()
