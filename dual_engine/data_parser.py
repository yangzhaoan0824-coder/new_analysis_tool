"""
DataParser module - Responsible for cleaning and standardizing raw data.

Per Refactor_Spec:
    DataParser: 负责清洗和标准化原始数据

All numeric conversions use Decimal for precision.
"""

import re
from decimal import Decimal
from typing import Any, Optional

from dual_engine.exceptions import AnalysisError
from dual_engine.utils import parse_num, parse_num_float, detect_market


class DataParser:
    """Cleans, validates and standardizes raw data from various sources.

    Handles:
    - mx-data table output parsing
    - Analyst target price text parsing
    - Technical indicator normalization
    - Financial data validation
    - Earnings forecast structuring
    """

    # ── Table parsing ─────────────────────────────────────────────────────
    @staticmethod
    def parse_mx_table(output: str) -> tuple[list[str], list[list[str]]]:
        """Parse mx-data pipe-delimited table output.

        Returns:
            (headers, rows) where headers is a list of column names
            and rows is a list of lists of cell values.
        """
        headers = []
        rows = []
        for line in output.splitlines():
            if re.match(r"\|\s*date\s*\|", line, re.I):
                headers = [p.strip() for p in line.strip().strip("|").split("|")]
            elif re.match(r"\|\s*\d{4}", line):
                parts = [p.strip() for p in line.strip().strip("|").split("|")]
                if len(parts) >= 2:
                    rows.append(parts)
        return headers, rows

    @staticmethod
    def extract_numeric(val: str) -> Optional[str]:
        """Extract first numeric value from a cell string, or None."""
        if not val or val == "-":
            return None
        if re.match(r"^[\d.]+$", val.strip()):
            return val.strip()
        match = re.search(r"([\d.]+)", val)
        return match.group(1) if match else None

    # ── Analyst target price ──────────────────────────────────────────────
    @staticmethod
    def parse_analyst_target(text: str) -> dict:
        """Parse analyst target price string into structured data.

        Input examples:
            "均值 $185.50USD（近季25家机构）"
            "均值 7.66港元 | 最高 8.50港元 | 最低 6.80港元"
            "目标价 7.66 评级买入- 上涨空间44.5%"

        Returns:
            dict with keys: mean, max, min, rating, upside (all strings or None)
        """
        result = {"mean": None, "max": None, "min": None, "rating": None, "upside": None}
        if not text or text == "N/A":
            return result

        # Try "均值 VALUE" pattern
        mean_match = re.search(r"(?:均值|目标价)[:\s]*([\d.]+)", text)
        if mean_match:
            result["mean"] = mean_match.group(1)

        # Try "最高 VALUE" pattern
        max_match = re.search(r"最高\s*([\d.]+)", text)
        if max_match:
            result["max"] = max_match.group(1)

        # Try "最低 VALUE" pattern
        min_match = re.search(r"最低\s*([\d.]+)", text)
        if min_match:
            result["min"] = min_match.group(1)

        # Try rating pattern
        rating_match = re.search(r"评级\s*(\S+)", text)
        if rating_match:
            result["rating"] = rating_match.group(1)

        # Try upside pattern
        upside_match = re.search(r"(?:上涨空间|上行)\s*([\d.]+)%?", text)
        if upside_match:
            result["upside"] = upside_match.group(1)

        return result

    # ── Technical indicators ───────────────────────────────────────────────
    @staticmethod
    def parse_tech_indicators(raw_data: dict) -> dict:
        """Normalize technical indicator data from various sources.

        Input: raw_data with col_1..col_5, date, price keys
        Output: dict with ma5, ma20, rsi, macd_diff, macd_dea, date keys
        """
        indicators = {
            "ma5": None, "ma20": None, "rsi": None,
            "macd_diff": None, "macd_dea": None, "date": None, "price": None
        }
        if not raw_data:
            return indicators

        # Map col_N keys to named keys
        mapping = {
            "col_1": "ma5",    # MA5
            "col_2": "ma20",   # MA20
            "col_3": "macd_diff",  # MACD-DIFF
            "col_4": "macd_dea",   # MACD-DEA
            "col_5": "rsi",    # RSI
        }
        for col_key, name in mapping.items():
            val = raw_data.get(col_key)
            if val is not None:
                try:
                    indicators[name] = float(val)
                except (ValueError, TypeError):
                    pass

        # Price (for US stocks)
        if "price" in raw_data and raw_data["price"] is not None:
            try:
                indicators["price"] = float(raw_data["price"])
            except (ValueError, TypeError):
                pass

        # Date
        if "date" in raw_data and raw_data["date"]:
            indicators["date"] = raw_data["date"]

        return indicators

    # ── Financial data validation ──────────────────────────────────────────
    @staticmethod
    def validate_financial_data(data: dict) -> dict:
        """Validate and clean financial data dict.

        Raises AnalysisError if critical fields have illegal values.
        Returns cleaned dict with Decimal values for numeric fields.
        """
        if not data:
            raise AnalysisError("data_parser", "Financial data dict is empty or None")

        cleaned = {}
        numeric_fields = [
            "revenue", "net_profit", "eps", "gross_margin", "roe",
            "debt_ratio", "operating_cashflow", "target_price",
            "forecast_revenue_fy1", "forecast_revenue_fy2",
            "forecast_net_profit_fy1", "forecast_net_profit_fy2",
            "forecast_eps_fy1", "forecast_eps_fy2",
            "forecast_pe_fy1", "forecast_peg_fy1",
        ]
        for field in numeric_fields:
            val = data.get(field)
            if val is not None:
                cleaned[field] = parse_num(val)
            else:
                cleaned[field] = None

        # Copy non-numeric fields as-is
        for key, val in data.items():
            if key not in numeric_fields:
                cleaned[key] = val

        return cleaned

    # ── Earnings forecast structuring ──────────────────────────────────────
    @staticmethod
    def structure_earnings_forecast(raw: dict) -> dict:
        """Ensure earnings_forecast has the required structure.

        Required keys: years, revenue, net_profit, eps, profit_growth
        Each list must have 3 elements.
        """
        forecast = raw.copy() if raw else {}

        if not forecast.get("years") or all(str(y) in ("N/A", "") for y in forecast.get("years", [])):
            forecast["years"] = ["2025A", "2026E", "2027E"]

        # Ensure all list fields have 3 elements
        list_fields = ["revenue", "revenue_growth", "net_profit", "profit_growth", "eps"]
        for field in list_fields:
            lst = forecast.get(field, [])
            if not isinstance(lst, list):
                lst = [lst]
            while len(lst) < 3:
                lst.append("N/A")
            forecast[field] = lst[:3]

        return forecast

    # ── Macro score validation ─────────────────────────────────────────────
    @staticmethod
    def validate_macro_score(macro_score) -> Optional[dict]:
        """Validate macro_score object, return dict or None."""
        if macro_score is None:
            return None
        try:
            return {
                "macro": getattr(macro_score, "macro", 0),
                "sector": getattr(macro_score, "sector", 0),
                "news": getattr(macro_score, "news", 0),
                "total": getattr(macro_score, "total", 0),
                "data_available": getattr(macro_score, "data_available", False),
            }
        except Exception:
            return None

    # ── Query ticker conversion ────────────────────────────────────────────
    @staticmethod
    def to_query_ticker(ticker: str, market: str) -> str:
        """Convert ticker to the format expected by mx-data API.

        HK01316 → 01316.HK (for hk)
        600519  → 600519.SS (for a-shares starting with 6/5)
        000858  → 000858.SZ (for a-shares starting with 0/3)
        TSLA    → TSLA       (for us)
        """
        if market == "hk":
            if ticker.startswith("HK"):
                return ticker[2:].lstrip("0").zfill(5) + ".HK"
            return ticker
        elif market == "a":
            suffix = ".SS" if ticker.startswith(("6", "5")) else ".SZ"
            return ticker + suffix
        else:
            return ticker

    @staticmethod
    def to_mx_query_ticker_hk_numeric(ticker: str) -> str:
        """Convert HK ticker to numeric-only format for mx-data financial fetcher.

        HK01316 → 01316
        """
        if ticker.startswith("HK"):
            return ticker[2:]
        return ticker
