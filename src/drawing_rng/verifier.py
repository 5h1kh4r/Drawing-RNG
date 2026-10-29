"""Lean Draw2Seed verifier v2.0. Research prototype, not production authentication.

Frozen balanced profile; no scene rescue, fuzzy outputs, timing gate or step-up.
Counterfactual calibration is performed at enrollment, and its results are
reported on verification. A conservative counterfactual gate can be activated
only after independent calibration (D2S_COUNTERFACTUAL_ENFORCE=1).
"""
from __future__ import annotations
import math
import statistics
from copy import deepcopy
from typing import Any

from .profiles import PROFILES
from .stroke_token_encoder import encode_json_payload
from .similarity import token_similarity
from .geometry_verifier import extract_geometry_signature, compare_geometry, geometry_failure_reasons

PROFILE = 'balanced'
PARAMS = PROFILES[PROFILE]
TOKEN_THRESHOLD = 0.50  # frozen v1 balanced reference threshold
MAX_STROKES = 24
MAX_POINTS_PER_STROKE = 800
MAX_TOTAL_POINTS = 3000


def validate_strokes(raw: Any) -> list[list[list[float]]]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_STROKES:
        raise ValueError('A drawing must contain 1–24 strokes.')
    cleaned: list[list[list[float]]] = []
    total = 0
    for stroke in raw:
        if not isinstance(stroke, list) or not 2 <= len(stroke) <= MAX_POINTS_PER_STROKE:
            raise ValueError('Each stroke must contain 2–800 points.')
        pts = []
        for p in stroke:
            if not isinstance(p, (list, tuple)) or len(p) < 2:
                raise ValueError('Invalid point.')
            try:
                x, y = float(p[0]), float(p[1])
            except (ValueError, TypeError):
                raise ValueError('Invalid coordinates.') from None
            if not math.isfinite(x) or not math.isfinite(y) or not (-100 <= x <= 1500 and -100 <= y <= 1200):
                raise ValueError('Coordinates are out of bounds.')
            pts.append([round(x, 3), round(y, 3)])
        if sum(math.dist(a, b) for a, b in zip(pts, pts[1:])) < 6:
            raise ValueError('Each stroke must have visible length.')
        cleaned.append(pts)
        total += len(pts)
    if total > MAX_TOTAL_POINTS:
        raise ValueError('Too many drawing points.')
    return cleaned


def features(strokes: list) -> dict:
    encoded = encode_json_payload({'strokes': strokes, 'params': PARAMS})
    return {'tokens': encoded.get('tokens') or [],
            'geometry': extract_geometry_signature(strokes, PARAMS)}


def comparison(a: dict, b: dict) -> dict:
    ts = token_similarity(a['tokens'], b['tokens'])
    geom = compare_geometry(a['geometry'], b['geometry'])
    failures = geometry_failure_reasons(geom, PROFILE)
    return {'token_score': round(ts, 5), 'geometry_score': round(geom['geometry_final'], 5),
            'score': round(.55*ts + .45*geom['geometry_final'], 5),
            'geometry_pass': not failures, 'failure_reasons': failures,
            'accepted': ts >= TOKEN_THRESHOLD and not failures}


def _mutate(strokes, kind):
    s = deepcopy(strokes)
    pts = [p for st in s for p in st]
    cx = sum(p[0] for p in pts)/len(pts)
    cy = sum(p[1] for p in pts)/len(pts)
    w = max(p[0] for p in pts)-min(p[0] for p in pts)
    h = max(p[1] for p in pts)-min(p[1] for p in pts)
    if kind == 'translation':
        for st in s:
            for p in st: p[0] += 17; p[1] -= 9
    elif kind == 'scaling':
        for st in s:
            for p in st: p[0] = cx+(p[0]-cx)*1.08; p[1] = cy+(p[1]-cy)*1.08
    elif kind == 'jitter':
        for j, st in enumerate(s):
            for i,p in enumerate(st):
                p[0] += 0.9*math.sin(i*1.13+j); p[1] += 0.9*math.cos(i*1.23+j)
    elif kind == 'drop_stroke' and len(s)>1:
        return s[:-1]
    elif kind == 'collapse_gap' and len(s)>1:
        c0 = [sum(p[axis] for p in s[0])/len(s[0]) for axis in (0,1)]
        c1 = [sum(p[axis] for p in s[1])/len(s[1]) for axis in (0,1)]
        # Translate one stroke closer to the other by 75% of centre separation.
        for p in s[1]:
            p[0] += .75*(c0[0]-c1[0]); p[1] += .75*(c0[1]-c1[1])
    elif kind == 'reflect_stroke':
        for p in s[-1]: p[0] = 2*cx-p[0]
    elif kind == 'deform':
        # Strong nonrigid deformation of a single-stroke gesture.
        for i,p in enumerate(s[0]):
            p[0] += .35*max(h,25)*math.sin(i/(len(s[0])-1)*math.pi)
    elif kind == 'flatten':
        for st in s:
            for p in st: p[1] = cy + .15*(p[1]-cy)
    return s


