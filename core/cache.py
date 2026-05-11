"""File-based JSON cache with TTL expiry.

Ported from JS index.js (readCache/writeCache).
Stores cached data as JSON with metadata (discovered_at, expires_at).
"""

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

DEFAULT_CACHE_DIR = os.path.expanduser("~/.hermes/fbc-registry")
DEFAULT_TTL = 24 * 60 * 60  # 24 hours


class Cache:
    """Simple file-based JSON cache with TTL."""

    def __init__(self, cache_dir: str = DEFAULT_CACHE_DIR):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        """Get the file path for a cache key."""
        safe_key = key.replace("/", "_").replace("\\", "_")
        return self.cache_dir / f"{safe_key}.json"

    def get(self, key: str, ttl: int = DEFAULT_TTL) -> Optional[Any]:
        """Read cached data. Returns None if missing or expired."""
        path = self._path_for(key)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            expires_at = data.get("expires_at", 0)
            if time.time() < expires_at:
                return data.get("data")
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            pass
        return None

    def set(self, key: str, data: Any, ttl: int = DEFAULT_TTL) -> None:
        """Write data to cache with TTL."""
        path = self._path_for(key)
        payload = {
            "discovered_at": time.time(),
            "expires_at": time.time() + ttl,
            "ttl": ttl,
            "data": data,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

    def invalidate(self, key: str) -> None:
        """Remove a cache entry."""
        path = self._path_for(key)
        if path.exists():
            path.unlink()

    def clear(self) -> None:
        """Remove all cache entries."""
        for f in self.cache_dir.glob("*.json"):
            f.unlink()