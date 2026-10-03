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
CACHE = DATA / "cache"


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


def cmd_estimate(cfg, args):
    n_tiles = len(plan_tiles(cfg))
    n_kw = len(cfg["discover"]["keywords"])
    sc = cfg["surroundings"]
    n_feat = len(sc["google_types"]) if sc["source"] == "google_aggregate" else 0
    n_markets = args.markets
    if MARKETS.exists():
        n_markets = len(pd.read_csv(MARKETS))
    print(f"grid: {n_tiles} tiles × {n_kw} keywords")
    print(f"  Text Search Pro       ≈ {n_tiles * n_kw:,} – {n_tiles * n_kw * 3:,} calls (free cap 5,000/month)")
    print(f"  Place Details Ent.    ≈ {n_markets:,} calls (free cap 1,000/month)  [markets ≈ {n_markets}]")
    print(f"  Places Aggregate      ≈ {n_markets * n_feat * len(sc['radii_m']):,} calls"
          "  (check its price in Cloud Console; set surroundings.source: osm for a free run)")
    print("  (calls already in data/cache are free on re-runs)")


def cmd_discover(cfg, args):
    from .discover import discover

    client = google_client(args)
    try:
        df = discover(cfg, client)
    finally:
        print(client.usage_report())
    MARKETS.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(MARKETS, index=False, encoding="utf-8-sig")
    print(f"→ {MARKETS} ({len(df)} markets). Open it, delete rows that are not real markets, then run `enrich`.")


def cmd_enrich(cfg, args):
    from .enrich import enrich

    markets = need(MARKETS, "discover")
    client = google_client(args)
    try:
        df = enrich(markets, client)
    finally:
        print(client.usage_report())
    df.to_csv(DETAILS, index=False, encoding="utf-8-sig")
    print(f"→ {DETAILS} ({len(df)} rows)")


def cmd_surround(cfg, args):
    from .surround import surround

    markets = need(MARKETS, "discover")
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
    persona = args.persona or sc["persona"]
    if persona not in sc["personas"]:
        sys.exit(f"unknown persona '{persona}', choose from {list(sc['personas'])}")
    df = build_features(
        need(MARKETS, "discover"),
        need(DETAILS, "enrich"),
        need(SURROUND, "surround"),
        sc.get("rating_prior_reviews", 30),
    )
    ranked = score(df, sc["personas"][persona])
    ranked.to_csv(RANKED, index=False, encoding="utf-8-sig")
    cols = ["rank", "score", "name", "reviews", "rating", "why"]
    print(f"persona: {persona}")
    print(ranked[cols].head(20).to_string(index=False))
    print(f"→ {RANKED}")


def cmd_map(cfg, args):
    from .mapviz import make_map

    ranked = need(RANKED, "score")
    make_map(ranked, cfg.get("output", {}).get("top_n_map", 50)).save(str(MAP))
    print(f"→ {MAP} (open in a browser)")


def cmd_all(cfg, args):
    for fn in (cmd_discover, cmd_enrich, cmd_surround, cmd_score, cmd_map):
        print(f"\n== {fn.__name__[4:]} ==")
        fn(cfg, args)


COMMANDS = {
    "estimate": (cmd_estimate, "estimate API calls before spending quota"),
    "discover": (cmd_discover, "1. find markets (Text Search) → data/markets.csv"),
    "enrich": (cmd_enrich, "2. reviews / rating / opening hours → data/market_details.csv"),
    "surround": (cmd_surround, "3. count POIs around each market → data/surroundings.csv"),
    "score": (cmd_score, "4. rank markets for a persona → data/ranked.csv"),
    "map": (cmd_map, "5. interactive map → data/map.html"),
    "all": (cmd_all, "run stages 1–5"),
}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="sitefinder", description="หาทำเลตลาดสำหรับตั้งร้าน")
    parser.add_argument("command", choices=COMMANDS, help=" | ".join(f"{k}: {v[1]}" for k, v in COMMANDS.items()))
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--persona", help="override scoring.persona")
    parser.add_argument("--max-calls", type=int, default=4000, help="stop after this many billable Google calls")
    parser.add_argument("--markets", type=int, default=400, help="assumed market count for `estimate`")
    args = parser.parse_args(argv)

    load_env()
    cfg = load_config(args.config)
    COMMANDS[args.command][0](cfg, args)


if __name__ == "__main__":
    main()
