# Geometry Verifier v2 notes

This version fixes a major false-accept issue found during testing: a symbol like `7` could unlock a vault enrolled with `1` because the previous verifier allowed reversed open-stroke matching and did not weight open-stroke endpoint/direction structure strongly enough.

Changes:

- Open strokes no longer compare against reversed point order.
- Closed strokes still allow cyclic/reversed matching because the start point of a closed loop can be arbitrary.
- Added open-stroke endpoint similarity: start point, end point, endpoint vector angle, endpoint vector length.
- Added direction-histogram similarity.
- Added dominant-segment-count similarity.
- Increased `stroke_shape` importance for single open-stroke symbols.
- Single open-stroke cases now require a stronger shape gate.
- Frontend no longer sends a hardcoded `0.50` threshold. Backend chooses profile-aware defaults.

This is still a prototype. If a drawing is too simple, it should ideally be rejected during enrollment in Phase 3 guardrails.
