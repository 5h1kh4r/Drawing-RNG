from __future__ import annotations

import math
from itertools import combinations
from typing import Any, Dict, List, Sequence, Tuple

Point = Tuple[float, float]
N = 64


def _point(value: Any) -> Point | None:
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return None


def _clean_strokes(raw_strokes: Any) -> List[List[Point]]:
    out: List[List[Point]] = []
    if not isinstance(raw_strokes, list):
        return out
    for raw_stroke in raw_strokes:
        if not isinstance(raw_stroke, list):
            continue
        stroke = [p for p in (_point(value) for value in raw_stroke) if p is not None]
        if stroke:
            out.append(stroke)
    return out


def _path_length(points: Sequence[Point]) -> float:
    return sum(
        math.hypot(points[index][0] - points[index - 1][0], points[index][1] - points[index - 1][1])
        for index in range(1, len(points))
    )


def _resample(points: List[Point], count: int = N) -> List[Point]:
    if len(points) < 2:
        return []
    interval = _path_length(points) / max(count - 1, 1)
    if interval <= 1e-12:
        return [points[0]] * count
    source = list(points)
    out = [source[0]]
    distance_accumulator = 0.0
    index = 1
    while index < len(source) and len(out) < count:
        previous = source[index - 1]
        current = source[index]
        segment = math.hypot(current[0] - previous[0], current[1] - previous[1])
        if distance_accumulator + segment >= interval and segment > 0:
            ratio = (interval - distance_accumulator) / segment
            point = (
                previous[0] + ratio * (current[0] - previous[0]),
                previous[1] + ratio * (current[1] - previous[1]),
            )
            out.append(point)
            source.insert(index, point)
            distance_accumulator = 0.0
        else:
            distance_accumulator += segment
            index += 1
    while len(out) < count:
        out.append(source[-1])
    return out[:count]


def _normalise(points: List[Point]) -> List[Point]:
    if not points:
        return []
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    scale = max(max(xs) - min(xs), max(ys) - min(ys), 1e-9)
    scaled = [(point[0] / scale, point[1] / scale) for point in points]
    center_x = sum(point[0] for point in scaled) / len(scaled)
    center_y = sum(point[1] for point in scaled) / len(scaled)
    return [(point[0] - center_x, point[1] - center_y) for point in scaled]


def make_shape_lock_template(raw_strokes: Any) -> Dict[str, Any] | None:
    strokes = _clean_strokes(raw_strokes)
    flattened = [point for stroke in strokes for point in stroke]
    if len(flattened) < 2 or _path_length(flattened) < 40.0:
        return None
    points = _normalise(_resample(flattened, N))
    if not points:
        return None
    return {
        "points": [[point[0], point[1]] for point in points],
        "stroke_count": len(strokes),
    }


def _mean_distance(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]]) -> float:
    if not a or len(a) != len(b):
        return 1.0
    return sum(
        math.hypot(float(a[index][0]) - float(b[index][0]), float(a[index][1]) - float(b[index][1]))
        for index in range(len(a))
    ) / len(a)


def shape_lock_similarity(a: Dict[str, Any] | None, b: Dict[str, Any] | None) -> float:
    if not isinstance(a, dict) or not isinstance(b, dict):
        return 0.0
    points_a = a.get("points") or []
    points_b = b.get("points") or []
    forward = _mean_distance(points_a, points_b)
    reversed_distance = _mean_distance(points_a, list(reversed(points_b)))
    distance = min(forward, reversed_distance)
    score = 1.0 - distance / 0.5
    if int(a.get("stroke_count") or 0) != int(b.get("stroke_count") or 0):
        score -= 0.12
    return max(0.0, min(1.0, score))


def learn_shape_lock_baseline(raw_attempts: List[Any]) -> Dict[str, Any]:
    templates = []
    for attempt in raw_attempts:
        strokes = attempt.get("strokes") if isinstance(attempt, dict) else attempt
        template = make_shape_lock_template(strokes)
        if template:
            templates.append(template)
    pair_scores = [shape_lock_similarity(a, b) for a, b in combinations(templates, 2)]
    self_score = sum(pair_scores) / len(pair_scores) if pair_scores else 0.0
    threshold = min(0.9, max(0.7, self_score - 0.08))
    return {
        "version": "lovable-shapelock-compatible-v1",
        "templates": templates,
        "pair_scores": pair_scores,
        "self_score": self_score,
        "threshold": threshold,
    }


def verify_shape_lock_baseline(attempt_strokes: Any, model: Dict[str, Any] | None) -> Dict[str, Any]:
    attempt = make_shape_lock_template(attempt_strokes)
    templates = (model or {}).get("templates") or []
    scores = [shape_lock_similarity(attempt, template) for template in templates]
    best = max(scores) if scores else 0.0
    threshold = float((model or {}).get("threshold") or 0.70)
    return {
        "score": best,
        "threshold": threshold,
        "passed": best >= threshold,
        "reference_scores": scores,
        "version": "lovable-shapelock-compatible-v1",
    }
