from __future__ import annotations

"""
Hardened prototype fuzzy extractor / fuzzy-commitment layer for Drawing-RNG.

This version upgrades the earlier repetition-code prototype in two ways:

1. Salted feature projection:
   Drawing token/geometry features are projected through deterministic,
   user-specific random hyperplanes generated from public salt. This makes the
   sketch bits less directly tied to human drawing biases than plain feature
   hashing. It does not magically create entropy, but it improves bit balance
   and makes the helper format cleaner for evaluation.

2. BCH error-correcting code when available:
   If the optional `bchlib` package is installed, the hidden secret is encoded
   using a binary BCH code and committed with public helper data. If bchlib is
   unavailable, the module falls back to the older repetition code and marks the
   helper accordingly.

Security note:
   This is still a research prototype. Public helper data leaks information
   about the sketch/codeword relation, and this implementation has not had a
   formal leakage proof. Treat it as an experimental fuzzy-commitment layer for
   FAR/FRR testing, not as production cryptography.
"""

import base64
import hashlib
import hmac
import json
import math
import secrets
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple

try:  # optional hardened ECC backend
    import bchlib  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    bchlib = None  # type: ignore

FeatureMap = Dict[str, float]

# Locked fuzzy-extractor parameters for the CFP/evaluation freeze.
# These are intentionally fixed so FAR/FRR/EER measurements are comparable
# across runs. If bchlib cannot instantiate this BCH configuration on a
# platform, the code falls back to repetition mode and reports it explicitly.
HARDENED_BCH_M = 10
HARDENED_BCH_T = 32
HARDENED_SECRET_NBYTES = 16  # 128-bit hidden recovery secret
SOFT_DIRECTION_BOUNDARY_DEGREES = 3.0

_DIR8_LABELS = ["E", "NE", "N", "NW", "W", "SW", "S", "SE"]


def _segment_angle_deg(a: Sequence[float], b: Sequence[float]) -> float | None:
    try:
        dx = float(b[0]) - float(a[0])
        # Flip y so the labels match the token encoder's compass convention.
        dy = -(float(b[1]) - float(a[1]))
    except Exception:
        return None
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        return None
    angle = math.degrees(math.atan2(dy, dx))
    if angle < 0:
        angle += 360.0
    return angle


