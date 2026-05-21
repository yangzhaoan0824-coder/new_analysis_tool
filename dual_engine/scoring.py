"""
Scoring module - All financial/scoring calculations using Decimal precision.

Per Refactor_Spec:
    引入 decimal 库进行核心指标计算，禁用 float 进行财务/评分运算

Key functions preserved from original with Decimal upgrade:
    - calculate_peg()          → PEG valuation with Decimal
    - calc_confidence()        → Confidence score (0-100)
    - get_decision()           → Top-down decision matrix
    - determine_rating()       → Investment rating
    - _calc_fundamental_scores() → Dynamic fundamental scoring
    - weekly_signal()          → Weekly/daily signal fusion
"""

import re
from decimal import Decimal, InvalidOperation, getcontext, ROUND_HALF_UP
from typing import Optional

from dual_engine.utils import parse_num, decimal_round, decimal_to_str
from dual_engine.exceptions import AnalysisError

getcontext().prec = 28


# ═══════════════════════════════════════════════════════════════════════════════
# PEG Valuation (Decimal precision)
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_peg(pe_ttm: str, profit_growth: list) -> dict:
    """Calculate PEG indicator with investment bank standard valuation judgment.

    Uses Decimal for all arithmetic to eliminate floating-point errors.

    PEG = PE / earnings growth rate
    Investment bank standards:
        PEG < 0.5: Significantly undervalued (Strong Buy)
        PEG 0.5-1.0: Undervalued (Buy)
        PEG 1.0-1.5: Fair value (Hold)
        PEG 1.5-2.0: Overvalued (Reduce)
        PEG > 2.0: Significantly overvalued (Sell)
    """
    result = {
        "peg": None,
        "peg_str": "N/A",
        "valuation": "N/A",
        "rating": "",
        "icon": "⚪",
        "description": ""
    }

    try:
        # Extract PE value
        pe_val = None
        if pe_ttm and pe_ttm != "N/A":
            match = re.search(r"([\d.]+)", pe_ttm)
            if match:
                pe_val = Decimal(match.group(1))

        # Extract average growth rate from forecast
        growth_rate = None
        valid_growth = []
        for g in profit_growth[:3]:
            if g and g != "N/A":
                match = re.search(r"([\d.]+)", str(g))
                if match:
                    valid_growth.append(Decimal(match.group(1)))

        if valid_growth:
            growth_rate = sum(valid_growth) / Decimal(len(valid_growth))

        # Calculate PEG using Decimal
        if pe_val and growth_rate and growth_rate > 0:
            peg = pe_val / growth_rate
            peg_rounded = decimal_round(peg, 2)
            result["peg"] = float(peg_rounded)
            result["peg_str"] = str(peg_rounded)

            # Investment bank valuation judgment
            if peg < Decimal("0.5"):
                result["valuation"] = "显著低估"
                result["rating"] = "强烈买入"
                result["icon"] = "🟢"
                result["description"] = f"PEG<{peg_rounded}，股价显著低于内在价值，安全边际高"
            elif peg < Decimal("1.0"):
                result["valuation"] = "低估"
                result["rating"] = "买入"
                result["icon"] = "🔵"
                result["description"] = f"PEG={peg_rounded}，股价低于内在价值，具备投资价值"
            elif peg < Decimal("1.5"):
                result["valuation"] = "合理估值"
                result["rating"] = "持有"
                result["icon"] = "🟡"
                result["description"] = f"PEG={peg_rounded}，股价与内在价值匹配，估值合理"
            elif peg < Decimal("2.0"):
                result["valuation"] = "高估"
                result["rating"] = "减持"
                result["icon"] = "🟠"
                result["description"] = f"PEG={peg_rounded}，股价高于内在价值，建议逢高减仓"
            else:
                result["valuation"] = "显著高估"
                result["rating"] = "卖出"
                result["icon"] = "🔴"
                result["description"] = f"PEG>{peg_rounded}，股价显著高于内在价值，泡沫风险大"
        else:
            result["peg_str"] = "N/A"
            result["description"] = "无法计算（PE 或增长率数据缺失）"

    except Exception as e:
        result["peg_str"] = "N/A"
        result["description"] = "计算失败"

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Confidence Calculation
# ═══════════════════════════════════════════════════════════════════════════════

