from __future__ import annotations

from typing import Any, Dict, List

from .enrollment import analyze_enrollment as _base_analyze_enrollment
from .enrollment import verify_redraw as _base_verify_redraw
from .hough_style import learn_hough_model, verify_hough_style
from .seed_derivation import avatar_palette, demo_password, seed_hex
from .shape_lock_baseline import learn_shape_lock_baseline, verify_shape_lock_baseline
from .structural_signature import (
    compare_structural_signatures,
    extract_structural_signature,
    learn_structural_model,
)

V22_ALGORITHM_REVISION = "draw2seed-v2.2.7-hough-residual"
V22_CONFIG_REVISION = "v2.2.7-hough-residual-2026-08-01"


def _attempt_strokes(attempt: Any) -> List[Any]:
    if isinstance(attempt, dict):
        strokes = attempt.get("strokes")
        return strokes if isinstance(strokes, list) else []
    return attempt if isinstance(attempt, list) else []


def analyze_enrollment_v22(
    attempts: List[Dict[str, Any]],
    domain: str = "example.com",
    salt: str | None = None,
) -> Dict[str, Any]:
    result = _base_analyze_enrollment(attempts=attempts, domain=domain, salt=salt)
    structural_model = learn_structural_model(attempts)
    shape_lock_model = learn_shape_lock_baseline(attempts)
    hough_model = learn_hough_model(attempts)
    signatures = structural_model.get("reference_signatures") or []
    templates = shape_lock_model.get("templates") or []

    references = result.get("verification_references") or []
    for index, reference in enumerate(references):
        if not isinstance(reference, dict):
            continue
        attempt_index = int(reference.get("attempt") or index + 1) - 1
        if 0 <= attempt_index < len(attempts):
            reference["raw_strokes"] = _attempt_strokes(attempts[attempt_index])
        if 0 <= attempt_index < len(signatures):
            reference["structural_signature"] = signatures[attempt_index]
        if 0 <= attempt_index < len(templates):
            reference["shape_lock_template"] = templates[attempt_index]

    result["verification_references"] = references
    result["structural_model"] = structural_model
    result["shape_lock_baseline"] = shape_lock_model
    result["hough_style_model"] = hough_model
    result["algorithm_revision"] = V22_ALGORITHM_REVISION
    result["config_revision"] = V22_CONFIG_REVISION

    warnings = list(result.get("warnings") or [])
    if not bool(structural_model.get("stable")):
        warnings.append("structural_consensus_unstable_diagnostic_only")
    if not bool(hough_model.get("applicable")):
        warnings.append("hough_style_not_applicable")
    result["warnings"] = list(dict.fromkeys(warnings))
    return result


def _reference_for_attempt(enrollment_result: Dict[str, Any], attempt_number: Any) -> Dict[str, Any] | None:
    references = enrollment_result.get("verification_references") or []
    for reference in references:
        if not isinstance(reference, dict):
            continue
        if str(reference.get("attempt")) == str(attempt_number):
            return reference
    return references[0] if references and isinstance(references[0], dict) else None


def _restore_primary_outputs(enrollment_result: Dict[str, Any], result: Dict[str, Any]) -> None:
    seed_material = enrollment_result.get("canonical_seed_material") or ""
    if not seed_material or seed_material == "[redacted]":
        return
    salt = enrollment_result.get("public_salt") or ""
    domain = (
        enrollment_result.get("domain")
        or (enrollment_result.get("outputs") or {}).get("domain")
        or "example.com"
    )
    result["outputs"] = {
        "seed_hex": seed_hex(seed_material, salt, "drawing-rng-master"),
        "demo_password": demo_password(seed_material, salt, domain),
        "domain": domain,
        "avatar_palette": avatar_palette(seed_material, salt),
        "source": "canonical_seed_material_v227_no_step_up",
    }
    result["output_source"] = "canonical_seed_material_v227_no_step_up"


