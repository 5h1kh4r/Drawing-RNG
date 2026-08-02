#!/usr/bin/env python3
from __future__ import annotations

import csv
import html
import json
import math
import os
from collections import Counter
from pathlib import Path
from typing import Any

from supabase import create_client


NEGATIVE_TYPES = {
    "blind_impostor",
    "informed_forgery",
    "near_miss",
    "true_wrong_shape",
}

TABLES = {
    "legacy": {
        "enrollments": "drawing_seed_enrollments",
        "verifications": "drawing_seed_verifications",
    },
    "v2": {
        "enrollments": "draw2seed_v2_enrollments",
        "verifications": "draw2seed_v2_verifications",
    },
}


def load_env(path: Path) -> None:
    if not path.exists():
        return

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)

        os.environ.setdefault(
            key.strip(),
            value.strip().strip('"').strip("'"),
        )


def parse_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value

    return value


def truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "t",
    }


def number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None

    return parsed if math.isfinite(parsed) else None


def point_from(value: Any) -> tuple[float, float] | None:
    value = parse_json(value)

    if isinstance(value, (list, tuple)) and len(value) >= 2:
        x = number(value[0])
        y = number(value[1])

        if x is not None and y is not None:
            return x, y

        return None

    if not isinstance(value, dict):
        return None

    x = None
    y = None

    for key in ("x", "clientX", "pageX", "offsetX", "screenX"):
        if key in value:
            x = number(value[key])
            break

    for key in ("y", "clientY", "pageY", "offsetY", "screenY"):
        if key in value:
            y = number(value[key])
            break

    if x is None or y is None:
        return None

    return x, y


def normalize_strokes(value: Any) -> list[list[tuple[float, float]]]:
    value = parse_json(value)

    if value is None:
        return []

    if isinstance(value, dict):
        for key in (
            "strokes",
            "raw_strokes",
            "redraw_strokes",
            "points",
            "path",
            "samples",
            "stroke",
        ):
            if key in value:
                return normalize_strokes(value[key])

        return []

    if not isinstance(value, list) or not value:
        return []

    first_point = point_from(value[0])

    if first_point is not None:
        stroke = []

        for raw_point in value:
            parsed = point_from(raw_point)

            if parsed is not None:
                stroke.append(parsed)

        return [stroke] if stroke else []

    strokes: list[list[tuple[float, float]]] = []

    for item in value:
        strokes.extend(normalize_strokes(item))

    return strokes


def enrollment_attempts(row: dict[str, Any]) -> list[list[list[tuple[float, float]]]]:
    raw = parse_json(row.get("attempts"))

    if isinstance(raw, dict):
        raw = raw.get("attempts", raw.get("samples", raw))

    if not isinstance(raw, list):
        return []

    # A raw point list or stroke list represents one attempt.
    if raw and point_from(raw[0]) is not None:
        return [normalize_strokes(raw)]

    attempts = []

    for item in raw:
        strokes = normalize_strokes(item)

        if strokes:
            attempts.append(strokes)

    return attempts


def svg_for(
    strokes: list[list[tuple[float, float]]],
    width: int = 300,
    height: int = 230,
) -> str:
    points = [
        point
        for stroke in strokes
        for point in stroke
    ]

    if not points:
        return f"""
<svg viewBox="0 0 {width} {height}" role="img">
  <rect width="{width}" height="{height}" fill="white"/>
  <text x="18" y="32" fill="#b42318" font-size="14">
    No renderable trajectory points
  </text>
</svg>
"""

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]

    min_x = min(xs)
    max_x = max(xs)
    min_y = min(ys)
    max_y = max(ys)

    source_width = max(max_x - min_x, 1.0)
    source_height = max(max_y - min_y, 1.0)

    padding = 20

    scale = min(
        (width - 2 * padding) / source_width,
        (height - 2 * padding) / source_height,
    )

    offset_x = (width - source_width * scale) / 2 - min_x * scale
    offset_y = (height - source_height * scale) / 2 - min_y * scale

    paths = []

    for stroke in strokes:
        if not stroke:
            continue

        transformed = [
            (
                x * scale + offset_x,
                y * scale + offset_y,
            )
            for x, y in stroke
        ]

        if len(transformed) == 1:
            x, y = transformed[0]

            paths.append(
                f'<circle cx="{x:.2f}" cy="{y:.2f}" '
                f'r="2.5" fill="#111"/>'
            )

            continue

        commands = [
            f"M {transformed[0][0]:.2f} {transformed[0][1]:.2f}"
        ]

        commands.extend(
            f"L {x:.2f} {y:.2f}"
            for x, y in transformed[1:]
        )

        paths.append(
            '<path d="'
            + " ".join(commands)
            + '" fill="none" stroke="#111" '
              'stroke-width="4" stroke-linecap="round" '
              'stroke-linejoin="round"/>'
        )

    return f"""
<svg viewBox="0 0 {width} {height}" role="img">
  <rect width="{width}" height="{height}" fill="white"/>
  {''.join(paths)}
</svg>
"""


