from __future__ import annotations

from typing import Dict, Iterable, List


def edit_distance(a: List[str], b: List[str]) -> int:
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


def filtered_similarity(a: List[str], b: List[str], kind: str) -> float:
    return token_similarity([x for x in a if token_kind(x) == kind], [x for x in b if token_kind(x) == kind])


def similarity_report(a: List[str], b: List[str]) -> Dict[str, float]:
    return {
        "overall": token_similarity(a, b),
        "direction": filtered_similarity(a, b, "direction"),
        "structure": filtered_similarity(a, b, "structure"),
        "penup": filtered_similarity(a, b, "penup"),
        "relation": filtered_similarity(a, b, "relation"),
        "turn": filtered_similarity(a, b, "turn"),
    }