def calc_confidence(news_text: str, analyst_target: str, weekly_text: str,
                    macro_available: bool, daily_signal: str) -> tuple[int, str]:
    """Calculate confidence score (0-100), returns (score, detail_string)."""
    score = 20
    items = []

    if news_text and len(news_text) > 100:
        score += 25
        items.append("消息面✅+25")
    else:
        items.append("消息面❌(无新闻)")

    if analyst_target and analyst_target != "N/A":
        score += 20
        items.append("机构目标价✅+20")
    else:
        items.append("机构目标价❌(无数据)")

    is_weekly_bull = "多头" in weekly_text
    is_daily_buy = "买入" in daily_signal or "BUY" in daily_signal.upper()
    is_daily_sell = "卖出" in daily_signal or "SELL" in daily_signal.upper()
    if (is_daily_buy and is_weekly_bull) or (is_daily_sell and not is_weekly_bull):
        score += 20
        items.append("周线日线一致✅+20")
    else:
        items.append("周线日线分歧❌(方向相反)")

    if macro_available:
        score += 15
        items.append("宏观数据✅+15")
    else:
        items.append("宏观数据❌(不可用)")

    return min(100, score), " | ".join(items)


# ═══════════════════════════════════════════════════════════════════════════════
# Decision Matrix
# ═══════════════════════════════════════════════════════════════════════════════

def get_decision(macro_total: int, tech_score: int,
                 weekly_aligned: bool = True, rr_ratio: Optional[float] = None) -> str:
    """Top-down decision matrix with weekly divergence veto + RR veto."""
    if not weekly_aligned:
        return f"周线空头否决 ⚠️（日线/周线分歧）→ ⚪ 观望，等待周线转多"
    if rr_ratio is not None and rr_ratio < 2.0:
        return f"RR否决 ⚠️（风险收益比 {rr_ratio:.2f}:1 < 2:1）→ ⚪ 观望，等待更好买点"

    if macro_total >= 75:
        env = "三层共振利好 🟢"
        threshold = 60
    elif macro_total >= 55:
        env = "环境友好 ✅"
        threshold = 70
    elif macro_total >= 35:
        env = "环境中性 ⚪"
        threshold = 80
    else:
        return f"环境恶劣 🔴（基本面-消息面分 {macro_total}/100），建议观望"

    if tech_score >= threshold:
        return f"{env}（{macro_total}/100）+ 技术分 {tech_score}≥{threshold} → 🟢 可以操作"
    else:
        return f"{env}（{macro_total}/100）+ 技术分 {tech_score}<{threshold} → ⚪ 观望等待技术信号"


# ═══════════════════════════════════════════════════════════════════════════════
# Rating Determination (Decimal precision)
# ═══════════════════════════════════════════════════════════════════════════════

def determine_rating(sentiment_score: int, macro_score, rr, weekly_text: str) -> tuple:
    """Determine investment rating based on composite score.

    Uses Decimal for score calculation to avoid float precision errors.
    """
    base_score = Decimal(str(sentiment_score))

    # Macro adjustment
    if macro_score:
        macro_total = Decimal(str(getattr(macro_score, "total", 0)))
        base_score = base_score * Decimal("0.6") + macro_total * Decimal("0.4")

    # RR adjustment
    if rr is not None:
        rr_val = Decimal(str(rr))
        if rr_val < Decimal("2"):
            base_score -= Decimal("10")

    # Weekly divergence adjustment
    if weekly_text and "⚠️" in weekly_text:
        base_score -= Decimal("15")

    # Determine rating
    score_float = float(base_score)
    if score_float >= 80:
        return "买入", "🟢"
    elif score_float >= 65:
        return "增持", "🔵"
    elif score_float >= 50:
        return "持有", "🟡"
    elif score_float >= 35:
        return "减持", "🟠"
    else:
        return "卖出", "🔴"


# ═══════════════════════════════════════════════════════════════════════════════
# Weekly Signal Fusion
# ═══════════════════════════════════════════════════════════════════════════════