def verify_redraw_v22(
    enrollment_result: Dict[str, Any],
    redraw_strokes: List[Any],
    threshold: float | None = None,
    fuzzy_required: bool = False,
) -> Dict[str, Any]:
    result = _base_verify_redraw(
        enrollment_result=enrollment_result,
        redraw_strokes=redraw_strokes,
        threshold=threshold,
        fuzzy_required=fuzzy_required,
    )

    # The collection build has no component step-up flow. Remove stale diagnostic
    # reasons unconditionally, then restore a primary accept if the old verifier
    # only withheld it for step-up.
    step_up_was_required = bool(result.get("step_up_required"))
    result["failure_reasons"] = [
        reason
        for reason in (result.get("failure_reasons") or [])
        if reason != "step_up_component_required"
    ]
    if step_up_was_required and bool(result.get("primary_accepted")):
        result["accepted"] = True
        _restore_primary_outputs(enrollment_result, result)
    result["step_up_required"] = False
    result["step_up_challenge"] = None

    structural_model = enrollment_result.get("structural_model") or {}
    candidate_signature = extract_structural_signature(redraw_strokes)
    selected_reference = _reference_for_attempt(
        enrollment_result,
        result.get("selected_reference_attempt"),
    )
    reference_signature = (selected_reference or {}).get("structural_signature")
    if not isinstance(reference_signature, dict):
        reference_signature = extract_structural_signature((selected_reference or {}).get("raw_strokes"))

    structural_comparison = compare_structural_signatures(
        reference_signature if isinstance(reference_signature, dict) else {},
        candidate_signature,
    )

    reference_comparisons: List[Dict[str, Any]] = []
    for reference in enrollment_result.get("verification_references") or []:
        if not isinstance(reference, dict):
            continue
        signature = reference.get("structural_signature")
        if not isinstance(signature, dict):
            signature = extract_structural_signature(reference.get("raw_strokes"))
        comparison = compare_structural_signatures(signature, candidate_signature)
        reference_comparisons.append({
            "attempt": reference.get("attempt"),
            "score": comparison.get("score"),
            "hard_pass": comparison.get("hard_pass"),
            "failure_reasons": comparison.get("failure_reasons") or [],
        })

    support_scores = sorted(
        (float(item.get("score") or 0.0) for item in reference_comparisons),
        reverse=True,
    )
    second_support = support_scores[1] if len(support_scores) >= 2 else (support_scores[0] if support_scores else 0.0)

    stable_enrollment_structure = bool(structural_model.get("stable"))
    multi_stroke = int(candidate_signature.get("stroke_count") or 0) >= 2
    simple_symbol = not bool(result.get("complex_scene_mode"))
    structural_gate_applicable = stable_enrollment_structure and multi_stroke and simple_symbol
    structural_score = float(structural_comparison.get("score") or 0.0)
    structural_gate_pass = bool(
        (not structural_gate_applicable)
        or (
            bool(structural_comparison.get("hard_pass"))
            and structural_score >= 0.68
            and (len(reference_comparisons) < 2 or second_support >= 0.60)
        )
    )

    pre_structural_accepted = bool(result.get("accepted"))
    if pre_structural_accepted and not structural_gate_pass:
        result["accepted"] = False
        result["outputs"] = None
        result["output_source"] = None
        failure_reasons = list(result.get("failure_reasons") or [])
        failure_reasons.extend(structural_comparison.get("failure_reasons") or [])
        if structural_score < 0.68:
            failure_reasons.append("structural_score_below_0.68")
        if len(reference_comparisons) >= 2 and second_support < 0.60:
            failure_reasons.append("structural_second_reference_support_below_0.60")
        result["failure_reasons"] = list(dict.fromkeys(failure_reasons))

    hough_hard_gate_enabled = not bool(result.get("complex_scene_mode"))
    hough = verify_hough_style(
        redraw_strokes,
        enrollment_result.get("hough_style_model") or {},
        hard_gate_enabled=hough_hard_gate_enabled,
    )
    pre_hough_accepted = bool(result.get("accepted"))
    if pre_hough_accepted and bool(hough.get("hard_gate_enabled")) and not bool(hough.get("pass")):
        result["accepted"] = False
        result["outputs"] = None
        result["output_source"] = None
        failure_reasons = list(result.get("failure_reasons") or [])
        failure_reasons.append("hough_line_style_changed")
        result["failure_reasons"] = list(dict.fromkeys(failure_reasons))

    baseline = verify_shape_lock_baseline(
        redraw_strokes,
        enrollment_result.get("shape_lock_baseline") or {},
    )

    geometry_scores = result.get("geometry_scores")
    if not isinstance(geometry_scores, dict):
        geometry_scores = {}
    model = enrollment_result.get("hough_style_model") or {}
    candidate_hough = hough.get("candidate_signature") or {}
    geometry_scores.update({
        "hough_style_score": hough.get("score"),
        "hough_style_applicable": hough.get("applicable"),
        "hough_style_pass": hough.get("pass"),
        "hough_style_raw_pass": hough.get("raw_pass"),
        "hough_style_violation_count": hough.get("violation_count"),
        "hough_style_strong_violation_count": hough.get("strong_violation_count"),
        "hough_style_critical_violation_count": hough.get("critical_violation_count"),
        "hough_style_medium_violation_count": hough.get("medium_violation_count"),
        "hough_style_bend_violation_count": hough.get("bend_violation_count"),
        "hough_style_severe_bend_violation_count": hough.get("severe_bend_violation_count"),
        "hough_style_lost_line_failure": hough.get("lost_line_failure"),
        "hough_style_bend_failure": hough.get("bend_failure"),
        "hough_style_hard_gate_reliable": hough.get("hard_gate_reliable"),
        "hough_style_hard_gate_enabled": hough.get("hard_gate_enabled"),
        "hough_style_coverage_failure": hough.get("coverage_failure"),
        "hough_line_coverage": candidate_hough.get("line_coverage"),
        "hough_long_line_coverage": candidate_hough.get("long_line_coverage"),
        "hough_candidate_line_count": candidate_hough.get("line_count"),
        "hough_stable_line_count": len(model.get("stable_lines") or []),
        "hough_line_matches": hough.get("line_matches") or [],
    })
    result["geometry_scores"] = geometry_scores

    result.update({
        "algorithm_revision": V22_ALGORITHM_REVISION,
        "config_revision": V22_CONFIG_REVISION,
        "pre_structural_accepted": pre_structural_accepted,
        "pre_hough_accepted": pre_hough_accepted,
        "structural_signature": candidate_signature,
        "structural_score": structural_score,
        "structural_gate_applicable": structural_gate_applicable,
        "structural_gate_pass": structural_gate_pass,
        "structural_failure_reasons": structural_comparison.get("failure_reasons") or [],
        "structural_second_reference_support": second_support,
        "structural_reference_scores": reference_comparisons,
        "hough_style": hough,
        "hough_style_score": hough.get("score"),
        "hough_style_applicable": hough.get("applicable"),
        "hough_style_pass": hough.get("pass"),
        "shape_lock_baseline": baseline,
        "shape_lock_baseline_score": baseline.get("score"),
        "shape_lock_baseline_threshold": baseline.get("threshold"),
        "shape_lock_baseline_pass": baseline.get("passed"),
    })
    return result
