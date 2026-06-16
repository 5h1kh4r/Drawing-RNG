# Algorithm Map

```text
stroke_token_encoder.py  Raw strokes -> normalized symbolic trajectory tokens
similarity.py            Flat/weighted edit distance and bigram similarity
geometry_verifier.py     Layout, relation, topology, curve, stroke-shape, closed-style gates
scene_verifier.py        Complex-scene clustering, part assignment, raster/chamfer checks
seed_quality.py          Enrollment quality, common-shape risk, stability/copyability scoring
timing_features.py       Diagnostic rhythm features from timestamps
fuzzy_extractor.py       Experimental SimHash/BCH-style recovery layer
seed_derivation.py       Deterministic demo output derivation
use_case_simulator.py    Product-style result simulations for demo cards
enrollment.py            Orchestrates enrollment, redraw verification, and step-up challenge
profiles.py              Verification profile presets and thresholds
```
