"""Fixed versioned research comparison rules, independent of the legacy pipeline.

DO NOT tune constants against the final holdout. Never interpret coordinates as
biometric identity proof or the output as a source of cryptographic entropy.
"""
import math
import statistics
from .verifier import features, comparison, validate_strokes, TOKEN_THRESHOLD
from .similarity import token_similarity

VERSION = 'd2s24-consensus-2of3-2026-10'
N_ENROLL = 8


def make_references(samples):
    if len(samples) != N_ENROLL:
        raise ValueError(f'Expected {N_ENROLL} enrollment drawings.')
    clean = [validate_strokes(s) for s in samples]
    f = [features(s) for s in clean]
    # Select the medoid using symbolic trajectory alone. Compute geometry on
    # only seven comparisons, not on every pair: inexpensive on small hosts.
    pair = {(i,j): token_similarity(f[i]['tokens'],f[j]['tokens'])
            for i in range(N_ENROLL) for j in range(i+1,N_ENROLL)}
    def sim(i,j):
        return 1.0 if i == j else pair[tuple(sorted((i,j)))]
    medoid = max(range(N_ENROLL), key=lambda i: sum(sim(i,j) for j in range(N_ENROLL)))
    candidates = [(j,comparison(f[medoid],f[j])) for j in range(N_ENROLL) if j != medoid]
    # Prefer structural agreement at enrollment; retain unlike samples in raw
    # collection even if they do not become verification templates.
    candidates.sort(key=lambda x:(x[1]['geometry_pass'],x[1]['score']),reverse=True)
    chosen = [medoid] + [j for j,_ in candidates[:2]]
    agreement = sum(c['geometry_pass'] and c['token_score']>=TOKEN_THRESHOLD for _,c in candidates)
    return {
        'algorithm_version':VERSION,
        'references':[clean[i] for i in chosen],
        'canonical_index':medoid,
        'reference_indices':chosen,
        'token_stability':round(statistics.median(pair.values()),5),
        'reference_agreement_count':int(agreement),
        'quality_warning': agreement < 2,
        'selection_rule':'token_medoid_plus_two_closest_structural_matches',
    }


def _sample_at(stroke, n=32):
    """Arc-length resample, preserving stroke boundaries for DTW comparator."""
    if len(stroke)<2:return []
    dist=[0.0]
    for a,b in zip(stroke,stroke[1:]):dist.append(dist[-1]+math.hypot(b[0]-a[0],b[1]-a[1]))
    if dist[-1]<1e-9:return [stroke[0][:2]]*n
    out=[];j=1
    for k in range(n):
        d=dist[-1]*k/(n-1)
        while j<len(dist)-1 and dist[j]<d:j+=1
        t=(d-dist[j-1])/max(dist[j]-dist[j-1],1e-9)
        out.append([stroke[j-1][0]*(1-t)+stroke[j][0]*t,stroke[j-1][1]*(1-t)+stroke[j][1]*t])
    return out


def dtw_distance(a,b):
    """Normalized shape DTW diagnostic; not a learned or calibrated decision."""
    if len(a)!=len(b):return None
    points_a=[p for s in a for p in s];points_b=[p for s in b for p in s]
    def normalize(strokes,points):
        xs=[p[0] for p in points];ys=[p[1] for p in points]
        midx=(max(xs)+min(xs))/2;midy=(max(ys)+min(ys))/2
        scale=max(max(xs)-min(xs),max(ys)-min(ys),1e-8)
        return [[[(x-midx)/scale,(y-midy)/scale] for x,y in _sample_at(s)] for s in strokes]
    aa=normalize(a,points_a);bb=normalize(b,points_b)
    stroke_costs=[]
    for x,y in zip(aa,bb):
        n=len(x);m=len(y);prev=[float('inf')]*(m+1);prev[0]=0
        for i in range(1,n+1):
            curr=[float('inf')]*(m+1)
            for j in range(max(1,i-8),min(m,i+8)+1):
                curr[j]=math.dist(x[i-1],y[j-1])+min(prev[j],curr[j-1],prev[j-1])
            prev=curr
        stroke_costs.append(prev[m]/max(n,m))
    return round(sum(stroke_costs)/len(stroke_costs),6)


def evaluate(reference, attempt):
    clean=validate_strokes(attempt)
    f=features(clean)
    comps=[comparison(features(r),f) for r in reference['references']]
    n=sum(c['accepted'] for c in comps)
    # Two independent templates must match; we do not use nearest-one acceptance.
    consensus=bool(n>=2 and len(clean)==len(reference['references'][0]))
    canonical=comps[0]
    return {
        'algorithm_version':VERSION,
        'accepted':consensus,
        'consensus_votes':n,
        'canonical_accepted':bool(canonical['accepted']),
        'token_score':canonical['token_score'],
        'geometry_score':canonical['geometry_score'],
        'geometry_pass':canonical['geometry_pass'],
        'failure_reasons':canonical['failure_reasons'],
        'reference_comparisons':comps,
        'baseline_token_only':canonical['token_score']>=TOKEN_THRESHOLD,
        'baseline_geometry_only':canonical['geometry_pass'],
        'baseline_single_reference':canonical['accepted'],
        'baseline_any_reference':bool(n>=1),
        'baseline_dtw_distance':min((d for d in (dtw_distance(r,clean) for r in reference['references']) if d is not None),default=None),
        'gallery_enabled':False,
    }
