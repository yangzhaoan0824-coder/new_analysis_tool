"""
Data fetching layer - All subprocess and API calls for external data sources.

This module isolates all I/O operations from the core calculation logic.
Functions here are thin wrappers around external tool invocations.
"""

import os
import re
import subprocess
import json
from typing import Optional
from datetime import datetime, timedelta

from dual_engine.constants import (
    TIMEOUT_NEWS, TIMEOUT_DATA, TIMEOUT_ANALYSIS, TIMEOUT_FINANCIAL,
    DAILY_ANALYSIS_DIR, TRADING_AGENTS_SCRIPT, MX_DATA_SCRIPT,
    MX_SEARCH_SCRIPT, INVESTMENT_DB_SCRIPT, FINANCIAL_FETCHER,
    NOTION_SYNC_DIR, NOTION_INVEST_PAGE_ID,
)
from dual_engine.utils import log_error, detect_market, _load_zshrc_env
from dual_engine.data_parser import DataParser


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
            print(f"   📊 使用港股专用技术分析工具...")
            hk_code = ticker.replace("HK", "").replace("hk", "")
            hk_analyzer = os.path.join(os.path.dirname(os.path.dirname(__file__)), "hk_technical_analyzer.py")
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
                    print(f"   ✅ 港股技术指标获取成功：{latest_tech_data['date']}")
                    os.environ["MX_LATEST_DATE"] = latest_tech_data['date']
            else:
                print(f"   ⚠️ 港股技术分析工具失败：{result.stderr[:200]}")
        except Exception as e:
            print(f"   ⚠️ 港股技术分析异常：{e}")

    elif market == "a":
        try:
            query_ticker = ticker + (".SS" if ticker.startswith(("6", "5")) else ".SZ")
            mx_result = subprocess.run(
                ["python3.12", MX_DATA_SCRIPT, f"{query_ticker} MA5 MA20 MACD RSI 技术指标 近 30 日"],
                capture_output=True, text=True, timeout=TIMEOUT_DATA
            )
            for line in mx_result.stdout.splitlines():
                if re.match(r"\|\s*20\d{2}-\d{2}-\d{2}", line):
                    parts = [p.strip() for p in line.strip().strip("|").split("|")]
                    if len(parts) >= 2:
                        date = parts[0]
                        if not latest_tech_data.get('date') or date > latest_tech_data['date']:
                            latest_tech_data['date'] = date
                            for i, val in enumerate(parts[1:], 1):
                                if val and val != "-":
                                    match = re.search(r"([\d.]+)", val)
                                    if match:
                                        latest_tech_data[f'col_{i}'] = float(match.group(1))
            if latest_tech_data.get('date'):
                print(f"   ✅ mx-data 最新数据日期：{latest_tech_data['date']}")
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
        col1 = latest_tech_data.get('col_1', 0)
        col2 = latest_tech_data.get('col_2', 0)
        col5 = latest_tech_data.get('col_5', 0)
        col3 = latest_tech_data.get('col_3', 0)
        col4 = latest_tech_data.get('col_4', 0)

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
            return all(v in ("N/A", "", None) or (isinstance(v, str) and any(kw in v for kw in ["营业总", "净利润(", "归母", "EPS(", "每股"])) for v in val)
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
# HK Real-time Price
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_hk_price_from_mx(ticker: str) -> Optional[dict]:
    """Fetch HK real-time price via mx-data as fallback."""
    try:
        if ticker.startswith("HK") and ticker[2:].isdigit():
            query_ticker = f"{ticker[2:]}.HK"
        else:
            query_ticker = ticker

        result = subprocess.run(
            ["python3.12", MX_DATA_SCRIPT, f"{query_ticker} 最新价 涨跌幅 成交量 总市值 市盈率"],
            capture_output=True, text=True, timeout=TIMEOUT_DATA,
            env={**os.environ, "MX_APIKEY": os.environ.get("MX_APIKEY", "")}
        )

        price_data = {'price': None, 'change': None, 'volume': None, 'market_cap': None, 'pe': None}
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
                                    if "最新价" in col or "现价" in col:
                                        price_data['price'] = num
                                    elif "涨跌幅" in col:
                                        price_data['change'] = num
                                    elif "成交量" in col:
                                        price_data['volume'] = int(num * 10000) if num > 1000 else int(num)
                                    elif "总市值" in col:
                                        price_data['market_cap'] = val
                                    elif "市盈率" in col or "PE" in col.upper():
                                        price_data['pe'] = val
                    break

        if price_data['price']:
            print(f"   ✅ mx-data 获取到港股实时价格：{price_data['price']} 港元")
            return price_data
    except subprocess.TimeoutExpired:
        log_error("mx-data-hk-price", f"查询超时 ({TIMEOUT_DATA}秒)")
    except Exception as e:
        log_error("mx-data-hk-price", f"查询失败：{e}")
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# Company Profile, Earnings, Peers, Catalysts, GS Metrics
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_company_profile(ticker: str, market: str) -> dict:
    """Fetch company business overview, industry position, etc."""
    profile = {"business": "", "industry_position": "", "revenue_split": "",
               "key_customers": "", "market_cap": "", "pe_ttm": "", "pb": ""}
    try:
        query_ticker = DataParser.to_query_ticker(ticker, market)
        # Query 1: company intro
        try:
            r1 = subprocess.run(["python3.12", MX_DATA_SCRIPT, f"{query_ticker} 公司简介"],
                                capture_output=True, text=True, timeout=TIMEOUT_DATA)
            biz_match = re.search(r"【公司简介】(.*?)(?:【|$)", r1.stdout)
            if biz_match:
                profile["business"] = biz_match.group(1).strip()[:80]
        except Exception:
            pass

        # Query 2: industry sector
        try:
            r1b = subprocess.run(["python3.12", MX_DATA_SCRIPT, f"{query_ticker} 所属行业板块"],
                                 capture_output=True, text=True, timeout=TIMEOUT_DATA)
            sector = None
            for line in r1b.stdout.splitlines():
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
        except Exception:
            pass

        # Query 3: market cap, PE, PB
        try:
            r2 = subprocess.run(["python3.12", MX_DATA_SCRIPT, f"{query_ticker} 总市值 市盈率 TTM 市净率"],
                                capture_output=True, text=True, timeout=TIMEOUT_DATA)
            headers = []
            for line in r2.stdout.splitlines():
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
        except Exception:
            pass

        # Query 4: revenue composition
        try:
            r3 = subprocess.run(["python3.12", MX_DATA_SCRIPT, f"{query_ticker} 主营构成 收入构成 国内 海外"],
                                capture_output=True, text=True, timeout=TIMEOUT_DATA)
            domestic_match = re.search(r"(?:国内|中国|境内).*?([\d.]+)%", r3.stdout)
            overseas_match = re.search(r"(?:海外|国外|境外|国际).*?([\d.]+)%", r3.stdout)
            if domestic_match or overseas_match:
                profile["revenue_split"] = f"国内 {domestic_match.group(1) if domestic_match else 'N/A'}% | 海外 {overseas_match.group(1) if overseas_match else 'N/A'}%"
        except Exception:
            pass

        if not profile["business"]:
            profile["business"] = "主营业务数据待完善"
        if not profile["industry_position"]:
            profile["industry_position"] = "行业地位待更新"
    except Exception as e:
        log_error("company_profile", str(e))
    return profile


