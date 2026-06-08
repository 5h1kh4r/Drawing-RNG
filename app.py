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

from drawing_rng.enrollment import analyze_enrollment, verify_redraw
from drawing_rng.profiles import get_profile
from drawing_rng.stroke_token_encoder import encode_json_payload

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
        "public_salt": result.get("public_salt"),
        "ui_version": ui_version,
        "notes": notes or "",
        "user_agent": request.headers.get("User-Agent", ""),
    }
    if supabase:
        eid = _insert_supabase(ENROLLMENT_TABLE, row)
        return {"storage": "supabase", "id": eid}
    path = _save_local("enrollments", row)
    return {"storage": "local", "path": path}


def _log_verification(payload: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    enrollment_result = payload.get("enrollment_result") or {}
    geometry_scores = result.get("geometry_scores") or {}
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
        "geometry_final": geometry_scores.get("geometry_final"),
        "layout_score": geometry_scores.get("layout"),
        "relation_score": geometry_scores.get("relation"),
        "curve_score": geometry_scores.get("curve"),
        "stroke_shape_score": geometry_scores.get("stroke_shape"),
        "fuzzy_ok": fuzzy.get("ok") if isinstance(fuzzy, dict) else None,
        "fuzzy_mode": fuzzy.get("ecc_mode") if isinstance(fuzzy, dict) else None,
        "failure_reasons": result.get("failure_reasons") or [],
        "ui_version": payload.get("ui_version", "seed-enrollment-codefreeze"),
        "user_agent": request.headers.get("User-Agent", ""),
    }
    if supabase:
        vid = _insert_supabase(VERIFICATION_TABLE, row)
        return {"storage": "supabase", "id": vid}
    path = _save_local("verifications", row)
    return {"storage": "local", "path": path}


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
        "public_salt": result.get("public_salt"),
        "ui_version": payload.get("ui_version", "seed-enrollment-v1"),
        "notes": payload.get("notes", ""),
        "user_agent": request.headers.get("User-Agent", ""),
    }

    if supabase:
        try:
            res = supabase.table(ENROLLMENT_TABLE).insert(row).execute()
            return jsonify({"ok": True, "storage": "supabase", "id": (res.data or [{}])[0].get("id")})
        except Exception as exc:
            return jsonify({"error": f"Supabase insert failed: {exc}"}), 500

    local_path = _save_local("enrollments", row)
    return jsonify({"ok": True, "storage": "local", "path": local_path})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
