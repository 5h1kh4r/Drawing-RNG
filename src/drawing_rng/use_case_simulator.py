"""Use-case simulation layer for Draw2Seed / Drawing-RNG.

These routines do not change the verifier.  They translate a verification result
into product-style outcomes that are easier to demo: password-manager unlock,
domain password derivation, CAPTCHA-like knowledge challenge, and recovery flow.
The simulations are intentionally labeled as simulations so the prototype does
not overclaim production readiness.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List


def _f(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def _label_from_score(score: float) -> str:
    if score >= 0.80:
        return "strong"
    if score >= 0.65:
        return "usable"
    if score >= 0.50:
        return "borderline"
    return "weak"


def _state(v: Dict[str, Any]) -> str:
    if v.get("step_up_required"):
        return "step_up"
    if v.get("accepted"):
        return "granted"
    return "denied"


def _base_metrics(v: Dict[str, Any], enrollment_result: Dict[str, Any] | None = None) -> Dict[str, Any]:
    geom = v.get("geometry_scores") or {}
    scene = v.get("scene_scores") or {}
    timing = v.get("timing_scores") or {}
    quality = (enrollment_result or {}).get("seed_quality") or {}
    if not isinstance(quality, dict):
        quality = {}
    return {
        "final_score": round(_f(v.get("final_score", v.get("score"))), 3),
        "token_score": round(_f(v.get("token_score")), 3),
        "layout_score": round(_f(geom.get("layout")), 3) if geom else None,
        "topology_score": round(_f(geom.get("topology")), 3) if geom else None,
        "scene_score": round(_f(scene.get("scene_final")), 3) if scene else None,
        "timing_score": round(_f(timing.get("timing_final")), 3) if timing else None,
        "seed_quality": round(_f(quality.get("quality_score")), 1) if quality else None,
        "profile": v.get("profile") or (enrollment_result or {}).get("recommended_profile"),
        "complex_scene_mode": bool(v.get("complex_scene_mode")),
        "fuzzy_ok": bool((v.get("fuzzy_recovery") or {}).get("ok")) if isinstance(v.get("fuzzy_recovery"), dict) else None,
    }


def _safe_outputs(v: Dict[str, Any], domain: str | None = None) -> Dict[str, Any]:
    outputs = v.get("outputs") or {}
    password = outputs.get("demo_password") or "not released"
    seed_hex = outputs.get("seed_hex") or "not released"
    if seed_hex and len(seed_hex) > 18 and seed_hex != "not released":
        seed_hex = seed_hex[:10] + "…" + seed_hex[-6:]
    return {
        "domain": outputs.get("domain") or domain or "example.com",
        "demo_password": password,
        "seed_hex_preview": seed_hex,
        "palette": outputs.get("avatar_palette"),
    }


def _sim_password_manager(v: Dict[str, Any], enrollment_result: Dict[str, Any] | None, domain: str | None) -> Dict[str, Any]:
    state = _state(v)
    if state == "granted":
        outcome = "Vault unlocked"
        action = "Release the derived demo credential and visual confirmation palette."
        risk = "Works as a local unlock simulation. In production, step-up should trigger for suspicious accepts or known visual exposure."
    elif state == "step_up":
        outcome = "Component challenge required"
        action = "Withhold credentials until the user redraws the requested remembered component."
        risk = "Useful for informed-forgery pressure: a screenshot copy must survive a second, hidden component test."
    else:
        outcome = "Vault remains locked"
        action = "Do not release derived material. Offer retry or recovery path."
        risk = "False rejects are still possible, especially with complex scenes; this motivates fallback challenge and enrollment quality scoring."
    return {
        "id": "password_manager",
        "title": "Password-manager / vault unlock",
        "subtitle": "Can a redraw release a deterministic local secret?",
        "state": state,
        "outcome": outcome,
        "action": action,
        "security_note": risk,
        "display_output": _safe_outputs(v, domain) if state == "granted" else {},
        "metrics": _base_metrics(v, enrollment_result),
    }


def _sim_domain_password(v: Dict[str, Any], enrollment_result: Dict[str, Any] | None, domain: str | None) -> Dict[str, Any]:
    state = _state(v)
    if state == "granted":
        outcome = "Domain-specific password generated"
        action = "Use public salt + domain label + canonical seed material to produce a deterministic demo password."
    elif state == "step_up":
        outcome = "Password derivation paused"
        action = "Require the component challenge before revealing the domain credential."
    else:
        outcome = "No password generated"
        action = "Reject the redraw and keep deterministic output hidden."
    return {
        "id": "domain_password",
        "title": "Deterministic site password",
        "subtitle": "Same drawing seed, different app/domain labels.",
        "state": state,
        "outcome": outcome,
        "action": action,
        "security_note": "This is a demo derivation flow, not a recommendation to replace password managers. The interesting property is repeatable secret derivation from a human motor gesture.",
        "display_output": _safe_outputs(v, domain) if state == "granted" else {},
        "metrics": _base_metrics(v, enrollment_result),
    }


def _sim_captcha_like(v: Dict[str, Any], enrollment_result: Dict[str, Any] | None, domain: str | None) -> Dict[str, Any]:
    state = _state(v)
    if state == "granted":
        outcome = "Knowledge-factor challenge passed"
        action = "Allow the protected action after a remembered gesture redraw."
    elif state == "step_up":
        outcome = "Adaptive challenge escalated"
        action = "Ask for one component instead of accepting the whole drawing immediately."
    else:
        outcome = "Challenge failed"
        action = "Deny the protected action or offer another challenge."
    return {
        "id": "captcha_like",
        "title": "CAPTCHA-like knowledge challenge",
        "subtitle": "Not bot detection; a human-memory challenge for a known user.",
        "state": state,
        "outcome": outcome,
        "action": action,
        "security_note": "This should be described as a knowledge-factor challenge, not a traditional CAPTCHA. The threat model is remembered gesture verification, not general human-vs-bot classification.",
        "display_output": {},
        "metrics": _base_metrics(v, enrollment_result),
    }


def _sim_account_recovery(v: Dict[str, Any], enrollment_result: Dict[str, Any] | None, domain: str | None) -> Dict[str, Any]:
    state = _state(v)
    quality = (enrollment_result or {}).get("seed_quality") or {}
    quality_label = quality.get("quality_label") if isinstance(quality, dict) else None
    if state == "granted":
        outcome = "Recovery factor accepted"
        action = "Proceed to a second recovery factor; do not treat the drawing as sufficient by itself."
    elif state == "step_up":
        outcome = "Recovery requires component confirmation"
        action = "Use step-up and an out-of-band factor before allowing recovery."
    else:
        outcome = "Recovery factor rejected"
        action = "Fall back to another recovery method."
    return {
        "id": "account_recovery",
        "title": "Account recovery / backup factor",
        "subtitle": "A revocable human-memory factor in a multi-factor recovery flow.",
        "state": state,
        "outcome": outcome,
        "action": action,
        "security_note": f"Recommended only as one factor in a recovery ceremony. Seed quality was {quality_label or 'unknown'}, which should affect whether this factor is allowed.",
        "display_output": {},
        "metrics": _base_metrics(v, enrollment_result),
    }


def _sim_informed_forgery_demo(v: Dict[str, Any], enrollment_result: Dict[str, Any] | None, domain: str | None) -> Dict[str, Any]:
    state = _state(v)
    if state == "granted":
        outcome = "Risk: visual copy accepted"
        action = "Treat this as a failure case for the talk; seed quality, timing, or step-up should be strengthened."
        risk = "This is the dominant adversarial finding in the pilot: once the drawing is visible, shape-only verification can be copied."
    elif state == "step_up":
        outcome = "Copy hit the adaptive defense"
        action = "The system withheld output and demanded a hidden component redraw."
        risk = "This is the desired response for suspicious visual matches: do not immediately reveal the derived secret."
    else:
        outcome = "Visual copy rejected"
        action = "Show the failure reasons and compare to owner redraw behavior."
        risk = "A useful demonstration of strict gates, topology checks, and seed-quality constraints."
    return {
        "id": "informed_forgery",
        "title": "Informed-forgery pressure test",
        "subtitle": "What happens when someone saw the drawing?",
        "state": state,
        "outcome": outcome,
        "action": action,
        "security_note": risk,
        "display_output": {},
        "metrics": _base_metrics(v, enrollment_result),
    }


SIMULATORS = {
    "password_manager": _sim_password_manager,
    "domain_password": _sim_domain_password,
    "captcha_like": _sim_captcha_like,
    "account_recovery": _sim_account_recovery,
    "informed_forgery": _sim_informed_forgery_demo,
}

DEFAULT_USE_CASES = [
    "password_manager",
    "domain_password",
    "captcha_like",
    "account_recovery",
    "informed_forgery",
]


def simulate_use_cases(
    verification_result: Dict[str, Any],
    enrollment_result: Dict[str, Any] | None = None,
    domain: str | None = None,
    use_cases: Iterable[str] | None = None,
) -> List[Dict[str, Any]]:
    """Return product-style use-case outcomes for a verifier result."""
    if not isinstance(verification_result, dict):
        raise TypeError("verification_result must be a dict")
    enrollment_result = enrollment_result or {}
    selected = list(use_cases or DEFAULT_USE_CASES)
    rows: List[Dict[str, Any]] = []
    for name in selected:
        fn = SIMULATORS.get(str(name))
        if fn:
            rows.append(fn(verification_result, enrollment_result, domain))
    return rows


def simulation_summary(simulations: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts: Dict[str, int] = {"granted": 0, "step_up": 0, "denied": 0}
    for row in simulations:
        state = str(row.get("state") or "denied")
        counts[state] = counts.get(state, 0) + 1
    return {
        "count": len(simulations),
        "states": counts,
        "takeaway": (
            "The same low-level verification result maps to different product policies. "
            "A password manager can require step-up before revealing a secret, while a CAPTCHA-like challenge can simply deny or escalate."
        ),
    }