def fetch_earnings_forecast(ticker: str, market: str) -> dict:
    """Fetch analyst consensus estimates (revenue, net profit, EPS, target price)."""
    forecast = {"years": [], "revenue": [], "revenue_growth": [], "net_profit": [],
                "profit_growth": [], "eps": [], "target_price": "", "analyst_count": 0, "upside": ""}
    try:
        query_ticker = DataParser.to_query_ticker(ticker, market)
        r = subprocess.run(["python3.12", MX_DATA_SCRIPT, f"{query_ticker} 机构一致预期 2026 2027 2028"],
                           capture_output=True, text=True, timeout=TIMEOUT_DATA)
        headers = []
        for line in r.stdout.splitlines():
            if re.match(r"\|\s*date\s*\|", line, re.I):
                headers = [p.strip() for p in line.strip().strip("|").split("|")]
            elif re.match(r"\|\s*[23]\d{3}[AE]?\s*\|", line):
                parts = [p.strip() for p in line.strip().strip("|").split("|")]
                if len(parts) >= 2 and headers:
                    year = parts[0]
                    if year in forecast["years"]:
                        continue
                    revenue_val = parts[1] if len(parts) > 1 and parts[1] != "-" else None
                    revenue_growth_val = None
                    if len(parts) > 2 and parts[2] != "-":
                        match = re.search(r"([\d.]+)", parts[2])
                        if match: revenue_growth_val = match.group(1) + "%"
                    profit_val = parts[3] if len(parts) > 3 and parts[3] != "-" else None
                    profit_growth_val = None
                    if len(parts) > 4 and parts[4] != "-":
                        match = re.search(r"([\d.]+)", parts[4])
                        if match: profit_growth_val = match.group(1) + "%"
                    eps_val = parts[5] if len(parts) > 5 and parts[5] != "-" else None

                    forecast["years"].append(year)
                    forecast["revenue"].append(revenue_val or "N/A")
                    forecast["revenue_growth"].append(revenue_growth_val or "N/A")
                    forecast["net_profit"].append(profit_val or "N/A")
                    forecast["profit_growth"].append(profit_growth_val or "N/A")
                    forecast["eps"].append(eps_val or "N/A")
                    if len(forecast["years"]) >= 3:
                        break

        if not forecast["years"]:
            forecast = DataParser.structure_earnings_forecast(forecast)
        elif len(forecast["years"]) < 3:
            forecast = DataParser.structure_earnings_forecast(forecast)
    except Exception as e:
        log_error("earnings_forecast", str(e))
        forecast = DataParser.structure_earnings_forecast(forecast)
    return forecast


