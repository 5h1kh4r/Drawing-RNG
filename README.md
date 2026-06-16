# Draw2Seed Public Demo

This is the public, GitHub/Render-safe build of Drawing-RNG / Draw2Seed.

It contains only the user-facing enrollment and verification demo. It intentionally omits the local `/dev` console, dataset browser, delete tools, report generators, raw exports, and service-role workflows.

## What this build does

- Captures hand-drawn vector strokes from an HTML5 canvas.
- Runs enrollment stability and Seed Quality Score analysis.
- Verifies redraws through token, geometry, topology, scene, timing, and step-up logic.
- Shows use-case simulation cards for vault unlock / deterministic secret derivation style flows.
- Optionally logs public demo enrollments and verification attempts for dataset collection.

## Safety boundary

This public build must use the Supabase anon key only.

It refuses to start if `SUPABASE_SERVICE_ROLE_KEY` is present.

Server-side demo logging redacts reusable secret outputs before storage.

## Render settings

Root directory:

```text
public
```

Build command:

```bash
pip install -r requirements.txt
```

Start command:

```bash
gunicorn app:app
```

Environment variables:

```text
SUPABASE_URL=...
SUPABASE_ANON_KEY=...
PUBLIC_ENABLE_SERVER_LOGGING=1
AUTO_LOG_ENROLLMENTS=1
AUTO_LOG_VERIFICATIONS=1
```

Do not set:

```text
SUPABASE_SERVICE_ROLE_KEY
```

## Supabase policy

Use `sql/public_insert_only_schema.sql` as the public logging baseline. The public anon role should be insert-only for demo tables. Do not grant public select/update/delete.
