"""Monthly per-SKU usage ledger, so runs stop at the free cap instead of billing.

Google's free usage is per billing account per calendar month (UTC) and per SKU. The ledger lives in
data/cache/usage.json (kept between GitHub Actions runs with the rest of the cache); usage that happened
before the ledger existed can be seeded with `budget.already_used` in config.yaml.
"""

import json
from datetime import datetime, timezone
from pathlib import Path


def this_month():
    return datetime.now(timezone.utc).strftime("%Y-%m")


class Ledger:
    def __init__(self, path, caps=None, already_used=None):
        self.path = Path(path)
        self.caps = dict(caps or {})
        self.seed = {m: dict(v) for m, v in (already_used or {}).items()}
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def used(self, sku, month=None):
        month = month or this_month()
        return self.data.get(month, {}).get(sku, 0) + self.seed.get(month, {}).get(sku, 0)

    def remaining(self, sku, month=None):
        cap = self.caps.get(sku)
        return None if cap is None else max(0, cap - self.used(sku, month))

    def allow(self, sku):
        cap = self.caps.get(sku)
        return cap is None or self.used(sku) < cap

    def record(self, sku, n=1):
        month = this_month()
        self.data.setdefault(month, {})
        self.data[month][sku] = self.data[month].get(sku, 0) + n
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1), encoding="utf-8")

    def report(self):
        month = this_month()
        skus = sorted(set(self.caps) | set(self.data.get(month, {})) | set(self.seed.get(month, {})))
        lines = [f"Google usage {month} (used / monthly cap):"]
        for sku in skus:
            cap = self.caps.get(sku)
            lines.append(f"  {sku:<26} {self.used(sku):>6} / {cap if cap is not None else '-'}")
        return "\n".join(lines)
