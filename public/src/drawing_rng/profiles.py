from __future__ import annotations

from typing import Any, Dict

PROFILES: Dict[str, Dict[str, Any]] = {
    "strict": {
        "resample_spacing": 0.035,
        "direction_buckets": 16,
        "length_buckets": {"short_max": 0.12, "medium_max": 0.28},
        "zone_grid": 4,
        "order_mode": "drawn",
        "min_stroke_points": 2,
        "min_raw_stroke_length": 5.0,
        "min_normalized_stroke_length": 0.020,
        "jitter_run_max": 0,
        "simplify_epsilon": 0.005,
        "include_turn_tokens": True,
        "include_turn_magnitude": True,
        "include_start_zone": True,
        "include_penup_moves": True,
        "include_closed_tokens": True,
        "include_relation_tokens": True,
        "close_threshold": 0.075,
        "round_normalized": 4,
    },
    "balanced": {
        "resample_spacing": 0.05,
        "direction_buckets": 8,
        "length_buckets": {"short_max": 0.18, "medium_max": 0.40},
        "zone_grid": 3,
        "order_mode": "spatial",
        "min_stroke_points": 2,
        "min_raw_stroke_length": 5.0,
        "min_normalized_stroke_length": 0.035,
        "jitter_run_max": 1,
        "simplify_epsilon": 0.015,
        "include_turn_tokens": True,
        "include_turn_magnitude": False,
        "include_start_zone": True,
        "include_penup_moves": True,
        "include_closed_tokens": True,
        "include_relation_tokens": True,
        "close_threshold": 0.075,
        "round_normalized": 4,
    },
    "tolerant": {
        "resample_spacing": 0.08,
        "direction_buckets": 4,
        "length_buckets": {"short_max": 0.25, "medium_max": 0.60},
        "zone_grid": 2,
        "order_mode": "spatial",
        "min_stroke_points": 2,
        "min_raw_stroke_length": 5.0,
        "min_normalized_stroke_length": 0.050,
        "jitter_run_max": 2,
        "simplify_epsilon": 0.030,
        "include_turn_tokens": True,
        "include_turn_magnitude": False,
        "include_start_zone": True,
        "include_penup_moves": True,
        "include_closed_tokens": True,
        "include_relation_tokens": True,
        "close_threshold": 0.075,
        "round_normalized": 4,
    },
}

DEFAULT_PROFILE = "balanced"


def get_profile(name: str | None) -> Dict[str, Any]:
    if not name:
        return dict(PROFILES[DEFAULT_PROFILE])
    if name not in PROFILES:
        raise ValueError(f"Unknown profile: {name}. Valid: {', '.join(PROFILES)}")
    return dict(PROFILES[name])
