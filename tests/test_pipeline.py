import json

import numpy as np
import pandas as pd
import pytest

from sitefinder.cache import DiskCache
from sitefinder.discover import discover
from sitefinder.enrich import enrich
from sitefinder.geo import Rect, haversine_m, tiles
from sitefinder.google import GoogleAPIError, GoogleClient
from sitefinder.hours import hours_features
from sitefinder.mapviz import make_map
from sitefinder.osm import build_count_query, parse_counts
from sitefinder.score import build_features, score
from sitefinder.surround import external_counts, google_counts


def period(od, oh, cd, ch, cm=0):
    return {"open": {"day": od, "hour": oh, "minute": 0}, "close": {"day": cd, "hour": ch, "minute": cm}}


# ---------- geo ----------

def test_tiles_cover_bbox():
    bbox = {"south": 12.55, "north": 13.62, "west": 100.78, "east": 101.62}
    ts = tiles(bbox, 0.1)
    assert len(ts) == 11 * 9
    assert min(t.south for t in ts) == 12.55 and max(t.north for t in ts) == 13.62
    assert min(t.west for t in ts) == 100.78 and max(t.east for t in ts) == 101.62


def test_subdivide_into_quadrants():
    subs = Rect(0, 0, 2, 2).subdivide()
    assert len(subs) == 4
    assert {(s.south, s.west) for s in subs} == {(0, 0), (0, 1), (1, 0), (1, 1)}


def test_haversine_about_111km_per_degree_lat():
    assert haversine_m(13.0, 100.0, 14.0, 100.0) == pytest.approx(111_195, rel=1e-3)


# ---------- hours ----------

def test_evening_market_weekends_only():
    hours = {"periods": [period(6, 16, 6, 22), period(0, 16, 0, 22)]}  # Sat + Sun 16:00–22:00
    f = hours_features(hours)
    assert f["days_open"] == pytest.approx(2 / 7)
    assert f["open_evening"] == pytest.approx(2 / 7)
    assert f["open_night"] == pytest.approx(2 / 7)
    assert f["open_morning"] == 0


def test_overnight_market_wraps_midnight_and_week():
    hours = {"periods": [period(6, 18, 0, 2)]}  # Saturday 18:00 → Sunday 02:00
    f = hours_features(hours)
    assert f["days_open"] == pytest.approx(2 / 7)  # touches Saturday and Sunday
    assert f["open_night"] == pytest.approx(1 / 7)


def test_close_with_minutes_includes_that_hour():
    f = hours_features({"periods": [period(1, 5, 1, 9, cm=30)]})  # Mon 05:00–09:30
    assert f["open_morning"] == pytest.approx(1 / 7)


def test_open_24_7():
    f = hours_features({"periods": [{"open": {"day": 0, "hour": 0, "minute": 0}}]})
    assert f["days_open"] == 1 and f["open_morning"] == 1 and f["open_night"] == 1


def test_unknown_hours():
    f = hours_features(None)
    assert f["hours_known"] == 0 and f["open_evening"] is None


# ---------- fake Google ----------

class FakeGoogle:
    """Serves Text Search pages from a dict; records calls."""

    def __init__(self, places_by_keyword, page_size=20):
        self.places = places_by_keyword
        self.page_size = page_size
        self.calls = []

    def text_search(self, query, rect, page_token=None):
        self.calls.append((query, rect, page_token))
        inside = [
            p for p in self.places.get(query, [])
            if rect.south <= p["location"]["latitude"] < rect.north
            and rect.west <= p["location"]["longitude"] < rect.east
        ]
        start = int(page_token or 0)
        page = inside[start:start + self.page_size]
        out = {"places": page}
        if start + self.page_size < min(len(inside), 60):
            out["nextPageToken"] = str(start + self.page_size)
        return out

    def place_details(self, place_id):
        n = int(place_id.split("_")[1])
        return {
            "id": place_id,
            "rating": 4.0 + (n % 10) / 10,
            "userRatingCount": n * 10,
            "businessStatus": "OPERATIONAL",
            "regularOpeningHours": {"periods": [period(d, 16, d, 21) for d in range(7)]} if n % 2 else None,
        }

    def count_places(self, lat, lng, radius_m, types):
        if "bad_type" in types:
            raise GoogleAPIError(400, "Unsupported types: bad_type")
        return int(radius_m / 100) + len(types)


