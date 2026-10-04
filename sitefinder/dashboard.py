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
    ("tourism", "นักท่องเที่ยว"),
    ("access", "การเดินทาง"),
    ("other", "อื่น ๆ"),
]

# feature base name -> (Thai label, group, unit, display multiplier)
FEATURE_INFO = {
    # labels say what is actually counted (QA: "ซูเปอร์มาร์เก็ต 133 แห่ง" were mostly grocery shops)
    "conv_store": ("ร้านสะดวกซื้อ", "shops", "แห่ง", 1),
    "supermarket": ("ซูเปอร์ฯ / ร้านของชำ", "shops", "แห่ง", 1),
    "mall": ("ห้าง / พลาซ่า / ตลาดอื่น (Google นับรวม)", "shops", "แห่ง", 1),
    "seven": ("7-Eleven", "shops", "สาขา", 1),
    "campus": ("มหาวิทยาลัย / วิทยาลัย (นับวิทยาเขต)", "people", "แห่ง", 1),
    "university": ("หมุด 'มหาวิทยาลัย' ของ Google (นับทุกตึก/คณะ)", "people", "หมุด", 1),
    "school": ("โรงเรียน (OSM)", "people", "แห่ง", 1),
    "apartment": ("หอพัก / อพาร์ตเมนต์", "people", "แห่ง", 1),
    "lodging": ("โรงแรม / ที่พักนักท่องเที่ยว", "tourism", "แห่ง", 1),
    "hospital": ("โรงพยาบาล (OSM)", "people", "แห่ง", 1),
    "workplace": ("ออฟฟิศ / หน่วยราชการ", "work", "แห่ง", 1),
    "industrial_ha": ("พื้นที่โรงงาน (OSM)", "work", "ไร่", 6.25),  # hectares → rai
    "industrial": ("โรงงาน", "work", "แห่ง", 1),
    "estate_workers": ("คนงานนิคมฯ", "work", "คน", 1),
    "transit": ("ป้ายรถเมล์ (OSM — ข้อมูลไม่ครบ)", "access", "จุด", 1),
}

# non-radius features: key -> (label, group, unit, display multiplier)
BASE_FEATURES = {
    "reviews": ("จำนวนรีวิว", "popularity", "รีวิว", 1),
    "rating": ("เรตติ้ง (ปรับตามจำนวนรีวิว)", "popularity", "/ 5", 1),
    "dup_count": ("listing ซ้ำที่รวมเข้ามา", "other", "listing", 1),
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
    "hours_text", "hours_known", "reviews_log", "rating_adj", "keep_auto", "dup_count", "dup_names",
    "dup_ids", "open_days", "fetched", "tier",
}

# Chonburi districts: (standard name, aliases found in Google addresses)
AMPHOE = [
    ("เมืองชลบุรี", ["เมืองชลบุรี", "อ.เมือง", "อำเภอเมือง", "mueang chon buri", "mueang chonburi"]),
    ("บ้านบึง", ["บ้านบึง", "ban bueng"]),
    ("หนองใหญ่", ["หนองใหญ่", "nong yai"]),
    ("บางละมุง", ["บางละมุง", "bang lamung", "พัทยา", "pattaya"]),
    ("พานทอง", ["พานทอง", "phan thong"]),
    ("พนัสนิคม", ["พนัสนิคม", "phanat nikhom"]),
    ("ศรีราชา", ["ศรีราชา", "si racha", "sri racha", "sriracha"]),
    ("เกาะสีชัง", ["เกาะสีชัง", "ko si chang", "koh sichang"]),
    ("สัตหีบ", ["สัตหีบ", "sattahip"]),
    ("บ่อทอง", ["บ่อทอง", "bo thong"]),
    ("เกาะจันทร์", ["เกาะจันทร์", "ko chan", "koh chan"]),
]


def market_kind(name, keywords=""):
    """Type of market from its own name (not from the search keyword that found it)."""
    text = str(name or "").lower()
    for pattern, label in KINDS:
        if re.search(pattern, text):
            return label
    return "ตลาดทั่วไป"


def amphoe(address):
    address = str(address or "")
    low = address.lower()
    for standard, aliases in AMPHOE:
        if any(a in low for a in aliases):
            return standard
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


def build_payload(df, cfg, review=None, excluded=None):
    feature_cols = [
        c for c in df.columns
        if c not in NON_FEATURE_COLS and pd.api.types.is_numeric_dtype(df[c]) and df[c].notna().any()
    ]
    features = [{"key": "reviews", **describe_feature("reviews")},
                {"key": "rating", **describe_feature("rating")}]
    if "dup_count" in df and df["dup_count"].notna().any():
        features.append({"key": "dup_count", **describe_feature("dup_count")})
        feature_cols = feature_cols + ["dup_count"]
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
            "days": _clean(row.get("open_days")) or "",
            "dups": _clean(row.get("dup_names")) or "",
            "f": f,
        })

    excluded_rows = []
    if excluded is not None and len(excluded):
        for row in excluded.to_dict("records"):
            excluded_rows.append({
                "id": row["place_id"], "name": _clean(row.get("name")) or "",
                "note": _clean(row.get("note")) or "", "type": _clean(row.get("primary_type")) or "",
                "amphoe": amphoe(row.get("address")), "url": _clean(row.get("maps_url")) or "",
            })
    review_rows = []
    if review is not None and len(review):
        for row in review.fillna("").to_dict("records"):
            review_rows.append({"id": row.get("place_id", ""), "keep": row.get("keep", ""), "note": row.get("note", "")})
    fetched = sorted(d for d in df.get("fetched", pd.Series(dtype=str)).dropna().astype(str) if d)

    sc = cfg["scoring"]
    return {
        "area": cfg.get("area", {}).get("name", ""),
        "generated": date.today().isoformat(),
        "fetched": [fetched[0], fetched[-1]] if fetched else None,
        "scales": dict(sc.get("scales") or {}),
        "excluded": excluded_rows,
        "review": review_rows,
        "groups": [{"key": k, "label": v} for k, v in GROUPS],
        "features": features,
        "pillars": [
            {"key": k, "label": p.get("label", k), "tag": p.get("tag", ""), "weight": p.get("weight", 1),
             "measures": dict(p.get("measures") or {})}
            for k, p in sc["pillars"].items()
        ],
        "profileTopPct": sc.get("profile_top_pct", 30),
        "ratingPrior": sc.get("rating_prior_reviews", 30),
        "topN": cfg.get("output", {}).get("top_n_map", 50),
        "markets": markets,
    }


def render(payload):
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", data)