def weekly_signal(weekly_text: str, daily_signal: str) -> str:
    """Fusion of weekly trend and daily signal."""
    is_weekly_bull = "多头" in weekly_text
    is_daily_buy = "买入" in daily_signal or "BUY" in daily_signal.upper()
    is_daily_sell = "卖出" in daily_signal or "SELL" in daily_signal.upper()

    if is_daily_buy and is_weekly_bull:
        return "✅ 日线买入 + 周线多头，信号可信，正常操作"
    elif is_daily_buy and not is_weekly_bull:
        return "⚠️ 日线买入 + 周线空头，信号存疑，降低仓位或等待"
    elif is_daily_sell and not is_weekly_bull:
        return "✅ 日线卖出 + 周线空头，信号可信，正常操作"
    elif is_daily_sell and is_weekly_bull:
        return "⚠️ 日线卖出 + 周线多头，可能短期回调，谨慎减仓"
    else:
        return "⚪ 观望信号，维持观望"


# ═══════════════════════════════════════════════════════════════════════════════
# Dynamic Fundamental Scoring (Decimal precision)
# ═══════════════════════════════════════════════════════════════════════════════

def calc_fundamental_scores(earnings_forecast, gs_metrics, mx_fin,
                            company_profile, news_text, analyst_target,
                            catalysts_list) -> dict:
    """Dynamic fundamental scoring based on actual data (total 100).

    Uses Decimal for all score calculations to eliminate precision errors.
    Scoring logic is mathematically identical to the original _calc_fundamental_scores.
    """
    scores = {}

    # ── Extract data ──
    roe_val = parse_num(gs_metrics.get("roe", "N/A"))
    if roe_val == 0 and mx_fin:
        roe_val = parse_num(mx_fin.get("roe"))

    gross_margin = parse_num(mx_fin.get("gross_margin") if mx_fin else None)
    if gross_margin == 0 and earnings_forecast:
        gross_margin = parse_num(earnings_forecast.get("gross_margin"))

    profit_growth = Decimal("0")
    if earnings_forecast:
        pg = earnings_forecast.get("profit_growth", [])
        if isinstance(pg, list) and pg:
            profit_growth = parse_num(pg[0])

    revenue = Decimal("0")
    if earnings_forecast:
        rev = earnings_forecast.get("revenue", [])
        if isinstance(rev, list) and rev:
            revenue = parse_num(rev[0])
    if revenue == 0 and mx_fin:
        revenue = parse_num(mx_fin.get("revenue"))

    debt_ratio = parse_num(gs_metrics.get("debt_ratio", "N/A"))
    if debt_ratio == 0 and mx_fin:
        debt_ratio = parse_num(mx_fin.get("debt_ratio"))

    net_debt_ebitda = parse_num(gs_metrics.get("net_debt_ebitda", "N/A"))
    is_net_cash = net_debt_ebitda < 0 if net_debt_ebitda else False

    catalyst_count = len(catalysts_list) if catalysts_list else 0
    has_analyst = analyst_target and analyst_target != "N/A" and "N/A" not in str(analyst_target)
    news_positive = "利好" in (news_text or "") or "增持" in (news_text or "") or "买入" in (news_text or "")

    # ── 1. Financial Quality (max 25) ──
    fin = 0
    if roe_val >= 15: fin += 8
    elif roe_val >= 10: fin += 6
    elif roe_val >= 5: fin += 3
    elif roe_val > 0: fin += 1
    if gross_margin >= 30: fin += 5
    elif gross_margin >= 20: fin += 3
    elif gross_margin > 0: fin += 1
    if profit_growth >= 30: fin += 5
    elif profit_growth >= 15: fin += 3
    elif profit_growth > 0: fin += 1
    if debt_ratio > 0:
        if debt_ratio <= 30: fin += 4
        elif debt_ratio <= 50: fin += 3
        elif debt_ratio <= 70: fin += 1
    else:
        fin += 2
    if is_net_cash: fin += 3
    scores["财务质量"] = min(fin, 25)

    _fin_detail = []
    if roe_val: _fin_detail.append(f"ROE{float(roe_val):.1f}%")
    if gross_margin: _fin_detail.append(f"毛利率{float(gross_margin):.0f}%")
    if profit_growth: _fin_detail.append(f"利润增速{float(profit_growth):+.0f}%")
    if debt_ratio: _fin_detail.append(f"负债率{float(debt_ratio):.0f}%")
    if is_net_cash: _fin_detail.append("净现金+3")
    fin_detail = "，".join(_fin_detail) if _fin_detail else f"得分{fin}/25"

    # ── 2. Business Model (max 25) ──
    biz = 10
    if revenue >= 1000: biz += 5
    elif revenue >= 300: biz += 4
    elif revenue >= 100: biz += 3
    elif revenue > 0: biz += 2
    if profit_growth >= 20: biz += 5
    elif profit_growth >= 10: biz += 3
    elif profit_growth > 0: biz += 1
    if company_profile:
        pos = str(company_profile.get("industry_position", "")).lower()
        if "第一" in pos or "龙头" in pos or ">40" in pos:
            biz += 5
        elif "前列" in pos or "领先" in pos:
            biz += 3
    scores["商业模式"] = min(biz, 25)

    biz_detail_parts = ["基础分10"]
    if revenue: biz_detail_parts.append(f"营收{float(revenue):.0f}亿(+{min(biz-10,5)})")
    if profit_growth: biz_detail_parts.append(f"增速{float(profit_growth):+.0f}%(+{min(5 if profit_growth >= 20 else 3 if profit_growth >= 10 else 1, 5)})")
    if company_profile:
        pos = str(company_profile.get("industry_position", "")).lower()
        if "第一" in pos or "龙头" in pos or ">40" in pos:
            biz_detail_parts.append("行业龙头+5")
    biz_detail = "+".join(biz_detail_parts)

    # ── 3. Moat (max 25) ──
    moat = 8
    if roe_val >= 15: moat += 8
    elif roe_val >= 10: moat += 5
    elif roe_val > 0: moat += 2
    if gross_margin >= 35: moat += 5
    elif gross_margin >= 25: moat += 3
    elif gross_margin > 0: moat += 1
    if revenue >= 500: moat += 4
    elif revenue >= 100: moat += 2
    scores["护城河"] = min(moat, 25)

    moat_detail_parts = ["基础分8"]
    if roe_val: moat_detail_parts.append(f"ROE{float(roe_val):.1f}%(+{min(8 if roe_val>=15 else 5 if roe_val>=10 else 2,8)})")
    if gross_margin: moat_detail_parts.append(f"毛利率{float(gross_margin):.0f}%(+{min(5 if gross_margin>=35 else 3 if gross_margin>=25 else 1,5)})")
    if revenue: moat_detail_parts.append(f"营收{float(revenue):.0f}亿(+{min(4 if revenue>=500 else 2,4)})")
    moat_detail = "+".join(moat_detail_parts)

    # ── 4. Management (max 10) ──
    mgmt = 5
    if profit_growth >= 15: mgmt += 3
    elif profit_growth > 0: mgmt += 1
    if "回购" in (news_text or "") or "增持" in (news_text or ""):
        mgmt += 2
    scores["管理层"] = min(mgmt, 10)

    mgmt_detail_parts = ["基础分5"]
    if profit_growth: mgmt_detail_parts.append(f"利润增速{float(profit_growth):+.0f}%(+{min(3 if profit_growth >= 15 else 1, 3)})")
    if "回购" in (news_text or "") or "增持" in (news_text or ""):
        mgmt_detail_parts.append("回购/增持+2")
    mgmt_detail = "+".join(mgmt_detail_parts)

    # ── 5. News Sentiment (max 15) ──
    news = 4
    if news_positive: news += 4
    if catalyst_count >= 3: news += 4
    elif catalyst_count >= 1: news += 2
    if has_analyst: news += 3
    scores["个股消息面"] = min(news, 15)

    news_detail_parts = ["基础分4"]
    if news_positive: news_detail_parts.append("新闻正面+4")
    if catalyst_count: news_detail_parts.append(f"催化剂{catalyst_count}个(+{min(4 if catalyst_count >= 3 else 2, 4)})")
    if has_analyst: news_detail_parts.append("机构评级+3")
    news_detail = "+".join(news_detail_parts)

    # ── Total ──
    total = sum(scores.values())
    scores["基本面合计"] = total

    # ── Detail ──
    scores["_detail"] = {
        "财务质量": fin_detail,
        "商业模式": biz_detail,
        "护城河": moat_detail,
        "管理层": mgmt_detail,
        "个股消息面": news_detail,
    }

    # ── Status labels ──
    def _status(s, m):
        pct = s / m * 100 if m else 0
        if pct >= 80: return "✅ 优秀"
        elif pct >= 60: return "✅ 良好"
        elif pct >= 40: return "⚪ 中性"
        else: return "⚠️ 偏弱"

    scores["_status"] = {
        "商业模式": _status(scores["商业模式"], 25),
        "护城河": _status(scores["护城河"], 25),
        "财务质量": _status(scores["财务质量"], 25),
        "管理层": _status(scores["管理层"], 10),
        "个股消息面": _status(scores["个股消息面"], 15),
        "基本面合计": _status(total, 100),
    }

    return scores


