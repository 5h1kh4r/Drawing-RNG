from __future__ import annotations

"""Seed Quality Score for Drawing-RNG / Draw2Seed.

This module scores an enrolled drawing seed before it is treated as a usable
security input.  It does not derive secret material and it should not be read as
an entropy estimator in the cryptographic sense.  It is a usable-security filter
that asks whether a drawing is stable enough for the owner and structurally rich
enough to avoid obvious/common visual collisions.
"""

import math
from collections import Counter
from typing import Any, Dict, List, Sequence, Tuple

from .similarity import token_kind

_DIRECTIONS_16 = {
    "E", "ENE", "NE", "NNE", "N", "NNW", "NW", "WNW",
    "W", "WSW", "SW", "SSW", "S", "SSE", "SE", "ESE",
}


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def _score_between(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    return _clamp01((value - low) / (high - low))


def _direction_base(token: str) -> str | None:
    if not isinstance(token, str) or "_" not in token:
        return None
    base = token.rsplit("_", 1)[0]
    return base if base in _DIRECTIONS_16 else None


def _length_bucket(token: str) -> str | None:
    if not isinstance(token, str) or "_" not in token:
        return None
    maybe = token.rsplit("_", 1)[1]
    return maybe if maybe in {"S", "M", "L"} else None


def _bigrams(tokens: Sequence[str]) -> Counter[Tuple[str, str]]:
    return Counter(zip(tokens, tokens[1:]))


def _unique_ratio(values: Sequence[Any]) -> float:
    if not values:
        return 0.0
    return len(set(values)) / max(len(values), 1)


def _entropy(values: Sequence[Any]) -> float:
    if not values:
        return 0.0
    counts = Counter(values)
    total = sum(counts.values())
    if total <= 1:
        return 0.0
    h = 0.0
    for count in counts.values():
        p = count / total
        h -= p * math.log2(p)
    # normalized by maximum possible entropy for this support size
    max_h = math.log2(max(len(counts), 2))
    return _clamp01(h / max_h) if max_h else 0.0


def _token_complexity(tokens: Sequence[str]) -> Tuple[float, Dict[str, Any], List[str]]:
    warnings: List[str] = []
    token_count = len(tokens)
    direction_tokens = [t for t in tokens if token_kind(str(t)) == "direction"]
    directions = [d for d in (_direction_base(str(t)) for t in direction_tokens) if d]
    lengths = [l for l in (_length_bucket(str(t)) for t in direction_tokens) if l]
    turn_count = sum(1 for t in tokens if token_kind(str(t)) == "turn")
    penup_count = sum(1 for t in tokens if token_kind(str(t)) == "penup")
    relation_count = sum(1 for t in tokens if token_kind(str(t)) == "relation")
    bigrams = _bigrams(tokens)

    count_score = _score_between(token_count, 10, 42)
    # Excessively long token streams often represent brittle scenes. Do not
    # punish too hard, but avoid letting length alone inflate quality.
    if token_count > 90:
        count_score *= 0.88
    direction_score = _score_between(len(set(directions)), 3, 8)
    length_score = _score_between(len(set(lengths)), 1, 3)
    turn_score = _score_between(turn_count, 2, 10)
    penup_score = _score_between(penup_count, 0, 4)
    bigram_score = _clamp01(0.35 + 0.65 * _unique_ratio(list(bigrams.elements()))) if bigrams else 0.0
    entropy_score = _entropy(tokens)

    if token_count < 10:
        warnings.append("low_token_count")
    if len(set(directions)) < 3:
        warnings.append("low_direction_diversity")
    if turn_count < 2 and token_count < 24:
        warnings.append("low_turn_diversity")
    if len(bigrams) < 8:
        warnings.append("low_bigram_diversity")

    score = _clamp01(
        0.24 * count_score
        + 0.22 * direction_score
        + 0.10 * length_score
        + 0.15 * turn_score
        + 0.09 * penup_score
        + 0.10 * bigram_score
        + 0.10 * entropy_score
    )
    features = {
        "token_count": token_count,
        "direction_token_count": len(direction_tokens),
        "unique_direction_count": len(set(directions)),
        "unique_length_bucket_count": len(set(lengths)),
        "turn_token_count": turn_count,
        "penup_token_count": penup_count,
        "relation_token_count": relation_count,
        "unique_bigram_count": len(bigrams),
        "token_entropy": entropy_score,
        "token_count_score": count_score,
        "direction_diversity_score": direction_score,
        "turn_diversity_score": turn_score,
        "bigram_diversity_score": bigram_score,
    }
    return score, features, warnings


def _geometry_complexity(geometry: Dict[str, Any], scene_model: Dict[str, Any] | None = None) -> Tuple[float, Dict[str, Any], List[str]]:
    warnings: List[str] = []
    components = geometry.get("components") or []
    relations = geometry.get("relations") or []
    stroke_count = int(geometry.get("stroke_count") or len(components) or 0)
    closed_count = sum(1 for c in components if bool(c.get("closed")))
    open_count = max(0, stroke_count - closed_count)
    total_crossings = sum(_safe_float(r.get("crossing_count")) for r in relations)
    overlaps = [_safe_float(r.get("overlap")) for r in relations]
    centers = [c.get("center") or [0.0, 0.0] for c in components]

    # Layout spread: simple but useful signal that parts occupy more than a tiny
    # local patch. Geometry signatures are normalized around roughly [-0.5, 0.5].
    if centers:
        xs = [_safe_float(c[0]) for c in centers]
        ys = [_safe_float(c[1]) for c in centers]
        spread = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    else:
        spread = 0.0

    if scene_model:
        complexity_class = str(scene_model.get("complexity_class") or "")
        scene_stability = _safe_float(scene_model.get("scene_stability_score"))
        scene_pair_scores = scene_model.get("scene_pair_scores") or []
        scene_pair_median = sorted([_safe_float(x) for x in scene_pair_scores])[len(scene_pair_scores) // 2] if scene_pair_scores else scene_stability
        canonical_scene = scene_model.get("canonical_scene") or {}
        major_clusters = int(canonical_scene.get("major_cluster_count") or len(canonical_scene.get("major_clusters") or []) or 0)
        layout_entropy = _safe_float(canonical_scene.get("layout_entropy"))
    else:
        complexity_class = ""
        scene_stability = 0.0
        scene_pair_median = 0.0
        major_clusters = 0
        layout_entropy = 0.0

    stroke_score = _score_between(stroke_count, 2, 8)
    if stroke_count > 18:
        stroke_score *= 0.90
    component_score = _score_between(len(components), 2, 7)
    relation_score = _score_between(len(relations), 1, 12)
    open_closed_mix_score = 0.0
    if stroke_count > 0:
        if closed_count and open_count:
            open_closed_mix_score = 1.0
        elif closed_count or open_count >= 2:
            open_closed_mix_score = 0.55
    crossing_score = _score_between(total_crossings, 0, 2)
    overlap_score = _clamp01(sum(overlaps) / max(len(overlaps), 1)) if overlaps else 0.0
    spread_score = _score_between(spread, 0.18, 0.72)
    scene_part_score = _score_between(major_clusters, 1, 4)
    scene_layout_score = _score_between(layout_entropy, 0.10, 0.34)

    if stroke_count <= 1:
        warnings.append("too_few_strokes")
    if len(components) <= 1 and stroke_count <= 3:
        warnings.append("single_component_seed")
    if spread < 0.16 and stroke_count <= 5:
        warnings.append("low_layout_spread")
    if closed_count == stroke_count and stroke_count <= 2:
        warnings.append("mostly_simple_closed_contours")

    score = _clamp01(
        0.17 * stroke_score
        + 0.14 * component_score
        + 0.14 * relation_score
        + 0.12 * open_closed_mix_score
        + 0.08 * crossing_score
        + 0.07 * overlap_score
        + 0.14 * spread_score
        + 0.08 * scene_part_score
        + 0.06 * scene_layout_score
    )
    features = {
        "stroke_count": stroke_count,
        "component_count": len(components),
        "closed_component_count": closed_count,
        "open_component_count": open_count,
        "relation_count": len(relations),
        "total_crossings": total_crossings,
        "layout_spread": spread,
        "major_cluster_count": major_clusters,
        "scene_layout_entropy": layout_entropy,
        "complexity_class": complexity_class,
        "scene_stability_score": scene_stability,
        "scene_pair_median": scene_pair_median,
        "stroke_complexity_score": stroke_score,
        "component_complexity_score": component_score,
        "open_closed_mix_score": open_closed_mix_score,
        "layout_spread_score": spread_score,
    }
    return score, features, warnings


def _closed_contour_commonness(geometry: Dict[str, Any]) -> Tuple[float, List[str]]:
    comps = geometry.get("components") or []
    if not comps:
        return 0.0, []
    warnings: List[str] = []
    stroke_count = int(geometry.get("stroke_count") or len(comps) or 0)
    closed_like = []
    roundness_values = []
    circularity_values = []
    for c in comps:
        curv = c.get("curvature") or {}
        closure = _safe_float(c.get("closure_confidence"), 1.0 if c.get("closed") else 0.0)
        roundness = _safe_float(curv.get("closed_roundness"))
        circularity = _safe_float(curv.get("closed_contour_circularity"))
        if bool(c.get("closed")) or closure >= 0.28:
            closed_like.append(c)
            roundness_values.append(roundness)
            circularity_values.append(circularity)
    if not closed_like:
        return 0.0, []
    avg_round = sum(roundness_values) / len(roundness_values)
    avg_circ = sum(circularity_values) / len(circularity_values)
    risk = 0.0
    if stroke_count <= 2 and avg_round >= 0.62:
        risk = max(risk, 0.78)
        warnings.append("common_circle_or_loop_like_seed")
    if stroke_count <= 4 and len(closed_like) == 1 and avg_circ >= 0.45:
        risk = max(risk, 0.55)
        warnings.append("single_closed_shape_seed")
    return _clamp01(risk), warnings


def _common_shape_risk(tokens: Sequence[str], geometry: Dict[str, Any], scene_model: Dict[str, Any] | None = None) -> Tuple[float, Dict[str, Any], List[str]]:
    warnings: List[str] = []
    token_count = len(tokens)
    direction_count = len({d for d in (_direction_base(str(t)) for t in tokens) if d})
    turn_count = sum(1 for t in tokens if token_kind(str(t)) == "turn")
    penups = sum(1 for t in tokens if token_kind(str(t)) == "penup")
    components = geometry.get("components") or []
    stroke_count = int(geometry.get("stroke_count") or len(components) or 0)
    closed_count = sum(1 for c in components if bool(c.get("closed")))

    risk = 0.0
    if token_count < 12:
        risk = max(risk, 0.65)
        warnings.append("very_short_seed_pattern")
    if direction_count <= 2 and token_count < 22:
        risk = max(risk, 0.70)
        warnings.append("line_or_angle_like_seed")
    if stroke_count <= 2 and penups <= 1 and token_count < 28:
        risk = max(risk, 0.58)
        warnings.append("low_component_count_seed")
    if stroke_count in {3, 4} and closed_count == 0 and direction_count <= 4:
        risk = max(risk, 0.50)
        warnings.append("basic_polygon_or_arrow_like_seed")
    if turn_count <= 1 and token_count < 24:
        risk = max(risk, 0.52)
        warnings.append("low_curvature_seed")

    contour_risk, contour_warnings = _closed_contour_commonness(geometry)
    risk = max(risk, contour_risk)
    warnings.extend(contour_warnings)

    # Common scenes are still usable, but a basic multi-object scene like
    # sun+boat+person should be flagged as visually memorable/copyable rather
    # than rejected by default.
    if scene_model:
        cls = str(scene_model.get("complexity_class") or "")
        canonical_scene = scene_model.get("canonical_scene") or {}
        major_count = int(canonical_scene.get("major_cluster_count") or len(canonical_scene.get("major_clusters") or []) or 0)
        if cls == "complex_scene" and major_count <= 3:
            risk = max(risk, 0.42)
            warnings.append("copyable_common_scene_risk")

    features = {
        "common_shape_risk": _clamp01(risk),
        "token_count": token_count,
        "direction_count": direction_count,
        "turn_count": turn_count,
        "penup_count": penups,
        "stroke_count": stroke_count,
        "closed_count": closed_count,
    }
    return _clamp01(risk), features, sorted(set(warnings))


def _stability_score(token_stability: float, geometry_stability: float, scene_stability: float, complexity_class: str) -> Tuple[float, List[str]]:
    warnings: List[str] = []
    if complexity_class == "complex_scene":
        score = _clamp01(0.42 * token_stability + 0.34 * geometry_stability + 0.24 * max(scene_stability, 0.0))
    else:
        score = _clamp01(0.62 * token_stability + 0.38 * geometry_stability)
    if token_stability < 0.42:
        warnings.append("low_owner_token_stability")
    if geometry_stability < 0.45:
        warnings.append("low_owner_geometry_stability")
    if complexity_class == "complex_scene" and scene_stability < 0.52:
        warnings.append("low_complex_scene_stability")
    return score, warnings


def quality_label(score: float, hard_reject: bool = False) -> str:
    if hard_reject:
        return "reject"
    if score < 35:
        return "weak"
    if score < 58:
        return "usable_with_warning"
    if score < 76:
        return "good"
    return "strong"


def evaluate_seed_quality(
    tokens: Sequence[str],
    geometry: Dict[str, Any],
    *,
    token_stability: float = 0.0,
    geometry_stability: float = 0.0,
    scene_model: Dict[str, Any] | None = None,
    minimum_complexity_failures: Sequence[str] | None = None,
) -> Dict[str, Any]:
    """Return an enrollment-time quality report.

    The score is intentionally conservative: it blends owner stability with
    structural richness and subtracts a common-shape risk penalty.  It should be
    used as an enrollment warning/reject filter, not as proof of entropy.
    """
    scene_model = scene_model or {}
    complexity_class = str(scene_model.get("complexity_class") or "simple_symbol")
    scene_stability = _safe_float(scene_model.get("scene_stability_score"))

    token_score, token_features, token_warnings = _token_complexity(tokens)
    geom_score, geom_features, geom_warnings = _geometry_complexity(geometry, scene_model)
    common_risk, common_features, common_warnings = _common_shape_risk(tokens, geometry, scene_model)
    stability, stability_warnings = _stability_score(
        _safe_float(token_stability),
        _safe_float(geometry_stability),
        scene_stability,
        complexity_class,
    )

    # Phase 4.1 calibration: keep the quality model conservative, but avoid
    # equating all simple symbols with automatic rejection. A simple symbol can
    # be usable if it is stable and structurally distinctive. Hard rejection is
    # reserved for extreme commonness/instability; ordinary low-complexity seeds
    # become "weak" or "usable_with_warning" so the report can separate
    # enrollment guidance from proven forgeability.
    if complexity_class == "complex_scene":
        raw = 100.0 * (0.32 * stability + 0.22 * token_score + 0.31 * geom_score + 0.15 * _clamp01(scene_stability))
        penalty = 12.0 * common_risk
    else:
        # Distinctiveness proxy: reward stable/simple glyphs that still have
        # diverse directions, turns, bigrams, and layout spread.
        direction_div = _safe_float(token_features.get("direction_diversity_score"))
        turn_div = _safe_float(token_features.get("turn_diversity_score"))
        bigram_div = _safe_float(token_features.get("bigram_diversity_score"))
        layout_spread = _safe_float(geom_features.get("layout_spread_score"))
        distinctiveness = _clamp01(0.28 * direction_div + 0.22 * turn_div + 0.25 * bigram_div + 0.25 * layout_spread)
        raw = 100.0 * (0.39 * stability + 0.24 * token_score + 0.24 * geom_score + 0.13 * distinctiveness)
        # Penalize commonness, but reduce the penalty for stable/distinctive
        # glyphs; this prevents many good owner-stable simple symbols from
        # becoming automatic rejects.
        penalty = (22.0 * common_risk) * (1.0 - 0.35 * distinctiveness)
    score = max(0.0, min(100.0, raw - penalty))

    hard_reasons: List[str] = list(minimum_complexity_failures or [])
    if stability < 0.28:
        hard_reasons.append("owner_redraw_stability_too_low")
    if common_risk >= 0.82 and token_score < 0.36 and geom_score < 0.36:
        hard_reasons.append("too_common_and_too_simple")
    if score < 24 and common_risk >= 0.65:
        hard_reasons.append("seed_quality_extremely_low_and_common")
    hard_reject = bool(hard_reasons)

    warnings = sorted(set(token_warnings + geom_warnings + common_warnings + stability_warnings))
    recommendations: List[str] = []
    if "very_short_seed_pattern" in warnings or "low_token_count" in warnings:
        recommendations.append("Add one or two distinctive strokes or components.")
    if "low_direction_diversity" in warnings:
        recommendations.append("Use more than one direction family; avoid only straight or single-axis strokes.")
    if "common_circle_or_loop_like_seed" in warnings or "single_closed_shape_seed" in warnings:
        recommendations.append("Avoid a single common closed shape; add a personal spatial relation or second component.")
    if "low_owner_token_stability" in warnings or "low_owner_geometry_stability" in warnings:
        recommendations.append("Redraw the seed more consistently or simplify unstable decorative details.")
    if "copyable_common_scene_risk" in warnings:
        recommendations.append("The scene is memorable but visually copyable; consider adding one private distinctive component.")
    if not recommendations and score >= 62:
        recommendations.append("Seed quality is acceptable for the current pilot; continue collecting verification attempts.")

    return {
        "quality_score": round(score, 3),
        "quality_score_0_1": round(score / 100.0, 4),
        "quality_label": quality_label(score, hard_reject=hard_reject),
        "hard_reject": hard_reject,
        "hard_reject_reasons": sorted(set(hard_reasons)),
        "warnings": warnings,
        "recommendations": recommendations,
        "feature_breakdown": {
            "stability": round(stability, 4),
            "token_complexity": round(token_score, 4),
            "geometry_complexity": round(geom_score, 4),
            "common_shape_risk": round(common_risk, 4),
            "visual_copyability_risk": round(common_risk, 4),
            "scene_stability": round(scene_stability, 4),
        },
        "features": {
            "token": token_features,
            "geometry": geom_features,
            "commonness": common_features,
        },
    }
