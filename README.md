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

## Optional public logging

Server-side logging is disabled by default. The app works without Supabase.

To enable optional insert-only logging for your own demo instance, configure Supabase Row Level Security with insert-only policies and use an anon key, never a service-role key:

```bash
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-anon-key
PUBLIC_ENABLE_SERVER_LOGGING=1
AUTO_LOG_ENROLLMENTS=1
AUTO_LOG_VERIFICATIONS=1
python app.py
```

The public build refuses to start if `SUPABASE_SERVICE_ROLE_KEY` is set.

## Security note

This is a research prototype, not production authentication. Do not use it to protect real accounts or secrets.
