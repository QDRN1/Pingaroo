"""Timezone-aware wall-clock helpers plus NTP synchronisation status."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from . import shell

_sync_cache: dict = {"t": 0.0, "value": None}


def now() -> datetime:
    return datetime.now().astimezone()


def iso(dt: datetime | None = None) -> str:
    return (dt or now()).isoformat(timespec="seconds")


def iso_in(seconds: float) -> str:
    return iso(now() + timedelta(seconds=seconds))


def hhmmss(dt: datetime | None = None) -> str:
    return (dt or now()).strftime("%H:%M:%S")


def clock_synced(max_age: float = 60.0) -> bool | None:
    """True/False from timedatectl, None if unknown.  Cached for max_age seconds."""
    t = time.monotonic()
    if _sync_cache["value"] is not None and t - _sync_cache["t"] < max_age:
        return _sync_cache["value"]
    res = shell.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], timeout=5)
    value: bool | None
    if res.ok:
        text = res.stdout.strip().lower()
        value = True if text == "yes" else False if text == "no" else None
    else:
        value = None
    _sync_cache.update(t=t, value=value)
    return value


def age_text(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m ago"
    return f"{seconds // 86400}d ago"


def mmss(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def hms(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
