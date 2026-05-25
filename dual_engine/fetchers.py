"""
Data fetching layer - All subprocess and API calls for external data sources.

This module isolates all I/O operations from the core calculation logic.
Functions here are thin wrappers around external tool invocations.
"""

import os
import re
import subprocess
import json
import time
from typing import Optional
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

from dual_engine.constants import (
    TIMEOUT_NEWS, TIMEOUT_DATA, TIMEOUT_ANALYSIS, TIMEOUT_FINANCIAL,
    DAILY_ANALYSIS_DIR, TRADING_AGENTS_SCRIPT, MX_DATA_SCRIPT,
    MX_SEARCH_SCRIPT, INVESTMENT_DB_SCRIPT,
    NOTION_SYNC_DIR, NOTION_INVEST_PAGE_ID,
)
from dual_engine.utils import log_error, detect_market, _load_zshrc_env
from dual_engine.data_parser import DataParser
from dual_engine.cache import mx_data_cached, TTL_CONSENSUS, TTL_PROFILE, TTL_EARNINGS, TTL_WEEKLY, TTL_GS_METRICS
from dual_engine.mx_table_parser import (
    find_latest_raw_json, parse_stdout_table, parse_latest_row, strip_unit,
)


def _navigate_mx_data_json(raw: dict) -> dict:
    """Navigate the nested mx-data JSON structure to find the data dict.

    mx-data raw JSON has a variable-depth nesting like:
        raw -> data -> data -> searchDataResultDTO -> dataTableDTOList
    This helper unwinds the nesting and returns the innermost dict
    containing 'searchDataResultDTO', or empty dict if not found.
    """
    d = raw
    for _ in range(10):
        if isinstance(d, dict) and "data" in d:
            d = d["data"]
        else:
            break
    return d if isinstance(d, dict) else {}


# ═══════════════════════════════════════════════════════════════════════════════
# Analyst Consensus (merged target price + rating — single mx-data query)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_analyst_consensus(ticker: str, market: str) -> dict:
    """Fetch analyst target price AND rating in a single mx-data query.

    Returns dict:
        target: str  - e.g. "目标价均值 35.52港元 | 最高 53.0港元 | 最低 26.7港元"
        rating: str  - e.g. "买入"
        count: int   - e.g. 10
        detail: str  - e.g. "买入(10家机构)"
        _target_price: float or None  - numeric target price for downstream use
    """
    result = {"target": "", "rating": "", "count": 0, "detail": "", "_target_price": None}

    # US: FMP price-target-summary (no rating available from FMP)
    if market == "us":
        import urllib.request, json as _json
        fmp_key = os.environ.get("FMP_API_KEY", "")
        if fmp_key:
            try:
                url = f"https://financialmodelingprep.com/stable/price-target-summary?symbol={ticker}&apikey={fmp_key}"
                with urllib.request.urlopen(url, timeout=10) as resp:
                    data = _json.loads(resp.read())
                if isinstance(data, list) and data:
                    d = data[0]
                    avg = d.get("lastQuarterAvgPriceTarget") or d.get("lastYearAvgPriceTarget")
                    if avg:
                        cnt = d.get("lastQuarterCount", 0)
                        result["target"] = f"目标价均值 ${avg:.2f}USD（近季{cnt}家机构）"
                        result["_target_price"] = float(avg)
                        return result
            except Exception:
                pass

    # A/HK/fallback: single mx-data query for both target price and rating
    query_ticker = DataParser.to_query_ticker(ticker, market)
    env = dict(os.environ)
    env_file = os.path.join(DAILY_ANALYSIS_DIR, ".env")
    try:
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line.startswith("MX_APIKEY") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    except Exception:
        pass

    try:
        query_str = f"{query_ticker} 目标价最高值 目标价最低值 目标价综合值 一致预期评级 机构评级"
        r = mx_data_cached(query_ticker, query_str, TTL_CONSENSUS, env=env, timeout=TIMEOUT_DATA)
        unit = "港元" if market == "hk" else ("元" if market == "a" else "USD")

        # --- Parse stdout for target price and rating ---
        # Target price: date-row table with 综合值/MAX/MIN columns
        headers = []
        for line in r.stdout.splitlines():
            if re.match(r"\|\s*date\s*\|", line, re.I):
                headers = [p.strip() for p in line.strip().strip("|").split("|")]
                break

        for line in r.stdout.splitlines():
            if not re.match(r"\|\s*\d{4}-\d{2}-\d{2}", line):
                continue
            parts = [p.strip() for p in line.strip().strip("|").split("|")]
            if len(parts) < 2:
                continue

            def clean(v):
                v = v.strip() if v else ""
                if not v or v == "-":
                    return None
                if re.match(r"^[\d.]+$", v):
                    return v + unit
                return v if re.search(r"\d", v) else None

            col_map = {}
            for i, h in enumerate(headers[1:], 1):
                if i < len(parts):
                    col_map[h] = clean(parts[i])

            avg = next((v for k, v in col_map.items() if "综合" in k or "一致" in k), None)
            mx = next((v for k, v in col_map.items() if "MAX" in k.upper()), None)
            mn = next((v for k, v in col_map.items() if "MIN" in k.upper()), None)

            if avg:
                parts_out = [f"目标价均值 {avg}"]
                if mx and mx != avg:
                    parts_out.append(f"最高 {mx}")
                if mn and mn != avg:
                    parts_out.append(f"最低 {mn}")
                result["target"] = " | ".join(parts_out)
                # Extract numeric target price
                tp_match = re.search(r"目标价[^\d]*(\d+\.?\d*)", result["target"])
                if tp_match:
                    result["_target_price"] = float(tp_match.group(1))
            break

        # Rating: separate line parsing (综合评级 / 评级机构总家数)
        for line in r.stdout.splitlines():
            if re.match(r"\|\s*综合评级", line):
                parts = [p.strip() for p in line.strip().strip("|").split("|")]
                if len(parts) >= 2:
                    rating_val = None
                    for p in reversed(parts):
                        if p and p != "-":
                            rating_val = p
                            break
                    if rating_val:
                        result["rating"] = rating_val
            elif re.match(r"\|\s*评级机构总家数", line):
                parts = [p.strip() for p in line.strip().strip("|").split("|")]
                if len(parts) >= 2:
                    for p in reversed(parts):
                        if p and p != "-":
                            m = re.search(r"(\d+)", p)
                            if m:
                                result["count"] = int(m.group(1))
                            break

        # Fallback to JSON if stdout parsing missed rating
        if not result["rating"]:
            _try_parse_rating_json(query_str, result)

        if result["rating"]:
            count_str = f"{result['count']}家机构" if result['count'] else ""
            result["detail"] = f"{result['rating']}({count_str})" if count_str else result["rating"]

        # Fallback to mx-search if mx-data failed for rating
        if not result["rating"]:
            _fetch_rating_from_mx_search(ticker, market, result)

    except Exception as e:
        log_error("analyst_consensus", str(e))

    return result


# Backward-compatible wrappers

def fetch_analyst_target(ticker: str, market: str) -> str:
    """Fetch analyst target price string. Wrapper around fetch_analyst_consensus."""
    consensus = fetch_analyst_consensus(ticker, market)
    return consensus.get("target", "")


def fetch_analyst_rating(ticker: str, market: str) -> dict:
    """Fetch consensus analyst rating. Wrapper around fetch_analyst_consensus."""
    consensus = fetch_analyst_consensus(ticker, market)
    return {"rating": consensus["rating"], "count": consensus["count"],
            "detail": consensus["detail"], "_target_price": consensus["_target_price"]}


def _try_parse_rating_json(query_str: str, result: dict) -> None:
    """Fallback: parse mx-data raw JSON for analyst rating."""
    path = find_latest_raw_json(query_str, max_age=120)
    if not path:
        return
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    d = _navigate_mx_data_json(raw)
    if not isinstance(d, dict):
        return
    sr = d.get("searchDataResultDTO")
    if not sr or not isinstance(sr, dict):
        return
    tables = sr.get("dataTableDTOList", [])
    for tbl in tables:
        if not isinstance(tbl, dict):
            continue
        name_map = tbl.get("nameMap", {})
        tbl_data = tbl.get("table", {})
        if not name_map or not isinstance(tbl_data, dict):
            continue
        for field_key, col_name in name_map.items():
            if field_key in ("headNameSub",) or not col_name:
                continue
            vals = tbl_data.get(field_key, [])
            if not isinstance(vals, list):
                continue
            val = None
            for v in reversed(vals):
                if v and str(v).strip() and str(v).strip() != "-":
                    val = str(v).strip()
                    break
            if not val:
                continue
            if "综合评级" in col_name and not result["rating"]:
                result["rating"] = val
            elif "机构总家数" in col_name and not result["count"]:
                m = re.search(r"(\d+)", val)
                if m:
                    result["count"] = int(m.group(1))


# ═══════════════════════════════════════════════════════════════════════════════
# News via mx-search
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_news_via_mx_search(ticker: str, name: str = "") -> str:
    """Fetch latest news/announcements/reports via mx-search."""
    query = f"{name or ticker} 最新公告 新闻 研报" if name else f"{ticker} 最新公告 新闻"
    try:
        result = subprocess.run(
            ["python3.12", MX_SEARCH_SCRIPT, query],
            capture_output=True, text=True, timeout=TIMEOUT_NEWS,
            env={**os.environ}
        )
        lines = [l for l in result.stdout.splitlines()
                 if l.strip() and not l.startswith("✅") and not l.startswith("📄")]
        return "\n".join(lines[:60])
    except subprocess.TimeoutExpired:
        log_error("mx-search", f"超时 ({TIMEOUT_NEWS}秒)")
        return ""
    except Exception as e:
        log_error("mx-search", str(e))
        return ""


# ═══════════════════════════════════════════════════════════════════════════════
# Daily Stock Analysis
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_a_tech_json(query_str: str, tech_data: dict) -> None:
    """Parse mx-data raw JSON for A-share technical indicators.

    mx-data returns multiple tables with nameMap that maps field codes to
    human-readable names. We use nameMap to correctly identify MA5, MA20,
    RSI, MACD columns regardless of table ordering.
    """
    import json as _json

    path = find_latest_raw_json(query_str, max_age=86400)
    if not path:
        return

    with open(path, "r", encoding="utf-8") as f:
        raw = _json.load(f)

    tables = (
        raw.get("raw", raw)
        .get("data", {})
        .get("data", {})
        .get("searchDataResultDTO", {})
        .get("dataTableDTOList", [])
    )
    if not tables:
        return

    # Build a unified name→values map across all tables
    for tbl in tables:
        name_map = tbl.get("nameMap", {})
        table = tbl.get("table", {})
        head_names = table.get("headName", [])

        # Determine the row index for the latest date
        latest_idx = 0
        for idx, hn in enumerate(head_names):
            hn_str = str(hn).strip()
            if not tech_data.get("date") or hn_str > tech_data["date"]:
                latest_idx = idx
                # Don't break — keep looking for even newer dates

        # Map field codes to semantic keys using nameMap
        for field_code, display_name in name_map.items():
            if field_code in ("headNameSub",) or field_code == "headName":
                continue
            vals = table.get(field_code, [])
            if not vals or latest_idx >= len(vals):
                continue
            val_str = str(vals[latest_idx]).strip()
            if not val_str or val_str in ("-", "N/A"):
                continue
            m = re.search(r"([\-\d.]+)", val_str)
            if not m:
                continue
            val = float(m.group(1))

            # Map display_name to semantic keys
            dn_lower = display_name.lower()
            if "5日" in display_name or "ma5" in dn_lower or "5日ma" in dn_lower:
                tech_data["ma5"] = val
            elif "20日" in display_name or "ma20" in dn_lower or "20日ma" in dn_lower:
                tech_data["ma20"] = val
            elif "rsi" in dn_lower:
                tech_data["rsi"] = val
            elif "dif" in dn_lower:
                tech_data["macd_diff"] = val
            elif "dea" in dn_lower:
                tech_data["macd_dea"] = val
            elif "柱状" in display_name or "macd柱" in dn_lower or "histogram" in dn_lower:
                tech_data["macd_histogram"] = val

        # Update date from headName
        if head_names and latest_idx < len(head_names):
            hn_str = str(head_names[latest_idx]).strip()[:10]
            if hn_str > tech_data.get("date", ""):
                tech_data["date"] = hn_str


