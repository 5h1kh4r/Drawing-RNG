#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import html
import json
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
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "t", "y"}


def parse_json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return fallback


def fetch_all(client: Any, table: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        page = client.table(table).select("*").range(start, start + 499).execute().data or []
        rows.extend(page)
        if len(page) < 500:
            break
        start += 500
    return rows


def metric(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if value in (None, ""):
        return "—"
    try:
        return f"{float(value):.3f}"
    except Exception:
        return html.escape(str(value))


def attempts_from(value: Any) -> list[Any]:
    attempts = parse_json(value, [])
    output = []
    for attempt in attempts if isinstance(attempts, list) else []:
        if isinstance(attempt, dict):
            output.append(attempt.get("strokes") or [])
        elif isinstance(attempt, list):
            output.append(attempt)
    return output[:3]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        default="dev/results/paper_v227_final/primary_analysis_rows.csv",
    )
    parser.add_argument(
        "--out",
        default="dev/results/paper_v227_final/accepted_negative_review",
    )
    args = parser.parse_args()

    load_env(Path("dev/.env"))
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required.")

    with Path(args.input).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    selected = [
        row
        for row in rows
        if row.get("attempt_type") in NEGATIVE_TYPES
        and truthy(row.get("draw2seed_accepted"))
        and not str(row.get("duplicate_of") or "").strip()
    ]
    selected.sort(
        key=lambda row: (
            row.get("attempt_type") or "",
            row.get("source_cohort") or "",
            row.get("source_enrollment_id") or "",
            row.get("source_verification_id") or "",
        )
    )

    client = create_client(url, key)
    enrollment_maps = {}
    verification_maps = {}
    for cohort, tables in TABLES.items():
        enrollment_maps[cohort] = {
            str(row.get("id")): row for row in fetch_all(client, tables["enrollments"])
        }
        verification_maps[cohort] = {
            str(row.get("id")): row for row in fetch_all(client, tables["verifications"])
        }

    cards = []
    review_rows = []
    missing = []
    card_index = 0

    for analysis in selected:
        cohort = str(analysis.get("source_cohort") or "")
        enrollment_id = str(
            analysis.get("source_enrollment_id")
            or analysis.get("enrollment_id")
            or ""
        )
        verification_id = str(
            analysis.get("source_verification_id")
            or analysis.get("verification_id")
            or ""
        )
        enrollment = enrollment_maps.get(cohort, {}).get(enrollment_id)
        verification = verification_maps.get(cohort, {}).get(verification_id)
        if not enrollment or not verification:
            missing.append({
                "source_cohort": cohort,
                "source_enrollment_id": enrollment_id,
                "source_verification_id": verification_id,
            })
            continue

        attempts = attempts_from(enrollment.get("attempts"))
        redraw = parse_json(verification.get("redraw_strokes"), [])
        payload = json.dumps(
            {"attempts": attempts, "redraw": redraw},
            ensure_ascii=False,
        ).replace("</", "<\\/")

        enroll_canvases = "".join(
            f'<div class="drawing"><b>Enrollment {i + 1}</b><canvas id="e-{card_index}-{i}" width="270" height="220"></canvas></div>'
            for i in range(len(attempts))
        )

        original = truthy(analysis.get("original_accepted"))
        shape_lock = truthy(
            analysis.get("shape_lock_accepted")
            or analysis.get("shape_lock_baseline_pass")
        )
        attempt_type = str(analysis.get("attempt_type") or "")

        cards.append(f'''
<section class="case" data-type="{html.escape(attempt_type)}" data-cohort="{html.escape(cohort)}">
  <header>
    <div>
      <span class="badge">{html.escape(attempt_type)}</span>
      <span class="badge">{html.escape(cohort)}</span>
      <span class="badge accepted">v2.2.7 accepted</span>
      <span class="badge {'old-accepted' if original else 'old-rejected'}">stored/original {'accepted' if original else 'rejected'}</span>
    </div>
    <b>Case {card_index + 1}</b>
  </header>
  <div class="ids">
    <button onclick="copyText('{html.escape(enrollment_id)}')">Copy enrollment ID</button><code>{html.escape(enrollment_id)}</code>
    <button onclick="copyText('{html.escape(verification_id)}')">Copy verification ID</button><code>{html.escape(verification_id)}</code>
  </div>
  <div class="grid">
    {enroll_canvases}
    <div class="drawing verify"><b>Accepted verification</b><canvas id="v-{card_index}" width="270" height="220"></canvas></div>
  </div>
  <div class="metrics">
    <span>Token <b>{metric(analysis, 'token_score')}</b></span>
    <span>Geometry <b>{metric(analysis, 'geometry_final')}</b></span>
    <span>Structural <b>{metric(analysis, 'structural_score')}</b></span>
    <span>Hough <b>{metric(analysis, 'hough_style_score')}</b></span>
    <span>ShapeLock <b>{'accepted' if shape_lock else 'rejected'}</b></span>
  </div>
  <details><summary>Metadata</summary><pre>Dataset: {html.escape(cohort)}
Enrollment: {html.escape(enrollment_id)}
Verification: {html.escape(verification_id)}
Stored label: {html.escape(str(verification.get('attempt_type') or attempt_type))}
Created: {html.escape(str(verification.get('created_at') or analysis.get('created_at') or ''))}
Participant: {html.escape(str(verification.get('participant_id') or enrollment.get('participant_id') or '—'))}</pre></details>
  <script type="application/json" id="p-{card_index}">{payload}</script>
</section>
''')

        review_rows.append({
            "source_cohort": cohort,
            "attempt_type": attempt_type,
            "source_enrollment_id": enrollment_id,
            "source_verification_id": verification_id,
            "stored_original_accepted": analysis.get("original_accepted"),
            "v227_draw2seed_accepted": analysis.get("draw2seed_accepted"),
            "shape_lock_accepted": (
                analysis.get("shape_lock_accepted")
                or analysis.get("shape_lock_baseline_pass")
            ),
            "token_score": analysis.get("token_score"),
            "geometry_final": analysis.get("geometry_final"),
            "structural_score": analysis.get("structural_score"),
            "hough_style_score": analysis.get("hough_style_score"),
            "review_decision": "",
            "review_notes": "",
        })
        card_index += 1

    counts = Counter(row["attempt_type"] for row in review_rows)
    count_line = " · ".join(f"{name}: {counts.get(name, 0)}" for name in sorted(NEGATIVE_TYPES))

    page = f'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Draw2Seed accepted-negative review</title>
<style>
body{{margin:0;background:#0d1117;color:#e6edf3;font-family:system-ui,sans-serif}}main{{max-width:1500px;margin:auto;padding:24px}}.toolbar{{position:sticky;top:0;background:#0d1117ee;padding:12px 0;border-bottom:1px solid #30363d;z-index:5}}select,button{{background:#21262d;color:#e6edf3;border:1px solid #484f58;border-radius:7px;padding:7px 10px}}.case{{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:18px;margin:22px 0}}header{{display:flex;justify-content:space-between;gap:12px}}.badge{{display:inline-block;background:#30363d;border-radius:999px;padding:4px 8px;margin-right:4px;font-size:12px}}.accepted{{background:#1f6f43}}.old-rejected{{background:#6e3b17}}.old-accepted{{background:#7d2727}}.ids{{display:grid;grid-template-columns:auto 1fr auto 1fr;gap:8px;align-items:center;margin:14px 0}}code{{overflow-wrap:anywhere;color:#a5d6ff}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(220px,1fr));gap:12px}}.drawing{{background:#fff;border:1px solid #30363d;border-radius:9px;overflow:hidden;color:#e6edf3}}.drawing>b{{display:block;background:#21262d;padding:7px 9px;font-size:13px}}canvas{{display:block;width:100%;height:220px;background:#fff}}.verify{{outline:2px solid #3fb950}}.metrics{{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-top:14px}}.metrics span{{background:#21262d;border-radius:7px;padding:9px;display:flex;justify-content:space-between}}pre{{white-space:pre-wrap}}@media(max-width:1000px){{.grid{{grid-template-columns:repeat(2,1fr)}}.ids{{grid-template-columns:1fr}}.metrics{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body><main>
<h1>Draw2Seed v2.2.7 accepted-negative review</h1>
<p>Only deduplicated negative attempts accepted by the final rerun are shown. Compare the redraw to the enrollment samples before changing any label.</p>
<div class="toolbar">
<label>Category <select id="type" onchange="filterCases()"><option value="">All</option><option>blind_impostor</option><option>informed_forgery</option><option>near_miss</option><option>true_wrong_shape</option></select></label>
<label>Cohort <select id="cohort" onchange="filterCases()"><option value="">All</option><option>legacy</option><option>v2</option></select></label>
<span style="margin-left:12px">{html.escape(count_line)} · total: {len(review_rows)}</span>
</div>
{''.join(cards)}
<script>
function copyText(v){{navigator.clipboard.writeText(v)}}
function filterCases(){{const t=document.getElementById('type').value,c=document.getElementById('cohort').value;document.querySelectorAll('.case').forEach(x=>x.style.display=(!t||x.dataset.type===t)&&(!c||x.dataset.cohort===c)?'':'none')}}
function draw(canvas,strokes){{if(!canvas)return;const ctx=canvas.getContext('2d');ctx.fillStyle='#fff';ctx.fillRect(0,0,canvas.width,canvas.height);const pts=[];(strokes||[]).forEach(s=>(s||[]).forEach(p=>{{if(Number.isFinite(+p.x)&&Number.isFinite(+p.y))pts.push({{x:+p.x,y:+p.y}})}}));if(!pts.length)return;const xs=pts.map(p=>p.x),ys=pts.map(p=>p.y),minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys),w=Math.max(1,maxX-minX),h=Math.max(1,maxY-minY),pad=18,scale=Math.min((canvas.width-2*pad)/w,(canvas.height-2*pad)/h),ox=(canvas.width-w*scale)/2-minX*scale,oy=(canvas.height-h*scale)/2-minY*scale;ctx.lineWidth=4;ctx.lineCap='round';ctx.lineJoin='round';ctx.strokeStyle='#111';(strokes||[]).forEach(s=>{{if(!Array.isArray(s)||s.length<2)return;ctx.beginPath();s.forEach((p,i)=>{{const x=+p.x*scale+ox,y=+p.y*scale+oy;i?ctx.lineTo(x,y):ctx.moveTo(x,y)}});ctx.stroke()}})}}
document.querySelectorAll('.case').forEach((card,i)=>{{const p=JSON.parse(document.getElementById(`p-${{i}}`).textContent);(p.attempts||[]).forEach((a,j)=>draw(document.getElementById(`e-${{i}}-${{j}}`),a));draw(document.getElementById(`v-${{i}}`),p.redraw||[])}})
</script></main></body></html>'''

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "accepted_negative_review.html").write_text(page, encoding="utf-8")
    with (out_dir / "accepted_negative_review.csv").open("w", newline="", encoding="utf-8") as handle:
        if review_rows:
            writer = csv.DictWriter(handle, fieldnames=list(review_rows[0].keys()))
            writer.writeheader()
            writer.writerows(review_rows)
    (out_dir / "missing_rows.json").write_text(json.dumps(missing, indent=2), encoding="utf-8")

    print(f"Accepted negative rows: {len(review_rows)}")
    for name in sorted(NEGATIVE_TYPES):
        print(f"  {name}: {counts.get(name, 0)}")
    print(f"HTML: {out_dir / 'accepted_negative_review.html'}")
    print(f"CSV:  {out_dir / 'accepted_negative_review.csv'}")
    print(f"Missing raw rows: {len(missing)}")


if __name__ == "__main__":
    main()