def fetch_all(client: Any, table: str) -> list[dict[str, Any]]:
    rows = []
    start = 0
    page_size = 500

    while True:
        response = (
            client.table(table)
            .select("*")
            .range(start, start + page_size - 1)
            .execute()
        )

        page = list(response.data or [])
        rows.extend(page)

        if len(page) < page_size:
            break

        start += page_size

    return rows


def first_value(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)

        if value not in (None, ""):
            return str(value)

    return ""


def metric(row: dict[str, Any], *keys: str) -> str:
    value = first_value(row, *keys)

    if not value:
        return "—"

    try:
        return f"{float(value):.3f}"
    except ValueError:
        return html.escape(value)


def main() -> None:
    root = Path.cwd()

    input_path = (
        root
        / "dev/results/paper_v227_final/primary_analysis_rows.csv"
    )

    output_dir = (
        root
        / "dev/results/paper_v227_final/accepted_negative_review"
    )

    output_dir.mkdir(parents=True, exist_ok=True)

    load_env(root / "dev/.env")

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    if not url or not key:
        raise SystemExit(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required."
        )

    if not input_path.exists():
        raise SystemExit(f"Missing: {input_path}")

    with input_path.open(
        newline="",
        encoding="utf-8",
    ) as handle:
        rows = list(csv.DictReader(handle))

    accepted_rows = [
        row
        for row in rows
        if row.get("attempt_type") in NEGATIVE_TYPES
        and truthy(row.get("draw2seed_accepted"))
        and not first_value(row, "duplicate_of")
    ]

    accepted_rows.sort(
        key=lambda row: (
            row.get("attempt_type", ""),
            row.get("source_cohort", ""),
            first_value(
                row,
                "source_enrollment_id",
                "enrollment_id",
            ),
            first_value(
                row,
                "source_verification_id",
                "verification_id",
            ),
        )
    )

    client = create_client(url, key)

    enrollment_maps = {}
    verification_maps = {}

    for cohort, tables in TABLES.items():
        enrollment_maps[cohort] = {
            str(row["id"]): row
            for row in fetch_all(
                client,
                tables["enrollments"],
            )
        }

        verification_maps[cohort] = {
            str(row["id"]): row
            for row in fetch_all(
                client,
                tables["verifications"],
            )
        }

    cards = []
    review_csv_rows = []
    missing = []

    for index, analysis in enumerate(accepted_rows, start=1):
        cohort = str(analysis.get("source_cohort") or "")

        enrollment_id = first_value(
            analysis,
            "source_enrollment_id",
            "enrollment_id",
        )

        verification_id = first_value(
            analysis,
            "source_verification_id",
            "verification_id",
        )

        enrollment = enrollment_maps.get(cohort, {}).get(
            enrollment_id
        )

        verification = verification_maps.get(cohort, {}).get(
            verification_id
        )

        if enrollment is None or verification is None:
            missing.append(
                {
                    "cohort": cohort,
                    "enrollment_id": enrollment_id,
                    "verification_id": verification_id,
                }
            )

            continue

        attempts = enrollment_attempts(enrollment)
        redraw = normalize_strokes(
            verification.get("redraw_strokes")
        )

        drawing_cells = []

        for attempt_index in range(3):
            attempt = (
                attempts[attempt_index]
                if attempt_index < len(attempts)
                else []
            )

            drawing_cells.append(
                f"""
<div class="drawing-cell">
  <div class="drawing-label">
    Enrollment {attempt_index + 1}
  </div>
  {svg_for(attempt)}
</div>
"""
            )

        drawing_cells.append(
            f"""
<div class="drawing-cell verification-cell">
  <div class="drawing-label">Accepted verification</div>
  {svg_for(redraw)}
</div>
"""
        )

        attempt_type = str(
            analysis.get("attempt_type") or ""
        )

        original_accepted = truthy(
            analysis.get("original_accepted")
        )

        shape_lock = truthy(
            analysis.get("shape_lock_accepted")
            or analysis.get("shape_lock_baseline_pass")
        )

        cards.append(
            f"""
<section
  class="case"
  data-category="{html.escape(attempt_type)}"
  data-cohort="{html.escape(cohort)}"
>
  <header>
    <div>
      <span class="badge">{html.escape(attempt_type)}</span>
      <span class="badge cohort">{html.escape(cohort)}</span>
      <span class="badge current">v2.2.7 accepted</span>
      <span class="badge {'stored-accepted' if original_accepted else 'stored-rejected'}">
        stored/original {'accepted' if original_accepted else 'rejected'}
      </span>
    </div>
    <strong>Case {index}</strong>
  </header>

  <div class="identifiers">
    <div>
      <b>Enrollment ID</b>
      <code>{html.escape(enrollment_id)}</code>
    </div>
    <div>
      <b>Verification ID</b>
      <code>{html.escape(verification_id)}</code>
    </div>
  </div>

  <div class="drawings">
    {''.join(drawing_cells)}
  </div>

  <div class="metrics">
    <div><b>Token</b><span>{metric(analysis, "token_score")}</span></div>
    <div><b>Geometry</b><span>{metric(analysis, "geometry_final")}</span></div>
    <div><b>Structural</b><span>{metric(analysis, "structural_score")}</span></div>
    <div><b>Hough</b><span>{metric(analysis, "hough_style_score")}</span></div>
    <div><b>ShapeLock</b><span>{'accepted' if shape_lock else 'rejected'}</span></div>
  </div>

  <details>
    <summary>Metadata</summary>
    <pre>Dataset: {html.escape(cohort)}
Enrollment: {html.escape(enrollment_id)}
Verification: {html.escape(verification_id)}
Stored label: {html.escape(str(verification.get("attempt_type") or ""))}
Created: {html.escape(str(verification.get("created_at") or ""))}
Participant: {html.escape(str(verification.get("participant_id") or enrollment.get("participant_id") or "—"))}</pre>
  </details>
</section>
"""
        )

        review_csv_rows.append(
            {
                "source_cohort": cohort,
                "attempt_type": attempt_type,
                "enrollment_id": enrollment_id,
                "verification_id": verification_id,
                "original_accepted": original_accepted,
                "token_score": first_value(
                    analysis,
                    "token_score",
                ),
                "geometry_final": first_value(
                    analysis,
                    "geometry_final",
                ),
                "structural_score": first_value(
                    analysis,
                    "structural_score",
                ),
                "hough_style_score": first_value(
                    analysis,
                    "hough_style_score",
                ),
                "review_decision": "",
                "review_notes": "",
            }
        )

    counts = Counter(
        row["attempt_type"]
        for row in review_csv_rows
    )

    count_text = " · ".join(
        f"{label}: {counts.get(label, 0)}"
        for label in sorted(NEGATIVE_TYPES)
    )

    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Draw2Seed accepted-negative review</title>
