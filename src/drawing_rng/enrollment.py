from __future__ import annotations

from itertools import combinations
from statistics import median
from typing import Any, Dict, List

from .profiles import PROFILES
from .seed_derivation import avatar_palette, demo_password, new_public_salt, seed_hex
from .fuzzy_extractor import (
    enroll_fuzzy_secret,
    recover_fuzzy_secret,
    fuzzy_avatar_palette,
    fuzzy_demo_password,
    fuzzy_seed_hex,
)
from .similarity import similarity_report, token_bigram_jaccard, token_similarity, weighted_token_similarity
from .stroke_token_encoder import encode_json_payload
from .geometry_verifier import (
    compare_geometry,
    extract_geometry_signature,
    geometry_failure_reasons,
    geometry_thresholds,
)
from .seed_quality import evaluate_seed_quality
from .timing_features import learn_timing_model, compare_timing_model
from .scene_verifier import (
    compare_scene_model,
    compare_scene_signatures,
    extract_scene_signature,
    is_complex_scene_model,
    learn_scene_model,
    scene_failure_reasons,
    scene_signature_from_geometry,
    scene_thresholds,
)


def stability_label(score: float) -> str:
    if score < 0.25:
        return "unstable"
    if score < 0.40:
        return "weak_moderate"
    if score < 0.55:
        return "usable_for_demo"
    return "strong_redraw_stability"


def accepted_for_demo(score: float) -> bool:
    return score >= 0.40


def token_threshold_for_profile(profile: str) -> float:
    # Prototype verification thresholds. Tolerant mode strips more detail,
    # so it must require a higher token match.
    if profile == "strict":
        return 0.40
    if profile == "tolerant":
        return 0.65
    return 0.50


def _region_name(center: Any) -> str:
    try:
        x = float((center or [0.0, 0.0])[0])
        y = float((center or [0.0, 0.0])[1])
    except Exception:
        return "central"
    horiz = "left" if x < -0.18 else "right" if x > 0.18 else "center"
    vert = "upper" if y < -0.18 else "lower" if y > 0.18 else "middle"
    if horiz == "center" and vert == "middle":
        return "central"
    if horiz == "center":
        return vert
    if vert == "middle":
        return horiz
    return f"{vert}-{horiz}"


