"""Stage 2: pull reviews, rating and opening hours for each market (Place Details)."""

import pandas as pd

from .google import BudgetExceeded, GoogleAPIError
from .hours import describe_hours, hours_features


def enrich(markets, client, log=print):
    rows = []
    for i, m in enumerate(markets.itertuples(index=False), 1):
        try:
            d = client.place_details(m.place_id)
        except BudgetExceeded as e:
            log(f"  ! stopping enrich at {i - 1}/{len(markets)}: {e} — the rest stay 'unknown'")
            break
        except GoogleAPIError as e:
            log(f"  ! details failed for {m.name} ({m.place_id}): {e}")
            continue
        row = {
            "place_id": m.place_id,
            "rating": d.get("rating"),
            "reviews": d.get("userRatingCount", 0),
            "business_status_now": d.get("businessStatus", ""),
            "hours_text": describe_hours(d.get("regularOpeningHours")),
            "fetched": fetched.date().isoformat() if (fetched := getattr(client, "last_fetched", None)) else "",
        }
        row.update(hours_features(d.get("regularOpeningHours")))
        rows.append(row)
        if i % 50 == 0:
            log(f"  {i}/{len(markets)}")
    return pd.DataFrame(rows)
