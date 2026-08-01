from __future__ import annotations

import math
from statistics import median
from typing import Any, Dict, Iterable, List, Sequence, Tuple

Point = Tuple[float, float]
Segment = Dict[str, float | int]

THETA_BINS = 72
RHO_BINS = 96
RHO_MAX = math.sqrt(0.5)
RHO_TOLERANCE = 0.026
ANGLE_TOLERANCE = math.radians(13.0)
MAX_PEAKS = 16


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _point(value: Any) -> Point | None:
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return None


def _attempt_strokes(attempt: Any) -> List[List[Point]]:
    raw = attempt.get("strokes") if isinstance(attempt, dict) else attempt
    if not isinstance(raw, list):
        return []
    strokes: List[List[Point]] = []
    for raw_stroke in raw:
        if not isinstance(raw_stroke, list):
            continue
        stroke = [point for point in (_point(value) for value in raw_stroke) if point is not None]
        if len(stroke) >= 2:
            strokes.append(stroke)
    return strokes


def _distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _path_length(points: Sequence[Point]) -> float:
    return sum(_distance(points[index - 1], points[index]) for index in range(1, len(points)))


def _normalize(strokes: Sequence[Sequence[Point]]) -> List[List[Point]]:
    points = [point for stroke in strokes for point in stroke]
    if not points:
        return []
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    scale = max(max_x - min_x, max_y - min_y, 1e-9)
    cx = (min_x + max_x) / 2.0
    cy = (min_y + max_y) / 2.0
    return [
        [((point[0] - cx) / scale, (point[1] - cy) / scale) for point in stroke]
        for stroke in strokes
    ]


def _resample(points: Sequence[Point], spacing: float = 0.010) -> List[Point]:
    if len(points) < 2:
        return list(points)
    total = _path_length(points)
    if total <= 1e-12:
        return [points[0], points[-1]]
    count = max(3, min(220, int(math.ceil(total / spacing)) + 1))
    cumulative = [0.0]
    for index in range(1, len(points)):
        cumulative.append(cumulative[-1] + _distance(points[index - 1], points[index]))
    out: List[Point] = []
    segment = 1
    for sample_index in range(count):
        target = total * sample_index / max(1, count - 1)
        while segment < len(cumulative) - 1 and cumulative[segment] < target:
            segment += 1
        a = points[segment - 1]
        b = points[segment]
        lo = cumulative[segment - 1]
        hi = cumulative[segment]
        alpha = 0.0 if hi <= lo else (target - lo) / (hi - lo)
        out.append((a[0] + alpha * (b[0] - a[0]), a[1] + alpha * (b[1] - a[1])))
    return out


def _angle_distance_pi(a: float, b: float) -> float:
    diff = abs((a - b) % math.pi)
    return min(diff, math.pi - diff)


def _signed_angle_diff_pi(a: float, b: float) -> float:
    return ((a - b + math.pi / 2.0) % math.pi) - math.pi / 2.0


def _quantile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = max(0.0, min(1.0, q)) * (len(ordered) - 1)
    lo = int(math.floor(position))
    hi = int(math.ceil(position))
    if lo == hi:
        return ordered[lo]
    alpha = position - lo
    return ordered[lo] * (1.0 - alpha) + ordered[hi] * alpha


