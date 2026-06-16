#!/usr/bin/env python3
"""
Export Drawing-RNG Supabase stroke samples and render them back into images.

Purpose:
  - Pull rows from public.stroke_samples in Supabase.
  - Render each saved stroke JSON into a PNG and SVG-like simple image.
  - Produce an index CSV for manual relabeling/classification.
  - Optionally create contact sheets so you can quickly inspect drawings.

Environment variables required:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY

Install:
  pip install supabase pillow

Run:
  python tools/export_render_supabase_drawings.py --out rendered_drawings

Optional:
  python tools/export_render_supabase_drawings.py --out rendered_drawings --limit 200
  python tools/export_render_supabase_drawings.py --out rendered_drawings --no-grid
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont
from supabase import create_client


Point = Tuple[float, float]
Stroke = List[Point]


def slugify(value: Any, fallback: str = "unknown") -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_.-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or fallback


def parse_strokes(raw: Any) -> List[Stroke]:
    """
    Supports:
      strokes = [
        [[x, y], [x, y], ...],
        ...
      ]

    If points include timestamps like [x, y, t], the timestamp is ignored.
    """
    strokes: List[Stroke] = []
    if not isinstance(raw, list):
        return strokes

    for stroke in raw:
        if not isinstance(stroke, list):
            continue
        parsed: Stroke = []
        for point in stroke:
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                try:
                    parsed.append((float(point[0]), float(point[1])))
                except (TypeError, ValueError):
                    pass
        if len(parsed) >= 2:
            strokes.append(parsed)

    return strokes


def all_points(strokes: List[Stroke]) -> List[Point]:
    return [p for s in strokes for p in s]


def detect_canvas_size(row: Dict[str, Any], strokes: List[Stroke]) -> Tuple[int, int]:
    """
    Prefer stored canvas_size. Fall back to bounding box with a little padding.
    """
    size = row.get("canvas_size")
    if isinstance(size, list) and len(size) >= 2:
        try:
            w, h = int(size[0]), int(size[1])
            if w > 0 and h > 0:
                return w, h
        except (TypeError, ValueError):
            pass

    pts = all_points(strokes)
    if not pts:
        return 1200, 900

    max_x = max(p[0] for p in pts)
    max_y = max(p[1] for p in pts)
    return max(800, int(max_x + 80)), max(600, int(max_y + 80))


def normalize_to_image(
    strokes: List[Stroke],
    canvas_size: Tuple[int, int],
    out_size: Tuple[int, int],
    margin: int = 40,
) -> List[Stroke]:
    """
    Map original canvas coordinates into a fixed PNG size while preserving aspect ratio.
    """
    src_w, src_h = canvas_size
    out_w, out_h = out_size

    if src_w <= 0 or src_h <= 0:
        src_w, src_h = 1200, 900

    scale = min((out_w - 2 * margin) / src_w, (out_h - 2 * margin) / src_h)
    offset_x = (out_w - src_w * scale) / 2
    offset_y = (out_h - src_h * scale) / 2

    mapped: List[Stroke] = []
    for stroke in strokes:
        mapped.append([(offset_x + x * scale, offset_y + y * scale) for x, y in stroke])
    return mapped


def draw_grid(draw: ImageDraw.ImageDraw, size: Tuple[int, int], step: int = 64) -> None:
    w, h = size
    grid_color = (230, 234, 240)
    for x in range(step, w, step):
        draw.line([(x, 0), (x, h)], fill=grid_color, width=1)
    for y in range(step, h, step):
        draw.line([(0, y), (w, y)], fill=grid_color, width=1)


def render_png(
    row: Dict[str, Any],
    out_path: Path,
    out_size: Tuple[int, int] = (900, 675),
    grid: bool = True,
    label: bool = True,
) -> Dict[str, Any]:
    strokes = parse_strokes(row.get("strokes"))
    canvas_size = detect_canvas_size(row, strokes)
    mapped = normalize_to_image(strokes, canvas_size, out_size)

    img = Image.new("RGB", out_size, (250, 250, 250))
    draw = ImageDraw.Draw(img)

    if grid:
        draw_grid(draw, out_size)

    # Draw each stroke.
    # We intentionally keep this visually simple: dark strokes on a light canvas.
    for stroke in mapped:
        if len(stroke) >= 2:
            draw.line(stroke, fill=(15, 23, 42), width=5, joint="curve")

    # Mark stroke starts with tiny dots so you can inspect drawing order if needed.
    for idx, stroke in enumerate(mapped, start=1):
        if stroke:
            x, y = stroke[0]
            r = 4
            draw.ellipse((x - r, y - r, x + r, y + r), fill=(2, 132, 199))

    if label:
        concept = row.get("concept") or row.get("sample_name") or "unknown"
        participant = row.get("participant_id") or "unknown_participant"
        row_id = str(row.get("id") or "")[:8]
        txt = f"{slugify(concept)} | {participant} | {row_id} | strokes={len(strokes)}"
        draw.rectangle((0, out_size[1] - 30, out_size[0], out_size[1]), fill=(248, 250, 252))
        draw.text((10, out_size[1] - 23), txt, fill=(15, 23, 42))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)

    point_count = sum(len(s) for s in strokes)
    return {
        "stroke_count": len(strokes),
        "point_count": point_count,
        "canvas_width": canvas_size[0],
        "canvas_height": canvas_size[1],
        "png_path": str(out_path),
    }


def render_contact_sheet(
    image_paths: List[Path],
    out_path: Path,
    thumb_size: Tuple[int, int] = (260, 195),
    cols: int = 4,
) -> None:
    if not image_paths:
        return

    rows = math.ceil(len(image_paths) / cols)
    sheet = Image.new("RGB", (cols * thumb_size[0], rows * thumb_size[1]), (255, 255, 255))

    for i, img_path in enumerate(image_paths):
        try:
            img = Image.open(img_path).convert("RGB")
        except Exception:
            continue
        img.thumbnail((thumb_size[0] - 8, thumb_size[1] - 8))
        x = (i % cols) * thumb_size[0] + 4
        y = (i // cols) * thumb_size[1] + 4
        sheet.paste(img, (x, y))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)


def fetch_all_supabase(limit: Optional[int] = None, page_size: int = 1000) -> List[Dict[str, Any]]:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    if not url or not key:
        raise SystemExit(
            "Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY environment variables."
        )

    supabase = create_client(url, key)
    rows: List[Dict[str, Any]] = []
    start = 0

    while True:
        end = start + page_size - 1
        result = (
            supabase
            .table("stroke_samples")
            .select("*")
            .order("created_at", desc=False)
            .range(start, end)
            .execute()
        )
        batch = result.data or []
        rows.extend(batch)

        if limit is not None and len(rows) >= limit:
            return rows[:limit]

        if len(batch) < page_size:
            break

        start += page_size

    return rows


def load_local_jsons(input_dir: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in sorted(input_dir.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        if "strokes" not in data:
            continue

        data.setdefault("id", path.stem)
        data.setdefault("sample_name", path.stem)
        data.setdefault("created_at", data.get("saved_at_utc") or "")
        data["_source_file"] = str(path)
        rows.append(data)

    return rows


def write_index_csv(rows_for_index: List[Dict[str, Any]], out_csv: Path) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "row_id",
        "created_at",
        "participant_id_original",
        "concept_original",
        "sample_name",
        "redraw_id_original",
        "stroke_count",
        "point_count",
        "canvas_width",
        "canvas_height",
        "png_path",
        "manual_person_id",
        "manual_concept_label",
        "manual_redraw_group",
        "discard",
        "discard_reason",
        "notes_original",
        "review_notes",
    ]

    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

        for row in rows_for_index:
            writer.writerow({
                "row_id": row.get("id", ""),
                "created_at": row.get("created_at", row.get("saved_at_utc", "")),
                "participant_id_original": row.get("participant_id", ""),
                "concept_original": row.get("concept", ""),
                "sample_name": row.get("sample_name", row.get("name", "")),
                "redraw_id_original": row.get("redraw_id", ""),
                "stroke_count": row.get("_render", {}).get("stroke_count", ""),
                "point_count": row.get("_render", {}).get("point_count", ""),
                "canvas_width": row.get("_render", {}).get("canvas_width", ""),
                "canvas_height": row.get("_render", {}).get("canvas_height", ""),
                "png_path": row.get("_render", {}).get("png_path", ""),
                # Fill these manually after inspecting images:
                "manual_person_id": "",
                "manual_concept_label": "",
                "manual_redraw_group": "",
                "discard": "",
                "discard_reason": "",
                "notes_original": row.get("notes", ""),
                "review_notes": "",
            })


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["supabase", "local"], default="supabase")
    parser.add_argument("--input", type=Path, default=Path("datasets/stroke_samples"))
    parser.add_argument("--out", type=Path, default=Path("rendered_drawings"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--width", type=int, default=900)
    parser.add_argument("--height", type=int, default=675)
    parser.add_argument("--no-grid", action="store_true")
    parser.add_argument("--no-label", action="store_true")
    parser.add_argument("--contact-sheets", action="store_true", default=True)
    args = parser.parse_args()

    if args.source == "supabase":
        rows = fetch_all_supabase(limit=args.limit)
    else:
        rows = load_local_jsons(args.input)
        if args.limit is not None:
            rows = rows[:args.limit]

    if not rows:
        raise SystemExit("No rows/samples found.")

    images_root = args.out / "images"
    raw_root = args.out / "raw_json"
    image_paths: List[Path] = []

    for i, row in enumerate(rows, start=1):
        row_id = slugify(str(row.get("id") or f"row_{i}")[:12], f"row_{i}")
        concept = slugify(row.get("concept") or row.get("sample_name") or row.get("name"), "unknown")
        participant = slugify(row.get("participant_id"), "unknown_participant")

        # Save a raw JSON copy for reproducibility.
        raw_dir = raw_root / participant / concept
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / f"{i:04d}_{row_id}.json"
        raw_path.write_text(json.dumps(row, indent=2, ensure_ascii=False), encoding="utf-8")

        png_path = images_root / participant / concept / f"{i:04d}_{row_id}.png"
        render_meta = render_png(
            row,
            png_path,
            out_size=(args.width, args.height),
            grid=not args.no_grid,
            label=not args.no_label,
        )
        row["_render"] = render_meta
        image_paths.append(png_path)

    write_index_csv(rows, args.out / "review_index.csv")

    if args.contact_sheets:
        # Global contact sheet
        render_contact_sheet(image_paths, args.out / "contact_sheets" / "all_samples.png")

        # Contact sheets by original concept
        by_concept: Dict[str, List[Path]] = {}
        for row, path in zip(rows, image_paths):
            concept = slugify(row.get("concept") or row.get("sample_name") or row.get("name"), "unknown")
            by_concept.setdefault(concept, []).append(path)

        for concept, paths in sorted(by_concept.items()):
            render_contact_sheet(paths, args.out / "contact_sheets" / f"{concept}.png")

    print(f"[done] Rendered {len(rows)} samples.")
    print(f"[done] Images: {images_root}")
    print(f"[done] Review CSV: {args.out / 'review_index.csv'}")
    print(f"[done] Contact sheets: {args.out / 'contact_sheets'}")


if __name__ == "__main__":
    main()
