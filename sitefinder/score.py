"""Stage 4: combine features and rank markets for a persona."""

import numpy as np
import pandas as pd


def build_features(markets, details, surroundings, rating_prior_reviews=30):
    df = markets.merge(details, on="place_id", how="left").merge(surroundings, on="place_id", how="left")
    if "business_status_now" in df.columns:
        df = df[df["business_status_now"].fillna("") != "CLOSED_PERMANENTLY"]

    n = df["reviews"].fillna(0)
    df["reviews_log"] = np.log1p(n)
    # Bayesian average: a 5.0 from 3 reviews should not beat a 4.4 from 2,000
    mean = df["rating"].mean() if df["rating"].notna().any() else 0
    adj = (rating_prior_reviews * mean + n * df["rating"].fillna(mean)) / (rating_prior_reviews + n)
    # rounded so float noise can't break ties differently here and in the dashboard's JS
    df["rating_adj"] = adj.round(6)
    return df.reset_index(drop=True)


# persona weight key -> feature column (anything else maps to itself)
ALIASES = {"reviews": "reviews_log", "rating": "rating_adj"}


def score(df, weights, log=print):
    """Percentile-rank each weighted feature, then weighted-average into 0–100."""
    used, contributions = {}, {}
    for key, w in weights.items():
        col = ALIASES.get(key, key)
        if col not in df.columns or df[col].notna().sum() == 0:
            log(f"  - weight '{key}' has no data column, ignored")
            continue
        # unknown values (e.g. no opening hours) get the median rank rather than 0 or 1
        contributions[key] = df[col].rank(pct=True).fillna(0.5) * w
        used[key] = w

    if not used:
        raise ValueError("none of the persona weights matched a feature column")

    total_w = sum(abs(w) for w in used.values())
    contrib = pd.DataFrame(contributions)
    out = df.copy()
    out["score"] = (contrib.sum(axis=1) / total_w * 100).round(1)
    out["why"] = contrib.apply(
        lambda r: ", ".join(f"{k}" for k in r.sort_values(ascending=False).index[:3]), axis=1
    )
    out = out.sort_values("score", ascending=False).reset_index(drop=True)
    out.insert(0, "rank", range(1, len(out) + 1))
    return out
