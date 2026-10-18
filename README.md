# FoodBridge

FoodBridge connects restaurants that have surplus food with NGOs and community kitchens that can collect it. The project includes a Flask API, database persistence, role-based workflows, and optional TinyFish-powered public research through FoodTrust.

> **Deployment note:** GitHub is where the source code is stored. GitHub Pages can serve the static frontend, but it cannot run the Flask API or store live database changes. The backend must run on a Python web host, and its database must be hosted somewhere the backend can securely reach.

## Project layout

```text
FoodBridge/
├── README.md
├── .gitignore
├── .venv/                       # Local only; do not commit
└── foodbridge-backend/
    ├── app.py                   # Flask API
    ├── database.py              # SQLite/MySQL persistence and schema
    ├── tinyfish_client.py       # TinyFish Search/Fetch integration
    ├── requirements.txt         # Python dependencies
    ├── Procfile                 # Gunicorn start command
    ├── .env.example             # Safe configuration template
    ├── .env                     # Local secrets; create this yourself; do not commit
    └── FRONTEND_API_CONNECTION.txt
```

Your existing frontend files may be stored elsewhere in the repository. Keep their paths and existing design unless you intentionally change them.

## Features

- Restaurant and NGO registration with admin approval
- Login/logout and server-side role checks
- Donation listings, search, filters, detail pages, and pickup deadlines
- NGO donation requests and restaurant approval/decline
- Donation status and handover tracking
- NGO directory and requirement updates
- Admin registration review and contact-message management
- FoodTrust research with source-linked TinyFish results when an API key is configured
- SQLite for local development, or MySQL for a hosted database

## Local development on Windows

These commands assume the `.venv` folder is in the top-level `FoodBridge` folder and the backend is in `foodbridge-backend`.

1. Open PowerShell at the project root:

   ```powershell
   cd "C:\Users\abinp\Downloads\FoodBridge"
   ```

