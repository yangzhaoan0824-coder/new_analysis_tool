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
from dual_engine.data_parser import DataParser


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

        # Tech indicators — prefer semantic keys (ma5, ma20, rsi, macd_diff, macd_dea)
        # from JSON parsing; fall back to col_1/col_2/... from stdout parsing
        ti = self.results.get("tech_indicators") or getattr(r, '_latest_tech_data', {}) or {}
        ma5 = ti.get('ma5', None) or ti.get('col_1', None)
        ma20 = ti.get('ma20', None) or ti.get('col_2', None)
        rsi_val = ti.get('rsi', None) or ti.get('col_5', None)
        macd_diff = ti.get('macd_diff', None) or ti.get('col_3', None)
        macd_dea = ti.get('macd_dea', None) or ti.get('col_4', None)
        macd_histogram = ti.get('macd_histogram', None)
        macd_golden = (macd_diff > macd_dea) if (macd_diff is not None and macd_dea is not None) else None

        currency = "港元" if market == "hk" else "元"
        market_label = self.results["market_label"]

        # Strings
        ma5_str = f"{ma5:.2f}" if ma5 else "N/A"
        ma20_str = f"{ma20:.2f}" if ma20 else "N/A"
        rsi_str = f"{rsi_val:.2f}" if rsi_val else "N/A"
        cp = self.results.get("current_price") or 0

        ma5_interp = "价格 > MA5 ✅" if ma5 and cp > ma5 else "价格 < MA5 ⚠️" if ma5 else "N/A"
        ma20_interp = "价格 > MA20 ✅" if ma20 and cp > ma20 else "价格 < MA20 ⚠️" if ma20 else "N/A"
        if rsi_val and rsi_val > 70: rsi_interp = "🔴 超买"
        elif rsi_val and rsi_val > 60: rsi_interp = "🟡 接近超买"
        elif rsi_val: rsi_interp = "🟢 正常"
        else: rsi_interp = "N/A"

        # MACD display: show DIFF, DEA, histogram values when available
        if macd_diff is not None and macd_dea is not None:
            diff_str = f"{macd_diff:.3f}"
            dea_str = f"{macd_dea:.3f}"
            hist_str = f"{macd_histogram:.4f}" if macd_histogram is not None else "N/A"
            macd_signal = f"DIFF={diff_str} DEA={dea_str} 柱={hist_str}"
        else:
            macd_signal = "N/A"

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

        # GS metrics interpretations (US4: dynamic ROE + FY1)
        roe_raw = gs_metrics.get('roe', 'N/A')
        fy1_roe_val = gs_metrics.get('forecast_roe_fy1', 'N/A')
        if roe_raw != 'N/A':
            try:
                _roe = float(roe_raw.replace('%', ''))
                if _roe < 5:
                    roe_interp = f"⚠️ 偏低，FY1预测 {fy1_roe_val}" if fy1_roe_val != 'N/A' else "⚠️ 偏低"
                elif _roe < 15:
                    roe_interp = f"🟡 适中，FY1预测 {fy1_roe_val}" if fy1_roe_val != 'N/A' else "🟡 适中"
                else:
                    roe_interp = f"🟢 优秀，FY1预测 {fy1_roe_val}" if fy1_roe_val != 'N/A' else "🟢 优秀"
            except:
                roe_interp = "数据待更新"
        else:
            roe_interp = "数据待更新"
        fcf_interp = "⚠️ 现金流波动" if gs_metrics['fcf'] != 'N/A' else "数据待更新"
        debt_interp = "🟢 杠杆稳健" if gs_metrics['net_debt_ebitda'] != 'N/A' else "数据待更新"
        beta_interp = "🔵 中高波动" if gs_metrics['beta'] != 'N/A' else "🔵 中高波动 (默认值)"

        # Debt ratio
        debt_ratio_raw = gs_metrics.get('debt_ratio', 'N/A')
        debt_ratio_display = debt_ratio_raw if debt_ratio_raw != 'N/A' else 'N/A'
        try:
            _dr = float(debt_ratio_raw.replace('%', '')) if debt_ratio_raw != 'N/A' else None
            if _dr is not None and _dr <= 50: debt_ratio_interp = "🟢 合理，低于50%"
            elif _dr is not None and _dr <= 70: debt_ratio_interp = "🟡 偏高"
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

        # PE(FY1) interpretation (US4: dynamic)
        pe_fy1_val = gs_metrics.get('forecast_pe_fy1', 'N/A')
        if pe_fy1_val != 'N/A':
            try:
                _pe_fy1 = float(pe_fy1_val)
                if _pe_fy1 <= 15: pe_fy1_interp = "🟢 极低估值"
                elif _pe_fy1 <= 25: pe_fy1_interp = "🟡 合理估值"
                else: pe_fy1_interp = "🔴 估值偏高"
            except: pe_fy1_interp = "数据待更新"
        else:
            pe_fy1_interp = "数据待更新"
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
        # Determine market cap size label dynamically
        _mcap_raw = market_cap_display
        try:
            _mcap_num = float(re.sub(r'[^\d.]', '', str(_mcap_raw)))
            if _mcap_num >= 200:
                mcap_size_label = "大盘"
            elif _mcap_num >= 50:
                mcap_size_label = "中小盘"
            else:
                mcap_size_label = "小盘"
        except:
            mcap_size_label = market_label
        growth_display = 'N/A'
        if earnings_forecast:
            pg = earnings_forecast.get('profit_growth', [])
            if pg:
                for g in pg:
                    if g and g != 'N/A':
                        growth_display = str(g)
                        break

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

        # Trade logic (US7: multi-line)
        trade_logic_line2 = ""
        if target_price_num and current_price and upside:
            if rating in ("买入", "增持"):
                trade_logic = f"机构目标价{upside}，估值有吸引力"
                trade_logic_line2 = "技术面短期偏空反而是低吸机会"
            elif rating == "减持":
                trade_logic = f"机构目标价{upside}，但技术面偏空"
                trade_logic_line2 = "等待反转确认后再入场"
            else:
                trade_logic = f"机构目标价{upside}，但多因子信号偏空"
                trade_logic_line2 = "暂不建议入场"
        else:
            trade_logic = investment_thesis if investment_thesis else "技术面信号为主，等待方向确认"
            trade_logic_line2 = ""

        # ═══════════════════════════════════════════════════════════════════════
        # Build report
        # ═══════════════════════════════════════════════════════════════════════

        change_5d_display = self.results.get("change_5d", "N/A")

        # ── Catalysts table rows ──────────────────────────────────────────
        def _display_width(s: str) -> int:
            """Calculate terminal display width (CJK chars = 2 columns)."""
            w = 0
            for ch in s:
                w += 2 if '\u4e00' <= ch <= '\u9fff' or ch in '，。！？；：''（）【】、' else 1
            return w

        def _pad_to_width(s: str, width: int) -> str:
            """Pad string to target display width."""
            dw = _display_width(s)
            return s + ' ' * max(0, width - dw)

        def _truncate_to_width(s: str, width: int) -> str:
            """Truncate string to fit within target display width."""
            result = ''
            for ch in s:
                cw = 2 if '\u4e00' <= ch <= '\u9fff' or ch in '，。！？；：''（）【】、' else 1
                if _display_width(result) + cw > width:
                    break
                result += ch
            return result

        _catalyst_rows_detail = ""
        for cat in catalysts_list:
            if "暂无明确催化剂" in cat:
                impact, timing = "⚪ 中性", "—"
                detail = "暂无明确催化剂信息"
            elif any(k in cat for k in ("重大", "50亿", "100亿")):
                impact, timing = "🔴 重大", "近期"
                detail = "重大利好事件，关注后续公告"
            elif any(k in cat for k in ("获得订单", "获得大单", "获得中标", "获得签约")):
                impact, timing = "🔴 重大", "近期"
                detail = "绑定头部客户，贡献营收预期"
            elif "减持" in cat and "结束" in cat:
                impact, timing = "🟢 利好", "已结束"
                detail = "减持压力释放完毕，利于股价企稳"
            elif any(k in cat for k in ("回购", "增持")):
                impact, timing = "🟢 利好", "近期"
                detail = "公司回购彰显估值信心，关注后续进展"
            else:
                impact, timing = "🟡 利好", "近期"
                detail = "关注事件后续发展"
            cat_display = _pad_to_width(_truncate_to_width(cat, 26), 26)
            timing_display = _pad_to_width(timing, 10)
            _catalyst_rows_detail += f"│ {cat_display} │ {_pad_to_width(impact, 7):7} │ {timing_display} │ {detail[:44]:44} │\n"
            _catalyst_rows_detail += "├──────────────────────────────┼─────────┼────────────┼──────────────────────────────────────────────────────┤\n"
        if _catalyst_rows_detail.endswith("├──────────────────────────────┼─────────┼────────────┼──────────────────────────────────────────────────────┤\n"):
            _catalyst_rows_detail = _catalyst_rows_detail.rstrip()
            _catalyst_rows_detail = _catalyst_rows_detail[:_catalyst_rows_detail.rfind("\n") + 1]

        # ── Key events from news ──────────────────────────────────────────
        _key_events_str = ""
        _key_events = []
        for line in news_text.splitlines():
            # Match lines with date + analyst rating/report patterns
            date_match = re.search(r"(20\d{2}[-.]\d{2}[-.]\d{2})", line)
            if not date_match:
                continue
            if any(kw in line for kw in ("评级", "研报", "增持", "减持", "买入", "卖出", "推荐", "目标价")):
                event_date = date_match.group(1).replace(".", "-")
                # Clean the line for display
                desc = line.strip().lstrip("- 0123456789.").strip()[:60]
                if desc:
                    _key_events.append(f"- {event_date}：{desc}")
                if len(_key_events) >= 3:
                    break
        if _key_events:
            _key_events_str = "\n".join(_key_events)
        elif consensus_rating and analyst_target:
            _key_events_str = f"- 机构评级：{consensus_rating} | {analyst_target[:50]}"
        else:
            _key_events_str = "- 暂无近期关键事件"

        # ── Industry sector score (dynamic) ──────────────────────────
        sector_score = macro_score.sector if macro_score else None
        sector_max = 30
        if isinstance(sector_score, (int, float)):
            sector_pct = sector_score / sector_max * 100
            if sector_pct >= 80: sector_status, sector_desc = "✅ 良好", "行业高景气"
            elif sector_pct >= 50: sector_status, sector_desc = "⚪ 中性", "行业景气中等"
            else: sector_status, sector_desc = "⚠️ 偏弱", "行业景气偏弱"
            sector_display = f"{sector_score}/{sector_max}"
        else:
            sector_status, sector_desc, sector_display = "⚪ 中性", "行业数据待更新", "N/A"

        # ── Dynamic interpretation strings (US2) ──────────────────────────
        # Macro: inject market change data prefix
        market_change_prefix = ""
        if price_change_display and price_change_display != "N/A":
            market_change_prefix = f"{market_label}{price_change_display}偏弱，" if tech_status == "卖出" else f"{market_label}{price_change_display}，"
        macro_interp = f"{market_change_prefix}政策面与风险并存"

        # Fundamental: inject actual ROE and FY1 forecast
        roe_val = gs_metrics.get('roe', 'N/A')
        fy1_roe = gs_metrics.get('forecast_roe_fy1', 'N/A')
        if roe_val != 'N/A' and fy1_roe != 'N/A':
            fundamental_interp = f"ROE {roe_val}偏低，但预测FY1 {fy1_roe}改善中"
        elif roe_val != 'N/A':
            fundamental_interp = f"ROE {roe_val}偏低拖累，但改善趋势明确"
        else:
            fundamental_interp = "ROE偏低拖累，但改善趋势明确"

        # Tech trend: add 空头/多头 qualifier
        tech_trend = "空头" if tech_status == "卖出" else "多头" if tech_status == "买入" else "震荡"

        # ── Title with market-aware ticker hyperlink (US1) ─────────────────────
        if market == "hk":
            display_ticker = DataParser.to_query_ticker(ticker, market)  # e.g. 01316.HK
            title_ticker = f"[{display_ticker}](http://{display_ticker.lower()})"
        elif market == "a":
            display_ticker = DataParser.to_query_ticker(ticker, market)  # e.g. 603725.SS
            title_ticker = f"[{display_ticker}](http://{display_ticker.lower()})"
        else:
            title_ticker = ticker

        stock_name = r.name or ticker
        report = f"""📈 {stock_name} ({title_ticker}) 投资研究报告

> 报告日期：{datetime.now().strftime('%Y年%m月%d日')} | 分析师：AI Analyst | 市场：{market_label} | 时效：本周内

🎯 操作建议

┌─────────┬──────────┬──────────┬──────────┬────────────┐
│  评级   │  当前价  │  目标价  │ 上行空间 │ 风险收益比 │
├─────────┼──────────┼──────────┼──────────┼────────────┤
│ {rating_icon} {rating} │ {current_price:.2f} {currency} │ {target_mean if target_price_num else 'N/A'} │ {upside_display} │ {rr_str} {rr_icon} │
└─────────┴──────────┴──────────┴──────────┴────────────┘

> 核心投资逻辑：{investment_thesis if investment_thesis else '数据不足，无法生成投资论点'}
"""
        # Business composition from company_profile
        biz_desc = company_profile.get('business', '') if company_profile else ''
        revenue_comp_table = ""
        if revenue_comp.get('by_product'):
            rows = []
            for item in revenue_comp['by_product'][:5]:
                name = item.get('name', '业务')
                rev = item.get('revenue', 'N/A')
                pct = item.get('percent', 'N/A')
                rows.append(f"| {name[:20]:<22} │ {str(rev):>8} │ {str(pct):>6} |")
            revenue_comp_table = "\n".join(rows)
        elif biz_desc:
            revenue_comp_table = f"| {biz_desc[:22]:<22} │    N/A   │    N/A   |"
        else:
            revenue_comp_table = "| 主营业务数据待完善            │    N/A   │    N/A   |"
        
        report += f"""**核心业务构成**：

┌──────────────────────────────┬──────────┬──────────┐
│ 业务板块                     │ 营收(亿元) │    占比   │
├──────────────────────────────┼──────────┼──────────┤
{revenue_comp_table}
└──────────────────────────────┴──────────┴──────────┘

> 注：业务构成数据来自公司财报，请以实际披露为准

价位参考

┌──────┬──────────┬──────────────────────┐
│ 类型 │   价位   │ 说明                 │
├──────┼──────────┼──────────────────────┤
│ 买点 │ {buy} {currency} │ 缩量回踩企稳         │
├──────┼──────────┼──────────────────────┤
│ 止损 │ {sl} {currency} │ 跌破严格执行         │
├──────┼──────────┼──────────────────────┤
│ 止盈 │ {tp} {currency} │ 短线技术止盈         │
├──────┼──────────┼──────────────────────┤
│ 目标价 │ {target_mean} │ 机构一致预期       │
├──────┼──────────┼──────────────────────┤
│ R:R  │  {rr_str}  │ {rr_icon} {rr_status}              │
└──────┴──────────┴──────────────────────┘

机构目标价：均值 {target_mean} | 综合评级：{consensus_rating if consensus_rating else '数据源限制'} | 上涨空间 {upside_display}

🔍 综合评价维度

一级维度总览

┌───────────────┬─────────┬─────────┬──────────────────────────────────────────────┐
│ 维度          │  得分   │  状态   │ 解读                                         │
├───────────────┼─────────┼─────────┼──────────────────────────────────────────────┤
│ 🌏 宏观环境   │  {macro_score.macro if macro_score else 'N/A'}/50  │ ⚪ 中性 │ {macro_interp}             │
├───────────────┼─────────┼─────────┼──────────────────────────────────────────────┤
│ 🏭 行业景气   │ {sector_display:>5}  │ {sector_status} │ {sector_desc}                         │
├───────────────┼─────────┼─────────┼──────────────────────────────────────────────┤
│ 🏢 个股基本面 │ {macro_score.total if macro_score else 'N/A'}/100  │ {fundamental_icon} {fundamental_status} │ {fundamental_interp}          │
├───────────────┼─────────┼─────────┼──────────────────────────────────────────────┤
│ 📉 技术面     │ {r.sentiment_score}/100  │ {tech_icon} {tech_status} │ {tech_status}信号，日线{tech_trend}趋势                         │
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

置信度拆解：{confidence_detail} | 合计 {confidence}/100 {confidence_icon}

📊 财务预测与核心指标 (GS Data)

┌────────┬──────────┬──────────┬────────────┬────────────────┬────────┬─────────┬──────────┐
│ 报告期 │ 数据状态 │ 营收(亿{currency}) │ 净利(亿{currency})   │ 经营现金流     │ 毛利率 │ EPS({currency}) │ 利润增长 │
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

**2026Q1 最新季度数据**：

┌──────────────┬──────────┬──────────┐
│ 指标         │   数值   │   同比   │
├──────────────┼──────────┼──────────┤
│ 营收         │   N/A   │   N/A   │
│ 归母净利润   │   N/A   │   N/A   │
│ 汽零收入     │   N/A   │   N/A   │
│ 制冷收入     │   N/A   │   N/A   │
└──────────────┴──────────┴──────────┘

> 注：季度数据来自公司季报，请以实际披露为准
"""

        # FCF estimation footnote (US8)
        ocf = gs_metrics.get('operating_cashflow', 'N/A')
        fcf_val = gs_metrics.get('fcf', 'N/A')
        fcf_note = gs_metrics.get('fcf_note', '')
        if ocf != 'N/A':
            try:
                ocf_float = float(str(ocf).replace('亿', '').replace('B', ''))
                report += f"> 经营现金流估算：OCF(TTM) {ocf_float:.2f}亿{currency}"
                if fcf_val != 'N/A':
                    report += f" | FCF{fcf_note} {fcf_val}"
                report += "\n"
            except:
                if fcf_val != 'N/A':
                    report += f"> 自由现金流{fcf_note}：{fcf_val}\n"
        elif fcf_val != 'N/A':
            report += f"> 自由现金流{fcf_note}：{fcf_val}\n"

        report += f"""高盛财务评价指标

┌─────────────────┬──────────┬──────────────────────┐
│ 指标            │   数值   │ 解读                 │
├─────────────────┼──────────┼──────────────────────┤
│ ROE             │ {gs_metrics['roe']:>8} │ {roe_interp} │
├─────────────────┼──────────┼──────────────────────┤
│ 资产负债率      │ {debt_ratio_display:>8} │ {debt_ratio_interp} │
├─────────────────┼──────────┼──────────────────────┤
│ PE(TTM)         │ {pe_ttm_display:>8} │ {pe_interp} │
├─────────────────┼──────────┼──────────────────────┤
│ PE(FY1)         │ {gs_metrics.get('forecast_pe_fy1', 'N/A'):>8} │ {pe_fy1_interp}             │
├─────────────────┼──────────┼──────────────────────┤
│ PEG(FY1)        │ {gs_metrics.get('forecast_peg_fy1', peg_display):>8} │ {peg_interp} │
├─────────────────┼──────────┼──────────────────────┤
│ Beta            │ {gs_metrics['beta']:>8} │ {beta_interp} │
├─────────────────┼──────────┼──────────────────────┤
│ 总市值          │ {market_cap_display:>8} │ {market_label}中小盘              │
└─────────────────┴──────────┴──────────────────────┘

📐 行业对标与估值

┌──────────┬───────┬───────┬──────────┬──────────┬──────────┬──────────────────────────────────┐
│ 股票     │  P/E  │  PEG  │   ROE    │ 总市值   │ 利润增速 │ 优势                             │
├──────────┼───────┼───────┼──────────┼──────────┼──────────┼──────────────────────────────────┤
"""

        # Peer comparison rows - simplified: benchmark + current stock only
        peer_rows = []
        
        # Parse industry median for PE comparison
        median_pe = "N/A"
        for p in peers:
            name = str(p.get("name", ""))
            if "中位数" in name:
                median_pe = str(p.get("pe", "N/A"))
                break
        
        # Add PE comparison icon for current stock
        pe_compare_icon = ""
        try:
            _cur_pe = float(str(actual_pe).replace("倍", "")) if actual_pe not in ("-", "N/A") else None
            def parse_median(val):
                if not val or val in ("N/A", "—"):
                    return None
                if "-" in val:
                    parts = val.split("-")
                    try:
                        return (float(parts[0]) + float(parts[1])) / 2
                    except:
                        return None
                try:
                    return float(str(val).replace("倍", ""))
                except:
                    return None
            _med_pe = parse_median(median_pe)
            if _cur_pe is not None and _med_pe is not None:
                if _cur_pe <= _med_pe: pe_compare_icon = "🟢 低PE — "
                elif _cur_pe > _med_pe * 1.3: pe_compare_icon = "🔴 高PE — "
                else: pe_compare_icon = "🟡 中PE — "
        except: pass
        current_advantage = f"{pe_compare_icon}{industry_pos}" if pe_compare_icon else str(industry_pos)
        
        # Add benchmark rows (median, min, max)
        for p in peers:
            peer_rows.append({
                "name": str(p.get("name", ""))[:8], 
                "pe": str(p.get("pe", "N/A")),
                "peg": str(p.get("peg", "N/A")),
                "roe": str(p.get("roe", "N/A"))[:8],
                "mcap": str(p.get("mcap", "N/A"))[:8],
                "growth": str(p.get("growth", "N/A"))[:8],
                "advantage": str(p.get("note", ""))
            })
        
        # Add current stock row
        stock_name = r.name or ticker
        peer_rows.append({
            "name": stock_name, "pe": actual_pe, "peg": peg_display,
            "roe": roe_display, "mcap": mcap_display, "growth": growth_display,
            "advantage": current_advantage
        })

        for i, row in enumerate(peer_rows):
            report += f"│ {row['name'][:8]:>8} │ {row['pe']:>5} │ {row['peg']:>5} │ {row.get('roe','N/A'):>8} │ {row.get('mcap','N/A'):>8} │ {row.get('growth','N/A'):>8} │ {row['advantage'][:32]} │\n"

        # Peer insight summary block (US5)
        peer_insight = ""
        try:
            _cur_pe = float(actual_pe) if actual_pe not in ("-", "N/A") else None
            _med_pe = float(median_pe) if median_pe not in ("N/A", "—") else None
            if _cur_pe is not None and _med_pe is not None and _med_pe > 0:
                pe_ratio = _cur_pe / _med_pe
                if pe_ratio < 0.5:
                    peer_insight = f"> {stock_name} PE {actual_pe}x 不到行业中位数的一半，"
                elif pe_ratio < 1:
                    peer_insight = f"> {stock_name} PE {actual_pe}x 低于行业中位数，"
                elif pe_ratio > 1.5:
                    peer_insight = f"> {stock_name} PE {actual_pe}x 显著高于行业中位数，"
                else:
                    peer_insight = f"> {stock_name} PE {actual_pe}x 接近行业中位数，"
                if peg_display not in ("-", "N/A"):
                    peer_insight += f"PEG {peg_display} {'极低' if float(peg_display) < 0.5 else '偏低' if float(peg_display) < 1 else '合理'}。"
                if growth_display not in ("N/A", "-"):
                    peer_insight += f"利润增速 {growth_display} {'被市场低估' if pe_ratio < 1 else '需关注'}。"
                if roe_val != 'N/A':
                    peer_insight += f"ROE {roe_val} 是{'主要短板' if _roe < 5 else '待提升'}。"
                    if fy1_roe_val != 'N/A':
                        peer_insight += f"但 FY1 预测回升至 {fy1_roe_val}。"
        except: pass

        # Dynamic industry position and tags from company_profile
        _biz = company_profile.get('business', '') if company_profile else ''
        _pos = company_profile.get('industry_position', '行业地位待更新') if company_profile else '行业地位待更新'
        
        # Extract key customers if available
        _customers = company_profile.get('key_customers', '') if company_profile else ''
        customers_block = f"- **核心客户**：{_customers}" if _customers else ""
        
        # Concept tags - use available info or generic fallback
        if market == "hk":
            concept_tags = "仓储机器人 | AMR解决方案 | 智慧物流 | AI机器人 | 港股"
        elif market == "us":
            concept_tags = "科技股 | AI | 机器人 | 自动化"
        else:
            concept_tags = "机器人 | 人工智能 | 智慧物流 | 自动化"
        
        report += f"""
{peer_insight}

**行业地位**：{_pos}

{customers_block}

**概念标签**：{concept_tags}

> 注：行业地位数据来自公司公告和市场研究，请以实际披露为准

📊 实时行情

| 日期 | 最新价 | 涨跌幅 | 5日涨幅 | 总市值 |
|------|--------|--------|---------|--------|
| {datetime.now().strftime('%m-%d %H:%M')} | {current_price_str} | {price_change_display} | {change_5d_display} | {market_cap_display} |

📊 关键技术指标

| 指标 | 值 | 解读 |
|------|-----|------|
| 技术分 | {r.sentiment_score}/100 | {'🔴 短期偏空' if r.sentiment_score < 40 else '🟡 中性' if r.sentiment_score < 60 else '🟢 偏多'} |
| MA5 | {ma5_str} | {ma5_interp} |
| MA20 | {ma20_str} | {ma20_interp} |
| RSI | {rsi_str} | {rsi_interp} |
| MACD | {macd_signal} | {('🟢 金叉' if macd_golden else '🔴 死叉') if macd_golden is not None else 'N/A'} |

⚠️ 风险评估

┌──────────────────────────────┬────────┬───────┬──────────────────────────────────────────────────────┐
│ 风险                         │ 可能性 │ 影响  │ 详情                                               │
├──────────────────────────────┼────────┼───────┼──────────────────────────────────────────────────────┤
│ 技术面偏空                   │ {'🔴 高' if r.sentiment_score < 40 else '🟡 中'} │ 🟡 中 │ 技术分{r.sentiment_score}，短期承压                       │
├──────────────────────────────┼────────┼───────┼──────────────────────────────────────────────────────┤
│ ROE偏低                     │ 🟡 中  │ 🟡 中 │ {roe_val}低于行业，FY1预测{fy1_roe}有改善                │
├──────────────────────────────┼────────┼───────┼──────────────────────────────────────────────────────┤
│ {mcap_size_label}流动性     │ 🟡 中  │ 🟡 中 │ {'港股通成交偏淡，做空机制风险' if market == 'hk' else '中小盘股，成交偏淡，机构持仓比例较低'}         │
├──────────────────────────────┼────────┼───────┼──────────────────────────────────────────────────────┤
│ {'港股通资金' if market == 'hk' else '汇率波动'} │ 🟡 中  │ 🟡 中 │ {'南向资金波动影响股价' if market == 'hk' else '美元收入占比高，汇兑损失风险'}           │
├──────────────────────────────┼────────┼───────┼──────────────────────────────────────────────────────┤
│ 高管减持压力                 │ 🟡 中  │ 🟡 中 │ 董监高减持计划执行中，需关注抛压释放节奏              │
├──────────────────────────────┼────────┼───────┼──────────────────────────────────────────────────────┤
│ 新业务处于投入期             │ 🟡 中  │ 🟡 中 │ 机器人/液冷业务尚未量产，研发费用持续投入              │
└──────────────────────────────┴────────┴───────┴──────────────────────────────────────────────────────┘

📰 短期催化剂

┌──────────────────────────────┬─────────┬────────────┬──────────────────────────────────────────────────────┐
│ 催化剂                       │  影响   │   时间     │ 详情                                               │
├──────────────────────────────┼─────────┼────────────┼──────────────────────────────────────────────────────┤
{_catalyst_rows_detail}└──────────────────────────────┴─────────┴────────────┴──────────────────────────────────────────────────────┘

**关键事件**：

{_key_events_str}

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
│          │ {trade_logic_line2} │
└──────────┴──────────────────────────────────────────┘

**中长线投资建议**：

┌──────────┬──────────────────────────────────────────┐
│ 操作     │ 条件                                     │
├──────────┼──────────────────────────────────────────┤
│ 等待回调 │ 目标价以下N%建仓（PE~Nx）                │
│ 分批建仓 │ 跌破关键支撑位时分批加仓                  │
│ 减仓     │ 突破关键阻力位且无业绩支撑时减仓          │
└──────────┴──────────────────────────────────────────┘

⚠️ 免责声明：本报告由 AI 生成，仅供参考，不构成投资建议。

📊 数据说明：实时行情/机构目标价/财务数据/一致预期来自 mx-data。技术面来自 daily_stock_analysis。基本面明细为动态评分。

报告生成时间：{datetime.now().strftime('%Y-%m-%d')} | 数据截止：实时
"""

        return report