# ═══════════════════════════════════════════════════════════════════════════════
# Investment Thesis Generation
# ═══════════════════════════════════════════════════════════════════════════════

def generate_investment_thesis(ticker: str, sentiment_score: int, macro_score,
                               peg_result: dict, rr, weekly_conclusion: str) -> str:
    """Generate investment bank style core investment thesis."""
    thesis_parts = []

    if peg_result["peg"] is not None and peg_result["peg"] < 1.0:
        thesis_parts.append(f"PEG ({peg_result['peg_str']}) 显示估值具备吸引力")
    elif peg_result["peg"] is not None and peg_result["peg"] > 2.0:
        thesis_parts.append(f"PEG ({peg_result['peg_str']}) 显示估值偏高，需警惕回调风险")

    if sentiment_score >= 70:
        thesis_parts.append("技术面呈现多头排列，短期动能强劲")
    elif sentiment_score <= 40:
        thesis_parts.append("技术面处于空头趋势，建议等待反转信号")
    else:
        thesis_parts.append("技术指标处于震荡区间，短期缺乏明确方向")

    if macro_score and getattr(macro_score, "total", 0) >= 60:
        thesis_parts.append("基本面稳健，宏观环境 supportive")
    elif macro_score and getattr(macro_score, "total", 0) < 40:
        thesis_parts.append("基本面承压，需关注下行风险")

    rr_val = float(rr) if rr is not None else None
    if rr_val is not None and rr_val >= 3:
        thesis_parts.append(f"风险收益比 ({rr_val:.1f}:1) 优异，安全边际充足")
    elif rr_val is not None and rr_val < 2:
        thesis_parts.append(f"风险收益比 ({rr_val:.1f}:1) 不足，建议等待更好买点")

    if "⚠️" in weekly_conclusion:
        thesis_parts.append("周线/日线存在分歧，需谨慎对待")

    return "；".join(thesis_parts[:3])


