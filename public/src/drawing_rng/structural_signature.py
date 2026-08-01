from __future__ import annotations

import math
from collections import Counter
from itertools import combinations
from statistics import median
from typing import Any, Dict, Iterable, List, Sequence, Tuple

Point = Tuple[float, float]
Stroke = List[Point]


def _point(value: Any) -> Point | None:
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return None


def _clean_strokes(raw_strokes: Any) -> List[Stroke]:
    out: List[Stroke] = []
    if not isinstance(raw_strokes, list):
        return out
    for raw_stroke in raw_strokes:
        if not isinstance(raw_stroke, list):
            continue
        stroke = [p for p in (_point(value) for value in raw_stroke) if p is not None]
        if len(stroke) >= 2:
            out.append(stroke)
    return out


def _distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _path_length(stroke: Sequence[Point]) -> float:
    return sum(_distance(stroke[index - 1], stroke[index]) for index in range(1, len(stroke)))


def _normalise(strokes: List[Stroke]) -> List[Stroke]:
    points = [point for stroke in strokes for point in stroke]
    if not points:
        return []
    min_x = min(point[0] for point in points)
    max_x = max(point[0] for point in points)
    min_y = min(point[1] for point in points)
    max_y = max(point[1] for point in points)
    scale = max(max_x - min_x, max_y - min_y, 1e-9)
    center_x = (min_x + max_x) / 2.0
    center_y = (min_y + max_y) / 2.0
    return [
        [((point[0] - center_x) / scale, (point[1] - center_y) / scale) for point in stroke]
        for stroke in strokes
    ]


def _bbox(stroke: Sequence[Point]) -> Tuple[float, float, float, float]:
    xs = [point[0] for point in stroke]
    ys = [point[1] for point in stroke]
    return min(xs), min(ys), max(xs), max(ys)


def _centroid(stroke: Sequence[Point]) -> Point:
    return (
        sum(point[0] for point in stroke) / max(len(stroke), 1),
        sum(point[1] for point in stroke) / max(len(stroke), 1),
    )


def _overlap_1d(a0: float, a1: float, b0: float, b1: float) -> float:
    intersection = max(0.0, min(a1, b1) - max(a0, b0))
    denominator = max(min(a1 - a0, b1 - b0), 1e-9)
    return max(0.0, min(1.0, intersection / denominator))


def _orientation(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: Point, b: Point, p: Point, eps: float = 1e-7) -> bool:
    return (
        min(a[0], b[0]) - eps <= p[0] <= max(a[0], b[0]) + eps
        and min(a[1], b[1]) - eps <= p[1] <= max(a[1], b[1]) + eps
        and abs(_orientation(a, b, p)) <= eps
    )


def _segments_intersect(a: Point, b: Point, c: Point, d: Point, eps: float = 1e-7) -> bool:
    o1 = _orientation(a, b, c)
    o2 = _orientation(a, b, d)
    o3 = _orientation(c, d, a)
    o4 = _orientation(c, d, b)
    if ((o1 > eps and o2 < -eps) or (o1 < -eps and o2 > eps)) and (
        (o3 > eps and o4 < -eps) or (o3 < -eps and o4 > eps)
    ):
        return True
    return (
        _on_segment(a, b, c, eps)
        or _on_segment(a, b, d, eps)
        or _on_segment(c, d, a, eps)
        or _on_segment(c, d, b, eps)
    )


def _segment_distance(a: Point, b: Point, p: Point) -> float:
    vx, vy = b[0] - a[0], b[1] - a[1]
    denominator = vx * vx + vy * vy
    if denominator <= 1e-12:
        return _distance(a, p)
    t = ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / denominator
    t = max(0.0, min(1.0, t))
    projection = (a[0] + t * vx, a[1] + t * vy)
    return _distance(projection, p)


