# Deployment

You deploy three independent pieces. They only meet in MongoDB.

## A. Frontend (static)

### Option 1 — GitHub Pages
1. Repo → **Settings → Pages → Build and deployment → Source: GitHub Actions**.
2. Repo → **Settings → Secrets and variables → Actions → Variables** → add `API_BASE_URL` = your backend's public URL (no trailing slash).
3. Push to `main`. `.github/workflows/pages.yml` writes `frontend/config.js` and publishes `frontend/`.
4. Site: `https://USERNAME.github.io/REPO/` (landing) and `…/dashboard/`. Set on the backend: `FRONTEND_URL=https://USERNAME.github.io/REPO` (origin for CORS is derived automatically).

> **Important — GitHub's terms.** GitHub documents that Pages is *not* intended to be used as a free host for an online business, e-commerce site or any site "primarily directed at … providing commercial software as a service (SaaS)" ([Pages usage limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits)). A free community dashboard is fine; **if you sell Premium or run this as a business, host the frontend elsewhere.** The frontend is plain static files, so moving is trivial.

### Option 2 — Same server as the API (recommended for commercial use)
Set `SERVE_FRONTEND=1` on the backend and `BACKEND_URL = FRONTEND_URL`, and set `frontend/config.js` to `API_BASE: ""`. One host, same origin, no CORS.

### Option 3 — Another static host
Cloudflare Pages, Netlify, Vercel etc.: publish the `frontend/` folder (no build command) and write `config.js` as above. Check that provider's current terms for commercial use.

## B. Backend (FastAPI)
Any Python host (Render, Railway, Fly.io, a VPS…). Install `backend/requirements.txt`.
* Start command: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`
* Environment: `MONGODB_URI`, `MONGODB_DB`, `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `BACKEND_URL`, `FRONTEND_URL`, `DATA_ENCRYPTION_KEY` (+ optional `ALLOWED_ORIGINS`, `SESSION_TTL_DAYS`).
* Box-score screenshots (optional): stored in the `match_shots` collection — one document per match holding every image (up to 8), with the first mirrored in the older fields — and posted with the result. The browser shrinks each to at most 1600 px / JPEG before upload. No OCR service, key or system package is involved. If you ever clear old matches manually, delete their `match_shots` rows too.
* **Do not** set `DISCORD_TOKEN` here. Serve it over HTTPS only (Discord requires an HTTPS redirect URI outside localhost).
* Free hosts sleep when idle — the first request after a nap takes ~30 s. The site shows a loading state; use a paid instance for production.
* Health check: `GET /health`.

## C. Bot
Same as before (Koyeb etc.). **The start command changed** because the code moved into `/bot`:
* Install: `pip install -r bot/requirements.txt`
* Run: `python bot/bot.py`
* Environment: `DISCORD_TOKEN`, `MONGODB_URI` (or `DATABASE_URL`), `PORT`.
* Enable the **Server Members Intent** in the Developer Portal (already required before).
* On first start it applies migrations 1-3 and publishes each server's channels/roles to the dashboard.

## D. Updating an existing install
1. Back up: Atlas → **Backup/Export**, or the dashboard's **Data Export** (per server).
2. Deploy the bot first (migrations run automatically and are safe to re-run), then the backend, then the frontend.
3. Rotate any secret that was ever committed to git (an old `.env` in history counts).
