"""Config loader — single source of truth for all external configuration.

Usage:
    from dual_engine.config import get_config
    cfg = get_config()
    script = cfg.mx_data_script
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# ── Defaults (can be overridden by .env or environment variables) ──

_DEFAULTS: dict[str, str] = {
    "MX_DATA_SCRIPT": "~/.openclaw/skills/mx-data/mx_data.py",
    "MX_SEARCH_SCRIPT": "~/.openclaw/skills/mx-search/mx_search.py",
    "DAILY_ANALYSIS_DIR": "~/.openclaw/workspace/skills/daily_stock_analysis",
    "TRADING_AGENTS_DIR": "~/.openclaw/workspace/skills/trading-agents",
    # TRADING_AGENTS_SCRIPT is derived from project root, not from env
    "DATA_WAREHOUSE_SCRIPT": "~/.openclaw/workspace/skills/investment-db/scripts/data_warehouse.py",
    "NOTION_SYNC_DIR": "~/.openclaw/workspace/skills/notion-sync",
    "FMP_API_KEY": "",
    "MX_APIKEY": "",
    "NOTION_API_KEY": "",
    "DUAL_ENGINE_CACHE_DIR": "",
    "CACHE_MAX_BYTES": "524288000",
    "CACHE_LRU_THRESHOLD_BYTES": "471859200",
    "CACHE_LRU_PROTECT_SECONDS": "600",
}


def _resolve_path(value: str) -> Path:
    """Expand ~, $HOME, and env vars, then convert to Path."""
    expanded = os.path.expanduser(os.path.expandvars(value))
    return Path(expanded)


@dataclass(frozen=True)
class Config:
    """Immutable configuration object.

    All path fields are resolved (no leading ~ or $VAR) at load time.
    """

    mx_data_script: Path
    mx_search_script: Path
    daily_analysis_dir: Path
    trading_agents_dir: Path
    data_warehouse_script: Path
    notion_sync_dir: Path
    fmp_api_key: str
    mx_apikey: str
    notion_api_key: str
    cache_dir: Path
    cache_max_bytes: int
    cache_lru_threshold_bytes: int
    cache_lru_protect_seconds: int

    @classmethod
    def load(cls, env_path: Optional[Path] = None) -> "Config":
        """Load configuration from .env file and environment variables.

        Priority (high → low):
            1. Environment variables (already set in process)
            2. .env file (via python-dotenv)
            3. _DEFAULTS built-in values

        Args:
            env_path: Optional explicit path to .env file.
                      Defaults to project root .env if it exists.

        Returns:
            Config instance with all fields populated.

        Raises:
            ValueError: If .env file exists but cannot be parsed.
        """
        # Load .env into os.environ (does not override existing env vars)
        if env_path is None:
            candidate = Path(__file__).resolve().parent.parent / ".env"
            env_path = candidate if candidate.exists() else None

        if env_path is not None:
            try:
                load_dotenv(env_path, override=False)
            except Exception as exc:
                raise ValueError(f"Failed to parse .env file ({env_path}): {exc}") from exc

        def _get(key: str) -> str:
            return os.environ.get(key, _DEFAULTS[key])

        def _get_path(key: str) -> Path:
            return _resolve_path(_get(key))

        def _get_int(key: str) -> int:
            try:
                return int(_get(key))
            except ValueError:
                return int(_DEFAULTS[key])

        cache_dir_raw = _get("DUAL_ENGINE_CACHE_DIR")
        cache_dir = _resolve_path(cache_dir_raw) if cache_dir_raw else Path.home() / ".cache" / "dual_engine"

        return cls(
            mx_data_script=_get_path("MX_DATA_SCRIPT"),
            mx_search_script=_get_path("MX_SEARCH_SCRIPT"),
            daily_analysis_dir=_get_path("DAILY_ANALYSIS_DIR"),
            trading_agents_dir=_get_path("TRADING_AGENTS_DIR"),
            data_warehouse_script=_get_path("DATA_WAREHOUSE_SCRIPT"),
            notion_sync_dir=_get_path("NOTION_SYNC_DIR"),
            fmp_api_key=_get("FMP_API_KEY"),
            mx_apikey=_get("MX_APIKEY"),
            notion_api_key=_get("NOTION_API_KEY"),
            cache_dir=cache_dir,
            cache_max_bytes=_get_int("CACHE_MAX_BYTES"),
            cache_lru_threshold_bytes=_get_int("CACHE_LRU_THRESHOLD_BYTES"),
            cache_lru_protect_seconds=_get_int("CACHE_LRU_PROTECT_SECONDS"),
        )


# ── Global singleton ───────────────────────────────────────────────────

_config: Optional[Config] = None


def get_config() -> Config:
    """Return the global Config singleton, loading on first call."""
    global _config
    if _config is None:
        _config = Config.load()
    return _config


def reset_config() -> None:
    """Reset singleton (useful in tests)."""
    global _config
    _config = None
