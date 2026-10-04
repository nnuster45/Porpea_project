"""Command line entry point: python -m sitefinder <stage>."""

import argparse
import os
import sys
from pathlib import Path

import pandas as pd
import yaml

from .cache import DiskCache
from .discover import plan_tiles

DATA = Path("data")
MARKETS = DATA / "markets.csv"
DETAILS = DATA / "market_details.csv"
SURROUND = DATA / "surroundings.csv"
RANKED = DATA / "ranked.csv"
MAP = DATA / "map.html"
DASHBOARD = DATA / "dashboard.html"
CACHE = DATA / "cache"
REVIEW = Path("review") / "keep.csv"  # committed overrides: place_id, keep, note


def load_env(path=".env"):
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_config(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def google_client(args):
    from .google import GoogleClient

    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        sys.exit("GOOGLE_MAPS_API_KEY is not set — copy .env.example to .env and put your key there")
    return GoogleClient(key, DiskCache(CACHE), max_calls=args.max_calls)


def overpass_client():
    from .osm import OverpassClient

    return OverpassClient(DiskCache(CACHE))


def need(path, stage):
    if not path.exists():
        sys.exit(f"{path} not found — run `python -m sitefinder {stage}` first")
    return pd.read_csv(path)


def load_markets():
    """Markets the user kept (keep != 0, after review/keep.csv). Missing keep column = keep everything."""
    from .discover import apply_review

    df = apply_review(need(MARKETS, "discover"), REVIEW)
    if "keep" in df.columns:
        df = df[pd.to_numeric(df["keep"], errors="coerce").fillna(1) != 0]
    return df.reset_index(drop=True)


# monthly free usage per SKU (Google Maps Platform, since Mar 2025)
FREE_CAPS = {"text_search_pro": 5000, "place_details_enterprise": 1000, "places_aggregate": 5000}
AGGREGATE_USD_PER_1K = 10.0


def _budget_line(label, low, high, cap):
    span = f"{low:,}" if low == high else f"{low:,} – {high:,}"
    over = max(0, high - cap)
    status = "✓ within free cap" if over == 0 else f"✗ up to {over:,} over the free cap"
    return f"  {label:<22} ≈ {span:>15} calls  (free {cap:,}/month)  {status}"


def cmd_estimate(cfg, args):
    n_tiles = len(plan_tiles(cfg))
    n_kw = len(cfg["discover"]["keywords"])
    sc = cfg["surroundings"]
    from .surround import google_call_count

    per_market = google_call_count(sc, 1) if sc["source"] == "google_aggregate" else 0
    n_markets, source = args.markets, f"assumed (--markets {args.markets})"
    if MARKETS.exists():
        all_markets = pd.read_csv(MARKETS)
        n_markets = len(load_markets())
        source = f"keep=1 in {MARKETS} ({len(all_markets) - n_markets} of {len(all_markets)} set to keep=0)"
    n_agg = n_markets * per_market
    print(f"grid: {n_tiles} tiles × {n_kw} keywords; markets: {n_markets} — {source}")
    print(_budget_line("discover (Text Search)", n_tiles * n_kw, n_tiles * n_kw * 3, FREE_CAPS["text_search_pro"]))
    print(_budget_line("enrich (Details)", n_markets, n_markets, FREE_CAPS["place_details_enterprise"]))
    if per_market:
        print(_budget_line("surround (Aggregate)", n_agg, n_agg, FREE_CAPS["places_aggregate"]))
        over = max(0, n_agg - FREE_CAPS["places_aggregate"])
        if over:
            max_markets = FREE_CAPS["places_aggregate"] // per_market
            print(f"    → ≈ ${over / 1000 * AGGREGATE_USD_PER_1K:.0f} at ${AGGREGATE_USD_PER_1K:.0f}/1,000;"
                  f" stays free with ≤ {max_markets} markets at keep=1 or fewer google_types/radii")
    print("  caps are per billing account per month; calls already in data/cache are free on re-runs")


def cmd_discover(cfg, args):
    from .discover import discover

    client = google_client(args)
    try:
        df = discover(cfg, client)
    finally:
        print(client.usage_report())
    from .discover import apply_review, carry_over_keep

    MARKETS.parent.mkdir(parents=True, exist_ok=True)
    old = pd.read_csv(MARKETS) if MARKETS.exists() else None
    df = apply_review(carry_over_keep(df, old), REVIEW)
    df.to_csv(MARKETS, index=False, encoding="utf-8-sig")
    kept = int((df["keep"] == 1).sum())
    print(f"→ {MARKETS}: {len(df)} places, {kept} keep=1, {len(df) - kept} keep=0 (suggested, see `note`)")
    print(f"  Set keep=0 on anything that is not a real market (in {MARKETS}, or in {REVIEW} for cloud runs),")
    print("  then run `estimate` and `enrich`.")


def review_report(df, samples=4):
    """Markdown summary of markets.csv: what was kept/dropped, by primary type and keyword."""
    from .discover import apply_review

    df = apply_review(df, REVIEW)
    keep = pd.to_numeric(df.get("keep", 1), errors="coerce").fillna(1) != 0
    kept, dropped = df[keep], df[~keep]
    lines = [f"## markets.csv: {len(df)} places — keep=1: {len(kept)}, keep=0: {len(dropped)}", ""]

    def table(sub, title):
        lines.extend([f"### {title}", "", "| primary_type | n | examples |", "|---|---:|---|"])
        counts = sub["primary_type"].fillna("(none)").value_counts()
        for t, n in counts.items():
            names = sub.loc[sub["primary_type"].fillna("(none)") == t, "name"].head(samples).tolist()
            lines.append(f"| {t} | {n} | {' · '.join(str(x) for x in names)} |")
        lines.append("")

    table(kept, "keep=1 by primary_type")
    if "keywords" in kept:
        only = kept["keywords"].fillna("").str.split("|").map(len) == 1
        by_kw = kept.loc[only, "keywords"].value_counts()
        lines.extend(["### keep=1 found by a single keyword only", "", "| keyword | n |", "|---|---:|"])
        lines.extend(f"| {k} | {n} |" for k, n in by_kw.items())
        lines.append("")
    if "note" in dropped:
        lines.extend(["### keep=0 reasons", "", "| note | n |", "|---|---:|"])
        lines.extend(f"| {k} | {n} |" for k, n in dropped["note"].fillna("").value_counts().head(20).items())
    return "\n".join(lines)


def cmd_review(cfg, args):
    report = review_report(need(MARKETS, "discover"))
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(report + "\n")


def cmd_enrich(cfg, args):
    from .enrich import enrich

    markets = load_markets()
    client = google_client(args)
    try:
        df = enrich(markets, client)
    finally:
        print(client.usage_report())
    df.to_csv(DETAILS, index=False, encoding="utf-8-sig")
    print(f"→ {DETAILS} ({len(df)} rows)")


def cmd_surround(cfg, args):
    from .surround import surround

    markets = load_markets()
    gc = google_client(args) if cfg["surroundings"]["source"] == "google_aggregate" else None
    try:
        df = surround(cfg, markets, google_client=gc, overpass=overpass_client())
    finally:
        if gc:
            print(gc.usage_report())
    df.to_csv(SURROUND, index=False, encoding="utf-8-sig")
    print(f"→ {SURROUND} ({len(df.columns) - 1} features)")


def cmd_score(cfg, args):
    from .score import build_features, score

    sc = cfg["scoring"]
    df = build_features(
        load_markets(),
        need(DETAILS, "enrich"),
        need(SURROUND, "surround"),
        sc.get("rating_prior_reviews", 30),
    )
    ranked = score(df, sc["pillars"], sc.get("profile_top_pct", 30))
    ranked.to_csv(RANKED, index=False, encoding="utf-8-sig")
    cols = ["rank", "score", *[f"pillar_{k}" for k in sc["pillars"] if f"pillar_{k}" in ranked], "name", "profile"]
    print(ranked[cols].head(20).to_string(index=False))
    print(f"→ {RANKED}")


def cmd_map(cfg, args):
    from .mapviz import make_map

    ranked = need(RANKED, "score")
    make_map(ranked, cfg.get("output", {}).get("top_n_map", 50)).save(str(MAP))
    print(f"→ {MAP} (open in a browser)")


def cmd_dashboard(cfg, args):
    from .dashboard import build_payload, render
    from .score import build_features

    df = build_features(
        load_markets(),
        need(DETAILS, "enrich"),
        need(SURROUND, "surround"),
        cfg["scoring"].get("rating_prior_reviews", 30),
    )
    DASHBOARD.write_text(render(build_payload(df, cfg)), encoding="utf-8")
    print(f"→ {DASHBOARD} ({len(df)} markets) — double-click to open; weights/filters update live")


def cmd_all(cfg, args):
    for fn in (cmd_discover, cmd_enrich, cmd_surround, cmd_score, cmd_map, cmd_dashboard):
        print(f"\n== {fn.__name__[4:]} ==")
        fn(cfg, args)


COMMANDS = {
    "estimate": (cmd_estimate, "estimate API calls before spending quota"),
    "review": (cmd_review, "summarise data/markets.csv by type/keyword to decide what to drop"),
    "discover": (cmd_discover, "1. find markets (Text Search) → data/markets.csv"),
    "enrich": (cmd_enrich, "2. reviews / rating / opening hours → data/market_details.csv"),
    "surround": (cmd_surround, "3. count POIs around each market → data/surroundings.csv"),
    "score": (cmd_score, "4. rank markets (scoring.pillars) → data/ranked.csv"),
    "map": (cmd_map, "5. interactive map → data/map.html"),
    "dashboard": (cmd_dashboard, "6. dashboard: tune weights/filters live → data/dashboard.html"),
    "all": (cmd_all, "run stages 1–6"),
}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="sitefinder", description="หาทำเลตลาดสำหรับตั้งร้าน")
    parser.add_argument("command", choices=COMMANDS, help=" | ".join(f"{k}: {v[1]}" for k, v in COMMANDS.items()))
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--max-calls", type=int, default=4900, help="stop after this many billable Google calls in one run (cached calls are free)")
    parser.add_argument("--markets", type=int, default=400, help="assumed market count for `estimate`")
    args = parser.parse_args(argv)

    load_env()
    cfg = load_config(args.config)
    COMMANDS[args.command][0](cfg, args)


if __name__ == "__main__":
    main()