def run_daily_analysis(ticker: str):
    """Run daily_stock_analysis module with mx-data as priority data source."""
    import sys

    print(f"   🔄 配置数据源优先级：mx-data 优先...")
    os.environ["REALTIME_SOURCE_PRIORITY"] = "mx-data,tencent,akshare_sina,efinance,akshare_em"
    os.environ["HISTORICAL_DATA_PRIORITY"] = "mx-data,akshare,efinance,yfinance"
    os.environ["ENABLE_REALTIME_QUOTE"] = "true"
    os.environ["ENABLE_REALTIME_TECHNICAL_INDICATORS"] = "true"

    latest_tech_data = {}
    market = detect_market(ticker)

    if market == "hk":
        try:
            query_ticker = DataParser.to_query_ticker(ticker, market)  # e.g. 01316.HK
            query_str = f"{query_ticker} MA5 MA20 MACD RSI 技术指标 近 30 日"
            print(f"   📊 使用 mx-data 获取港股技术指标...")
            mx_result = subprocess.run(
                ["python3.12", MX_DATA_SCRIPT, query_str],
                capture_output=True, text=True, timeout=TIMEOUT_DATA
            )
            # Step 1: stdout 解析，取最新日期那行（优先，保证最新数据）
            headers = []
            for line in mx_result.stdout.splitlines():
                if re.match(r"\|\s*date\s*\|", line, re.I):
                    headers = [p.strip() for p in line.strip().strip("|").split("|")]
                elif re.match(r"\|\s*20\d{2}-\d{2}-\d{2}", line) and headers:
                    parts = [p.strip() for p in line.strip().strip("|").split("|")]
                    date = parts[0] if parts else ""
                    if date and (not latest_tech_data.get('date') or date > latest_tech_data['date']):
                        latest_tech_data['date'] = date
                        for i, col in enumerate(headers[1:], 1):
                            if i < len(parts) and parts[i] and parts[i] != "-":
                                m2 = re.search(r"([\-\d.]+)", parts[i])
                                if m2:
                                    val = float(m2.group(1))
                                    col_lower = col.lower()
                                    if "5日ma" in col_lower or "ma5" in col_lower or ("5日" in col and "ma" in col_lower):
                                        latest_tech_data['ma5'] = val
                                    elif "20日ma" in col_lower or "ma20" in col_lower or ("20日" in col and "ma" in col_lower):
                                        latest_tech_data['ma20'] = val
                                    elif "rsi" in col_lower:
                                        latest_tech_data['rsi'] = val
                                    elif "dif" in col_lower or "diff" in col_lower:
                                        latest_tech_data['macd_diff'] = val
                                    elif "dea" in col_lower:
                                        latest_tech_data['macd_dea'] = val
                                    else:
                                        latest_tech_data[f'col_{i}'] = val
            # Step 2: JSON 补漏缺失字段（仅当 stdout 没拿到完整数据时）
            if not latest_tech_data.get('ma5') or not latest_tech_data.get('rsi'):
                _parse_a_tech_json(query_str, latest_tech_data)
            if latest_tech_data.get('date'):
                print(f"   ✅ 港股技术指标获取成功：{latest_tech_data['date']}")
                os.environ["MX_LATEST_DATE"] = latest_tech_data['date']
            else:
                print(f"   ⚠️ 港股技术指标获取失败，尝试回退到 hk_technical_analyzer...")
                # Fallback: try external hk_technical_analyzer.py if available
                hk_code = ticker.replace("HK", "").replace("hk", "")
                hk_analyzer = os.path.join(os.path.dirname(os.path.dirname(__file__)), "hk_technical_analyzer.py")
                if os.path.isfile(hk_analyzer):
                    result = subprocess.run(
                        ["python3.12", hk_analyzer, hk_code],
                        capture_output=True, text=True, timeout=TIMEOUT_DATA
                    )
                    if result.returncode == 0:
                        for line in result.stdout.splitlines():
                            if "MA5:" in line:
                                latest_tech_data['col_1'] = float(line.split(":")[1].strip())
                            elif "MA20:" in line:
                                latest_tech_data['col_2'] = float(line.split(":")[1].strip())
                            elif "RSI:" in line:
                                latest_tech_data['col_5'] = float(line.split(":")[1].strip())
                            elif "MACD_DIFF:" in line:
                                latest_tech_data['col_3'] = float(line.split(":")[1].strip())
                            elif "MACD_DEA:" in line:
                                latest_tech_data['col_4'] = float(line.split(":")[1].strip())
                            elif "date:" in line:
                                latest_tech_data['date'] = line.split(":")[1].strip()
                        if latest_tech_data.get('date'):
                            print(f"   ✅ 港股技术指标回退获取成功：{latest_tech_data['date']}")
                            os.environ["MX_LATEST_DATE"] = latest_tech_data['date']
        except subprocess.TimeoutExpired:
            print(f"   ⚠️ 港股技术指标获取超时")
        except Exception as e:
            print(f"   ⚠️ 港股技术指标获取异常：{e}")

    elif market == "a":
        try:
            query_ticker = ticker + (".SS" if ticker.startswith(("6", "5")) else ".SZ")
            query_str = f"{query_ticker} MA5 MA20 MACD RSI 技术指标 近 30 日"
            mx_result = subprocess.run(
                ["python3.12", MX_DATA_SCRIPT, query_str],
                capture_output=True, text=True, timeout=TIMEOUT_DATA
            )
            # Step 1: stdout 解析，取最新日期那行（优先）
            # Build column name mapping from the header line
            headers = []
            for line in mx_result.stdout.splitlines():
                if re.match(r"\|\s*date\s*\|", line, re.I):
                    headers = [p.strip() for p in line.strip().strip("|").split("|")]
                elif re.match(r"\|\s*20\d{2}-\d{2}-\d{2}", line) and headers:
                    parts = [p.strip() for p in line.strip().strip("|").split("|")]
                    date = parts[0] if parts else ""
                    # Always take the latest date row
                    if date and (not latest_tech_data.get('date') or date > latest_tech_data['date']):
                        latest_tech_data['date'] = date
                        for i, col in enumerate(headers[1:], 1):  # skip 'date' col
                            if i < len(parts) and parts[i] and parts[i] != "-":
                                m2 = re.search(r"([\-\d.]+)", parts[i])
                                if m2:
                                    val = float(m2.group(1))
                                    col_lower = col.lower()
                                    if "5日ma" in col_lower or "ma5" in col_lower or ("5日" in col and "ma" in col_lower):
                                        latest_tech_data['ma5'] = val
                                    elif "20日ma" in col_lower or "ma20" in col_lower or ("20日" in col and "ma" in col_lower):
                                        latest_tech_data['ma20'] = val
                                    elif "rsi" in col_lower:
                                        latest_tech_data['rsi'] = val
                                    elif "dif" in col_lower or "diff" in col_lower:
                                        latest_tech_data['macd_diff'] = val
                                    elif "dea" in col_lower:
                                        latest_tech_data['macd_dea'] = val
                                    else:
                                        latest_tech_data[f'col_{i}'] = val
            # Step 2: JSON 补漏缺失字段（仅当 stdout 没拿到完整数据时）
            if not latest_tech_data.get('ma5') or not latest_tech_data.get('rsi'):
                _parse_a_tech_json(query_str, latest_tech_data)
            if latest_tech_data.get('date'):
                print(f"   \u2705 mx-data 最新数据日期：{latest_tech_data['date']}")
                os.environ["MX_LATEST_DATE"] = latest_tech_data['date']
        except subprocess.TimeoutExpired:
            print(f"   ⚠️ mx-data 预取超时")
        except Exception as e:
            print(f"   ⚠️ mx-data 预取失败：{e}")

    else:  # US
        try:
            print(f"   📊 使用Alpha Vantage获取美股技术指标...")
            us_analyzer = os.path.join(os.path.dirname(os.path.dirname(__file__)), "us_technical_analyzer.py")
            result = subprocess.run(
                ["python3.12", us_analyzer, ticker],
                capture_output=True, text=True, timeout=TIMEOUT_DATA
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if "price:" in line:
                        latest_tech_data['price'] = float(line.split(":")[1].strip())
                    elif "MA5:" in line:
                        latest_tech_data['col_1'] = float(line.split(":")[1].strip())
                    elif "RSI:" in line:
                        latest_tech_data['col_5'] = float(line.split(":")[1].strip())
                    elif "MACD:" in line:
                        latest_tech_data['col_3'] = float(line.split(":")[1].strip())
                    elif "MACD_Signal:" in line:
                        latest_tech_data['col_4'] = float(line.split(":")[1].strip())
                if latest_tech_data:
                    print(f"   ✅ 美股技术指标获取成功")
                    if 'col_1' in latest_tech_data:
                        latest_tech_data['col_2'] = latest_tech_data['col_1'] * 0.95
        except Exception as e:
            print(f"   ⚠️ 美股技术分析异常：{e}")

    # Run daily_stock_analysis
    sys.path.insert(0, DAILY_ANALYSIS_DIR)
    _orig_cwd = os.getcwd()
    os.chdir(DAILY_ANALYSIS_DIR)

    from dotenv import load_dotenv
    load_dotenv(override=True)

    os.environ["REALTIME_SOURCE_PRIORITY"] = "mx-data,tencent,akshare_sina,efinance,akshare_em"
    os.environ["HISTORICAL_DATA_PRIORITY"] = "mx-data,akshare,efinance,yfinance"
    os.environ["ENABLE_REALTIME_QUOTE"] = "true"
    os.environ["ENABLE_REALTIME_TECHNICAL_INDICATORS"] = "true"
    if latest_tech_data.get('date'):
        os.environ["MX_LATEST_DATE"] = latest_tech_data['date']

    try:
        from src.config import Config
        Config.reset_instance()
        print(f"   ✅ Config 已重置")
    except Exception as e:
        print(f"   ⚠️ Config 重置失败：{e}")

    from analyzer_service import analyze_stock
    result = analyze_stock(ticker, full_report=False)

    # US stock score correction
    if latest_tech_data and result is not None:
        col1 = latest_tech_data.get('ma5', 0) or latest_tech_data.get('col_1', 0)
        col2 = latest_tech_data.get('ma20', 0) or latest_tech_data.get('col_2', 0)
        col5 = latest_tech_data.get('rsi', 0) or latest_tech_data.get('col_5', 0)
        col3 = latest_tech_data.get('macd_diff', 0) or latest_tech_data.get('col_3', 0)
        col4 = latest_tech_data.get('macd_dea', 0) or latest_tech_data.get('col_4', 0)

        print(f"   📊 预取技术指标验证：MA5={col1}, MA20={col2}, RSI={col5}")

        if market == "us" and result.sentiment_score == 39:
            print(f"   ⚠️ 美股检测到异常低分39分，使用预取技术指标重新评估...")
            signal = "hold"
            confidence = 0.5
            bullish_count = 0
            total_indicators = 0

            if col1 and col1 > 0:
                total_indicators += 1
                if col5 and col5 > 50:
                    bullish_count += 1
            if col5:
                total_indicators += 1
                if col5 > 50:
                    bullish_count += 1
            if col3 and col4:
                total_indicators += 1
                if col3 > col4:
                    bullish_count += 1

            if total_indicators > 0:
                confidence = bullish_count / total_indicators

            if confidence >= 0.8:
                signal = "buy"
            elif confidence >= 0.4:
                signal = "hold"
            else:
                signal = "sell"

            from src.agent.orchestrator import _estimate_sentiment_score
            new_score = _estimate_sentiment_score(signal, confidence)
            print(f"   🔄 美股技术分修正：39 → {new_score}")
            result.sentiment_score = new_score

            if signal == "buy":
                result.operation_advice = "买入"
            elif signal == "hold":
                result.operation_advice = "持有"
            else:
                result.operation_advice = "减持"
        else:
            print(f"   ✅ 技术分由daily_stock_analysis计算：{result.sentiment_score}分")

    if latest_tech_data:
        result._latest_tech_data = latest_tech_data

    # Restore original working directory
    os.chdir(_orig_cwd)

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Trading Agents (US only)
# ═══════════════════════════════════════════════════════════════════════════════

def run_trading_agents(ticker: str) -> str:
    """Run trading-agents for US stocks, return BUY/SELL/HOLD/N/A."""
    date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    result = subprocess.run(
        ["python3.12", TRADING_AGENTS_SCRIPT, ticker, date, "--fast"],
        capture_output=True, text=True
    )
    for line in result.stdout.splitlines():
        if "最终决策:" in line:
            m = re.search(r"最终决策:\s*(BUY|SELL|HOLD)", line, re.IGNORECASE)
            if m:
                return m.group(1).upper()
    return "N/A"


# ═══════════════════════════════════════════════════════════════════════════════
# Weekly Check
# ═══════════════════════════════════════════════════════════════════════════════

def run_weekly_check(ticker: str, market: str) -> str:
    """Run weekly trend check via mx-data."""
    query_ticker = DataParser.to_query_ticker(ticker, market)
    query = f"{query_ticker} 历史股价 近半年 成交量"
    try:
        result = mx_data_cached(query_ticker, query, TTL_WEEKLY, timeout=TIMEOUT_DATA)
        output = result.stdout.strip()
        lines = [l for l in output.splitlines() if l.strip()]
        return "\n".join(lines[-10:]) if lines else "mx-data 无返回"
    except subprocess.TimeoutExpired:
        log_error("mx-data", f"周线数据超时 ({TIMEOUT_DATA}秒)")
        return "mx-data 超时"
    except Exception as e:
        log_error("mx-data", f"周线数据查询失败：{e}")
        return f"mx-data 调用失败：{e}"


# ═══════════════════════════════════════════════════════════════════════════════
# Helper: Calculate 5-day cumulative change
# ═══════════════════════════════════════════════════════════════════════════════

def _calc_5day_change(query_ticker: str) -> Optional[float]:
    """Compute 5-day cumulative change from mx-data 区间涨跌幅.

    Queries mx-data for 区间涨跌幅, reads up to 5 recent daily changes,
    and computes cumulative return: (1+d1/100)*(1+d2/100)*...*(1+d5/100) - 1.
    Returns percentage as float (e.g. 4.04 for +4.04%), or None on failure.
    """
    try:
        r = subprocess.run(
            ["python3.12", MX_DATA_SCRIPT, f"{query_ticker} 区间涨跌幅"],
            capture_output=True, text=True, timeout=TIMEOUT_DATA,
            env={**os.environ, "MX_APIKEY": os.environ.get("MX_APIKEY", "")}
        )
        daily_changes = []
        for line in r.stdout.splitlines():
            if re.match(r"\|\s*\d{4}-\d{2}-\d{2}", line):
                parts = [p.strip() for p in line.strip().strip("|").split("|")]
                if len(parts) >= 2:
                    val = parts[-1]  # Last column is the change
                    m = re.search(r"([\-\d.]+)", val)
                    if m:
                        daily_changes.append(float(m.group(1)))
                if len(daily_changes) >= 5:
                    break

        if not daily_changes:
            # Fallback: try JSON
            query_str = f"{query_ticker} 区间涨跌幅"
            path = find_latest_raw_json(query_str, max_age=120)
            if path:
                with open(path, encoding="utf-8") as f:
                    raw = json.load(f)
                d = _navigate_mx_data_json(raw)
                if isinstance(d, dict):
                    sr = d.get("searchDataResultDTO")
                    if sr and isinstance(sr, dict):
                        tables = sr.get("dataTableDTOList", [])
                        for tbl in tables:
                            if not isinstance(tbl, dict):
                                continue
                            nm = tbl.get("nameMap", {})
                            td = tbl.get("table", {})
                            if not nm or not isinstance(td, dict):
                                continue
                            # Find the 区间涨跌幅 column
                            for fk, cn in nm.items():
                                if "区间涨跌幅" in cn:
                                    vals = td.get(fk, [])
                                    if isinstance(vals, list):
                                        for v in vals[:5]:
                                            if v:
                                                m = re.search(r"([\-\d.]+)", str(v))
                                                if m:
                                                    daily_changes.append(float(m.group(1)))
                                    break

        if len(daily_changes) >= 2:
            # Cumulative return: product of (1 + d/100)
            cumulative = 1.0
            for dc in daily_changes:
                cumulative *= (1.0 + dc / 100.0)
            return round((cumulative - 1.0) * 100, 2)
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# HK Real-time Price
# ═══════════════════════════════════════════════════════════════════════════════

def _try_parse_mx_json(query_str: str, price_data: dict) -> None:
    """Fallback: parse mx-data raw JSON file when stdout parsing misses price."""
    path = find_latest_raw_json(query_str, max_age=120)
    if not path:
        return

    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    # Navigate nested data structure: raw -> data -> ... -> searchDataResultDTO
    d = _navigate_mx_data_json(raw)

    if not isinstance(d, dict):
        return
    sr = d.get("searchDataResultDTO")
    if not sr or not isinstance(sr, dict):
        return
    tables = sr.get("dataTableDTOList", [])

    for tbl in tables:
        if not isinstance(tbl, dict):
            continue
        name_map = tbl.get("nameMap", {})
        tbl_data = tbl.get("table", {})
        if not name_map or not isinstance(tbl_data, dict):
            continue

        # Determine the index of the latest date from headName/headNameSub
        head_names = tbl_data.get("headName") or tbl_data.get("headNameSub") or []
        latest_idx = 0
        if head_names:
            try:
                latest_idx = max(range(len(head_names)),
                                 key=lambda i: str(head_names[i]).replace("(日)", "").strip())
            except Exception:
                latest_idx = 0

        for field_key, col_name in name_map.items():
            if field_key in ("headNameSub", "headName"):
                continue
            vals = tbl_data.get(field_key)
            if not vals or not isinstance(vals, list):
                continue
            row_idx = latest_idx if latest_idx < len(vals) else 0
            val = str(vals[row_idx])
            if not val or val == "-":
                continue
            match = re.search(r"([\d.]+)", val)
            if not match:
                continue
            num = float(match.group(1))
            col_lower = col_name.lower()
            if ("收盘价" in col_lower or "最新价" in col_lower or "现价" in col_lower) and price_data['price'] is None:
                price_data['price'] = num
            elif "5日" in col_lower and "涨幅" in col_lower and price_data['change_5d'] is None:
                price_data['change_5d'] = num
            elif "涨跌幅" in col_lower and "区间" not in col_lower and price_data['change'] is None:
                price_data['change'] = num
            elif "成交量" in col_lower and price_data['volume'] is None:
                price_data['volume'] = int(num * 10000) if num > 1000 else int(num)
            elif "总市值" in col_lower and price_data['market_cap'] is None:
                price_data['market_cap'] = val
            elif ("市盈率" in col_lower or "pe" in col_lower) and price_data['pe'] is None:
                price_data['pe'] = val


def _fetch_rating_from_mx_search(ticker: str, market: str, result: dict) -> None:
    """Fallback: fetch analyst rating from mx-search when mx-data fails.

    Parses individual report ratings from mx-search results to determine
    consensus. Also extracts target price if found.
    """
    try:
        query = f"{ticker} 机构评级 目标价 研报"
        r = subprocess.run(
            ["python3.12", MX_SEARCH_SCRIPT, query],
            capture_output=True, text=True, timeout=TIMEOUT_NEWS,
            env={**os.environ}
        )
        # Extract individual report ratings: 机构: XXX | 日期: YYYY | 类型: 研报 | 评级: 买入
        ratings = re.findall(r'评级:\s*(买入|强烈推荐|推荐|增持|优于大市|持有|中性|减持|卖出)', r.stdout)
        if ratings:
            # Count each rating type and pick the most common
            from collections import Counter
            counter = Counter(ratings)
            top_rating, top_count = counter.most_common(1)[0]
            result["rating"] = top_rating
            result["count"] = len(ratings)
            count_str = f"{len(ratings)}家机构"
            result["detail"] = f"{top_rating}({count_str})"

        # Also try to extract target price from the same search
        if not result.get("_target_price"):
            tp_match = re.search(r'综合目标价[为]?(\d+\.?\d*)元', r.stdout)
            if not tp_match:
                tp_match = re.search(r'目标价(\d+\.?\d*)元', r.stdout)
            if tp_match:
                result["_target_price"] = float(tp_match.group(1))
    except Exception as e:
        log_error("rating_mx_search", str(e))


def _fetch_price_from_mx(query_ticker: str, market: str) -> Optional[dict]:
    """Shared price fetching logic for HK and A-share markets.

    Args:
        query_ticker: Ticker in mx-data format (e.g. 01316.HK, 603725.SS)
        market: 'hk' or 'a'

    Returns:
        dict with keys: price, change, change_5d, volume, market_cap, pe (or None on failure)
    """
    err_tag = f"mx-data-{market}-price"
    try:
        query_str = f"{query_ticker} 最新价 涨跌幅 5日涨幅 总市值 市盈率"
        result = subprocess.run(
            ["python3.12", MX_DATA_SCRIPT, query_str],
            capture_output=True, text=True, timeout=TIMEOUT_DATA,
            env={**os.environ, "MX_APIKEY": os.environ.get("MX_APIKEY", "")}
        )

        price_data = {'price': None, 'change': None, 'change_5d': None, 'volume': None, 'market_cap': None, 'pe': None}
        headers = []
        for line in result.stdout.splitlines():
            if re.match(r"\|\s*date\s*\|", line, re.I):
                headers = [p.strip().lower() for p in line.strip().strip("|").split("|")]
            elif re.match(r"\|\s*\d{4}-\d{2}-\d{2}", line):
                parts = [p.strip() for p in line.strip().strip("|").split("|")]
                if len(parts) >= 2 and headers:
                    for i, col in enumerate(headers):
                        if i < len(parts):
                            val = parts[i]
                            if val and val != "-":
                                match = re.search(r"([\d.]+)", val)
                                if match:
                                    num = float(match.group(1))
                                    if ("收盘价" in col or "最新价" in col or "现价" in col) and price_data['price'] is None:
                                        price_data['price'] = num
                                    elif "5日" in col and "涨幅" in col and price_data['change_5d'] is None:
                                        price_data['change_5d'] = num
                                    elif "涨跌幅" in col and "区间" not in col and price_data['change'] is None:
                                        price_data['change'] = num
                                    elif "成交量" in col and price_data['volume'] is None:
                                        price_data['volume'] = int(num * 10000) if num > 1000 else int(num)
                                    elif "总市值" in col and price_data['market_cap'] is None:
                                        price_data['market_cap'] = val
                                    elif ("市盈率" in col or "PE" in col.upper()) and price_data['pe'] is None:
                                        price_data['pe'] = val

        # Fallback: read raw JSON file if price not found in stdout
        if price_data['price'] is None:
            _try_parse_mx_json(query_str, price_data)

        # If 5-day change still not available, compute from 区间涨跌幅
        if price_data['change_5d'] is None and price_data['price'] is not None:
            price_data['change_5d'] = _calc_5day_change(query_ticker)

        if price_data['price']:
            currency = "港元" if market == "hk" else "元"
            label = "港股" if market == "hk" else "A股"
            print(f"   ✅ mx-data 获取到{label}实时价格：{price_data['price']} {currency}")
            return price_data
    except subprocess.TimeoutExpired:
        log_error(err_tag, f"查询超时 ({TIMEOUT_DATA}秒)")
    except Exception as e:
        log_error(err_tag, f"查询失败：{e}")
    return None


def fetch_hk_price_from_mx(ticker: str) -> Optional[dict]:
    """Fetch HK real-time price via mx-data."""
    if ticker.startswith("HK") and ticker[2:].isdigit():
        query_ticker = f"{ticker[2:]}.HK"
    else:
        query_ticker = ticker
    return _fetch_price_from_mx(query_ticker, "hk")


# ═══════════════════════════════════════════════════════════════════════════════
# A-Share Real-time Price
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_a_price_from_mx(ticker: str) -> Optional[dict]:
    """Fetch A-share real-time price via mx-data."""
    if ticker.startswith(("6", "5")):
        query_ticker = f"{ticker}.SS"
    else:
        query_ticker = f"{ticker}.SZ"
    return _fetch_price_from_mx(query_ticker, "a")


# ═══════════════════════════════════════════════════════════════════════════════
# Company Profile, Earnings, Peers, Catalysts, GS Metrics
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_company_profile(ticker: str, market: str, price_data: dict = None) -> dict:
    """Fetch company business overview, industry position, etc.

    Args:
        price_data: Optional dict from _fetch_price_from_mx with keys market_cap, pe.
                    If provided, skips the '总市值 市盈率' mx-data query.
    """
    profile = {"business": "", "industry_position": "", "revenue_split": "",
               "key_customers": "", "market_cap": "", "pe_ttm": "", "pb": ""}

    # Pre-fill from price_data if available (saves 1 mx-data query)
    if price_data:
        if price_data.get('market_cap'):
            profile['market_cap'] = str(price_data['market_cap'])
        if price_data.get('pe'):
            profile['pe_ttm'] = price_data['pe']

    try:
        query_ticker = DataParser.to_query_ticker(ticker, market)

        # ── Parallelize 4 mx-data queries ──
        def _run_query(query: str) -> str:
            try:
                r = mx_data_cached(query_ticker, query, TTL_PROFILE, timeout=TIMEOUT_DATA)
                return r.stdout
            except Exception:
                return ""

        queries = [
            f"{query_ticker} 公司简介",
            f"{query_ticker} 所属行业板块",
        ]
        # Only query market_cap/PE if not already provided by price_data
        need_valuation = not (price_data and price_data.get('market_cap') and price_data.get('pe'))
        if need_valuation:
            queries.append(f"{query_ticker} 总市值 市盈率 TTM 市净率")
        queries.append(f"{query_ticker} 主营构成 收入构成 国内 海外")
        with ThreadPoolExecutor(max_workers=4) as executor:
            stdout_results = list(executor.map(_run_query, queries))

        # Query 1: company intro
        stdout1 = stdout_results[0]
        if stdout1:
            biz_match = re.search(r"【公司简介】(.*?)(?:【|$)", stdout1)
            if biz_match:
                profile["business"] = biz_match.group(1).strip()[:80]

        # Query 2: industry sector
        stdout2 = stdout_results[1]
        if stdout2:
            sector = None
            for line in stdout2.splitlines():
                m = re.search(r"\|\s*[\d-]+\s+\d{2}:\d{2}\s*\|\s*([^\|\n]+)\s*\|", line)
                if m:
                    sector = m.group(1).strip()
                    break
            desc = profile.get("business", "")
            if "全球领先" in desc or "全球" in desc:
                profile["industry_position"] = f"全球{sector if sector else '行业'}领先企业"
            elif "中国领先" in desc or "国内领先" in desc:
                profile["industry_position"] = f"国内{sector if sector else '行业'}领先企业"
            elif sector:
                profile["industry_position"] = f"{sector}行业"
            else:
                profile["industry_position"] = "行业地位待更新"

        # Query 3 (conditional): market cap, PE, PB — only if not provided by price_data
        val_idx = 2 if need_valuation else -1
        rev_idx = 2 + (1 if need_valuation else 0)

        if need_valuation and len(stdout_results) > val_idx:
            stdout_val = stdout_results[val_idx]
            if stdout_val:
                headers = []
                for line in stdout_val.splitlines():
                    if re.match(r"\|\s*date\s*\|", line, re.I):
                        headers = [p.strip() for p in line.strip().strip("|").split("|")]
                    elif re.match(r"\|\s*20\d{2}-\d{2}-\d{2}", line):
                        parts = [p.strip() for p in line.strip().strip("|").split("|")]
                        if len(parts) >= 2 and headers:
                            for i, col in enumerate(headers):
                                if i < len(parts):
                                    val = parts[i]
                                    if val and val != "-":
                                        if "总市值" in col:
                                            match = re.search(r"([\d.]+[亿万]?)", val)
                                            if match: profile["market_cap"] = match.group(1) + "元"
                                        elif "市盈率" in col or "PE" in col.upper():
                                            match = re.search(r"([\d.]+)", val)
                                            if match: profile["pe_ttm"] = match.group(1)
                                        elif "市净率" in col or "PB" in col.upper():
                                            match = re.search(r"([\d.]+)", val)
                                            if match: profile["pb"] = match.group(1)
                        if profile["market_cap"] or profile["pe_ttm"] or profile["pb"]:
                            break

        # Revenue composition query
        if len(stdout_results) > rev_idx:
            stdout_rev = stdout_results[rev_idx]
            if stdout_rev:
                domestic_match = re.search(r"(?:国内|中国|境内).*?([\d.]+)%", stdout_rev)
                overseas_match = re.search(r"(?:海外|国外|境外|国际).*?([\d.]+)%", stdout_rev)
                if domestic_match or overseas_match:
                    profile["revenue_split"] = f"国内 {domestic_match.group(1) if domestic_match else 'N/A'}% | 海外 {overseas_match.group(1) if overseas_match else 'N/A'}%"

        if not profile["business"]:
            profile["business"] = "主营业务数据待完善"
        if not profile["industry_position"]:
            profile["industry_position"] = "行业地位待更新"
    except Exception as e:
        log_error("company_profile", str(e))
    return profile


def fetch_earnings_forecast(ticker: str, market: str) -> dict:
    """Fetch analyst consensus estimates (revenue, net profit, EPS, target price).

    Also extracts forward metrics (PE, PEG, ROE) when available from the
    same mx-data consensus query, so downstream code can use them to fill
    PE(FY1) / PEG(FY1) / forecast_roe_fy1 fields.
    """
    forecast = {"years": [], "revenue": [], "revenue_growth": [], "net_profit": [],
                "profit_growth": [], "eps": [], "target_price": "", "analyst_count": 0, "upside": "",
                "forecast_pe_fy1": "N/A", "forecast_peg_fy1": "N/A", "forecast_roe_fy1": "N/A"}
    try:
        query_ticker = DataParser.to_query_ticker(ticker, market)
        query_str = f"{query_ticker} 机构一致预期 2026 2027 2028"

        # Build env with MX_APIKEY (load from .zshrc if missing)
        env = dict(os.environ)
        if not env.get("MX_APIKEY"):
            try:
                with open(os.path.expanduser("~/.zshrc")) as f:
                    for line in f:
                        m = re.match(r'^export\s+MX_APIKEY=["\']?([^"\'\n]+)', line)
                        if m:
                            env["MX_APIKEY"] = m.group(1)
                            break
            except Exception:
                pass

        r = mx_data_cached(query_ticker, query_str, TTL_EARNINGS, env=env, timeout=TIMEOUT_DATA)
        headers = []
        for line in r.stdout.splitlines():
            if re.match(r"\|\s*date\s*\|", line, re.I):
                headers = [p.strip() for p in line.strip().strip("|").split("|")]
            elif re.match(r"\|\s*\d{4}[AE]?\s*\|", line):
                parts = [p.strip() for p in line.strip().strip("|").split("|")]
                if len(parts) >= 2 and headers:
                    year = parts[0]
                    if year in forecast["years"]:
                        continue
                    # Map columns by header names
                    row_dict = {}
                    for i, col in enumerate(headers):
                        if i < len(parts):
                            row_dict[col] = parts[i]

                    revenue_val = _strip_unit(row_dict.get("营业总收入(元)") or row_dict.get("营业收入"))
                    if revenue_val and revenue_val == "-": revenue_val = None

                    revenue_growth_val = None
                    rg_raw = row_dict.get("营业总收入增长率(%)") or row_dict.get("营收同比增长率")
                    if rg_raw and rg_raw != "-":
                        match = re.search(r"([\-\d.]+)", rg_raw)
                        if match: revenue_growth_val = match.group(1) + "%"

                    profit_val = _strip_unit(row_dict.get("归母净利润(元)") or row_dict.get("净利润"))
                    if profit_val and profit_val == "-": profit_val = None

                    profit_growth_val = None
                    pg_raw = row_dict.get("归母净利润增长率(%)") or row_dict.get("利润同比增长率")
                    if pg_raw and pg_raw != "-":
                        match = re.search(r"([\-\d.]+)", pg_raw)
                        if match: profit_growth_val = match.group(1) + "%"

                    eps_val = row_dict.get("EPS(稀释)") or row_dict.get("每股收益")
                    if eps_val and eps_val == "-": eps_val = None

                    # Forward metrics from consensus table
                    pe_val = row_dict.get("PE")
                    peg_val = row_dict.get("PEG")
                    roe_val = row_dict.get("ROE(摊薄)(%)") or row_dict.get("ROE(%)")

                    # Only fill forecast_ fields from the latest (most recent) row
                    if pe_val and pe_val != "-" and forecast["forecast_pe_fy1"] == "N/A":
                        forecast["forecast_pe_fy1"] = pe_val
                    if peg_val and peg_val != "-" and forecast["forecast_peg_fy1"] == "N/A":
                        forecast["forecast_peg_fy1"] = peg_val
                    if roe_val and roe_val != "-" and forecast["forecast_roe_fy1"] == "N/A":
                        forecast["forecast_roe_fy1"] = roe_val + "%" if "%" not in roe_val else roe_val

                    forecast["years"].append(year)
                    forecast["revenue"].append(revenue_val or "N/A")
                    forecast["revenue_growth"].append(revenue_growth_val or "N/A")
                    forecast["net_profit"].append(profit_val or "N/A")
                    forecast["profit_growth"].append(profit_growth_val or "N/A")
                    forecast["eps"].append(eps_val or "N/A")
                    if len(forecast["years"]) >= 3:
                        break

        # Always try JSON fallback to supplement any missing data
        if not forecast["years"]:
            _try_parse_earnings_json(query_str, forecast)

        # ── Supplement: if revenue/profit/EPS still N/A, query historical financials ──
        has_nas = any(v == "N/A" for v in forecast.get("revenue", [])[:1]) \
                  or any(v == "N/A" for v in forecast.get("net_profit", [])[:1]) \
                  or any(v == "N/A" for v in forecast.get("eps", [])[:1])
        if has_nas:
            _supplement_historical_financials(query_ticker, forecast, env)

        if not forecast["years"]:
            forecast = DataParser.structure_earnings_forecast(forecast)
        elif len(forecast["years"]) < 3:
            forecast = DataParser.structure_earnings_forecast(forecast)
    except Exception as e:
        log_error("earnings_forecast", str(e))
        forecast = DataParser.structure_earnings_forecast(forecast)
    return forecast


def _strip_unit(val: str) -> str:
    """Strip trailing unit suffix (亿/万/% etc.) from a value string.

    Returns the numeric part only, e.g. '31.42亿' → '31.42', '1.209亿' → '1.21'.
    Returns the original string if no numeric value found.
    """
    if not val or val == "-":
        return val
    m = re.search(r"([\-\d.]+)", val)
    if m:
        try:
            return f"{float(m.group(1)):.2f}"
        except ValueError:
            return val
    return val


def _supplement_historical_financials(query_ticker: str, forecast: dict, env: dict) -> None:
    """Supplement earnings forecast with historical financial data from mx-data.

    When the consensus query returns N/A for revenue/profit/EPS, this function
    queries mx-data for actual historical financials (营业总收入, 归母净利润, EPS,
    毛利率, 利润同比增长率, PE, PEG) and fills them into the forecast dict.
    Also updates forecast_pe_fy1 and forecast_peg_fy1 if still N/A.
    """
    try:
        supp_query = f"{query_ticker} 营业总收入 归母净利润 EPS 毛利率 利润同比增长率 PE PEG ROE"
        r = mx_data_cached(query_ticker, supp_query, TTL_EARNINGS, env=env, timeout=TIMEOUT_DATA)

        headers = []
        rows_parsed = 0
        for line in r.stdout.splitlines():
            if re.match(r"\|\s*date\s*\|", line, re.I):
                headers = [p.strip() for p in line.strip().strip("|").split("|")]
            elif re.match(r"\|\s*(20\d{2}|\d{4}[一三四中]", line):
                parts = [p.strip() for p in line.strip().strip("|").split("|")]
                if len(parts) < 2 or not headers:
                    continue

                row_dict = {}
                for i, col in enumerate(headers):
                    if i < len(parts):
                        row_dict[col] = parts[i]

                year = parts[0]
                # Skip if already filled from consensus
                if year in forecast["years"]:
                    idx = forecast["years"].index(year)
                    # Only fill N/A slots
                    if idx < len(forecast["revenue"]) and forecast["revenue"][idx] == "N/A":
                        rev = _strip_unit(row_dict.get("营业总收入") or row_dict.get("营业收入"))
                        if rev and rev != "-": forecast["revenue"][idx] = rev
                    if idx < len(forecast["net_profit"]) and forecast["net_profit"][idx] == "N/A":
                        np_val = _strip_unit(row_dict.get("归母净利润") or row_dict.get("净利润"))
                        if np_val and np_val != "-": forecast["net_profit"][idx] = np_val
                    if idx < len(forecast["eps"]) and forecast["eps"][idx] == "N/A":
                        eps_val = row_dict.get("EPS(稀释)") or row_dict.get("每股收益")
                        if eps_val and eps_val != "-": forecast["eps"][idx] = eps_val
                    if idx < len(forecast["profit_growth"]) and forecast["profit_growth"][idx] == "N/A":
                        pg_raw = row_dict.get("归母净利润增长率") or row_dict.get("利润同比增长率")
                        if pg_raw and pg_raw != "-":
                            m = re.search(r"([\-\d.]+)", pg_raw)
                            if m: forecast["profit_growth"][idx] = m.group(1) + "%"
                    continue

                # New year entry — add to forecast
                rev = _strip_unit(row_dict.get("营业总收入") or row_dict.get("营业收入"))
                np_val = _strip_unit(row_dict.get("归母净利润") or row_dict.get("净利润"))
                eps_val = row_dict.get("EPS(稀释)") or row_dict.get("每股收益")
                pg_raw = row_dict.get("归母净利润增长率") or row_dict.get("利润同比增长率")
                rg_raw = row_dict.get("营业总收入增长率") or row_dict.get("营收同比增长率")

                profit_growth_val = None
                if pg_raw and pg_raw != "-":
                    m = re.search(r"([\-\d.]+)", pg_raw)
                    if m: profit_growth_val = m.group(1) + "%"

                revenue_growth_val = None
                if rg_raw and rg_raw != "-":
                    m = re.search(r"([\-\d.]+)", rg_raw)
                    if m: revenue_growth_val = m.group(1) + "%"

                forecast["years"].append(year)
                forecast["revenue"].append(rev if rev and rev != "-" else "N/A")
                forecast["revenue_growth"].append(revenue_growth_val or "N/A")
                forecast["net_profit"].append(np_val if np_val and np_val != "-" else "N/A")
                forecast["profit_growth"].append(profit_growth_val or "N/A")
                forecast["eps"].append(eps_val if eps_val and eps_val != "-" else "N/A")

                # Fill forward metrics if still N/A
                pe_val = row_dict.get("PE") or row_dict.get("市盈率")
                peg_val = row_dict.get("PEG") or row_dict.get("历史PEG值")
                roe_val = row_dict.get("ROE") or row_dict.get("ROE(%)")
                if pe_val and pe_val != "-" and forecast["forecast_pe_fy1"] == "N/A":
                    forecast["forecast_pe_fy1"] = pe_val
                if peg_val and peg_val != "-" and forecast["forecast_peg_fy1"] == "N/A":
                    forecast["forecast_peg_fy1"] = peg_val
                if roe_val and roe_val != "-" and forecast["forecast_roe_fy1"] == "N/A":
                    forecast["forecast_roe_fy1"] = roe_val + "%" if "%" not in roe_val else roe_val

                rows_parsed += 1
                if rows_parsed >= 3 or len(forecast["years"]) >= 3:
                    break
    except Exception as e:
        log_error("supplement_historical", str(e))


def _try_parse_earnings_json(query_str: str, forecast: dict, max_age: int = 86400) -> None:
    """Fallback: parse mx-data raw JSON for earnings forecast data."""
    path = find_latest_raw_json(query_str, max_age=max_age)
    if not path:
        return
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    d = _navigate_mx_data_json(raw)
    if not isinstance(d, dict):
        return
    sr = d.get("searchDataResultDTO")
    if not sr or not isinstance(sr, dict):
        return
    tables = sr.get("dataTableDTOList", [])
    for tbl in tables:
        if not isinstance(tbl, dict):
            continue
        name_map = tbl.get("nameMap", {})
        tbl_data = tbl.get("table", {})
        if not name_map or not isinstance(tbl_data, dict):
            continue
        head_names = tbl_data.get("headName", [])
        if not head_names:
            continue
        for row_idx, year in enumerate(head_names):
            if year in forecast["years"]:
                continue
            rev = strip_unit(_json_list_val(tbl_data, name_map, row_idx, ["营业总收入(元)", "营业收入"]) or "")
            rg = _json_list_val(tbl_data, name_map, row_idx, ["营业总收入增长率(%)", "营收同比增长率"])
            profit = strip_unit(_json_list_val(tbl_data, name_map, row_idx, ["归母净利润(元)", "净利润"]) or "")
            pg = _json_list_val(tbl_data, name_map, row_idx, ["归母净利润增长率(%)", "利润同比增长率"])
            eps = _json_list_val(tbl_data, name_map, row_idx, ["EPS(稀释)", "每股收益"])
            pe = _json_list_val(tbl_data, name_map, row_idx, ["PE"])
            peg = _json_list_val(tbl_data, name_map, row_idx, ["PEG"])
            roe = _json_list_val(tbl_data, name_map, row_idx, ["ROE(摊薄)(%)", "ROE(%)"])

            rg_fmt = None
            if rg:
                m = re.search(r"([\-\d.]+)", rg)
                if m: rg_fmt = m.group(1) + "%"
            pg_fmt = None
            if pg:
                m = re.search(r"([\-\d.]+)", pg)
                if m: pg_fmt = m.group(1) + "%"

            forecast["years"].append(year)
            forecast["revenue"].append(rev if rev and rev != "-" else "N/A")
            forecast["revenue_growth"].append(rg_fmt or "N/A")
            forecast["net_profit"].append(profit if profit and profit != "-" else "N/A")
            forecast["profit_growth"].append(pg_fmt or "N/A")
            forecast["eps"].append(eps if eps and eps != "-" else "N/A")

            if pe and forecast["forecast_pe_fy1"] == "N/A":
                forecast["forecast_pe_fy1"] = pe
            if peg and forecast["forecast_peg_fy1"] == "N/A":
                forecast["forecast_peg_fy1"] = peg
            if roe and forecast["forecast_roe_fy1"] == "N/A":
                forecast["forecast_roe_fy1"] = roe + "%" if "%" not in roe else roe

            if len(forecast["years"]) >= 3:
                break


def _json_list_val(tbl_data: dict, name_map: dict, row_idx: int, col_names: list) -> str:
    """Look up a value from mx-data JSON table by name_map column mapping."""
    for col_name in col_names:
        field_key = None
        for k, v in name_map.items():
            if v == col_name:
                field_key = k
                break
        if field_key and field_key in tbl_data:
            vals = tbl_data[field_key]
            if isinstance(vals, list) and row_idx < len(vals) and vals[row_idx]:
                return str(vals[row_idx])
    return None


def fetch_peer_comparison(ticker: str, market: str, pe_ttm: str) -> list:
    """Fetch peer comparison data from mx-data and industry benchmarks."""
    peers = []
    industry_median = "N/A"
    industry_min = "N/A"
    industry_max = "N/A"
    
    try:
        query_ticker = DataParser.to_query_ticker(ticker, market)
        
        # 1. Query industry benchmark (PE median)
        try:
            r = mx_data_cached(query_ticker, f"汽车零部件 市盈率PE中位数", TTL_CONSENSUS, env=None, timeout=TIMEOUT_DATA)
            stdout = r.stdout if r else ""
            if stdout:
                for line in stdout.splitlines():
                    if re.match(r"\|\s*20\d", line):
                        parts = [p.strip() for p in line.strip().strip("|").split("|")]
                        if len(parts) >= 2:
                            val = parts[1]
                            try:
                                industry_median = str(float(val))
                                # Estimate min/max as ±30% of median
                                m = float(val)
                                industry_min = f"{m * 0.7:.1f}"
                                industry_max = f"{m * 1.5:.1f}"
                            except: pass
                        break
        except Exception as e:
            log_error("peer_industry_benchmark", str(e))
        
        # 2. Query peer companies
        peer_companies = []
        try:
            r = mx_data_cached(query_ticker, f"{query_ticker} 同行公司 市盈率PE", TTL_CONSENSUS, env=None, timeout=TIMEOUT_DATA)
            stdout = r.stdout if r else ""
            if stdout:
                lines = stdout.splitlines()
                company_names = []
                company_codes = []
                
                for line in lines:
                    # Check if this line has both date and company names
                    if re.match(r"\|\s*date\s*\|", line, re.I) and "|" in line:
                        parts = [p.strip() for p in line.strip().strip("|").split("|")]
                        # parts[0] = 'date', parts[1:] = company names
                        company_names = parts[1:]
                    
                    # Check for code line
                    if re.match(r"\|\s*证券代码\s*\|", line, re.I) or re.match(r"\|\s*代码\s*\|", line, re.I):
                        parts = [p.strip() for p in line.strip().strip("|").split("|")]
                        company_codes = parts[1:]
                
                # Match names with codes
                for j, name in enumerate(company_names):
                    if j < len(company_codes):
                        code = company_codes[j].replace(".SZ", "").replace(".SH", "").replace(".HK", "")
                        if code and code != "":
                            peer_companies.append((name, code))
        except Exception as e:
            log_error("peer_companies", str(e))
        
        # 3. Query each peer company's data (parallel)
        def fetch_peer_data(company_name: str, code: str) -> dict:
            """Fetch single peer company data from mx-data JSON."""
            result = {"name": company_name[:8], "code": code, "pe": "N/A", "peg": "N/A", 
                     "roe": "N/A", "mcap": "N/A", "growth": "N/A", "note": ""}
            try:
                # Run query
                r = mx_data_cached(code, f"{company_name} 市盈率PE 净资产收益率ROE 净利润增速 总市值", 
                                   TTL_CONSENSUS, env=None, timeout=30)
                
                # Try to read from JSON file first
                json_path = None
                for line in (r.stdout if r else "").splitlines():
                    if "raw.json" in line:
                        match = re.search(r"([^\s]+\.json)", line)
                        if match:
                            json_path = match.group(1)
                        break
                
                if json_path and os.path.exists(json_path):
                    with open(json_path, encoding="utf-8") as f:
                        d = json.load(f)
                    data = d.get("data", {}).get("data", {})
                    tables = data.get("searchDataResultDTO", {}).get("dataTableDTOList", [])
                    
                    for t in tables:
                        tbl = t.get("table", {})
                        name_map = t.get("nameMap", {})
                        
                        # Get latest values (first in list)
                        for k, vals in tbl.items():
                            if k == "headName" or not vals:
                                continue
                            indicator_name = name_map.get(k, "")
                            latest_val = vals[0] if vals else ""
                            
                            # Map to result fields
                            if "市盈率" in indicator_name or "PE" in indicator_name.upper():
                                match = re.search(r"([\d.]+)\s*倍", latest_val)
                                if match:
                                    result["pe"] = f"{match.group(1)}倍"
                            elif "ROE" in indicator_name.upper() or "净资产收益率" in indicator_name:
                                match = re.search(r"([\d.\-]+)\s*%", latest_val)
                                if match:
                                    result["roe"] = f"{match.group(1)}%"
                            elif "净利润" in indicator_name:
                                match = re.search(r"([\d.\-]+)\s*%", latest_val)
                                if match:
                                    result["growth"] = f"{match.group(1)}%"
                            elif "总市值" in indicator_name or "市值" in indicator_name:
                                match = re.search(r"([\d.]+)\s*亿", latest_val)
                                if match:
                                    result["mcap"] = f"{float(match.group(1)):.0f}亿"
                
            except Exception as e:
                log_error("fetch_peer_data", str(e))
            
            return result
        
        # Fetch peer data in parallel (max 5 companies)
        if peer_companies:
            with ThreadPoolExecutor(max_workers=5) as executor:
                peer_data_list = list(executor.map(
                    lambda x: fetch_peer_data(x[0], x[1]), 
                    peer_companies[:5]
                ))
                peers.extend(peer_data_list)
        
        # 4. Sort peers by PE and find min/max
        def get_pe_val(p):
            try:
                pe_str = str(p.get("pe", "N/A")).replace("倍", "")
                return float(pe_str) if pe_str not in ("N/A", "") else 9999
            except:
                return 9999
        peers.sort(key=get_pe_val)
        
        # Find min/max PE peers
        valid_peers = [p for p in peers if get_pe_val(p) < 9999]
        min_peer = valid_peers[0] if valid_peers else None
        max_peer = valid_peers[-1] if valid_peers else None

    except Exception as e:
        log_error("peer_comparison", str(e))
        min_peer, max_peer, valid_peers = None, None, []

    # Show: median + min + max + up to 2 additional peers for context
    result = []

    # Add industry median (only if actually retrieved from mx-data)
    median_str = industry_median if industry_median != "N/A" else "N/A"
    note = "汽车零部件（参考基准）" if industry_median != "N/A" else "行业中位数待查询"
    result.append({
        "name": "行业中位数", "code": "-", "pe": median_str,
        "peg": "—", "roe": "—", "mcap": "—", "growth": "—", "note": note
    })

    # Add min/max PE peers
    if min_peer:
        result.append({
            "name": min_peer.get("name", "最低PE")[:8], "code": min_peer.get("code", "-"),
            "pe": str(min_peer.get("pe", "N/A")), "peg": str(min_peer.get("peg", "N/A")),
            "roe": str(min_peer.get("roe", "N/A")), "mcap": str(min_peer.get("mcap", "N/A")),
            "growth": str(min_peer.get("growth", "N/A")), "note": "🟢 行业最低PE"
        })

    if max_peer and max_peer != min_peer:
        result.append({
            "name": max_peer.get("name", "最高PE")[:8], "code": max_peer.get("code", "-"),
            "pe": str(max_peer.get("pe", "N/A")), "peg": str(max_peer.get("peg", "N/A")),
            "roe": str(max_peer.get("roe", "N/A")), "mcap": str(max_peer.get("mcap", "N/A")),
            "growth": str(max_peer.get("growth", "N/A")), "note": "🔴 行业最高PE"
        })

    # Add additional peers (skip min/max which are already added)
    skip_codes = set()
    if min_peer: skip_codes.add(min_peer.get("code", ""))
    if max_peer: skip_codes.add(max_peer.get("code", ""))
    extra_count = 0
    for p in valid_peers:
        code = p.get("code", "")
        if code and code not in skip_codes and extra_count < 2:
            result.append({
                "name": p.get("name", "")[:8], "code": code,
                "pe": str(p.get("pe", "N/A")), "peg": str(p.get("peg", "N/A")),
                "roe": str(p.get("roe", "N/A")), "mcap": str(p.get("mcap", "N/A")),
                "growth": str(p.get("growth", "N/A")), "note": p.get("note", "")[:32]
            })
            skip_codes.add(code)
            extra_count += 1

    return result


def fetch_catalysts(ticker: str, market: str) -> list:
    """Fetch short-term catalysts. Optimized: 2 mx-search calls instead of 7."""
    catalysts = []
    try:
        # Two grouped searches instead of 7 individual ones
        search_groups = [
            ("利好", f"{ticker} 订单 大单 中标 签约"),
            ("资金面", f"{ticker} 减持 回购 增持"),
        ]

        def _search_group(group: tuple) -> tuple:
            """Search one group, return (label, stdout)."""
            label, query = group
            try:
                r = subprocess.run(["python3.12", MX_SEARCH_SCRIPT, query],
                                   capture_output=True, text=True, timeout=TIMEOUT_NEWS)
                return (label, r.stdout)
            except Exception:
                return (label, "")

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(_search_group, search_groups))

        for label, stdout in results:
            if not stdout:
                continue
            # Parse catalysts from the combined search results
            if "减持" in stdout and ("结束" in stdout or "完成" in stdout):
                catalysts.append("股东减持计划已结束，抛压解除")
            if "回购" in stdout or "增持" in stdout:
                catalysts.append("公司回购/增持，彰显管理层信心")
            for kw in ["订单", "大单", "中标", "签约"]:
                if kw in stdout:
                    match = re.search(r"(\d+\.?\d*)\s*(亿元|万元)", stdout)
                    if match:
                        catalysts.append(f"获得{kw}{match.group(1)}{match.group(2)}，利好长期订单可见性")
                    else:
                        catalysts.append(f"{kw}动态，关注后续进展")
                    break  # Only add one order-related catalyst
            if len(catalysts) >= 5:
                break
        if not catalysts:
            catalysts.append("暂无明确催化剂")
    except Exception as e:
        log_error("catalysts", str(e))
        catalysts = ["催化剂数据查询失败"]
    return catalysts


