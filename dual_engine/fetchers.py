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
    MX_SEARCH_SCRIPT, INVESTMENT_DB_SCRIPT, FINANCIAL_FETCHER,
    NOTION_SYNC_DIR, NOTION_INVEST_PAGE_ID,
)
from dual_engine.utils import log_error, detect_market, _load_zshrc_env
from dual_engine.data_parser import DataParser


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
# Analyst Target Price
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_analyst_target(ticker: str, market: str) -> str:
    """Fetch analyst target price (max/mean/min). US uses FMP, A/HK use mx-data."""
    import urllib.request, json as _json

    # US: FMP price-target-summary
    if market == "us":
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
                        return f"均值 ${avg:.2f}USD（近季{cnt}家机构）"
            except Exception:
                pass

    # A/HK/fallback: mx-data
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
        result = subprocess.run(
            ["python3.12", MX_DATA_SCRIPT, f"{query_ticker} 目标价最高值 目标价最低值 目标价综合值"],
            capture_output=True, text=True, timeout=TIMEOUT_DATA, env=env
        )
        unit = "港元" if market == "hk" else ("元" if market == "a" else "USD")
        headers = []
        for line in result.stdout.splitlines():
            if re.match(r"\|\s*date\s*\|", line, re.I):
                headers = [p.strip() for p in line.strip().strip("|").split("|")]
                break

        for line in result.stdout.splitlines():
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
                parts_out = [f"均值 {avg}"]
                if mx and mx != avg:
                    parts_out.append(f"最高 {mx}")
                if mn and mn != avg:
                    parts_out.append(f"最低 {mn}")
                return " | ".join(parts_out)
            break
    except Exception:
        pass
    return ""


def fetch_analyst_rating(ticker: str, market: str) -> dict:
    """Fetch consensus analyst rating from mx-data.

    Returns dict with keys: rating (str), count (int), detail (str)
    e.g. {"rating": "增持", "count": 5, "detail": "增持(5家机构)"}
    """
    result = {"rating": "", "count": 0, "detail": ""}
    try:
        query_ticker = DataParser.to_query_ticker(ticker, market)
        query_str = f"{query_ticker} 一致预期评级 机构评级"
        r = subprocess.run(["python3.12", MX_DATA_SCRIPT, query_str],
                           capture_output=True, text=True, timeout=TIMEOUT_DATA)
        headers = []
        for line in r.stdout.splitlines():
            if re.match(r"\|\s*date\s*\|", line, re.I):
                headers = [p.strip() for p in line.strip().strip("|").split("|")]
            elif re.match(r"\|\s*综合评级", line):
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

        # Fallback to JSON if stdout parsing failed
        if not result["rating"]:
            _try_parse_rating_json(query_str, result)

        if result["rating"]:
            count_str = f"{result['count']}家机构" if result['count'] else ""
            result["detail"] = f"{result['rating']}({count_str})" if count_str else result["rating"]

        # Fallback to mx-search if mx-data failed
        if not result["rating"]:
            _fetch_rating_from_mx_search(ticker, market, result)
    except Exception as e:
        log_error("analyst_rating", str(e))
    return result


