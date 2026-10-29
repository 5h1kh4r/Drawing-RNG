"""Controlled structural boundary search and candidate-gallery safety checks.

Diagnostic research utilities; no generic security guarantee. Labels indicating a
changed topology come from discrete structural descriptors, not the accept gate.
"""
from __future__ import annotations

from copy import deepcopy
import math

from .verifier import (
    _structural_difference, comparison, features, validate_strokes, verify,
)

STEPS = (0.12, 0.24, 0.36, 0.50, 0.65, 0.80, 1.0, 1.20)


def _perturb(strokes, family, amount):
    s = deepcopy(strokes)
    pts = [p for st in s for p in st]
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    if family == 'gap_collapse' and len(s) > 1:
        centers = [[sum(p[a] for p in st) / len(st) for a in (0, 1)] for st in s[:2]]
        dx = centers[0][0] - centers[1][0]
        dy = centers[0][1] - centers[1][1]
        for p in s[1]:
            p[0] += dx * amount
            p[1] += dy * amount
    elif family == 'stroke_reflection' and len(s) > 1:
        for p in s[-1]:
            reflected = 2 * cx - p[0]
            p[0] += (reflected - p[0]) * amount
    elif family == 'vertical_compression':
        for st in s:
            for p in st:
                p[1] = cy + (p[1] - cy) * max(0.03, 1 - amount * .80)
    elif family == 'stroke_removal' and len(s) > 1:
        return s[:-1]
    else:
        return None
    return s


def _displacement(a, b):
    if len(a) != len(b):
        return None
    pts_a = [p for st in a for p in st]
    pts_b = [p for st in b for p in st]
    if len(pts_a) != len(pts_b):
        return None
    diag = max(1.0, math.hypot(max(p[0] for p in pts_a) - min(p[0] for p in pts_a),
                               max(p[1] for p in pts_a) - min(p[1] for p in pts_a)))
    rms = math.sqrt(sum((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2
                        for p, q in zip(pts_a, pts_b)) / len(pts_a))
    return round(rms / diag, 5)


def discover_boundary(model, gallery=None):
    """Bounded deterministic probe; seeks accepted *topology-altering* samples.

    Excludes deformations where the independent discrete topology signature is
    unchanged. 'No found escape' must never be interpreted as a proof of safety.
    """
    canonical = model['canonical']
    base = features(canonical)
    tested = 0
    structural = 0
    accepted = []
    family_stats = {}
    families = ('gap_collapse', 'stroke_reflection', 'vertical_compression', 'stroke_removal')
    for family in families:
        if len(canonical) == 1 and family in ('gap_collapse', 'stroke_reflection', 'stroke_removal'):
            continue
        values = (1.0,) if family == 'stroke_removal' else STEPS
        for amount in values:
            sample = _perturb(canonical, family, amount)
            if sample is None:
                continue
            try:
                sample = validate_strokes(sample)
                altered = _structural_difference(base, features(sample))
            except (ValueError, TypeError, OverflowError, ZeroDivisionError):
                continue
            tested += 1
            if not altered:
                continue
            structural += 1
            family_stats[family] = family_stats.get(family, 0) + 1
            outcome = verify(model, sample, gallery, enforce_counterfactual=False)
            if outcome['accepted']:
                accepted.append({'family': family, 'amount': amount,
                                 'normalized_displacement': _displacement(canonical, sample),
                                 'token_score': outcome['token_score'],
                                 'gallery_rescue': outcome['gallery_rescue']})
    # Deleting a stroke has no point-to-point displacement metric; report it
    # separately and choose a minimum only among comparable coordinate edits.
    comparable = [item for item in accepted if item['normalized_displacement'] is not None]
    closest = min(comparable, key=lambda item: item['normalized_displacement']) if comparable else None
    return {'probes_evaluated': tested, 'topology_changed_probes': structural,
            'accepted_topology_changed': len(accepted), 'accepted_by_family': {
                fam: sum(1 for a in accepted if a['family'] == fam) for fam in families
            }, 'smallest_accepted_normalized_displacement': (
                closest['normalized_displacement'] if closest else None),
            'closest_accepted': closest,
            'note': ('Synthetic grid search over specified transformations only. '
                     'An absent successful probe is not a robustness guarantee. '
                     'Point-displacement distances are not defined for stroke deletion.')}


def audit_gallery_promotion(model, gallery, candidate, forbidden=None, cap=6):
    """Evaluate the proposed *post-eviction* gallery against all fixed negatives.

    Safety condition: every available negative must remain rejected after the
    update. This is stronger than non-regression, but remains finite-test only.
    """
    gallery = list(gallery or [])
    after = gallery[-(cap-1):] + [candidate]
    bank = [x['strokes'] for x in model.get('negative_bank', [])]
    bank += list(forbidden or [])
    if not bank:
        return {'safe': False, 'reason': 'no_negative_references', 'checked': 0,
                'accepted_before': 0, 'accepted_after': 0, 'newly_accepted': 0}
    before_accepts, after_accepts, newly = 0, 0, 0
    for neg in bank:
        first = verify(model, neg, gallery, enforce_counterfactual=False,
                       enforce_user_negatives=False)['accepted']
        second = verify(model, neg, after, enforce_counterfactual=False,
                        enforce_user_negatives=False)['accepted']
        before_accepts += int(first)
        after_accepts += int(second)
        newly += int(second and not first)
    safe = after_accepts == 0
    reason=('passed' if safe else 'new_negative_accepted' if newly else 'existing_negative_accepted')
    return {'safe': safe, 'reason': reason,
            'checked': len(bank), 'accepted_before': before_accepts,
            'accepted_after': after_accepts, 'newly_accepted': newly}
