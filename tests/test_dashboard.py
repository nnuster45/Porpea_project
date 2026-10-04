import json
import re
import shutil
import subprocess

import numpy as np
import pandas as pd
import pytest

from sitefinder.dashboard import TEMPLATE, amphoe, build_payload, describe_feature, market_kind, render
from sitefinder.score import build_features, score

PILLAR_SETS = {
    "default_like": {
        "market": {"label": "ตลาด", "weight": 2, "measures": {"reviews": 2, "rating": 1}},
        "activity": {"label": "คึกคัก", "tag": "ย่านคึกคัก", "weight": 1, "measures": {"conv_store_500": 2, "seven_ปั๊ม_500": 1}},
        "workers": {"label": "ทำงาน", "tag": "ย่านที่ทำงาน", "weight": 1.5, "measures": {"industrial_ha_1500": 1}},
    },
    # zero weights, a negative measure, a measure with no column, a pillar with nothing usable
    "edge_cases": {
        "market": {"label": "ตลาด", "weight": 1, "measures": {"reviews": 1, "rating": 0}},
        "activity": {"label": "คึกคัก", "weight": 3, "measures": {"conv_store_500": -1, "open_evening": 2, "missing_col": 4}},
        "residents": {"label": "ที่พัก", "weight": 2, "measures": {"missing_col": 1}},
        "workers": {"label": "ทำงาน", "weight": 0, "measures": {"industrial_ha_1500": 1}},
    },
}

CFG = {
    "area": {"name": "ชลบุรี"},
    "scoring": {"rating_prior_reviews": 30, "profile_top_pct": 30, "pillars": PILLAR_SETS["default_like"],
                "scales": {"reviews": "minmax"}},
    "output": {"top_n_map": 10},
}


@pytest.fixture
def features_df():
    rng = np.random.default_rng(3)
    n = 40
    markets = pd.DataFrame({
        "place_id": [f"p{i}" for i in range(n)],
        "name": [f"ตลาดนัด {i}" if i % 3 else f"Walking Street {i}" for i in range(n)],
        "lat": 13 + rng.random(n) * 0.3,
        "lng": 100.9 + rng.random(n) * 0.3,
        "address": [f"ต.แสนสุข อำเภอเมืองชลบุรี ชลบุรี 20130" if i % 2 else "อ.ศรีราชา ชลบุรี" for i in range(n)],
        "maps_url": "https://maps.google.com",
        "keywords": "ตลาดนัด",
    })
    details = pd.DataFrame({
        "place_id": markets["place_id"],
        "rating": np.where(rng.random(n) < 0.15, np.nan, rng.uniform(3.2, 4.9, n).round(1)),
        "reviews": np.where(rng.random(n) < 0.1, np.nan, rng.integers(0, 900, n)),
        "hours_text": "วันเสาร์: 16:00–21:00",
        "days_open": np.where(rng.random(n) < 0.2, np.nan, rng.integers(1, 8, n) / 7),
        "open_evening": np.where(rng.random(n) < 0.2, np.nan, rng.integers(0, 4, n) / 7),
    })
    surround = pd.DataFrame({
        "place_id": markets["place_id"],
        "conv_store_500": rng.integers(0, 6, n),          # many ties
        "industrial_ha_1500": np.where(rng.random(n) < 0.3, np.nan, rng.uniform(0, 300, n).round(1)),
        "seven_ปั๊ม_500": rng.integers(0, 3, n),
    })
    return build_features(markets, details, surround, rating_prior_reviews=30)


def run_js_scoring(payload, prior):
    js = TEMPLATE.read_text(encoding="utf-8")
    code = re.search(r"// <scoring>[^\n]*\n(.*?)// </scoring>", js, re.S).group(1)
    script = code + f"""
const DATA = {json.dumps(payload, ensure_ascii=False)};
const res = scorePillars(DATA.markets, DATA.pillars, {prior}, DATA.scales);
console.log(JSON.stringify({{scores: res.scores, tiers: res.tiers, pillars: Object.fromEntries(res.pillars.map(p => [p.key, p.scores]))}}));
"""
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("pillar_set", list(PILLAR_SETS))
def test_browser_scores_match_python(features_df, pillar_set):
    pillars = PILLAR_SETS[pillar_set]
    cfg = {**CFG, "scoring": {**CFG["scoring"], "pillars": pillars}}
    payload = build_payload(features_df, cfg)
    js = run_js_scoring(payload, 30)
    ids = [m["id"] for m in payload["markets"]]
    py = score(features_df, pillars, scales=cfg["scoring"].get("scales"), log=lambda *_: None).set_index("place_id")
    for pid, js_score, js_tier in zip(ids, js["scores"], js["tiers"]):
        assert js_score == pytest.approx(py.loc[pid, "score"], abs=0.11), pid  # only last-digit rounding may differ
        assert js_tier == py.loc[pid, "tier"], pid
    assert set(js["pillars"]) == {c[len("pillar_"):] for c in py.columns if c.startswith("pillar_")}
    for key, vals in js["pillars"].items():
        for pid, v in zip(ids, vals):
            assert v * 100 == pytest.approx(py.loc[pid, f"pillar_{key}"], abs=0.051)


