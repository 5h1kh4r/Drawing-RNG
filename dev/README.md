# Draw2Seed Local Dev Build

This is the local research/development build of Drawing-RNG / Draw2Seed.

Use this build for dataset collection, relabeling, seed-quality backfills, ablation reports, false-accept inspection, and local algorithm evaluation.

Do not deploy this build publicly. It may use a Supabase service-role key for local admin workflows.

## Quick start

```powershell
cd dev
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open:

```text
http://127.0.0.1:5000/
http://127.0.0.1:5000/dev
```

## Environment

Create a `.env` or set variables in your shell:

```text
SUPABASE_URL=...
SUPABASE_ANON_KEY=...
SUPABASE_SERVICE_ROLE_KEY=...
```

Keep the service-role key local only.

## Main evaluation commands

```powershell
python .\experiments\run_verification_pilot.py
python .\tools\run_seed_quality_backfill.py --dry-run
python .\tools\run_seed_quality_backfill.py --update
python .\tools\generate_seed_quality_report.py
python .\tools\run_ablation_report.py
python .\tools\inspect_false_accepts.py --include-stress
python .\tools\generate_step_up_study_report.py
```
