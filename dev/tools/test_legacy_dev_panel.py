#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app as dev_app

rules = {rule.rule for rule in dev_app.app.url_map.iter_rules()}
required_rules = {
    "/api/dev/datasets",
    "/api/dev/enrollments",
    "/api/dev/enrollments/<enrollment_id>",
    "/api/dev/verifications/<verification_id>",
}
missing = required_rules - rules
assert not missing, f"Missing routes: {sorted(missing)}"

assert dev_app.LEGACY_ENROLLMENT_TABLE == "drawing_seed_enrollments"
assert dev_app.LEGACY_VERIFICATION_TABLE == "drawing_seed_verifications"

html = (ROOT / "static" / "dev.html").read_text(encoding="utf-8")
js = (ROOT / "static" / "dev.js").read_text(encoding="utf-8")
assert 'id="datasetSelect"' in html
assert 'id="verificationFilter"' in html
assert "Legacy cleanup mode" in js
assert "datasetUrl(" in js

print("Legacy dev-panel static and route checks passed")
