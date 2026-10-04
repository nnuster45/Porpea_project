"""Stage 3: what is around each market — Places Aggregate / OSM counts, OSM land-use area, external files."""

import re
from pathlib import Path

import numpy as np
import pandas as pd

from .geo import haversine_m
from .google import BudgetExceeded, GoogleAPIError


def google_specs(sc):
    """Normalise surroundings.google_types to {feature: (types, radii)}.

    Each entry is either a list of types (uses surroundings.radii_m) or
    {types: [...], radii_m: [...]} to override the radii for that feature.
    """
    specs = {}
    for feature, spec in (sc.get("google_types") or {}).items():
        if isinstance(spec, dict):
            specs[feature] = (list(spec["types"]), list(spec.get("radii_m", sc["radii_m"])))
        else:
            specs[feature] = (list(spec), list(sc["radii_m"]))
    return specs


def google_call_count(sc, n_markets):
    return n_markets * sum(len(radii) for _, radii in google_specs(sc).values())


def google_counts(markets, client, specs, log=print):
    rows = []
    broken = set()  # features whose types the API rejected; don't keep paying for errors
    out_of_budget = False
    for i, m in enumerate(markets.itertuples(index=False), 1):
        row = {"place_id": m.place_id}
        for feature, (types, radii) in specs.items():
            for r in radii:
                col = f"{feature}_{r}"
                if feature in broken or out_of_budget:
                    row[col] = np.nan
                    continue
                try:
                    row[col] = client.count_places(m.lat, m.lng, r, types)
                except BudgetExceeded as e:
                    out_of_budget = True
                    row[col] = np.nan
                    log(f"  ! stopping Google counts at market {i}/{len(markets)}: {e} — the rest stay 'unknown'")
                except GoogleAPIError as e:
                    if e.status in (401, 403) or "API key" in str(e):
                        raise  # key/billing/API-not-enabled: every call would fail the same way
                    if e.status == 400:
                        broken.add(feature)
                        log(f"  ! feature '{feature}' {types} rejected, skipping it: {e}")
                    else:
                        log(f"  ! count failed for {m.name} {col}: {e}")
                    row[col] = np.nan
        rows.append(row)
        if i % 25 == 0:
            log(f"  {i}/{len(markets)}")
    return pd.DataFrame(rows)


class _GiveUp:
    """Stop calling a service after `limit` failures in a row (e.g. it rejects every request)."""

    def __init__(self, what, log, limit=5):
        self.what, self.log, self.limit, self.streak = what, log, limit, 0

    @property
    def dead(self):
        return self.streak >= self.limit

    def ok(self):
        self.streak = 0

    def fail(self, where, err):
        self.streak += 1
        self.log(f"  ! {self.what} failed for {where}: {err}")
        if self.dead:
            self.log(f"  ! {self.what}: {self.limit} failures in a row — skipping the rest (left empty)")


def osm_counts(markets, overpass, osm_filters, radii, log=print):
    rows = []
    guard = _GiveUp("overpass", log)
    for i, m in enumerate(markets.itertuples(index=False), 1):
        row = {"place_id": m.place_id}
        for r in radii:
            counts = {k: np.nan for k in osm_filters}
            if not guard.dead:
                try:
                    counts = overpass.count(m.lat, m.lng, r, osm_filters)
                    guard.ok()
                except Exception as e:  # Overpass is a shared free service; keep going on errors
                    guard.fail(f"{m.name} r={r}", e)
            for feature, n in counts.items():
                row[f"{feature}_{r}"] = n
        rows.append(row)
        if i % 25 == 0:
            log(f"  {i}/{len(markets)}")
    return pd.DataFrame(rows)


def osm_areas(markets, overpass, area_filters, radii, log=print):
    """`<feature>_ha_<r>`: hectares of the land use inside each radius (big plants weigh more)."""
    rows = []
    guard = _GiveUp("overpass area", log)
    for i, m in enumerate(markets.itertuples(index=False), 1):
        row = {"place_id": m.place_id}
        for feature, filters in area_filters.items():
            areas = {r: np.nan for r in radii}
            if not guard.dead:
                try:
                    areas = overpass.area_ha(m.lat, m.lng, radii, filters, feature=feature)
                    guard.ok()
                except Exception as e:
                    guard.fail(f"{m.name} {feature}", e)
            for r, ha in areas.items():
                row[f"{feature}_ha_{r}"] = ha
        rows.append(row)
        if i % 25 == 0:
            log(f"  {i}/{len(markets)}")
    return pd.DataFrame(rows)


def osm_campus(markets, local, campus_features, radii, log=print):
    """`<feature>_<r>`: distinct institutions (campuses) within each radius."""
    rows = []
    for m in markets.itertuples(index=False):
        row = {"place_id": m.place_id}
        for feature in campus_features:
            for r, n in local.campus_count(m.lat, m.lng, radii, feature).items():
                row[f"{feature}_{r}"] = n
        rows.append(row)
    return pd.DataFrame(rows)


