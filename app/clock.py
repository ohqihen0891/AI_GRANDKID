"""Single source of 'now' for the whole app.

A demo offset lets you skip ahead in time (e.g. +30 min to show a medication follow-up)
without waiting. Real use: offset stays 0.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import S

_offset = timedelta(0)
PTZ = ZoneInfo(S.PARENT_TZ)


def now_utc() -> datetime:
    return datetime.now(timezone.utc) + _offset


def parent_now() -> datetime:
    return now_utc().astimezone(PTZ)


def child_now(tz: str = None) -> datetime:
    return now_utc().astimezone(ZoneInfo(tz or S.CHILD_TZ))


def set_to(dt_utc: datetime):
    """Demo time-lapse: move the app's clock to an exact moment."""
    global _offset
    _offset = dt_utc - datetime.now(timezone.utc)


def reset():
    global _offset
    _offset = timedelta(0)


def offset_minutes() -> int:
    return int(_offset.total_seconds() // 60)


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def to_parent(iso: str) -> datetime:
    return parse_iso(iso).astimezone(PTZ)


def slot_to_utc(slot: str) -> datetime:
    """'YYYY-MM-DD HH:MM' in parent's time zone -> aware UTC datetime."""
    return datetime.strptime(slot, "%Y-%m-%d %H:%M").replace(tzinfo=PTZ).astimezone(timezone.utc)


def fmt_both(iso_or_dt, child_tz: str = None) -> str:
    """e.g. '17:00 HK · 10:00 London'"""
    dt = parse_iso(iso_or_dt) if isinstance(iso_or_dt, str) else iso_or_dt
    hk = dt.astimezone(PTZ).strftime("%H:%M")
    ch = dt.astimezone(ZoneInfo(child_tz or S.CHILD_TZ)).strftime("%H:%M")
    return f"{hk} HK · {ch} {S.CHILD_CITY}"