def _bend_stats(segments: Sequence[Segment], indices: Sequence[int], theta: float, rho: float) -> Dict[str, float]:
    if len(indices) < 2:
        return {
            "angle_span": 0.0,
            "rho_span": 0.0,
            "path_excess": 0.0,
            "corridor_support": 0.0,
        }
    line_angle = (theta + math.pi / 2.0) % math.pi
    cos_theta = math.cos(theta)
    sin_theta = math.sin(theta)
    selected = [segments[index] for index in indices]
    angle_residuals = [
        _signed_angle_diff_pi(float(segment["angle"]), line_angle)
        for segment in selected
    ]
    rho_residuals = [
        float(segment["mx"]) * cos_theta + float(segment["my"]) * sin_theta - rho
        for segment in selected
    ]
    path = sum(float(segment["length"]) for segment in selected)
    first = selected[0]
    last = selected[-1]
    chord = math.hypot(
        float(last["bx"]) - float(first["ax"]),
        float(last["by"]) - float(first["ay"]),
    )
    return {
        "angle_span": max(0.0, _quantile(angle_residuals, 0.90) - _quantile(angle_residuals, 0.10)),
        "rho_span": max(0.0, _quantile(rho_residuals, 0.90) - _quantile(rho_residuals, 0.10)),
        "path_excess": max(0.0, path / max(chord, 1e-9) - 1.0),
        "corridor_support": path,
    }


def _corridor_for_line(segments: Sequence[Segment], theta: float, rho: float) -> List[int]:
    line_angle = (theta + math.pi / 2.0) % math.pi
    cos_theta = math.cos(theta)
    sin_theta = math.sin(theta)
    by_stroke: Dict[int, List[Tuple[int, float, bool, int]]] = {}
    for global_index, segment in enumerate(segments):
        distance = abs(float(segment["mx"]) * cos_theta + float(segment["my"]) * sin_theta - rho)
        aligned = _angle_distance_pi(float(segment["angle"]), line_angle) <= math.radians(38.0)
        hit = distance <= 0.082 and aligned
        by_stroke.setdefault(int(segment["stroke"]), []).append(
            (int(segment["index"]), float(segment["length"]), hit, global_index)
        )

    best_indices: List[int] = []
    best_length = 0.0
    for values in by_stroke.values():
        values.sort()
        run_indices: List[int] = []
        run_length = 0.0
        gap_budget = 2
        for _index, length, hit, global_index in values:
            if hit:
                run_indices.append(global_index)
                run_length += length
                gap_budget = 2
            elif gap_budget > 0 and run_indices:
                gap_budget -= 1
            else:
                run_indices = []
                run_length = 0.0
                gap_budget = 2
            if run_length > best_length:
                best_length = run_length
                best_indices = list(run_indices)
    return best_indices


def _segments(strokes: Sequence[Sequence[Point]]) -> Tuple[List[Segment], float]:
    segments: List[Segment] = []
    total = 0.0
    for stroke_index, stroke in enumerate(strokes):
        sampled = _resample(stroke)
        for index in range(1, len(sampled)):
            a, b = sampled[index - 1], sampled[index]
            length = _distance(a, b)
            if length <= 1e-9:
                continue
            angle = math.atan2(b[1] - a[1], b[0] - a[0]) % math.pi
            segments.append({
                "stroke": stroke_index,
                "index": index - 1,
                "ax": a[0],
                "ay": a[1],
                "bx": b[0],
                "by": b[1],
                "mx": (a[0] + b[0]) / 2.0,
                "my": (a[1] + b[1]) / 2.0,
                "length": length,
                "angle": angle,
            })
            total += length
    return segments, total


def _rho_index(rho: float) -> int:
    scaled = (rho + RHO_MAX) / (2.0 * RHO_MAX)
    return max(0, min(RHO_BINS - 1, int(round(scaled * (RHO_BINS - 1)))))


def _rho_center(index: int) -> float:
    return -RHO_MAX + (2.0 * RHO_MAX * index / max(1, RHO_BINS - 1))


def _theta_center(index: int) -> float:
    return math.pi * index / THETA_BINS


def _accumulator(segments: Sequence[Segment]) -> List[List[float]]:
    accumulator = [[0.0 for _ in range(RHO_BINS)] for _ in range(THETA_BINS)]
    cosines = [math.cos(_theta_center(index)) for index in range(THETA_BINS)]
    sines = [math.sin(_theta_center(index)) for index in range(THETA_BINS)]
    for segment in segments:
        x = float(segment["mx"])
        y = float(segment["my"])
        weight = float(segment["length"])
        for theta_index in range(THETA_BINS):
            rho = x * cosines[theta_index] + y * sines[theta_index]
            accumulator[theta_index][_rho_index(rho)] += weight
    return accumulator


