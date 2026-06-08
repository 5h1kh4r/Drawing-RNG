# Drawing-RNG

**Drawing-RNG** is a research prototype for turning hand-drawn secrets into reusable deterministic seed material.

It does **not** claim production-grade cryptographic security. The project explores whether drawings can be used as a more human-friendly seed/password interface by replacing brittle pixel hashing with stroke-token encoding, repeated enrollment, and stability checks.

## Current project direction

The project now has two separate workflows:

1. **Prompt Doodle Collection** — collect prompt-based drawings to study which drawing categories are naturally stable or unstable.
2. **Drawing Seed Enrollment** — ask a user to draw the same remembered secret multiple times, score redraw stability, and generate demo outputs.

The key idea is:

```text
human drawing → captured strokes → stroke-token encoder → enrollment stability check → deterministic seed outputs
```

## What this repo includes

```text
app.py                         Flask backend
static/                        Web pages for collection + seed enrollment
src/drawing_rng/               Core encoder, enrollment, seed derivation modules
sql/                           Supabase schemas
tools/                         Dataset export/render/filter utilities
experiments/                   Clean dataset analysis runner
docs/                          Project notes and deployment guidance
data/                          Empty local data folders, raw data is not committed
```

Generated datasets, raw volunteer drawings, rendered review images, and old experimental clutter have been intentionally removed.

## Install locally

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

## Supabase setup

For a clean setup, use a new Supabase project for the seed-enrollment workflow.

Run these in Supabase SQL Editor:

```text
sql/001_prompt_collection_schema.sql      optional prompt doodle collection
sql/002_seed_enrollment_schema.sql        seed enrollment records
sql/003_clean_dataset_schema.sql          cleaned prompt dataset, if needed
```

Set environment variables on Render/local:

```text
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
PROMPT_TABLE=stroke_samples
ENROLLMENT_TABLE=drawing_seed_enrollments
```

The service role key must stay server-side only. Never put it into frontend JavaScript.

## Deploy on Render

Use:

```text
Build command: pip install -r requirements.txt
Start command: gunicorn app:app
```

Render Free is okay for hosting, because Supabase stores the actual records.

## Main demo

Go to `/enroll`:

1. Draw the same secret attempt 1.
2. Draw the same secret attempt 2.
3. Draw the same secret attempt 3.
4. Analyze enrollment.
5. Drawing-RNG reports stability, recommended profile, warnings, seed hex, demo password, and avatar palette.

## Research claim

Drawing-RNG is not better than random seeds. It investigates whether hand-drawn secrets can be a more memorable interface than user-chosen text passwords when paired with enrollment and quality checks.

The honest claim is:

> Drawing-RNG turns hand-drawn secrets into deterministic seed material using stroke-token encoding and repeated enrollment. It can measure whether a drawing is stable enough for demo seed generation and warn when a drawing is unstable or likely weak.

## Safety note

Do not collect names, signatures, initials, passwords, private symbols, or personal identifiers in drawings.

## Code-freeze evaluation build

This build includes:

- soft direction-boundary allocation in the fuzzy feature projection layer;
- minimum complexity rejection during enrollment;
- locked BCH parameters for evaluation;
- automatic Supabase logging for enrollments and verification attempts.

Before deploying, run:

```text
sql/004_verification_attempts_schema.sql
```

Then set:

```text
VERIFICATION_TABLE=drawing_seed_verifications
AUTO_LOG_ENROLLMENTS=1
AUTO_LOG_VERIFICATIONS=1
```
