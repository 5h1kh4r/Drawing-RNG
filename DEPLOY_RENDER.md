# Deploy Draw2Seed v2.3 PUBLIC to Render

This is the PUBLIC application only. **Never deploy the DEV ZIP**, which contains `/dev` routes. The public repository must have `app.py`, `requirements.txt`, and `render.yaml` at its root, not inside an extra wrapper folder.

1. Upload only the files in this folder to a private GitHub repository. Do not commit `.env`, the DEV app, database credentials, or real volunteer study data.
2. In [Render](https://dashboard.render.com/), choose **New > Blueprint** (the interface might say **Blueprints > New Blueprint**). Connect your GitHub repo and select its `render.yaml`.
3. Render will prompt for **SUPABASE_DB_URL**. Paste the `postgresql+psycopg://` **Session pooler, port 5432** URI from Supabase's **Connect** dialog, with your actual database password and `?sslmode=require` (or `&sslmode=require` if query parameters already exist). URL-encode special characters in the password. Paste the value into Render's secret field only.
4. The Blueprint generates `SECRET_KEY` automatically; the other settings are defined in `render.yaml`. Confirm the **Free** plan and **Singapore** region (change `region` in YAML beforehand if your Supabase project is nearer another Render region), then create the service.
5. Wait for deployment to finish and open `https://<your-service>.onrender.com/api/health`. Expect JSON with `"ok":true`. Open `/` in an ordinary browser, enroll a **test-only** gesture, and submit a test attempt.
6. Locally run the DEV build with its `/dev` dashboard pointed at **the same Supabase database**, and confirm those test records appear. Check the PUBLIC URL `/dev` gives a 404.

When pushing via WSL, you can use `git init -b main`, `git add .`, `git commit -m 'Deploy public Draw2Seed v2.3'`, connect a pre-created private GitHub repository with `git remote add origin https://github.com/YOUR_USERNAME/draw2seed-volunteers.git`, and `git push -u origin main` (GitHub authentication is required).

Notes:
- Do not set `ENABLE_DEV_ADMIN` or `DEV_ADMIN_PASSWORD` on Render.
- `APP_ENV=production` enables secure session cookies, and production refuses missing/short `SECRET_KEY` or non-TLS PostgreSQL URLs.
- A single worker avoids competing `create_all()` attempts on initial startup and conserves DB connections; two threads allow light concurrent testing.
- The current `/api/health` only checks the Flask process. It does not independently ping the DB. An invalid DB URI causes startup to fail during table initialization.
- Free Render web services may sleep after inactivity. Supabase persists the research records independently of the Render instance.
- The consent form, privacy/retention policy, exposure of gesture samples, DB permissions and account-withdrawal workflow should be reviewed before inviting actual volunteers. No personal usernames or passwords are required.
