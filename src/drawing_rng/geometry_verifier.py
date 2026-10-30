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

def _orientation(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    """Robust enough segment intersection for normalized drawing coordinates."""
    def on_segment(p: Point, q: Point, r: Point) -> bool:
        return (min(p[0], r[0]) - 1e-9 <= q[0] <= max(p[0], r[0]) + 1e-9 and
                min(p[1], r[1]) - 1e-9 <= q[1] <= max(p[1], r[1]) + 1e-9)

    o1 = _orientation(a, b, c)
    o2 = _orientation(a, b, d)
    o3 = _orientation(c, d, a)
    o4 = _orientation(c, d, b)

    if (o1 * o2 < 0) and (o3 * o4 < 0):
        return True
    if abs(o1) < 1e-9 and on_segment(a, c, b):
        return True
    if abs(o2) < 1e-9 and on_segment(a, d, b):
        return True
    if abs(o3) < 1e-9 and on_segment(c, a, d):
        return True
    if abs(o4) < 1e-9 and on_segment(c, b, d):
        return True
    return False


def _segment_distance(a: Point, b: Point, c: Point, d: Point) -> float:
    if _segments_intersect(a, b, c, d):
        return 0.0
    return min(
        _point_line_distance(a, c, d),
        _point_line_distance(b, c, d),
        _point_line_distance(c, a, b),
        _point_line_distance(d, a, b),
    )


def _polyline_min_distance(a: Stroke, b: Stroke) -> float:
    if len(a) < 2 or len(b) < 2:
        return 1.0
    best = 1e9
    # Use fixed resampling to keep the pairwise test cheap and stable.
    aa = _resample_fixed(a, n=24)
    bb = _resample_fixed(b, n=24)
    for i in range(1, len(aa)):
        for j in range(1, len(bb)):
            best = min(best, _segment_distance(aa[i - 1], aa[i], bb[j - 1], bb[j]))
    return float(best if best < 1e9 else 1.0)


def _polyline_intersection_count(a: Stroke, b: Stroke) -> int:
    if len(a) < 2 or len(b) < 2:
        return 0
    aa = _resample_fixed(a, n=24)
    bb = _resample_fixed(b, n=24)
    count = 0
    for i in range(1, len(aa)):
        for j in range(1, len(bb)):
            if _segments_intersect(aa[i - 1], aa[i], bb[j - 1], bb[j]):
                count += 1
    return int(count)


def _endpoint_min_distance(a: Stroke, b: Stroke) -> float:
    if not a or not b:
        return 1.0
    endpoints_a = [a[0], a[-1]]
    endpoints_b = [b[0], b[-1]]
    return min(_distance(x, y) for x in endpoints_a for y in endpoints_b)


def _axis_overlap_and_gap(a0: float, a1: float, b0: float, b1: float) -> Tuple[float, float]:
    span_a = max(a1 - a0, 1e-9)
    span_b = max(b1 - b0, 1e-9)
    overlap = max(0.0, min(a1, b1) - max(a0, b0))
    gap = max(0.0, max(a0, b0) - min(a1, b1))
    return overlap / max(min(span_a, span_b), 1e-9), gap


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



def _polygon_area(points: Stroke) -> float:
    """Signed area magnitude of a polyline treated as a closed contour."""
    if len(points) < 3:
        return 0.0
    acc = 0.0
    for i in range(len(points)):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % len(points)]
        acc += x1 * y2 - x2 * y1
    return abs(acc) / 2.0


def _radial_cv(points: Stroke) -> float:
    """Coefficient of variation of radii around the centroid.

    Round/ellipse-like closed contours tend to have smoother radius fields;
    boxy polygons and angular redraws have more radial fluctuation.
    """
    if len(points) < 3:
        return 1.0
    cx = sum(x for x, _ in points) / len(points)
    cy = sum(y for _, y in points) / len(points)
    radii = [math.hypot(x - cx, y - cy) for x, y in points]
    mean_r = sum(radii) / max(1, len(radii))
    if mean_r <= 1e-9:
        return 1.0
    var = sum((r - mean_r) ** 2 for r in radii) / max(1, len(radii))
    return math.sqrt(var) / mean_r