# ═══════════════════════════════════════════════════════════════════════════════
# Scenario Analysis (Decimal precision)
# ═══════════════════════════════════════════════════════════════════════════════

def generate_scenario_analysis(current_price, target_price, currency: str = "元") -> list:
    """Generate bull/base/bear scenario analysis using Decimal."""
    try:
        cp = Decimal(str(current_price))
        tp = Decimal(str(target_price))
        bull_target = tp * Decimal("1.15")
        base_target = tp
        bear_target = tp * Decimal("0.75")

        scenarios = [
            {"name": "乐观", "prob": "30%", "target": f"{float(bull_target):.1f}{currency}",
             "return": f"+{float((bull_target/cp - 1)*100):.0f}%"},
            {"name": "基准", "prob": "50%", "target": f"{float(base_target):.1f}{currency}",
             "return": f"+{float((base_target/cp - 1)*100):.0f}%"},
            {"name": "悲观", "prob": "20%", "target": f"{float(bear_target):.1f}{currency}",
             "return": f"{float((bear_target/cp - 1)*100):.0f}%"},
        ]
        return scenarios
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# Risk Matrix
# ═══════════════════════════════════════════════════════════════════════════════

def generate_risk_matrix(ticker: str, market: str) -> list:
    """Generate systematic risk matrix."""
    return [
        {"type": "行业竞争加剧", "prob": "中", "impact": "中", "desc": "新进入者可能压缩毛利率"},
        {"type": "原材料价格波动", "prob": "高", "impact": "中", "desc": "铜、铝等原材料涨价影响成本"},
        {"type": "下游需求放缓", "prob": "中", "impact": "高", "desc": "汽车/家电行业增速放缓"},
        {"type": "汇率波动", "prob": "低", "impact": "低", "desc": "出口业务受汇率影响"},
    ]
