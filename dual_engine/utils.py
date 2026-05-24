"""
Utility functions for the Dual Engine Analysis package.

Key change from original: All numeric parsing uses decimal.Decimal
to eliminate floating-point precision errors per Refactor_Spec.
"""

import os
import re
from decimal import Decimal, InvalidOperation, getcontext, ROUND_HALF_UP

from dual_engine.constants import DECIMAL_PRECISION, MARKET_HK, MARKET_A, MARKET_US
from dual_engine.exceptions import AnalysisError

# Set Decimal precision
getcontext().prec = DECIMAL_PRECISION

# ── Global error log ─────────────────────────────────────────────────────────
ERROR_LOG: list[str] = []


def log_error(source: str, message: str):
    """Record error to global log list."""
    ERROR_LOG.append(f"{source}: {message}")
    print(f"   ⚠️ [{source}] {message}")


def clear_error_log():
    """Clear the global error log (called at start of each analysis)."""
    ERROR_LOG.clear()


def detect_market(ticker: str) -> str:
    """Detect market from ticker format.

    - HK prefix + digits (e.g. HK08379) → 'hk'
    - .HK suffix (e.g. 08379.HK) → 'hk'
    - Pure 6-digit number (e.g. 002050) → 'a'
    - Otherwise → 'us'
    """
    t = ticker.strip().upper()
    if t.startswith("HK") and t[2:].isdigit():
        return MARKET_HK
    if t.endswith(".HK"):
        return MARKET_HK
    if t.isdigit() and len(t) == 6:
        return MARKET_A
    return MARKET_US


def parse_num(val, default: Decimal = Decimal("0")) -> Decimal:
    """Safe numeric parser using Decimal for precision.

    Handles 'N/A', '15.92%', '310.1亿', '$123.45', '1,234.56' etc.
    Returns Decimal for precise arithmetic; falls back to default on failure.
    """
    if val is None or val == "N/A" or val == "":
        return default
    if isinstance(val, Decimal):
        return val
    if isinstance(val, bool):
        return Decimal("1") if val else Decimal("0")
    if isinstance(val, (int, float)):
        return Decimal(str(val))
    s = str(val).replace("%", "").replace("亿", "").replace("元", "") \
                .replace("$", "").replace(",", "").replace("x", "").strip()
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return default


def parse_num_float(val, default: float = 0) -> float:
    """Backward-compatible float parser (for interface compatibility).

    Internally uses Decimal then converts to float to minimize precision loss.
    """
    result = parse_num(val, Decimal(str(default)))
    return float(result)


def decimal_round(value: Decimal, places: int = 2) -> Decimal:
    """Round a Decimal to specified places using ROUND_HALF_UP."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    quantize_str = "0." + "0" * places if places > 0 else "0"
    return value.quantize(Decimal(quantize_str), rounding=ROUND_HALF_UP)


def decimal_to_str(value: Decimal, places: int = 2) -> str:
    """Format a Decimal to string with specified decimal places."""
    return str(decimal_round(value, places))


def _load_zshrc_env():
    """Parse export KEY=VALUE from ~/.zshrc, inject into process env.

    Required for non-interactive shells (e.g. Feishu/Lark integration).
    """
    zshrc = os.path.expanduser("~/.zshrc")
    if not os.path.exists(zshrc):
        return
    with open(zshrc) as f:
        for line in f:
            line = line.strip()
            m = re.match(r'^export\s+([A-Za-z_][A-Za-z0-9_]*)=["\']?([^"\'#\n]*)["\']?', line)
            if m:
                key, val = m.group(1), m.group(2).strip()
                if key not in os.environ:
                    os.environ[key] = val
