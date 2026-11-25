# Setup

## 1. MongoDB Atlas (free)
1. Create an account at <https://www.mongodb.com/cloud/atlas> → **Build a database → M0 (Free)**.
2. **Database Access** → add a user with *read and write to any database*; save the password.
3. **Network Access** → allow the IPs of your bot host **and** backend host. (Many PaaS hosts use changing IPs; `0.0.0.0/0` works but is only as safe as your password — use a long random one.)
4. **Connect → Drivers** → copy the `mongodb+srv://…` string into `MONGODB_URI` (the bot also accepts the old `DATABASE_URL`).
5. Nothing else to do: on first start the bot (and the backend) create every index and run the numbered migrations (`database/__init__.py → _MIGRATIONS`). Existing data is kept; migration 3 backfills `competitions`, adds `division` to teams and removes duplicate roster rows.

> M0 limits: 512 MB storage, shared CPU, ~500 connections. Change streams are supported on M0 (the bot uses one; it falls back to polling if unavailable).

## 2. Discord application / OAuth2
1. <https://discord.com/developers/applications> → your TeamCore application → **OAuth2**.
2. Copy **Client ID** → `DISCORD_CLIENT_ID`; **Reset Secret** → `DISCORD_CLIENT_SECRET` (backend host only).
3. **Redirects** → add exactly `BACKEND_URL/auth/callback` (e.g. `https://api.example.com/auth/callback`; for local dev `http://localhost:8000/auth/callback`).
4. Scopes used at login: `identify guilds` only. The bot token stays on the bot host.

## 3. Generate the encryption key (backend)
```
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
Put it in `DATA_ENCRYPTION_KEY`. Keep a copy: if it is lost, stored webhook URLs can't be decrypted (users just re-enter them).

## 4. Local development
```
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # fill it in
python bot/bot.py                                     # terminal 1 — the bot
uvicorn backend.main:app --reload --port 8000         # terminal 2 — the API
python -m http.server 5500 --directory frontend       # terminal 3 — the site: http://localhost:5500/dashboard/
```
For local dev set `BACKEND_URL=http://localhost:8000`, `FRONTEND_URL=http://localhost:5500`, and leave `frontend/config.js` pointing at `http://localhost:8000`.

## 5. First login checklist
* The bot must be online once so it publishes your server to the dashboard (`servers` collection).
* Sign in → choose the server → **Settings**. Owners / Administrators / Manage Server members get full access automatically; grant everyone else on **Staff → Dashboard Access**.