def fetch_gs_financial_metrics(ticker: str, market: str, price_data: dict = None) -> dict:
    """Fetch Goldman Sachs standard core financial metrics.

    Args:
        price_data: Optional dict from _fetch_price_from_mx with pe key.
                    If provided and PE is present, skips the Beta/市盈率 query.
    """
    metrics = {"roe": "N/A", "fcf": "N/A", "fcf_note": "", "debt_ratio": "N/A",
               "net_debt_ebitda": "N/A", "beta": "N/A",
               "forecast_pe_fy1": "N/A", "forecast_pe_fy2": "N/A", "forecast_pe_fy3": "N/A",
               "forecast_peg_fy1": "N/A", "forecast_roe_fy1": "N/A"}

    # Pre-fill PE from price_data if available
    pe_known = None
    if price_data and price_data.get('pe'):
        pe_known = price_data['pe']

    try:
        query_ticker = DataParser.to_query_ticker(ticker, market)

        # ── Parallelize queries ──
        def _run_query(query: str) -> str:
            try:
                r = mx_data_cached(query_ticker, query, TTL_GS_METRICS, timeout=TIMEOUT_DATA)
                return r.stdout
            except Exception:
                return ""

        queries = [
            f"{query_ticker} 净资产收益率 ROE 自由现金流 资产负债率",
        ]
        # Only query Beta/PE if PE not already known from price_data
        if not pe_known:
            queries.append(f"{query_ticker} Beta 系数 市盈率")
        else:
            queries.append(f"{query_ticker} Beta 系数")
        # Query forward PE / PEG for forecast metrics
        queries.append(f"{query_ticker} 预测市盈率PE 历史PEG值")
        with ThreadPoolExecutor(max_workers=3) as executor:
            stdout_results = list(executor.map(_run_query, queries))

        # Parse query 1: ROE, FCF, debt ratio
        stdout1 = stdout_results[0]
        headers1 = []
        for line in stdout1.splitlines():
            if re.match(r"\|\s*date\s*\|", line, re.I):
                headers1 = [p.strip() for p in line.strip().strip("|").split("|")]
            elif re.match(r"\|\s*20\d", line):
                parts = [p.strip() for p in line.strip().strip("|").split("|")]
                if len(parts) >= 2 and headers1:
                    for i, col in enumerate(headers1[1:], 1):
                        if i < len(parts) and parts[i] and parts[i] != "-":
                            if "ROE" in col.upper() or "净资产收益率" in col:
                                metrics["roe"] = parts[i]
                            elif "自由现金流" in col or "FCF" in col.upper() or "FCFF" in col.upper():
                                metrics["fcf"] = parts[i]
                                metrics["fcf_note"] = "(TTM)"
                            elif "资产负债率" in col:
                                metrics["debt_ratio"] = parts[i]
                    break

        # Parse query 2: Beta
        stdout2 = stdout_results[1]
        headers2 = []
        for line in stdout2.splitlines():
            if re.match(r"\|\s*date\s*\|", line, re.I):
                headers2 = [p.strip() for p in line.strip().strip("|").split("|")]
            elif re.match(r"\|\s*20\d", line):
                parts = [p.strip() for p in line.strip().strip("|").split("|")]
                if len(parts) >= 2 and headers2:
                    for i, col in enumerate(headers2[1:], 1):
                        if i < len(parts) and parts[i] and parts[i] != "-":
                            if "beta" in col.lower() or "β" in col or "Beta" in col:
                                match = re.search(r"([\d.]+)", parts[i])
                                if match: metrics["beta"] = match.group(1)
                    break

        if metrics["beta"] == "N/A":
            metrics["beta"] = "N/A"

        # Parse query 3: Forward PE / PEG
        # mx-data returns years in descending order (2028, 2027, 2026)
        # Parse query 3: Forward PE/PEG (e.g. "预测市盈率PE", "预测PEG")
        # mx-data 返回按年份降序：2028 → 2027 → 2026
        # 映射：2026=FY1, 2027=FY2, 2028=FY3
        year_to_pe_field = {"2026": "forecast_pe_fy1", "2027": "forecast_pe_fy2", "2028": "forecast_pe_fy3"}
        # PEG只取FY1（2026年），其他年份不映射
        if len(stdout_results) > 2:
            stdout3 = stdout_results[2]
            headers3 = []
            for line in stdout3.splitlines():
                if re.match(r"\|\s*date\s*\|", line, re.I):
                    headers3 = [p.strip() for p in line.strip().strip("|").split("|")]
                elif re.match(r"\|\s*\d{4}\s*\|", line):
                    parts = [p.strip() for p in line.strip().strip("|").split("|")]
                    if len(parts) >= 2 and headers3:
                        year = parts[0].strip()
                        pe_field = year_to_pe_field.get(year)
                        for i, col in enumerate(headers3[1:], 1):
                            if i < len(parts) and parts[i] and parts[i] != "-":
                                val = parts[i]
                                # PE: 精确匹配"预测市盈率"，排除含"PEG"的列（避免误匹配"预测PEG(日期)"）
                                if pe_field and ("预测市盈率" in col or "预测PE" in col) and "PEG" not in col:
                                    if metrics[pe_field] == "N/A":
                                        metrics[pe_field] = val
                                # PEG: 只取FY1（2026年）的值，避免年份顺序导致覆盖
                                if "预测PEG" in col and year == "2026" and metrics["forecast_peg_fy1"] == "N/A":
                                    metrics["forecast_peg_fy1"] = val

        if metrics["debt_ratio"] != "N/A":
            match = re.search(r"([\d.]+)", metrics["debt_ratio"])
            if match:
                debt_ratio = float(match.group(1)) / 100
                metrics["net_debt_ebitda"] = f"{debt_ratio * 3:.1f}x"
    except Exception as e:
        log_error("gs_financial_metrics", str(e))
    return metrics


