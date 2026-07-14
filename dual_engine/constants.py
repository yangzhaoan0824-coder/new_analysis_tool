"""Constants for the Dual Engine Analysis package.

Only true, non-configurable constants remain here.
All path / API key configuration is managed by config.py.
"""

# ── Timeout constants (seconds) ──────────────────────────────────────────────
TIMEOUT_NEWS = 45          # mx-search news search timeout
TIMEOUT_DATA = 90          # mx-data data query timeout
TIMEOUT_ANALYSIS = 120     # daily_stock_analysis timeout
TIMEOUT_FINANCIAL = 60     # financial data fetch timeout

# ── Version / metadata ───────────────────────────────────────────────────────
VERSION = "1.1.0"
ENGINE_ID = "dual-analysis-v2"

# ── Precision settings ───────────────────────────────────────────────────────
DECIMAL_PRECISION = 28     # Decimal context precision (matches Python default)
SCORE_DECIMAL_PLACES = 6   # precision_factor decimal places
COMPOSITE_DECIMAL_PLACES = 2  # composite_score decimal places

# ── Market detection rules ───────────────────────────────────────────────────
MARKET_HK = "hk"
MARKET_A = "a"
MARKET_US = "us"

# ── Notion ──────────────────────────────────────────────────────────────────
NOTION_INVEST_PAGE_ID = "33894e07-be3e-80d7-88c9-dcf46cea068c"
