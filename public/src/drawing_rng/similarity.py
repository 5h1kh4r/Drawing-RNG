from __future__ import annotations

import math
from collections import Counter
from typing import Dict, List, Sequence, Tuple

_DIRECTIONS_16 = [
    "E", "ENE", "NE", "NNE",
    "N", "NNW", "NW", "WNW",
    "W", "WSW", "SW", "SSW",
    "S", "SSE", "SE", "ESE",
]
_DIRECTION_INDEX = {label: idx for idx, label in enumerate(_DIRECTIONS_16)}
_LENGTH_ORDER = {"S": 0, "M": 1, "L": 2}


def edit_distance(a: List[str], b: List[str]) -> int:
    """Classic unit-cost Levenshtein distance, retained for baseline metrics."""
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i]
        for j, cb in enumerate(b, start=1):
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (0 if ca == cb else 1)))
        prev = curr
    return prev[-1]


def token_similarity(a: List[str], b: List[str]) -> float:
    """Unit-cost edit similarity used by the original verifier."""
    denom = max(len(a), len(b), 1)
    return max(0.0, 1.0 - edit_distance(a, b) / denom)


def token_kind(token: str) -> str:
    if token == "END":
        return "end"
    if token == "CLOSED" or token == "S" or token.startswith("S@"):
        return "structure"
    if token == "PU" or token.startswith("PU_"):
        return "penup"
    if token.startswith("REL_"):
        return "relation"
    if token in {"TR", "TL", "TU", "TS"} or token.startswith(("TR_", "TL_")):
        return "turn"
    return "direction"


def _parse_direction_token(token: str) -> Tuple[str | None, str | None]:
    if token_kind(token) != "direction" or "_" not in token:
        return None, None
    direction, length = token.rsplit("_", 1)
    if direction in _DIRECTION_INDEX and length in _LENGTH_ORDER:
        return direction, length
    return None, None


def _direction_delta_steps(a: str, b: str) -> int:
    ia = _DIRECTION_INDEX[a]
    ib = _DIRECTION_INDEX[b]
    raw = abs(ia - ib)
    return min(raw, len(_DIRECTIONS_16) - raw)


def _turn_parts(token: str) -> Tuple[str, str | None]:
    if "_" not in token:
        return token, None
    side, mag = token.split("_", 1)
    return side, mag


def substitution_cost(a: str, b: str) -> float:
    """Semantic substitution cost for token edits.

    Unit-cost Levenshtein treats E_M -> NE_M and E_M -> W_L as equally wrong.
    For drawing gestures, those edits are not equivalent: adjacent directions and
    nearby length buckets usually reflect human redraw variance, while opposite
    directions or topology/structure changes are much larger deviations.
    """
    if a == b:
        return 0.0

    ka = token_kind(a)
    kb = token_kind(b)
    if ka != kb:
        return 1.15

    if ka == "direction":
        da, la = _parse_direction_token(a)
        db, lb = _parse_direction_token(b)
        if da is None or db is None or la is None or lb is None:
            return 1.0
        direction_steps = _direction_delta_steps(da, db)
        angle_degrees = direction_steps * 22.5
        if direction_steps == 0:
            direction_cost = 0.0
        elif angle_degrees <= 45.0:
            direction_cost = 0.40
        else:
            direction_cost = min(1.0, angle_degrees / 180.0)
        length_delta = abs(_LENGTH_ORDER[la] - _LENGTH_ORDER[lb])
        length_cost = 0.0 if length_delta == 0 else 0.30 if length_delta == 1 else 0.45
        return min(1.0, direction_cost + length_cost)

    if ka == "turn":
        side_a, mag_a = _turn_parts(a)
        side_b, mag_b = _turn_parts(b)
        if side_a == side_b:
            if mag_a == mag_b:
                return 0.0
            return 0.35
        if {side_a, side_b} == {"TR", "TL"}:
            return 0.80
        if "TU" in {side_a, side_b}:
            return 0.95
        if "TS" in {side_a, side_b}:
            return 0.60
        return 0.85

    if ka == "penup":
        return 0.55
    if ka == "relation":
        return 0.70
    if ka == "structure":
        return 0.85
    if ka == "end":
        return 1.0
    return 1.0


def weighted_edit_distance(a: Sequence[str], b: Sequence[str], insert_delete_cost: float = 1.0) -> float:
    if not a:
        return float(len(b)) * insert_delete_cost
    if not b:
        return float(len(a)) * insert_delete_cost
    prev = [j * insert_delete_cost for j in range(len(b) + 1)]
    for i, ca in enumerate(a, start=1):
        curr = [i * insert_delete_cost]
        for j, cb in enumerate(b, start=1):
            curr.append(min(
                prev[j] + insert_delete_cost,
                curr[j - 1] + insert_delete_cost,
                prev[j - 1] + substitution_cost(ca, cb),
            ))
        prev = curr
    return float(prev[-1])


def weighted_token_similarity(a: Sequence[str], b: Sequence[str]) -> float:
    denom = max(len(a), len(b), 1)
    return max(0.0, min(1.0, 1.0 - weighted_edit_distance(a, b) / denom))


def _bigrams(tokens: Sequence[str]) -> Counter[Tuple[str, str]]:
    return Counter(zip(tokens, tokens[1:]))


def token_bigram_jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    ca = _bigrams(a)
    cb = _bigrams(b)
    if not ca and not cb:
        return 1.0
    keys = set(ca) | set(cb)
    inter = sum(min(ca[k], cb[k]) for k in keys)
    union = sum(max(ca[k], cb[k]) for k in keys)
    return inter / union if union else 1.0


def token_bigram_cosine(a: Sequence[str], b: Sequence[str]) -> float:
    ca = _bigrams(a)
    cb = _bigrams(b)
    if not ca and not cb:
        return 1.0
    if not ca or not cb:
        return 0.0
    keys = set(ca) | set(cb)
    dot = sum(ca[k] * cb[k] for k in keys)
    norm_a = math.sqrt(sum(v * v for v in ca.values()))
    norm_b = math.sqrt(sum(v * v for v in cb.values()))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def filtered_similarity(a: List[str], b: List[str], kind: str) -> float:
    return token_similarity([x for x in a if token_kind(x) == kind], [x for x in b if token_kind(x) == kind])


def filtered_weighted_similarity(a: List[str], b: List[str], kind: str) -> float:
    return weighted_token_similarity([x for x in a if token_kind(x) == kind], [x for x in b if token_kind(x) == kind])


def similarity_report(a: List[str], b: List[str]) -> Dict[str, float]:
    return {
        "overall": token_similarity(a, b),
        "overall_weighted": weighted_token_similarity(a, b),
        "bigram_jaccard": token_bigram_jaccard(a, b),
        "bigram_cosine": token_bigram_cosine(a, b),
        "direction": filtered_similarity(a, b, "direction"),
        "direction_weighted": filtered_weighted_similarity(a, b, "direction"),
        "structure": filtered_similarity(a, b, "structure"),
        "penup": filtered_similarity(a, b, "penup"),
        "relation": filtered_similarity(a, b, "relation"),
        "turn": filtered_similarity(a, b, "turn"),
        "turn_weighted": filtered_weighted_similarity(a, b, "turn"),
    }
