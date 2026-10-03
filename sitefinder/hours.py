"""Turn Google `regularOpeningHours.periods` into simple time-of-day features."""

HOURS_PER_WEEK = 7 * 24

# hour-of-day windows (start inclusive, end exclusive; end > 24 wraps past midnight)
WINDOWS = {
    "open_morning": (6, 10),
    "open_evening": (16, 20),
    "open_night": (20, 26),
}


def weekly_open_slots(periods):
    """Return a set of week-hour indices (day*24 + hour, Sunday=0) the place is open."""
    slots = set()
    for period in periods or []:
        start = period.get("open")
        if not start:
            continue
        end = period.get("close")
        if end is None:  # open 24/7 is encoded as a single open at day 0 00:00 with no close
            return set(range(HOURS_PER_WEEK))
        s = start.get("day", 0) * 24 + start.get("hour", 0)
        e = end.get("day", 0) * 24 + end.get("hour", 0) + (1 if end.get("minute", 0) else 0)
        if e <= s:
            e += HOURS_PER_WEEK
        for h in range(s, e):
            slots.add(h % HOURS_PER_WEEK)
    return slots


def hours_features(opening_hours):
    """Features in [0, 1] = share of the week's 7 days that the place is open in each window."""
    periods = (opening_hours or {}).get("periods")
    if not periods:
        return {"hours_known": 0, "days_open": None, **{k: None for k in WINDOWS}}

    slots = weekly_open_slots(periods)
    features = {
        "hours_known": 1,
        "days_open": sum(1 for d in range(7) if any(d * 24 + h in slots for h in range(24))) / 7,
    }
    for name, (lo, hi) in WINDOWS.items():
        days = 0
        for d in range(7):
            if any((d * 24 + h) % HOURS_PER_WEEK in slots for h in range(lo, hi)):
                days += 1
        features[name] = days / 7
    return features


def describe_hours(opening_hours):
    return " | ".join((opening_hours or {}).get("weekdayDescriptions") or [])