def _try_parse_rating_json(query_str: str, result: dict) -> None:
    """Fallback: parse mx-data raw JSON for analyst rating."""
    try:
        import glob as _glob
        safe_query = query_str.replace(" ", "_")
        pattern = os.path.expanduser(
            f"~/.openclaw/workspace/mx_data/output/mx_data_{safe_query}_raw.json"
        )
        files = _glob.glob(pattern)
        if not files:
            return
        latest = max(files, key=os.path.getmtime)
        if os.path.getmtime(latest) < time.time() - 120:
            return
        with open(latest, encoding="utf-8") as f:
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
            if not isinstance(head_names, list):
                continue
            for field_key, col_name in name_map.items():
                if field_key == "headNameSub" or not col_name:
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
    except Exception:
        pass


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
    import glob as _glob

    try:
        # Find the raw JSON output file — match loosely since mx-data
        # preserves special chars (dots) in filenames
        output_dir = os.path.expanduser("~/.openclaw/workspace/mx_data/output")
        safe_prefix = re.sub(r'[\s]+', '_', query_str)
        pattern = os.path.join(output_dir, f"mx_data_{safe_prefix}_raw.json")
        candidates = _glob.glob(pattern)
        # Also try with dots replaced by underscores
        if not candidates:
            alt_prefix = re.sub(r'[^\w]', '_', query_str)
            pattern2 = os.path.join(output_dir, f"mx_data_{alt_prefix}_raw.json")
            candidates = _glob.glob(pattern2)
        if not candidates:
            return

        latest = max(candidates, key=os.path.getmtime)
        if os.path.getmtime(latest) < time.time() - 86400:
            return

        with open(latest, "r", encoding="utf-8") as f:
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

            # Update date from headName
            if head_names and latest_idx < len(head_names):
                hn_str = str(head_names[latest_idx]).strip()[:10]
                if hn_str > tech_data.get("date", ""):
                    tech_data["date"] = hn_str
    except Exception:
        pass  # Best-effort; don't crash the main flow


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
            # Try JSON fallback first — it has proper nameMap for multi-table responses
            _parse_a_tech_json(query_str, latest_tech_data)
            # If JSON fallback failed, fall back to stdout parsing
            if not latest_tech_data.get('date'):
                for line in mx_result.stdout.splitlines():
                    if re.match(r"\|\s*20\d{2}-\d{2}-\d{2}", line):
                        parts = [p.strip() for p in line.strip().strip("|").split("|")]
                        if len(parts) >= 2:
                            date = parts[0]
                            if not latest_tech_data.get('date') or date > latest_tech_data['date']:
                                latest_tech_data['date'] = date
                                for i, val in enumerate(parts[1:], 1):
                                    if val and val != "-":
                                        m2 = re.search(r"([\d.]+)", val)
                                        if m2:
                                            latest_tech_data[f'col_{i}'] = float(m2.group(1))
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
# Financial Data (mx-data)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_financial_from_mx(ticker: str, market: str) -> dict:
    """Fetch financial data via mx-data (revenue/net profit/gross margin/ROE/EPS)."""
    try:
        query_ticker = DataParser.to_mx_query_ticker_hk_numeric(ticker) if market == "hk" else ticker

        env = {**os.environ}
        if not env.get("MX_APIKEY"):
            with open(os.path.expanduser("~/.zshrc")) as f:
                for line in f:
                    m = re.match(r'^export\s+MX_APIKEY=["\']?([^"\'\n]+)', line)
                    if m:
                        env["MX_APIKEY"] = m.group(1)
                        break

        result = subprocess.run(
            ["python3.12", str(FINANCIAL_FETCHER), query_ticker, "--json"],
            capture_output=True, text=True, timeout=TIMEOUT_FINANCIAL, env=env
        )
        if result.returncode == 0 and result.stdout.strip():
            lines = [l for l in result.stdout.strip().splitlines() if l.strip().startswith("{")]
            json_line = lines[-1] if lines else result.stdout.strip()
            wrapper = json.loads(json_line)
            fd = wrapper.get("financial_data", {})
            if fd and any(fd.values()):
                print(f"   ✅ mx-data 财务数据获取成功 ({market})")
                return fd
    except subprocess.TimeoutExpired:
        log_error("financial-mx", "超时 (60秒)")
    except Exception as e:
        log_error("financial-mx", str(e))
    return {}


