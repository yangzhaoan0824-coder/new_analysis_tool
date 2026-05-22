"""
EngineProcessor - Core dual-engine cross-validation logic.

Per Refactor_Spec:
    EngineProcessor: 负责双引擎的核心交叉逻辑

Engine1 = daily_stock_analysis (all markets)
Engine2 = trading-agents (US only)

This module orchestrates the two engines and computes composite metrics
using Decimal precision for all financial calculations.
"""

import os
import re
import time
from decimal import Decimal, getcontext
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from dual_engine.constants import VERSION, ENGINE_ID, TRADING_AGENTS_DIR
from dual_engine.exceptions import AnalysisError
from dual_engine.utils import (
    log_error, clear_error_log, detect_market, parse_num, parse_num_float,
    decimal_round, decimal_to_str, _load_zshrc_env, ERROR_LOG
)
from dual_engine.data_parser import DataParser
from dual_engine.scoring import (
    calculate_peg, calc_confidence, get_decision, determine_rating,
    weekly_signal, calc_fundamental_scores, generate_investment_thesis,
    generate_scenario_analysis, generate_risk_matrix,
)
from dual_engine.fetchers import (
    fetch_analyst_target, fetch_analyst_rating, fetch_news_via_mx_search, run_daily_analysis,
    run_trading_agents, fetch_financial_from_mx, enrich_earnings_from_mx,
    run_weekly_check, fetch_hk_price_from_mx, fetch_a_price_from_mx,
    fetch_company_profile, fetch_earnings_forecast, fetch_peer_comparison,
    fetch_catalysts, fetch_gs_financial_metrics, fetch_revenue_composition,
    save_to_investment_db, save_to_notion,
)

getcontext().prec = 28