def _slug(text):
    return re.sub(r"[^0-9a-zA-Zก-๙]+", "_", str(text)).strip("_").lower() or "other"


def load_pois(path, weight_col=None, log=print):
    df = pd.read_csv(path)
    df = df.rename(columns={c: c.lower().strip() for c in df.columns})
    aliases = {"latitude": "lat", "lon": "lng", "long": "lng", "longitude": "lng"}
    df = df.rename(columns={k: v for k, v in aliases.items() if k in df.columns and v not in df.columns})
    if not {"lat", "lng"} <= set(df.columns):
        raise ValueError(f"{path}: needs lat and lng columns, got {list(df.columns)}")
    missing_pos = df["lat"].isna() | df["lng"].isna()
    if missing_pos.any():
        log(f"  ! {path}: {int(missing_pos.sum())} rows without lat/lng ignored")
    df = df[~missing_pos].copy()
    if weight_col:
        weight_col = weight_col.lower()
        if weight_col not in df.columns:
            raise ValueError(f"{path}: weight column '{weight_col}' not found")
        df[weight_col] = pd.to_numeric(df[weight_col], errors="coerce")
        if df[weight_col].isna().any():
            names = df.loc[df[weight_col].isna()].get("name", pd.Series(dtype=str)).tolist()
            log(f"  ! {path}: no '{weight_col}' for {names or int(df[weight_col].isna().sum())} → counted as 0")
            df[weight_col] = df[weight_col].fillna(0)
    return df


def external_counts(markets, name, pois, radii, weight_col=None):
    """POIs from a CSV around each market: count, or sum of `weight_col` (e.g. workers) if given.
    Also split by a `category` column if present."""
    lat, lng = pois["lat"].to_numpy(float), pois["lng"].to_numpy(float)
    weights = pois[weight_col.lower()].to_numpy(float) if weight_col else np.ones(len(pois))
    cats = pois["category"].map(_slug).to_numpy() if "category" in pois.columns else None
    rows = []
    for m in markets.itertuples(index=False):
        dist = haversine_m(m.lat, m.lng, lat, lng)
        row = {"place_id": m.place_id}
        for r in radii:
            within = dist <= r
            row[f"{name}_{r}"] = float(weights[within].sum()) if weight_col else int(within.sum())
            if cats is not None:
                for cat in np.unique(cats):
                    sel = within & (cats == cat)
                    row[f"{name}_{cat}_{r}"] = float(weights[sel].sum()) if weight_col else int(sel.sum())
        rows.append(row)
    return pd.DataFrame(rows)


def surround(cfg, markets, google_client=None, overpass=None, log=print):
    sc = cfg["surroundings"]
    radii = sc["radii_m"]
    frames = []

    if sc["source"] == "google_aggregate":
        log("  Places Aggregate counts…")
        frames.append(google_counts(markets, google_client, google_specs(sc), log))
    elif sc["source"] == "osm":
        log("  OSM counts…")
        frames.append(osm_counts(markets, overpass, sc["osm_filters"], radii, log))
    else:
        raise ValueError(f"unknown surroundings.source: {sc['source']}")

    extra = dict(sc.get("osm_extra") or {})
    if sc["source"] == "osm":  # already counted above
        extra = {k: v for k, v in extra.items() if k not in sc["osm_filters"]}
    if extra:
        log(f"  OSM extra counts ({', '.join(extra)})…")
        frames.append(osm_counts(markets, overpass, extra, radii, log))

    if sc.get("osm_campus"):
        if hasattr(overpass, "campus_count"):
            campus_radii = sc.get("campus_radii_m", radii)
            log(f"  OSM campuses ({', '.join(sc['osm_campus'])})…")
            frames.append(osm_campus(markets, overpass, sc["osm_campus"], campus_radii, log))
        else:
            log("  - osm_campus needs a local OSM extract (`osm-extract`); skipped")

    if sc.get("osm_area"):
        log(f"  OSM land-use area ({', '.join(sc['osm_area'])})…")
        frames.append(osm_areas(markets, overpass, sc["osm_area"], radii, log))

    for ext in cfg.get("external_pois") or []:
        path = Path(ext["path"])
        if not path.exists():
            log(f"  - external '{ext['name']}' not found at {path}, skipping")
            continue
        pois = load_pois(path, ext.get("weight"), log)
        how = f"sum of '{ext['weight']}'" if ext.get("weight") else "count"
        log(f"  external '{ext['name']}': {len(pois)} points ({how})")
        frames.append(external_counts(markets, ext["name"], pois, ext.get("radii_m", radii), ext.get("weight")))

    out = markets[["place_id"]].copy()
    for f in frames:
        out = out.merge(f, on="place_id", how="left")
    return out
