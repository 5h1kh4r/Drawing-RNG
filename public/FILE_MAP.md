# Public File Map

```text
app.py                         Flask public demo API
requirements.txt                Python dependencies
.env.example                    Render/local env template
src/drawing_rng/                Core verifier modules
static/index.html               Landing page
static/enroll.html              Enrollment + verification UI
static/enroll.js                Canvas and frontend flow
static/shared.js                Canvas utilities and participant IDs
static/style.css                Demo UI styling
sql/public_insert_only_schema.sql  Optional Supabase schema/policies
```

The public build does not include `/dev`, evaluation reports, local tools, or service-role code paths.