2. Create a virtual environment if it does not already exist, then install packages:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r .\foodbridge-backend\requirements.txt
   ```

3. Create a private `.env` file from the example:

   ```powershell
   Copy-Item .\foodbridge-backend\.env.example .\foodbridge-backend\.env
   ```

4. Open `foodbridge-backend\.env` and configure it for a local SQLite run. At minimum, set these values:

   ```env
   FOODBRIDGE_DB_BACKEND=sqlite
   FOODBRIDGE_DB=./foodbridge.db
   FOODBRIDGE_SEED_DEMO_DATA=true
   FOODBRIDGE_SECRET_KEY=change-this-local-development-secret
   FOODBRIDGE_ADMIN_USERNAME=admin
   FOODBRIDGE_ADMIN_EMAIL=admin@example.com
   FOODBRIDGE_ADMIN_PASSWORD=choose-a-strong-local-password
   TINYFISH_API_KEY=
   ```

   Keep the rest of the template if you plan to switch to MySQL later. Do not put real passwords or keys in `.env.example`.

5. Start the backend from its own folder so the relative SQLite path is easy to find:

   ```powershell
   cd .\foodbridge-backend
   ..\.venv\Scripts\python.exe app.py
   ```

6. Open these local endpoints:

   - API status: <http://127.0.0.1:5000/>
   - Database health check: <http://127.0.0.1:5000/api/health>
   - Bootstrap data: <http://127.0.0.1:5000/api/bootstrap>

   The health endpoint should return `{"status":"ok","database":"connected"}` when the database is reachable. Press `Ctrl+C` in the terminal to stop the server.

If PowerShell says a package is missing, install requirements through the same `.venv` interpreter used to run the app. For example, run `..\.venv\Scripts\python.exe -m pip install -r requirements.txt` from inside `foodbridge-backend`.

## TinyFish / FoodTrust configuration

TinyFish research is optional. Add a valid `TINYFISH_API_KEY` to `foodbridge-backend\.env` for local development, or set it in the backend host's private environment settings after deployment. Restart the backend after changing environment variables.

If a research request fails, use the HTTP status and response details returned by the backend to investigate. Common categories include an invalid key, denied access, quota/rate limiting, invalid request parameters, or an upstream service problem. Do not paste API keys into issues, screenshots, or public logs. Do not treat search excerpts as certification or as proof of current food-safety conditions.

## Use a cloud MySQL database

To use MySQL instead of SQLite, create a MySQL database with your database hosting provider, then set these environment variables using the actual connection details from that provider:

```env
FOODBRIDGE_DB_BACKEND=mysql
MYSQL_HOST=your-provider-hostname
MYSQL_PORT=3306
MYSQL_DATABASE=foodbridge
MYSQL_USER=your-provider-username
MYSQL_PASSWORD=your-provider-password
MYSQL_SSL_CA=ca.pem
MYSQL_SSL_REQUIRED=true
FOODBRIDGE_SEED_DEMO_DATA=false
```

Use the actual port, database name, and connection values supplied by your provider; `3306` is only the usual MySQL port. Configure TLS according to the provider's instructions and place its CA certificate where the deployed backend can read it. Never commit the certificate or database credentials.

On startup, the backend initializes its schema and, when demo seeding is disabled, creates/configures the administrator using the private admin environment variables. Back up any data before changing database settings. The SQLite-to-MySQL migration script is only needed if you decide to copy existing SQLite records; it is not required for a clean new database.

## Deploy the backend on Render

1. Push the source code to GitHub. Include `foodbridge-backend/requirements.txt`, `app.py`, `database.py`, `tinyfish_client.py`, `Procfile`, `.env.example`, and this README. Do **not** commit `.env`, `.venv`, local database files, API keys, passwords, or private certificates.
2. In Render, create a **Web Service** and connect the repository.
3. Set the service's **Root Directory** to `foodbridge-backend`.
4. Use these commands:

   - Build: `pip install -r requirements.txt`
   - Start: `gunicorn app:app`

5. In Render's private **Environment** settings, add the production values from `.env.example`. Use a unique `FOODBRIDGE_SECRET_KEY` (at least 32 characters), a strong `FOODBRIDGE_ADMIN_PASSWORD` (at least 12 characters), the configured admin username/email, cloud MySQL credentials, TLS settings, and `FOODBRIDGE_SEED_DEMO_DATA=false` for a clean deployment.
6. Provide the database provider's CA certificate as a private file if needed, and set `MYSQL_SSL_CA` to its path.
7. Deploy and test `/api/health` on the Render URL before testing all workflows.

Do not use the local SQLite file as a substitute for persistent cloud storage in a deployed service. A host's temporary filesystem can be cleared on restart or redeploy.

## Connect GitHub Pages frontend to the backend

If the frontend stays on GitHub Pages and the Flask backend runs on Render, the frontend API helper must call the absolute Render URL instead of relative paths like `/api/bootstrap`. See `foodbridge-backend/FRONTEND_API_CONNECTION.txt` for the helper replacement.

Set `FRONTEND_ORIGINS` in the backend's environment settings to the exact **origin** of the GitHub Pages site, for example `https://YOUR-USERNAME.github.io` (no path and no trailing slash). Add a local development origin only if needed. Do not use `*` with credentialed requests.

Login relies on session cookies. Because GitHub Pages and Render are different origins, browser third-party-cookie restrictions can affect whether sessions persist. If login is unreliable, consider serving the frontend and backend from the same origin while still storing the source code on GitHub.

## Security checklist

- Keep `.env`, API keys, database passwords, private keys and CA certificates out of Git.
- Keep `.env.example` limited to placeholders.
- Rotate any real credential that was accidentally pushed to a public repository.
- Use HTTPS for the site/API and TLS for cloud database connections.
- Use strong, unique production secrets and disable demo accounts/data in a public deployment.
- Treat frontend checks as usability features only; the Flask backend must enforce authorization.

## Troubleshooting

- **`ModuleNotFoundError`:** Install `requirements.txt` using the same `.venv` interpreter used to run `app.py`.
- **`API key not configured`:** Check that `TINYFISH_API_KEY` is set in `foodbridge-backend\.env` locally or in the hosting provider's environment, then restart/redeploy.
- **MySQL connection error:** Check hostname, port, database name, username, password, network access rules, and TLS certificate path.
- **GitHub Pages shows a failed API request:** Confirm that the frontend API base URL points to your deployed backend and `FRONTEND_ORIGINS` matches the GitHub Pages origin.
- **`/api/health` returns 503:** Check the backend logs and database connection configuration.

The app has not been verified against your live cloud MySQL service or TinyFish account. Test those integrations with your own credentials before relying on the deployed application.