def _structural_difference(a: dict, b: dict) -> bool:
    """Independent, discrete structural labels, not the verifier decision.

    Used only to decide whether a generated mutant is *actually* a structural
    negative. Unlike filtering on geometry_failure_reasons, this lets the
    robustness experiment expose weaknesses in that geometry verifier.
    """
    ga, gb = a['geometry'], b['geometry']
    if ga['stroke_count'] != gb['stroke_count']:
        return True
    ca, cb = ga.get('components',[]), gb.get('components',[])
    if [bool(x.get('closed')) for x in ca] != [bool(x.get('closed')) for x in cb]:
        return True
    ra,rb=ga.get('relations',[]),gb.get('relations',[])
    for x,y in zip(ra,rb):
        if round(x.get('crossing_count',0)) != round(y.get('crossing_count',0)):
            return True
        if (x.get('x_gap',0)>.035) != (y.get('x_gap',0)>.035):
            return True
        if (x.get('y_gap',0)>.035) != (y.get('y_gap',0)>.035):
            return True
    return False


def counterfactual_bank(canonical: list, cap: int = 4) -> list[dict]:
    """Deterministic, pre-registered mutations with independent labels.

    'Structural' negatives require a changed discrete relation. Non-topology
    shape deformations are included as their own class, not mislabeled as
    topology changes. A valid negative CAN fool the current verifier.
    """
    base = features(canonical)
    kinds = ['drop_stroke','collapse_gap','reflect_stroke','flatten'] if len(canonical)>1 else ['deform','flatten']
    bank = []
    for kind in kinds:
        mutant = _mutate(canonical, kind)
        try: f = features(mutant)
        except (ValueError, ZeroDivisionError): continue
        structural = _structural_difference(base,f)
        if kind in ('drop_stroke','collapse_gap') and not structural:
            continue
        bank.append({'kind':kind,'class':'structural' if structural else 'shape_deformation',
                     'strokes':mutant})
        if len(bank)>=cap:break
    return bank


def _near_positive(canonical: list) -> dict:
    f = features(canonical)
    vals = []
    for kind in ('translation','scaling','jitter'):
        sample = _mutate(canonical,kind)
        c = comparison(f,features(sample))
        vals.append({'kind':kind, 'accepted':c['accepted'], 'token_score':c['token_score'],
                     'geometry_score':c['geometry_score']})
    return vals


def robustness(canonical: list, bank: list[dict]) -> dict:
    base = features(canonical)
    positives = _near_positive(canonical)
    negatives = [{'kind':n['kind'], 'class':n['class'], 'accepted':comparison(base,features(n['strokes']))['accepted']}
                 for n in bank]
    # Overall score is conditional on available valid counterfactuals.
    if negatives:
        score = 0.5*(sum(v['accepted'] for v in positives)/len(positives) +
                     sum(not v['accepted'] for v in negatives)/len(negatives))
    else:
        score = None
    return {'score': None if score is None else round(score,4),
            'positive_pass_rate':round(sum(v['accepted'] for v in positives)/len(positives),4),
            'negative_reject_rate':round(sum(not v['accepted'] for v in negatives)/len(negatives),4) if negatives else None,
            'positives':positives, 'negatives':negatives,
            'warning':'No applicable counterfactuals generated; score unavailable.' if not negatives else None}