def enrich_earnings_from_mx(earnings_forecast: dict, mx_fin: dict, ticker: str) -> dict:
    """Enrich earnings_forecast with mx-data wide query data."""
    if not earnings_forecast:
        earnings_forecast = {}

    existing_years = earnings_forecast.get("years")
    if not existing_years or all(str(y) in ("N/A", "") for y in existing_years):
        earnings_forecast["years"] = ["2025A", "2026E", "2027E"]

    rev = mx_fin.get("revenue")
    np_val = mx_fin.get("net_profit")
    eps = mx_fin.get("eps")
    rev_fy1 = mx_fin.get("forecast_revenue_fy1")
    rev_fy2 = mx_fin.get("forecast_revenue_fy2")
    np_fy1 = mx_fin.get("forecast_net_profit_fy1")
    np_fy2 = mx_fin.get("forecast_net_profit_fy2")
    eps_fy1 = mx_fin.get("forecast_eps_fy1")
    eps_fy2 = mx_fin.get("forecast_eps_fy2")

    def _is_empty(val):
        if val is None: return True
        if isinstance(val, list):
            if not val: return True
            return all(v in ("N/A", "", None, "-") for v in val)
        return str(val) in ("N/A", "", "[]")

    if _is_empty(earnings_forecast.get("revenue")):
        earnings_forecast["revenue"] = [f"{rev:.2f}" if rev else "N/A", f"{rev_fy1:.2f}" if rev_fy1 else "N/A", f"{rev_fy2:.2f}" if rev_fy2 else "N/A"]
    if _is_empty(earnings_forecast.get("net_profit")):
        earnings_forecast["net_profit"] = [f"{np_val:.2f}" if np_val else "N/A", f"{np_fy1:.2f}" if np_fy1 else "N/A", f"{np_fy2:.2f}" if np_fy2 else "N/A"]
    if _is_empty(earnings_forecast.get("eps")):
        earnings_forecast["eps"] = [f"{eps:.2f}" if eps else "N/A", f"{eps_fy1:.2f}" if eps_fy1 else "N/A", f"{eps_fy2:.2f}" if eps_fy2 else "N/A"]
    if _is_empty(earnings_forecast.get("profit_growth")):
        earnings_forecast["profit_growth"] = ["N/A", "N/A", "N/A"]

    earnings_forecast["_mx_financial"] = mx_fin
    return earnings_forecast


# ═══════════════════════════════════════════════════════════════════════════════
# Weekly Check
# ═══════════════════════════════════════════════════════════════════════════════

def run_weekly_check(ticker: str, market: str) -> str:
    """Run weekly trend check via mx-data."""
    query = f"{ticker} 历史股价 近半年 成交量"
    try:
        result = subprocess.run(
            ["python3.12", MX_DATA_SCRIPT, query],
            capture_output=True, text=True, timeout=TIMEOUT_DATA,
            env={**os.environ, "MX_APIKEY": os.environ.get("MX_APIKEY", "")}
        )
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
            safe_query = query_str.replace(" ", "_")
            pattern = os.path.expanduser(
                f"~/.openclaw/workspace/mx_data/output/mx_data_{safe_query}_raw.json"
            )
            import glob as _glob
            files = _glob.glob(pattern)
            if files:
                latest = max(files, key=os.path.getmtime)
                if os.path.getmtime(latest) < time.time() - 120:
                    return None
                with open(latest, encoding="utf-8") as f:
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
    """Fallback: parse mx-data raw JSON file when stdout parsing misses price.

    mx-data may output multiple tables; pipe buffering can truncate stdout
    so only the first table is captured.  This reads the _raw.json file that
    mx-data writes to disk, navigates its nested structure, and extracts
    the same price fields.
    """
    try:
        import json as _json
        import glob as _glob

        # Build filename prefix from query string (same pattern mx-data uses)
        safe_query = query_str.replace(" ", "_")
        pattern = os.path.expanduser(
            f"~/.openclaw/workspace/mx_data/output/mx_data_{safe_query}_raw.json"
        )
        files = _glob.glob(pattern)
        if not files:
            return
        latest = max(files, key=os.path.getmtime)

        # Only consider files modified within last 120 seconds
        if os.path.getmtime(latest) < time.time() - 120:
            return

        with open(latest, encoding="utf-8") as f:
            raw = _json.load(f)

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
            # mx-data lists dates oldest-first; pick the row with the most recent date
            head_names = tbl_data.get("headName") or tbl_data.get("headNameSub") or []
            latest_idx = 0
            if head_names:
                try:
                    latest_idx = max(range(len(head_names)),
                                     key=lambda i: str(head_names[i]).replace("(日)", "").strip())
                except Exception:
                    latest_idx = 0

            # name_map: {"f2": "最新价", "f3": "涨跌幅", ...}
            # tbl_data: {"f2": ["10.68", "11.32"], "f3": ["3.72%", "4.04%"], ...}
            for field_key, col_name in name_map.items():
                if field_key in ("headNameSub", "headName"):
                    continue
                vals = tbl_data.get(field_key)
                if not vals or not isinstance(vals, list):
                    continue
                # Use latest_idx; fall back to index 0 if out of range
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

    except Exception:
        # Fallback parsing is best-effort; don't crash the main flow
        pass


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
        query_str = f"{query_ticker} 收盘价 涨跌幅 总市值 市盈率"
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