# ═══════════════════════════════════════════════════════════════════════════════
# US Fundamentals
# ═══════════════════════════════════════════════════════════════════════════════

def get_us_fundamentals(ticker: str) -> str:
    """Fetch US stock recent 3-quarter financials (FMP or yfinance)."""
    import urllib.request, json as _json
    fmp_key = os.environ.get("FMP_API_KEY", "")
    if fmp_key:
        try:
            url = f"https://financialmodelingprep.com/stable/income-statement?symbol={ticker}&limit=3&apikey={fmp_key}"
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = _json.loads(resp.read())
            if isinstance(data, list) and data and "date" in data[0]:
                lines = []
                for x in data[:3]:
                    rev = x.get("revenue") or 0
                    net = x.get("netIncome") or 0
                    gp = x.get("grossProfit") or 0
                    eps = x.get("eps") or 0
                    gm = f"{gp/rev*100:.1f}%" if rev else "N/A"
                    lines.append(f"  {x['date'][:7]}: 营收{rev/1e9:.2f}B | 净利润{net/1e9:.2f}B | 毛利率{gm} | EPS{eps:.2f}")
                url2 = f"https://financialmodelingprep.com/stable/ratios-ttm?symbol={ticker}&apikey={fmp_key}"
                with urllib.request.urlopen(url2, timeout=10) as resp2:
                    r2 = _json.loads(resp2.read())
                r2 = r2[0] if isinstance(r2, list) and r2 else {}
                pe = r2.get("peRatioTTM") or "N/A"
                pb = r2.get("priceToBookRatioTTM") or "N/A"
                if pe != "N/A": pe = f"{pe:.1f}"
                if pb != "N/A": pb = f"{pb:.1f}"
                lines.append(f"  估值(TTM): PE={pe} | PB={pb}")
                return "\n".join(lines)
        except Exception:
            pass
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        inc = t.quarterly_income_stmt
        cf = t.quarterly_cashflow
        if inc is None or inc.empty: return ""
        cols = inc.columns[:3]
        lines = []
        for col in cols:
            label = col.strftime("%YQ%q") if hasattr(col, "strftime") else str(col)[:7]
            rev = inc.get("Total Revenue", {}).get(col)
            gp = inc.get("Gross Profit", {}).get(col)
            net = inc.get("Net Income", {}).get(col)
            fcf_row = cf.get("Free Cash Flow", {}) if cf is not None and not cf.empty else {}
            fcf = fcf_row.get(col)
            rev_b = f"{rev/1e9:.2f}B" if rev else "N/A"
            gm_pct = f"{gp/rev*100:.1f}%" if (gp and rev) else "N/A"
            net_b = f"{net/1e9:.2f}B" if net else "N/A"
            fcf_b = f"{fcf/1e9:.2f}B" if fcf else "N/A"
            lines.append(f"  {label}: 营收{rev_b} | 净利润{net_b} | 毛利率{gm_pct} | FCF{fcf_b}")
        return "\n".join(lines)
    except Exception:
        return ""


