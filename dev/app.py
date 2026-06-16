from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from flask import Flask, jsonify, request, send_from_directory

# Make src/ importable when running directly or under gunicorn.
import sys
ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from drawing_rng.enrollment import analyze_enrollment, verify_redraw, verify_step_up_component
from drawing_rng.profiles import get_profile
from drawing_rng.stroke_token_encoder import encode_json_payload
from drawing_rng.use_case_simulator import simulate_use_cases, simulation_summary

try:
    from supabase import create_client
except Exception:  # local-only mode still works
    create_client = None

STATIC = ROOT / "static"
LOCAL_DATA_DIR = Path(os.environ.get("LOCAL_DATA_DIR", ROOT / "data" / "local_submissions"))
LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
PROMPT_TABLE = os.environ.get("PROMPT_TABLE", "stroke_samples")
ENROLLMENT_TABLE = os.environ.get("ENROLLMENT_TABLE", "drawing_seed_enrollments")
VERIFICATION_TABLE = os.environ.get("VERIFICATION_TABLE", "drawing_seed_verifications")
AUTO_LOG_ENROLLMENTS = os.environ.get("AUTO_LOG_ENROLLMENTS", "1") != "0"
AUTO_LOG_VERIFICATIONS = os.environ.get("AUTO_LOG_VERIFICATIONS", "1") != "0"

supabase = None
if create_client and SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

app = Flask(__name__, static_folder=str(STATIC), static_url_path="")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _slug(text: Any, fallback: str = "sample") -> str:
    value = str(text or "").strip().lower()
    value = re.sub(r"[^a-z0-9_.-]+", "_", value).strip("_")
    return value or fallback


def _save_local(kind: str, payload: Dict[str, Any]) -> str:
    folder = LOCAL_DATA_DIR / kind
    folder.mkdir(parents=True, exist_ok=True)
    name = _slug(payload.get("sample_name") or payload.get("concept") or kind)
    path = folder / f"{_stamp()}_{name}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path.relative_to(ROOT))


_SECRET_KEYS = {
    "demo_password",
    "seed_hex",
    "secret_hex",
    "secret_hex_for_demo_only",
    "canonical_seed_material",
}


