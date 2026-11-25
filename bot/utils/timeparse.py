"""Compatibility shim — the implementation lives in shared/timeutil.py (also used by the backend)."""
from shared.timeutil import *  # noqa: F401,F403
from shared.timeutil import parse_duration, format_duration, parse_local_datetime, aware  # noqa: F401