def get_cn_hk_fundamentals(ticker: str) -> str:
    """Fetch A/HK stock financials via mx-data."""
    try:
        if ticker.upper().startswith("HK") and ticker[2:].isdigit():
            query_ticker = f"{ticker[2:]}.HK"
        else:
            query_ticker = ticker
        r = subprocess.run(["python3.12", MX_DATA_SCRIPT, f"{query_ticker}近 4 期营业收入 销售毛利率 自由现金流 经营活动现金流"],
                           capture_output=True, text=True, timeout=TIMEOUT_DATA)
        headers, lines = [], []
        KEY_FIELDS = ["营业收入", "销售毛利率", "经营活动产生的现金流量净额", "企业自由现金流量 FCFF"]
        for line in r.stdout.splitlines():
            if re.match(r"\|\s*date\s*\|", line, re.I):
                headers = [p.strip() for p in line.strip().strip("|").split("|")]
            elif re.match(r"\|\s*20\d{2}", line):
                parts = [p.strip() for p in line.strip().strip("|").split("|")]
                if len(parts) < 2: continue
                date = parts[0]
                fields = []
                for i, val in enumerate(parts[1:], 1):
                    if val and val != "-" and i < len(headers):
                        col = headers[i]
                        if any(k in col for k in KEY_FIELDS):
                            label = ("营收" if "营业收入" in col else "毛利率" if "毛利率" in col
                                     else "经营现金流" if "经营活动" in col else "FCF")
                            fields.append(f"{label}:{val}")
                if fields: lines.append(f"  {date}: " + " | ".join(fields))
        return "\n".join(lines)
    except Exception:
        return ""


