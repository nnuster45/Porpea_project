"""Data-quality report: distributions, outliers, near-duplicates and redundant measures.

Meant for sanity checks such as "why does this market have 42 universities within 1.5 km".
"""

import numpy as np
import pandas as pd

from .geo import haversine_m

SKIP = {"place_id", "lat", "lng", "keep", "keep_auto", "is_new", "rank", "dup_count"}


def _num_cols(df):
    return [c for c in df.columns
            if c not in SKIP and pd.api.types.is_numeric_dtype(df[c]) and df[c].notna().any()]


def distribution_table(df, cols):
    lines = ["| measure | n | null | zero | min | p25 | p50 | p75 | p90 | p99 | max |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for c in cols:
        s = df[c]
        v = s.dropna()
        q = v.quantile([0, .25, .5, .75, .9, .99, 1]).round(2).tolist() if len(v) else [np.nan] * 7
        lines.append(f"| {c} | {len(s)} | {int(s.isna().sum())} | {int((s == 0).sum())} | "
                     + " | ".join(f"{x:g}" for x in q) + " |")
    return lines


def top_table(df, col, n=8):
    sub = df.dropna(subset=[col]).sort_values(col, ascending=False).head(n)
    return [f"- **{col}**: " + " · ".join(f"{r['name']} ({r[col]:g})" for _, r in sub.iterrows())]


def near_duplicates(df, within_m=150):
    """Pairs of markets closer than `within_m` — often the same market listed twice."""
    lat, lng = df["lat"].to_numpy(float), df["lng"].to_numpy(float)
    pairs = []
    for i in range(len(df)):
        d = haversine_m(lat[i], lng[i], lat[i + 1:], lng[i + 1:])
        for j in np.nonzero(d <= within_m)[0]:
            k = i + 1 + j
            pairs.append((df.iloc[i]["name"], df.iloc[k]["name"], round(float(d[j]))))
    return pairs


def redundant_pairs(df, cols, threshold=0.85):
    """Spearman correlation between measures — very high means they measure the same thing."""
    usable = [c for c in cols if df[c].notna().sum() > 10 and df[c].nunique() > 2]
    corr = df[usable].corr(method="spearman")
    out = []
    for i, a in enumerate(usable):
        for b in usable[i + 1:]:
            r = corr.loc[a, b]
            if pd.notna(r) and abs(r) >= threshold:
                out.append((a, b, round(float(r), 2)))
    return sorted(out, key=lambda x: -abs(x[2])), corr


def report(features_df, ranked=None, raw_count=None, pillars=None):
    df = features_df.reset_index(drop=True)
    cols = _num_cols(df)
    head = f"## QA report — {len(df)} markets"
    if raw_count is not None:
        head += f" (from {raw_count} listings with keep=1; {raw_count - len(df)} merged as duplicates)"
    lines = [head, ""]

    if "dup_count" in df:
        merged = df[df["dup_count"] > 0].sort_values("dup_count", ascending=False)
        lines += [f"### Merged duplicates: {len(merged)} markets absorbed {int(df['dup_count'].sum())} listings", "",
                  "kept,kept_place_id,merged_names,merged_place_ids"]
        lines += [f"{r['name']},{r['place_id']},{r['dup_names']},{r['dup_ids']}" for _, r in merged.iterrows()]
        lines.append("")

    sparse = [(c, (df[c] == 0).mean()) for c in cols if df[c].notna().any() and (df[c] == 0).mean() >= 0.6]
    lines += ["### Mostly-zero measures (≥60% zeros — percentiles of these mostly reflect map coverage)", ""]
    lines += [f"- {c}: {z:.0%} zeros" for c, z in sparse] or ["- none"]
    lines += ["", "### Distribution of every measure", ""]
    lines += distribution_table(df, cols)
    lines += ["", "### Highest values per measure (check these by hand)", ""]
    for c in cols:
        lines += top_table(df, c)

    lines += ["", "### Near-duplicate markets (≤150 m apart)", ""]
    dups = near_duplicates(df)
    lines.append(f"{len(dups)} pairs")
    lines += [f"- {a} ↔ {b} ({d} m)" for a, b, d in dups[:60]]

    names = df["name"].fillna("").str.strip().str.lower()
    same = names[names.duplicated(keep=False)]
    lines += ["", f"### Same name used by several places: {same.nunique()} names, {len(same)} places", ""]
    lines += [f"- {n} ×{c}" for n, c in same.value_counts().head(30).items()]

    lines += ["", "### Highly correlated measures (Spearman |r| ≥ 0.85)", ""]
    pairs, corr = redundant_pairs(df, cols)
    lines += [f"- {a} ~ {b}: {r}" for a, b, r in pairs] or ["- none"]
    lines += ["", "### Full Spearman correlation matrix", "", corr.round(2).to_csv()]

    if ranked is not None:
        lines += ["", "### Score / pillars", ""]
        pcols = ["score"] + [c for c in ranked.columns if c.startswith("pillar_")]
        lines += distribution_table(ranked, pcols)
        if len(pcols) > 2:
            lines += ["", "Pillar correlation (Spearman) — high values mean two pillars measure the same thing:", "",
                      ranked[pcols[1:]].corr(method="spearman").round(2).to_csv()]
        if "tier" in ranked:
            lines += ["Tiers: " + ", ".join(f"{t}={n}" for t, n in ranked["tier"].value_counts().sort_index().items())]
        if "profile" in ranked:
            lines += ["", "Profile tag combinations:", ""]
            lines += [f"- {p or '(none)'}: {n}" for p, n in ranked["profile"].fillna("").value_counts().items()]
        keep = ["rank", "tier", "name", "score"] + pcols[1:] + [c for c in cols if c in ranked.columns]
        keep = [c for c in keep if c in ranked.columns]
        lines += ["", "### Top 25 with all measures", "", ranked[keep].head(25).to_csv(index=False)]
        lines += ["", "### Bottom 10", "", ranked[keep].tail(10).to_csv(index=False)]
    return "\n".join(lines)
