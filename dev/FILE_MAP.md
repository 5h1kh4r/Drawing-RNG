# Dev File Map

```text
app.py                              Flask dev API + /dev console routes
src/drawing_rng/                    Core algorithm modules
static/enroll.html + enroll.js      User enrollment/verification UI
static/dev.html + dev.js            Local dataset/dev console
static/collect.html + collect.js    Collection flow
experiments/run_verification_pilot.py  Main pilot metrics runner
tools/run_seed_quality_backfill.py  Recompute and optionally write Seed Quality Scores
tools/generate_seed_quality_report.py  Quality vs outcome report
tools/run_ablation_report.py        Layer/policy comparison report
tools/inspect_false_accepts.py      Accepted non-owner inspection export
tools/generate_step_up_study_report.py Step-up challenge evaluation
tools/generate_use_case_simulation_report.py Use-case simulation report
sql/*.sql                           Supabase schema migrations
```

This compact dev build intentionally omits old generated reports, server logs, pycache files, and phase-history docs.
