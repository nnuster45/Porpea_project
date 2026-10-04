"""Stage 4: combine features and rank markets (market-anchored weighted scoring in pillars)."""

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


# measure key -> feature column (anything else maps to itself)
ALIASES = {"reviews": "reviews_log", "rating": "rating_adj"}


def score(df, pillars, profile_top_pct=30, log=print):
    """Rank markets: percentile per measure → weighted mean per pillar → weighted mean of pillars (0–100).

    Adds pillar_<key> (0–100), `profile` (tags of pillars where the market is in the top
    profile_top_pct % of markets) and `why`.
    """
    pillar_scores, pillar_w, labels = {}, {}, {}
    for pk, p in pillars.items():
        parts = {}
        for key, w in (p.get("measures") or {}).items():
            col = ALIASES.get(key, key)
            if not w:
                continue
            if col not in df.columns or df[col].notna().sum() == 0:
                log(f"  - '{key}' in pillar '{pk}' has no data, ignored")
                continue
            # unknown values get the median rank rather than 0 or 1
            parts[key] = (df[col].rank(pct=True).fillna(0.5), w)
        if not parts or not p.get("weight"):
            if p.get("weight"):
                log(f"  - pillar '{pk}' has no usable measures, ignored")
            continue
        total = sum(abs(w) for _, w in parts.values())
        pillar_scores[pk] = sum(pct * w for pct, w in parts.values()) / total
        pillar_w[pk] = p["weight"]
        labels[pk] = p.get("tag") or p.get("label") or pk

    if not pillar_scores:
        raise ValueError("no pillar has a usable measure — check scoring.pillars in config.yaml")

    total_w = sum(abs(w) for w in pillar_w.values())
    out = df.copy()
    out["score"] = (sum(pillar_scores[k] * pillar_w[k] for k in pillar_scores) / total_w * 100).round(1)
    for k, s in pillar_scores.items():
        out[f"pillar_{k}"] = (s * 100).round(1)

    cutoff = 1 - profile_top_pct / 100
    top = {k: pillar_scores[k].rank(pct=True) >= cutoff for k in pillar_scores if pillars[k].get("tag")}
    out["profile"] = [", ".join(pillars[k]["tag"] for k in top if top[k].iloc[i]) for i in range(len(out))]
    contrib = pd.DataFrame({k: pillar_scores[k] * pillar_w[k] for k in pillar_scores})
    out["why"] = contrib.apply(
        lambda r: ", ".join(labels[k] for k in r.sort_values(ascending=False).index[:2]), axis=1
    )
    out = out.sort_values("score", ascending=False).reset_index(drop=True)
    out.insert(0, "rank", range(1, len(out) + 1))
    return out