def _soft_direction_memberships(angle_deg: float, *, buckets: int = 8, boundary_deg: float = SOFT_DIRECTION_BOUNDARY_DEGREES) -> List[Tuple[str, float]]:
    """Return soft direction labels for a segment angle.

    The normal tokenizer is hard-quantized. If an angle lands very close to a
    direction-bucket boundary, tiny hand jitter can flip the token. For the
    fuzzy sketch only, we smooth that cliff by assigning fractional weight to
    both neighboring buckets. This keeps edit-distance tokens unchanged while
    making the fuzzy projection less brittle.
    """
    if buckets != 8:
        buckets = 8
    width = 360.0 / buckets
    # Boundaries for direction_between() are center+width/2.
    # Boundary i lies between label i and label i+1.
    best_boundary = None
    best_dist = 999.0
    for i in range(buckets):
        boundary = (width / 2.0 + i * width) % 360.0
        dist = abs((angle_deg - boundary + 180.0) % 360.0 - 180.0)
        if dist < best_dist:
            best_dist = dist
            best_boundary = i
    if best_boundary is not None and best_dist <= boundary_deg:
        left = _DIR8_LABELS[best_boundary % buckets]
        right = _DIR8_LABELS[(best_boundary + 1) % buckets]
        return [(left, 0.5), (right, 0.5)]

    idx = int((angle_deg + width / 2.0) // width) % buckets
    return [(_DIR8_LABELS[idx], 1.0)]


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash_bytes(label: str, *, digest_size: int = 32) -> bytes:
    return hashlib.blake2b(label.encode("utf-8"), digest_size=digest_size).digest()


def _hash_float(label: str) -> float:
    raw = int.from_bytes(_hash_bytes(label, digest_size=8), "big")
    return raw / float(2**64 - 1)


def _bucket(value: float, bins: int = 8) -> int:
    try:
        v = float(value)
    except Exception:
        return 0
    if v < 0:
        v = 0.0
    if v > 1:
        v = 1.0
    return max(0, min(bins - 1, int(v * bins)))


def _bits_from_bytes(data: bytes, nbits: int | None = None) -> List[int]:
    bits: List[int] = []
    for byte in data:
        for shift in range(7, -1, -1):
            bits.append((byte >> shift) & 1)
    return bits if nbits is None else bits[:nbits]


def _bytes_from_bits(bits: Sequence[int]) -> bytes:
    out = bytearray()
    for i in range(0, len(bits), 8):
        byte = 0
        chunk = list(bits[i:i + 8])
        if len(chunk) < 8:
            chunk += [0] * (8 - len(chunk))
        for bit in chunk:
            byte = (byte << 1) | (1 if bit else 0)
        out.append(byte)
    return bytes(out)


def _xor_bytes(a: bytes, b: bytes) -> bytes:
    if len(a) != len(b):
        raise ValueError("byte arrays must have equal length")
    return bytes(x ^ y for x, y in zip(a, b))


def _xor_bits(a: Sequence[int], b: Sequence[int]) -> List[int]:
    if len(a) != len(b):
        raise ValueError("bit arrays must have equal length")
    return [(1 if x else 0) ^ (1 if y else 0) for x, y in zip(a, b)]


def hamming_distance(a: Sequence[int], b: Sequence[int]) -> int:
    if len(a) != len(b):
        raise ValueError("bit arrays must have equal length")
    return sum((1 if x else 0) != (1 if y else 0) for x, y in zip(a, b))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    padded = text + "=" * ((4 - len(text) % 4) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _b64_bits(bits: Sequence[int]) -> str:
    return _b64(_bytes_from_bits(bits))


def _bits_from_b64(text: str, nbits: int) -> List[int]:
    return _bits_from_bytes(_unb64(text), nbits)


def _add(feats: FeatureMap, label: str, weight: float = 1.0) -> None:
    if not label:
        return
    feats[label] = feats.get(label, 0.0) + float(weight or 1.0)


def geometry_feature_map(geometry: Dict[str, Any]) -> FeatureMap:
    feats: FeatureMap = {}
    if not isinstance(geometry, dict):
        return feats

    strokes = geometry.get("strokes") or geometry.get("components") or []
    if isinstance(strokes, list):
        _add(feats, f"stroke_count:{min(len(strokes), 12)}", 2.5)
        for i, st in enumerate(strokes[:12]):
            if not isinstance(st, dict):
                continue
            center = st.get("center") if isinstance(st.get("center"), list) else None
            cx = st.get("center_x", st.get("cx", center[0] if center and len(center) > 0 else 0.0))
            cy = st.get("center_y", st.get("cy", center[1] if center and len(center) > 1 else 0.0))
            w = st.get("width", st.get("w", 0.0))
            h = st.get("height", st.get("h", 0.0))
            closed = st.get("closed", False)
            curvature = st.get("curvature") if isinstance(st.get("curvature"), dict) else {}
            straight = st.get("straightness", curvature.get("straightness", 0.0))
            turn_norm = curvature.get("total_turn_norm", 0.0)
            corner45 = curvature.get("corner45_count", 0.0)
            direction_hist = st.get("direction_hist") if isinstance(st.get("direction_hist"), dict) else {}
            dominant_segments = st.get("dominant_segment_count", st.get("segment_count", 0))

            # Per-stroke coarse layout/shape features.
            _add(feats, f"s{i}:cx:{_bucket(cx, 12)}", 1.4)
            _add(feats, f"s{i}:cy:{_bucket(cy, 12)}", 1.4)
            _add(feats, f"s{i}:w:{_bucket(w, 10)}", 0.9)
            _add(feats, f"s{i}:h:{_bucket(h, 10)}", 0.9)
            _add(feats, f"s{i}:closed:{int(bool(closed))}", 1.4)
            _add(feats, f"s{i}:straight:{_bucket(straight, 10)}", 1.2)
            _add(feats, f"s{i}:turnnorm:{min(16, int(float(turn_norm or 0.0) * 2))}", 1.1)
            _add(feats, f"s{i}:corner45:{min(16, int(float(corner45 or 0.0)))}", 0.9)
            _add(feats, f"s{i}:domseg:{min(12, int(float(dominant_segments or 0.0)))}", 1.2)

            for d, val in sorted(direction_hist.items()):
                try:
                    bucket = _bucket(float(val), 8)
                except Exception:
                    bucket = 0
                _add(feats, f"s{i}:dirhist:{d}:{bucket}", 0.6)

            # Soft-token allocation for direction-boundary cases. This is only
            # used by the fuzzy projection layer, not by the human-readable
            # token string or edit-distance verifier. Near a bucket boundary,
            # inject both neighboring direction labels with fractional weight so
            # tiny angle jitter causes gradual bit changes instead of cliffs.
            pts = st.get("points_global") if isinstance(st.get("points_global"), list) else []
            for pa, pb in zip(pts, pts[1:]):
                angle = _segment_angle_deg(pa, pb)
                if angle is None:
                    continue
                for label, frac in _soft_direction_memberships(angle):
                    _add(feats, f"softdir8:{label}", 0.28 * frac)
                    _add(feats, f"s{i}:softdir8:{label}", 0.20 * frac)

        # Pairwise relative layout features. These are crucial for cases like
        # same components arranged differently.
        for i in range(min(len(strokes), 10)):
            a = strokes[i] if isinstance(strokes[i], dict) else {}
            ac = a.get("center") if isinstance(a.get("center"), list) else None
            ax = float(a.get("center_x", a.get("cx", ac[0] if ac and len(ac) > 0 else 0.0)) or 0.0)
            ay = float(a.get("center_y", a.get("cy", ac[1] if ac and len(ac) > 1 else 0.0)) or 0.0)
            for j in range(i + 1, min(len(strokes), 10)):
                b = strokes[j] if isinstance(strokes[j], dict) else {}
                bc = b.get("center") if isinstance(b.get("center"), list) else None
                bx = float(b.get("center_x", b.get("cx", bc[0] if bc and len(bc) > 0 else 0.0)) or 0.0)
                by = float(b.get("center_y", b.get("cy", bc[1] if bc and len(bc) > 1 else 0.0)) or 0.0)
                dx = bx - ax
                dy = by - ay
                horiz = "R" if dx > 0.08 else "L" if dx < -0.08 else "C"
                vert = "B" if dy > 0.08 else "A" if dy < -0.08 else "M"
                dist = min(1.0, (dx * dx + dy * dy) ** 0.5)
                dist_bucket = _bucket(dist, 10)
                angle_bucket = int(((math.atan2(dy, dx) + math.pi) / (2 * math.pi)) * 12) % 12
                _add(feats, f"rel:{i}-{j}:{horiz}{vert}:d{dist_bucket}:a{angle_bucket}", 1.7)

    global_info = geometry.get("global") or {}
    if isinstance(global_info, dict):
        for key in ("width", "height", "aspect", "stroke_count", "closed_count"):
            if key in global_info:
                value = global_info.get(key)
                if isinstance(value, (int, float)):
                    _add(feats, f"global:{key}:{_bucket(float(value), 10)}", 0.9)
                else:
                    _add(feats, f"global:{key}:{value}", 0.9)

    return feats


def token_feature_map(tokens: Sequence[str]) -> FeatureMap:
    feats: FeatureMap = {}
    toks = [str(t) for t in (tokens or [])]
    _add(feats, f"token_count_bucket:{min(24, len(toks) // 4)}", 1.1)

    for t in toks:
        _add(feats, f"tok:{t}", 1.0)
        base = t.split("_")[0]
        _add(feats, f"tokbase:{base}", 0.65)

    for a, b in zip(toks, toks[1:]):
        _add(feats, f"bigram:{a}|{b}", 1.25)

    for a, b, c in zip(toks, toks[1:], toks[2:]):
        _add(feats, f"trigram:{a}|{b}|{c}", 0.95)

    direction_prefixes = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
    for d in direction_prefixes:
        count = sum(1 for t in toks if t == d or t.startswith(d + "_"))
        if count:
            _add(feats, f"dir_count:{d}:{min(count, 10)}", 0.75)

    return feats


def features_from_profile(tokens: Sequence[str], geometry: Dict[str, Any]) -> FeatureMap:
    feats = token_feature_map(tokens)
    for k, v in geometry_feature_map(geometry).items():
        feats[k] = feats.get(k, 0.0) + v
    return feats


def _normalize_features(feats: FeatureMap) -> FeatureMap:
    if not feats:
        return {}
    norm = math.sqrt(sum(v * v for v in feats.values())) or 1.0
    return {k: v / norm for k, v in feats.items()}


def salted_projection_bits(features: Mapping[str, float], salt: str, nbits: int) -> List[int]:
    """Project a sparse feature vector through salted random hyperplanes.

    This is SimHash-style locality-sensitive hashing, but the hyperplanes are
    generated from a public salt so bits are less directly tied to predictable
    human-drawing feature labels. Similar feature vectors should still produce
    nearby bitstrings, while the bit distribution becomes more balanced than
    plain deterministic feature hashing.
    """
    feats = _normalize_features(dict(features))
    if not feats:
        return [0] * nbits

    bits: List[int] = []
    projection_salt = hashlib.sha256(f"DRNG-projection-v2|{salt}".encode("utf-8")).hexdigest()

    # Deterministic sparse random projection. For each output bit, each feature
    # contributes +/- weight with a tiny salt-specific jitter. This avoids
    # explicitly storing a giant matrix.
    for bit_index in range(nbits):
        acc = 0.0
        for label, weight in feats.items():
            h = _hash_bytes(f"{projection_salt}|bit={bit_index}|feat={label}", digest_size=8)
            raw = int.from_bytes(h, "big")
            sign = 1.0 if (raw & 1) else -1.0
            # Mild magnitude jitter prevents many ties without destroying LSH behavior.
            mag = 0.75 + ((raw >> 1) & 0xFFFF) / 0xFFFF * 0.5
            acc += sign * mag * weight
        bits.append(1 if acc >= 0 else 0)
    return bits


def sketch_diagnostics(bits: Sequence[int], feature_count: int) -> Dict[str, Any]:
    n = len(bits) or 1
    ones = sum(1 for b in bits if b)
    frac = ones / n
    return {
        "bit_count": len(bits),
        "ones": ones,
        "zeros": len(bits) - ones,
        "ones_fraction": frac,
        "bias_from_half": abs(frac - 0.5),
        "feature_count": feature_count,
    }


# -------------------------- ECC backends --------------------------


@dataclass
class EncodedSecret:
    mode: str
    codeword: bytes
    metadata: Dict[str, Any]


def _try_make_bch(t: int = HARDENED_BCH_T, m: int = HARDENED_BCH_M):
    if bchlib is None:
        return None, "bchlib_not_installed"

    # Code-freeze rule: do not silently drift across BCH parameters. The
    # evaluation baseline is fixed to GF(2^m) with correction limit t. bchlib
    # has minor API differences across versions, so we try only equivalent
    # keyword orderings, not weaker fallback polynomials or t-only defaults.
    attempts = [
        {"m": int(m), "t": int(t)},
        {"t": int(t), "m": int(m)},
    ]
    last_error = None
    for kwargs in attempts:
        try:
            return bchlib.BCH(**kwargs), None  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover - depends on bchlib version
            last_error = str(exc)
    return None, f"locked_bch_init_failed:m={m},t={t}:{last_error}"


def _bch_ecc_bytes(bch: Any) -> int:
    for attr in ("ecc_bytes", "eccbytes"):
        if hasattr(bch, attr):
            try:
                return int(getattr(bch, attr))
            except Exception:
                pass
    # Conservative fallback for common BCH(255,*,t=16)-style parameters.
    return 32


def _bch_encode_secret(secret: bytes, t: int = HARDENED_BCH_T, m: int = HARDENED_BCH_M) -> Tuple[EncodedSecret | None, str | None]:
    bch, err = _try_make_bch(t=t, m=m)
    if bch is None:
        return None, err
    try:
        ecc = bytes(bch.encode(bytearray(secret)))
    except Exception as exc:  # pragma: no cover
        return None, f"bch_encode_failed:{exc}"
    return EncodedSecret(
        mode="bchlib",
        codeword=secret + ecc,
        metadata={
            "ecc_mode": "bchlib",
            "bch_t": t,
            "bch_m": m,
            "bch_locked": True,
            "bch_parameter_note": f"GF(2^{m}) with t={t} bit corrections over the BCH codeword",
            "secret_nbytes": len(secret),
            "ecc_nbytes": len(ecc),
            "codeword_nbytes": len(secret) + len(ecc),
        },
    ), None


def _bch_decode_codeword(codeword: bytes, metadata: Mapping[str, Any]) -> Tuple[bytes | None, Dict[str, Any]]:
    t = int(metadata.get("bch_t", HARDENED_BCH_T))
    m = int(metadata.get("bch_m", HARDENED_BCH_M))
    secret_nbytes = int(metadata.get("secret_nbytes", 16))
    ecc_nbytes = int(metadata.get("ecc_nbytes", max(0, len(codeword) - secret_nbytes)))
    data = bytearray(codeword[:secret_nbytes])
    ecc = bytearray(codeword[secret_nbytes:secret_nbytes + ecc_nbytes])

    bch, err = _try_make_bch(t=t, m=m)
    if bch is None:
        return None, {"ok": False, "reason": f"bch_unavailable:{err}"}

    decode_errors: List[str] = []
    bitflips = None

    # bchlib has had a few API variants, so try the common ones.
    try:
        bitflips = bch.decode(data, ecc)  # type: ignore[misc]
        try:
            bch.correct(data, ecc)  # type: ignore[misc]
        except Exception:
            pass
    except Exception as exc1:
        decode_errors.append(str(exc1))
        try:
            bitflips = bch.decode_inplace(data, ecc)  # type: ignore[attr-defined]
        except Exception as exc2:
            decode_errors.append(str(exc2))
            try:
                bitflips = bch.decode(data=data, recv_ecc=ecc)  # type: ignore[misc]
                try:
                    bch.correct(data, ecc)  # type: ignore[misc]
                except Exception:
                    pass
            except Exception as exc3:
                decode_errors.append(str(exc3))
                return None, {
                    "ok": False,
                    "reason": "bch_decode_failed",
                    "decode_errors": decode_errors[-3:],
                }

    return bytes(data), {
        "ok": True,
        "reason": "bch_decode_attempted",
        "bitflips": bitflips,
        "bch_t": t,
        "bch_m": m,
        "ecc_nbytes": ecc_nbytes,
    }


def repetition_encode(secret_bits: Sequence[int], repeat: int) -> List[int]:
    out: List[int] = []
    for bit in secret_bits:
        out.extend([1 if bit else 0] * repeat)
    return out


def repetition_decode(code_bits: Sequence[int], repeat: int) -> Tuple[List[int], Dict[str, Any]]:
    if repeat <= 0:
        raise ValueError("repeat must be positive")
    if len(code_bits) % repeat != 0:
        raise ValueError("code length must be divisible by repeat")

    decoded: List[int] = []
    group_confidences: List[float] = []
    corrected_errors = 0
    weak_groups = 0

    for i in range(0, len(code_bits), repeat):
        group = [1 if x else 0 for x in code_bits[i:i + repeat]]
        ones = sum(group)
        zeros = repeat - ones
        bit = 1 if ones >= zeros else 0
        decoded.append(bit)
        majority = max(ones, zeros)
        corrected_errors += repeat - majority
        conf = majority / repeat
        group_confidences.append(conf)
        if conf < 0.67:
            weak_groups += 1

    return decoded, {
        "corrected_errors_estimate": corrected_errors,
        "weak_groups": weak_groups,
        "min_group_confidence": min(group_confidences) if group_confidences else 0.0,
        "mean_group_confidence": sum(group_confidences) / len(group_confidences) if group_confidences else 0.0,
    }


def _repetition_encode_secret(secret: bytes, repeat: int = 9) -> EncodedSecret:
    secret_bits = _bits_from_bytes(secret)
    code_bits = repetition_encode(secret_bits, repeat)
    return EncodedSecret(
        mode="repetition",
        codeword=_bytes_from_bits(code_bits),
        metadata={
            "ecc_mode": "repetition",
            "repeat": repeat,
            "secret_bits": len(secret_bits),
            "secret_nbytes": len(secret),
            "codeword_nbytes": len(_bytes_from_bits(code_bits)),
        },
    )


def commitment(secret: bytes, salt: str, context: str = "Drawing-RNG-Fuzzy-v2") -> str:
    msg = context.encode("utf-8") + b"|" + (salt or "").encode("utf-8") + b"|" + secret
    return hashlib.sha256(msg).hexdigest()


def enroll_fuzzy_secret(
    tokens: Sequence[str],
    geometry: Dict[str, Any],
    salt: str,
    *,
    secret_bytes: bytes | None = None,
    secret_nbytes: int = HARDENED_SECRET_NBYTES,
    preferred_ecc: str = "bch",
    bch_t: int = HARDENED_BCH_T,
    bch_m: int = HARDENED_BCH_M,
    repeat: int = 9,
) -> Dict[str, Any]:
    """Create public helper data for fuzzy recovery of a random secret.

    The returned helper data is public. The returned `secret_hex_for_demo_only`
    is included so the demo can display immediate outputs; do not persist it in
    a production design.
    """
    # Freeze BCH/secret parameters for comparable evaluation runs. Callers may
    # pass arguments for backwards compatibility, but the hardened build locks
    # them here.
    secret_nbytes = HARDENED_SECRET_NBYTES
    bch_t = HARDENED_BCH_T
    bch_m = HARDENED_BCH_M

    if secret_bytes is None:
        secret_bytes = secrets.token_bytes(secret_nbytes)

    encoded: EncodedSecret
    ecc_warning = None
    if preferred_ecc == "bch":
        encoded_bch, err = _bch_encode_secret(secret_bytes, t=bch_t, m=bch_m)
        if encoded_bch is not None:
            encoded = encoded_bch
        else:
            ecc_warning = err
            encoded = _repetition_encode_secret(secret_bytes, repeat=repeat)
    else:
        encoded = _repetition_encode_secret(secret_bytes, repeat=repeat)

    nbits = len(encoded.codeword) * 8
    features = features_from_profile(tokens, geometry)
    sketch_bits = salted_projection_bits(features, salt=salt, nbits=nbits)
    sketch = _bytes_from_bits(sketch_bits)[:len(encoded.codeword)]
    helper = _xor_bytes(sketch, encoded.codeword)
    diag = sketch_diagnostics(sketch_bits, feature_count=len(features))

    return {
        "version": "drng-fuzzy-salted-projection-ecc-v0.3-codefreeze",
        "projection": "salted_random_hyperplanes_with_soft_direction_tokens_v2",
        "nbits": nbits,
        "helper_b64": _b64(helper),
        "commitment": commitment(secret_bytes, salt),
        "feature_count": len(features),
        "sketch_diagnostics": diag,
        "ecc_warning": ecc_warning,
        "code_freeze": {
            "soft_direction_boundary_degrees": SOFT_DIRECTION_BOUNDARY_DEGREES,
            "bch_m": HARDENED_BCH_M,
            "bch_t": HARDENED_BCH_T,
            "secret_nbytes": HARDENED_SECRET_NBYTES,
        },
        **encoded.metadata,
        # Demo only. Do not store/persist this in a real deployment.
        "secret_hex_for_demo_only": secret_bytes.hex(),
    }


def _recover_repetition(codeword: bytes, helper_data: Mapping[str, Any]) -> Tuple[bytes | None, Dict[str, Any]]:
    repeat = int(helper_data.get("repeat", 9))
    secret_bits_len = int(helper_data.get("secret_bits", int(helper_data.get("secret_nbytes", 16)) * 8))
    code_bits = _bits_from_bytes(codeword, int(helper_data.get("nbits", len(codeword) * 8)))
    decoded_bits, stats = repetition_decode(code_bits, repeat=repeat)
    decoded_bits = decoded_bits[:secret_bits_len]
    return _bytes_from_bits(decoded_bits), stats


def recover_fuzzy_secret(
    tokens: Sequence[str],
    geometry: Dict[str, Any],
    salt: str,
    helper_data: Dict[str, Any],
) -> Dict[str, Any]:
    if not isinstance(helper_data, dict):
        return {"ok": False, "reason": "missing_helper_data"}

    try:
        nbits = int(helper_data["nbits"])
        helper = _unb64(str(helper_data["helper_b64"]))
        ecc_mode = str(helper_data.get("ecc_mode", helper_data.get("mode", "repetition")))
    except Exception as exc:
        return {"ok": False, "reason": f"bad_helper_data:{exc}"}

    features = features_from_profile(tokens, geometry)
    sketch_bits = salted_projection_bits(features, salt=salt, nbits=nbits)
    sketch = _bytes_from_bits(sketch_bits)[:len(helper)]
    recovered_codeword = _xor_bytes(helper, sketch)

    if ecc_mode == "bchlib":
        secret, decode_stats = _bch_decode_codeword(recovered_codeword, helper_data)
    else:
        secret, decode_stats = _recover_repetition(recovered_codeword, helper_data)

    if not secret:
        return {
            "ok": False,
            "reason": decode_stats.get("reason", "decode_failed"),
            "decode_stats": decode_stats,
            "feature_count": len(features),
            "sketch_diagnostics": sketch_diagnostics(sketch_bits, feature_count=len(features)),
            "ecc_mode": ecc_mode,
        }

    expected = str(helper_data.get("commitment") or "")
    got = commitment(secret, salt)
    ok = hmac.compare_digest(expected, got)

    return {
        "ok": ok,
        "reason": "commitment_match" if ok else "commitment_mismatch",
        "secret_hex": secret.hex() if ok else None,
        "decode_stats": decode_stats,
        "feature_count": len(features),
        "sketch_diagnostics": sketch_diagnostics(sketch_bits, feature_count=len(features)),
        "ecc_mode": ecc_mode,
    }


def derive_seed_from_fuzzy_secret(secret_hex: str, salt: str, domain: str, out_bytes: int = 32) -> bytes:
    secret = bytes.fromhex(secret_hex)
    # Domain-separated keyed derivation. This is demo KDF material, not a final API.
    key = hashlib.sha256((salt or "").encode("utf-8") + b"|" + secret).digest()
    msg = f"Drawing-RNG-fuzzy-output-v2|domain={domain}".encode("utf-8")
    return hashlib.blake2b(msg, key=key, digest_size=out_bytes).digest()


def fuzzy_seed_hex(secret_hex: str, salt: str, domain: str = "drawing-rng-master") -> str:
    return derive_seed_from_fuzzy_secret(secret_hex, salt, domain).hex()


def fuzzy_demo_password(secret_hex: str, salt: str, domain: str, length: int = 18) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%^&*()-_=+"
    raw = derive_seed_from_fuzzy_secret(secret_hex, salt, f"password:{domain}", out_bytes=64)
    return "".join(alphabet[b % len(alphabet)] for b in raw[:length])


def fuzzy_avatar_palette(secret_hex: str, salt: str) -> Dict[str, str]:
    raw = derive_seed_from_fuzzy_secret(secret_hex, salt, "avatar", out_bytes=9)
    return {
        "primary": f"#{raw[0]:02x}{raw[1]:02x}{raw[2]:02x}",
        "secondary": f"#{raw[3]:02x}{raw[4]:02x}{raw[5]:02x}",
        "accent": f"#{raw[6]:02x}{raw[7]:02x}{raw[8]:02x}",
    }
