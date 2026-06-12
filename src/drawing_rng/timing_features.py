from __future__ import annotations

"""Timing/rhythm diagnostics for Drawing-RNG.

This module intentionally stays diagnostic-only.  Shape verification remains the
primary unlock gate; timing features are logged so the pilot can measure whether
owner redraws and informed forgeries separate by motor rhythm.

Input points may be [x, y] or [x, y, t].  The browser records t from
performance.now() in milliseconds.  If timing is unavailable, functions return a
clear has_timing=False result rather than affecting decisions.
"""

import math
from statistics import median
from typing import Any, Dict, List, Sequence, Tuple


def _xy(p: Sequence[Any]) -> Tuple[float, float]:
    return float(p[0]), float(p[1])


def _t(p: Sequence[Any]) -> float | None:
    if len(p) < 3:
        return None
    try:
        v = float(p[2])
    except Exception:
        return None
    if not math.isfinite(v):
        return None
    return v


def _dist(a: Sequence[Any], b: Sequence[Any]) -> float:
    ax, ay = _xy(a)
    bx, by = _xy(b)
    return math.hypot(bx - ax, by - ay)


def _cv(values: List[float]) -> float:
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if len(vals) < 2:
        return 0.0
    m = sum(vals) / len(vals)
    if abs(m) < 1e-9:
        return 0.0
    var = sum((x - m) ** 2 for x in vals) / len(vals)
    return math.sqrt(var) / abs(m)


def _ratios(values: List[float]) -> List[float]:
    vals = [max(0.0, float(v)) for v in values]
    total = sum(vals)
    if total <= 1e-9:
        return []
    return [v / total for v in vals]


def _log_ratio_similarity(a: float, b: float, tolerance: float = 1.10) -> float:
    if a <= 1e-9 and b <= 1e-9:
        return 1.0
    if a <= 1e-9 or b <= 1e-9:
        return 0.0
    return max(0.0, min(1.0, 1.0 - abs(math.log(a / b)) / tolerance))


def _vector_l1_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    n = max(len(a), len(b), 1)
    aa = list(a) + [0.0] * (n - len(a))
    bb = list(b) + [0.0] * (n - len(b))
    # L1 distance between probability vectors is in [0, 2].
    d = sum(abs(aa[i] - bb[i]) for i in range(n))
    return max(0.0, min(1.0, 1.0 - d / 2.0))


def extract_timing_signature(raw_strokes: Sequence[Sequence[Sequence[Any]]]) -> Dict[str, Any]:
    stroke_durations: List[float] = []
    stroke_lengths: List[float] = []
    stroke_speeds: List[float] = []
    stroke_start_times: List[float] = []
    stroke_end_times: List[float] = []

    for stroke in raw_strokes or []:
        pts = list(stroke or [])
        if len(pts) < 2:
            continue
        times = [_t(p) for p in pts]
        if any(t is None for t in times):
            continue
        t0 = float(times[0])
        t1 = float(times[-1])
        duration = max(0.0, t1 - t0)
        length = sum(_dist(pts[i - 1], pts[i]) for i in range(1, len(pts)))
        stroke_start_times.append(t0)
        stroke_end_times.append(t1)
        stroke_durations.append(duration)
        stroke_lengths.append(length)
        stroke_speeds.append(length / max(duration, 1e-6))

    if not stroke_durations:
        return {
            "has_timing": False,
            "stroke_count_with_timing": 0,
            "reason": "timestamps_missing_or_insufficient",
        }

    order = sorted(range(len(stroke_durations)), key=lambda i: stroke_start_times[i])
    starts = [stroke_start_times[i] for i in order]
    ends = [stroke_end_times[i] for i in order]
    durs = [stroke_durations[i] for i in order]
    lengths = [stroke_lengths[i] for i in order]
    speeds = [stroke_speeds[i] for i in order]
    pauses = [max(0.0, starts[i] - ends[i - 1]) for i in range(1, len(starts))]
    total_duration = max(0.0, max(ends) - min(starts)) if starts else 0.0
    active_duration = sum(durs)
    pause_total = sum(pauses)

    return {
        "has_timing": True,
        "stroke_count_with_timing": len(durs),
        "total_duration_ms": float(total_duration),
        "active_duration_ms": float(active_duration),
        "pause_duration_ms": float(pause_total),
        "stroke_durations_ms": [float(x) for x in durs],
        "penup_pauses_ms": [float(x) for x in pauses],
        "stroke_duration_ratios": _ratios(durs),
        "pause_duration_ratios": _ratios(pauses),
        "stroke_lengths_px": [float(x) for x in lengths],
        "speed_px_per_ms": [float(x) for x in speeds],
        "duration_cv": float(_cv(durs)),
        "pause_cv": float(_cv(pauses)),
        "speed_cv": float(_cv(speeds)),
        "median_speed_px_per_ms": float(median(speeds) if speeds else 0.0),
    }


