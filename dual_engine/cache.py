"""
File-based cache for mx-data/mx-search API results.

Reduces redundant API calls by caching stdout from mx-data queries.
Cache is stored in /tmp/dual_engine_cache/ with TTL-based expiration.

TTL tiers:
  - 4h (14400s): Analyst consensus data (changes during trading hours)
  - 24h (86400s): Company profile, industry, Beta, revenue (rarely changes)
  - No cache: Real-time price, technical indicators (always fresh)
"""

import hashlib
import json
import os
import time
import subprocess
from typing import Optional

CACHE_DIR = "/tmp/dual_engine_cache"

# TTL constants (seconds)
TTL_CONSENSUS = 4 * 3600       # 4 hours — analyst target/rating
TTL_PROFILE = 24 * 3600        # 24 hours — company profile, industry, Beta
TTL_EARNINGS = 12 * 3600       # 12 hours — earnings forecast
TTL_WEEKLY = 4 * 3600          # 4 hours — weekly trend data
TTL_GS_METRICS = 24 * 3600     # 24 hours — ROE, FCF, debt ratio, Beta


def _ensure_cache_dir():
    """Create cache directory if it doesn't exist."""
    os.makedirs(CACHE_DIR, exist_ok=True)


def _cache_key(ticker: str, query: str) -> str:
    """Generate a unique cache filename from ticker and query."""
    h = hashlib.md5(f"{ticker}||{query}".encode()).hexdigest()[:12]
    # Also include a sanitized query prefix for readability
    safe_prefix = query.replace(" ", "_")[:40].replace("/", "_")
    return f"{ticker}_{safe_prefix}_{h}"


def cache_get(ticker: str, query: str, ttl: int) -> Optional[str]:
    """Read cached stdout for a query if it exists and hasn't expired.

    Args:
        ticker: Stock ticker
        query: The mx-data query string
        ttl: Time-to-live in seconds

    Returns:
        Cached stdout string, or None if cache miss/expired
    """
    _ensure_cache_dir()
    key = _cache_key(ticker, query)
    path = os.path.join(CACHE_DIR, f"{key}.json")
    try:
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            entry = json.load(f)
        cached_at = entry.get("timestamp", 0)
        if time.time() - cached_at > ttl:
            return None  # Expired
        return entry.get("stdout", "")
    except Exception:
        return None


def cache_set(ticker: str, query: str, stdout: str) -> None:
    """Save mx-data stdout to cache.

    Args:
        ticker: Stock ticker
        query: The mx-data query string
        stdout: The stdout from the mx-data subprocess call
    """
    _ensure_cache_dir()
    key = _cache_key(ticker, query)
    path = os.path.join(CACHE_DIR, f"{key}.json")
    try:
        entry = {
            "ticker": ticker,
            "query": query,
            "stdout": stdout,
            "timestamp": time.time(),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False)
    except Exception:
        pass  # Cache write failure is non-critical


def mx_data_cached(ticker: str, query: str, ttl: int,
                   env: dict = None, timeout: int = 90) -> subprocess.CompletedProcess:
    """Run mx-data query with file-based caching.

    If a valid cache entry exists, returns a CompletedProcess with cached stdout.
    Otherwise, runs the subprocess and caches the result.

    Args:
        ticker: Stock ticker (for cache key)
        query: The mx-data query string
        ttl: Cache TTL in seconds (0 = no cache)
        env: Environment variables for subprocess
        timeout: Subprocess timeout in seconds

    Returns:
        subprocess.CompletedProcess with stdout populated
    """
    from dual_engine.constants import MX_DATA_SCRIPT

    # Check cache first
    if ttl > 0:
        cached = cache_get(ticker, query, ttl)
        if cached is not None:
            # Return a fake CompletedProcess with cached data
            result = subprocess.CompletedProcess(
                args=["python3.12", MX_DATA_SCRIPT, query],
                returncode=0,
                stdout=cached,
                stderr=""
            )
            return result

    # Cache miss — run the actual query
    if env is None:
        env = dict(os.environ)
    result = subprocess.run(
        ["python3.12", MX_DATA_SCRIPT, query],
        capture_output=True, text=True, timeout=timeout, env=env
    )

    # Cache successful results
    if ttl > 0 and result.returncode == 0 and result.stdout.strip():
        cache_set(ticker, query, result.stdout)

    return result