def _step_up_component_challenge(scene_model: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if not isinstance(scene_model, dict):
        return None
    canonical = scene_model.get("canonical_scene") or {}
    clusters = list(canonical.get("major_clusters") or [])
    if not clusters:
        return None
    # Prefer a smaller but still major component; it tends to be harder for a
    # screenshot copier to reproduce from memory and easier to describe by region.
    clusters.sort(key=lambda c: float(c.get("ink_fraction", 1.0)))
    chosen = clusters[0]
    region = _region_name(chosen.get("center"))
    return {
        "recommended": True,
        "kind": "component_redraw",
        "prompt": f"Redraw only the {region} component of your drawing seed.",
        "region": region,
        "cluster_id": chosen.get("cluster_id"),
        "reason": "borderline_or_suspicious_complex_match",
    }


def _direction_base(token: str) -> str | None:
    # Direction-run tokens look like E_L, NE_M, S_S, ENE_L, etc.
    if not isinstance(token, str) or "_" not in token:
        return None
    base = token.split("_", 1)[0]
    valid = {
        "E", "N", "W", "S",
        "NE", "NW", "SW", "SE",
        "ENE", "NNE", "NNW", "WNW", "WSW", "SSW", "SSE", "ESE",
    }
    return base if base in valid else None


def _unique_direction_token_count(tokens: List[str]) -> int:
    return len({b for b in (_direction_base(t) for t in tokens) if b})


def _minimum_complexity_failures(tokens: List[str]) -> List[str]:
    failures: List[str] = []
    if len(tokens) < 8:
        failures.append("very_short_token_sequence")
    if _unique_direction_token_count(tokens) < 3:
        failures.append("too_few_unique_directions")
    return failures


def _encode_attempts(attempts: List[Dict[str, Any]], profile: str) -> List[Dict[str, Any]]:
    params = PROFILES[profile]
    out = []
    for idx, attempt in enumerate(attempts, start=1):
        strokes = attempt.get("strokes", [])
        result = encode_json_payload({"strokes": strokes, "params": params})
        geometry = extract_geometry_signature(strokes, params)
        scene = extract_scene_signature(strokes, params)
        out.append({
            "attempt": idx,
            "profile": profile,
            "tokens": result.get("tokens") or [],
            "serialized": result.get("serialized") or "",
            "stats": result.get("stats") or {},
            "geometry": geometry,
            "scene": scene,
            "raw_strokes": strokes,
        })
    return out


def _pair_scores(encoded: List[Dict[str, Any]]) -> List[float]:
    return [token_similarity(a["tokens"], b["tokens"]) for a, b in combinations(encoded, 2)]


def _geometry_pair_scores(encoded: List[Dict[str, Any]]) -> List[float]:
    scores: List[float] = []
    for a, b in combinations(encoded, 2):
        g = compare_geometry(a.get("geometry") or {}, b.get("geometry") or {})
        scores.append(float(g.get("geometry_final", 0.0)))
    return scores


def _central_attempt(encoded: List[Dict[str, Any]]) -> Dict[str, Any]:
    if len(encoded) == 1:
        return encoded[0]
    scored = []
    for item in encoded:
        others = [x for x in encoded if x is not item]
        sims = [token_similarity(item["tokens"], x["tokens"]) for x in others]
        scored.append((median(sims) if sims else 0.0, item))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def analyze_enrollment(attempts: List[Dict[str, Any]], domain: str = "example.com", salt: str | None = None) -> Dict[str, Any]:
    if len(attempts) < 2:
        raise ValueError("Enrollment needs at least 2 attempts; 3 is recommended.")

    profile_results: Dict[str, Dict[str, Any]] = {}
    for profile in PROFILES:
        encoded = _encode_attempts(attempts, profile)
        pair_scores = _pair_scores(encoded)
        geometry_pair_scores = _geometry_pair_scores(encoded)
        scene_model_candidate = learn_scene_model(attempts, PROFILES[profile], central_attempt_index=1)
        scene_pair_scores = scene_model_candidate.get("scene_pair_scores") or []
        score = median(pair_scores) if pair_scores else 0.0
        geometry_score = median(geometry_pair_scores) if geometry_pair_scores else 0.0
        scene_score = median(scene_pair_scores) if scene_pair_scores else 0.0
        # Scene stability is only useful for genuinely complex scenes.  Simple
        # symbols should still be judged mostly by token + geometry stability.
        if scene_model_candidate.get("complexity_class") == "complex_scene":
            combined_score = 0.52 * score + 0.28 * geometry_score + 0.20 * scene_score
        else:
            combined_score = 0.65 * score + 0.35 * geometry_score
        profile_results[profile] = {
            "profile": profile,
            "stability_score": score,
            "geometry_stability_score": geometry_score,
            "combined_stability_score": combined_score,
            "pair_scores": pair_scores,
            "geometry_pair_scores": geometry_pair_scores,
            "scene_pair_scores": scene_pair_scores,
            "scene_stability_score": scene_score,
            "scene_model_candidate": scene_model_candidate,
            "encoded_attempts": encoded,
        }

    # Choose the profile that balances token repeatability with visual/geometry stability.
    # This prevents a profile from winning only because it over-simplifies the drawing.
    best_profile = max(profile_results, key=lambda p: profile_results[p]["combined_stability_score"])
    best = profile_results[best_profile]
    central = _central_attempt(best["encoded_attempts"])
    scene_model = learn_scene_model(attempts, PROFILES[best_profile], central_attempt_index=int(central.get("attempt", 1)))
    timing_model = learn_timing_model(attempts)
    score = float(best["stability_score"])
    label = stability_label(score)
    accepted = accepted_for_demo(score)

    warnings = []
    all_flags = []
    for attempt in best["encoded_attempts"]:
        all_flags.extend(attempt["stats"].get("weak_seed_flags") or [])
    for flag in sorted(set(all_flags)):
        warnings.append(flag)
    if not accepted:
        warnings.append("redraw_stability_below_demo_threshold")

    # Code-freeze minimum complexity gate. This prevents trivial graphical
    # passwords (dots, single straight lines, overly simple symbols) from ever
    # reaching the fuzzy/cryptographic pipeline as accepted enrollments.
    hard_reject_reasons = _minimum_complexity_failures(central["tokens"])
    warnings.extend(x for x in hard_reject_reasons if x not in warnings)

    if float(best.get("geometry_stability_score", 0.0)) < 0.45:
        warnings.append("low_geometry_stability")

    if scene_model.get("complexity_class") == "complex_scene":
        warnings.append("complex_scene_mode_enabled")
        if float(scene_model.get("scene_stability_score", 0.0)) < 0.50:
            warnings.append("low_scene_stability")

    seed_quality = evaluate_seed_quality(
        central["tokens"],
        central.get("geometry") or {},
        token_stability=score,
        geometry_stability=float(best.get("geometry_stability_score", 0.0)),
        scene_model=scene_model,
        minimum_complexity_failures=hard_reject_reasons,
    )
    if seed_quality.get("hard_reject"):
        accepted = False
        label = "rejected_low_seed_quality"
    for flag in seed_quality.get("warnings") or []:
        if flag not in warnings:
            warnings.append(flag)

    salt = salt or new_public_salt()
    seed_material = central["serialized"]

    # Prototype fuzzy extractor helper. This creates a random hidden secret and
    # masks it with a SimHash sketch of the canonical drawing profile. The
    # helper can later be used with a close redraw to recover the same secret.
    # The secret itself is used only to produce immediate demo outputs; it is
    # not included in fuzzy_helper.
    fuzzy_helper_full = enroll_fuzzy_secret(
        central["tokens"],
        central.get("geometry") or {},
        salt,
    )
    fuzzy_secret_hex = fuzzy_helper_full.pop("secret_hex_for_demo_only")

    return {
        "accepted_for_demo": accepted,
        "stability_score": score,
        "stability_label": label,
        "recommended_profile": best_profile,
        "attempt_count": len(attempts),
        "pair_scores": best["pair_scores"],
        "profile_scores": {p: profile_results[p]["stability_score"] for p in profile_results},
        "profile_geometry_scores": {p: profile_results[p]["geometry_stability_score"] for p in profile_results},
        "profile_combined_scores": {p: profile_results[p]["combined_stability_score"] for p in profile_results},
        "geometry_stability_score": best.get("geometry_stability_score", 0.0),
        "geometry_pair_scores": best.get("geometry_pair_scores", []),
        "scene_stability_score": scene_model.get("scene_stability_score", 0.0),
        "scene_pair_scores": scene_model.get("scene_pair_scores", []),
        "complexity_class": scene_model.get("complexity_class", "simple_symbol"),
        "scene_model": scene_model,
        "timing_model": timing_model,
        "timing_stability_score": timing_model.get("timing_stability_score"),
        "central_attempt": central["attempt"],
        "canonical_seed_material": seed_material,
        "canonical_tokens": central["tokens"],
        "canonical_geometry": central.get("geometry") or {},
        "canonical_token_count": len(central["tokens"]),
        "unique_direction_token_count": _unique_direction_token_count(central["tokens"]),
        "minimum_complexity_failures": hard_reject_reasons,
        "warnings": warnings,
        "seed_quality": seed_quality,
        "seed_quality_score": seed_quality.get("quality_score"),
        "seed_quality_label": seed_quality.get("quality_label"),
        "seed_quality_hard_reject": seed_quality.get("hard_reject"),
        "public_salt": salt,
        "fuzzy_enabled": True,
        "fuzzy_helper": fuzzy_helper_full,
        "fuzzy_note": (
            "Prototype fuzzy-commitment helper over a SimHash sketch. "
            "Useful for experiments, not production cryptography."
        ),
        "outputs": {
            "seed_hex": fuzzy_seed_hex(fuzzy_secret_hex, salt, "drawing-rng-master"),
            "demo_password": fuzzy_demo_password(fuzzy_secret_hex, salt, domain),
            "domain": domain,
            "avatar_palette": fuzzy_avatar_palette(fuzzy_secret_hex, salt),
            "source": "prototype_fuzzy_extractor",
        },
        "fallback_canonical_outputs": {
            "seed_hex": seed_hex(seed_material, salt, "drawing-rng-master"),
            "demo_password": demo_password(seed_material, salt, domain),
            "domain": domain,
            "avatar_palette": avatar_palette(seed_material, salt),
            "source": "canonical_seed_material",
        },
    }


def verify_redraw(
    enrollment_result: Dict[str, Any],
    redraw_strokes: List[Any],
    threshold: float | None = None,
    fuzzy_required: bool = False,
) -> Dict[str, Any]:
    """Verify a fresh redraw against an enrolled Drawing-RNG profile.

    Important design rule:
      The fresh redraw is used only for verification. If accepted, outputs are
      regenerated from the enrolled canonical_seed_material, not from the new
      redraw tokens. This avoids changing the password/seed when the redraw is
      slightly different but still close enough to unlock.
    """
    if not isinstance(enrollment_result, dict):
        raise ValueError("enrollment_result must be an object")
    if not isinstance(redraw_strokes, list) or not redraw_strokes:
        raise ValueError("redraw_strokes must be a non-empty list")

    canonical_tokens = enrollment_result.get("canonical_tokens")
    if not isinstance(canonical_tokens, list) or not canonical_tokens:
        raise ValueError(
            "enrollment_result is missing canonical_tokens. "
            "Re-run enrollment after updating analyze_enrollment()."
        )

    profile = enrollment_result.get("recommended_profile") or "balanced"
    if profile not in PROFILES:
        raise ValueError(f"Unknown enrolled profile: {profile}")

    if threshold is None:
        threshold = token_threshold_for_profile(profile)
    else:
        try:
            threshold = float(threshold)
        except (TypeError, ValueError):
            raise ValueError("threshold must be a number")
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be between 0.0 and 1.0")

    params = PROFILES[profile]
    redraw_encoded = encode_json_payload({
        "strokes": redraw_strokes,
        "params": params,
    })
    redraw_tokens = redraw_encoded.get("tokens") or []

    token_score = token_similarity(canonical_tokens, redraw_tokens)
    token_score_weighted = weighted_token_similarity(canonical_tokens, redraw_tokens)
    token_bigram_score = token_bigram_jaccard(canonical_tokens, redraw_tokens)

    canonical_geometry = enrollment_result.get("canonical_geometry") or {}
    redraw_geometry = extract_geometry_signature(redraw_strokes, params)
    geometry_scores = compare_geometry(canonical_geometry, redraw_geometry)
    geom_thresholds = geometry_thresholds(profile)
    geom_failures = geometry_failure_reasons(geometry_scores, profile)

    scene_model = enrollment_result.get("scene_model") if isinstance(enrollment_result.get("scene_model"), dict) else None
    if scene_model and isinstance(scene_model.get("canonical_scene"), dict):
        canonical_scene = scene_model.get("canonical_scene") or {}
    else:
        canonical_scene = scene_signature_from_geometry(canonical_geometry)
        scene_model = {
            "complexity_class": canonical_scene.get("complexity_class", "simple_symbol"),
            "canonical_scene": canonical_scene,
            "reference_scenes": [canonical_scene],
            "legacy_scene_model": True,
        }
    redraw_scene = extract_scene_signature(redraw_strokes, params)
    scene_scores = compare_scene_model(scene_model or {}, redraw_scene)
    timing_scores = compare_timing_model(enrollment_result.get("timing_model") if isinstance(enrollment_result.get("timing_model"), dict) else None, redraw_strokes)
    scene_threshold_map = scene_thresholds(profile)
    scene_failures = scene_failure_reasons(scene_scores, profile)
    complex_scene_mode = is_complex_scene_model(scene_model, canonical_geometry)

    # Combined score is useful for UI/ranking, while hard gates stop cases where
    # token similarity is high but the visual layout/curvature is wrong.
    geometry_final = float(geometry_scores.get("geometry_final", 0.0))
    final_score = max(0.0, min(1.0, 0.55 * token_score + 0.45 * geometry_final))

    token_pass = token_score >= threshold
    geometry_pass = not geom_failures
    diagnostic_high_score = final_score > 0.80

    # Complex-scene mode deliberately changes the abstraction, but it must not
    # become an informed-forgery bypass.  Phase 2.10 made this path too lenient:
    # a low raster match or weak layout could still pass if cluster assignment
    # looked good.  Phase 2.11 keeps the macro-scene rescue but adds three guard
    # rails:
    #   1. token similarity can be softened only a little,
    #   2. scene/raster/assignment thresholds are substantially higher, and
    #   3. the old geometry verifier must clear a low sanity floor for layout,
    #      relation, and topology.
    complex_token_threshold = max(0.58, threshold - 0.07 if threshold >= 0.62 else threshold)
    complex_token_primary_pass = token_score >= complex_token_threshold
    layout_score = float(geometry_scores.get("layout", 0.0))
    relation_score = float(geometry_scores.get("relation", 0.0))
    topology_score = float(geometry_scores.get("topology", 0.0))
    closed_style_score = float(geometry_scores.get("closed_style", 0.0)) if geometry_scores.get("closed_style") is not None else 1.0
    scene_final_score = float(scene_scores.get("scene_final", 0.0))
    scene_assignment_score = float(scene_scores.get("scene_assignment", 0.0))
    scene_raster_score = float(scene_scores.get("scene_raster", 0.0))

    # Phase 2.12: add a narrow owner-recovery band for complex scenes.
    # The previous 2.11 patch fixed informed-forgery leniency, but it also
    # false-rejected legitimate redraws of stable scenes when the token score was
    # only slightly below 0.58 or the clusterer collapsed one macro part.  The
    # recovery band is intentionally narrow: it requires strong independent
    # geometry evidence, a decent final diagnostic score, and a scene match that
    # is not obviously poor.  This rescues owner redraws like sun+boat+person
    # without returning to the very permissive 2.10 behavior.
    complex_owner_recovery_pass = (
        token_score >= 0.53
        and final_score >= 0.56
        and layout_score >= 0.68
        and relation_score >= 0.62
        and topology_score >= 0.60
        and closed_style_score >= 0.58
        and (scene_final_score >= 0.60 or scene_assignment_score >= 0.62 or scene_raster_score >= 0.56)
    )
    complex_token_pass = complex_token_primary_pass or complex_owner_recovery_pass

    scene_pass = not scene_failures
    if complex_owner_recovery_pass and scene_failures:
        # In the recovery band, ignore only known clustering artifacts.  Keep
        # real low scene-score, low raster, and multi-part missing/new failures.
        recoverable_scene_failures = {
            "complex_scene_cluster_collapse",
            "required_major_part_missing",
        }
        scene_failures = [f for f in scene_failures if f not in recoverable_scene_failures]
        scene_pass = not scene_failures

    complex_geometry_floor_pass = (
        layout_score >= 0.50
        and relation_score >= 0.50
        and topology_score >= 0.48
    )

    if complex_scene_mode:
        accepted = complex_token_pass and scene_pass and complex_geometry_floor_pass
    else:
        # Security decision must remain an all-or-nothing hard gate. The previous
        # high-confidence override accepted redraws based on blended final_score
        # even when geometry gates had failed, which invalidated near-miss/FAR
        # interpretation and contradicted the documented architecture.
        accepted = token_pass and geometry_pass

    failure_reasons: List[str] = []
    overridden_failure_reasons: List[str] = []
    if complex_scene_mode:
        if not complex_token_pass:
            failure_reasons.append(f"complex_token_score_below_{complex_token_threshold:.2f}")
        elif complex_owner_recovery_pass and not complex_token_primary_pass:
            overridden_failure_reasons.append("complex_owner_recovery_band_used")
        failure_reasons.extend(scene_failures)
        if not complex_geometry_floor_pass:
            if layout_score < 0.50:
                failure_reasons.append("complex_layout_floor_below_0.50")
            if relation_score < 0.50:
                failure_reasons.append("complex_relation_floor_below_0.50")
            if topology_score < 0.48:
                failure_reasons.append("complex_topology_floor_below_0.48")
        # Keep strict geometry failures as diagnostics for UI/research, but do
        # not hard-fail complex scenes solely because micro-stroke count/pairing
        # changed.
    else:
        if not token_pass:
            failure_reasons.append(f"token_score_below_{threshold:.2f}")
        failure_reasons.extend(geom_failures)

    seed_material = enrollment_result.get("canonical_seed_material") or ""
    salt = enrollment_result.get("public_salt") or ""
    domain = (
        enrollment_result.get("domain")
        or (enrollment_result.get("outputs") or {}).get("domain")
        or "example.com"
    )

    fuzzy_recovery = None
    outputs = None
    output_source = None

    if accepted and isinstance(enrollment_result.get("fuzzy_helper"), dict):
        fuzzy_recovery = recover_fuzzy_secret(
            redraw_tokens,
            redraw_geometry,
            salt,
            enrollment_result.get("fuzzy_helper") or {},
        )
        if fuzzy_recovery.get("ok"):
            recovered_secret = fuzzy_recovery.get("secret_hex")
            outputs = {
                "seed_hex": fuzzy_seed_hex(recovered_secret, salt, "drawing-rng-master"),
                "demo_password": fuzzy_demo_password(recovered_secret, salt, domain),
                "domain": domain,
                "avatar_palette": fuzzy_avatar_palette(recovered_secret, salt),
                "source": "prototype_fuzzy_extractor",
            }
            output_source = "prototype_fuzzy_extractor"
        elif fuzzy_required:
            accepted = False
            failure_reasons.append("fuzzy_secret_recovery_failed")

    # During early experiments, fall back to canonical seed material if fuzzy
    # recovery fails but the redraw verifier itself accepted. This keeps the
    # vault demo usable while exposing fuzzy_recovery.ok in the JSON so we can
    # tune the extractor. Set fuzzy_required=True later to hard-enforce it.
    if accepted and outputs is None:
        outputs = {
            "seed_hex": seed_hex(seed_material, salt, "drawing-rng-master"),
            "demo_password": demo_password(seed_material, salt, domain),
            "domain": domain,
            "avatar_palette": avatar_palette(seed_material, salt),
            "source": "canonical_seed_material_fallback",
        }
        output_source = "canonical_seed_material_fallback"

    suspicious_accept = bool(
        accepted
        and complex_scene_mode
        and (
            complex_owner_recovery_pass
            or token_score < max(threshold, 0.62)
            or (fuzzy_recovery is not None and not bool(fuzzy_recovery.get("ok")))
        )
    )
    borderline_reject = bool(
        (not accepted)
        and complex_scene_mode
        and final_score >= 0.52
        and layout_score >= 0.55
        and relation_score >= 0.55
    )
    step_up_challenge = _step_up_component_challenge(scene_model) if (suspicious_accept or borderline_reject) else None
    if step_up_challenge:
        step_up_challenge["trigger"] = "suspicious_accept" if suspicious_accept else "borderline_reject"

    # Full step-up mode: a suspicious complex-scene accept is not allowed to
    # reveal outputs immediately.  It becomes an intermediate state that must
    # be cleared by verify_step_up_component().  This directly targets the
    # screenshot/informed-forgery issue without making honest complex redraws
    # fail outright.
    primary_accepted = bool(accepted)
    step_up_required = bool(step_up_challenge and (suspicious_accept or borderline_reject))
    if step_up_required:
        accepted = False
        outputs = None
        output_source = None
        if "step_up_component_required" not in failure_reasons:
            failure_reasons.append("step_up_component_required")

    return {
        "accepted": accepted,
        "score": final_score,
        "final_score": final_score,
        "high_confidence_override": False,
        "diagnostic_high_score": diagnostic_high_score,
        "high_confidence_threshold": 0.80,
        "overridden_failure_reasons": overridden_failure_reasons,
        "suspicious_accept": suspicious_accept,
        "borderline_reject": borderline_reject,
        "primary_accepted": primary_accepted,
        "step_up_required": step_up_required,
        "step_up_challenge": step_up_challenge,
        "token_score": token_score,
        "token_score_weighted": token_score_weighted,
        "token_bigram_score": token_bigram_score,
        "threshold": threshold,
        "token_threshold": threshold,
        "profile": profile,
        "domain": domain,
        "outputs": outputs,
        "output_source": output_source,
        "fuzzy_required": bool(fuzzy_required),
        "fuzzy_recovery": fuzzy_recovery,
        "timing_scores": timing_scores,
        "failure_reasons": failure_reasons,
        "geometry_scores": geometry_scores,
        "geometry_thresholds": geom_thresholds,
        "geometry_failure_reasons_diagnostic": geom_failures,
        "complex_scene_mode": bool(complex_scene_mode),
        "complex_token_threshold": complex_token_threshold,
        "complex_token_primary_pass": bool(complex_token_primary_pass) if complex_scene_mode else None,
        "complex_token_pass": bool(complex_token_pass),
        "complex_owner_recovery_pass": bool(complex_owner_recovery_pass) if complex_scene_mode else None,
        "complex_geometry_floor_pass": bool(complex_geometry_floor_pass) if complex_scene_mode else None,
        "complex_geometry_floor_thresholds": {
            "layout": 0.50,
            "relation": 0.50,
            "topology": 0.48,
        } if complex_scene_mode else None,
        "scene_scores": scene_scores,
        "scene_thresholds": scene_threshold_map,
        "scene_failure_reasons": scene_failures,
        "scene_pass": bool(scene_pass),
        "scene_model_summary": {
            "complexity_class": scene_model.get("complexity_class"),
            "scene_stability_score": scene_model.get("scene_stability_score"),
            "legacy_scene_model": bool(scene_model.get("legacy_scene_model")),
            "reference_scene_count": scene_scores.get("reference_scene_count"),
            "best_reference_index": scene_scores.get("best_reference_index"),
            "canonical_major_cluster_count": scene_scores.get("canonical_major_cluster_count"),
            "redraw_major_cluster_count": scene_scores.get("redraw_major_cluster_count"),
        },
        "canonical_token_count": len(canonical_tokens),
        "redraw_token_count": len(redraw_tokens),
        "similarity_report": similarity_report(canonical_tokens, redraw_tokens),
        "redraw_tokens": redraw_tokens,
        "redraw_serialized": redraw_encoded.get("serialized"),
        "redraw_stats": redraw_encoded.get("stats") or {},
        "redraw_geometry": redraw_geometry,
    }



def _cluster_by_id(scene_model: Dict[str, Any] | None, cluster_id: Any) -> Dict[str, Any] | None:
    if not isinstance(scene_model, dict):
        return None
    canonical = scene_model.get("canonical_scene") or {}
    for c in canonical.get("major_clusters") or []:
        if str(c.get("cluster_id")) == str(cluster_id):
            return c
    clusters = list(canonical.get("major_clusters") or [])
    return clusters[0] if clusters else None


def _strokes_for_cluster_from_geometry(canonical_geometry: Dict[str, Any], cluster: Dict[str, Any] | None) -> List[Any]:
    comps = list((canonical_geometry or {}).get("components") or [])
    if not comps:
        return []
    indices = []
    if isinstance(cluster, dict):
        indices = [int(i) for i in (cluster.get("stroke_indices") or []) if str(i).lstrip("-").isdigit()]
    if not indices:
        # Fallback: pick the component whose center is closest to the cluster center.
        if isinstance(cluster, dict) and cluster.get("center"):
            try:
                cx, cy = float(cluster["center"][0]), float(cluster["center"][1])
                def cd(comp: Dict[str, Any]) -> float:
                    box = comp.get("bbox") or [0, 0, 0, 0]
                    mx = (float(box[0]) + float(box[2])) / 2.0
                    my = (float(box[1]) + float(box[3])) / 2.0
                    return (mx - cx) ** 2 + (my - cy) ** 2
                best = min(range(len(comps)), key=lambda i: cd(comps[i]))
                indices = [best]
            except Exception:
                indices = [0]
        else:
            indices = [0]
    strokes = []
    for idx in indices:
        if 0 <= idx < len(comps):
            pts = comps[idx].get("points_norm") or comps[idx].get("points_global") or comps[idx].get("points_local") or []
            if pts:
                strokes.append([[float(p[0]), float(p[1])] for p in pts])
    return strokes


def _component_closed_ok(target_cluster: Dict[str, Any] | None, candidate_geometry: Dict[str, Any], geometry_scores: Dict[str, Any]) -> bool:
    if not isinstance(target_cluster, dict) or not bool(target_cluster.get("has_closed")):
        return True
    comps = candidate_geometry.get("components") or []
    candidate_has_closed = any(bool(c.get("closed")) for c in comps)
    closed_style = geometry_scores.get("closed_style")
    if candidate_has_closed:
        return True
    try:
        return float(closed_style) >= 0.52
    except Exception:
        return False


def verify_step_up_component(
    enrollment_result: Dict[str, Any],
    challenge: Dict[str, Any],
    component_strokes: List[Any],
    initial_result: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Verify the second-stage component redraw challenge.

    The first-stage redraw must be borderline/suspicious and must have produced a
    step_up_challenge.  This function compares the submitted component against
    the corresponding canonical scene cluster and reveals outputs only if the
    component also matches.
    """
    if not isinstance(enrollment_result, dict):
        raise ValueError("enrollment_result must be an object")
    if not isinstance(challenge, dict):
        raise ValueError("challenge must be an object")
    if not isinstance(component_strokes, list) or not component_strokes:
        raise ValueError("component_strokes must be a non-empty list")

    profile = enrollment_result.get("recommended_profile") or "balanced"
    if profile not in PROFILES:
        profile = "balanced"
    params = dict(PROFILES[profile])
    # Component targets may be reconstructed from already-normalized canonical
    # geometry, so use lower raw-length filters for this isolated challenge.
    params["min_raw_stroke_length"] = min(float(params.get("min_raw_stroke_length", 5.0)), 0.0001)
    params["min_normalized_stroke_length"] = min(float(params.get("min_normalized_stroke_length", 0.02)), 0.005)
    scene_model = enrollment_result.get("scene_model") if isinstance(enrollment_result.get("scene_model"), dict) else None
    canonical_geometry = enrollment_result.get("canonical_geometry") or {}
    cluster = _cluster_by_id(scene_model, challenge.get("cluster_id"))
    target_strokes = _strokes_for_cluster_from_geometry(canonical_geometry, cluster)
    if not target_strokes:
        raise ValueError("Could not reconstruct target component from enrollment. Re-enroll with the current build.")

    target_encoded = encode_json_payload({"strokes": target_strokes, "params": params})
    candidate_encoded = encode_json_payload({"strokes": component_strokes, "params": params})
    target_tokens = target_encoded.get("tokens") or []
    candidate_tokens = candidate_encoded.get("tokens") or []

    token_score = token_similarity(target_tokens, candidate_tokens)
    weighted_score = weighted_token_similarity(target_tokens, candidate_tokens)
    bigram_score = token_bigram_jaccard(target_tokens, candidate_tokens)
    target_geometry = extract_geometry_signature(target_strokes, params)
    candidate_geometry = extract_geometry_signature(component_strokes, params)
    geometry_scores = compare_geometry(target_geometry, candidate_geometry)
    geometry_final = float(geometry_scores.get("geometry_final", 0.0))
    target_scene = extract_scene_signature(target_strokes, params)
    candidate_scene = extract_scene_signature(component_strokes, params)
    scene_scores = compare_scene_signatures(target_scene, candidate_scene)
    raster_score = float(scene_scores.get("scene_raster", 0.0))

    component_score = max(0.0, min(1.0,
        0.32 * weighted_score
        + 0.18 * token_score
        + 0.16 * bigram_score
        + 0.20 * geometry_final
        + 0.14 * raster_score
    ))
    closed_ok = _component_closed_ok(cluster, candidate_geometry, geometry_scores)
    initial_step_up = bool((initial_result or {}).get("step_up_required") or (initial_result or {}).get("step_up_challenge"))
    # In dev/public local demo mode the client carries the enrollment object, so
    # this is a verifier workflow guard, not a cryptographic authorization check.
    accepted = bool(
        initial_step_up
        and component_score >= 0.56
        and weighted_score >= 0.44
        and (geometry_final >= 0.42 or raster_score >= 0.46)
        and closed_ok
    )

    seed_material = enrollment_result.get("canonical_seed_material") or ""
    salt = enrollment_result.get("public_salt") or ""
    domain = (enrollment_result.get("domain") or (enrollment_result.get("outputs") or {}).get("domain") or "example.com")
    outputs = None
    output_source = None
    if accepted:
        outputs = {
            "seed_hex": seed_hex(seed_material, salt, "drawing-rng-master"),
            "demo_password": demo_password(seed_material, salt, domain),
            "domain": domain,
            "avatar_palette": avatar_palette(seed_material, salt),
            "source": "canonical_seed_material_after_step_up",
        }
        output_source = "canonical_seed_material_after_step_up"

    failure_reasons: List[str] = []
    if not initial_step_up:
        failure_reasons.append("initial_redraw_did_not_request_step_up")
    if component_score < 0.56:
        failure_reasons.append("component_score_below_0.56")
    if weighted_score < 0.44:
        failure_reasons.append("component_weighted_token_below_0.44")
    if geometry_final < 0.42 and raster_score < 0.46:
        failure_reasons.append("component_geometry_and_raster_below_floor")
    if not closed_ok:
        failure_reasons.append("component_closed_style_mismatch")

    return {
        "accepted": accepted,
        "step_up_passed": accepted,
        "step_up_required": False,
        "component_score": component_score,
        "component_token_score": token_score,
        "component_weighted_token_score": weighted_score,
        "component_bigram_score": bigram_score,
        "component_geometry_final": geometry_final,
        "component_raster_score": raster_score,
        "component_closed_ok": closed_ok,
        "challenge": {k: v for k, v in challenge.items() if k not in {"target_strokes", "target_tokens"}},
        "target_token_count": len(target_tokens),
        "candidate_token_count": len(candidate_tokens),
        "geometry_scores": geometry_scores,
        "scene_scores": scene_scores,
        "timing_scores": compare_timing_model(enrollment_result.get("timing_model") if isinstance(enrollment_result.get("timing_model"), dict) else None, component_strokes),
        "failure_reasons": failure_reasons,
        "outputs": outputs,
        "output_source": output_source,
        "domain": domain,
        "profile": profile,
        "redraw_strokes": component_strokes,
    }
