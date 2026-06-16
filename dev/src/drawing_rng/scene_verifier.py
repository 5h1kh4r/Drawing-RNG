from __future__ import annotations

"""Complex-scene verifier for Drawing-RNG.

The original verifier is deliberately strict for simple symbols: stroke count,
component topology, and stroke-level shape are treated as hard gates.  That is
correct for stars, triangles, initials, and other low-stroke symbols, but it is
brittle for remembered *scenes* such as "sun + boat + person" where a legitimate
redraw may add/remove decorative rays or split the hull into a different number
of micro-strokes.

This module adds a second abstraction for high-stroke drawings:

    raw strokes -> geometric clusters / parts -> coarse scene layout + raster

It never derives secret material.  It only produces diagnostic/verification
features.  The goal is to tolerate micro-stroke variance while still requiring
that the same major parts appear in roughly the same locations.
"""

import math
from itertools import permutations
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .stroke_token_encoder import (
    EncoderParams,
    Stroke,
    bounding_box,
    clean_strokes,
    filter_tiny_strokes,
    is_closed,
    normalize_strokes,
    params_from_dict,
    stroke_box,
)

Point = Tuple[float, float]
Box = Tuple[float, float, float, float]


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _distance(a: Point, b: Point) -> float:
    return math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1]))


def _path_length(stroke: Stroke) -> float:
    return sum(_distance(stroke[i - 1], stroke[i]) for i in range(1, len(stroke)))


def _bbox_area(box: Box) -> float:
    x0, y0, x1, y1 = box
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _bbox_union(boxes: Sequence[Box]) -> Box:
    if not boxes:
        return (0.0, 0.0, 0.0, 0.0)
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _bbox_overlap_ratio(a: Box, b: Box) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = ix * iy
    denom = max(min(_bbox_area(a), _bbox_area(b)), 1e-12)
    return inter / denom


def _bbox_gap(a: Box, b: Box) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    gx = max(0.0, max(ax0, bx0) - min(ax1, bx1))
    gy = max(0.0, max(ay0, by0) - min(ay1, by1))
    return math.hypot(gx, gy)


def _box_center(box: Box) -> Point:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _ratio_similarity(a: float, b: float, tolerance: float = 0.80) -> float:
    if a <= 1e-9 and b <= 1e-9:
        return 1.0
    if a <= 1e-9 or b <= 1e-9:
        return 0.0
    return _clamp01(1.0 - abs(math.log(a / b)) / tolerance)


def _point_segment_distance(p: Point, a: Point, b: Point) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx = bx - ax
    dy = by - ay
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        return _distance(p, a)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return _distance(p, (ax + t * dx, ay + t * dy))