def test_payload_shape(features_df):
    payload = build_payload(features_df, CFG)
    keys = [f["key"] for f in payload["features"]]
    assert keys[:2] == ["reviews", "rating"]
    assert {"conv_store_500", "industrial_ha_1500", "seven_ปั๊ม_500", "open_evening", "days_open"} <= set(keys)
    assert "rating_adj" not in keys and "reviews_log" not in keys and "lat" not in keys
    m = payload["markets"][1]
    assert m["amphoe"] == "เมืองชลบุรี" and m["kind"] == "ตลาดนัด"
    assert all(v is None or isinstance(v, (int, float)) for v in m["f"].values())
    json.dumps(payload)  # serialisable (no NaN / numpy types)
    assert "NaN" not in json.dumps(payload)


def test_describe_feature_labels():
    assert describe_feature("conv_store_500") == {"label": "ร้านสะดวกซื้อ", "group": "shops", "unit": "แห่ง", "scale": 1, "radius": 500}
    assert describe_feature("industrial_ha_1500")["label"] == "พื้นที่โรงงาน (OSM)"
    assert describe_feature("campus_3000")["label"].startswith("มหาวิทยาลัย")
    assert "นับทุกตึก" in describe_feature("university_1500")["label"]  # says what Google actually counts
    assert describe_feature("lodging_1500")["group"] == "tourism"
    assert describe_feature("industrial_ha_1500")["unit"] == "ไร่"
    assert describe_feature("seven_ปั๊ม_500")["label"] == "7-Eleven · ปั๊ม"
    assert describe_feature("estate_workers_3000")["group"] == "work"
    assert describe_feature("open_evening")["scale"] == 7
    assert describe_feature("cj_more_500")["group"] == "other"


def test_amphoe_and_kind():
    assert amphoe("123 ตำบลหนองปรือ อำเภอบางละมุง ชลบุรี 20150") == "บางละมุง"
    assert amphoe("อ.ศรีราชา จ.ชลบุรี") == "ศรีราชา"
    assert amphoe("Sattahip District, Chon Buri") == "สัตหีบ"
    assert amphoe("อ.เมือง จ.ชลบุรี") == amphoe("อำเภอเมืองชลบุรี") == "เมืองชลบุรี"
    assert amphoe("ถ.เลียบหาด เมืองพัทยา ชลบุรี") == "บางละมุง"
    assert amphoe("") == "ไม่ระบุ"
    assert market_kind("ตลาดโต้รุ่งบางแสน") == "ตลาดโต้รุ่ง"
    assert market_kind("Naklua Walking Street") == "ถนนคนเดิน"
    assert market_kind("ตลาดสดหนองมน") == "ตลาดทั่วไป"
    assert market_kind("ตลาดหนองมน", "ตลาดนัด") == "ตลาดทั่วไป"   # the search keyword doesn't decide the type
    assert market_kind("ตลาดสดเทศบาลเมืองชลบุรี", "ตลาด|ตลาดโต้รุ่ง") == "ตลาดทั่วไป"


def test_render_embeds_data_safely(features_df):
    df = features_df.copy()
    df.loc[0, "name"] = "ตลาด </script><script>alert(1)</script>"
    html = render(build_payload(df, CFG))
    assert "/*__DATA__*/null" not in html
    assert "</script><script>alert(1)" not in html
    data = json.loads(re.search(r"const DATA = (.*?);\n", html).group(1).replace("<\\/", "</"))
    assert data["markets"][0]["name"].startswith("ตลาด </script>")


def test_pillars_in_payload(features_df):
    payload = build_payload(features_df, CFG)
    assert [p["key"] for p in payload["pillars"]] == ["market", "activity", "workers"]
    assert payload["pillars"][1] == {"key": "activity", "label": "คึกคัก", "tag": "ย่านคึกคัก", "weight": 1,
                                     "measures": {"conv_store_500": 2, "seven_ปั๊ม_500": 1}}
    assert payload["profileTopPct"] == 30