def learn_timing_model(attempts: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    sigs = [extract_timing_signature((a or {}).get("strokes") or []) for a in attempts or []]
    sigs = [s for s in sigs if s.get("has_timing")]
    if not sigs:
        return {"has_timing": False, "timing_stability_score": None, "attempt_count": 0}

    total = [float(s.get("total_duration_ms") or 0.0) for s in sigs]
    active = [float(s.get("active_duration_ms") or 0.0) for s in sigs]
    speed = [float(s.get("median_speed_px_per_ms") or 0.0) for s in sigs]
    count = [int(s.get("stroke_count_with_timing") or 0) for s in sigs]

    ratio_scores: List[float] = []
    for i in range(len(sigs)):
        for j in range(i + 1, len(sigs)):
            dur_sim = _vector_l1_similarity(sigs[i].get("stroke_duration_ratios") or [], sigs[j].get("stroke_duration_ratios") or [])
            pause_sim = _vector_l1_similarity(sigs[i].get("pause_duration_ratios") or [], sigs[j].get("pause_duration_ratios") or [])
            total_sim = _log_ratio_similarity(float(sigs[i].get("total_duration_ms") or 0.0), float(sigs[j].get("total_duration_ms") or 0.0))
            speed_sim = _log_ratio_similarity(float(sigs[i].get("median_speed_px_per_ms") or 0.0), float(sigs[j].get("median_speed_px_per_ms") or 0.0))
            ratio_scores.append(0.40 * dur_sim + 0.20 * pause_sim + 0.20 * total_sim + 0.20 * speed_sim)

    return {
        "has_timing": True,
        "attempt_count": len(sigs),
        "timing_stability_score": float(median(ratio_scores) if ratio_scores else 1.0),
        "median_total_duration_ms": float(median(total)),
        "median_active_duration_ms": float(median(active)),
        "median_speed_px_per_ms": float(median(speed)),
        "median_stroke_count": int(round(median(count))),
        "reference_signatures": sigs,
        "note": "Diagnostic-only timing/rhythm model; not required for unlock decisions yet.",
    }


def compare_timing_model(model: Dict[str, Any] | None, raw_strokes: Sequence[Sequence[Sequence[Any]]]) -> Dict[str, Any]:
    if not isinstance(model, dict) or not model.get("has_timing"):
        return {"has_timing": False, "timing_available": False, "timing_final": None, "reason": "no_enrollment_timing_model"}
    sig = extract_timing_signature(raw_strokes)
    if not sig.get("has_timing"):
        return {"has_timing": False, "timing_available": False, "timing_final": None, "reason": "redraw_timing_missing", "redraw_timing": sig}

    refs = model.get("reference_signatures") or []
    scores: List[float] = []
    for ref in refs:
        dur_sim = _vector_l1_similarity(ref.get("stroke_duration_ratios") or [], sig.get("stroke_duration_ratios") or [])
        pause_sim = _vector_l1_similarity(ref.get("pause_duration_ratios") or [], sig.get("pause_duration_ratios") or [])
        total_sim = _log_ratio_similarity(float(ref.get("total_duration_ms") or 0.0), float(sig.get("total_duration_ms") or 0.0))
        speed_sim = _log_ratio_similarity(float(ref.get("median_speed_px_per_ms") or 0.0), float(sig.get("median_speed_px_per_ms") or 0.0))
        stroke_count_sim = max(0.0, 1.0 - abs(int(ref.get("stroke_count_with_timing") or 0) - int(sig.get("stroke_count_with_timing") or 0)) / max(int(ref.get("stroke_count_with_timing") or 1), int(sig.get("stroke_count_with_timing") or 1), 1))
        scores.append(0.34 * dur_sim + 0.18 * pause_sim + 0.18 * total_sim + 0.18 * speed_sim + 0.12 * stroke_count_sim)

    return {
        "has_timing": True,
        "timing_available": True,
        "timing_final": float(max(scores) if scores else 0.0),
        "timing_best_reference_index": int(scores.index(max(scores))) if scores else None,
        "timing_scores_by_reference": [float(x) for x in scores],
        "redraw_timing": sig,
        "threshold_note": "Diagnostic only. Do not use as a hard unlock gate before multi-device validation.",
    }