def _stroke_min_distance(a: Stroke, b: Stroke) -> float:
    if not a or not b:
        return 1.0
    # Cheap endpoint/vertex-to-segment distance. Good enough for grouping.
    best = 1e9
    if len(a) == 1 or len(b) == 1:
        return min(_distance(pa, pb) for pa in a for pb in b)
    for p in a[:: max(1, len(a) // 12)]:
        for j in range(1, len(b)):
            best = min(best, _point_segment_distance(p, b[j - 1], b[j]))
    for p in b[:: max(1, len(b) // 12)]:
        for i in range(1, len(a)):
            best = min(best, _point_segment_distance(p, a[i - 1], a[i]))
    return float(best if best < 1e9 else 1.0)


def _as_strokes(raw_strokes: Sequence[Sequence[Sequence[Any]]], params: EncoderParams) -> List[Stroke]:
    clean = clean_strokes(
        raw_strokes,
        min_points=params.min_stroke_points,
        min_raw_length=params.min_raw_stroke_length,
    )
    if not clean:
        return []
    normalized_all = normalize_strokes(clean, round_digits=params.round_normalized)
    normalized, _dropped = filter_tiny_strokes(normalized_all, params.min_normalized_stroke_length)
    # Complex-scene mode is intentionally order-invariant.  Do not sort here;
    # clustering and assignment work from spatial features.
    return normalized


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra = self.find(a)
        rb = self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _stroke_feature(stroke: Stroke, idx: int, params: EncoderParams) -> Dict[str, Any]:
    box = stroke_box(stroke)
    x0, y0, x1, y1 = box
    w = max(x1 - x0, 1e-9)
    h = max(y1 - y0, 1e-9)
    closed = is_closed(stroke, params.close_threshold)
    return {
        "idx": idx,
        "bbox": list(box),
        "center": list(_box_center(box)),
        "width": float(w),
        "height": float(h),
        "area": float(_bbox_area(box)),
        "length": float(_path_length(stroke)),
        "closed": bool(closed),
        "aspect": float(w / max(h, 1e-9)),
        "points": [[float(x), float(y)] for x, y in stroke],
    }


def _should_link(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    box_a = tuple(a["bbox"])  # type: ignore[arg-type]
    box_b = tuple(b["bbox"])  # type: ignore[arg-type]
    overlap = _bbox_overlap_ratio(box_a, box_b)
    gap = _bbox_gap(box_a, box_b)
    ca = tuple(a["center"])  # type: ignore[arg-type]
    cb = tuple(b["center"])  # type: ignore[arg-type]
    center_dist = _distance(ca, cb)
    min_dim = max(0.015, min(float(a["width"]), float(a["height"]), float(b["width"]), float(b["height"])))
    min_stroke_dist = _stroke_min_distance(
        [(float(x), float(y)) for x, y in a.get("points") or []],
        [(float(x), float(y)) for x, y in b.get("points") or []],
    )

    # Same visual part: overlapping/touching strokes, or very close bboxes.
    if overlap >= 0.12:
        return True
    if min_stroke_dist <= 0.040:
        return True
    if gap <= 0.035 and center_dist <= 0.32:
        return True

    # Radial/decorative strokes around a closed loop, e.g. sun rays.
    for closed_item, other in ((a, b), (b, a)):
        if not bool(closed_item.get("closed")):
            continue
        closed_box = tuple(closed_item["bbox"])  # type: ignore[arg-type]
        other_box = tuple(other["bbox"])  # type: ignore[arg-type]
        cc = tuple(closed_item["center"])  # type: ignore[arg-type]
        oc = tuple(other["center"])  # type: ignore[arg-type]
        closed_radius = 0.5 * max(float(closed_item["width"]), float(closed_item["height"]))
        other_len = float(other.get("length", 0.0))
        # Short strokes close to a loop are likely rays/decoration belonging to it.
        if other_len <= 0.30 and _bbox_gap(closed_box, other_box) <= max(0.09, closed_radius * 0.65):
            if _distance(cc, oc) <= closed_radius + 0.24:
                return True

    # Tiny strokes very close to a larger part should not create a new required object.
    if min(float(a.get("area", 0.0)), float(b.get("area", 0.0))) <= 0.0035:
        if gap <= 0.07 or min_stroke_dist <= 0.065:
            return True

    return False


def _cluster_strokes(strokes: List[Stroke], params: EncoderParams) -> List[Dict[str, Any]]:
    features = [_stroke_feature(s, i, params) for i, s in enumerate(strokes)]
    if not features:
        return []
    uf = _UnionFind(len(features))
    for i in range(len(features)):
        for j in range(i + 1, len(features)):
            if _should_link(features[i], features[j]):
                uf.union(i, j)

    groups: Dict[int, List[Dict[str, Any]]] = {}
    for i, feat in enumerate(features):
        groups.setdefault(uf.find(i), []).append(feat)

    clusters: List[Dict[str, Any]] = []
    total_length = sum(float(f.get("length", 0.0)) for f in features) or 1.0
    total_area = sum(float(f.get("area", 0.0)) for f in features) or 1.0
    for cluster_idx, items in enumerate(groups.values()):
        boxes = [tuple(x["bbox"]) for x in items]  # type: ignore[list-item]
        box = _bbox_union(boxes)
        length = sum(float(x.get("length", 0.0)) for x in items)
        area = _bbox_area(box)
        closed_count = sum(1 for x in items if bool(x.get("closed")))
        points_count = sum(len(x.get("points") or []) for x in items)
        w = max(box[2] - box[0], 1e-9)
        h = max(box[3] - box[1], 1e-9)
        # Major parts are things that carry noticeable ink/extent. Decorative
        # rays remain part of the sun cluster, not independent parts.
        major = (length / total_length >= 0.075) or (area >= 0.018) or (len(items) >= 2 and area >= 0.010)
        clusters.append({
            "cluster_id": cluster_idx,
            "stroke_indices": [int(x["idx"]) for x in items],
            "stroke_count": len(items),
            "bbox": [float(v) for v in box],
            "center": [float(v) for v in _box_center(box)],
            "width": float(w),
            "height": float(h),
            "area": float(area),
            "ink_length": float(length),
            "ink_fraction": float(length / total_length),
            "area_fraction": float(area / max(1e-9, _bbox_area(_bbox_union([tuple(f["bbox"]) for f in features])))),
            "closed_count": int(closed_count),
            "has_closed": bool(closed_count > 0),
            "aspect": float(w / max(h, 1e-9)),
            "points_count": int(points_count),
            "is_major": bool(major),
        })

    clusters.sort(key=lambda c: (-int(c.get("is_major", False)), c["center"][1], c["center"][0]))
    for i, c in enumerate(clusters):
        c["cluster_id"] = i
    return clusters


def _rasterize(strokes: List[Stroke], size: int = 48, dilation: int = 1) -> List[int]:
    grid = [[0 for _ in range(size)] for _ in range(size)]
    if not strokes:
        return [0 for _ in range(size * size)]
    x0, y0, x1, y1 = bounding_box(strokes)
    span = max(x1 - x0, y1 - y0, 1e-9)
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0

    def to_cell(p: Point) -> Tuple[int, int]:
        # normalize into [0, 1] from the drawing's own bounding square.
        nx = (float(p[0]) - cx) / span + 0.5
        ny = (float(p[1]) - cy) / span + 0.5
        x = max(0, min(size - 1, int(round(nx * (size - 1)))))
        y = max(0, min(size - 1, int(round(ny * (size - 1)))))
        return x, y

    def mark(x: int, y: int) -> None:
        for dy in range(-dilation, dilation + 1):
            for dx in range(-dilation, dilation + 1):
                xx = x + dx
                yy = y + dy
                if 0 <= xx < size and 0 <= yy < size:
                    grid[yy][xx] = 1

    for stroke in strokes:
        if not stroke:
            continue
        if len(stroke) == 1:
            x, y = to_cell(stroke[0])
            mark(x, y)
            continue
        for i in range(1, len(stroke)):
            x0, y0 = to_cell(stroke[i - 1])
            x1, y1 = to_cell(stroke[i])
            steps = max(abs(x1 - x0), abs(y1 - y0), 1)
            for s in range(steps + 1):
                t = s / steps
                mark(int(round(x0 + t * (x1 - x0))), int(round(y0 + t * (y1 - y0))))
    return [grid[y][x] for y in range(size) for x in range(size)]


def _raster_jaccard(a: Sequence[int], b: Sequence[int]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    inter = sum(1 for i in range(n) if a[i] and b[i])
    union = sum(1 for i in range(n) if a[i] or b[i])
    if union == 0:
        return 1.0
    return inter / union


def _ink_points_from_raster(bits: Sequence[int], size: int = 48) -> List[Point]:
    return [(float(i % size), float(i // size)) for i, v in enumerate(bits[: size * size]) if v]


def _mean_nearest_distance(pa: List[Point], pb: List[Point]) -> float:
    if not pa and not pb:
        return 0.0
    if not pa or not pb:
        return float("inf")
    # Cap sampling for speed; deterministic stride keeps results stable.
    stride = max(1, len(pa) // 220)
    aa = pa[::stride]
    total = 0.0
    for p in aa:
        total += min(_distance(p, q) for q in pb)
    return total / max(1, len(aa))


def _chamfer_similarity(bits_a: Sequence[int], bits_b: Sequence[int], size: int = 48) -> float:
    pa = _ink_points_from_raster(bits_a, size)
    pb = _ink_points_from_raster(bits_b, size)
    if not pa and not pb:
        return 1.0
    if not pa or not pb:
        return 0.0
    d = 0.5 * (_mean_nearest_distance(pa, pb) + _mean_nearest_distance(pb, pa))
    # About 4 grid cells average distance is already visibly different.
    return _clamp01(1.0 - d / 4.5)


def extract_scene_signature(raw_strokes: Sequence[Sequence[Sequence[Any]]], params_raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    params = params_from_dict(params_raw or {})
    strokes = _as_strokes(raw_strokes, params)
    if not strokes:
        return {
            "stroke_count": 0,
            "clusters": [],
            "major_clusters": [],
            "complexity_class": "empty",
            "raster_size": 48,
            "raster": [],
        }
    clusters = _cluster_strokes(strokes, params)
    major_clusters = [c for c in clusters if c.get("is_major")]
    raster = _rasterize(strokes, size=48, dilation=1)
    drawing_box = bounding_box(strokes)
    layout_entropy = _layout_entropy(major_clusters)
    complexity_class = classify_scene_signature({
        "stroke_count": len(strokes),
        "clusters": clusters,
        "major_clusters": major_clusters,
        "layout_entropy": layout_entropy,
    })
    return {
        "stroke_count": len(strokes),
        "drawing_box": [float(v) for v in drawing_box],
        "clusters": clusters,
        "major_clusters": major_clusters,
        "major_cluster_count": len(major_clusters),
        "layout_entropy": float(layout_entropy),
        "complexity_class": complexity_class,
        "raster_size": 48,
        "raster": raster,
    }


def scene_signature_from_geometry(geometry: Dict[str, Any]) -> Dict[str, Any]:
    """Build a coarse scene signature from an existing geometry signature.

    This keeps old enrollment records usable before they are re-enrolled with a
    first-class scene model.  It cannot recover raster data perfectly, but it
    can still provide cluster count/layout information from stored components.
    """
    comps = geometry.get("components") or []
    clusters: List[Dict[str, Any]] = []
    if not comps:
        return {"stroke_count": 0, "clusters": [], "major_clusters": [], "complexity_class": "empty", "raster_size": 48, "raster": []}
    total_length = sum(float((c.get("curvature") or {}).get("path_length", 0.0)) for c in comps) or 1.0
    boxes: List[Box] = []
    for idx, c in enumerate(comps):
        box = tuple(float(x) for x in (c.get("bbox") or [0, 0, 0, 0]))  # type: ignore[assignment]
        boxes.append(box)
        length = float((c.get("curvature") or {}).get("path_length", 0.0))
        area = _bbox_area(box)
        major = (length / total_length >= 0.075) or area >= 0.018
        clusters.append({
            "cluster_id": idx,
            "stroke_indices": [idx],
            "stroke_count": 1,
            "bbox": [float(v) for v in box],
            "center": [float(v) for v in _box_center(box)],
            "width": float(max(box[2] - box[0], 1e-9)),
            "height": float(max(box[3] - box[1], 1e-9)),
            "area": float(area),
            "ink_length": float(length),
            "ink_fraction": float(length / total_length),
            "closed_count": 1 if bool(c.get("closed")) else 0,
            "has_closed": bool(c.get("closed")),
            "aspect": float(max(box[2] - box[0], 1e-9) / max(box[3] - box[1], 1e-9)),
            "is_major": bool(major),
        })
    # Raster fallback from stored normalized points.
    strokes: List[Stroke] = []
    for c in comps:
        pts = c.get("points_norm") or c.get("points_global") or []
        stroke = [(float(x), float(y)) for x, y in pts]
        if stroke:
            strokes.append(stroke)
    raster = _rasterize(strokes, size=48, dilation=1) if strokes else []
    major_clusters = [c for c in clusters if c.get("is_major")]
    layout_entropy = _layout_entropy(major_clusters)
    sig = {
        "stroke_count": int(geometry.get("stroke_count", len(comps))),
        "drawing_box": geometry.get("drawing_box") or [float(v) for v in _bbox_union(boxes)],
        "clusters": clusters,
        "major_clusters": major_clusters,
        "major_cluster_count": len(major_clusters),
        "layout_entropy": float(layout_entropy),
        "raster_size": 48,
        "raster": raster,
    }
    sig["complexity_class"] = classify_scene_signature(sig)
    return sig


def _layout_entropy(major_clusters: Sequence[Dict[str, Any]]) -> float:
    if len(major_clusters) < 2:
        return 0.0
    # Counts occupied thirds of the canvas. Multi-object scenes spread across
    # several grid zones; single dense symbols do not.
    cells = set()
    for c in major_clusters:
        x, y = c.get("center") or [0.0, 0.0]
        # geometry/stroke signatures are centered around roughly [-0.5, 0.5].
        nx = float(x) + 0.5
        ny = float(y) + 0.5
        gx = max(0, min(2, int(nx * 3)))
        gy = max(0, min(2, int(ny * 3)))
        cells.add((gx, gy))
    return len(cells) / 9.0


def classify_scene_signature(sig: Dict[str, Any]) -> str:
    stroke_count = int(sig.get("stroke_count", 0))
    major_count = int(sig.get("major_cluster_count", len(sig.get("major_clusters") or [])))
    layout_entropy = float(sig.get("layout_entropy", 0.0))
    # A flower may have many strokes but one major central cluster; keep it in
    # multi_stroke_symbol mode.  A scene has many strokes *and* multiple spatial
    # parts.
    if stroke_count >= 9 and major_count >= 2 and layout_entropy >= 0.18:
        return "complex_scene"
    if stroke_count >= 6 and major_count >= 3:
        return "complex_scene"
    if stroke_count >= 5:
        return "multi_stroke_symbol"
    return "simple_symbol"


def _cluster_match_score(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    ax, ay = a.get("center") or [0.0, 0.0]
    bx, by = b.get("center") or [0.0, 0.0]
    center_score = _clamp01(1.0 - math.hypot(float(ax) - float(bx), float(ay) - float(by)) / 0.42)
    width_score = _ratio_similarity(float(a.get("width", 0.0)), float(b.get("width", 0.0)), tolerance=0.90)
    height_score = _ratio_similarity(float(a.get("height", 0.0)), float(b.get("height", 0.0)), tolerance=0.90)
    area_score = _ratio_similarity(float(a.get("area", 0.0)), float(b.get("area", 0.0)), tolerance=1.25)
    ink_score = _ratio_similarity(float(a.get("ink_fraction", 0.0)), float(b.get("ink_fraction", 0.0)), tolerance=1.20)
    closed_score = 1.0 if bool(a.get("has_closed")) == bool(b.get("has_closed")) else 0.55
    aspect_score = _ratio_similarity(float(a.get("aspect", 1.0)), float(b.get("aspect", 1.0)), tolerance=1.25)
    return _clamp01(
        0.36 * center_score +
        0.14 * width_score +
        0.14 * height_score +
        0.10 * area_score +
        0.09 * ink_score +
        0.10 * aspect_score +
        0.07 * closed_score
    )


def _best_assignment(a_clusters: Sequence[Dict[str, Any]], b_clusters: Sequence[Dict[str, Any]]) -> Tuple[float, List[Dict[str, Any]]]:
    if not a_clusters or not b_clusters:
        return (1.0 if not a_clusters and not b_clusters else 0.0, [])
    n = len(a_clusters)
    m = len(b_clusters)
    k = min(n, m)
    # Small cluster counts are expected. Exact permutations make the assignment
    # deterministic and avoid bringing in scipy. Fall back to greedy for large n.
    best_score = -1.0
    best_pairs: List[Dict[str, Any]] = []
    if max(n, m) <= 7:
        if n <= m:
            for perm in permutations(range(m), k):
                pairs = []
                total = 0.0
                for i, j in enumerate(perm):
                    s = _cluster_match_score(a_clusters[i], b_clusters[j])
                    total += s
                    pairs.append({"a": int(a_clusters[i].get("cluster_id", i)), "b": int(b_clusters[j].get("cluster_id", j)), "score": float(s)})
                score = total / k
                if score > best_score:
                    best_score, best_pairs = score, pairs
        else:
            for perm in permutations(range(n), k):
                pairs = []
                total = 0.0
                for j, i in enumerate(perm):
                    s = _cluster_match_score(a_clusters[i], b_clusters[j])
                    total += s
                    pairs.append({"a": int(a_clusters[i].get("cluster_id", i)), "b": int(b_clusters[j].get("cluster_id", j)), "score": float(s)})
                score = total / k
                if score > best_score:
                    best_score, best_pairs = score, pairs
    else:
        unused = set(range(m))
        pairs = []
        total = 0.0
        for i, ca in enumerate(a_clusters):
            if not unused:
                break
            j = max(unused, key=lambda jj: _cluster_match_score(ca, b_clusters[jj]))
            unused.remove(j)
            s = _cluster_match_score(ca, b_clusters[j])
            total += s
            pairs.append({"a": int(ca.get("cluster_id", i)), "b": int(b_clusters[j].get("cluster_id", j)), "score": float(s)})
        best_score = total / max(1, len(pairs))
        best_pairs = pairs
    count_penalty = _clamp01(1.0 - abs(n - m) / max(n, m, 1))
    # Keep the count term soft. Redraws may split a hull or add one sun ray.
    return _clamp01(0.78 * best_score + 0.22 * count_penalty), best_pairs


def _relation_layout_score(a_clusters: Sequence[Dict[str, Any]], b_clusters: Sequence[Dict[str, Any]], pairs: Sequence[Dict[str, Any]]) -> float:
    if len(pairs) < 2:
        return 1.0
    a_by_id = {int(c.get("cluster_id", i)): c for i, c in enumerate(a_clusters)}
    b_by_id = {int(c.get("cluster_id", i)): c for i, c in enumerate(b_clusters)}
    scores = []
    for i in range(len(pairs)):
        for j in range(i + 1, len(pairs)):
            ai = a_by_id.get(int(pairs[i]["a"]))
            aj = a_by_id.get(int(pairs[j]["a"]))
            bi = b_by_id.get(int(pairs[i]["b"]))
            bj = b_by_id.get(int(pairs[j]["b"]))
            if not ai or not aj or not bi or not bj:
                continue
            aci = ai.get("center") or [0.0, 0.0]
            acj = aj.get("center") or [0.0, 0.0]
            bci = bi.get("center") or [0.0, 0.0]
            bcj = bj.get("center") or [0.0, 0.0]
            adx = float(acj[0]) - float(aci[0])
            ady = float(acj[1]) - float(aci[1])
            bdx = float(bcj[0]) - float(bci[0])
            bdy = float(bcj[1]) - float(bci[1])
            dist_score = _ratio_similarity(math.hypot(adx, ady), math.hypot(bdx, bdy), tolerance=1.0)
            angle_a = math.atan2(ady, adx)
            angle_b = math.atan2(bdy, bdx)
            angle_diff = abs((angle_b - angle_a + math.pi) % (2 * math.pi) - math.pi)
            angle_score = _clamp01(1.0 - angle_diff / math.pi)
            coarse_score = 1.0
            if (adx > 0.08) != (bdx > 0.08) and abs(adx) > 0.08 and abs(bdx) > 0.08:
                coarse_score -= 0.25
            if (ady > 0.08) != (bdy > 0.08) and abs(ady) > 0.08 and abs(bdy) > 0.08:
                coarse_score -= 0.25
            scores.append(_clamp01(0.45 * dist_score + 0.35 * angle_score + 0.20 * coarse_score))
    return _clamp01(sum(scores) / len(scores)) if scores else 1.0


def compare_scene_signatures(canonical: Dict[str, Any], redraw: Dict[str, Any]) -> Dict[str, Any]:
    major_a = canonical.get("major_clusters") or []
    major_b = redraw.get("major_clusters") or []
    assignment_score, pairs = _best_assignment(major_a, major_b)
    relation_score = _relation_layout_score(major_a, major_b, pairs)
    raster_a = canonical.get("raster") or []
    raster_b = redraw.get("raster") or []
    raster_j = _raster_jaccard(raster_a, raster_b) if raster_a and raster_b else 0.0
    chamfer = _chamfer_similarity(raster_a, raster_b, int(canonical.get("raster_size", 48))) if raster_a and raster_b else 0.0
    raster_score = _clamp01(0.38 * raster_j + 0.62 * chamfer) if raster_a and raster_b else assignment_score

    n = len(major_a)
    m = len(major_b)
    missing_major = max(0, n - m)
    extra_major = max(0, m - n)
    count_score = _clamp01(1.0 - abs(n - m) / max(n, m, 1))

    # Complex scene final score prioritizes macro evidence.  Assignment tells us
    # whether the same required parts exist; raster/chamfer tells us whether ink
    # landed in roughly the same places; relations preserve the remembered scene.
    final = _clamp01(0.38 * assignment_score + 0.27 * raster_score + 0.22 * relation_score + 0.13 * count_score)

    return {
        "scene_final": final,
        "scene_assignment": assignment_score,
        "scene_relation": relation_score,
        "scene_raster": raster_score,
        "scene_raster_jaccard": raster_j,
        "scene_chamfer": chamfer,
        "scene_count": count_score,
        "canonical_major_cluster_count": n,
        "redraw_major_cluster_count": m,
        "missing_major_parts": missing_major,
        "extra_major_parts": extra_major,
        "matched_parts": pairs,
        "canonical_complexity_class": canonical.get("complexity_class"),
        "redraw_complexity_class": redraw.get("complexity_class"),
    }


def learn_scene_model(attempts: Sequence[Dict[str, Any]], profile_params: Dict[str, Any], central_attempt_index: int = 1) -> Dict[str, Any]:
    """Learn a stable part-level model from enrollment attempts."""
    scenes = [extract_scene_signature(a.get("strokes", []), profile_params) for a in attempts]
    if not scenes:
        return {"complexity_class": "empty", "canonical_scene": {}, "scene_pair_scores": []}
    central_idx = max(0, min(len(scenes) - 1, int(central_attempt_index) - 1))
    canonical = scenes[central_idx]
    pair_scores = []
    for i in range(len(scenes)):
        for j in range(i + 1, len(scenes)):
            pair_scores.append(compare_scene_signatures(scenes[i], scenes[j]).get("scene_final", 0.0))
    complex_votes = sum(1 for s in scenes if s.get("complexity_class") == "complex_scene")
    complexity = "complex_scene" if complex_votes >= max(1, len(scenes) // 2) else canonical.get("complexity_class", "simple_symbol")
    return {
        "complexity_class": complexity,
        "canonical_scene": canonical,
        # Store all enrollment scene signatures. Verification uses the best
        # matching reference, not just one medoid, which is essential for complex
        # scenes where the user may split/merge strokes differently across
        # legitimate enrollment attempts.
        "reference_scenes": scenes,
        "scene_pair_scores": [float(x) for x in pair_scores],
        "scene_stability_score": float(median(pair_scores) if pair_scores else 0.0),
        "enrollment_major_cluster_counts": [int(s.get("major_cluster_count", 0)) for s in scenes],
    }


def compare_scene_model(scene_model: Dict[str, Any], redraw: Dict[str, Any]) -> Dict[str, Any]:
    """Compare a redraw against all enrollment scene references and return the best match.

    Complex drawings are often stable at the *scene* level but unstable at the
    exact micro-stroke level.  A single canonical enrollment attempt can be a
    poor representative: e.g. the boat/person/sun may be drawn with slightly
    different stroke splits in attempt 1 vs attempt 3.  This helper compares the
    redraw against every stored enrollment scene signature and picks the best
    reference.  It does not loosen thresholds by itself; it only avoids false
    rejects caused by choosing the wrong canonical scene exemplar.
    """
    references = []
    if isinstance(scene_model, dict):
        for ref in scene_model.get("reference_scenes") or []:
            if isinstance(ref, dict):
                references.append(ref)
        canonical = scene_model.get("canonical_scene")
        if isinstance(canonical, dict):
            # Keep canonical first if it is not already included by identity/content.
            references.insert(0, canonical)
    if not references:
        return compare_scene_signatures({}, redraw)

    best: Optional[Dict[str, Any]] = None
    best_idx = 0
    for idx, ref in enumerate(references):
        scores = compare_scene_signatures(ref, redraw)
        # Prefer the scene score, but break near-ties using assignment/raster so
        # stable visual matches win over accidental count matches.
        rank = (
            float(scores.get("scene_final", 0.0)),
            float(scores.get("scene_assignment", 0.0)),
            float(scores.get("scene_raster", 0.0)),
        )
        if best is None or rank > (
            float(best.get("scene_final", 0.0)),
            float(best.get("scene_assignment", 0.0)),
            float(best.get("scene_raster", 0.0)),
        ):
            best = scores
            best_idx = idx
    assert best is not None
    best["best_reference_index"] = best_idx
    best["reference_scene_count"] = len(references)
    return best


def scene_thresholds(profile: str) -> Dict[str, float]:
    # Phase 2.11: complex-scene mode is a *rescue path* for high-stroke owner
    # variance, not a global bypass around geometry.  The previous 2.10 raster
    # threshold was intentionally permissive (0.40-0.44) and allowed copied or
    # loosely similar scenes to pass if the cluster assignment looked good.
    # These thresholds keep owner redraws that preserve macro ink placement,
    # while rejecting low-raster / low-layout informed forgeries.
    if profile == "tolerant":
        return {
            "scene_final": 0.74,
            "scene_assignment": 0.68,
            "scene_raster": 0.56,
            "scene_relation": 0.56,
        }
    if profile == "strict":
        return {
            "scene_final": 0.72,
            "scene_assignment": 0.66,
            "scene_raster": 0.54,
            "scene_relation": 0.54,
        }
    return {
        "scene_final": 0.73,
        "scene_assignment": 0.67,
        "scene_raster": 0.55,
        "scene_relation": 0.55,
    }


def scene_failure_reasons(scores: Dict[str, Any], profile: str) -> List[str]:
    th = scene_thresholds(profile)
    reasons: List[str] = []
    for key, val in th.items():
        if float(scores.get(key, 0.0)) < val:
            reasons.append(f"{key}_below_{val:.2f}")

    canonical_count = int(scores.get("canonical_major_cluster_count", 0))
    redraw_count = int(scores.get("redraw_major_cluster_count", 0))
    missing = int(scores.get("missing_major_parts", 0))
    extra = int(scores.get("extra_major_parts", 0))
    raster = float(scores.get("scene_raster", 0.0))
    assignment = float(scores.get("scene_assignment", 0.0))

    # If a model is being treated as a complex scene, both sides must expose at
    # least two major parts. Otherwise the relation score can become trivially
    # 1.0 because there are not enough pairs to compare. This was visible in UI
    # logs where scene_relation stayed 1.000 even for suspicious attempts.
    if max(canonical_count, redraw_count) >= 2 and min(canonical_count, redraw_count) < 2:
        reasons.append("complex_scene_cluster_collapse")

    # Do not allow the scene path to hide missing/new major objects. One count
    # mismatch can be a clustering artifact, so only hard-fail it when the visual
    # evidence is not very strong. Two or more is always dangerous.
    if missing >= 2:
        reasons.append("multiple_major_parts_missing")
    elif missing == 1 and (raster < 0.65 or assignment < 0.76):
        reasons.append("required_major_part_missing")

    if extra >= 2:
        reasons.append("multiple_new_major_parts")
    elif extra == 1 and raster < 0.65:
        reasons.append("new_major_part_added")

    return reasons


def is_complex_scene_model(scene_model: Optional[Dict[str, Any]], canonical_geometry: Optional[Dict[str, Any]] = None) -> bool:
    if isinstance(scene_model, dict) and scene_model.get("complexity_class") == "complex_scene":
        return True
    if isinstance(scene_model, dict):
        canonical = scene_model.get("canonical_scene") or {}
        if canonical.get("complexity_class") == "complex_scene":
            return True
    if isinstance(canonical_geometry, dict):
        return scene_signature_from_geometry(canonical_geometry).get("complexity_class") == "complex_scene"
    return False