class EngineProcessor:
    """Orchestrates dual-engine analysis and computes composite metrics.

    Attributes:
        ticker: Stock ticker symbol
        market: Detected market ('hk', 'a', 'us')
        error_log: List of errors encountered during processing
    """

    def __init__(self, ticker: str):
        self.ticker = ticker.strip()
        self.market = detect_market(self.ticker)
        self.error_log: list[str] = []
        self._results: dict = {}

    def process(self) -> dict:
        """Run the full dual-engine analysis pipeline.

        Returns:
            dict with all analysis data needed for report generation.
        """
        clear_error_log()
        start_time = time.time()
        market_label = {"a": "A 股", "hk": "港股", "us": "美股"}[self.market]

        # ═══ Engine 1: daily_stock_analysis (serial, others depend on it) ═══
        print(f"\n🔍 [{self.ticker}] {market_label} | Step 1/5: daily_stock_analysis...")
        step1_start = time.time()
        r = run_daily_analysis(self.ticker)

        # HK price correction
        hk_price_data = None
        a_price_data = None
        if self.market == "hk":
            print(f"   🔄 港股检测：使用 mx-data 获取实时价格...")
            hk_price_data = fetch_hk_price_from_mx(self.ticker)
            if hk_price_data and hk_price_data.get('price') and r and hasattr(r, 'dashboard'):
                real_price = hk_price_data['price']
                try:
                    d = r.dashboard if isinstance(r.dashboard, dict) else {}
                    bp = d.get("battle_plan", {})
                    sp = bp.get("sniper_points", {}) if isinstance(bp, dict) else {}
                    sp['ideal_buy'] = round(real_price * 0.98, 2)
                    sp['stop_loss'] = round(real_price * 0.95, 2)
                    sp['take_profit'] = round(real_price * 1.05, 2)
                    cc = d.get("core_conclusion", {})
                    cc['one_sentence'] = f'当前价{real_price}港元，实时数据'
                    cc['time_sensitivity'] = '实时'
                    if hk_price_data.get('change'):
                        cc['one_sentence'] += f'，涨跌幅{hk_price_data["change"]}%'
                    print(f"   ✅ 已使用 mx-data 实时价格修正：{real_price} 港元")
                except Exception as e:
                    log_error("hk-price-correction", f"修正失败：{e}")

        # A-share price correction
        elif self.market == "a":
            print(f"   🔄 A股检测：使用 mx-data 获取实时价格...")
            a_price_data = fetch_a_price_from_mx(self.ticker)
            if a_price_data and a_price_data.get('price') and r and hasattr(r, 'dashboard'):
                real_price = a_price_data['price']
                try:
                    d = r.dashboard if isinstance(r.dashboard, dict) else {}
                    bp = d.get("battle_plan", {})
                    sp = bp.get("sniper_points", {}) if isinstance(bp, dict) else {}
                    sp['ideal_buy'] = round(real_price * 0.98, 2)
                    sp['stop_loss'] = round(real_price * 0.95, 2)
                    sp['take_profit'] = round(real_price * 1.05, 2)
                    cc = d.get("core_conclusion", {})
                    cc['one_sentence'] = f'当前价{real_price}元，实时数据'
                    cc['time_sensitivity'] = '实时'
                    if a_price_data.get('change'):
                        cc['one_sentence'] += f'，涨跌幅{a_price_data["change"]}%'
                    print(f"   ✅ 已使用 mx-data 实时价格修正：{real_price} 元")
                except Exception as e:
                    log_error("a-price-correction", f"修正失败：{e}")

        if not r:
            raise AnalysisError("engine1", f"{self.ticker} daily_stock_analysis 失败")

        print(f"   ✅ 情绪分：{r.sentiment_score} | 建议：{r.operation_advice} ({time.time()-step1_start:.1f}秒)")

        # ═══ Engine 2 + parallel data queries ═══
        print(f"   Step 2/5: 并行执行数据查询...")
        parallel_start = time.time()

        mx_financial_data = fetch_financial_from_mx(self.ticker, self.market)

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(fetch_news_via_mx_search, self.ticker, r.name if hasattr(r, 'name') else ""): "news",
                executor.submit(run_weekly_check, self.ticker, self.market): "weekly",
                executor.submit(fetch_company_profile, self.ticker, self.market): "profile",
                executor.submit(fetch_earnings_forecast, self.ticker, self.market): "forecast",
            }
            if self.market == "us":
                futures[executor.submit(run_trading_agents, self.ticker)] = "ta"

            results = {}
            for future in as_completed(futures):
                task_name = futures[future]
                try:
                    results[task_name] = future.result()
                except Exception as e:
                    log_error(task_name, str(e))
                    results[task_name] = None

        news_text = results.get("news", "")
        weekly_text = results.get("weekly", "")
        company_profile = results.get("profile", {})
        earnings_forecast = results.get("forecast", {})
        ta_decision = results.get("ta", None)

        if mx_financial_data:
            earnings_forecast = enrich_earnings_from_mx(earnings_forecast, mx_financial_data, self.ticker)

        print(f"   ✅ 并行任务完成 ({time.time()-parallel_start:.1f}秒)")

        if news_text:
            os.environ["EXTRA_NEWS_CONTEXT"] = news_text

        # ═══ Step 3: Macro scoring ═══
        print(f"   Step 3/5: 宏观 - 行业 - 消息面评分...")
        macro_score = None
        try:
            # Add trading-agents directory to sys.path for macro_scorer import
            import sys as _sys
            _ta_dir = TRADING_AGENTS_DIR
            if _ta_dir not in _sys.path and os.path.isdir(_ta_dir):
                _sys.path.insert(0, _ta_dir)
            from macro_scorer import get_macro_score
            macro_score = get_macro_score(self.ticker, self.market, news_text)
            print(f"   ✅ 基本面 - 消息面分：{macro_score.total}/100")
        except Exception as e:
            log_error("macro_scorer", str(e))

        # ═══ Analyst target ═══
        analyst_target = fetch_analyst_target(self.ticker, self.market)
        consensus_rating = ""
        tp_from_analyst_target = None

        # Extract target price number from analyst_target string (e.g. "目标价均值 35.52港元")
        if analyst_target and "目标价" in analyst_target:
            tp_match = re.search(r"目标价[^\d]*(\d+\.?\d*)", analyst_target)
            if tp_match:
                tp_from_analyst_target = float(tp_match.group(1))

        if mx_financial_data:
            tp = mx_financial_data.get("target_price")
            if tp:
                rating = mx_financial_data.get("consensus_rating", "")
                upside = mx_financial_data.get("upside")
                analyst_target = f"目标价 {tp} 评级{rating} 上涨空间{upside}%" if rating else f"目标价 {tp}"
                consensus_rating = f"评级 {rating}" if rating else ""

        # If analyst_target has target price but no upside yet, compute it
        if analyst_target and "目标价" in analyst_target and "上涨空间" not in analyst_target:
            tp_val = tp_from_analyst_target
            cur_price = None
            if self.market == "hk" and hk_price_data and hk_price_data.get('price'):
                cur_price = hk_price_data['price']
            elif self.market == "a" and a_price_data and a_price_data.get('price'):
                cur_price = a_price_data['price']
            if tp_val and cur_price:
                upside_pct = ((tp_val - cur_price) / cur_price) * 100
                analyst_target += f" 上涨空间{upside_pct:+.1f}%"

        # ═══ Analyst rating + target price (mx-data, mx-search fallback) ═══
        if not consensus_rating or not analyst_target or "目标价" not in analyst_target:
            rating_result = fetch_analyst_rating(self.ticker, self.market)
            if rating_result["rating"] and not consensus_rating:
                consensus_rating = f"评级 {rating_result['rating']}"
            if not analyst_target or "目标价" not in analyst_target:
                if rating_result.get("_target_price"):
                    tp = rating_result["_target_price"]
                    currency = "元" if self.market != "hk" else "港元"
                    rtg = rating_result.get("rating", consensus_rating.replace("评级 ", "") if consensus_rating else "")
                    analyst_target = f"目标价 {tp}{currency} 评级{rtg}"
                    cur_price = None
                    if self.market == "a" and a_price_data and a_price_data.get('price'):
                        cur_price = a_price_data['price']
                    elif self.market == "hk" and hk_price_data and hk_price_data.get('price'):
                        cur_price = hk_price_data['price']
                    if cur_price:
                        upside_pct = ((tp - cur_price) / cur_price) * 100
                        analyst_target += f" 上涨空间{upside_pct:+.1f}%"
                elif rating_result["detail"] and not analyst_target:
                    analyst_target = f"评级{rating_result['detail']}"

        # ═══ Current price ═══
        current_price = None
        try:
            d = r.dashboard if isinstance(r.dashboard, dict) else {}
            bp = d.get("battle_plan", {})
            sp = bp.get("sniper_points", {}) if isinstance(bp, dict) else {}
            buy = sp.get("ideal_buy", 0)
            if buy:
                current_price = float(buy) / 1.02
        except Exception:
            pass
        # Override with mx-data real-time price when available
        if self.market == "hk" and hk_price_data and hk_price_data.get('price'):
            current_price = hk_price_data['price']
        elif self.market == "a" and a_price_data and a_price_data.get('price'):
            current_price = a_price_data['price']

        # ═══ Price change / market cap / 5-day change ═══
        price_change_display = "N/A"
        market_cap_display = "N/A"
        change_5d_display = "N/A"
        if self.market == "hk" and hk_price_data:
            if hk_price_data.get('change') is not None:
                price_change_display = f"{hk_price_data['change']:+.2f}%"
            if hk_price_data.get('market_cap'):
                market_cap_display = str(hk_price_data['market_cap'])
            if hk_price_data.get('change_5d') is not None:
                change_5d_display = f"{hk_price_data['change_5d']:+.2f}%"
        elif self.market == "a" and a_price_data:
            if a_price_data.get('change') is not None:
                price_change_display = f"{a_price_data['change']:+.2f}%"
            if a_price_data.get('market_cap'):
                market_cap_display = str(a_price_data['market_cap'])
            if a_price_data.get('change_5d') is not None:
                change_5d_display = f"{a_price_data['change_5d']:+.2f}%"

        tech_indicators = getattr(r, '_latest_tech_data', None) if r else None

        # ═══ Compute composite metrics (Decimal precision) ═══
        composite_result = self.compute_composite_metrics(
            r, macro_score, weekly_text, analyst_target, current_price,
            company_profile, earnings_forecast, mx_financial_data,
            news_text, ta_decision
        )

        # ═══ Assemble all results ═══
        self._results = {
            "ticker": self.ticker,
            "market": self.market,
            "market_label": market_label,
            "analysis_result": r,
            "ta_decision": ta_decision,
            "weekly_text": weekly_text,
            "news_text": news_text,
            "analyst_target": analyst_target,
            "macro_score": macro_score,
            "company_profile": company_profile,
            "earnings_forecast": earnings_forecast,
            "current_price": current_price,
            "mx_financial_data": mx_financial_data,
            "price_change": price_change_display,
            "market_cap": market_cap_display,
            "change_5d": change_5d_display,
            "consensus_rating": consensus_rating,
            "tech_indicators": tech_indicators,
            "hk_price_data": hk_price_data,
            "a_price_data": a_price_data,
            "composite": composite_result,
            "elapsed_seconds": time.time() - start_time,
        }

        return self._results

    def compute_composite_metrics(self, r, macro_score, weekly_text,
                                   analyst_target, current_price,
                                   company_profile, earnings_forecast,
                                   mx_financial_data, news_text,
                                   ta_decision) -> dict:
        """Compute all composite metrics using Decimal precision.

        This is the core cross-engine logic that fuses engine1 + engine2 data.
        """
        # ── Risk/Reward Ratio ──
        d = r.dashboard if isinstance(r.dashboard, dict) else {}
        cc = d.get("core_conclusion", {})
        bp = d.get("battle_plan", {})
        sp = bp.get("sniper_points", {}) if isinstance(bp, dict) else {}
        buy = sp.get("ideal_buy", "N/A")
        sl = sp.get("stop_loss", "N/A")
        tp = sp.get("take_profit", "N/A")

        try:
            buy_d = Decimal(str(buy))
            sl_d = Decimal(str(sl))
            tp_d = Decimal(str(tp))
            rr = (tp_d - buy_d) / (buy_d - sl_d) if (buy_d - sl_d) != 0 else None
            rr = float(decimal_round(rr, 2)) if rr is not None else None
            rr_str = f"{rr}:1" if rr is not None else "N/A"
        except Exception:
            rr = None
            rr_str = "N/A"

        # ── Analyst target price ──
        target_price_num = None
        try:
            match = re.search(r"(?:均值|目标价)[:\s]*(\d+[\.\d]*)", analyst_target)
            if not match:
                match = re.search(r"目标价[^\d]*(\d+\.?\d*)", analyst_target)
            if match:
                target_price_num = float(match.group(1))
        except Exception:
            pass

        # ── Upside ──
        upside = ""
        if current_price and target_price_num:
            upside_pct = ((target_price_num - current_price) / current_price) * 100
            upside = f"{upside_pct:+.1f}%"

        # ── Rating ──
        rating, rating_icon = determine_rating(r.sentiment_score, macro_score, rr, weekly_text)

        # ── PEG ──
        pe_ttm = company_profile.get("pe_ttm", "") if company_profile else ""
        profit_growth = earnings_forecast.get("profit_growth", []) if earnings_forecast else []
        peg_result = calculate_peg(pe_ttm, profit_growth)

        # ── Weekly signal ──
        weekly_conclusion = weekly_signal(weekly_text, r.operation_advice)

        # ── GS Financial Metrics ──
        gs_metrics = fetch_gs_financial_metrics(self.ticker, self.market)
        if mx_financial_data:
            if gs_metrics.get("roe") == "N/A" and mx_financial_data.get("roe"):
                gs_metrics["roe"] = f"{mx_financial_data['roe']:.1f}%"
            if mx_financial_data.get("eps") and "eps" not in gs_metrics:
                gs_metrics["eps"] = f"${mx_financial_data['eps']:.2f}"
            if gs_metrics.get("debt_ratio") == "N/A" and mx_financial_data.get("debt_ratio"):
                gs_metrics["debt_ratio"] = f"{mx_financial_data['debt_ratio']:.1f}%"
            if mx_financial_data.get("operating_cashflow") and gs_metrics.get("operating_cashflow") == "N/A":
                gs_metrics["operating_cashflow"] = f"{mx_financial_data['operating_cashflow']:.2f}"
            if mx_financial_data.get("forecast_pe_fy1") and "forecast_pe" not in gs_metrics:
                gs_metrics["forecast_pe_fy1"] = f"{mx_financial_data['forecast_pe_fy1']:.1f}"
            if mx_financial_data.get("forecast_peg_fy1") and "forecast_peg" not in gs_metrics:
                gs_metrics["forecast_peg_fy1"] = f"{mx_financial_data['forecast_peg_fy1']:.2f}"

        # Fill forward metrics from earnings_forecast consensus data
        if earnings_forecast:
            if earnings_forecast.get("forecast_pe_fy1", "N/A") != "N/A" and gs_metrics.get("forecast_pe_fy1", "N/A") == "N/A":
                gs_metrics["forecast_pe_fy1"] = earnings_forecast["forecast_pe_fy1"]
            if earnings_forecast.get("forecast_peg_fy1", "N/A") != "N/A" and gs_metrics.get("forecast_peg_fy1", "N/A") == "N/A":
                gs_metrics["forecast_peg_fy1"] = earnings_forecast["forecast_peg_fy1"]
            if earnings_forecast.get("forecast_roe_fy1", "N/A") != "N/A" and gs_metrics.get("forecast_roe_fy1", "N/A") == "N/A":
                gs_metrics["forecast_roe_fy1"] = earnings_forecast["forecast_roe_fy1"]
            # Also fill gs_metrics roe from earnings forecast if gs_metrics roe is N/A
            if gs_metrics.get("roe") == "N/A" and earnings_forecast.get("forecast_roe_fy1", "N/A") != "N/A":
                gs_metrics["roe"] = earnings_forecast["forecast_roe_fy1"]

        # ── Revenue composition ──
        revenue_comp = fetch_revenue_composition(self.ticker, self.market)

        # ── Peer comparison ──
        peers = fetch_peer_comparison(self.ticker, self.market, pe_ttm)

        # Enrich current stock's peer entry with profit growth from earnings
        if earnings_forecast and earnings_forecast.get("profit_growth"):
            latest_growth = None
            for pg in reversed(earnings_forecast["profit_growth"]):
                if pg and pg != "N/A":
                    latest_growth = pg
                    break
            if latest_growth:
                for peer in peers:
                    if peer.get("name") == self.ticker or peer.get("note") == "当前标的":
                        peer["profit_growth"] = latest_growth
                        break

        # ── Catalysts ──
        catalysts_list = fetch_catalysts(self.ticker, self.market)

        # ── Investment thesis ──
        investment_thesis = generate_investment_thesis(
            self.ticker, r.sentiment_score, macro_score, peg_result, rr, weekly_conclusion
        )

        # ── Confidence ──
        confidence, confidence_detail = calc_confidence(
            news_text, analyst_target, weekly_text,
            macro_score.data_available if macro_score else False,
            r.operation_advice
        )

        # ── Fundamental scores ──
        fundamental_scores = calc_fundamental_scores(
            earnings_forecast, gs_metrics, mx_financial_data,
            company_profile, news_text, analyst_target, catalysts_list
        )

        # ── Precision factor (Decimal-based) ──
        # Each error in ERROR_LOG reduces precision by 0.000001
        error_count = len(ERROR_LOG) if ERROR_LOG else 0
        precision_factor = Decimal("1.000000") - Decimal("0.000001") * error_count
        precision_factor = max(precision_factor, Decimal("0.000000"))
        precision_str = f"{precision_factor:.6f}"

        # ── Composite score (Decimal-based) ──
        if macro_score:
            composite_score = (Decimal(str(r.sentiment_score)) * Decimal("0.4") +
                             Decimal(str(macro_score.total)) * Decimal("0.3") +
                             Decimal(str(confidence)) * Decimal("0.3"))
        else:
            composite_score = Decimal(str(r.sentiment_score)) * Decimal("0.6") + Decimal(str(confidence)) * Decimal("0.4")
        composite_score = decimal_round(composite_score, 2)

        return {
            "buy": buy, "sl": sl, "tp": tp,
            "rr": rr, "rr_str": rr_str,
            "target_price_num": target_price_num,
            "upside": upside,
            "rating": rating, "rating_icon": rating_icon,
            "peg_result": peg_result,
            "weekly_conclusion": weekly_conclusion,
            "gs_metrics": gs_metrics,
            "revenue_comp": revenue_comp,
            "peers": peers,
            "catalysts_list": catalysts_list,
            "investment_thesis": investment_thesis,
            "confidence": confidence, "confidence_detail": confidence_detail,
            "analyst_target": analyst_target,
            "consensus_rating": consensus_rating,
            "fundamental_scores": fundamental_scores,
            "precision_factor": precision_str,
            "composite_score": str(composite_score),
            "pe_ttm": pe_ttm,
        }
