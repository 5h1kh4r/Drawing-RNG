from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


def _float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "t"}
    return bool(value)


def _reasons_with_prefix(reasons: Iterable[str], prefixes: Iterable[str]) -> List[str]:
    prefix_tuple = tuple(prefixes)
    return sorted(str(reason) for reason in reasons if str(reason).startswith(prefix_tuple))


def _gate(
    *,
    applicable: bool,
    passed: Optional[bool],
    hard_gate: bool,
    score: Optional[float] = None,
    threshold: Optional[float] = None,
    reasons: Optional[List[str]] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "applicable": bool(applicable),
        "passed": bool(passed) if applicable and passed is not None else None,
        "hard_gate": bool(hard_gate and applicable),
        "score": score,
        "threshold": threshold,
        "failure_reasons": list(reasons or []),
        "note": note,
    }


def build_gate_trace(result: Dict[str, Any]) -> Dict[str, Any]:
    """Build an explicit, research-friendly gate trace from verify_redraw() output.

    This function does not change the verifier decision. It only converts the
    existing scores, thresholds, and failure reasons into one stable structure
    suitable for CSV exports, gate-failure plots, and ablation studies.
    """
    geometry = result.get("geometry_scores") or {}
    thresholds = result.get("geometry_thresholds") or {}
    failures = [str(x) for x in (result.get("failure_reasons") or [])]
    geom_failures = [
        str(x) for x in (result.get("geometry_failure_reasons_diagnostic") or [])
    ]
    scene_failures = [str(x) for x in (result.get("scene_failure_reasons") or [])]

    complex_scene = _bool(result.get("complex_scene_mode"))
    single_open = _bool(geometry.get("single_open_stroke_case"))
    single_closed = _bool(geometry.get("single_closed_stroke_case"))
    two_open = _bool(geometry.get("two_open_stroke_case"))
    closed_style_applicable = _bool(geometry.get("closed_style_applicable"))

    token_score = _float(result.get("token_score"), 0.0)
    token_threshold = _float(result.get("token_threshold"), _float(result.get("threshold"), 0.0))

    if complex_scene:
        effective_token_threshold = _float(
            result.get("complex_token_threshold"), token_threshold
        )
        token_pass = _bool(result.get("complex_token_pass"))
    else:
        effective_token_threshold = token_threshold
        token_pass = bool(
            token_score is not None
            and effective_token_threshold is not None
            and token_score >= effective_token_threshold
        )

    geometry_final_score = _float(geometry.get("geometry_final"))
    geometry_final_threshold = _float(thresholds.get("geometry_final"))
    count_score = _float(geometry.get("count"))
    layout_score = _float(geometry.get("layout"))
    relation_score = _float(geometry.get("relation"))
    topology_score = _float(geometry.get("topology"))
    curve_score = _float(geometry.get("curve"))
    shape_score = _float(geometry.get("stroke_shape"))
    closed_style_score = _float(geometry.get("closed_style"))

    # Applicability follows geometry_failure_reasons() in geometry_verifier.py.
    relation_applicable = not (single_open or single_closed)
    topology_applicable = not (single_open or single_closed)
    closed_style_gate_applicable = closed_style_applicable or single_closed

    count_reasons = _reasons_with_prefix(geom_failures, ["stroke_count_mismatch"])
    layout_reasons = _reasons_with_prefix(geom_failures, ["layout_"])
    relation_reasons = _reasons_with_prefix(
        geom_failures, ["relation_", "two_open_relation_"]
    )
    topology_reasons = _reasons_with_prefix(
        geom_failures, ["topology_", "two_open_topology_"]
    )
    curve_reasons = _reasons_with_prefix(geom_failures, ["curve_"])
    shape_reasons = _reasons_with_prefix(
        geom_failures, ["stroke_shape_", "single_open_shape_"]
    )
    closed_reasons = _reasons_with_prefix(
        geom_failures, ["closed_style_", "single_closed_style_"]
    )
    geometry_final_reasons = _reasons_with_prefix(
        geom_failures, ["geometry_final_"]
    )

    strict_geometry_pass = not geom_failures

    if complex_scene:
        floor_thresholds = result.get("complex_geometry_floor_thresholds") or {
            "layout": 0.50,
            "relation": 0.50,
            "topology": 0.48,
        }
        layout_threshold = _float(floor_thresholds.get("layout"), 0.50)
        relation_threshold = _float(floor_thresholds.get("relation"), 0.50)
        topology_threshold = _float(floor_thresholds.get("topology"), 0.48)

        layout_pass = bool(layout_score is not None and layout_score >= layout_threshold)
        relation_pass = bool(
            relation_score is not None and relation_score >= relation_threshold
        )
        topology_pass = bool(
            topology_score is not None and topology_score >= topology_threshold
        )
        scene_pass = _bool(result.get("scene_pass"))
        geometry_effective_pass = _bool(result.get("complex_geometry_floor_pass"))
    else:
        layout_threshold = _float(thresholds.get("layout"))
        relation_threshold = _float(thresholds.get("relation"))
        topology_threshold = _float(thresholds.get("topology"))

        layout_pass = not layout_reasons
        relation_pass = (not relation_reasons) if relation_applicable else None
        topology_pass = (not topology_reasons) if topology_applicable else None
        scene_pass = None
        geometry_effective_pass = strict_geometry_pass

    count_pass = not count_reasons
    curve_pass = not curve_reasons
    shape_pass = not shape_reasons
    closed_pass = (not closed_reasons) if closed_style_gate_applicable else None
    geometry_final_pass = not geometry_final_reasons

    scene_scores = result.get("scene_scores") or {}
    scene_thresholds = result.get("scene_thresholds") or {}

    fuzzy = result.get("fuzzy_recovery")
    fuzzy_applicable = isinstance(fuzzy, dict)
    fuzzy_pass = _bool(fuzzy.get("ok")) if fuzzy_applicable else None

    gates = {
        "token": _gate(
            applicable=True,
            passed=token_pass,
            hard_gate=True,
            score=token_score,
            threshold=effective_token_threshold,
            reasons=_reasons_with_prefix(
                failures, ["token_score_below_", "complex_token_score_below_"]
            ),
            note="Uses complex-scene token policy when complex_scene_mode is active.",
        ),
        "component_count": _gate(
            applicable=True,
            passed=count_pass,
            hard_gate=not complex_scene,
            score=count_score,
            threshold=1.0,
            reasons=count_reasons,
            note=(
                "Strict diagnostic only in complex-scene mode; macro-scene matching "
                "may tolerate micro-stroke count changes."
                if complex_scene
                else None
            ),
        ),
        "layout": _gate(
            applicable=True,
            passed=layout_pass,
            hard_gate=True,
            score=layout_score,
            threshold=layout_threshold,
            reasons=(
                _reasons_with_prefix(failures, ["complex_layout_floor_"])
                if complex_scene
                else layout_reasons
            ),
        ),
        "relation": _gate(
            applicable=relation_applicable,
            passed=relation_pass,
            hard_gate=True,
            score=relation_score,
            threshold=relation_threshold,
            reasons=(
                _reasons_with_prefix(failures, ["complex_relation_floor_"])
                if complex_scene
                else relation_reasons
            ),
        ),
        "topology": _gate(
            applicable=topology_applicable,
            passed=topology_pass,
            hard_gate=True,
            score=topology_score,
            threshold=topology_threshold,
            reasons=(
                _reasons_with_prefix(failures, ["complex_topology_floor_"])
                if complex_scene
                else topology_reasons
            ),
            note="Includes the specialized two-open-stroke topology policy where applicable.",
        ),
        "curve": _gate(
            applicable=True,
            passed=curve_pass,
            hard_gate=not complex_scene,
            score=curve_score,
            threshold=_float(thresholds.get("curve")),
            reasons=curve_reasons,
            note="Strict diagnostic only in complex-scene mode." if complex_scene else None,
        ),
        "stroke_shape": _gate(
            applicable=True,
            passed=shape_pass,
            hard_gate=not complex_scene,
            score=shape_score,
            threshold=_float(thresholds.get("stroke_shape")),
            reasons=shape_reasons,
            note="Strict diagnostic only in complex-scene mode." if complex_scene else None,
        ),
        "closed_style": _gate(
            applicable=closed_style_gate_applicable,
            passed=closed_pass,
            hard_gate=(not complex_scene and closed_style_gate_applicable),
            score=closed_style_score,
            threshold=_float(thresholds.get("closed_style")),
            reasons=closed_reasons,
            note="Only applicable when a closed or near-closed contour is present.",
        ),
        "geometry_final": _gate(
            applicable=True,
            passed=geometry_final_pass,
            hard_gate=not complex_scene,
            score=geometry_final_score,
            threshold=geometry_final_threshold,
            reasons=geometry_final_reasons,
            note="Diagnostic aggregate; still a configured geometry sub-gate for simple symbols.",
        ),
        "scene": _gate(
            applicable=complex_scene,
            passed=scene_pass,
            hard_gate=complex_scene,
            score=_float(scene_scores.get("scene_final")),
            threshold=_float(scene_thresholds.get("scene_final")),
            reasons=scene_failures,
        ),
        "fuzzy_recovery": _gate(
            applicable=fuzzy_applicable,
            passed=fuzzy_pass,
            hard_gate=_bool(result.get("fuzzy_required")),
            score=_float(fuzzy.get("hamming_distance")) if fuzzy_applicable else None,
            threshold=_float(fuzzy.get("max_correctable_bits")) if fuzzy_applicable else None,
            reasons=["fuzzy_secret_recovery_failed"] if fuzzy_applicable and not fuzzy_pass else [],
            note="Research signal unless fuzzy_required=True.",
        ),
        "step_up": _gate(
            applicable=_bool(result.get("step_up_required")),
            passed=False if _bool(result.get("step_up_required")) else None,
            hard_gate=_bool(result.get("step_up_required")),
            reasons=_reasons_with_prefix(failures, ["step_up_component_required"]),
            note="A separate component challenge must be completed before unlock.",
        ),
    }

    hard_applicable = [
        name
        for name, gate in gates.items()
        if gate["applicable"] and gate["hard_gate"]
    ]
    failed_hard = [
        name
        for name in hard_applicable
        if gates[name]["passed"] is False
    ]

    return {
        "schema_version": "gate-trace-v1",
        "complex_scene_mode": complex_scene,
        "shape_case": (
            "single_open"
            if single_open
            else "single_closed"
            if single_closed
            else "two_open"
            if two_open
            else "general_multistroke"
        ),
        "gates": gates,
        "hard_gate_names": hard_applicable,
        "failed_hard_gates": failed_hard,
        "strict_geometry_pass_diagnostic": strict_geometry_pass,
        "effective_geometry_pass": geometry_effective_pass,
        "primary_accepted": _bool(result.get("primary_accepted")),
        "final_accepted": _bool(result.get("accepted")),
    }
