"""Wall-clock recurrence calculations for scheduled chat work."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def resolve_wall_time(day, wall_time, zone):
    """Resolve a local time, choosing the first fold or the end of a gap."""
    if wall_time.tzinfo is not None:
        raise ValueError("The wall-clock time must be naive.")
    zone = ZoneInfo(zone) if isinstance(zone, str) else zone
    local = datetime.combine(day, wall_time)
    # An imaginary time round-trips to opposite sides of the gap with the
    # two folds. Search from the earlier side for the first valid instant.
    candidates = [
        local.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
        for fold in (0, 1)
    ]
    valid = [instant for instant in candidates if instant.astimezone(zone).replace(tzinfo=None) == local]
    if valid:
        return min(valid)
    start, end = sorted(candidates)
    # Offset transitions have second precision in the IANA database.
    while (end - start) > timedelta(seconds=1):
        middle = start + timedelta(seconds=int((end - start).total_seconds()) // 2)
        if middle.astimezone(zone).replace(tzinfo=None) < local:
            start = middle
        else:
            end = middle
    return end.replace(microsecond=0)


def next_occurrence(after, wall_time, zone, repetition, weekday=None):
    """Return the next daily or weekly occurrence strictly after an instant."""
    if after.tzinfo is None or after.utcoffset() is None:
        raise ValueError("The reference instant must be timezone-aware.")
    if repetition not in ("daily", "weekly"):
        raise ValueError("Expected daily or weekly repetition.")
    if repetition == "weekly" and (not isinstance(weekday, int) or not 0 <= weekday <= 6):
        raise ValueError("Weekly repetition requires a weekday from 0 to 6.")
    zone = ZoneInfo(zone) if isinstance(zone, str) else zone
    day = after.astimezone(zone).date()
    if repetition == "weekly":
        day += timedelta(days=(weekday - day.weekday()) % 7)
    candidate = resolve_wall_time(day, wall_time, zone)
    if candidate <= after.astimezone(timezone.utc):
        day += timedelta(days=1 if repetition == "daily" else 7)
        candidate = resolve_wall_time(day, wall_time, zone)
    return candidate