def enroll(raw_attempts: Any) -> dict:
    if not isinstance(raw_attempts, list) or len(raw_attempts)!=3:
        raise ValueError('Draw the gesture three times to enroll.')
    attempts = [validate_strokes(x) for x in raw_attempts]
    fs=[features(x) for x in attempts]
    rows = [[comparison(fs[i],fs[j]) if i!=j else None for j in range(3)] for i in range(3)]
    medoid=max(range(3), key=lambda i:sum(rows[i][j]['score'] for j in range(3) if j!=i))
    canonical=attempts[medoid]
    pair=[rows[i][j] for i in range(3) for j in range(i+1,3)]
    stability=statistics.median(v['score'] for v in pair)
    bank=counterfactual_bank(canonical)
    quality=robustness(canonical, bank)
    warnings=[]
    if stability < .55: warnings.append('Redraws vary considerably. Consider a simpler gesture.')
    if quality['positive_pass_rate'] < .67: warnings.append('Your gesture appears sensitive to small changes.')
    if not any(n['class']=='structural' for n in bank): warnings.append('No discrete topology-changing counterfactuals available for this gesture.')
    # Enrollment remains possible for study even if estimated robustness is low.
    return {'canonical':canonical, 'token_count':len(fs[medoid]['tokens']),
            'stability':round(stability,4), 'robustness':quality,
            'negative_bank':bank, 'warnings':warnings,
            'parameters':{'profile':PROFILE,'token_threshold':TOKEN_THRESHOLD}}


def verify(enrollment: dict, sample: Any, gallery: list[list] | None=None,
           enforce_counterfactual: bool=False, enforce_user_negatives: bool=True) -> dict:
    candidate=validate_strokes(sample)
    f=features(candidate)
    core = comparison(features(enrollment['canonical']),f)
    # Gallery can rescue only a narrow token deficit; core geometry always passes.
    gallery_scores=[]
    for g in (gallery or [])[:8]:
        gallery_scores.append(comparison(features(g), f)['token_score'])
    gallery_best=max(gallery_scores, default=0.0)
    rescued=(not core['accepted'] and core['geometry_pass']
             and core['token_score']>=TOKEN_THRESHOLD-.04
             and gallery_best>=TOKEN_THRESHOLD+.06)
    cf_scores=[]
    user_negative_veto=False
    most_negative_advantage=None
    for n in enrollment.get('negative_bank',[]):
        neg_match=comparison(features(n['strokes']),f)
        cf_scores.append(neg_match['score'])
        if n.get('kind')=='user_defined':
            advantage=round(neg_match['score']-core['score'],5)
            most_negative_advantage=max(advantage, most_negative_advantage) if most_negative_advantage is not None else advantage
            if (neg_match['geometry_pass'] and neg_match['token_score']>=.68 and advantage>=.04):
                user_negative_veto=True
    best_negative=max(cf_scores,default=None)
    cf_margin=round(core['score']-best_negative,5) if best_negative is not None else None
    cf_pass=cf_margin is None or cf_margin >= .025
    accepted=bool((core['accepted'] or rescued) and (cf_pass or not enforce_counterfactual)
                  and (not user_negative_veto or not enforce_user_negatives))
    reasons=core['failure_reasons'][:]
    if not core['accepted'] and not rescued and core['token_score']<TOKEN_THRESHOLD:
        reasons.append('token_score_below_threshold')
    if enforce_counterfactual and not cf_pass: reasons.append('counterfactual_margin_below_0.025')
    if user_negative_veto and enforce_user_negatives: reasons.append('closer_to_user_forbidden_0.04')
    # Update eligibility deliberately stricter than ordinary acceptance.
    # Accepted is not genuine identity proof: this is still vulnerable to poisoning.
    eligible=(accepted and core['accepted'] and core['token_score']>=.68
              and core['geometry_score']>=.77 and cf_pass)
    return {'accepted':accepted,'core_accepted':core['accepted'], 'gallery_rescue':bool(rescued),
            'token_score':core['token_score'],'geometry_score':core['geometry_score'],
            'final_score':core['score'],'geometry_pass':core['geometry_pass'],
            'failure_reasons':reasons, 'gallery_best_token_score':round(gallery_best,5) if gallery_scores else None,
            'counterfactual_margin':cf_margin, 'counterfactual_pass':cf_pass,
            'counterfactual_enforced':enforce_counterfactual,
            'user_negative_veto':bool(user_negative_veto and enforce_user_negatives),
            'user_negative_advantage':most_negative_advantage,
            'update_eligible':eligible}