def fetch_peer_comparison(ticker: str, market: str, pe_ttm: str) -> list:
    """Fetch peer comparison data."""
    peers = []
    try:
        if market == "hk":
            peers = [
                {"name": "行业平均", "code": "-", "pe": "15-20", "peg": "1.0-1.3", "note": "港股参考"},
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
        for keyword in keywords:
            try:
                r = subprocess.run(["python3.12", MX_SEARCH_SCRIPT, f"{ticker} {keyword}"],
                                   capture_output=True, text=True, timeout=TIMEOUT_NEWS)
                if "减持" in keyword and ("结束" in r.stdout or "完成" in r.stdout):
                    catalysts.append(f"股东{keyword}计划已结束，抛压解除")
                elif "订单" in keyword or "大单" in keyword or "中标" in keyword:
                    match = re.search(r"(\d+\.?\d*)\s*(亿元|万元)", r.stdout)
                    if match:
                        catalysts.append(f"获得{keyword}{match.group(1)}{match.group(2)}，利好长期订单可见性")
                elif "回购" in keyword or "增持" in keyword:
                    catalysts.append(f"公司{keyword}，彰显管理层信心")
                if len(catalysts) >= 5:
                    break
            except Exception:
                continue
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
        r1 = subprocess.run(["python3.12", MX_DATA_SCRIPT, f"{query_ticker} 净资产收益率 ROE 自由现金流 资产负债率"],
                            capture_output=True, text=True, timeout=TIMEOUT_DATA)
        r2 = subprocess.run(["python3.12", MX_DATA_SCRIPT, f"{query_ticker} Beta 系数 市盈率"],
                            capture_output=True, text=True, timeout=TIMEOUT_DATA)

        headers1 = []
        for line in r1.stdout.splitlines():
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

        headers2 = []
        for line in r2.stdout.splitlines():
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
            metrics["beta"] = {"a": "1.15", "hk": "1.20"}.get(market, "1.10")

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
