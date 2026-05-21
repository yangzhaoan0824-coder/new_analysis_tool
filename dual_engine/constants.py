"""
Configuration constants for the Dual Engine Analysis package.

Extracted from the original monolithic script for centralized management.
"""

import os
from pathlib import Path

# ── Timeout constants (seconds) ──────────────────────────────────────────────
TIMEOUT_NEWS = 45          # mx-search news search timeout
TIMEOUT_DATA = 90          # mx-data data query timeout
TIMEOUT_ANALYSIS = 120     # daily_stock_analysis timeout
TIMEOUT_FINANCIAL = 60     # financial data fetch timeout

# ── Path constants ───────────────────────────────────────────────────────────
DAILY_ANALYSIS_DIR = os.path.expanduser("~/.openclaw/workspace/skills/daily_stock_analysis")
TRADING_AGENTS_DIR = os.path.expanduser("~/.openclaw/workspace/skills/trading-agents")
TRADING_AGENTS_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "analyze.py")
MX_DATA_SCRIPT = os.path.expanduser("~/.openclaw/skills/mx-data/mx_data.py")
MX_SEARCH_SCRIPT = os.path.expanduser("~/.openclaw/skills/mx-search/mx_search.py")
INVESTMENT_DB_SCRIPT = os.path.expanduser("~/.openclaw/workspace/skills/investment-db/scripts/data_warehouse.py")
NOTION_SYNC_DIR = os.path.expanduser("~/.openclaw/workspace/skills/notion-sync")
NOTION_INVEST_PAGE_ID = "33894e07-be3e-80d7-88c9-dcf46cea068c"
FINANCIAL_FETCHER = Path(__file__).parent.parent / "us_financial_fetcher.py"

# ── Version / metadata ───────────────────────────────────────────────────────
VERSION = "1.0.0"
ENGINE_ID = "dual-analysis-v2"

# ── Precision settings ───────────────────────────────────────────────────────
DECIMAL_PRECISION = 28     # Decimal context precision (matches Python default)
SCORE_DECIMAL_PLACES = 6   # precision_factor decimal places
COMPOSITE_DECIMAL_PLACES = 2  # composite_score decimal places

# ── Market detection rules ───────────────────────────────────────────────────
MARKET_HK = "hk"
MARKET_A = "a"
MARKET_US = "us"