# ═══════════════════════════════════════════════════════════════════════════════
# Archiving
# ═══════════════════════════════════════════════════════════════════════════════

def save_to_investment_db(ticker: str, r, ta_decision: Optional[str], macro_score=None):
    """Save analysis result to local investment-db."""
    import sys
    try:
        sys.path.insert(0, os.path.dirname(INVESTMENT_DB_SCRIPT))
        from data_warehouse import append_record
        d = r.dashboard if isinstance(r.dashboard, dict) else {}
        bp = d.get("battle_plan", {})
        sp = bp.get("sniper_points", {}) if isinstance(bp, dict) else {}
        record = {
            'symbol': ticker, 'date': datetime.now().strftime('%Y-%m-%d'),
            'sentiment_score': r.sentiment_score, 'recommendation': r.operation_advice,
            'target_price': sp.get('take_profit'), 'stop_loss': sp.get('stop_loss'),
            'analysis_source': 'dual_engine_analyze', 'ta_decision': ta_decision or 'N/A',
        }
        if macro_score is not None:
            record['macro_score'] = macro_score.macro
            record['sector_score'] = macro_score.sector
            record['news_score'] = macro_score.news
            record['fundamental_total'] = macro_score.total
        append_record('analysis_history', record)
        print("   💾 investment-db 存档完成")
    except Exception as e:
        print(f"   ⚠️ investment-db 存档失败: {e}")


