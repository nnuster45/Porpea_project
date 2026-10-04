"""Thin client for Places API (New) and Places Aggregate API with caching, retries and a call budget.

SKU is decided by the field mask, so each method uses the narrowest mask it needs:
- text_search:   Pro fields (id, name, location, types, address)  -> "Text Search Pro"
- place_details: rating / userRatingCount / regularOpeningHours    -> "Place Details Enterprise"
- count_places:  Places Aggregate computeInsights (INSIGHT_COUNT)
"""

import time
from collections import Counter
from datetime import datetime, timezone

import requests

PLACES_BASE = "https://places.googleapis.com/v1"
AGGREGATE_URL = "https://areainsights.googleapis.com/v1:computeInsights"

TEXT_SEARCH_FIELDS = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.location",
        "places.types",
        "places.primaryType",
        "places.businessStatus",
        "places.googleMapsUri",
        "nextPageToken",
    ]
)
DETAILS_FIELDS = "id,rating,userRatingCount,regularOpeningHours,businessStatus"

RETRY_STATUS = {429, 500, 502, 503, 504}


class GoogleAPIError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class BudgetExceeded(RuntimeError):
    pass


class GoogleClient:
    def __init__(self, api_key, cache, max_calls=4000, min_interval_s=0.05, session=None, ledger=None):
        if not api_key:
            raise ValueError("GOOGLE_MAPS_API_KEY is not set (put it in .env)")
        self.api_key = api_key
        self.cache = cache
        self.max_calls = max_calls
        self.min_interval_s = min_interval_s
        self.session = session or requests.Session()
        self.calls = Counter()  # billable (non-cached) calls per SKU
        self.cache_hits = Counter()
        self.ledger = ledger  # monthly per-SKU free-cap guard (budget.Ledger)
        self.last_fetched = None
        self._last_call = 0.0

    def _request(self, sku, method, url, field_mask=None, body=None):
        key = {"method": method, "url": url, "mask": field_mask, "body": body}
        cached = self.cache.get(sku, key)
        if cached is not None:
            self.cache_hits[sku] += 1
            self.last_fetched = self.cache.fetched_at(sku, key)
            return cached

        if sum(self.calls.values()) >= self.max_calls:
            raise BudgetExceeded(
                f"reached --max-calls={self.max_calls}; re-run to continue (finished calls are cached)"
            )
        if self.ledger is not None and not self.ledger.allow(sku):
            raise BudgetExceeded(
                f"monthly cap for {sku} reached ({self.ledger.used(sku)} used); continues next month"
            )

        headers = {"X-Goog-Api-Key": self.api_key, "Content-Type": "application/json"}
        if field_mask:
            headers["X-Goog-FieldMask"] = field_mask

        for attempt in range(5):
            wait = self.min_interval_s - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            resp = self.session.request(method, url, headers=headers, json=body, timeout=30)
            if resp.status_code in RETRY_STATUS and attempt < 4:
                time.sleep(2 ** (attempt + 1))
                continue
            break

        self.calls[sku] += 1
        if self.ledger is not None:
            self.ledger.record(sku)
        if resp.status_code != 200:
            try:
                message = resp.json().get("error", {}).get("message", resp.text)
            except ValueError:
                message = resp.text
            raise GoogleAPIError(resp.status_code, message)

        data = resp.json()
        self.cache.set(sku, key, data)
        self.last_fetched = datetime.now(timezone.utc)
        return data

    def text_search(self, query, rect, page_token=None):
        body = {
            "textQuery": query,
            "languageCode": "th",
            "regionCode": "TH",
            "pageSize": 20,
            "locationRestriction": rect.to_google(),
        }
        if page_token:
            body["pageToken"] = page_token
        return self._request(
            "text_search_pro", "POST", f"{PLACES_BASE}/places:searchText", TEXT_SEARCH_FIELDS, body
        )

    def place_details(self, place_id):
        return self._request(
            "place_details_enterprise",
            "GET",
            f"{PLACES_BASE}/places/{place_id}?languageCode=th",
            DETAILS_FIELDS,
        )

    def count_places(self, lat, lng, radius_m, types):
        body = {
            "insights": ["INSIGHT_COUNT"],
            "filter": {
                "locationFilter": {
                    "circle": {"latLng": {"latitude": lat, "longitude": lng}, "radius": int(radius_m)}
                },
                "typeFilter": {"includedTypes": list(types)},
                "operatingStatus": ["OPERATING_STATUS_OPERATIONAL"],
            },
        }
        data = self._request("places_aggregate", "POST", AGGREGATE_URL, body=body)
        return int(data.get("count", 0))

    def usage_report(self):
        skus = sorted(set(self.calls) | set(self.cache_hits))
        if not skus:
            return "Google API: no calls"
        lines = ["Google API usage this run (billable / from cache):"]
        for sku in skus:
            lines.append(f"  {sku:<26} {self.calls[sku]:>6} / {self.cache_hits[sku]}")
        return "\n".join(lines)
