"""Stage 5b: a single-file interactive dashboard (data/dashboard.html).

All scoring happens in the browser with the same formula as score.py, so weights,
presets, filters and the rating prior can be changed live without re-running anything.
"""

import json
import re
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

TEMPLATE = Path(__file__).parent / "templates" / "dashboard.html"

GROUPS = [
    ("popularity", "ความนิยมของตลาด"),
    ("hours", "เวลาเปิด"),
    ("shops", "ร้านค้ารอบตลาด"),
    ("people", "คนที่อยู่รอบตลาด"),
    ("work", "ที่ทำงาน / โรงงาน"),
    ("access", "การเดินทาง"),
    ("other", "อื่น ๆ"),
]

# feature base name -> (Thai label, group, unit, display multiplier)
FEATURE_INFO = {
    "conv_store": ("ร้านสะดวกซื้อ", "shops", "แห่ง", 1),
    "supermarket": ("ซูเปอร์มาร์เก็ต", "shops", "แห่ง", 1),
    "mall": ("ห้างสรรพสินค้า", "shops", "แห่ง", 1),
    "seven": ("7-Eleven", "shops", "สาขา", 1),
    "university": ("มหาวิทยาลัย / วิทยาลัย", "people", "แห่ง", 1),
    "school": ("โรงเรียน", "people", "แห่ง", 1),
    "apartment": ("หอพัก / อพาร์ตเมนต์", "people", "แห่ง", 1),
    "lodging": ("โรงแรม / ที่พัก", "people", "แห่ง", 1),
    "hospital": ("โรงพยาบาล", "people", "แห่ง", 1),
    "workplace": ("ออฟฟิศ / หน่วยราชการ", "work", "แห่ง", 1),
    "industrial_ha": ("พื้นที่โรงงาน", "work", "ไร่", 6.25),  # hectares → rai
    "industrial": ("โรงงาน", "work", "แห่ง", 1),
    "estate_workers": ("คนงานนิคมฯ", "work", "คน", 1),
    "transit": ("ป้ายรถเมล์ / สถานี", "access", "จุด", 1),
}

# non-radius features: key -> (label, group, unit, display multiplier)
BASE_FEATURES = {
    "reviews": ("จำนวนรีวิว", "popularity", "รีวิว", 1),
    "rating": ("เรตติ้ง (ปรับตามจำนวนรีวิว)", "popularity", "/ 5", 1),
    "days_open": ("จำนวนวันที่เปิด", "hours", "วัน/สัปดาห์", 7),
    "open_morning": ("เปิดช่วงเช้า (06–10 น.)", "hours", "วัน/สัปดาห์", 7),
    "open_evening": ("เปิดช่วงเย็น (16–20 น.)", "hours", "วัน/สัปดาห์", 7),
    "open_night": ("เปิดช่วงดึก (20–02 น.)", "hours", "วัน/สัปดาห์", 7),
}

KINDS = [
    (r"โต้รุ่ง", "ตลาดโต้รุ่ง"),
    (r"walking street|ถนนคนเดิน", "ถนนคนเดิน"),
    (r"night market|ไนท์|ตลาดกลางคืน", "ตลาดกลางคืน"),
    (r"ตลาดนัด", "ตลาดนัด"),
    (r"ตลาดเช้า", "ตลาดเช้า"),
    (r"ตลาดเย็น", "ตลาดเย็น"),
]

NON_FEATURE_COLS = {
    "place_id", "name", "lat", "lng", "address", "primary_type", "types", "business_status",
    "maps_url", "keywords", "keep", "note", "is_new", "rating", "reviews", "business_status_now",
    "hours_text", "hours_known", "reviews_log", "rating_adj",
}


def market_kind(name, keywords=""):
    text = f"{name} {keywords}".lower()
    for pattern, label in KINDS:
        if re.search(pattern, text):
            return label
    return "ตลาดทั่วไป"


def amphoe(address):
    address = str(address or "")
    m = re.search(r"(?:อำเภอ|อ\.)\s*([^\s,]+)", address)
    if m:
        return m.group(1)
    m = re.search(r"([A-Za-z][A-Za-z ]+?) District", address)
    if m:
        return m.group(1).strip()
    if "พัทยา" in address or "Pattaya" in address:
        return "บางละมุง"
    return "ไม่ระบุ"


def describe_feature(col):
    """Column name → {label, group, unit, scale, radius} for the weights panel."""
    if col in BASE_FEATURES:
        label, group, unit, scale = BASE_FEATURES[col]
        return {"label": label, "group": group, "unit": unit, "scale": scale, "radius": None}
    m = re.match(r"^(.*)_(\d+)$", col)
    base, radius = (m.group(1), int(m.group(2))) if m else (col, None)
    known = max((k for k in FEATURE_INFO if base == k or base.startswith(k + "_")), key=len, default=None)
    if known:
        label, group, unit, scale = FEATURE_INFO[known]
        category = base[len(known) + 1:]
        if category:
            label = f"{label} · {category.replace('_', ' ')}"
    else:
        label, group, unit, scale = base.replace("_", " "), "other", "", 1
    return {"label": label, "group": group, "unit": unit, "scale": scale, "radius": radius}


def _clean(value):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if np.isnan(value) else round(float(value), 4)
    return value


def build_payload(df, cfg):
    feature_cols = [
        c for c in df.columns
        if c not in NON_FEATURE_COLS and pd.api.types.is_numeric_dtype(df[c]) and df[c].notna().any()
    ]
    features = [{"key": "reviews", **describe_feature("reviews")},
                {"key": "rating", **describe_feature("rating")}]
    features += [{"key": c, **describe_feature(c)} for c in feature_cols]

    markets = []
    for row in df.to_dict("records"):
        f = {c: _clean(row.get(c)) for c in feature_cols}
        markets.append({
            "id": row["place_id"],
            "name": row.get("name", ""),
            "lat": _clean(row.get("lat")),
            "lng": _clean(row.get("lng")),
            "address": _clean(row.get("address")) or "",
            "amphoe": amphoe(row.get("address")),
            "kind": market_kind(row.get("name", ""), _clean(row.get("keywords")) or ""),
            "url": _clean(row.get("maps_url")) or "",
            "reviews": _clean(row.get("reviews")),
            "rating": _clean(row.get("rating")),
            "hours": _clean(row.get("hours_text")) or "",
            "f": f,
        })

    sc = cfg["scoring"]
    return {
        "area": cfg.get("area", {}).get("name", ""),
        "generated": date.today().isoformat(),
        "groups": [{"key": k, "label": v} for k, v in GROUPS],
        "features": features,
        "personas": sc["personas"],
        "persona": sc.get("persona", next(iter(sc["personas"]))),
        "ratingPrior": sc.get("rating_prior_reviews", 30),
        "topN": cfg.get("output", {}).get("top_n_map", 50),
        "markets": markets,
    }


def render(payload):
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", data)