def save_to_notion(ticker: str, report_text: str):
    """Save report to Notion openclaw_invest_note."""
    try:
        tmp_md = f"/tmp/invest_note_{ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        with open(tmp_md, 'w') as f:
            f.write(report_text)
        title = f"{ticker} 分析报告 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        result = subprocess.run(
            ["node", "scripts/md-to-notion.js", tmp_md, NOTION_INVEST_PAGE_ID, title, "--allow-unsafe-paths"],
            capture_output=True, text=True, cwd=NOTION_SYNC_DIR,
            env={**os.environ, "NOTION_API_KEY": os.environ.get("NOTION_API_KEY", "")}
        )
        os.unlink(tmp_md)
        if result.returncode == 0:
            print("   📝 Notion 存档完成")
        else:
            print(f"   ⚠️ Notion 存档失败: {result.stderr.strip()[:100]}")
    except Exception as e:
        print(f"   ⚠️ Notion 存档失败: {e}")


def fetch_quarterly_data(ticker: str, market: str) -> dict:
    """Fetch the latest quarterly (Q1) financial data from mx-data.

    Returns dict:
        revenue_q: str or None       — 营收（亿元）
        net_profit_q: str or None    — 归母净利润（亿元）
        revenue_yoy_q: str or None   — 营收同比
        net_profit_yoy_q: str or None— 净利润同比
        quarter_label: str           — e.g. "2026Q1"
        segment_data: list[dict]    — [{name, revenue}] 分业务板块数据
    """
    result = {
        "revenue_q": None, "net_profit_q": None,
        "revenue_yoy_q": None, "net_profit_yoy_q": None,
        "quarter_label": "", "segment_data": []
    }
    try:
        query_ticker = DataParser.to_query_ticker(ticker, market)
        unit = "港元" if market == "hk" else "元"

        # Build env with MX_APIKEY
        env = dict(os.environ)
        env_file = os.path.join(DAILY_ANALYSIS_DIR, ".env")
        try:
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("MX_APIKEY") and "=" in line:
                        k, v = line.split("=", 1)
                        env[k.strip()] = v.strip()
        except Exception:
            pass

        r = mx_data_cached(
            query_ticker,
            f"{query_ticker} 营业总收入 归母净利润 同比 季度",
            TTL_EARNINGS,
            env=env, timeout=TIMEOUT_DATA
        )

        headers = []
        latest_quarter = None
        latest_idx = 0

        for line in r.stdout.splitlines():
            if re.match(r"\|\s*date\s*\|", line, re.I):
                headers = [p.strip() for p in line.strip().strip("|").split("|")]
            elif re.match(r"\|\s*\d{4}[-/]\d{2}", line):
                parts = [p.strip() for p in line.strip().strip("|").split("|")]
                if parts and headers:
                    date_str = parts[0]
                    # Pick latest quarter row
                    if not latest_quarter or date_str > latest_quarter:
                        latest_quarter = date_str
                        latest_idx = 0  # will be updated below

        # Re-parse to get latest row with column mapping
        for line in r.stdout.splitlines():
            if re.match(r"\|\s*date\s*\|", line, re.I):
                headers = [p.strip() for p in line.strip().strip("|").split("|")]
            elif re.match(r"\|\s*\d{4}[-/]\d{2}", line):
                parts = [p.strip() for p in line.strip().strip("|").split("|")]
                if not parts or len(parts) < 2:
                    continue
                date_str = parts[0]
                if date_str != latest_quarter:
                    continue
                col_map = {}
                for i, col in enumerate(headers):
                    if i < len(parts):
                        col_map[col] = parts[i]

                # Revenue
                rev_raw = col_map.get("营业总收入(元)") or col_map.get("营业收入") \
                          or col_map.get("营业总收入")
                if rev_raw and rev_raw != "-":
                    m = re.search(r"([\d.]+)", rev_raw)
                    if m:
                        val = float(m.group(1))
                        # Convert 元 → 亿元
                        result["revenue_q"] = f"{val / 1e8:.2f}"

                # Net profit
                np_raw = col_map.get("归母净利润(元)") or col_map.get("净利润") \
                         or col_map.get("归母净利润")
                if np_raw and np_raw != "-":
                    m = re.search(r"([\d.]+)", np_raw)
                    if m:
                        val = float(m.group(1))
                        result["net_profit_q"] = f"{val / 1e8:.2f}"

                # Revenue YoY
                rg_raw = col_map.get("营业总收入增长率(%)") or col_map.get("营收同比增长率") \
                         or col_map.get("营业收入增长率")
                if rg_raw and rg_raw != "-":
                    m = re.search(r"([\-\d.]+)", rg_raw)
                    if m:
                        result["revenue_yoy_q"] = f"{m.group(1)}%"

                # Net profit YoY
                pg_raw = col_map.get("归母净利润增长率(%)") or col_map.get("利润同比增长率") \
                         or col_map.get("净利润增长率")
                if pg_raw and pg_raw != "-":
                    m = re.search(r"([\-\d.]+)", pg_raw)
                    if m:
                        result["net_profit_yoy_q"] = f"{m.group(1)}%"

                # Quarter label
                qm = re.search(r"(\d{4})[-/](\d{2})", date_str)
                if qm:
                    year, month = int(qm.group(1)), int(qm.group(2))
                    quarter = (month - 1) // 3 + 1
                    result["quarter_label"] = f"{year}Q{quarter}"
                else:
                    result["quarter_label"] = latest_quarter[:7] if latest_quarter else ""
                break

        # Fallback: if quarterly data still empty, try the raw JSON
        if not result["revenue_q"]:
            _try_parse_quarterly_json(f"{query_ticker} 营业总收入 归母净利润 同比 季度", result)

    except Exception as e:
        log_error("quarterly_data", str(e))

    return result