def _local_maxima(accumulator: Sequence[Sequence[float]], total_length: float) -> List[Tuple[float, int, int]]:
    peaks: List[Tuple[float, int, int]] = []
    floor = max(0.025, total_length * 0.035)
    for theta_index in range(THETA_BINS):
        for rho_index in range(RHO_BINS):
            value = float(accumulator[theta_index][rho_index])
            if value < floor:
                continue
            is_maximum = True
            for theta_offset in (-1, 0, 1):
                other_theta = (theta_index + theta_offset) % THETA_BINS
                for rho_offset in (-2, -1, 0, 1, 2):
                    if theta_offset == 0 and rho_offset == 0:
                        continue
                    other_rho = rho_index + rho_offset
                    if 0 <= other_rho < RHO_BINS and float(accumulator[other_theta][other_rho]) > value:
                        is_maximum = False
                        break
                if not is_maximum:
                    break
            if is_maximum:
                peaks.append((value, theta_index, rho_index))
    peaks.sort(reverse=True)
    return peaks[: MAX_PEAKS * 3]


def _support_for_line(segments: Sequence[Segment], theta: float, rho: float) -> Dict[str, Any]:
    line_angle = (theta + math.pi / 2.0) % math.pi
    cos_theta = math.cos(theta)
    sin_theta = math.sin(theta)
    supported: List[int] = []
    by_stroke: Dict[int, List[Tuple[int, float, bool]]] = {}
    for global_index, segment in enumerate(segments):
        distance = abs(float(segment["mx"]) * cos_theta + float(segment["my"]) * sin_theta - rho)
        aligned = _angle_distance_pi(float(segment["angle"]), line_angle) <= ANGLE_TOLERANCE
        hit = distance <= RHO_TOLERANCE and aligned
        by_stroke.setdefault(int(segment["stroke"]), []).append((int(segment["index"]), float(segment["length"]), hit))
        if hit:
            supported.append(global_index)

    longest = 0.0
    longest_stroke = -1
    total_support = 0.0
    for stroke_index, values in by_stroke.items():
        values.sort()
        run = 0.0
        best = 0.0
        gap_budget = 1
        for _index, length, hit in values:
            if hit:
                run += length
                total_support += length
                gap_budget = 1
            elif gap_budget > 0 and run > 0.0:
                gap_budget -= 1
            else:
                run = 0.0
                gap_budget = 1
            best = max(best, run)
        if best > longest:
            longest = best
            longest_stroke = stroke_index
    return {
        "support": longest,
        "total_support": total_support,
        "stroke": longest_stroke,
        "supported_segment_indices": supported,
    }