def _polyline_gap(a: Sequence[Point], b: Sequence[Point]) -> float:
    best = float("inf")
    for index in range(1, len(a)):
        a0, a1 = a[index - 1], a[index]
        for point in b:
            best = min(best, _segment_distance(a0, a1, point))
    for index in range(1, len(b)):
        b0, b1 = b[index - 1], b[index]
        for point in a:
            best = min(best, _segment_distance(b0, b1, point))
    return 1.0 if best == float("inf") else max(0.0, min(1.0, best))


def _intersection_count(a: Sequence[Point], b: Sequence[Point], cap: int = 8) -> int:
    count = 0
    for index_a in range(1, len(a)):
        for index_b in range(1, len(b)):
            if _segments_intersect(a[index_a - 1], a[index_a], b[index_b - 1], b[index_b]):
                count += 1
                if count >= cap:
                    return cap
    return count


def _point_in_polygon(point: Point, polygon: Sequence[Point]) -> bool:
    if len(polygon) < 3:
        return False
    inside = False
    x, y = point
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)):
            crossing_x = (xj - xi) * (y - yi) / max(yj - yi, 1e-12) + xi
            if x < crossing_x:
                inside = not inside
        j = i
    return inside


def _is_closed(stroke: Sequence[Point]) -> bool:
    length = _path_length(stroke)
    if length <= 1e-9:
        return False
    return _distance(stroke[0], stroke[-1]) <= max(0.055, min(0.16, 0.12 * length))


def _relation(a: Sequence[Point], b: Sequence[Point], closed_a: bool, closed_b: bool) -> Dict[str, Any]:
    intersections = _intersection_count(a, b)
    gap = _polyline_gap(a, b)
    bbox_a = _bbox(a)
    bbox_b = _bbox(b)
    x_overlap = _overlap_1d(bbox_a[0], bbox_a[2], bbox_b[0], bbox_b[2])
    y_overlap = _overlap_1d(bbox_a[1], bbox_a[3], bbox_b[1], bbox_b[3])
    contains = None
    if closed_a and _point_in_polygon(_centroid(b), a):
        contains = "a_contains_b"
    elif closed_b and _point_in_polygon(_centroid(a), b):
        contains = "b_contains_a"
    if intersections > 0:
        label = "intersects"
    elif contains:
        label = contains
    elif gap <= 0.065:
        label = "near"
    elif x_overlap >= 0.22 and y_overlap >= 0.22:
        label = "overlap_without_crossing"
    else:
        label = "separated"
    return {
        "label": label,
        "intersections": intersections,
        "gap": round(gap, 6),
        "x_overlap": round(x_overlap, 6),
        "y_overlap": round(y_overlap, 6),
    }


def extract_structural_signature(raw_strokes: Any) -> Dict[str, Any]:
    strokes = _normalise(_clean_strokes(raw_strokes))
    closed = [_is_closed(stroke) for stroke in strokes]
    relations: List[Dict[str, Any]] = []
    for first, second in combinations(range(len(strokes)), 2):
        relation = _relation(strokes[first], strokes[second], closed[first], closed[second])
        relation.update({"a": first, "b": second})
        relations.append(relation)
    return {
        "version": "structural-signature-v1",
        "stroke_count": len(strokes),
        "closed_pattern": closed,
        "closed_count": sum(1 for value in closed if value),
        "total_intersections": sum(int(item["intersections"]) for item in relations),
        "relations": relations,
    }


def _relation_map(signature: Dict[str, Any]) -> Dict[Tuple[int, int], Dict[str, Any]]:
    out: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for item in signature.get("relations") or []:
        try:
            key = int(item["a"]), int(item["b"])
        except (TypeError, ValueError, KeyError):
            continue
        out[key] = item
    return out


def _relation_similarity(label_a: str, label_b: str) -> float:
    if label_a == label_b:
        return 1.0
    near_family = {"near", "separated", "overlap_without_crossing"}
    if label_a in near_family and label_b in near_family:
        if {label_a, label_b} == {"near", "separated"}:
            return 0.72
        return 0.58
    return 0.0


