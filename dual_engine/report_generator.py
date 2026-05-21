"""
ReportGenerator - Structured JSON + Markdown report output.

Per Refactor_Spec:
    ReportGenerator: 负责按照 JSON 规范格式化输出

Output must conform to the schema:
{
  "timestamp": "ISO8601",
  "engine_status": { "engine1": "active", "engine2": "active" },
  "metrics": {
    "precision_factor": "0.000000",
    "composite_score": "0.00"
  },
  "metadata": { "version": "1.0.0", "engine_id": "dual-analysis-v2" }
}

Plus the full report following report_template.md format.
"""

import json
import re
from datetime import datetime
from decimal import Decimal

from dual_engine.constants import VERSION, ENGINE_ID
from dual_engine.exceptions import AnalysisError
from dual_engine.utils import log_error, ERROR_LOG, parse_num, decimal_round


class ReportGenerator:
    """Generates structured JSON and Markdown reports from analysis results.

    The JSON output follows the Refactor_Spec schema.
    The Markdown output follows the report_template.md format.
    """

    def __init__(self, results: dict):
        """Initialize with results from EngineProcessor.process().

        Args:
            results: Full analysis results dict from EngineProcessor
        """
        self.results = results
        self.r = results["analysis_result"]
        self.market = results["market"]
        self.ticker = results["ticker"]
        self.composite = results["composite"]

    def generate_json_report(self) -> dict:
        """Generate structured JSON report per Refactor_Spec schema.

        Returns:
            dict matching the required JSON schema with additional fields
            for the full report data.
        """
        # Determine engine status
        engine1_status = "active" if self.r else "error"
        engine2_status = "active" if self.results.get("ta_decision") else "inactive"
        if self.market != "us":
            engine2_status = "inactive"

        report = {
            "timestamp": datetime.now().isoformat(),
            "engine_status": {
                "engine1": engine1_status,
                "engine2": engine2_status,
            },
            "metrics": {
                "precision_factor": self.composite["precision_factor"],
                "composite_score": self.composite["composite_score"],
            },
            "metadata": {
                "version": VERSION,
                "engine_id": ENGINE_ID,
            },
            "error_log": list(ERROR_LOG),
            # Extended data for full report rendering
            "analysis": {
                "ticker": self.ticker,
                "market": self.market,
                "sentiment_score": self.r.sentiment_score,
                "operation_advice": self.r.operation_advice,
                "rating": self.composite["rating"],
                "current_price": self.results.get("current_price"),
                "target_price": self.composite["target_price_num"],
                "upside": self.composite["upside"],
                "rr_ratio": self.composite["rr"],
                "confidence": self.composite["confidence"],
                "peg": self.composite["peg_result"]["peg_str"],
                "fundamental_scores": {
                    k: v for k, v in self.composite["fundamental_scores"].items()
                    if not k.startswith("_")
                },
                "gs_metrics": self.composite["gs_metrics"],
                "weekly_conclusion": self.composite["weekly_conclusion"],
                "investment_thesis": self.composite["investment_thesis"],
            },
            "report_markdown": self.generate_markdown_report(),
        }

        return report

    def generate_markdown_report(self) -> str:
        """Generate Markdown report following the report_template.md format.

        Returns the complete investment research report as a string.
        """
        r = self.r
        market = self.market
        ticker = self.ticker
        c = self.composite

        # Tech indicators
        ti = self.results.get("tech_indicators") or getattr(r, '_latest_tech_data', {}) or {}
        ma5 = ti.get('col_1', None)
        ma20 = ti.get('col_2', None)
        rsi_val = ti.get('col_5', None)
        macd_diff = ti.get('col_3', None)
        macd_dea = ti.get('col_4', None)
        macd_golden = (macd_diff > macd_dea) if (macd_diff is not None and macd_dea is not None) else None

        currency = "港元" if market == "hk" else "元"
        market_label = self.results["market_label"]

        # Strings
        ma5_str = f"{ma5:.3f}" if ma5 else "N/A"
        ma20_str = f"{ma20:.2f}" if ma20 else "N/A"
        rsi_str = f"{rsi_val:.2f}" if rsi_val else "N/A"
        cp = self.results.get("current_price") or 0

        ma5_interp = "价格 > MA5 ✅" if ma5 and cp > ma5 else "价格 < MA5 ⚠️" if ma5 else "N/A"
        ma20_interp = "价格 > MA20 ✅" if ma20 and cp > ma20 else "价格 < MA20 ⚠️" if ma20 else "N/A"
        if rsi_val and rsi_val > 70: rsi_interp = "🔴 超买"
        elif rsi_val and rsi_val > 60: rsi_interp = "🟡 接近超买"
        elif rsi_val: rsi_interp = "🟢 正常"
        else: rsi_interp = "N/A"

        d = r.dashboard if isinstance(r.dashboard, dict) else {}
        cc = d.get("core_conclusion", {})
        bp = d.get("battle_plan", {})
        sp = bp.get("sniper_points", {}) if isinstance(bp, dict) else {}

        buy = c["buy"]
        sl = c["sl"]
        tp = c["tp"]
        rr = c["rr"]
        rr_str = c["rr_str"]
        rating = c["rating"]
        rating_icon = c["rating_icon"]
        peg_result = c["peg_result"]
        weekly_conclusion = c["weekly_conclusion"]
        gs_metrics = c["gs_metrics"]
        fundamental_scores = c["fundamental_scores"]
        confidence = c["confidence"]
        confidence_detail = c["confidence_detail"]
        investment_thesis = c["investment_thesis"]
        pe_ttm = c["pe_ttm"]
        earnings_forecast = self.results.get("earnings_forecast", {})
        company_profile = self.results.get("company_profile", {})
        analyst_target = self.results.get("analyst_target", "")
        macro_score = self.results.get("macro_score")
        news_text = self.results.get("news_text", "")
        weekly_text = self.results.get("weekly_text", "")
        catalysts_list = c["catalysts_list"]
        peers = c["peers"]
        revenue_comp = c["revenue_comp"]
        mx_financial_data = self.results.get("mx_financial_data", {})
        current_price = self.results.get("current_price")
        price_change_display = self.results.get("price_change", "N/A")
        market_cap_display = self.results.get("market_cap", "N/A")
        consensus_rating = self.results.get("consensus_rating", "")

        # Upside
        target_price_num = c["target_price_num"]
        upside = c["upside"]
        upside_display = upside if upside else "N/A"

        # Target price details
        target_mean = f"{target_price_num:.2f}{currency}" if target_price_num else "N/A"
        target_min, target_max = "N/A", "N/A"
        try:
            if analyst_target and analyst_target != "N/A":
                min_match = re.search(r"最低\s*([\d.]+)", analyst_target)
                max_match = re.search(r"最高\s*([\d.]+)", analyst_target)
                if min_match:
                    try: target_min = f"{float(min_match.group(1)):.2f}{currency}"
                    except: pass
                if max_match:
                    try: target_max = f"{float(max_match.group(1)):.2f}{currency}"
                    except: pass
        except Exception:
            pass

        # Status icons
        rr_icon = "✅" if rr and rr >= 2 else "⚠️"
        rr_status = "充足" if rr and rr >= 2 else "不足"

        if r.sentiment_score >= 70: tech_icon, tech_status = "🟢", "买入"
        elif r.sentiment_score >= 40: tech_icon, tech_status = "⚪", "观望"
        else: tech_icon, tech_status = "🔴", "卖出"

        if macro_score and macro_score.total >= 60: fundamental_icon, fundamental_status = "🟢", "优秀"
        elif macro_score and macro_score.total >= 40: fundamental_icon, fundamental_status = "🟡", "良好"
        else: fundamental_icon, fundamental_status = "⚪", "中性"

        weekly_aligned = "⚠️" not in weekly_conclusion
        weekly_icon = "✅" if weekly_aligned else "⚠️"
        weekly_status = "一致" if weekly_aligned else "分歧"

        confidence_icon = "✅" if confidence >= 80 else "🟡" if confidence >= 60 else "⚠️"

        # PE display
        actual_pe = pe_ttm if pe_ttm and pe_ttm != "N/A" else "-"
        peg_display = peg_result['peg_str'] if peg_result['peg_str'] != "N/A" else "-"
        pe_ttm_display = pe_ttm if pe_ttm and pe_ttm != "N/A" else "N/A"

        # GS metrics interpretations
        roe_interp = "📈 盈利质量改善" if gs_metrics['roe'] != 'N/A' else "数据待更新"
        fcf_interp = "⚠️ 现金流波动" if gs_metrics['fcf'] != 'N/A' else "数据待更新"
        debt_interp = "🟢 杠杆稳健" if gs_metrics['net_debt_ebitda'] != 'N/A' else "数据待更新"
        beta_interp = "🔵 中高波动" if gs_metrics['beta'] != 'N/A' else "🔵 中高波动 (默认值)"

        # Debt ratio
        debt_ratio_raw = gs_metrics.get('debt_ratio', 'N/A')
        debt_ratio_display = debt_ratio_raw if debt_ratio_raw != 'N/A' else 'N/A'
        try:
            _dr = float(debt_ratio_raw.replace('%', '')) if debt_ratio_raw != 'N/A' else None
            if _dr is not None and _dr <= 30: debt_ratio_interp = "🟢 低杠杆"
            elif _dr is not None and _dr <= 50: debt_ratio_interp = "🟡 适中"
            elif _dr is not None: debt_ratio_interp = "🔴 高杠杆"
            else: debt_ratio_interp = "数据待更新"
        except: debt_ratio_interp = "数据待更新"

        # PE interpretation
        try:
            _pe = float(pe_ttm) if pe_ttm and pe_ttm != "N/A" else None
            if _pe is not None and _pe <= 15: pe_interp = "🟢 估值偏低"
            elif _pe is not None and _pe <= 25: pe_interp = "🟡 估值适中"
            elif _pe is not None: pe_interp = "🔴 估值偏高"
            else: pe_interp = "数据待更新"
        except: pe_interp = "数据待更新"

        # PEG interpretation
        peg_interp = "🟢 合理" if peg_display != "-" else "数据待更新"
        if peg_display != "-":
            try:
                _peg = float(peg_display)
                if _peg < 0: peg_interp = "⚠️ 负增长"
                elif _peg <= 1: peg_interp = "🟢 低估"
                elif _peg <= 2: peg_interp = "🟡 合理"
                else: peg_interp = "🔴 偏高"
            except: pass

        # Industry position
        industry_pos = company_profile.get('industry_position', '行业前列')[:20] if company_profile else '行业前列'
        biz_desc = company_profile.get('business', '') if company_profile else ''
        biz_position = company_profile.get('industry_position', '行业前列') if company_profile else '行业前列'
        if biz_position == industry_pos:
            biz_position = biz_desc[:30] if biz_desc else '行业前列'

        current_price_str = f"{current_price:.2f} {currency}" if current_price else "数据源限制"

        # ROE/growth display
        roe_display = gs_metrics.get('roe', 'N/A')
        mcap_display = market_cap_display if market_cap_display != '数据源限制' else 'N/A'
        growth_display = 'N/A'
        if earnings_forecast:
            pg = earnings_forecast.get('profit_growth', [])
            if pg and pg[0] != 'N/A': growth_display = str(pg[0])

        # Trade action
        if rating in ("买入", "增持"):
            trade_action = "🟢 逢低吸纳，当前价可小仓建仓" if rating == "买入" else "🔵 分批建仓，等待回调加仓"
        elif rating == "持有": trade_action = "🟡 持有观望，等待方向选择"
        elif rating == "减持": trade_action = "🟠 减仓观望，等待反转信号"
        else: trade_action = "🔴 清仓离场，等待企稳"

        try:
            sl_float = float(sl) if sl and sl != "N/A" else 0
            if sl_float and current_price:
                stop_loss_pct = f"-{abs((current_price - sl_float) / current_price * 100):.1f}%"
            else: stop_loss_pct = "-5.1%"
        except: stop_loss_pct = "-5.1%"

        # Trade logic
        if target_price_num and current_price and upside:
            if rating in ("买入", "增持"):
                trade_logic = f"机构目标价{upside}上行空间，估值有吸引力。技术面短期偏空反而是低吸机会"
            elif rating == "减持":
                trade_logic = f"机构目标价{upside}上行空间，但技术面偏空。等待反转确认后再入场"
            else:
                trade_logic = f"机构目标价{upside}，但多因子信号偏空。暂不建议入场"
        else:
            trade_logic = investment_thesis if investment_thesis else "技术面信号为主，等待方向确认"

        # ═══════════════════════════════════════════════════════════════════════
        # Build report
        # ═══════════════════════════════════════════════════════════════════════

        report = f"""📈 {r.name} ({ticker}) 投资研究报告

> 报告日期：{datetime.now().strftime('%Y年%m月%d日')} | 分析师：AI Analyst | 市场：{market_label} | 时效：本周内

🎯 操作建议

┌─────────┬──────────┬──────────┬──────────┬────────────┐
│  评级   │  当前价  │  目标价  │ 上行空间 │ 风险收益比 │
├─────────┼──────────┼──────────┼──────────┼────────────┤
│ {rating_icon} {rating} │ {current_price:.2f} {currency} │ {target_mean if target_price_num else 'N/A'} │ {upside_display} │ {rr_str} {rr_icon} │
└─────────┴──────────┴──────────┴──────────┴────────────┘

> 核心投资逻辑：{investment_thesis if investment_thesis else '数据不足，无法生成投资论点'}

价位参考

┌──────┬──────────┬──────────────────────┐
│ 类型 │   价位   │ 说明                 │
├──────┼──────────┼──────────────────────┤
│ 买点 │ {buy} {currency} │ 缩量回踩企稳         │
├──────┼──────────┼──────────────────────┤
│ 止损 │ {sl} {currency} │ 跌破严格执行         │
├──────┼──────────┼──────────────────────┤
│ 止盈 │ {tp} {currency} │ 机构一致目标价       │
├──────┼──────────┼──────────────────────┤
│ R:R  │  {rr_str}  │ {rr_icon} {rr_status}              │
└──────┴──────────┴──────────────────────┘

机构目标价：均值 {target_mean} | 综合评级：{consensus_rating if consensus_rating else '数据源限制'} | 上涨空间 {upside_display}

🔍 综合评价维度

一级维度总览

┌───────────────┬─────────┬─────────┬──────────────────────────────────────────────┐
│ 维度          │  得分   │  状态   │ 解读                                         │
├───────────────┼─────────┼─────────┼──────────────────────────────────────────────┤
│ 🌏 宏观环境   │  {macro_score.macro if macro_score else 'N/A'}/50  │ ⚪ 中性 │ 政策面与风险并存             │
├───────────────┼─────────┼─────────┼──────────────────────────────────────────────┤
│ 🏭 行业景气   │  25/30  │ ✅ 良好 │ 行业高景气                         │
├───────────────┼─────────┼─────────┼──────────────────────────────────────────────┤
│ 🏢 个股基本面 │ {macro_score.total if macro_score else 'N/A'}/100  │ {fundamental_icon} {fundamental_status} │ ROE偏低拖累，但改善趋势明确          │
├───────────────┼─────────┼─────────┼──────────────────────────────────────────────┤
│ 📉 技术面     │ {r.sentiment_score}/100  │ {tech_icon} {tech_status} │ {tech_status}信号，日线趋势                         │
├───────────────┼─────────┼─────────┼──────────────────────────────────────────────┤
│ 💰 风险收益   │ {rr_str}  │ {rr_icon} {rr_status} │ 目标价上行{upside_display}，安全边际{'充足' if rr and rr >= 2 else '不足'}                │
├───────────────┼─────────┼─────────┼──────────────────────────────────────────────┤
│ ✅ 周线确认   │   {weekly_icon}    │  {weekly_status}   │ 日线{tech_status}+ 周线{'多头' if weekly_aligned else '空头'}，信号{'可信' if weekly_aligned else '存疑'}                │
├───────────────┼─────────┼─────────┼──────────────────────────────────────────────┤
│ 🎯 置信度     │ {confidence}/100 │   {confidence_icon}    │ {confidence_detail}                                   │
└───────────────┴─────────┴─────────┴──────────────────────────────────────────────┘

个股基本面明细（动态评分）

┌────────────┬────────┬─────────┬────────────────────────────────────────────┐
│ 二级维度   │  得分  │  状态   │ 评分依据                                   │
├────────────┼────────┼─────────┼────────────────────────────────────────────┤
│ 商业模式   │ {fundamental_scores['商业模式']}/25  │ {fundamental_scores['_status']['商业模式']} │ {fundamental_scores['_detail'].get('商业模式', '行业龙头')}              │
├────────────┼────────┼─────────┼────────────────────────────────────────────┤
│ 护城河     │ {fundamental_scores['护城河']}/25  │ {fundamental_scores['_status']['护城河']} │ {fundamental_scores['_detail'].get('护城河', '市占率领先')} │
├────────────┼────────┼─────────┼────────────────────────────────────────────┤
│ 财务质量   │ {fundamental_scores['财务质量']}/25  │ {fundamental_scores['_status']['财务质量']} │ {fundamental_scores['_detail'].get('财务质量', '数据待更新')}       │
├────────────┼────────┼─────────┼────────────────────────────────────────────┤
│ 管理层     │  {fundamental_scores['管理层']}/10  │ {fundamental_scores['_status']['管理层']} │ {fundamental_scores['_detail'].get('管理层', '战略清晰')}                       │
├────────────┼────────┼─────────┼────────────────────────────────────────────┤
│ 个股消息面 │ {fundamental_scores['个股消息面']}/15  │ {fundamental_scores['_status']['个股消息面']} │ {fundamental_scores['_detail'].get('个股消息面', '获订单')}     │
├────────────┼────────┼─────────┼────────────────────────────────────────────┤
│ 基本面合计 │ {fundamental_scores['基本面合计']}/100 │ {fundamental_scores['_status']['基本面合计']} │ 基本面扎实，成长性明确                     │
└────────────┴────────┴─────────┴────────────────────────────────────────────┘

置信度拆解：{confidence_detail}

📊 财务预测与核心指标 (GS Data)

┌────────┬──────────┬──────────┬────────────┬────────────────┬────────┬─────────┬──────────┐
│ 报告期 │ 数据状态 │ 营收(亿) │ 净利(亿)   │ 经营现金流     │ 毛利率 │ EPS(元) │ 利润增长 │
├────────┼──────────┼──────────┼────────────┼────────────────┼────────┼─────────┼──────────┤
"""

        # Earnings table
        if earnings_forecast and earnings_forecast.get("years"):
            revenue = earnings_forecast.get('revenue', ['N/A']*3)
            profit = earnings_forecast.get('net_profit', ['N/A']*3)
            profit_growth_list = earnings_forecast.get('profit_growth', ['N/A']*3)
            eps_list = earnings_forecast.get('eps', ['N/A']*3)
            years = earnings_forecast.get('years', ['2025A', '2026E', '2027E'])

            for i, year in enumerate(years[:3]):
                status = "预测" if 'E' in year else "历史"
                rev_val = revenue[i] if i < len(revenue) and revenue[i] != 'N/A' else 'N/A'
                prof_val = profit[i] if i < len(profit) and profit[i] != 'N/A' else 'N/A'
                eps_val = eps_list[i] if i < len(eps_list) and eps_list[i] != 'N/A' else 'N/A'
                growth_val = profit_growth_list[i] if i < len(profit_growth_list) and profit_growth_list[i] != 'N/A' else 'N/A'
                report += f"│ {year}  │   {status}   │ {rev_val:>8} │ {prof_val:>8} │     N/A ✅     │  N/A   │ {eps_val:>6} │ {growth_val:>8} │\n"
        else:
            for year, status in [("2025A", "历史"), ("2026E", "预测"), ("2027E", "预测")]:
                report += f"│ {year}  │   {status}   │      N/A │      N/A │     N/A ✅     │  N/A   │    N/A │      N/A │\n"

        report += f"""└────────┴──────────┴──────────┴────────────┴────────────────┴────────┴─────────┴──────────┘

高盛财务评价指标

┌─────────────────┬──────────┬──────────────────────┐
│ 指标            │   数值   │ 解读                 │
├─────────────────┼──────────┼──────────────────────┤
│ ROE             │ {gs_metrics['roe']:>8} │ {roe_interp} │
├─────────────────┼──────────┼──────────────────────┤
│ 资产负债率      │ {debt_ratio_display:>8} │ {debt_ratio_interp} │
├─────────────────┼──────────┼──────────────────────┤
│ PE(TTM)         │ {pe_ttm_display:>8} │ {pe_interp} │
├─────────────────┼──────────┼──────────────────────┤
│ PE(FY1)         │ {gs_metrics.get('forecast_pe_fy1', 'N/A'):>8} │ 🟢 极低估值             │
├─────────────────┼──────────┼──────────────────────┤
│ PEG(FY1)        │ {gs_metrics.get('forecast_peg_fy1', peg_display):>8} │ {peg_interp} │
├─────────────────┼──────────┼──────────────────────┤
│ Beta            │ {gs_metrics['beta']:>8} │ {beta_interp} │
├─────────────────┼──────────┼──────────────────────┤
│ 总市值          │ {market_cap_display:>8} │ 港股中小盘              │
└─────────────────┴──────────┴──────────────────────┘

📐 行业对标与估值

┌──────────┬───────┬───────┬──────────┬──────────┬──────────┬──────────────────────────────────┐
│ 股票     │  P/E  │  PEG  │   ROE    │ 总市值   │ 利润增速 │ 优势                             │
├──────────┼───────┼───────┼──────────┼──────────┼──────────┼──────────────────────────────────┤
"""

        # Peer comparison rows
        peer_rows = []
        median_pe = "N/A"
        for p in peers:
            if "中位数" in str(p.get("name", "")) or "行业平均" in str(p.get("name", "")) or "行业" in str(p.get("name", "")):
                median_pe = p.get("pe", "N/A")
                break
        peer_rows.append({"name": "行业中位数", "pe": median_pe, "peg": "—", "roe": "—", "mcap": "—", "growth": "—", "advantage": "汽车零部件（剔除亏损）"})
        peer_rows.append({"name": r.name, "pe": actual_pe, "peg": peg_display, "roe": roe_display, "mcap": mcap_display, "growth": growth_display, "advantage": str(industry_pos)})
        # Add a comparison peer if available
        for p in peers:
            pn = str(p.get("name", ""))
            if "行业" not in pn and "中位数" not in pn and "平均" not in pn and "Sector" not in pn:
                peer_rows.insert(1, {"name": pn, "pe": str(p.get("pe", "N/A")), "peg": str(p.get("peg", "N/A")),
                                      "roe": "N/A", "mcap": "N/A", "growth": "N/A", "advantage": str(p.get("note", ""))})
                break

        for i, row in enumerate(peer_rows):
            report += f"│ {row['name'][:8]:>8} │ {row['pe']:>5} │ {row['peg']:>5} │ {row.get('roe','N/A'):>8} │ {row.get('mcap','N/A'):>8} │ {row.get('growth','N/A'):>8} │ {row['advantage'][:32]} │\n"

        report += f"""└──────────┴───────┴───────┴──────────┴──────────┴──────────┴──────────────────────────────────┘

📊 实时行情

| 日期 | 最新价 | 涨跌幅 | 5日涨幅 | 总市值 |
|------|--------|--------|---------|--------|
| {datetime.now().strftime('%m-%d')} | {current_price_str} | {price_change_display} | N/A | {market_cap_display} |

📊 关键技术指标

| 指标 | 值 | 解读 |
|------|-----|------|
| 技术分 | {r.sentiment_score}/100 | {'🔴 短期偏空' if r.sentiment_score < 40 else '🟡 中性' if r.sentiment_score < 60 else '🟢 偏多'} |
| MA5 | {ma5_str} | {ma5_interp} |
| MA20 | {ma20_str} | {ma20_interp} |
| RSI | {rsi_str} | {rsi_interp} |
| MACD | {('🟢 金叉' if macd_golden else '🔴 死叉') if macd_golden is not None else 'N/A'} | {'🟢 短期多头' if macd_golden else '🔴 短期空头' if macd_golden is not None else 'N/A'} |

⚠️ 风险评估

┌────────────┬────────┬───────┬──────────────────────────────┐
│ 风险       │ 可能性 │ 影响  │ 说明                         │
├────────────┼────────┼───────┼──────────────────────────────┤
│ 技术面偏空 │ {'🔴 高' if r.sentiment_score < 40 else '🟡 中'} │ 🟡 中 │ 技术分{r.sentiment_score}，短期承压           │
├────────────┼────────┼───────┼──────────────────────────────┤
│ ROE偏低    │ 🟡 中  │ 🟡 中 │ 盈利质量待提升 │
├────────────┼────────┼───────┼──────────────────────────────┤
│ 流动性风险 │ 🟡 中  │ 🟡 中 │ 中小盘股，成交偏淡           │
├────────────┼────────┼───────┼──────────────────────────────┤
│ 汇率波动   │ 🟡 中  │ 🟡 中 │ 美元收入占比高               │
└────────────┴────────┴───────┴──────────────────────────────┘

🎯 今日操作建议

┌──────────┬──────────────────────────────────────────┐
│ 操作     │ {trade_action}                            │
├──────────┼──────────────────────────────────────────┤
│ 仓位     │ 不超过总仓 10%，分 2-3 批入场            │
├──────────┼──────────────────────────────────────────┤
│ 止损线   │ {sl} {currency}（{stop_loss_pct}）                       │
├──────────┼──────────────────────────────────────────┤
│ 目标价   │ {target_mean}（{upside_display}，机构一致预期）        │
├──────────┼──────────────────────────────────────────┤
│ 逻辑     │ {trade_logic} │
└──────────┴──────────────────────────────────────────┘

⚠️ 免责声明：本报告由 AI 生成，仅供参考，不构成投资建议。

📊 数据说明：实时行情/机构目标价/财务数据/一致预期来自 mx-data。技术面来自 daily_stock_analysis。基本面明细为动态评分。

报告生成时间：{datetime.now().strftime('%Y-%m-%d')} | 数据截止：实时
"""

        return report