def _redact_for_logs(obj: Any) -> Any:
    """Remove directly reusable secret outputs before storing research logs."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in _SECRET_KEYS:
                out[k] = "[redacted]"
            elif k == "outputs" and isinstance(v, dict):
                # Keep domain/source but redact generated password/seed material.
                out[k] = _redact_for_logs(v)
            else:
                out[k] = _redact_for_logs(v)
        return out
    if isinstance(obj, list):
        return [_redact_for_logs(x) for x in obj]
    return obj


def _insert_supabase(table: str, row: Dict[str, Any]) -> str | None:
    if not supabase:
        return None
    res = supabase.table(table).insert(row).execute()
    return (res.data or [{}])[0].get("id")



_OPTIONAL_ENROLLMENT_COLUMNS = {
    "seed_quality_score",
    "seed_quality_label",
    "seed_quality_hard_reject",
    "complexity_class",
    "scene_stability_score",
    "timing_stability_score",
}


def _insert_enrollment_with_schema_fallback(row: Dict[str, Any]) -> Dict[str, Any]:
    if not supabase:
        path = _save_local("enrollments", row)
        return {"storage": "local", "path": path}
    try:
        eid = _insert_supabase(ENROLLMENT_TABLE, row)
        return {"storage": "supabase", "id": eid}
    except Exception as exc:
        first_error = str(exc)
        compact = {k: v for k, v in row.items() if k not in _OPTIONAL_ENROLLMENT_COLUMNS}
        try:
            eid = _insert_supabase(ENROLLMENT_TABLE, compact)
            return {
                "storage": "supabase",
                "id": eid,
                "warning": "logged_without_optional_seed_quality_columns",
                "first_error": first_error,
            }
        except Exception as retry_exc:
            row_with_error = dict(row)
            row_with_error["supabase_log_error"] = first_error
            row_with_error["supabase_retry_error"] = str(retry_exc)
            path = _save_local("enrollments", row_with_error)
            return {
                "storage": "local_fallback",
                "path": path,
                "warning": "supabase_insert_failed_saved_local",
                "first_error": first_error,
                "retry_error": str(retry_exc),
            }

def _log_enrollment(participant_id: Any, seed_label: Any, attempts: Any, result: Dict[str, Any], notes: Any = "", ui_version: str = "seed-enrollment-codefreeze") -> Dict[str, Any]:
    row = {
        "participant_id": participant_id,
        "seed_label": seed_label or "drawing_seed",
        "attempt_count": len(attempts) if isinstance(attempts, list) else 0,
        "attempts": attempts if isinstance(attempts, list) else [],
        "analysis_result": _redact_for_logs(result),
        "accepted_for_demo": result.get("accepted_for_demo"),
        "stability_score": result.get("stability_score"),
        "recommended_profile": result.get("recommended_profile"),
        "seed_quality_score": result.get("seed_quality_score"),
        "seed_quality_label": result.get("seed_quality_label"),
        "seed_quality_hard_reject": result.get("seed_quality_hard_reject"),
        "complexity_class": result.get("complexity_class"),
        "scene_stability_score": result.get("scene_stability_score"),
        "timing_stability_score": result.get("timing_stability_score"),
        "public_salt": result.get("public_salt"),
        "ui_version": ui_version,
        "notes": notes or "",
        "user_agent": request.headers.get("User-Agent", ""),
    }
    return _insert_enrollment_with_schema_fallback(row)



_OPTIONAL_VERIFICATION_COLUMNS = {
    "token_score_weighted",
    "token_bigram_score",
    "complex_scene_mode",
    "scene_final",
    "scene_assignment",
    "scene_raster",
    "scene_relation",
    "timing_final",
    "step_up_required",
    "step_up_passed",
    "component_score",
}


def _insert_verification_with_schema_fallback(row: Dict[str, Any]) -> Dict[str, Any]:
    """Insert a verification row without letting optional diagnostics break logging.

    Supabase/PostgREST returns PGRST204 when new columns exist in code but have
    not been applied/refreshed in the remote schema cache.  The old behavior let
    that exception bubble up, so no verification was stored.  This helper first
    tries the full row, then retries without optional Phase 2.9/2.10 diagnostic
    columns, then finally writes a local JSON fallback.
    """
    if not supabase:
        path = _save_local("verifications", row)
        return {"storage": "local", "path": path}

    try:
        vid = _insert_supabase(VERIFICATION_TABLE, row)
        return {"storage": "supabase", "id": vid}
    except Exception as exc:
        first_error = str(exc)
        compact = {k: v for k, v in row.items() if k not in _OPTIONAL_VERIFICATION_COLUMNS}
        try:
            vid = _insert_supabase(VERIFICATION_TABLE, compact)
            return {
                "storage": "supabase",
                "id": vid,
                "warning": "logged_without_optional_diagnostic_columns",
                "first_error": first_error,
            }
        except Exception as retry_exc:
            row_with_error = dict(row)
            row_with_error["supabase_log_error"] = first_error
            row_with_error["supabase_retry_error"] = str(retry_exc)
            path = _save_local("verifications", row_with_error)
            return {
                "storage": "local_fallback",
                "path": path,
                "warning": "supabase_insert_failed_saved_local",
                "first_error": first_error,
                "retry_error": str(retry_exc),
            }

def _log_verification(payload: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    enrollment_result = payload.get("enrollment_result") or {}
    geometry_scores = result.get("geometry_scores") or {}
    scene_scores = result.get("scene_scores") or {}
    fuzzy = result.get("fuzzy_recovery") or {}
    row = {
        "enrollment_id": payload.get("enrollment_id") or enrollment_result.get("enrollment_id"),
        "participant_id": payload.get("participant_id"),
        "seed_label": payload.get("seed_label"),
        "attempt_type": payload.get("attempt_type") or "owner_test",
        "redraw_strokes": payload.get("redraw_strokes") or [],
        "verification_result": _redact_for_logs(result),
        "accepted": result.get("accepted"),
        "profile": result.get("profile"),
        "final_score": result.get("final_score") or result.get("score"),
        "token_score": result.get("token_score"),
        "token_score_weighted": result.get("token_score_weighted"),
        "token_bigram_score": result.get("token_bigram_score"),
        "geometry_final": geometry_scores.get("geometry_final"),
        "layout_score": geometry_scores.get("layout"),
        "relation_score": geometry_scores.get("relation"),
        "curve_score": geometry_scores.get("curve"),
        "stroke_shape_score": geometry_scores.get("stroke_shape"),
        "complex_scene_mode": result.get("complex_scene_mode"),
        "scene_final": scene_scores.get("scene_final"),
        "scene_assignment": scene_scores.get("scene_assignment"),
        "scene_raster": scene_scores.get("scene_raster"),
        "scene_relation": scene_scores.get("scene_relation"),
        "timing_final": ((result.get("timing_scores") or {}).get("timing_final") if isinstance(result.get("timing_scores"), dict) else None),
        "step_up_required": result.get("step_up_required"),
        "step_up_passed": result.get("step_up_passed"),
        "component_score": result.get("component_score"),
        "fuzzy_ok": fuzzy.get("ok") if isinstance(fuzzy, dict) else None,
        "fuzzy_mode": fuzzy.get("ecc_mode") if isinstance(fuzzy, dict) else None,
        "failure_reasons": result.get("failure_reasons") or [],
        "ui_version": payload.get("ui_version", "seed-enrollment-codefreeze"),
        "user_agent": request.headers.get("User-Agent", ""),
    }
    return _insert_verification_with_schema_fallback(row)


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/collect")
def collect_page():
    return send_from_directory(STATIC, "collect.html")


@app.get("/enroll")
def enroll_page():
    return send_from_directory(STATIC, "enroll.html")


@app.get("/api/health")
def health():
    return jsonify({
        "ok": True,
        "service": "Drawing-RNG cleaned app",
        "supabase_configured": supabase is not None,
        "prompt_table": PROMPT_TABLE,
        "enrollment_table": ENROLLMENT_TABLE,
        "verification_table": VERIFICATION_TABLE,
        "auto_log_enrollments": AUTO_LOG_ENROLLMENTS,
        "auto_log_verifications": AUTO_LOG_VERIFICATIONS,
    })


@app.post("/api/tokenize")
def tokenize():
    payload = request.get_json(force=True, silent=False)
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON body must be an object"}), 400
    try:
        params = payload.get("params") or get_profile(payload.get("profile") or "balanced")
        result = encode_json_payload({"strokes": payload.get("strokes", []), "params": params})
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/save_prompt_sample")
def save_prompt_sample():
    payload = request.get_json(force=True, silent=False)
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON body must be an object"}), 400
    strokes = payload.get("strokes", [])
    if not isinstance(strokes, list) or not strokes:
        return jsonify({"error": "No strokes submitted"}), 400

    row = {
        "participant_id": payload.get("participant_id"),
        "concept": payload.get("concept"),
        "redraw_id": payload.get("redraw_id"),
        "sample_name": payload.get("sample_name") or payload.get("name") or "sample",
        "notes": payload.get("notes", ""),
        "strokes": strokes,
        "params": payload.get("params") or get_profile(payload.get("profile") or "balanced"),
        "canvas_size": payload.get("canvas_size"),
        "serialized": payload.get("serialized"),
        "ui_version": payload.get("ui_version", "cleaned-collector-v1"),
        "user_agent": request.headers.get("User-Agent", ""),
    }

    if supabase:
        try:
            res = supabase.table(PROMPT_TABLE).insert(row).execute()
            return jsonify({"ok": True, "storage": "supabase", "id": (res.data or [{}])[0].get("id")})
        except Exception as exc:
            return jsonify({"error": f"Supabase insert failed: {exc}"}), 500

    local_path = _save_local("prompt_samples", row)
    return jsonify({"ok": True, "storage": "local", "path": local_path})


@app.post("/api/analyze_enrollment")
def analyze_enrollment_route():
    payload = request.get_json(force=True, silent=False)
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON body must be an object"}), 400
    attempts = payload.get("attempts", [])
    if not isinstance(attempts, list) or len(attempts) < 2:
        return jsonify({"error": "Need at least 2 attempts; 3 is recommended."}), 400
    try:
        result = analyze_enrollment(
            attempts=attempts,
            domain=str(payload.get("domain") or "example.com"),
            salt=payload.get("public_salt"),
        )
        if AUTO_LOG_ENROLLMENTS:
            try:
                saved = _log_enrollment(
                    participant_id=payload.get("participant_id"),
                    seed_label=payload.get("seed_label"),
                    attempts=attempts,
                    result=result,
                    notes=payload.get("notes", ""),
                    ui_version=payload.get("ui_version", "seed-enrollment-codefreeze"),
                )
                result["enrollment_saved"] = saved
                if saved.get("id"):
                    result["enrollment_id"] = saved.get("id")
            except Exception as log_exc:
                result["enrollment_log_error"] = str(log_exc)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/verify_redraw")
def verify_redraw_route():
    payload = request.get_json(force=True, silent=False)
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON body must be an object"}), 400

    enrollment_result = payload.get("enrollment_result")
    redraw_strokes = payload.get("redraw_strokes")
    threshold = payload.get("threshold")
    fuzzy_required = bool(payload.get("fuzzy_required", False))

    if not isinstance(enrollment_result, dict):
        return jsonify({"error": "Missing or invalid enrollment_result"}), 400
    if not isinstance(redraw_strokes, list) or not redraw_strokes:
        return jsonify({"error": "Missing or invalid redraw_strokes"}), 400

    try:
        result = verify_redraw(
            enrollment_result=enrollment_result,
            redraw_strokes=redraw_strokes,
            threshold=threshold,
            fuzzy_required=fuzzy_required,
        )
        if AUTO_LOG_VERIFICATIONS:
            try:
                saved = _log_verification(payload, result)
                result["verification_saved"] = saved
                if saved.get("id"):
                    result["verification_id"] = saved.get("id")
            except Exception as log_exc:
                result["verification_log_error"] = str(log_exc)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400




@app.post("/api/verify_component_challenge")
def verify_component_challenge_route():
    payload = request.get_json(force=True, silent=False)
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON body must be an object"}), 400

    enrollment_result = payload.get("enrollment_result")
    challenge = payload.get("challenge")
    component_strokes = payload.get("component_strokes")
    initial_result = payload.get("initial_result") or {}

    if not isinstance(enrollment_result, dict):
        return jsonify({"error": "Missing or invalid enrollment_result"}), 400
    if not isinstance(challenge, dict):
        return jsonify({"error": "Missing or invalid challenge"}), 400
    if not isinstance(component_strokes, list) or not component_strokes:
        return jsonify({"error": "Missing or invalid component_strokes"}), 400

    try:
        result = verify_step_up_component(
            enrollment_result=enrollment_result,
            challenge=challenge,
            component_strokes=component_strokes,
            initial_result=initial_result,
        )
        if AUTO_LOG_VERIFICATIONS:
            try:
                log_payload = dict(payload)
                log_payload["redraw_strokes"] = component_strokes
                log_payload["attempt_type"] = payload.get("attempt_type") or "step_up_component"
                saved = _log_verification(log_payload, result)
                result["verification_saved"] = saved
                if saved.get("id"):
                    result["verification_id"] = saved.get("id")
            except Exception as log_exc:
                result["verification_log_error"] = str(log_exc)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400



@app.post("/api/simulate_use_cases")
def simulate_use_cases_route():
    """Translate a verification result into product-style demo scenarios.

    This endpoint is intentionally non-mutating and does not log to the
    verification dataset.  It can either accept an already-computed
    verification_result or run verify_redraw once from enrollment_result +
    redraw_strokes.
    """
    payload = request.get_json(force=True, silent=False)
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON body must be an object"}), 400

    enrollment_result = payload.get("enrollment_result") or {}
    verification_result = payload.get("verification_result")
    domain = str(payload.get("domain") or (enrollment_result or {}).get("domain") or "example.com")

    try:
        if not isinstance(verification_result, dict):
            redraw_strokes = payload.get("redraw_strokes")
            if not isinstance(enrollment_result, dict) or not enrollment_result:
                return jsonify({"error": "Missing enrollment_result or verification_result"}), 400
            if not isinstance(redraw_strokes, list) or not redraw_strokes:
                return jsonify({"error": "Missing redraw_strokes when verification_result is absent"}), 400
            verification_result = verify_redraw(
                enrollment_result=enrollment_result,
                redraw_strokes=redraw_strokes,
                threshold=payload.get("threshold"),
                fuzzy_required=bool(payload.get("fuzzy_required", False)),
            )

        simulations = simulate_use_cases(
            verification_result=verification_result,
            enrollment_result=enrollment_result if isinstance(enrollment_result, dict) else {},
            domain=domain,
            use_cases=payload.get("use_cases"),
        )
        return jsonify({
            "ok": True,
            "domain": domain,
            "verification_result": verification_result,
            "simulations": simulations,
            "summary": simulation_summary(simulations),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

@app.post("/api/save_enrollment")
def save_enrollment():
    payload = request.get_json(force=True, silent=False)
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON body must be an object"}), 400

    attempts = payload.get("attempts", [])
    result = payload.get("result") or None
    if not result:
        try:
            result = analyze_enrollment(attempts=attempts, domain=str(payload.get("domain") or "example.com"))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    row = {
        "participant_id": payload.get("participant_id"),
        "seed_label": payload.get("seed_label") or "drawing_seed",
        "attempt_count": len(attempts) if isinstance(attempts, list) else 0,
        "attempts": attempts,
        "analysis_result": _redact_for_logs(result),
        "accepted_for_demo": result.get("accepted_for_demo"),
        "stability_score": result.get("stability_score"),
        "recommended_profile": result.get("recommended_profile"),
        "seed_quality_score": result.get("seed_quality_score"),
        "seed_quality_label": result.get("seed_quality_label"),
        "seed_quality_hard_reject": result.get("seed_quality_hard_reject"),
        "complexity_class": result.get("complexity_class"),
        "scene_stability_score": result.get("scene_stability_score"),
        "timing_stability_score": result.get("timing_stability_score"),
        "public_salt": result.get("public_salt"),
        "ui_version": payload.get("ui_version", "seed-enrollment-v1"),
        "notes": payload.get("notes", ""),
        "user_agent": request.headers.get("User-Agent", ""),
    }

    saved = _insert_enrollment_with_schema_fallback(row)
    return jsonify({"ok": True, **saved})


@app.get("/dev")
def dev_page():
    return send_from_directory(STATIC, "dev.html")


def _require_supabase():
    if not supabase:
        raise RuntimeError("Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.")


def _fetch_enrollment_row(enrollment_id: str) -> Dict[str, Any]:
    _require_supabase()
    res = supabase.table(ENROLLMENT_TABLE).select("*").eq("id", enrollment_id).limit(1).execute()
    rows = res.data or []
    if not rows:
        raise ValueError(f"Enrollment not found: {enrollment_id}")
    return rows[0]


def _enrollment_result_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    result = row.get("analysis_result") or {}
    if isinstance(result, str):
        result = json.loads(result)
    if not isinstance(result, dict):
        result = {}
    # Attach DB metadata needed by verification/logging. The analysis_result in
    # Supabase is intentionally redacted, but it still contains tokens, geometry,
    # profile, public_salt and fuzzy_helper.
    result.setdefault("enrollment_id", row.get("id"))
    result.setdefault("recommended_profile", row.get("recommended_profile") or "balanced")
    result.setdefault("public_salt", row.get("public_salt") or "")
    result.setdefault("accepted_for_demo", row.get("accepted_for_demo"))
    result.setdefault("stability_score", row.get("stability_score"))
    if row.get("seed_label"):
        result.setdefault("seed_label", row.get("seed_label"))
    return result


@app.get("/api/dev/enrollments")
def dev_list_enrollments():
    try:
        _require_supabase()
        res = supabase.table(ENROLLMENT_TABLE).select("*").order("created_at", desc=True).execute()
        rows = res.data or []
        return jsonify({"ok": True, "count": len(rows), "enrollments": rows})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/dev/enrollments/<enrollment_id>")
def dev_get_enrollment(enrollment_id: str):
    try:
        row = _fetch_enrollment_row(enrollment_id)
        vres = supabase.table(VERIFICATION_TABLE).select("*").eq("enrollment_id", enrollment_id).order("created_at", desc=True).execute()
        return jsonify({"ok": True, "enrollment": row, "verifications": vres.data or []})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/dev/verifications")
def dev_list_verifications():
    try:
        _require_supabase()
        enrollment_id = request.args.get("enrollment_id")
        q = supabase.table(VERIFICATION_TABLE).select("*")
        if enrollment_id:
            q = q.eq("enrollment_id", enrollment_id)
        res = q.order("created_at", desc=True).execute()
        rows = res.data or []
        return jsonify({"ok": True, "count": len(rows), "verifications": rows})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/api/dev/verifications/<verification_id>")
def dev_get_verification(verification_id: str):
    try:
        _require_supabase()
        res = supabase.table(VERIFICATION_TABLE).select("*").eq("id", verification_id).limit(1).execute()
        rows = res.data or []
        if not rows:
            return jsonify({"ok": False, "error": "Verification attempt not found"}), 404
        return jsonify({"ok": True, "verification": rows[0]})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.patch("/api/dev/verifications/<verification_id>")
def dev_update_verification_type(verification_id: str):
    try:
        _require_supabase()
        payload = request.get_json(force=True, silent=False)
        attempt_type = str((payload or {}).get("attempt_type") or "").strip()
        allowed_types = {
            "owner_test",
            "blind_impostor",
            "informed_forgery",
            "wrong_shape",
            "true_wrong_shape",
            "near_miss",
            "concept_variant",
            "bad_sample",
            "ambiguous",
        }
        if attempt_type not in allowed_types:
            return jsonify({"ok": False, "error": "Invalid verification attempt_type"}), 400
        res = supabase.table(VERIFICATION_TABLE).update({"attempt_type": attempt_type}).eq("id", verification_id).execute()
        rows = res.data or []
        if not rows:
            return jsonify({"ok": False, "error": "Verification attempt not found"}), 404
        return jsonify({"ok": True, "verification": rows[0]})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/dev/verify_existing")
def dev_verify_existing():
    payload = request.get_json(force=True, silent=False)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "JSON body must be an object"}), 400
    enrollment_id = payload.get("enrollment_id")
    redraw_strokes = payload.get("redraw_strokes")
    if not enrollment_id:
        return jsonify({"ok": False, "error": "Missing enrollment_id"}), 400
    if not isinstance(redraw_strokes, list) or not redraw_strokes:
        return jsonify({"ok": False, "error": "Missing redraw_strokes"}), 400
    try:
        row = _fetch_enrollment_row(str(enrollment_id))
        enrollment_result = _enrollment_result_from_row(row)
        result = verify_redraw(
            enrollment_result=enrollment_result,
            redraw_strokes=redraw_strokes,
            threshold=payload.get("threshold"),
            fuzzy_required=bool(payload.get("fuzzy_required", False)),
        )
        if AUTO_LOG_VERIFICATIONS:
            log_payload = dict(payload)
            log_payload["enrollment_result"] = enrollment_result
            log_payload["enrollment_id"] = str(enrollment_id)
            log_payload.setdefault("participant_id", payload.get("participant_id") or "dev_tester")
            log_payload.setdefault("seed_label", row.get("seed_label"))
            log_payload.setdefault("ui_version", "dev-existing-enrollment-verifier")
            try:
                saved = _log_verification(log_payload, result)
                result["verification_saved"] = saved
                if saved.get("id"):
                    result["verification_id"] = saved.get("id")
            except Exception as log_exc:
                result["verification_log_error"] = str(log_exc)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.delete("/api/dev/verifications/<verification_id>")
def dev_delete_verification(verification_id: str):
    try:
        _require_supabase()
        supabase.table(VERIFICATION_TABLE).delete().eq("id", verification_id).execute()
        return jsonify({"ok": True, "deleted_verification_id": verification_id})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.delete("/api/dev/enrollments/<enrollment_id>")
def dev_delete_enrollment(enrollment_id: str):
    try:
        _require_supabase()
        # Delete linked verifications first to avoid orphan records.
        supabase.table(VERIFICATION_TABLE).delete().eq("enrollment_id", enrollment_id).execute()
        supabase.table(ENROLLMENT_TABLE).delete().eq("id", enrollment_id).execute()
        return jsonify({"ok": True, "deleted_enrollment_id": enrollment_id, "linked_verifications_deleted": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