def _closed_roundness(circularity: float, radial_cv: float, rdp030: float) -> float:
    """Soft estimate of how circle/ellipse-like a closed stroke is.

    This is not a classifier; it is a safety feature for the verifier.  It
    helps prevent a mostly circular enrollment from unlocking with a boxy
    polygon that has similar direction tokens.
    """
    circ_part = _clamp01((float(circularity) - 0.62) / 0.33)
    radial_part = _clamp01(1.0 - float(radial_cv) / 0.28)
    # More RDP points at a coarse epsilon usually means the contour is not just
    # a tiny triangle/box made of a few straight segments.  Keep this light so
    # wobbly hand-drawn circles are not over-penalized.
    complexity_part = _clamp01((float(rdp030) - 3.0) / 7.0)
    return _clamp01(0.50 * circ_part + 0.35 * radial_part + 0.15 * complexity_part)


def _closure_confidence(stroke: Stroke, threshold: float) -> float:
    """Return a soft closure estimate around the binary close threshold."""
    if len(stroke) < 3:
        return 0.0
    gap = _distance(stroke[0], stroke[-1])
    full_at = max(threshold * 0.50, 1e-9)
    zero_at = max(threshold * 2.50, full_at + 1e-9)
    return _clamp01((zero_at - gap) / (zero_at - full_at))


def _curvature_features(stroke: Stroke, closed: bool, closure_confidence: float) -> Dict[str, float]:
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
    rdp010 = _rdp_count(local, 0.010)
    rdp015 = _rdp_count(local, 0.015)
    rdp030 = _rdp_count(local, 0.030)
    # Higher values mean a more angular / low-poly stroke.  This matters for
    # cases where a smooth arch is redrawn as a few straight polygonal segments.
    hard_corner_ratio = corner45 / max(1, len(turns))
    medium_corner_ratio = corner25 / max(1, len(turns))
    turn_energy = sum(t * t for t in turns) / max(1, len(turns))

    # Closed-contour descriptors.  These are specifically meant to separate
    # round loops from boxy polygons.  Direction-token sequences alone often
    # collapse both into "closed + several long runs + turns".
    # Near-closed hand drawings still have useful contour descriptors. Their
    # influence is blended by closure_confidence during comparison.
    local_area = _polygon_area(local)
    local_path = _path_length(local)
    circularity = 4.0 * math.pi * local_area / max(local_path * local_path, 1e-9)
    circularity = _clamp01(circularity)
    radial_cv = _radial_cv(local)
    roundness = _closed_roundness(circularity, radial_cv, float(rdp030))

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
        "rdp010_count": float(rdp010),
        "rdp015_count": float(rdp015),
        "rdp030_count": float(rdp030),
        "hard_corner_ratio": float(hard_corner_ratio),
        "medium_corner_ratio": float(medium_corner_ratio),
        "turn_energy": float(turn_energy),
        "closed_contour_area": float(local_area),
        "closed_contour_circularity": float(circularity),
        "closed_radial_cv": float(radial_cv),
        "closed_roundness": float(roundness),
        "closed": 1.0 if closed else 0.0,
        "closure_confidence": float(closure_confidence),
    }


