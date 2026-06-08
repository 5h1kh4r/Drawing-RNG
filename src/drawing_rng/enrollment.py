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
from .similarity import similarity_report, token_similarity
from .stroke_token_encoder import encode_json_payload
from .geometry_verifier import (
    compare_geometry,
    extract_geometry_signature,
    geometry_failure_reasons,
    geometry_thresholds,
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
        out.append({
            "attempt": idx,
            "profile": profile,
            "tokens": result.get("tokens") or [],
            "serialized": result.get("serialized") or "",
            "stats": result.get("stats") or {},
            "geometry": geometry,
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
        score = median(pair_scores) if pair_scores else 0.0
        geometry_score = median(geometry_pair_scores) if geometry_pair_scores else 0.0
        combined_score = 0.65 * score + 0.35 * geometry_score
        profile_results[profile] = {
            "profile": profile,
            "stability_score": score,
            "geometry_stability_score": geometry_score,
            "combined_stability_score": combined_score,
            "pair_scores": pair_scores,
            "geometry_pair_scores": geometry_pair_scores,
            "encoded_attempts": encoded,
        }

    # Choose the profile that balances token repeatability with visual/geometry stability.
    # This prevents a profile from winning only because it over-simplifies the drawing.
    best_profile = max(profile_results, key=lambda p: profile_results[p]["combined_stability_score"])
    best = profile_results[best_profile]
    central = _central_attempt(best["encoded_attempts"])
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

    if hard_reject_reasons:
        accepted = False
        label = "rejected_low_complexity"

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
        "central_attempt": central["attempt"],
        "canonical_seed_material": seed_material,
        "canonical_tokens": central["tokens"],
        "canonical_geometry": central.get("geometry") or {},
        "canonical_token_count": len(central["tokens"]),
        "unique_direction_token_count": _unique_direction_token_count(central["tokens"]),
        "minimum_complexity_failures": hard_reject_reasons,
        "warnings": warnings,
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

    canonical_geometry = enrollment_result.get("canonical_geometry") or {}
    redraw_geometry = extract_geometry_signature(redraw_strokes, params)
    geometry_scores = compare_geometry(canonical_geometry, redraw_geometry)
    geom_thresholds = geometry_thresholds(profile)
    geom_failures = geometry_failure_reasons(geometry_scores, profile)

    # Combined score is useful for UI/ranking, while hard gates stop cases where
    # token similarity is high but the visual layout/curvature is wrong.
    geometry_final = float(geometry_scores.get("geometry_final", 0.0))
    final_score = max(0.0, min(1.0, 0.55 * token_score + 0.45 * geometry_final))

    token_pass = token_score >= threshold
    geometry_pass = not geom_failures
    accepted = token_pass and geometry_pass

    failure_reasons: List[str] = []
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

    return {
        "accepted": accepted,
        "score": final_score,
        "final_score": final_score,
        "token_score": token_score,
        "threshold": threshold,
        "token_threshold": threshold,
        "profile": profile,
        "domain": domain,
        "outputs": outputs,
        "output_source": output_source,
        "fuzzy_required": bool(fuzzy_required),
        "fuzzy_recovery": fuzzy_recovery,
        "failure_reasons": failure_reasons,
        "geometry_scores": geometry_scores,
        "geometry_thresholds": geom_thresholds,
        "canonical_token_count": len(canonical_tokens),
        "redraw_token_count": len(redraw_tokens),
        "similarity_report": similarity_report(canonical_tokens, redraw_tokens),
        "redraw_tokens": redraw_tokens,
        "redraw_serialized": redraw_encoded.get("serialized"),
        "redraw_stats": redraw_encoded.get("stats") or {},
        "redraw_geometry": redraw_geometry,
    }
