"""
OSV HTTP client with deterministic on-disk caching keyed by purl.
"""
from __future__ import annotations
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict
import httpx

OSV_QUERY_URL = "https://api.osv.dev/v1/query"
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}
LOGGER = logging.getLogger(__name__)

class OSVCache:
    """Filesystem cache for OSV responses."""
    def __init__(self, cache_dir: Path, refresh: bool):
        """Configure cache location and refresh behavior."""
        self.cache_dir: Path = cache_dir
        self.refresh: bool = refresh
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Track purls that returned empty responses
        self.empty_purls_file: Path = self.cache_dir / "empty_purls.json"

        if self.refresh:
            # Clear all cache when refresh is enabled
            self._clear_cache()
            self.empty_purls: set[str] = set()
        else:
            # Load empty purls for normal operation
            self.empty_purls: set[str] = self._load_empty_purls()

    def _load_empty_purls(self) -> set[str]:
        """Load the set of purls that returned empty responses."""
        if not self.empty_purls_file.exists():
            return set()
        try:
            data = json.loads(self.empty_purls_file.read_text(encoding="utf-8"))
            return set(data) if isinstance(data, list) else set()
        except (json.JSONDecodeError, OSError) as exc:
            LOGGER.warning(f"Failed to load empty purls cache: {exc}")
            return set()

    def _persist_empty_purls(self) -> None:
        """Persist the set of empty purls to disk."""
        try:
            self.empty_purls_file.write_text(
                json.dumps(sorted(self.empty_purls), ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except OSError as exc:
            LOGGER.warning(f"Failed to persist empty purls cache: {exc}")

    def _clear_cache(self) -> None:
        """Clear all cache files and empty purls tracking."""
        # Clear empty purls
        self.empty_purls = set()
        if self.empty_purls_file.exists():
            try:
                self.empty_purls_file.unlink()
            except OSError as exc:
                LOGGER.warning(f"Failed to delete empty purls file: {exc}")

        # Delete all cached response files
        for file in self.cache_dir.glob("*.json"):
            if file != self.empty_purls_file:
                try:
                    file.unlink()
                    LOGGER.debug(f"Deleted cache file: {file}")
                except OSError as exc:
                    LOGGER.warning(f"Failed to delete cache file {file}: {exc}")

    def _path(self, purl: str) -> Path:
        """Return deterministic cache file path for a purl."""
        digest = hashlib.sha256(purl.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def read(self, purl: str) -> Dict[str, Any] | None:
        """Load cached OSV response when available and valid."""
        if self.refresh:
            return None

        # Check if this purl is known to have empty response
        if purl in self.empty_purls:
            return {}

        # Check for cached file
        path = self._path(purl)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            LOGGER.warning(f"Dropping invalid cache entry for {purl}: {exc}", exc_info=True)
            return None

    def write(self, purl: str, obj: Dict[str, Any]) -> Path | None:
        """Persist OSV response to disk and return the file path."""
        # Check if response is empty (no vulnerabilities)
        if not obj or not obj.get("vulns"):
            # Track empty response without creating a file
            self.empty_purls.add(purl)
            self._persist_empty_purls()
            LOGGER.debug(f"Tracked empty response for {purl}")
            return None

        # Write non-empty response to file
        path = self._path(purl)
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

class OSVClient:
    """Small wrapper around the OSV API that respects the cache."""
    def __init__(self, cache: OSVCache, timeout_s: float = 20.0, retries: int = 1, backoff_s: float = 0.5):
        """Initialize client with cache, timeout, retry, and backoff settings."""
        self.cache: OSVCache = cache
        self.timeout_s: float = timeout_s
        self.retries: int = retries
        self.backoff_s: float = backoff_s

    def fetch(self, purl: str) -> Dict[str, Any]:
        """Return OSV record for purl using cache when possible."""
        cached = self.cache.read(purl)
        if cached is not None:
            return cached
        data = self._query_osv(purl)
        # Cache all responses (empty responses tracked separately)
        self.cache.write(purl, data)
        return data

    def _query_osv(self, purl: str) -> Dict[str, Any]:
        payload = {"package": {"purl": purl}}
        for attempt in range(self.retries + 1):
            try:
                response = httpx.post(OSV_QUERY_URL, json=payload, timeout=self.timeout_s)
            except httpx.HTTPError as exc:
                if attempt < self.retries:
                    delay = self.backoff_s * (2 ** attempt)
                    LOGGER.warning(f"Retrying OSV for {purl} after HTTP error: {exc}")
                    time.sleep(delay)
                    continue
                raise RuntimeError(f"OSV query error for purl={purl}: {exc}") from exc
            if response.status_code == 200:
                try:
                    return response.json()
                except (json.JSONDecodeError, ValueError) as exc:
                    raise RuntimeError(f"Invalid JSON from OSV for purl={purl}") from exc
            if response.status_code in RETRY_STATUS and attempt < self.retries:
                delay = self.backoff_s * (2 ** attempt)
                LOGGER.warning(f"Retrying OSV for {purl} (status={response.status_code} attempt={attempt + 1})")
                time.sleep(delay)
                continue
            raise RuntimeError(f"OSV query failed for purl={purl} status={response.status_code}")
        raise RuntimeError(f"OSV query failed for purl={purl} after retries")
