# FoodBridge backend (Flask + MySQL + TinyFish)

This backend is regenerated to match the existing FoodBridge frontend API. It keeps the existing route names and JSON shapes for login, registration, donation listings, requests, dashboards, admin review, contact messages, and FoodTrust.

## Included files

- `app.py` — Flask API routes, sessions, role checks, CORS and health endpoint.
- `database.py` — SQLite/MySQL schema and persistence operations.
- `tinyfish_client.py` — TinyFish Search + Fetch integration and source-linked reports.
- `requirements.txt` — Python dependencies for deployment.
- `Procfile` — Render/Gunicorn start command.
- `.env.example` — template only; it contains no live credentials.
- `FRONTEND_API_CONNECTION.txt` — tiny API URL / credentials adjustment needed when the frontend stays on GitHub Pages.

## Run locally with SQLite first

1. Open this folder in VS Code.
2. Create `.env` by copying `.env.example`.
3. For local development, set these values in `.env`:

   ```env
   FOODBRIDGE_DB_BACKEND=sqlite
   FOODBRIDGE_DB=./foodbridge.db
   FOODBRIDGE_SEED_DEMO_DATA=true
   FOODBRIDGE_SECRET_KEY=local-dev-only-change-this-secret
   FOODBRIDGE_ADMIN_PASSWORD=ChangeThisAdminPassword123!
   ```

4. Install packages:

   ```bash
   python -m pip install -r requirements.txt
   ```

5. Run:

   ```bash
   python app.py
   ```

6. Test `http://127.0.0.1:5000/api/health`. It should return `{"status":"ok","database":"connected"}`.

The local SQLite path is for development only. The project seeds demonstration data by default in SQLite if `FOODBRIDGE_SEED_DEMO_DATA` is unset; setting it to `true` makes that explicit.

## Connect a cloud MySQL database

1. Create an empty MySQL database in your database hosting provider.
2. Copy the actual host, port, database name, username and password into `.env` locally, or into the host's private environment-variable settings after deployment.
3. Set `FOODBRIDGE_DB_BACKEND=mysql`.
4. For a provider such as Aiven, download its CA certificate, make it available to the app as a private file, and set `MYSQL_SSL_CA` to its path. Keep `MYSQL_SSL_REQUIRED=true`.
5. Before the first cloud launch, set a unique `FOODBRIDGE_SECRET_KEY` (at least 32 characters), `FOODBRIDGE_ADMIN_PASSWORD` (at least 12 characters), `FOODBRIDGE_ADMIN_USERNAME`, and `FOODBRIDGE_ADMIN_EMAIL`.
6. Keep `FOODBRIDGE_SEED_DEMO_DATA=false` for a clean online database. On first startup, the backend creates its tables and configured administrator; it does not add the sample restaurants, NGOs, donations or demo accounts.

If you purposely want demo data for a demo deployment, enable `FOODBRIDGE_SEED_DEMO_DATA=true` only in a controlled demo environment. Do not enable public demo accounts in a real deployment.

## Deploy the API on Render

In Render, create a Web Service connected to the GitHub repository that contains these files.

- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app`

Set the variables from `.env.example` in Render's **Environment** panel. Do not upload `.env` or commit passwords/API keys. If the backend is in a subfolder of your repository, select that folder as the service's Root Directory.

The API root `/` returns a small JSON status document. `/api/health` checks database connectivity. The web app itself remains on GitHub Pages unless you choose to serve it from the same origin as the API.

## Connect the existing GitHub Pages frontend

The current frontend calls relative URLs such as `/api/bootstrap`. Since GitHub Pages and the API have different origins, those calls need the API base URL. Follow the exact small `api()` helper replacement in `FRONTEND_API_CONNECTION.txt`; it does not change the UI or page design.

Set `FRONTEND_ORIGINS` in the backend host to the exact origin of your GitHub Pages website, for example `https://YOUR-USERNAME.github.io` (no path and no trailing slash). Add your local development origin as a comma-separated entry if needed. Do not use `*` with credentialed CORS.

Cross-site login sessions require HTTPS and credentialed fetch requests. Browser restrictions on third-party cookies may still block cross-site cookies. If login does not persist reliably, the most robust deployment is to serve the frontend and backend from the same origin while continuing to keep the source code in GitHub.

## TinyFish

Set `TINYFISH_API_KEY` in the private environment settings. The key is read by the backend only. The TinyFish research response includes status and concise HTTP response details when API requests fail; it does not claim that a restaurant is certified or that reviews are complete/current.

## Security notes

- Never commit `.env`, database passwords, TinyFish keys or CA/private credentials.
- The backend checks roles on protected operations. The frontend's role display is not treated as authorization.
- Admin credentials must be configured before initializing a clean MySQL deployment.
- Use HTTPS and TLS to the database where supported.
- The source code was syntax-checked in this environment, but it has not been connected to your actual MySQL host or TinyFish account. Confirm `/api/health` and a real research run after configuring your own credentials.