def _component_signature(stroke: Stroke, idx: int, params: EncoderParams) -> Dict[str, Any]:
    x0, y0, x1, y1 = stroke_box(stroke)
    w = max(x1 - x0, 1e-9)
    h = max(y1 - y0, 1e-9)
    center = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    closed = is_closed(stroke, params.close_threshold)
    closure_confidence = _closure_confidence(stroke, params.close_threshold)
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
        "closure_confidence": closure_confidence,
        "points_local": [[x, y] for x, y in local],
        "points_global": [[x, y] for x, y in fixed],
        # Keep normalized original stroke for pairwise topology checks.
        # This is not secret material; it is used to compare overlap/crossing/gap behavior.
        "points_norm": [[x, y] for x, y in stroke],
        "curvature": _curvature_features(stroke, closed, closure_confidence),
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

        ax0, ay0, ax1, ay1 = a["bbox"]
        bx0, by0, bx1, by1 = b["bbox"]
        x_overlap, x_gap = _axis_overlap_and_gap(ax0, ax1, bx0, bx1)
        y_overlap, y_gap = _axis_overlap_and_gap(ay0, ay1, by0, by1)

        stroke_a = [(float(x), float(y)) for x, y in (a.get("points_norm") or [])]
        stroke_b = [(float(x), float(y)) for x, y in (b.get("points_norm") or [])]
        min_dist = _polyline_min_distance(stroke_a, stroke_b)
        endpoint_gap = _endpoint_min_distance(stroke_a, stroke_b)
        crossing_count = _polyline_intersection_count(stroke_a, stroke_b)

        rels.append({
            "i": i,
            "j": j,
            "dx": dx,
            "dy": dy,
            "dist": dist,
            "angle": angle,
            "overlap": overlap,
            "x_overlap": x_overlap,
            "y_overlap": y_overlap,
            "x_gap": x_gap,
            "y_gap": y_gap,
            "min_distance": min_dist,
            "endpoint_min_distance": endpoint_gap,
            "crossing_count": float(crossing_count),
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
        overlap_score = _linear_similarity(float(a["overlap"]) - float(b["overlap"]), 0.42)
        x_overlap_score = _linear_similarity(float(a.get("x_overlap", 0.0)) - float(b.get("x_overlap", 0.0)), 0.35)
        y_overlap_score = _linear_similarity(float(a.get("y_overlap", 0.0)) - float(b.get("y_overlap", 0.0)), 0.35)
        gap_score = _linear_similarity(float(a.get("min_distance", 0.0)) - float(b.get("min_distance", 0.0)), 0.12)
        endpoint_gap_score = _linear_similarity(float(a.get("endpoint_min_distance", 0.0)) - float(b.get("endpoint_min_distance", 0.0)), 0.14)
        crossing_score = _linear_similarity(float(a.get("crossing_count", 0.0)) - float(b.get("crossing_count", 0.0)), 0.75)
        coarse_score = 1.0
        if a.get("above_below") != b.get("above_below"):
            coarse_score -= 0.25
        if a.get("left_right") != b.get("left_right"):
            coarse_score -= 0.25
        scores.append(_clamp01(
            0.16 * angle_score +
            0.14 * dist_score +
            0.14 * dx_score +
            0.14 * dy_score +
            0.08 * overlap_score +
            0.07 * x_overlap_score +
            0.07 * y_overlap_score +
            0.10 * gap_score +
            0.08 * endpoint_gap_score +
            0.05 * crossing_score +
            0.03 * coarse_score
        ))

    if not scores:
        return 0.0
    return _clamp01((sum(scores) / len(scores)) * _count_score(sig_a.get("stroke_count", 0), sig_b.get("stroke_count", 0)))


def _topology_score(sig_a: Dict[str, Any], sig_b: Dict[str, Any]) -> float:
    """Compare pairwise component topology: gap, touching, crossing, overlap.

    This is designed for exactly the failure class you found: an enrollment with
    two separate open arches should not unlock with overlapping arches, collapsed
    center gaps, or crossings.
    """
    n = min(sig_a.get("stroke_count", 0), sig_b.get("stroke_count", 0))
    if n < 2:
        return 1.0
    rel_a = {(r["i"], r["j"]): r for r in sig_a.get("relations") or [] if r["i"] < n and r["j"] < n}
    rel_b = {(r["i"], r["j"]): r for r in sig_b.get("relations") or [] if r["i"] < n and r["j"] < n}
    scores = []
    for key in rel_a.keys() & rel_b.keys():
        a = rel_a[key]
        b = rel_b[key]
        # Topology measurements are sensitive to hand jitter near touching and
        # overlap boundaries. Use wider continuous tolerances so a small shift
        # lowers confidence instead of collapsing a component to zero.
        min_dist_score = _linear_similarity(float(a.get("min_distance", 0.0)) - float(b.get("min_distance", 0.0)), 0.16)
        endpoint_score = _linear_similarity(float(a.get("endpoint_min_distance", 0.0)) - float(b.get("endpoint_min_distance", 0.0)), 0.18)
        cross_score = _linear_similarity(float(a.get("crossing_count", 0.0)) - float(b.get("crossing_count", 0.0)), 1.25)
        x_overlap_score = _linear_similarity(float(a.get("x_overlap", 0.0)) - float(b.get("x_overlap", 0.0)), 0.42)
        y_overlap_score = _linear_similarity(float(a.get("y_overlap", 0.0)) - float(b.get("y_overlap", 0.0)), 0.42)
        bbox_overlap_score = _linear_similarity(float(a.get("overlap", 0.0)) - float(b.get("overlap", 0.0)), 0.52)
        scores.append(_clamp01(
            0.24 * min_dist_score +
            0.18 * endpoint_score +
            0.22 * cross_score +
            0.14 * x_overlap_score +
            0.10 * y_overlap_score +
            0.12 * bbox_overlap_score
        ))
    if not scores:
        return 0.0
    return _clamp01((sum(scores) / len(scores)) * _count_score(sig_a.get("stroke_count", 0), sig_b.get("stroke_count", 0)))


def _topology_flags(sig_a: Dict[str, Any], sig_b: Dict[str, Any]) -> List[str]:
    flags: List[str] = []
    n = min(sig_a.get("stroke_count", 0), sig_b.get("stroke_count", 0))
    if n < 2:
        return flags
    rel_a = {(r["i"], r["j"]): r for r in sig_a.get("relations") or [] if r["i"] < n and r["j"] < n}
    rel_b = {(r["i"], r["j"]): r for r in sig_b.get("relations") or [] if r["i"] < n and r["j"] < n}
    for key in rel_a.keys() & rel_b.keys():
        a = rel_a[key]
        b = rel_b[key]
        # Enrollment had a visible gap, redraw collapsed/touched it.
        if float(a.get("min_distance", 0.0)) > 0.11 and float(b.get("min_distance", 0.0)) < 0.018:
            flags.append("component_gap_collapsed")
        # Enrollment did not cross/touch, redraw crosses/touches.
        if float(a.get("crossing_count", 0.0)) < 0.5 and float(b.get("crossing_count", 0.0)) >= 1.0:
            flags.append("component_crossing_changed")
        # Open endpoint relationship changed from a visible separation to touching.
        if float(a.get("endpoint_min_distance", 0.0)) > 0.10 and float(b.get("endpoint_min_distance", 0.0)) < 0.015:
            flags.append("endpoint_gap_collapsed")
        # BBoxes were largely separate but redraw overlaps heavily.
        if float(a.get("overlap", 0.0)) < 0.18 and float(b.get("overlap", 0.0)) > 0.68:
            flags.append("component_overlap_changed")
        # X interval overlap changed a lot. This catches two separated arches
        # redrawn as overlapping/crossed arches.
        if float(a.get("x_overlap", 0.0)) < 0.25 and float(b.get("x_overlap", 0.0)) > 0.82:
            flags.append("x_overlap_changed")
    return sorted(set(flags))


def _curvature_component_score(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    ca = a.get("curvature") or {}
    cb = b.get("curvature") or {}
    parts = []
    parts.append(_linear_similarity(float(ca.get("total_turn_norm", 0.0)) - float(cb.get("total_turn_norm", 0.0)), 1.25))
    parts.append(_linear_similarity(float(ca.get("mean_turn", 0.0)) - float(cb.get("mean_turn", 0.0)), 0.16))
    parts.append(_linear_similarity(float(ca.get("max_turn", 0.0)) - float(cb.get("max_turn", 0.0)), 0.32))
    parts.append(_linear_similarity(float(ca.get("corner45_count", 0.0)) - float(cb.get("corner45_count", 0.0)), 2.5))
    parts.append(_linear_similarity(float(ca.get("corner25_count", 0.0)) - float(cb.get("corner25_count", 0.0)), 4.0))
    parts.append(_linear_similarity(float(ca.get("soft_turn_count", 0.0)) - float(cb.get("soft_turn_count", 0.0)), 7.0))
    parts.append(_linear_similarity(float(ca.get("straight_turn_ratio", 0.0)) - float(cb.get("straight_turn_ratio", 0.0)), 0.25))
    parts.append(_linear_similarity(float(ca.get("rdp005_count", 0.0)) - float(cb.get("rdp005_count", 0.0)), 9.0))
    parts.append(_linear_similarity(float(ca.get("rdp010_count", 0.0)) - float(cb.get("rdp010_count", 0.0)), 6.0))
    parts.append(_linear_similarity(float(ca.get("rdp015_count", 0.0)) - float(cb.get("rdp015_count", 0.0)), 4.0))
    parts.append(_linear_similarity(float(ca.get("rdp030_count", 0.0)) - float(cb.get("rdp030_count", 0.0)), 3.0))
    parts.append(_linear_similarity(float(ca.get("hard_corner_ratio", 0.0)) - float(cb.get("hard_corner_ratio", 0.0)), 0.10))
    parts.append(_linear_similarity(float(ca.get("medium_corner_ratio", 0.0)) - float(cb.get("medium_corner_ratio", 0.0)), 0.16))
    parts.append(_linear_similarity(float(ca.get("turn_energy", 0.0)) - float(cb.get("turn_energy", 0.0)), 0.08))

    # Extra closed-contour curvature style.  This catches circle/ellipse vs
    # boxy-polygon false accepts that token edit distance misses.
    if float(ca.get("closed", 0.0)) > 0.5 and float(cb.get("closed", 0.0)) > 0.5:
        parts.append(_linear_similarity(float(ca.get("closed_contour_circularity", 0.0)) - float(cb.get("closed_contour_circularity", 0.0)), 0.18))
        parts.append(_linear_similarity(float(ca.get("closed_radial_cv", 1.0)) - float(cb.get("closed_radial_cv", 1.0)), 0.12))
        parts.append(_linear_similarity(float(ca.get("closed_roundness", 0.0)) - float(cb.get("closed_roundness", 0.0)), 0.28))
    return _clamp01(sum(parts) / len(parts))



def _closed_style_component_score(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    """Compare circle/ellipse-like vs polygon-like style for closed strokes."""
    ca = a.get("curvature") or {}
    cb = b.get("curvature") or {}
    closure_a = float(a.get("closure_confidence", ca.get("closure_confidence", 1.0 if a.get("closed") else 0.0)))
    closure_b = float(b.get("closure_confidence", cb.get("closure_confidence", 1.0 if b.get("closed") else 0.0)))
    closure_score = _linear_similarity(closure_a - closure_b, 0.85)
    shared_closure = min(closure_a, closure_b)
    if max(closure_a, closure_b) < 0.15:
        return 1.0
    circularity_score = _linear_similarity(
        float(ca.get("closed_contour_circularity", 0.0)) - float(cb.get("closed_contour_circularity", 0.0)),
        0.16,
    )
    radial_score = _linear_similarity(
        float(ca.get("closed_radial_cv", 1.0)) - float(cb.get("closed_radial_cv", 1.0)),
        0.10,
    )
    roundness_score = _linear_similarity(
        float(ca.get("closed_roundness", 0.0)) - float(cb.get("closed_roundness", 0.0)),
        0.22,
    )
    rdp_score = _linear_similarity(
        float(ca.get("rdp030_count", 0.0)) - float(cb.get("rdp030_count", 0.0)),
        4.0,
    )
    contour_score = _clamp01(
        0.35 * circularity_score +
        0.25 * radial_score +
        0.30 * roundness_score +
        0.10 * rdp_score
    )
    contour_weight = 0.25 + 0.55 * shared_closure
    return _clamp01((1.0 - contour_weight) * closure_score + contour_weight * contour_score)


def _closed_style_score(sig_a: Dict[str, Any], sig_b: Dict[str, Any]) -> float:
    pairs = _component_pairs(sig_a, sig_b)
    closed_pairs = [
        (a, b) for a, b in pairs
        if max(
            float(a.get("closure_confidence", 1.0 if a.get("closed") else 0.0)),
            float(b.get("closure_confidence", 1.0 if b.get("closed") else 0.0)),
        ) >= 0.15
    ]
    if not closed_pairs:
        return 1.0
    base = sum(_closed_style_component_score(a, b) for a, b in closed_pairs) / len(closed_pairs)
    return _clamp01(base * _count_score(sig_a.get("stroke_count", 0), sig_b.get("stroke_count", 0)))

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
        # Add closed-contour style so a circle does not unlock with a square-like
        # polygon just because both are CLOSED with similar direction buckets.
        style_score = _closed_style_component_score(a, b)
        return _clamp01(0.38 * local_score + 0.18 * global_score + 0.14 * hist_score + 0.05 * segment_score + 0.05 * closed_score + 0.20 * style_score)

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
    topology = _topology_score(sig_a, sig_b)
    topology_flags = _topology_flags(sig_a, sig_b)
    curve = _curve_score(sig_a, sig_b)
    shape = _stroke_shape_score(sig_a, sig_b)
    closed_style = _closed_style_score(sig_a, sig_b)

    comps_a = sig_a.get("components") or []
    comps_b = sig_b.get("components") or []
    single_open = (
        len(comps_a) == 1 and len(comps_b) == 1 and
        not bool(comps_a[0].get("closed")) and not bool(comps_b[0].get("closed"))
    )

    # For multi-component drawings, relation/layout are important. For single
    # open-stroke symbols, relation is uninformative, so shape/curve dominate.
    # Treat one-stroke comparisons as a closed-style case whenever either side
    # is meaningfully loop-like. This lets near-closed redraws receive a graded
    # style score while still strongly comparing a loop against an open arc.
    single_closed = (
        len(comps_a) == 1 and len(comps_b) == 1 and
        max(
            float(comps_a[0].get("closure_confidence", 1.0 if comps_a[0].get("closed") else 0.0)),
            float(comps_b[0].get("closure_confidence", 1.0 if comps_b[0].get("closed") else 0.0)),
        ) >= 0.15
    )

    two_open = (
        len(comps_a) == 2 and len(comps_b) == 2 and
        not any(bool(c.get("closed")) for c in comps_a + comps_b)
    )

    closed_style_applicable = any(
        float(component.get("closure_confidence", 1.0 if component.get("closed") else 0.0)) >= 0.15
        for component in comps_a + comps_b
    )

    if single_open:
        final = _clamp01(0.10 * count + 0.15 * layout + 0.20 * curve + 0.55 * shape)
    elif two_open:
        # Two-stroke open symbols depend heavily on the relation between strokes:
        # gap, crossing, overlap, and left/right layout are part of the secret.
        final = _clamp01(0.10 * count + 0.18 * layout + 0.20 * relation + 0.22 * topology + 0.15 * curve + 0.15 * shape)
    elif single_closed:
        # For one closed contour, relation is uninformative; closed-style and
        # curve/shape must do most of the work.
        final = _clamp01(0.10 * count + 0.12 * layout + 0.25 * curve + 0.28 * shape + 0.25 * closed_style)
    else:
        final = _clamp01(0.12 * count + 0.20 * layout + 0.16 * relation + 0.16 * topology + 0.13 * curve + 0.18 * shape + 0.05 * closed_style)

    return {
        "count": count,
        "layout": layout,
        "relation": relation,
        "topology": topology,
        "topology_flags": topology_flags,
        "topology_flags_are_diagnostic": True,
        "curve": curve,
        "stroke_shape": shape,
        "closed_style": closed_style,
        "closed_style_applicable": closed_style_applicable,
        "single_open_stroke_case": single_open,
        "single_closed_stroke_case": single_closed,
        "two_open_stroke_case": two_open,
        "geometry_final": final,
        "canonical_stroke_count": sig_a.get("stroke_count", 0),
        "redraw_stroke_count": sig_b.get("stroke_count", 0),
    }


def geometry_thresholds(profile: str) -> Dict[str, float]:
    """Prototype gates for geometry-aware verification."""
    if profile == "strict":
        return {"layout": 0.55, "relation": 0.60, "topology": 0.60, "curve": 0.55, "stroke_shape": 0.62, "closed_style": 0.58, "geometry_final": 0.64}
    if profile == "tolerant":
        return {"layout": 0.62, "relation": 0.65, "topology": 0.65, "curve": 0.58, "stroke_shape": 0.68, "closed_style": 0.62, "geometry_final": 0.70}
    return {"layout": 0.58, "relation": 0.62, "topology": 0.62, "curve": 0.55, "stroke_shape": 0.65, "closed_style": 0.60, "geometry_final": 0.67}


def geometry_failure_reasons(scores: Dict[str, Any], profile: str) -> List[str]:
    thresholds = geometry_thresholds(profile)
    reasons = []
    single_open = bool(scores.get("single_open_stroke_case"))
    single_closed = bool(scores.get("single_closed_stroke_case"))
    two_open = bool(scores.get("two_open_stroke_case"))
    for key, th in thresholds.items():
        # Relation/topology graph has no information for one open/closed stroke; do not gate on it.
        if (single_open or single_closed) and key in {"relation", "topology"}:
            continue
        # Two-open drawings use the specialized gates below. Applying both the
        # generic and specialized gates made the same topology miss count twice.
        if two_open and key in {"relation", "topology"}:
            continue
        if float(scores.get(key, 0.0)) < th:
            reasons.append(f"{key}_below_{th:.2f}")

    if two_open:
        min_topology = 0.66 if profile == "tolerant" else 0.62
        if float(scores.get("topology", 0.0)) < min_topology:
            reasons.append(f"two_open_topology_below_{min_topology:.2f}")
        min_relation = 0.62 if profile == "tolerant" else 0.58
        if float(scores.get("relation", 0.0)) < min_relation:
            reasons.append(f"two_open_relation_below_{min_relation:.2f}")

    # Extra guard for closed one-stroke contours.  If someone enrolls a rounded
    # circle/loop and later draws a boxy polygon, token score can still be high;
    # closed_style is designed to catch that.
    if single_closed:
        min_style = 0.66 if profile == "tolerant" else 0.62
        if float(scores.get("closed_style", 0.0)) < min_style:
            reasons.append(f"single_closed_style_below_{min_style:.2f}")

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
