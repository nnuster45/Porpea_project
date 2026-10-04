"""Turn Google `regularOpeningHours.periods` into simple time-of-day features."""

import numpy as np

MIN_PER_DAY = 24 * 60
MIN_PER_WEEK = 7 * MIN_PER_DAY
MIN_OVERLAP = 60  # a market must be open at least this many minutes in a window to count for it

# hour-of-day windows (start inclusive, end exclusive; end > 24 wraps past midnight)
WINDOWS = {
    "open_morning": (6, 10),
    "open_evening": (16, 20),
    "open_night": (20, 26),
}
DAY_LETTERS = "อา จ อ พ พฤ ศ ส".split()  # Google: day 0 = Sunday


def weekly_open_minutes(periods):
    """Boolean array of the week's minutes (Sunday 00:00 = 0) when the place is open."""
    week = np.zeros(MIN_PER_WEEK, dtype=bool)
    for period in periods or []:
        start = period.get("open")
        if not start:
            continue
        end = period.get("close")
        if end is None:  # open 24/7 is a single open at day 0 00:00 with no close
            week[:] = True
            return week
        s = start.get("day", 0) * MIN_PER_DAY + start.get("hour", 0) * 60 + start.get("minute", 0)
        e = end.get("day", 0) * MIN_PER_DAY + end.get("hour", 0) * 60 + end.get("minute", 0)
        if e <= s:
            e += MIN_PER_WEEK
        idx = np.arange(s, e) % MIN_PER_WEEK
        week[idx] = True
    return week


def _open_in(week, day, lo_h, hi_h):
    lo = day * MIN_PER_DAY + lo_h * 60
    idx = np.arange(lo, day * MIN_PER_DAY + hi_h * 60) % MIN_PER_WEEK
    return int(week[idx].sum())


def hours_features(opening_hours):
    """Share of the 7 days the market is open ≥ MIN_OVERLAP minutes in each window, plus which days.

    `open_days` is a 7-char mask, Sunday first ("0000011" = Friday and Saturday)."""
    periods = (opening_hours or {}).get("periods")
    if not periods:
        return {"hours_known": 0, "days_open": None, "open_days": "", **{k: None for k in WINDOWS}}

    week = weekly_open_minutes(periods)
    days = [_open_in(week, d, 0, 24) >= MIN_OVERLAP for d in range(7)]
    features = {
        "hours_known": 1,
        "days_open": sum(days) / 7,
        "open_days": "".join("1" if x else "0" for x in days),
    }
    for name, (lo, hi) in WINDOWS.items():
        features[name] = sum(_open_in(week, d, lo, hi) >= MIN_OVERLAP for d in range(7)) / 7
    return features


def describe_days(mask):
    return " ".join(DAY_LETTERS[i] for i, c in enumerate(mask or "") if c == "1")


def describe_hours(opening_hours):
    return " | ".join((opening_hours or {}).get("weekdayDescriptions") or [])
