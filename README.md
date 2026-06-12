# Drawing-RNG / Draw2Seed Public Demo

This is the sanitized public demo build for Drawing-RNG / Draw2Seed.

It demonstrates a local, research-only flow for turning repeated freehand stroke gestures into a deterministic demo seed and then testing a redraw against that enrolled gesture.

## What is included

- Flask public demo app
- HTML5 canvas enrollment/redraw UI
- Stroke-token encoder
- Geometry/topology verifier
- Complex-scene verifier
- Seed Quality Score
- Step-up component challenge flow
- Timing/rhythm diagnostics
- Use-case simulation cards

## What is intentionally not included

- `/dev` console
- Supabase database browser
- dataset export/sync tools
- evaluation reports/results
- destructive delete/update utilities
- service-role-key usage
- private pilot data

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`.

## Public demo logging

This demo build is configured to log enrollment and verification attempts automatically when `SUPABASE_URL` and `SUPABASE_ANON_KEY` are present. It still works without Supabase; in that case, attempted logs fall back to local JSON files under `data/local_submissions/` for local testing.

For a deployed public demo, configure Supabase Row Level Security with insert-only policies and use an anon key, never a service-role key:

```bash
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-anon-key
PUBLIC_ENABLE_SERVER_LOGGING=1
AUTO_LOG_ENROLLMENTS=1
AUTO_LOG_VERIFICATIONS=1
python app.py
```

The public build refuses to start if `SUPABASE_SERVICE_ROLE_KEY` is set. To disable logging intentionally, set `PUBLIC_ENABLE_SERVER_LOGGING=0`.

## Security note

This is a research prototype, not production authentication. Do not use it to protect real accounts or secrets.