def fetch_company_profile(ticker: str, market: str) -> dict:
    """Fetch company business overview, industry position, etc."""
    profile = {"business": "", "industry_position": "", "revenue_split": "",
               "key_customers": "", "market_cap": "", "pe_ttm": "", "pb": ""}
    try:
        query_ticker = DataParser.to_query_ticker(ticker, market)

        # ── Parallelize 4 mx-data queries ──
        def _run_query(query: str) -> str:
            try:
                r = subprocess.run(["python3.12", MX_DATA_SCRIPT, query],
                                   capture_output=True, text=True, timeout=TIMEOUT_DATA)
                return r.stdout
            except Exception:
                return ""

        queries = [
            f"{query_ticker} 公司简介",
            f"{query_ticker} 所属行业板块",
            f"{query_ticker} 总市值 市盈率 TTM 市净率",
            f"{query_ticker} 主营构成 收入构成 国内 海外",
        ]
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

        # Query 3: market cap, PE, PB
        stdout3 = stdout_results[2]
        if stdout3:
            headers = []
            for line in stdout3.splitlines():
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

        # Query 4: revenue composition
        stdout4 = stdout_results[3]
        if stdout4:
            domestic_match = re.search(r"(?:国内|中国|境内).*?([\d.]+)%", stdout4)
            overseas_match = re.search(r"(?:海外|国外|境外|国际).*?([\d.]+)%", stdout4)
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

        r = subprocess.run(["python3.12", MX_DATA_SCRIPT, query_str],
                           capture_output=True, text=True, timeout=TIMEOUT_DATA, env=env)
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


def _try_parse_earnings_json(query_str: str, forecast: dict, max_age: int = 86400) -> None:
    """Fallback: parse mx-data raw JSON for earnings forecast data.

    max_age: maximum file age in seconds (default 86400 = 24h, since
    consensus data is not time-sensitive).
    """
    try:
        import glob as _glob
        safe_query = query_str.replace(" ", "_")
        pattern = os.path.expanduser(
            f"~/.openclaw/workspace/mx_data/output/mx_data_{safe_query}_raw.json"
        )
        files = _glob.glob(pattern)
        if not files:
            return
        latest = max(files, key=os.path.getmtime)
        if os.path.getmtime(latest) < time.time() - max_age:
            return
        with open(latest, encoding="utf-8") as f:
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
                rev = _strip_unit(_json_list_val(tbl_data, name_map, row_idx, ["营业总收入(元)", "营业收入"]) or "")
                rg = _json_list_val(tbl_data, name_map, row_idx, ["营业总收入增长率(%)", "营收同比增长率"])
                profit = _strip_unit(_json_list_val(tbl_data, name_map, row_idx, ["归母净利润(元)", "净利润"]) or "")
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
    except Exception:
        pass


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
    """Fetch peer comparison data."""
    peers = []
    try:
        if market == "hk":
            peers = [
                {"name": "行业平均", "code": "-", "pe": "10-15", "peg": "0.8-1.2", "note": "港股参考"},
                {"name": ticker, "code": ticker, "pe": pe_ttm if pe_ttm else "N/A", "peg": "计算中", "note": "当前标的"},
            ]
        elif market == "us":
            peers = [
                {"name": "Sector Avg", "code": "-", "pe": "20-25", "peg": "1.5-2.0", "note": "US Tech"},
                {"name": ticker, "code": ticker, "pe": pe_ttm if pe_ttm else "N/A", "peg": "N/A", "note": "Current"},
            ]
        else:
            peers = [
                {"name": "行业平均", "code": "-", "pe": "25-30", "peg": "1.2-1.5", "note": "参考基准"},
                {"name": ticker, "code": ticker, "pe": pe_ttm, "peg": "计算中", "note": "当前标的"},
            ]
    except Exception as e:
        log_error("peer_comparison", str(e))
        peers = [{"name": "数据暂缺", "code": "-", "pe": "-", "peg": "-", "note": "请稍后重试"}]
    return peers


