"""Stage 4: combine features and rank markets (market-anchored weighted scoring in pillars)."""

import re
from difflib import SequenceMatcher

import numpy as np
import pandas as pd

from .geo import haversine_m


def _norm_name(name):
    """Name without generic market words, for 'is this the same market' checks."""
    text = str(name or "").lower()
    text = re.sub(r"ตลาดนัด|ตลาดสด|ตลาด|market|night|walking street|ถนนคนเดิน|food|the|นัด", " ", text)
    return re.sub(r"[^0-9a-zก-๙]+", "", text)


def _similar(a, b):
    a, b = _norm_name(a), _norm_name(b)
    if not a or not b:
        return False
    if len(a) >= 3 and len(b) >= 3 and (a in b or b in a):
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.6


def dedupe(df, same_site_m=40, similar_name_m=150):
    """Merge listings of the same market (Google often has several, in several languages).

    Linked when ≤ same_site_m apart, or ≤ similar_name_m apart with similar names. Each group keeps
    the listing with most reviews, sums the group's reviews (`reviews`), and records the others in
    `dup_names` / `dup_ids` / `dup_count`.
    """
    df = df.reset_index(drop=True)
    n = len(df)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    lat, lng = df["lat"].to_numpy(float), df["lng"].to_numpy(float)
    names = df["name"].tolist()
    for i in range(n):
        d = haversine_m(lat[i], lng[i], lat[i + 1:], lng[i + 1:])
        for j in np.nonzero(d <= similar_name_m)[0]:
            k = i + 1 + j
            if d[j] <= same_site_m or _similar(names[i], names[k]):
                parent[find(i)] = find(k)

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    reviews = df["reviews"] if "reviews" in df else pd.Series(np.nan, index=df.index)
    rows = []
    for members in groups.values():
        rep = max(members, key=lambda i: -1 if pd.isna(reviews.iloc[i]) else reviews.iloc[i])
        row = df.iloc[rep].copy()
        others = [i for i in members if i != rep]
        r = reviews.iloc[members]
        row["reviews"] = r.sum() if r.notna().any() else np.nan
        if "rating" in df and r.notna().any():
            rated = df.iloc[members][["rating"]].assign(n=r.values).dropna()
            rated = rated[rated["n"] > 0]
            if len(rated):
                row["rating"] = round(float((rated["rating"] * rated["n"]).sum() / rated["n"].sum()), 2)
        row["dup_count"] = len(others)
        row["dup_names"] = " | ".join(str(names[i]) for i in others)
        row["dup_ids"] = " | ".join(str(df.iloc[i]["place_id"]) for i in others)
        rows.append(row)
    return pd.DataFrame(rows).reset_index(drop=True)


def build_features(markets, details, surroundings, rating_prior_reviews=30, dedupe_cfg=None):
    df = markets.merge(details, on="place_id", how="left").merge(surroundings, on="place_id", how="left")
    if "business_status_now" in df.columns:
        df = df[~df["business_status_now"].fillna("").isin(["CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"])]
    if dedupe_cfg is not None and len(df):
        df = dedupe(df, **dedupe_cfg)

    # NaN reviews = details could not be fetched (unknown), not "0 reviews"
    reviews = df["reviews"] if "reviews" in df else pd.Series(np.nan, index=df.index)
    df["reviews_log"] = np.log1p(reviews)
    # Bayesian average: a 5.0 from 3 reviews should not beat a 4.4 from 2,000.
    # No rating → unknown (NaN), so it is neither rewarded nor punished.
    n = reviews.fillna(0)
    mean = df["rating"].mean() if df["rating"].notna().any() else 0
    adj = (rating_prior_reviews * mean + n * df["rating"]) / (rating_prior_reviews + n)
    # rounded so float noise can't break ties differently here and in the dashboard's JS
    df["rating_adj"] = adj.round(6)
    return df.reset_index(drop=True)


# measure key -> feature column (anything else maps to itself)
ALIASES = {"reviews": "reviews_log", "rating": "rating_adj"}


# tier cut-offs on the score's percentile among markets: close ranks are not meaningfully different
TIERS = [(0.9, "A"), (0.7, "B"), (0.4, "C"), (0.0, "D")]


def measure_values(series, scale=None):
    """A measure → 0..1. Default: percentile rank (ties averaged).
    'minmax': position between min and max of the values (keeps real gaps, e.g. log of reviews).
    Unknown values → 0.5 (neutral)."""
    if scale == "minmax":
        lo, hi = series.min(), series.max()
        out = (series - lo) / (hi - lo) if hi > lo else series * 0 + 0.5
        return out.fillna(0.5)
    return series.rank(pct=True).fillna(0.5)


def tier_of(pct):
    for cut, name in TIERS:
        if pct >= cut:
            return name
    return TIERS[-1][1]


def score(df, pillars, profile_top_pct=30, scales=None, log=print):
    """Rank markets: each measure → 0..1 (percentile, or min-max for measures in `scales`) → weighted mean
    per pillar → weighted mean of pillars (0–100).

    Adds pillar_<key> (0–100), `tier` (A/B/C/D by score percentile), `profile` (tags of pillars where the
    market is in the top profile_top_pct % of markets) and `why`.
    """
    scales = scales or {}
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
            parts[key] = (measure_values(df[col], scales.get(key)), w)
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
    out["tier"] = out["score"].rank(pct=True).map(tier_of)
    out = out.sort_values("score", ascending=False).reset_index(drop=True)
    out.insert(0, "rank", range(1, len(out) + 1))
    return out
