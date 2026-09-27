"""
Date and timestamp utility helpers.
"""
from datetime import datetime, timezone

def get_now_in_iso() -> str:
    """Return the current UTC timestamp formatted as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()

def get_now_utc_str() -> str:
    """Return the current UTC timestamp formatted as 'YYYY-MM-DD HH:MM:SS'."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