<style>
  :root {{
    color-scheme: dark;
    font-family: Inter, system-ui, sans-serif;
  }}

  body {{
    margin: 0;
    background: #0d1117;
    color: #e6edf3;
  }}

  main {{
    max-width: 1800px;
    margin: auto;
    padding: 30px;
  }}

  .toolbar {{
    position: sticky;
    top: 0;
    z-index: 5;
    padding: 15px 0;
    background: #0d1117f2;
    border-bottom: 1px solid #30363d;
  }}

  select {{
    margin-right: 12px;
    padding: 8px;
    color: #e6edf3;
    background: #21262d;
    border: 1px solid #484f58;
    border-radius: 7px;
  }}

  .case {{
    margin: 26px 0;
    padding: 20px;
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 14px;
  }}

  header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
  }}

  .badge {{
    display: inline-block;
    margin-right: 6px;
    padding: 5px 9px;
    border-radius: 999px;
    background: #30363d;
    font-size: 13px;
  }}

  .cohort {{
    background: #28436f;
  }}

  .current {{
    background: #1f7844;
  }}

  .stored-rejected {{
    background: #8b4b16;
  }}

  .stored-accepted {{
    background: #8c2d2d;
  }}

  .identifiers {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 15px;
    margin: 18px 0;
  }}

  code {{
    display: block;
    margin-top: 5px;
    color: #a5d6ff;
    overflow-wrap: anywhere;
  }}

  .drawings {{
    display: grid;
    grid-template-columns: repeat(4, minmax(220px, 1fr));
    gap: 14px;
  }}

  .drawing-cell {{
    overflow: hidden;
    background: white;
    border: 1px solid #30363d;
    border-radius: 10px;
  }}

  .verification-cell {{
    outline: 2px solid #3fb950;
  }}

  .drawing-label {{
    padding: 9px 11px;
    color: #e6edf3;
    background: #21262d;
    font-weight: 600;
  }}

  svg {{
    display: block;
    width: 100%;
    height: 230px;
  }}

  .metrics {{
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 10px;
    margin-top: 16px;
  }}

  .metrics div {{
    display: flex;
    justify-content: space-between;
    padding: 10px;
    background: #21262d;
    border-radius: 8px;
  }}

  pre {{
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }}

  @media (max-width: 1100px) {{
    .drawings {{
      grid-template-columns: repeat(2, 1fr);
    }}

    .metrics {{
      grid-template-columns: repeat(2, 1fr);
    }}

    .identifiers {{
      grid-template-columns: 1fr;
    }}
  }}
