"""Fetch and cache ponisha.ir's full skill taxonomy from the REST API.

Skills are organized as: Category -> Skills[].  The exact Persian strings
returned by the API are the same strings that appear on project cards, so
the selector's output can be fed directly to `search.skills` in config.yaml
and to `scraper._filter_by_skills`.

Uses requests (already a dependency).  Results are cached to a local JSON
file; on network failure the cache is read even if stale.
"""

import json
import logging
import os
import time
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

CATEGORIES_URL = "https://api.ponisha.ir/api/v1/categories"
CATEGORY_SKILLS_URL = "https://api.ponisha.ir/api/v1/categories/{id}/skills"
CACHE_TTL_SECONDS = 7 * 24 * 3600  # 7 days
REQUEST_TIMEOUT = 20


class SkillFetcher:
    def __init__(self, cache_path: str = None):
        if cache_path is None:
            from ponishabot.paths import SKILLS_CACHE_PATH
            cache_path = SKILLS_CACHE_PATH
        self.cache_path = cache_path

    # ── public ──
    def load_skills(self) -> List[Dict]:
        """Return the full skill catalog: ``[{"id":.., "title":.., "skills": [..]}, ...]``.

        Tries the API first (when cache is stale or missing).  On any network
        error falls back to the cache file — even if expired — so the GUI
        always has something to show when a previous fetch succeeded.
        Returns an empty list only if both API and cache fail.
        """
        # 1. fast path: fresh cache
        cached = self._read_cache()
        if cached is not None:
            return cached

        # 2. network
        fresh = self._fetch_from_api()
        if fresh is not None:
            self._write_cache(fresh)
            return fresh

        # 3. stale cache fallback
        cached = self._read_cache(allow_stale=True)
        if cached is not None:
            logger.warning("Using stale skills cache (API unreachable)")
            return cached

        logger.error("No skills available: API unreachable and no cache file")
        return []

    # ── API ──
    def _fetch_from_api(self) -> Optional[List[Dict]]:
        try:
            resp = requests.get(CATEGORIES_URL, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            cats = resp.json().get("data", [])
        except Exception as e:
            logger.warning(f"Failed to fetch categories: {e}")
            return None

        result: List[Dict] = []
        for cat in cats:
            cid = cat.get("id")
            try:
                r = requests.get(
                    CATEGORY_SKILLS_URL.format(id=cid), timeout=REQUEST_TIMEOUT)
                r.raise_for_status()
                skills = r.json().get("data", [])
            except Exception as e:
                logger.warning(f"Failed to fetch skills for category {cid}: {e}")
                skills = []
            result.append({
                "id": cid,
                "title": cat.get("title", ""),
                "skills": [s.get("title", "") for s in skills if s.get("title")],
            })

        total = sum(len(c["skills"]) for c in result)
        logger.info(f"Fetched {total} skills across {len(result)} categories")
        return result

    # ── cache ──
    def _read_cache(self, allow_stale: bool = False) -> Optional[List[Dict]]:
        if not os.path.exists(self.cache_path):
            return None
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                wrapper = json.load(f)
            saved_at = wrapper.get("saved_at", 0)
            if not allow_stale and (time.time() - saved_at) > CACHE_TTL_SECONDS:
                return None  # stale
            data = wrapper.get("data", [])
            if not isinstance(data, list) or not data:
                return None
            return data
        except Exception:
            return None

    def _write_cache(self, data: List[Dict]) -> None:
        try:
            os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump({"saved_at": time.time(), "data": data}, f,
                          ensure_ascii=False)
            logger.info(f"Skills cache written: {self.cache_path}")
        except Exception as e:
            logger.warning(f"Failed to write skills cache: {e}")
