"""shared/settings.py — environment configuration used by the backend (and the bot, where relevant).

Secrets come ONLY from environment variables. Note what is *not* here: the backend never reads the bot token.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
load_dotenv()


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip().rstrip("/") for x in os.getenv(name, default).split(",") if x.strip()]


MONGODB_URI = os.getenv("MONGODB_URI") or os.getenv("DATABASE_URL")  # the bot already uses DATABASE_URL
DB_NAME = os.getenv("MONGODB_DB", "lockerroom_bot")

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")      # public URL of the FastAPI app
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5500").rstrip("/")    # base URL where /dashboard/ is hosted (include the /RepoName path for GitHub project sites)
# Origins allowed to call the API from a browser (CORS + Origin check on writes). Never "*".
def _origin(url: str) -> str:
    from urllib.parse import urlparse
    u = urlparse(url)
    return f"{u.scheme}://{u.netloc}"


ALLOWED_ORIGINS = _list("ALLOWED_ORIGINS", _origin(FRONTEND_URL)) or [_origin(FRONTEND_URL)]

SESSION_TTL_DAYS = int(os.getenv("SESSION_TTL_DAYS", "7"))
GUILD_LIST_TTL_SECONDS = int(os.getenv("GUILD_LIST_TTL_SECONDS", "600"))
JOB_WAIT_SECONDS = float(os.getenv("JOB_WAIT_SECONDS", "10"))

# 32-byte url-safe base64 key (Fernet). Used to encrypt Discord access tokens and webhook URLs at rest.
DATA_ENCRYPTION_KEY = os.getenv("DATA_ENCRYPTION_KEY", "")

BOT_OWNER_ID = os.getenv("BOT_OWNER_ID", "")