def place(i, lat, lng, name=None, address="ต.แสนสุข อ.เมืองชลบุรี ชลบุรี 20130", status="OPERATIONAL"):
    return {
        "id": f"p_{i}",
        "displayName": {"text": name or f"ตลาดนัด {i}"},
        "formattedAddress": address,
        "location": {"latitude": lat, "longitude": lng},
        "types": ["market"],
        "primaryType": "market",
        "businessStatus": status,
        "googleMapsUri": f"https://maps.google.com/?cid={i}",
    }


CFG = {
    "area": {
        "address_must_contain": ["ชลบุรี"],
        "bbox": {"south": 13.0, "north": 13.2, "west": 100.9, "east": 101.1},
        "tile_deg": 0.1,
        "max_subdivide_depth": 2,
    },
    "discover": {
        "keywords": ["ตลาดนัด", "walking street"],
        "name_must_match": "ตลาด|walking street",
        "name_exclude": "ตลาดหลักทรัพย์",
    },
}


def test_discover_filters_dedupes_and_tracks_keywords():
    shared = place(1, 13.05, 100.95)
    fake = FakeGoogle({
        "ตลาดนัด": [
            shared,
            place(2, 13.15, 101.05),
            place(3, 13.05, 100.95, address="อ.เมือง ระยอง"),              # other province
            place(4, 13.05, 100.95, name="ตลาดหลักทรัพย์ สาขาชลบุรี"),     # excluded name
            place(5, 13.05, 100.95, name="ร้านกาแฟ"),                      # not a market
            place(6, 13.05, 100.95, status="CLOSED_PERMANENTLY"),
        ],
        "walking street": [shared],
    })
    df = discover(CFG, fake, log=lambda *_: None)
    assert sorted(df["place_id"]) == ["p_1", "p_2"]
    assert df.set_index("place_id").loc["p_1", "keywords"] == "walking street|ตลาดนัด"