</style>
</head>
<body>
<main>
  <h1>Draw2Seed v2.2.7 accepted-negative review</h1>

  <p>
    Static SVG rendering of every deduplicated negative attempt accepted
    by the final rerun.
  </p>

  <div class="toolbar">
    <label>
      Category
      <select id="categoryFilter">
        <option value="">All</option>
        <option value="blind_impostor">blind_impostor</option>
        <option value="informed_forgery">informed_forgery</option>
        <option value="near_miss">near_miss</option>
        <option value="true_wrong_shape">true_wrong_shape</option>
      </select>
    </label>

    <label>
      Cohort
      <select id="cohortFilter">
        <option value="">All</option>
        <option value="legacy">legacy</option>
        <option value="v2">v2</option>
      </select>
    </label>

    <span>{html.escape(count_text)} · total: {len(review_csv_rows)}</span>
  </div>

  {''.join(cards)}
</main>

<script>
function applyFilters() {{
  const category =
    document.getElementById('categoryFilter').value;

  const cohort =
    document.getElementById('cohortFilter').value;

  document.querySelectorAll('.case').forEach(card => {{
    const visible =
      (!category || card.dataset.category === category) &&
      (!cohort || card.dataset.cohort === cohort);

    card.style.display = visible ? '' : 'none';
  }});
}}

document
  .getElementById('categoryFilter')
  .addEventListener('change', applyFilters);

document
  .getElementById('cohortFilter')
  .addEventListener('change', applyFilters);
</script>
</body>
</html>
"""

    html_path = (
        output_dir
        / "accepted_negative_review_static.html"
    )

    csv_path = (
        output_dir
        / "accepted_negative_review_static.csv"
    )

    missing_path = output_dir / "missing_static_rows.json"

    html_path.write_text(
        document,
        encoding="utf-8",
    )

    if review_csv_rows:
        with csv_path.open(
            "w",
            newline="",
            encoding="utf-8",
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=list(review_csv_rows[0]),
            )

            writer.writeheader()
            writer.writerows(review_csv_rows)

    missing_path.write_text(
        json.dumps(missing, indent=2),
        encoding="utf-8",
    )

    print("Accepted negative cases:", len(review_csv_rows))

    for label in sorted(NEGATIVE_TYPES):
        print(f"  {label}: {counts.get(label, 0)}")

    print("Missing rows:", len(missing))
    print("Gallery:", html_path)


if __name__ == "__main__":
    main()