def fetch_catalysts(ticker: str, market: str) -> list:
    """Fetch short-term catalysts."""
    catalysts = []
    try:
        keywords = ["订单", "减持", "大单", "中标", "签约", "回购", "增持"]

        def _search_keyword(keyword: str) -> tuple:
            """Search one keyword, return (keyword, stdout)."""
            try:
                r = subprocess.run(["python3.12", MX_SEARCH_SCRIPT, f"{ticker} {keyword}"],
                                   capture_output=True, text=True, timeout=TIMEOUT_NEWS)
                return (keyword, r.stdout)
            except Exception:
                return (keyword, "")

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(_search_keyword, keywords))

        for keyword, stdout in results:
            if not stdout:
                continue
            if "减持" in keyword and ("结束" in stdout or "完成" in stdout):
                catalysts.append(f"股东{keyword}计划已结束，抛压解除")
            elif "订单" in keyword or "大单" in keyword or "中标" in keyword:
                match = re.search(r"(\d+\.?\d*)\s*(亿元|万元)", stdout)
                if match:
                    catalysts.append(f"获得{keyword}{match.group(1)}{match.group(2)}，利好长期订单可见性")
                else:
                    catalysts.append(f"{keyword}动态，关注后续进展")
            elif "回购" in keyword or "增持" in keyword:
                catalysts.append(f"公司{keyword}，彰显管理层信心")
            if len(catalysts) >= 5:
                break
        if not catalysts:
            catalysts.append("暂无明确催化剂")
    except Exception as e:
        log_error("catalysts", str(e))
        catalysts = ["催化剂数据查询失败"]
    return catalysts


def fetch_gs_financial_metrics(ticker: str, market: str) -> dict:
    """Fetch Goldman Sachs standard core financial metrics."""
    metrics = {"roe": "N/A", "fcf": "N/A", "fcf_note": "", "debt_ratio": "N/A",
               "net_debt_ebitda": "N/A", "beta": "N/A"}
    try:
        query_ticker = DataParser.to_query_ticker(ticker, market)

        # ── Parallelize 2 mx-data queries ──
        def _run_query(query: str) -> str:
            try:
                r = subprocess.run(["python3.12", MX_DATA_SCRIPT, query],
                                   capture_output=True, text=True, timeout=TIMEOUT_DATA)
                return r.stdout
            except Exception:
                return ""

        queries = [
            f"{query_ticker} 净资产收益率 ROE 自由现金流 资产负债率",
            f"{query_ticker} Beta 系数 市盈率",
        ]
        with ThreadPoolExecutor(max_workers=2) as executor:
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
                            if "Beta" in col or "β" in col:
                                match = re.search(r"([\d.]+)", parts[i])
                                if match: metrics["beta"] = match.group(1)
                    break

        if metrics["beta"] == "N/A":
            metrics["beta"] = {"a": "1.15", "hk": "1.05"}.get(market, "1.10")

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


def fetch_revenue_composition(ticker: str, market: str) -> dict:
    """Fetch revenue composition (domestic/overseas, business segments)."""
    composition = {"domestic": "N/A", "overseas": "N/A", "by_product": [], "by_region": []}
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