def compare_structural_signatures(reference: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    failures: List[str] = []
    reference_count = int(reference.get("stroke_count") or 0)
    candidate_count = int(candidate.get("stroke_count") or 0)
    count_score = 1.0 if reference_count == candidate_count else max(
        0.0,
        1.0 - abs(reference_count - candidate_count) / max(reference_count, candidate_count, 1),
    )
    if reference_count != candidate_count:
        failures.append("structural_stroke_count_changed")

    reference_closed = list(reference.get("closed_pattern") or [])
    candidate_closed = list(candidate.get("closed_pattern") or [])
    closed_score = 1.0
    if reference_closed or candidate_closed:
        length = max(len(reference_closed), len(candidate_closed), 1)
        matches = sum(
            1
            for index in range(min(len(reference_closed), len(candidate_closed)))
            if bool(reference_closed[index]) == bool(candidate_closed[index])
        )
        closed_score = matches / length
    if reference_closed != candidate_closed:
        failures.append("structural_closed_contour_pattern_changed")

    reference_relations = _relation_map(reference)
    candidate_relations = _relation_map(candidate)
    relation_scores: List[float] = []
    hard_relation_failures: List[str] = []
    for key in sorted(set(reference_relations) | set(candidate_relations)):
        a = reference_relations.get(key, {})
        b = candidate_relations.get(key, {})
        label_a = str(a.get("label") or "missing")
        label_b = str(b.get("label") or "missing")
        relation_scores.append(_relation_similarity(label_a, label_b))
        crossing_a = int(a.get("intersections") or 0) > 0
        crossing_b = int(b.get("intersections") or 0) > 0
        containment_labels = {"a_contains_b", "b_contains_a"}
        if crossing_a != crossing_b:
            hard_relation_failures.append(f"structural_crossing_changed_{key[0]}_{key[1]}")
        if (label_a in containment_labels) != (label_b in containment_labels) or (
            label_a in containment_labels and label_b in containment_labels and label_a != label_b
        ):
            hard_relation_failures.append(f"structural_containment_changed_{key[0]}_{key[1]}")
    relation_score = sum(relation_scores) / len(relation_scores) if relation_scores else 1.0
    failures.extend(hard_relation_failures)

    reference_intersections = int(reference.get("total_intersections") or 0)
    candidate_intersections = int(candidate.get("total_intersections") or 0)
    intersection_score = 1.0 - min(
        1.0,
        abs(reference_intersections - candidate_intersections) / max(reference_intersections, candidate_intersections, 1),
    )

    score = max(
        0.0,
        min(
            1.0,
            0.28 * count_score
            + 0.22 * closed_score
            + 0.38 * relation_score
            + 0.12 * intersection_score,
        ),
    )
    hard_pass = not failures
    return {
        "score": score,
        "hard_pass": hard_pass,
        "failure_reasons": failures,
        "count_score": count_score,
        "closed_score": closed_score,
        "relation_score": relation_score,
        "intersection_score": intersection_score,
    }


def learn_structural_model(raw_attempts: Iterable[Any]) -> Dict[str, Any]:
    signatures = []
    for attempt in raw_attempts:
        strokes = attempt.get("strokes") if isinstance(attempt, dict) else attempt
        signatures.append(extract_structural_signature(strokes))
    comparisons = [
        compare_structural_signatures(a, b)
        for a, b in combinations(signatures, 2)
    ]
    scores = [float(item.get("score", 0.0)) for item in comparisons]
    hard_passes = [bool(item.get("hard_pass")) for item in comparisons]
    stable = bool(signatures) and all(hard_passes) and (median(scores) if scores else 1.0) >= 0.82
    stroke_counts = [int(item.get("stroke_count") or 0) for item in signatures]
    return {
        "version": "structural-consensus-v1",
        "stable": stable,
        "median_pair_score": median(scores) if scores else 1.0,
        "pair_scores": scores,
        "reference_signatures": signatures,
        "consensus_stroke_count": Counter(stroke_counts).most_common(1)[0][0] if stroke_counts else 0,
    }
