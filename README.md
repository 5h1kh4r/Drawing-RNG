# Draw2Seed v2.3 — anonymous, no-login volunteer prototype

**Research prototype only.** Drawings are experimental data, not secure authentication credentials and not cryptographic randomness. This release deliberately has **no volunteer login, no signup, and no passwords**. It retains the v2.1 token/geometry hard-gated verifier, bounded adaptive gallery, user-specified forbidden shapes, boundary probes and voluntary adversarial challenges.

## Which build?

- **`Draw2Seed-v2.3-PUBLIC.zip`** — the only package to deploy publicly. No local admin files or routes. Volunteer page opens directly into the three-drawing enrollment interface, with an explicit research-consent checkbox.
- **`Draw2Seed-v2.3-DEV.zip`** — use **only on your own computer**. Includes the local-only `/dev` research console and admin passphrase, with attempt/enrollment/participant browsing, stroke previews, filters, delete actions and manual testing.

Both connect to **Supabase PostgreSQL** through a server-only connection URI. You do *not* paste a Supabase `anon` or `service_role` API key into the site or frontend. Use **Supabase → Connect → Postgres connection string**, with the database password and `sslmode=require`. If needed, use the Supavisor session pooler connection URI. **SQLite is not a supported runtime store** (isolated unit tests alone may use an ephemeral SQLite database).

## How participants use it

1. Open the URL. The page shows the enrollment canvas immediately; no registration screen.
2. Acknowledge study consent, label the gesture and draw it three times. The server creates a random, pseudonymous participant record **only when the enrollment is submitted**.
3. Redraw your gesture, label intentional near misses and wrong shapes, optionally add forbidden variants or run boundary analysis.
4. Optionally opt your gesture into anonymous adversarial challenges. Other *browser sessions* can receive blind, informed-forgery or near-miss assignments. They do not see their matching results.
5. On a shared device, use **Start a different volunteer session** to switch participants without deleting study records; it does not recover the previous profile.
6. Use the on-page **Delete my study data** control to withdraw the live data associated with this browser session.

The Flask signed session cookie associates later requests with the pseudonymous participant ID. **Cookies are not identity proof.** Clearing the browser's cookies or changing browsers loses access to the old profile, but does **not** erase the stored rows. The local researcher console can find them for deletion. One volunteer may accidentally create multiple profiles; report that limitation in the study, and do not equate anonymous sessions with distinct human participants. Run research participation only under an appropriate consent/retention policy.

## Setup — local developer build

Python 3.11+:

```bash
cd Draw2Seed-v2.3-DEV
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Set these server-side environment variables (export them in your shell or load them using your IDE/process manager). The app **does not automatically load `.env`**:

```bash
export APP_ENV=development
export SUPABASE_DB_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/postgres?sslmode=require'
export SECRET_KEY='REPLACE_WITH_LONG_RANDOM_SECRET'
export ENABLE_DEV_ADMIN=1
export DEV_ADMIN_PASSWORD='REPLACE_WITH_UNIQUE_16_PLUS_CHARACTER_PASSPHRASE'
python app.py
```

Visit `http://127.0.0.1:5000` for volunteer flow and `http://127.0.0.1:5000/dev` for the **local-only console**. The development Flask process binds to `127.0.0.1`; the admin additionally checks the request originates from a loopback address and requires a separate passphrase. Never put the development package behind a reverse proxy, expose its port through tunnels, or deploy it publicly.

## Public deployment

Deploy **only** the PUBLIC ZIP. Set:

```text
APP_ENV=production
SUPABASE_DB_URL=postgresql+psycopg://...@.../postgres?sslmode=require
SECRET_KEY=<a-high-entropy-secret-of-at-least-32-characters>
AUTO_GALLERY_UPDATES=1
COUNTERFACTUAL_ENFORCE=0
ATTEMPTS_PER_HOUR=120
```

Install requirements, then start `gunicorn --workers 2 --bind 0.0.0.0:$PORT app:app` behind HTTPS. `ENABLE_DEV_ADMIN` has no effect in the PUBLIC build. Protect the database with a limited-privilege Postgres role and backups. Do not put Supabase credentials into browser code. Review retention and participant withdrawal behavior before distribution. Public installation **requires a PostgreSQL DSN**; the server fails closed if it is missing or does not request TLS.

## Study feature notes

- **Adaptive gallery:** up to six trusted candidate references, with original canonical reference frozen. Promotion is stricter than ordinary verification, and proposed updates are replay-tested against stored negative examples. *Passing a finite test bank is not a proof against poisoning.*
- **Counterfactual tests:** the verifier retains its diagnostic margin; by default synthetic-negative enforcement is off (`COUNTERFACTUAL_ENFORCE=0`), while manually defined forbidden gestures can veto sufficiently similar attempts.
- **Adversarial boundary search:** fixed deterministic structural mutation grid reports successes, not a certified minimum attack distance. Cached results may become stale when the gallery changes.
- **Challenge lab:** the attacker must have enrolled at least one gesture in a distinct browser session. Opt-in targets only. Owner/target names are never given to attackers; scores and accept/reject decisions are withheld. Assignment rules are browser-session based, not strong Sybil prevention.
- **Research labels:** owner_test, blind_impostor, informed_forgery, near_miss, true_wrong_shape, bad_sample. These represent participant claims or assigned experimental conditions—not independent ground truth.
- **Data:** raw gesture coordinates and result diagnostics are stored on the Supabase database. Existing v2.1 `d2s_*` tables are reused; the old `password_hash` column remains **only for backward schema compatibility**. New anonymous participants get the literal marker `LOGIN_DISABLED` in that unused column—no credentials are collected or used. Legacy account rows may still exist and are only accessible via the research console, not the volunteer app.

## Test and export

```bash
python -m unittest discover -s tests -v
python -m compileall -q .
node --check static/app.js
# DEV only:
node --check dev_ui/admin.js
python scripts/export_results.py --output results.csv
```

The HTTP test uses SQLite **only in APP_ENV=test** and will skip when Flask dependencies are unavailable. The algorithm unit tests run independently. Do not interpret skipped integration tests as a successful hosted end-to-end deployment. A real Supabase connection and the full public workflow must be tested before recruiting volunteers.

## Versioning / isolation

- Development package includes `dev_admin.py` and `dev_ui/` and registers the admin blueprint only if `APP_ENV=development` **and** `ENABLE_DEV_ADMIN=1`, plus loopback-only restrictions.
- Public package physically excludes those files and removes the development blueprint registration code from `app.py`; no database-browser or CRUD endpoint ships in production.
- Both packages use the same algorithm version `lean-v2.3-anonymous-2026-10` and the same database schema. Preserve the version string for study exports.

**This PUBLIC artifact contains no development administration code, endpoints or export scripts.**