def _deduplicate(lines: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    for line in sorted(lines, key=lambda item: float(item["support"]), reverse=True):
        duplicate = False
        for existing in selected:
            if (
                _angle_distance_pi(float(line["theta"]), float(existing["theta"])) <= math.radians(7.5)
                and abs(float(line["rho"]) - float(existing["rho"])) <= 0.040
            ):
                duplicate = True
                break
        if not duplicate:
            selected.append(line)
        if len(selected) >= MAX_PEAKS:
            break
    return selected


def extract_hough_signature(attempt: Any) -> Dict[str, Any]:
    strokes = _normalize(_attempt_strokes(attempt))
    segments, total_length = _segments(strokes)
    if not segments or total_length <= 1e-9:
        return {
            "valid": False,
            "total_length": 0.0,
            "line_coverage": 0.0,
            "long_line_coverage": 0.0,
            "line_count": 0,
            "lines": [],
            "orientation_histogram": [0.0] * 18,
        }

    accumulator = _accumulator(segments)
    raw_lines: List[Dict[str, Any]] = []
    for votes, theta_index, rho_index in _local_maxima(accumulator, total_length):
        theta = _theta_center(theta_index)
        rho = _rho_center(rho_index)
        support = _support_for_line(segments, theta, rho)
        contiguous = float(support["support"])
        if contiguous < max(0.055, 0.075 * total_length):
            continue
        corridor_indices = _corridor_for_line(segments, theta, rho)
        bend = _bend_stats(segments, corridor_indices, theta, rho)
        raw_lines.append({
            "theta": theta,
            "rho": rho,
            "angle": (theta + math.pi / 2.0) % math.pi,
            "votes": votes,
            "support": contiguous,
            "total_support": float(support["total_support"]),
            "support_fraction": contiguous / total_length,
            "stroke": int(support["stroke"]),
            "bend_angle_span": bend["angle_span"],
            "bend_rho_span": bend["rho_span"],
            "bend_path_excess": bend["path_excess"],
            "bend_corridor_support": bend["corridor_support"],
            "supported_segment_indices": support["supported_segment_indices"],
        })
    lines = _deduplicate(raw_lines)

    covered: set[int] = set()
    long_covered: set[int] = set()
    long_floor = max(0.12, 0.15 * total_length)
    for line in lines:
        indices = {int(index) for index in line.get("supported_segment_indices") or []}
        covered.update(indices)
        if float(line["support"]) >= long_floor:
            long_covered.update(indices)
    coverage_length = sum(float(segments[index]["length"]) for index in covered)
    long_coverage_length = sum(float(segments[index]["length"]) for index in long_covered)

    histogram = [0.0] * 18
    for line in lines:
        index = min(17, int(float(line["angle"]) / math.pi * 18))
        histogram[index] += float(line["support"])
    hist_total = sum(histogram)
    if hist_total > 1e-12:
        histogram = [value / hist_total for value in histogram]

    clean_lines = [
        {key: value for key, value in line.items() if key != "supported_segment_indices"}
        for line in lines
    ]
    return {
        "valid": True,
        "total_length": total_length,
        "line_coverage": _clamp01(coverage_length / total_length),
        "long_line_coverage": _clamp01(long_coverage_length / total_length),
        "line_count": len(clean_lines),
        "lines": clean_lines,
        "orientation_histogram": histogram,
    }


def _circular_mean_pi(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    x = sum(math.cos(2.0 * value) for value in values)
    y = sum(math.sin(2.0 * value) for value in values)
    return (0.5 * math.atan2(y, x)) % math.pi


def _cluster_reference_lines(signatures: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    clusters: List[Dict[str, Any]] = []
    for reference_index, signature in enumerate(signatures):
        for line in signature.get("lines") or []:
            best: Dict[str, Any] | None = None
            best_cost = 1e9
            for cluster in clusters:
                if reference_index in cluster["reference_indices"]:
                    continue
                angle_diff = _angle_distance_pi(float(line["theta"]), float(cluster["theta"]))
                rho_diff = abs(float(line["rho"]) - float(cluster["rho"]))
                if angle_diff <= math.radians(11.0) and rho_diff <= 0.075:
                    cost = angle_diff / math.radians(11.0) + rho_diff / 0.075
                    if cost < best_cost:
                        best = cluster
                        best_cost = cost
            if best is None:
                clusters.append({
                    "theta": float(line["theta"]),
                    "rho": float(line["rho"]),
                    "members": [(reference_index, line)],
                    "reference_indices": {reference_index},
                })
            else:
                best["members"].append((reference_index, line))
                best["reference_indices"].add(reference_index)
                best["theta"] = _circular_mean_pi([float(item[1]["theta"]) for item in best["members"]])
                best["rho"] = median([float(item[1]["rho"]) for item in best["members"]])

    stable: List[Dict[str, Any]] = []
    for cluster in clusters:
        members = cluster["members"]
        if len(cluster["reference_indices"]) < 2:
            continue
        supports = [float(item[1]["support"]) for item in members]
        fractions = [float(item[1]["support_fraction"]) for item in members]
        median_support = median(supports)
        if median_support < 0.085 or median(fractions) < 0.070:
            continue
        angle_spans = [float(item[1].get("bend_angle_span") or 0.0) for item in members]
        rho_spans = [float(item[1].get("bend_rho_span") or 0.0) for item in members]
        path_excesses = [float(item[1].get("bend_path_excess") or 0.0) for item in members]
        stable.append({
            "theta": _circular_mean_pi([float(item[1]["theta"]) for item in members]),
            "rho": median([float(item[1]["rho"]) for item in members]),
            "support": len(cluster["reference_indices"]),
            "median_segment_support": median_support,
            "min_segment_support": min(supports),
            "median_support_fraction": median(fractions),
            "median_bend_angle_span": median(angle_spans),
            "max_bend_angle_span": max(angle_spans),
            "median_bend_rho_span": median(rho_spans),
            "max_bend_rho_span": max(rho_spans),
            "median_bend_path_excess": median(path_excesses),
            "max_bend_path_excess": max(path_excesses),
            "strong": median_support >= 0.16 and median(fractions) >= 0.12,
        })
    stable.sort(key=lambda item: float(item["median_segment_support"]), reverse=True)
    return stable[:10]


def _base_model_from_signatures(signatures: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    valid = [signature for signature in signatures if bool(signature.get("valid"))]
    if len(valid) < 2:
        return {
            "applicable": False,
            "hard_gate_reliable": False,
            "reason": "insufficient_valid_references",
            "support": len(valid),
            "stable_lines": [],
            "critical_line_count": 0,
            "medium_line_count": 0,
            "reference_signatures": signatures,
        }

    stable_lines = _cluster_reference_lines(valid)
    support_count = len(valid)
    for line in stable_lines:
        ref_support = int(line.get("support") or 0)
        fraction = float(line.get("median_support_fraction") or 0.0)
        segment_support = float(line.get("median_segment_support") or 0.0)
        unanimous = ref_support >= support_count
        line["unanimous"] = unanimous
        line["critical"] = bool(
            unanimous
            and fraction >= 0.155
            and segment_support >= 0.145
        )
        line["medium"] = bool(
            unanimous
            and fraction >= 0.105
            and segment_support >= 0.095
        )

    coverages = [float(signature["line_coverage"]) for signature in valid]
    long_coverages = [float(signature["long_line_coverage"]) for signature in valid]
    counts = [int(signature["line_count"]) for signature in valid]
    median_coverage = median(coverages)
    median_long = median(long_coverages)
    clearly_line_dominant = median_coverage >= 0.42 and median_long >= 0.26 and median(counts) >= 2
    applicable = bool(clearly_line_dominant and len(stable_lines) >= 2)
    critical_count = sum(bool(line.get("critical")) for line in stable_lines)
    medium_count = sum(bool(line.get("medium")) for line in stable_lines)
    hard_gate_reliable = bool(applicable and (critical_count >= 1 or medium_count >= 2))

    return {
        "applicable": applicable,
        "hard_gate_reliable": hard_gate_reliable,
        "reason": None if applicable else "enrollment_not_consistently_line_dominant",
        "support": support_count,
        "median_line_coverage": median_coverage,
        "minimum_line_coverage": min(coverages),
        "median_long_line_coverage": median_long,
        "minimum_long_line_coverage": min(long_coverages),
        "minimum_candidate_coverage": max(0.24, min(coverages) - 0.16),
        "minimum_candidate_long_coverage": max(0.12, min(long_coverages) - 0.16),
        "stable_lines": stable_lines,
        "critical_line_count": critical_count,
        "medium_line_count": medium_count,
        "reference_signatures": signatures,
    }


def learn_hough_model(attempts: Sequence[Any]) -> Dict[str, Any]:
    """Learn a conservative multi-reference Hough line model.

    Only long lines reproduced in every valid enrollment reference can become
    non-compensable. Smaller or merely two-reference peaks remain diagnostic.
    """
    signatures = [extract_hough_signature(attempt) for attempt in attempts]
    return _base_model_from_signatures(signatures)


def _best_line_match(candidate_lines: Sequence[Dict[str, Any]], reference: Dict[str, Any]) -> Dict[str, Any] | None:
    best: Dict[str, Any] | None = None
    best_cost = 1e9
    for line in candidate_lines:
        angle_diff = _angle_distance_pi(float(line["theta"]), float(reference["theta"]))
        rho_diff = abs(float(line["rho"]) - float(reference["rho"]))
        if angle_diff > math.radians(14.0) or rho_diff > 0.105:
            continue
        cost = angle_diff / math.radians(14.0) + rho_diff / 0.105 - 0.30 * float(line["support_fraction"])
        if cost < best_cost:
            best = line
            best_cost = cost
    return best


def verify_hough_style(redraw_strokes: Any, model: Dict[str, Any], *, hard_gate_enabled: bool = True) -> Dict[str, Any]:
    """Verify unanimous lines and their straight-versus-bowed style.

    Hough localizes persistent line candidates. A second residual stage checks
    whether matched long lines have acquired distributed tangent drift and
    orthogonal bow. Coverage alone remains diagnostic.
    """
    signature = extract_hough_signature(redraw_strokes)
    if not bool(model.get("applicable")):
        return {
            "applicable": False,
            "hard_gate_reliable": False,
            "hard_gate_enabled": False,
            "pass": True,
            "raw_pass": True,
            "score": 1.0,
            "violation_count": 0,
            "strong_violation_count": 0,
            "critical_violation_count": 0,
            "medium_violation_count": 0,
            "bend_violation_count": 0,
            "severe_bend_violation_count": 0,
            "lost_line_failure": False,
            "bend_failure": False,
            "coverage_failure": False,
            "candidate_signature": signature,
            "line_matches": [],
        }

    candidate_lines = signature.get("lines") or []
    matches: List[Dict[str, Any]] = []
    violations = 0
    strong_violations = 0
    critical_violations = 0
    medium_violations = 0
    bend_violations = 0
    severe_bend_violations = 0
    support_ratios: List[float] = []
    for reference in model.get("stable_lines") or []:
        match = _best_line_match(candidate_lines, reference)
        candidate_support = float(match.get("support") or 0.0) if match else 0.0
        median_support = float(reference.get("median_segment_support") or 0.0)
        minimum_support = float(reference.get("min_segment_support") or 0.0)
        allowed_support = max(0.055, 0.52 * median_support, minimum_support - 0.070)
        strong_floor = max(0.035, 0.30 * median_support)
        ratio = _clamp01(candidate_support / max(median_support, 1e-9))
        violated = candidate_support < allowed_support
        strong = bool(reference.get("strong")) and candidate_support < strong_floor
        critical = bool(reference.get("critical")) and ratio < 0.53
        medium = bool(reference.get("medium")) and ratio < 0.40

        candidate_angle_span = float(match.get("bend_angle_span") or 0.0) if match else 0.0
        candidate_rho_span = float(match.get("bend_rho_span") or 0.0) if match else 0.0
        candidate_path_excess = float(match.get("bend_path_excess") or 0.0) if match else 0.0
        angle_limit = max(
            math.radians(12.0),
            1.80 * float(reference.get("max_bend_angle_span") or 0.0) + math.radians(2.5),
        )
        rho_limit = max(
            0.042,
            1.85 * float(reference.get("max_bend_rho_span") or 0.0) + 0.010,
        )
        path_limit = max(
            0.045,
            2.00 * float(reference.get("max_bend_path_excess") or 0.0) + 0.015,
        )
        line_is_consensus = bool(reference.get("unanimous")) and bool(
            reference.get("critical") or reference.get("medium")
        )
        bend = bool(
            line_is_consensus
            and match is not None
            and ratio >= 0.42
            and (
                (candidate_angle_span > angle_limit and candidate_rho_span > rho_limit)
                or (
                    candidate_path_excess > path_limit
                    and candidate_angle_span > 0.82 * angle_limit
                    and candidate_rho_span > 0.82 * rho_limit
                )
            )
        )
        severe_bend = bool(
            bend
            and (
                (candidate_angle_span > 2.00 * angle_limit and candidate_rho_span > 1.10 * rho_limit)
                or candidate_path_excess > 1.55 * path_limit
            )
        )
        if violated:
            violations += 1
        if strong:
            strong_violations += 1
        if critical:
            critical_violations += 1
        if medium:
            medium_violations += 1
        if bend:
            bend_violations += 1
        if severe_bend:
            severe_bend_violations += 1
        support_ratios.append(ratio)
        matches.append({
            "reference_theta": reference.get("theta"),
            "reference_rho": reference.get("rho"),
            "reference_support": median_support,
            "reference_support_fraction": reference.get("median_support_fraction"),
            "reference_count": reference.get("support"),
            "reference_unanimous": reference.get("unanimous"),
            "reference_critical": reference.get("critical"),
            "reference_medium": reference.get("medium"),
            "candidate_support": candidate_support,
            "allowed_support": allowed_support,
            "support_ratio": ratio,
            "violation": violated,
            "strong": strong,
            "critical_violation": critical,
            "medium_violation": medium,
            "bend_violation": bend,
            "severe_bend_violation": severe_bend,
            "candidate_bend_angle_span": candidate_angle_span,
            "candidate_bend_rho_span": candidate_rho_span,
            "candidate_bend_path_excess": candidate_path_excess,
            "bend_angle_limit": angle_limit,
            "bend_rho_limit": rho_limit,
            "bend_path_limit": path_limit,
            "candidate_line": match,
        })

    line_coverage = float(signature.get("line_coverage") or 0.0)
    long_coverage = float(signature.get("long_line_coverage") or 0.0)
    coverage_failure = bool(
        line_coverage < float(model.get("minimum_candidate_coverage") or 0.0)
        and long_coverage < float(model.get("minimum_candidate_long_coverage") or 0.0)
    )
    coverage_ratio = _clamp01(line_coverage / max(float(model.get("median_line_coverage") or 0.0), 1e-9))
    long_ratio = _clamp01(long_coverage / max(float(model.get("median_long_line_coverage") or 0.0), 1e-9))
    line_ratio = median(support_ratios) if support_ratios else 0.0
    score = _clamp01(0.35 * coverage_ratio + 0.30 * long_ratio + 0.35 * line_ratio)

    reliable = bool(model.get("hard_gate_reliable"))
    lost_line_failure = bool(
        reliable
        and score < 0.74
        and (
            critical_violations >= 2
            or medium_violations >= 3
        )
    )
    bend_failure = bool(
        reliable
        and (
            bend_violations >= 2
            or severe_bend_violations >= 1
        )
    )
    hard_failure = bool(lost_line_failure or bend_failure)
    raw_pass = not hard_failure
    effective_pass = bool(raw_pass or not hard_gate_enabled)

    return {
        "applicable": True,
        "hard_gate_reliable": reliable,
        "hard_gate_enabled": bool(hard_gate_enabled and reliable),
        "pass": effective_pass,
        "raw_pass": raw_pass,
        "score": score,
        "violation_count": violations,
        "strong_violation_count": strong_violations,
        "critical_violation_count": critical_violations,
        "medium_violation_count": medium_violations,
        "bend_violation_count": bend_violations,
        "severe_bend_violation_count": severe_bend_violations,
        "lost_line_failure": lost_line_failure,
        "bend_failure": bend_failure,
        "coverage_failure": coverage_failure,
        "candidate_signature": signature,
        "line_matches": matches,
    }
