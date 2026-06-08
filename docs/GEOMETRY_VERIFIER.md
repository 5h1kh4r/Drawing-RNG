# Phase 2.5 Geometry-Aware Verification

This version strengthens vault unlock verification beyond raw stroke-token edit distance.

The old verifier compared:

```text
canonical_tokens vs redraw_tokens
```

That could accept drawings with similar stroke ingredients but different visual layout, such as rearranged circles/plus signs, or similar-looking simple glyphs like `1` and `7` under tolerant tokenization.

The new verifier compares:

```text
token similarity
+ component layout
+ pairwise component relations
+ curve/straightness features
+ local/global stroke shape
```

## New API fields

`analyze_enrollment()` now includes:

```json
{
  "canonical_tokens": [...],
  "canonical_geometry": {...},
  "geometry_stability_score": 0.0,
  "geometry_pair_scores": [...]
}
```

`verify_redraw()` now returns:

```json
{
  "accepted": false,
  "final_score": 0.0,
  "token_score": 0.0,
  "geometry_scores": {
    "count": 1.0,
    "layout": 0.0,
    "relation": 0.0,
    "curve": 0.0,
    "stroke_shape": 0.0,
    "geometry_final": 0.0
  },
  "failure_reasons": ["layout_below_0.58"]
}
```

## Why this matters

A drawing seed should not unlock just because the redraw has the same broad token ingredients. It should also preserve visual arrangement and curve/straightness behavior.

Examples this is designed to catch:

- Same circles/plus components but rearranged on the canvas.
- A straight triangle drawn as a very curvy triangle.
- Similar single-stroke glyphs such as `1` vs `7`.
- Missing or extra components.

## Current status

This is still a prototype verifier. The geometry thresholds are heuristic and should be tuned with FAR/FRR tests.