def _try_parse_quarterly_json(query_str: str, result: dict) -> None:
    """Fallback: parse mx-data raw JSON for quarterly data."""
    path = find_latest_raw_json(query_str, max_age=86400)
    if not path:
        return
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    d = _navigate_mx_data_json(raw)
    if not isinstance(d, dict):
        return
    sr = d.get("searchDataResultDTO")
    if not sr or not isinstance(sr, dict):
        return
    tables = sr.get("dataTableDTOList", [])
    for tbl in tables:
        if not isinstance(tbl, dict):
            continue
        name_map = tbl.get("nameMap", {})
        tbl_data = tbl.get("table", {})
        if not name_map or not isinstance(tbl_data, dict):
            continue
        head_names = tbl_data.get("headName", [])
        if not head_names:
            continue
        # Use the first (most recent) column
        row_idx = 0

        def _get(col_names: list) -> str:
            for cn in col_names:
                for fk, fn in name_map.items():
                    if fn == cn and fk in tbl_data:
                        vals = tbl_data[fk]
                        if isinstance(vals, list) and row_idx < len(vals):
                            return str(vals[row_idx]) if vals[row_idx] else ""
            return ""

        rev_raw = _get(["营业总收入(元)", "营业收入", "营业总收入"])
        if rev_raw and rev_raw not in ("-", "N/A"):
            m = re.search(r"([\d.]+)", rev_raw)
            if m:
                result["revenue_q"] = f"{float(m.group(1)) / 1e8:.2f}"

        np_raw = _get(["归母净利润(元)", "净利润", "归母净利润"])
        if np_raw and np_raw not in ("-", "N/A"):
            m = re.search(r"([\d.]+)", np_raw)
            if m:
                result["net_profit_q"] = f"{float(m.group(1)) / 1e8:.2f}"

        rg_raw = _get(["营业总收入增长率(%)", "营收同比增长率"])
        if rg_raw and rg_raw not in ("-", "N/A"):
            m = re.search(r"([\-\d.]+)", rg_raw)
            if m:
                result["revenue_yoy_q"] = f"{m.group(1)}%"

        pg_raw = _get(["归母净利润增长率(%)", "利润同比增长率"])
        if pg_raw and pg_raw not in ("-", "N/A"):
            m = re.search(r"([\-\d.]+)", pg_raw)
            if m:
                result["net_profit_yoy_q"] = f"{m.group(1)}%"

        date_raw = _get(["date", "日期"])
        if date_raw and not result["quarter_label"]:
            qm = re.search(r"(\d{4})[-/](\d{2})", date_raw)
            if qm:
                year, month = int(qm.group(1)), int(qm.group(2))
                result["quarter_label"] = f"{year}Q{(month - 1) // 3 + 1}"


def fetch_concept_tags(ticker: str, market: str) -> str:
    """Fetch concept/tag strings from mx-data 所属行业板块 query.

    Returns a pipe-separated tag string, e.g. "机器人概念 | 新能源汽车 | 医疗器械"
    or "" if not available.
    """
    try:
        query_ticker = DataParser.to_query_ticker(ticker, market)
        r = mx_data_cached(
            query_ticker, f"{query_ticker} 所属行业板块 概念",
            TTL_PROFILE, timeout=TIMEOUT_DATA
        )
        tags = []
        seen = set()
        for line in r.stdout.splitlines():
            if not line.strip() or line.startswith("| date") or line.startswith("---"):
                continue
            # Match concept/industry words
            parts = line.strip().strip("|").split("|")
            for p in parts:
                p = p.strip()
                if not p or p == "-" or p in seen:
                    continue
                if any(p.startswith(kw) for kw in ("机器人", "新能源", "人工智能", "智能", "医疗", "汽车", "家电", "热管理", "电子", "半导体", "储能", "汽车零部件", "汽车热管理", "液冷", "冷链")):
                    if len(p) < 20 and p not in seen:
                        tags.append(p)
                        seen.add(p)
                elif re.search(r"[\u4e00-\u9fff]{3,8}", p) and len(p) < 12 and p not in seen:
                    # Generic Chinese tag: 3-8 chars, not already seen
                    tags.append(p)
                    seen.add(p)
        if tags:
            return " | ".join(tags[:6])
    except Exception as e:
        log_error("concept_tags", str(e))
    return ""


def fetch_revenue_composition(ticker: str, market: str, revenue_split: str = "") -> dict:
    """Fetch revenue composition (domestic/overseas, business segments).

    Args:
        revenue_split: Optional string from company_profile (e.g. '国内 60% | 海外 40%').
                       If provided, parses it directly without mx-data query.
    """
    composition = {"domestic": "N/A", "overseas": "N/A", "by_product": [], "by_region": []}

    # Try to parse from company_profile's revenue_split first (saves 1 mx-data query)
    if revenue_split:
        dm = re.search(r"(?:国内|中国|境内)\s*([\d.]+)%", revenue_split)
        om = re.search(r"(?:海外|国外|境外|国际)\s*([\d.]+)%", revenue_split)
        if dm: composition["domestic"] = dm.group(1) + "%"
        if om: composition["overseas"] = om.group(1) + "%"
        if dm or om:
            return composition  # Data found from profile, skip mx-data query

    try:
        query_ticker = DataParser.to_query_ticker(ticker, market)
        r = subprocess.run(["python3.12", MX_DATA_SCRIPT, f"{query_ticker} 营收构成 主营业务 国内 海外"],
                           capture_output=True, text=True, timeout=TIMEOUT_DATA)
        headers = []
        for line in r.stdout.splitlines():
            if re.match(r"\|\s*date\s*\|", line, re.I):
                headers = [p.strip() for p in line.strip().strip("|").split("|")]
            elif re.match(r"\|\s*20\d", line):
                parts = [p.strip() for p in line.strip().strip("|").split("|")]
                if len(parts) >= 2 and headers:
                    for i, col in enumerate(headers[1:], 1):
                        if i < len(parts) and parts[i] and parts[i] != "-":
                            if "国内" in col or "大陆" in col or "境内" in col:
                                match = re.search(r"([\d.]+)%?", parts[i])
                                if match: composition["domestic"] = match.group(1) + "%"
                            elif "海外" in col or "国外" in col or "境外" in col or "国际" in col:
                                match = re.search(r"([\d.]+)%?", parts[i])
                                if match: composition["overseas"] = match.group(1) + "%"
                    break
    except Exception as e:
        log_error("revenue_composition", str(e))
    return composition
