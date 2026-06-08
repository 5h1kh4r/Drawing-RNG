from __future__ import annotations

import math
from itertools import combinations
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .stroke_token_encoder import (
    EncoderParams,
    Stroke,
    bounding_box,
    clean_strokes,
    filter_tiny_strokes,
    is_closed,
    normalize_strokes,
    params_from_dict,
    resample_stroke,
    sort_strokes_spatial,
    stroke_box,
)

Point = Tuple[float, float]


def _distance(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _path_length(stroke: Stroke) -> float:
    return sum(_distance(stroke[i - 1], stroke[i]) for i in range(1, len(stroke)))


def _bbox_area(box: Tuple[float, float, float, float]) -> float:
    x0, y0, x1, y1 = box
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _linear_similarity(diff: float, tolerance: float) -> float:
    if tolerance <= 0:
        return 1.0 if abs(diff) < 1e-12 else 0.0
    return _clamp01(1.0 - abs(diff) / tolerance)


def _ratio_similarity(a: float, b: float, tolerance: float = 0.55) -> float:
    """Scale-safe similarity for positive values."""
    if a <= 1e-9 and b <= 1e-9:
        return 1.0
    if a <= 1e-9 or b <= 1e-9:
        return 0.0
    return _linear_similarity(abs(math.log(a / b)), tolerance, )


def _as_strokes(raw_strokes: Sequence[Sequence[Sequence[Any]]], params: EncoderParams) -> List[Stroke]:
    clean = clean_strokes(
        raw_strokes,
        min_points=params.min_stroke_points,
        min_raw_length=params.min_raw_stroke_length,
    )
    if not clean:
        return []
    normalized_all = normalize_strokes(clean, round_digits=params.round_normalized)
    normalized, _dropped = filter_tiny_strokes(normalized_all, params.min_normalized_stroke_length)
    if params.order_mode == "spatial":
        normalized = sort_strokes_spatial(normalized)
    return normalized


def _resample_fixed(stroke: Stroke, n: int = 32) -> Stroke:
    if len(stroke) < 2:
        return list(stroke)
    total = _path_length(stroke)
    if total <= 1e-9:
        return [stroke[0] for _ in range(n)]
    spacing = total / max(1, n - 1)
    pts = resample_stroke(stroke, spacing)
    if len(pts) == n:
        return pts
    # resample_stroke may return off by one due to ceil; interpolate again simply.
    out: Stroke = []
    dists = [0.0]
    for i in range(1, len(stroke)):
        dists.append(dists[-1] + _distance(stroke[i - 1], stroke[i]))
    seg = 1
    for j in range(n):
        td = total * j / max(1, n - 1)
        while seg < len(dists) - 1 and dists[seg] < td:
            seg += 1
        a, b = stroke[seg - 1], stroke[seg]
        pd, nd = dists[seg - 1], dists[seg]
        t = 0.0 if nd <= pd else (td - pd) / (nd - pd)
        out.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
    return out


def _normalize_points_local(points: Stroke) -> Stroke:
    if not points:
        return []
    x0, y0, x1, y1 = stroke_box(points)
    w = max(x1 - x0, 1e-9)
    h = max(y1 - y0, 1e-9)
    scale = max(w, h, 1e-9)
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    return [((x - cx) / scale, (y - cy) / scale) for x, y in points]


def _angle_between(a: Point, b: Point) -> float:
    return math.atan2(b[1] - a[1], b[0] - a[0])


def _angle_delta(a: float, b: float) -> float:
    d = (b - a + math.pi) % (2 * math.pi) - math.pi
    return d


def _turn_angles(points: Stroke) -> List[float]:
    if len(points) < 3:
        return []
    angles = []
    for i in range(1, len(points) - 1):
        a1 = _angle_between(points[i - 1], points[i])
        a2 = _angle_between(points[i], points[i + 1])
        angles.append(abs(_angle_delta(a1, a2)))
    return angles


def _point_line_distance(p: Point, a: Point, b: Point) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx = bx - ax
    dy = by - ay
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        return _distance(p, a)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    proj = (ax + t * dx, ay + t * dy)
    return _distance(p, proj)


def _rdp_count(stroke: Stroke, epsilon: float) -> int:
    if len(stroke) <= 2:
        return len(stroke)
    first, last = stroke[0], stroke[-1]
    max_dist = -1.0
    idx = -1
    for i in range(1, len(stroke) - 1):
        d = _point_line_distance(stroke[i], first, last)
        if d > max_dist:
            max_dist = d
            idx = i
    if max_dist > epsilon and idx != -1:
        return _rdp_count(stroke[: idx + 1], epsilon) + _rdp_count(stroke[idx:], epsilon) - 1
    return 2


def _curvature_features(stroke: Stroke, closed: bool) -> Dict[str, float]:
    pts = _resample_fixed(stroke, n=64)
    turns = _turn_angles(pts)
    path = _path_length(stroke)
    chord = _distance(stroke[0], stroke[-1]) if len(stroke) >= 2 else 0.0
    total_turn = sum(turns)
    mean_turn = total_turn / max(1, len(turns))
    max_turn = max(turns) if turns else 0.0
    corner45 = sum(1 for t in turns if t >= math.radians(45))
    corner25 = sum(1 for t in turns if t >= math.radians(25))
    soft = sum(1 for t in turns if math.radians(8) <= t < math.radians(25))
    straight_ratio = sum(1 for t in turns if t < math.radians(8)) / max(1, len(turns))

    # RDP counts on local-normalized shape preserve curve-vs-straight information.
    local = _normalize_points_local(pts)
    rdp005 = _rdp_count(local, 0.005)
    rdp015 = _rdp_count(local, 0.015)
    rdp030 = _rdp_count(local, 0.030)

    return {
        "path_length": path,
        "chord_length": chord,
        "straightness": chord / max(path, 1e-9),
        "total_turn_norm": total_turn / math.pi,
        "mean_turn": mean_turn,
        "max_turn": max_turn,
        "corner45_count": float(corner45),
        "corner25_count": float(corner25),
        "soft_turn_count": float(soft),
        "straight_turn_ratio": straight_ratio,
        "rdp005_count": float(rdp005),
        "rdp015_count": float(rdp015),
        "rdp030_count": float(rdp030),
        "closed": 1.0 if closed else 0.0,
    }


def _component_signature(stroke: Stroke, idx: int, params: EncoderParams) -> Dict[str, Any]:
    x0, y0, x1, y1 = stroke_box(stroke)
    w = max(x1 - x0, 1e-9)
    h = max(y1 - y0, 1e-9)
    center = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    closed = is_closed(stroke, params.close_threshold)
    fixed = _resample_fixed(stroke, n=32)
    local = _normalize_points_local(fixed)
    return {
        "index": idx,
        "center": [center[0], center[1]],
        "bbox": [x0, y0, x1, y1],
        "width": w,
        "height": h,
        "area": _bbox_area((x0, y0, x1, y1)),
        "aspect": w / max(h, 1e-9),
        "path_length": _path_length(stroke),
        "closed": closed,
        "points_local": [[x, y] for x, y in local],
        "points_global": [[x, y] for x, y in fixed],
        "curvature": _curvature_features(stroke, closed),
    }


def extract_geometry_signature(raw_strokes: Sequence[Sequence[Sequence[Any]]], params_raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Extract a vector-geometry signature from raw drawing strokes.

    This signature is intentionally separate from the token sequence.  It keeps
    visual layout, relative component distances, and curve/straightness features
    that tokenization may simplify away.
    """
    params = params_from_dict(params_raw or {})
    strokes = _as_strokes(raw_strokes, params)
    if not strokes:
        return {"stroke_count": 0, "components": [], "relations": []}

    drawing_box = bounding_box(strokes)
    comps = [_component_signature(s, i, params) for i, s in enumerate(strokes)]
    relations = _relation_signature(comps)
    return {
        "stroke_count": len(comps),
        "drawing_box": list(drawing_box),
        "components": comps,
        "relations": relations,
        "params_hint": {
            "order_mode": params.order_mode,
            "close_threshold": params.close_threshold,
            "round_normalized": params.round_normalized,
        },
    }


def _relation_signature(comps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rels = []
    for i, j in combinations(range(len(comps)), 2):
        a = comps[i]
        b = comps[j]
        ax, ay = a["center"]
        bx, by = b["center"]
        dx = bx - ax
        dy = by - ay
        dist = math.hypot(dx, dy)
        angle = math.atan2(dy, dx)
        overlap = _bbox_overlap_ratio(tuple(a["bbox"]), tuple(b["bbox"]))
        rels.append({
            "i": i,
            "j": j,
            "dx": dx,
            "dy": dy,
            "dist": dist,
            "angle": angle,
            "overlap": overlap,
            "above_below": "below" if dy > 0.08 else "above" if dy < -0.08 else "same_y",
            "left_right": "right" if dx > 0.08 else "left" if dx < -0.08 else "same_x",
        })
    return rels


def _bbox_overlap_ratio(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = ix * iy
    denom = max(min(_bbox_area(a), _bbox_area(b)), 1e-12)
    return inter / denom


def _component_pairs(sig_a: Dict[str, Any], sig_b: Dict[str, Any]) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    a = sig_a.get("components") or []
    b = sig_b.get("components") or []
    # Components have already been spatially/drawn ordered by the profile.
    return list(zip(a, b))


def _count_score(n: int, m: int) -> float:
    if n == 0 and m == 0:
        return 1.0
    if n == 0 or m == 0:
        return 0.0
    return _clamp01(1.0 - abs(n - m) / max(n, m))


def _layout_component_score(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    ax, ay = a["center"]
    bx, by = b["center"]
    center_dist = math.hypot(ax - bx, ay - by)
    center_score = _clamp01(1.0 - center_dist / 0.45)
    width_score = _ratio_similarity(float(a["width"]), float(b["width"]), tolerance=0.65)
    height_score = _ratio_similarity(float(a["height"]), float(b["height"]), tolerance=0.65)
    area_score = _ratio_similarity(float(a["area"]), float(b["area"]), tolerance=1.00)
    closed_score = 1.0 if bool(a.get("closed")) == bool(b.get("closed")) else 0.35
    return _clamp01(0.55 * center_score + 0.15 * width_score + 0.15 * height_score + 0.05 * area_score + 0.10 * closed_score)


def _layout_score(sig_a: Dict[str, Any], sig_b: Dict[str, Any]) -> float:
    pairs = _component_pairs(sig_a, sig_b)
    if not pairs:
        return _count_score(sig_a.get("stroke_count", 0), sig_b.get("stroke_count", 0))
    base = sum(_layout_component_score(a, b) for a, b in pairs) / len(pairs)
    return _clamp01(0.75 * base + 0.25 * _count_score(sig_a.get("stroke_count", 0), sig_b.get("stroke_count", 0)))


def _relation_score(sig_a: Dict[str, Any], sig_b: Dict[str, Any]) -> float:
    n = min(sig_a.get("stroke_count", 0), sig_b.get("stroke_count", 0))
    if n < 2:
        return _count_score(sig_a.get("stroke_count", 0), sig_b.get("stroke_count", 0))

    rel_a = {(r["i"], r["j"]): r for r in sig_a.get("relations") or [] if r["i"] < n and r["j"] < n}
    rel_b = {(r["i"], r["j"]): r for r in sig_b.get("relations") or [] if r["i"] < n and r["j"] < n}
    scores = []
    for key in rel_a.keys() & rel_b.keys():
        a = rel_a[key]
        b = rel_b[key]
        # Angle matters for layout: top-left vs bottom-right should not match.
        angle_diff = abs(_angle_delta(float(a["angle"]), float(b["angle"])))
        angle_score = _clamp01(1.0 - angle_diff / math.pi)
        dist_score = _ratio_similarity(float(a["dist"]), float(b["dist"]), tolerance=0.80)
        dx_score = _linear_similarity(float(a["dx"]) - float(b["dx"]), 0.35)
        dy_score = _linear_similarity(float(a["dy"]) - float(b["dy"]), 0.35)
        overlap_score = _linear_similarity(float(a["overlap"]) - float(b["overlap"]), 0.50)
        coarse_score = 1.0
        if a.get("above_below") != b.get("above_below"):
            coarse_score -= 0.25
        if a.get("left_right") != b.get("left_right"):
            coarse_score -= 0.25
        scores.append(_clamp01(0.25 * angle_score + 0.20 * dist_score + 0.20 * dx_score + 0.20 * dy_score + 0.10 * overlap_score + 0.05 * coarse_score))

    if not scores:
        return 0.0
    return _clamp01((sum(scores) / len(scores)) * _count_score(sig_a.get("stroke_count", 0), sig_b.get("stroke_count", 0)))


def _curvature_component_score(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    ca = a.get("curvature") or {}
    cb = b.get("curvature") or {}
    parts = []
    parts.append(_linear_similarity(float(ca.get("total_turn_norm", 0.0)) - float(cb.get("total_turn_norm", 0.0)), 1.75))
    parts.append(_linear_similarity(float(ca.get("mean_turn", 0.0)) - float(cb.get("mean_turn", 0.0)), 0.22))
    parts.append(_linear_similarity(float(ca.get("corner45_count", 0.0)) - float(cb.get("corner45_count", 0.0)), 4.0))
    parts.append(_linear_similarity(float(ca.get("corner25_count", 0.0)) - float(cb.get("corner25_count", 0.0)), 6.0))
    parts.append(_linear_similarity(float(ca.get("soft_turn_count", 0.0)) - float(cb.get("soft_turn_count", 0.0)), 10.0))
    parts.append(_linear_similarity(float(ca.get("straight_turn_ratio", 0.0)) - float(cb.get("straight_turn_ratio", 0.0)), 0.35))
    parts.append(_linear_similarity(float(ca.get("rdp015_count", 0.0)) - float(cb.get("rdp015_count", 0.0)), 8.0))
    parts.append(_linear_similarity(float(ca.get("rdp030_count", 0.0)) - float(cb.get("rdp030_count", 0.0)), 6.0))
    return _clamp01(sum(parts) / len(parts))


def _curve_score(sig_a: Dict[str, Any], sig_b: Dict[str, Any]) -> float:
    pairs = _component_pairs(sig_a, sig_b)
    if not pairs:
        return _count_score(sig_a.get("stroke_count", 0), sig_b.get("stroke_count", 0))
    base = sum(_curvature_component_score(a, b) for a, b in pairs) / len(pairs)
    return _clamp01(base * _count_score(sig_a.get("stroke_count", 0), sig_b.get("stroke_count", 0)))


def _point_sequence_distance(pa: List[List[float]], pb: List[List[float]], closed: bool) -> float:
    if not pa or not pb:
        return 1e9
    n = min(len(pa), len(pb))
    aa = [(float(x), float(y)) for x, y in pa[:n]]
    bb0 = [(float(x), float(y)) for x, y in pb[:n]]

    def avg_dist(x: List[Point], y: List[Point]) -> float:
        return sum(_distance(p, q) for p, q in zip(x, y)) / max(1, len(x))

    # Important: for OPEN strokes, do NOT compare against the reversed stroke.
    # The direction/start/end of an open symbol is part of the secret. Allowing
    # reversal made symbols such as "1" and "7" much easier to collide.
    candidates = [bb0]
    if closed and n >= 6:
        # Closed strokes can start at slightly different points. Try cyclic shifts
        # and both directions, because a circle-like closed stroke has no natural
        # start/end anchor.
        shifts = range(0, n, max(1, n // 8))
        candidates = []
        for s in shifts:
            shifted = bb0[s:] + bb0[:s]
            candidates.append(shifted)
            candidates.append(list(reversed(shifted)))
    return min(avg_dist(aa, bb) for bb in candidates)


def _endpoint_similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    """Compare start/end anchors for open strokes.

    For open symbols, start/end placement and the overall start→end vector
    matter a lot. This helps separate symbols such as 1 vs 7, L vs V, etc.
    Closed strokes get a neutral score because their start point is arbitrary.
    """
    if bool(a.get("closed")) or bool(b.get("closed")):
        return 1.0 if bool(a.get("closed")) == bool(b.get("closed")) else 0.25

    pa = a.get("points_local") or []
    pb = b.get("points_local") or []
    if len(pa) < 2 or len(pb) < 2:
        return 0.0

    a0, a1 = (float(pa[0][0]), float(pa[0][1])), (float(pa[-1][0]), float(pa[-1][1]))
    b0, b1 = (float(pb[0][0]), float(pb[0][1])), (float(pb[-1][0]), float(pb[-1][1]))

    start_score = _clamp01(1.0 - _distance(a0, b0) / 0.40)
    end_score = _clamp01(1.0 - _distance(a1, b1) / 0.40)

    va = (a1[0] - a0[0], a1[1] - a0[1])
    vb = (b1[0] - b0[0], b1[1] - b0[1])
    lena = math.hypot(*va)
    lenb = math.hypot(*vb)
    len_score = _ratio_similarity(lena, lenb, tolerance=0.60)
    if lena <= 1e-9 or lenb <= 1e-9:
        angle_score = 0.0
    else:
        aa = math.atan2(va[1], va[0])
        ab = math.atan2(vb[1], vb[0])
        angle_score = _clamp01(1.0 - abs(_angle_delta(aa, ab)) / math.pi)

    return _clamp01(0.30 * start_score + 0.30 * end_score + 0.25 * angle_score + 0.15 * len_score)


def _direction_histogram(points: List[List[float]], bins: int = 8) -> List[float]:
    if len(points) < 2:
        return [0.0] * bins
    hist = [0.0] * bins
    total = 0.0
    for i in range(1, len(points)):
        x0, y0 = float(points[i - 1][0]), float(points[i - 1][1])
        x1, y1 = float(points[i][0]), float(points[i][1])
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length <= 1e-9:
            continue
        angle = math.atan2(dy, dx)
        # Map [-pi, pi] into [0, bins).
        idx = int(((angle + math.pi) / (2 * math.pi)) * bins) % bins
        hist[idx] += length
        total += length
    if total <= 1e-9:
        return [0.0] * bins
    return [h / total for h in hist]


def _histogram_similarity(a: List[float], b: List[float]) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    l1 = sum(abs(float(a[i]) - float(b[i])) for i in range(n))
    # L1 range for normalized histograms is [0, 2].
    return _clamp01(1.0 - l1 / 2.0)


def _direction_histogram_score(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    pa = a.get("points_local") or []
    pb = b.get("points_local") or []
    return _histogram_similarity(_direction_histogram(pa), _direction_histogram(pb))


def _dominant_segment_count(comp: Dict[str, Any]) -> int:
    curv = comp.get("curvature") or {}
    # rdp030_count approximates the number of meaningful points after removing
    # wobble. Segment count is points - 1.
    try:
        return max(1, int(round(float(curv.get("rdp030_count", 2.0)))) - 1)
    except Exception:
        return 1


def _segment_count_similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    ca = _dominant_segment_count(a)
    cb = _dominant_segment_count(b)
    return _clamp01(1.0 - abs(ca - cb) / max(ca, cb, 1))


def _stroke_shape_component_score(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    both_closed = bool(a.get("closed")) and bool(b.get("closed"))
    closed_match = bool(a.get("closed")) == bool(b.get("closed"))

    d = _point_sequence_distance(a.get("points_local") or [], b.get("points_local") or [], closed=both_closed)
    local_score = _clamp01(1.0 - d / 0.28)

    # Also compare in global normalized space so rearranged components are punished.
    dg = _point_sequence_distance(a.get("points_global") or [], b.get("points_global") or [], closed=both_closed)
    global_score = _clamp01(1.0 - dg / 0.38)

    endpoint_score = _endpoint_similarity(a, b)
    hist_score = _direction_histogram_score(a, b)
    segment_score = _segment_count_similarity(a, b)
    closed_score = 1.0 if closed_match else 0.0

    if both_closed:
        # Closed loops have arbitrary start points, so endpoints matter less.
        return _clamp01(0.48 * local_score + 0.22 * global_score + 0.18 * hist_score + 0.07 * segment_score + 0.05 * closed_score)

    # Open strokes are where symbols such as 1/7/L/V live. Their start/end
    # anchors, direction histogram, and dominant segment structure are important.
    return _clamp01(
        0.35 * local_score +
        0.20 * global_score +
        0.20 * endpoint_score +
        0.15 * hist_score +
        0.07 * segment_score +
        0.03 * closed_score
    )


def _stroke_shape_score(sig_a: Dict[str, Any], sig_b: Dict[str, Any]) -> float:
    pairs = _component_pairs(sig_a, sig_b)
    if not pairs:
        return _count_score(sig_a.get("stroke_count", 0), sig_b.get("stroke_count", 0))
    base = sum(_stroke_shape_component_score(a, b) for a, b in pairs) / len(pairs)
    return _clamp01(base * _count_score(sig_a.get("stroke_count", 0), sig_b.get("stroke_count", 0)))


def compare_geometry(sig_a: Dict[str, Any], sig_b: Dict[str, Any]) -> Dict[str, Any]:
    count = _count_score(int(sig_a.get("stroke_count", 0)), int(sig_b.get("stroke_count", 0)))
    layout = _layout_score(sig_a, sig_b)
    relation = _relation_score(sig_a, sig_b)
    curve = _curve_score(sig_a, sig_b)
    shape = _stroke_shape_score(sig_a, sig_b)

    comps_a = sig_a.get("components") or []
    comps_b = sig_b.get("components") or []
    single_open = (
        len(comps_a) == 1 and len(comps_b) == 1 and
        not bool(comps_a[0].get("closed")) and not bool(comps_b[0].get("closed"))
    )

    # For multi-component drawings, relation/layout are important. For single
    # open-stroke symbols, relation is uninformative, so shape/curve dominate.
    if single_open:
        final = _clamp01(0.10 * count + 0.15 * layout + 0.20 * curve + 0.55 * shape)
    else:
        final = _clamp01(0.15 * count + 0.25 * layout + 0.20 * relation + 0.15 * curve + 0.25 * shape)

    return {
        "count": count,
        "layout": layout,
        "relation": relation,
        "curve": curve,
        "stroke_shape": shape,
        "single_open_stroke_case": single_open,
        "geometry_final": final,
        "canonical_stroke_count": sig_a.get("stroke_count", 0),
        "redraw_stroke_count": sig_b.get("stroke_count", 0),
    }


def geometry_thresholds(profile: str) -> Dict[str, float]:
    """Prototype gates for geometry-aware verification."""
    if profile == "strict":
        return {"layout": 0.55, "relation": 0.60, "curve": 0.52, "stroke_shape": 0.62, "geometry_final": 0.64}
    if profile == "tolerant":
        return {"layout": 0.62, "relation": 0.65, "curve": 0.55, "stroke_shape": 0.68, "geometry_final": 0.70}
    return {"layout": 0.58, "relation": 0.62, "curve": 0.52, "stroke_shape": 0.65, "geometry_final": 0.67}


def geometry_failure_reasons(scores: Dict[str, Any], profile: str) -> List[str]:
    thresholds = geometry_thresholds(profile)
    reasons = []
    single_open = bool(scores.get("single_open_stroke_case"))
    for key, th in thresholds.items():
        # Relation graph has no information for one open stroke; do not gate on it.
        if single_open and key == "relation":
            continue
        if float(scores.get(key, 0.0)) < th:
            reasons.append(f"{key}_below_{th:.2f}")

    # Extra guard for simple symbols such as 1 vs 7. If both are single open
    # strokes, shape has to be very close, because layout/relation alone cannot
    # distinguish them.
    if single_open:
        min_shape = 0.76 if profile == "tolerant" else 0.72
        if float(scores.get("stroke_shape", 0.0)) < min_shape:
            reasons.append(f"single_open_shape_below_{min_shape:.2f}")

    if float(scores.get("count", 0.0)) < 1.0:
        reasons.append("stroke_count_mismatch")
    return reasons
