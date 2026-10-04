"""Disk cache for API responses so re-running a stage never pays twice."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


class DiskCache:
    def __init__(self, root):
        self.root = Path(root)

    def _path(self, namespace, key_obj):
        raw = json.dumps(key_obj, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()
        return self.root / namespace / digest[:2] / f"{digest}.json"

    def get(self, namespace, key_obj):
        path = self._path(namespace, key_obj)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return None

    def fetched_at(self, namespace, key_obj):
        """When the cached response was fetched (file time), or None."""
        path = self._path(namespace, key_obj)
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) if path.exists() else None

    def set(self, namespace, key_obj, value):
        path = self._path(namespace, key_obj)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
