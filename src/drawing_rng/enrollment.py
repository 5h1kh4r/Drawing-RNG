from __future__ import annotations

from itertools import combinations
from statistics import median
from typing import Any, Dict, List

from .profiles import PROFILES
from .seed_derivation import avatar_palette, demo_password, new_public_salt, seed_hex
from .similarity import similarity_report, token_similarity
from .stroke_token_encoder import encode_json_payload


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


def _encode_attempts(attempts: List[Dict[str, Any]], profile: str) -> List[Dict[str, Any]]:
    params = PROFILES[profile]
    out = []
    for idx, attempt in enumerate(attempts, start=1):
        result = encode_json_payload({"strokes": attempt.get("strokes", []), "params": params})
        out.append({
            "attempt": idx,
            "profile": profile,
            "tokens": result.get("tokens") or [],
            "serialized": result.get("serialized") or "",
            "stats": result.get("stats") or {},
        })
    return out


def _pair_scores(encoded: List[Dict[str, Any]]) -> List[float]:
    return [token_similarity(a["tokens"], b["tokens"]) for a, b in combinations(encoded, 2)]


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
        score = median(pair_scores) if pair_scores else 0.0
        profile_results[profile] = {
            "profile": profile,
            "stability_score": score,
            "pair_scores": pair_scores,
            "encoded_attempts": encoded,
        }

    best_profile = max(profile_results, key=lambda p: profile_results[p]["stability_score"])
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
    if len(central["tokens"]) < 8:
        warnings.append("very_short_token_sequence")

    salt = salt or new_public_salt()
    seed_material = central["serialized"]
    return {
        "accepted_for_demo": accepted,
        "stability_score": score,
        "stability_label": label,
        "recommended_profile": best_profile,
        "attempt_count": len(attempts),
        "pair_scores": best["pair_scores"],
        "profile_scores": {p: profile_results[p]["stability_score"] for p in profile_results},
        "central_attempt": central["attempt"],
        "canonical_seed_material": seed_material,
        "canonical_tokens": central["tokens"],
        "canonical_token_count": len(central["tokens"]),
        "warnings": warnings,
        "public_salt": salt,
        "outputs": {
            "seed_hex": seed_hex(seed_material, salt, "drawing-rng-master"),
            "demo_password": demo_password(seed_material, salt, domain),
            "domain": domain,
            "avatar_palette": avatar_palette(seed_material, salt),
        },
    }


def verify_redraw(
    enrollment_result: Dict[str, Any],
    redraw_strokes: List[Any],
    threshold: float = 0.50,
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

    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        raise ValueError("threshold must be a number")
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0.0 and 1.0")

    redraw_encoded = encode_json_payload({
        "strokes": redraw_strokes,
        "params": PROFILES[profile],
    })
    redraw_tokens = redraw_encoded.get("tokens") or []

    score = token_similarity(canonical_tokens, redraw_tokens)
    accepted = score >= threshold

    seed_material = enrollment_result.get("canonical_seed_material") or ""
    salt = enrollment_result.get("public_salt") or ""
    domain = (
        enrollment_result.get("domain")
        or (enrollment_result.get("outputs") or {}).get("domain")
        or "example.com"
    )

    outputs = None
    if accepted:
        outputs = {
            "seed_hex": seed_hex(seed_material, salt, "drawing-rng-master"),
            "demo_password": demo_password(seed_material, salt, domain),
            "domain": domain,
            "avatar_palette": avatar_palette(seed_material, salt),
        }

    return {
        "accepted": accepted,
        "score": score,
        "threshold": threshold,
        "profile": profile,
        "domain": domain,
        "outputs": outputs,
        "canonical_token_count": len(canonical_tokens),
        "redraw_token_count": len(redraw_tokens),
        "similarity_report": similarity_report(canonical_tokens, redraw_tokens),
        "redraw_tokens": redraw_tokens,
        "redraw_serialized": redraw_encoded.get("serialized"),
        "redraw_stats": redraw_encoded.get("stats") or {},
    }