def test_discover_subdivides_saturated_tiles():
    dense = [place(i, 13.01 + (i % 9) * 0.01, 100.91 + (i // 9) * 0.01) for i in range(80)]
    fake = FakeGoogle({"ตลาดนัด": dense, "walking street": []})
    df = discover(CFG, fake, log=lambda *_: None)
    assert len(df) == 80  # a single tile caps at 60; subdividing recovers the rest
    assert len([c for c in fake.calls if c[0] == "ตลาดนัด"]) > 4


# ---------- enrich / surround / score / map ----------

@pytest.fixture
def markets():
    return pd.DataFrame([
        {"place_id": f"p_{i}", "name": f"ตลาด {i}", "lat": 13.0 + i * 0.01, "lng": 101.0,
         "maps_url": f"https://maps.google.com/?cid={i}"}
        for i in range(1, 6)
    ])


def test_google_counts_skips_rejected_feature(markets):
    calls = []
    fake = FakeGoogle({})
    orig = fake.count_places
    fake.count_places = lambda *a: calls.append(a) or orig(*a)
    specs = {"conv_store": (["convenience_store"], [500, 1500]), "bad": (["bad_type"], [500, 1500])}
    df = google_counts(markets, fake, specs, log=lambda *_: None)
    assert df["conv_store_500"].tolist() == [6] * 5
    assert df["bad_500"].isna().all() and df["bad_1500"].isna().all()
    assert sum(1 for c in calls if "bad_type" in c[3]) == 1  # gave up after the first 400


def test_google_counts_aborts_on_auth_error(markets):
    fake = FakeGoogle({})
    fake.count_places = lambda *a: (_ for _ in ()).throw(GoogleAPIError(403, "API not enabled"))
    with pytest.raises(GoogleAPIError):
        google_counts(markets, fake, {"conv_store": (["convenience_store"], [500])}, log=lambda *_: None)


def test_external_counts_by_radius_and_category(markets):
    pois = pd.DataFrame({
        "lat": [13.01, 13.011, 13.03, 13.5],
        "lng": [101.0, 101.0, 101.0, 101.0],
        "category": ["ปั๊ม", "ชุมชน", "ชุมชน", "ชุมชน"],
    })
    df = external_counts(markets, "seven", pois, [500, 3000]).set_index("place_id")
    assert df.loc["p_1", "seven_500"] == 2
    assert df.loc["p_1", "seven_3000"] == 3
    assert df.loc["p_1", "seven_ปั๊ม_500"] == 1
    assert df.loc["p_5", "seven_500"] == 0


def test_overpass_query_and_parse():
    q = build_count_query(13.0, 101.0, 500, {"conv": ['nwr["shop"="convenience"]'], "school": ['nwr["amenity"="school"]']})
    assert q.count("out count;") == 2 and "around:500,13.0,101.0" in q
    data = {"elements": [{"type": "count", "tags": {"total": "7"}}, {"type": "count", "tags": {"total": "2"}}]}
    assert parse_counts(data, ["conv", "school"]) == {"conv": 7, "school": 2}


def test_end_to_end_score_and_map(markets, tmp_path):
    fake = FakeGoogle({})
    details = enrich(markets, fake, log=lambda *_: None)
    surround_df = google_counts(markets, fake, {"conv_store": (["convenience_store"], [500])}, log=lambda *_: None)
    surround_df["conv_store_500"] = [1, 2, 3, 4, 5]
    df = build_features(markets, details, surround_df)
    pillars = {
        "market": {"label": "ตลาด", "weight": 2, "measures": {"reviews": 1}},
        "activity": {"label": "คึกคัก", "tag": "ย่านคึกคัก", "weight": 1, "measures": {"conv_store_500": 1, "missing_col": 5}},
        "residents": {"label": "ที่พัก", "tag": "ย่านที่พัก", "weight": 1, "measures": {"missing_col": 1}},
    }
    logs = []
    ranked = score(df, pillars, profile_top_pct=30, log=logs.append)
    assert ranked["rank"].tolist() == [1, 2, 3, 4, 5]
    assert ranked.iloc[0]["place_id"] == "p_5"  # most reviews + most stores
    assert ranked["score"].between(0, 100).all()
    assert "pillar_residents" not in ranked and any("residents" in m for m in logs)  # no usable measure → skipped
    top = ranked.iloc[0]
    assert top["pillar_market"] == 100 and top["pillar_activity"] == 100 and top["score"] == 100
    assert top["profile"] == "ย่านคึกคัก" and ranked.iloc[-1]["profile"] == ""
    assert (ranked["profile"] == "ย่านคึกคัก").sum() == 2  # top 30% of 5 markets = ranks 1–2 (pct ≥ 0.7)
    assert top["why"].startswith("ตลาด")
    out = tmp_path / "map.html"
    make_map(ranked, top_n=2).save(str(out))
    assert "ตลาด 5" in out.read_text(encoding="utf-8")


def test_bayesian_rating_pulls_small_samples_to_mean(markets):
    details = pd.DataFrame({"place_id": markets["place_id"], "rating": [5.0, 4.0, 4.0, 4.0, 4.0],
                            "reviews": [2, 2000, 2000, 2000, 2000]})
    df = build_features(markets, details, markets[["place_id"]], rating_prior_reviews=30)
    assert df.loc[0, "rating_adj"] == pytest.approx((30 * 4.2 + 2 * 5.0) / 32)  # 5.0 from 2 reviews → ~4.25


# ---------- client: cache + budget ----------

class FakeResp:
    def __init__(self, status, payload):
        self.status_code, self._payload, self.text = status, payload, json.dumps(payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses, self.n = list(responses), 0

    def request(self, *a, **k):
        self.n += 1
        return self.responses.pop(0)


def test_client_caches_and_enforces_budget(tmp_path):
    session = FakeSession([FakeResp(200, {"count": "12"}), FakeResp(200, {"count": "3"})])
    client = GoogleClient("key", DiskCache(tmp_path), max_calls=2, min_interval_s=0, session=session)
    assert client.count_places(13.0, 101.0, 500, ["convenience_store"]) == 12
    assert client.count_places(13.0, 101.0, 500, ["convenience_store"]) == 12  # cached
    assert session.n == 1
    assert client.count_places(13.0, 101.0, 1500, ["convenience_store"]) == 3
    from sitefinder.google import BudgetExceeded
    with pytest.raises(BudgetExceeded):
        client.count_places(13.0, 101.0, 3000, ["convenience_store"])


def test_client_raises_api_error_message(tmp_path):
    session = FakeSession([FakeResp(403, {"error": {"message": "Places API (New) has not been used"}})])
    client = GoogleClient("key", DiskCache(tmp_path), min_interval_s=0, session=session)
    with pytest.raises(GoogleAPIError, match="has not been used"):
        client.place_details("abc")


# ---------- keep column ----------

from sitefinder.discover import carry_over_keep, suggest_keep  # noqa: E402

DISC = {
    "suspect_primary_type": "restaurant|cafe|lodging|real_estate",
    "suspect_name": "^ร้าน|คอนโด",
}


def _found():
    return pd.DataFrame({
        "place_id": ["a", "b", "c", "d", "e"],
        "name": ["ตลาดนัดวัดใหม่", "ร้านส้มตำ ตลาดเก่า", "คอนโด ตลาดบางแสน", "ตลาดคาเฟ่", "ตลาดหนองมน"],
        "primary_type": ["buddhist_temple", "thai_restaurant", "apartment_building", "cafe", "market"],
    })


def test_suggest_keep_flags_shops_but_not_temple_markets():
    df = suggest_keep(_found(), DISC).set_index("place_id")
    assert df["keep"].to_dict() == {"a": 1, "b": 0, "c": 0, "d": 0, "e": 1}
    assert df.loc["b", "note"] == "primary_type=thai_restaurant"
    assert "shop" in df.loc["c", "note"]


def test_rerun_discover_keeps_manual_edits():
    old = suggest_keep(_found(), DISC)
    old.loc[old["place_id"] == "d", "keep"] = 1          # user: the café one is a real market
    old.loc[old["place_id"] == "e", ["keep", "note"]] = [0, "ไปดูแล้ว เงียบ"]
    new = suggest_keep(pd.concat([_found(), pd.DataFrame(
        {"place_id": ["f"], "name": ["ตลาดใหม่"], "primary_type": ["market"]})]), DISC)
    merged = carry_over_keep(new, old).set_index("place_id")
    assert merged.loc["d", "keep"] == 1
    assert merged.loc["e", "keep"] == 0 and merged.loc["e", "note"] == "ไปดูแล้ว เงียบ"
    assert merged.loc["f", "is_new"] == 1 and merged.loc["a", "is_new"] == 0


def test_cli_stages_only_use_keep_rows(tmp_path, monkeypatch, capsys):
    from sitefinder import cli
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    pd.DataFrame({"place_id": ["a", "b", "c"], "keep": [1, 0, ""]}).to_csv("data/markets.csv", index=False)
    assert cli.load_markets()["place_id"].tolist() == ["a", "c"]  # blank keep counts as keep

    cfg = {"area": CFG["area"], "discover": {"keywords": ["x"]},
           "surroundings": {"source": "google_aggregate", "radii_m": [500, 1500],
                            "google_types": {f"f{i}": ["t"] for i in range(6)}}}
    cli.cmd_estimate(cfg, type("A", (), {"markets": 400})())
    out = capsys.readouterr().out
    assert "markets: 2" in out and "24 calls" in out


def test_osm_extra_not_double_counted_when_source_is_osm(markets):
    from sitefinder.surround import surround

    class FakeOverpass:
        def __init__(self):
            self.features = []

        def count(self, lat, lng, r, filters):
            self.features.append(tuple(filters))
            return {k: 1 for k in filters}

    ov = FakeOverpass()
    cfg = {"surroundings": {"source": "osm", "radii_m": [500],
                            "osm_filters": {"school": ["s"], "conv_store": ["c"]},
                            "osm_extra": {"school": ["s"], "industrial": ["i"]}}}
    df = surround(cfg, markets, overpass=ov, log=lambda *_: None)
    assert sorted(c for c in df.columns if c != "place_id") == ["conv_store_500", "industrial_500", "school_500"]


# ---------- workplaces / factories ----------

from sitefinder.osm import _to_local_m, clipped_area_ha, polygons_from_overpass  # noqa: E402
from sitefinder.surround import google_call_count, google_specs, load_pois  # noqa: E402


def test_google_specs_per_feature_radii():
    sc = {"radii_m": [500, 1500], "google_types": {
        "conv_store": ["convenience_store"],
        "workplace": {"types": ["corporate_office", "city_hall"], "radii_m": [500]},
    }}
    specs = google_specs(sc)
    assert specs["conv_store"] == (["convenience_store"], [500, 1500])
    assert specs["workplace"] == (["corporate_office", "city_hall"], [500])
    assert google_call_count(sc, 400) == 400 * 3


def test_shipped_config_stays_within_aggregate_free_cap():
    import yaml
    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    assert google_call_count(cfg["surroundings"], 400) <= 5000


def _square(lat0, lng0, half_m, as_relation=False):
    """Overpass `out geom` element for a square of side 2*half_m centred on (lat0, lng0)."""
    dlat = half_m / 111_320
    dlng = half_m / (111_320 * np.cos(np.radians(lat0)))
    corners = [(lat0 - dlat, lng0 - dlng), (lat0 - dlat, lng0 + dlng),
               (lat0 + dlat, lng0 + dlng), (lat0 + dlat, lng0 - dlng)]
    pts = [{"lat": a, "lon": b} for a, b in corners + corners[:1]]
    if not as_relation:
        return {"type": "way", "geometry": pts}
    # outer ring split into two unclosed ways, as large estates are often mapped
    return {"type": "relation", "members": [
        {"type": "way", "role": "outer", "geometry": pts[:3]},
        {"type": "way", "role": "outer", "geometry": pts[2:]},
    ]}


def test_industrial_area_way_and_split_relation():
    proj = _to_local_m(13.0, 101.0)
    for as_rel in (False, True):
        polys = polygons_from_overpass({"elements": [_square(13.0, 101.0, 100, as_rel)]}, proj)
        assert clipped_area_ha(polys, [1000])[1000] == pytest.approx(4.0, rel=0.01)  # 200 m × 200 m


def test_industrial_area_clipped_to_circle_and_not_double_counted():
    proj = _to_local_m(13.0, 101.0)
    big = _square(13.0, 101.0, 1000)              # 2 km × 2 km = 400 ha
    dup = _square(13.0, 101.0, 1000, as_relation=True)
    polys = polygons_from_overpass({"elements": [big, dup]}, proj)
    areas = clipped_area_ha(polys, [500, 5000])
    assert areas[500] == pytest.approx(np.pi * 500 ** 2 / 10_000, rel=0.01)  # circle fully inside
    assert areas[5000] == pytest.approx(400, rel=0.01)                       # whole square, once
    assert clipped_area_ha([], [500]) == {500: 0.0}


def test_estate_workers_summed_by_weight(markets, tmp_path):
    path = tmp_path / "estates.csv"
    pd.DataFrame({
        "name": ["A", "B", "C", "D"],
        "lat": [13.01, 13.02, 13.30, None],
        "lng": [101.0, 101.0, 101.0, 101.0],
        "workers": [50000, "", 9000, 1000],
    }).to_csv(path, index=False)
    logs = []
    pois = load_pois(path, "workers", logs.append)
    assert len(pois) == 3 and any("['B']" in m for m in logs)   # missing workers → 0, warned
    df = external_counts(markets, "estate_workers", pois, [3000], "workers").set_index("place_id")
    assert df.loc["p_1", "estate_workers_3000"] == 50000
    assert df.loc["p_3", "estate_workers_3000"] == 50000  # 2.2 km away
    assert df.loc["p_5", "estate_workers_3000"] == 0      # 4.4 km away


def test_pillars_stop_double_counting(markets):
    """Three measures of the same thing inside one pillar weigh no more than one measure elsewhere."""
    details = pd.DataFrame({"place_id": markets["place_id"], "reviews": [50, 40, 30, 20, 10], "rating": 4.0})
    surround = pd.DataFrame({"place_id": markets["place_id"],
                             "conv_store_500": [1, 2, 3, 4, 5], "conv_store_1500": [1, 2, 3, 4, 5], "seven_500": [1, 2, 3, 4, 5]})
    df = build_features(markets, details, surround)
    pillars = {
        "market": {"weight": 1, "measures": {"reviews": 1}},
        "activity": {"weight": 1, "measures": {"conv_store_500": 1, "conv_store_1500": 1, "seven_500": 1}},
    }
    ranked = score(df, pillars, log=lambda *_: None).set_index("place_id")
    # p_1 is best on reviews, p_5 best on stores: equal pillar weights → a tie, not 3:1 for stores
    assert ranked.loc["p_1", "score"] == ranked.loc["p_5", "score"]


# ---------- review/keep.csv ----------

from sitefinder.discover import apply_review  # noqa: E402


def test_review_file_overrides_markets_csv(tmp_path):
    markets = pd.DataFrame({"place_id": ["a", "b", "c"], "keep": [1, 0, 1], "note": ["", "auto", ""]})
    review = tmp_path / "keep.csv"
    # written by the dashboard: BOM + header, a Thai note with a comma
    review.write_text('﻿place_id,keep,note\na,0,"ร้านในตลาด, ไม่ใช่ตลาด"\nb,1,\nzzz,0,not in list\n', encoding="utf-8")
    out = apply_review(markets, review).set_index("place_id")
    assert out["keep"].to_dict() == {"a": 0, "b": 1, "c": 1}
    assert out.loc["a", "note"] == "ร้านในตลาด, ไม่ใช่ตลาด"
    assert out.loc["b", "note"] == "auto"  # empty note in review keeps the old one
    assert apply_review(markets, tmp_path / "missing.csv") is markets


def test_load_markets_applies_review(tmp_path, monkeypatch):
    from sitefinder import cli
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "review").mkdir()
    pd.DataFrame({"place_id": ["a", "b"], "keep": [1, 1]}).to_csv("data/markets.csv", index=False)
    (tmp_path / "review" / "keep.csv").write_text("place_id,keep\nb,0\n", encoding="utf-8")
    assert cli.load_markets()["place_id"].tolist() == ["a"]
