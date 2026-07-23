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

    # ═══════════════════════════════════════════════════════════════════════════════
    # Markdown report sections — each is a focused, single-responsibility method
    # ═══════════════════════════════════════════════════════════════════════════════

    # ── Helper: CJK-aware width ──────────────────────────────────────────────

    @staticmethod
    def _display_width(s: str) -> int:
        """Terminal display width: CJK chars = 2 columns."""
        w = 0
        for ch in s:
            w += 2 if '\u4e00' <= ch <= '\u9fff' or ch in '，。！？；：''（）【】、' else 1
        return w

    @staticmethod
    def _pad_to_width(s: str, width: int) -> str:
        return s + ' ' * max(0, width - ReportGenerator._display_width(s))

    @staticmethod
    def _truncate_to_width(s: str, width: int) -> str:
        result = ''
        for ch in s:
            cw = 2 if '\u4e00' <= ch <= '\u9fff' or ch in '，。！？；：''（）【】、' else 1
            if ReportGenerator._display_width(result) + cw > width:
                break
            result += ch
        return result

    # ── Helper: missing-data detection ────────────────────────────────────────

    def _collect_missing_fields(self) -> list[dict]:
        """Detect N/A fields caused by upstream data-source gaps (not algorithm faults).

        Returns list of {field, label, source, impact}. Empty list = full data.
        """
        missing: list[dict] = []
        c = self.composite
        gs = c.get("gs_metrics") or {}
        currency = "港元" if self.market == "hk" else "元"

        # 1. 机构目标价
        target = c.get("target_price_num")
        consensus_rating = self.results.get("consensus_rating", "") or ""
        analyst_target = self.results.get("analyst_target", "") or ""
        if not target:
            missing.append({
                "field": "target_price",
                "label": f"机构目标价均值",
                "source": "mx-data 机构评级接口",
                "impact": "上行空间 / 风险收益比 / 目标价位均无法计算",
            })
        if not consensus_rating or consensus_rating == "数据源限制":
            missing.append({
                "field": "consensus_rating",
                "label": "综合评级（买入/增持等）",
                "source": "mx-data 机构评级接口",
                "impact": "一致预期评级缺失",
            })
        if not analyst_target:
            missing.append({
                "field": "analyst_target",
                "label": "机构目标价详情",
                "source": "mx-data 机构评级接口",
                "impact": "无法展示具体目标价区间",
            })

        # 2. GS 财务指标字段
        if gs.get("debt_ratio", "N/A") == "N/A":
            missing.append({
                "field": "debt_ratio",
                "label": "资产负债率",
                "source": "mx-data 财务预测表",
                "impact": "杠杆水平评价缺失",
            })
        if gs.get("net_debt_ebitda", "N/A") == "N/A":
            missing.append({
                "field": "net_debt_ebitda",
                "label": "净负债/EBITDA",
                "source": "mx-data 财务预测表",
                "impact": "偿债能力评价缺失",
            })

        # 3. 业务结构
        company_profile = self.results.get("company_profile") or {}
        rev_comp = c.get("revenue_comp") or {}
        by_product = company_profile.get("revenue_by_product") or []
        if not by_product and not rev_comp.get("by_product"):
            missing.append({
                "field": "revenue_by_product",
                "label": "业务收入构成",
                "source": "mx-data 公司画像接口",
                "impact": "业务板块拆分不可用",
            })

        # 4. 财务预测期（历史 vs 预测）
        forecast_keys = ["forecast_pe_fy1", "forecast_pe_fy2", "forecast_pe_fy3",
                         "forecast_peg_fy1", "forecast_roe_fy1"]
        if all(not gs.get(k) for k in forecast_keys):
            missing.append({
                "field": "forecast_metrics",
                "label": "未来年度财务预测（FY1/FY2/FY3）",
                "source": "mx-data 一致预期接口",
                "impact": "前瞻估值全部缺失，仅看历史",
            })

        # 5. 实时行情/涨跌幅
        price_change = self.results.get("price_change", "N/A")
        change_5d = self.results.get("change_5d", "N/A")
        if price_change == "N/A" and change_5d == "N/A":
            missing.append({
                "field": "price_changes",
                "label": "实时涨跌幅 / 5日涨幅",
                "source": "mx-data 实时行情接口",
                "impact": "行情动量维度缺失",
            })

        return missing

    def _build_missing_data_banner(self) -> str:
        """Render compact missing-data info block (shown when upstream has gaps)."""
        missing = self._collect_missing_fields()
        if not missing:
            return ""

        lines = [
            "⚠️ 数据源字段缺失提示",
            "",
            "本次分析中以下字段因上游数据源（mx-data）未返回而标记为 N/A，非算法问题：",
            "",
        ]
        for i, m in enumerate(missing, 1):
            lines.append(f"{i}. **{m['label']}** (`{m['field']}`)")
            lines.append(f"   - 数据源：{m['source']}")
            lines.append(f"   - 影响：{m['impact']}")

        lines.extend([
            "",
            f"> 共检测到 {len(missing)} 项字段缺失，请结合实际可得数据审慎判断。",
            "",
        ])
        return "\n".join(lines)

    # ── Header: title + operation table + business composition ─────────────────

    def _build_header(self) -> str:
        r, market, ticker, c = self.r, self.market, self.ticker, self.composite
        currency = "港元" if market == "hk" else "元"
        market_label = self.results["market_label"]

        buy, sl, tp = c["buy"], c["sl"], c["tp"]
        rr_str = c["rr_str"]
        rating, rating_icon = c["rating"], c["rating_icon"]
        target_price_num = c["target_price_num"]
        upside = c["upside"] or "N/A"
        current_price = self.results.get("current_price") or 0
        consensus_rating = self.results.get("consensus_rating", "") or "数据源限制"

        target_mean = f"{target_price_num:.2f}{currency}" if target_price_num else "N/A"
        rr_icon = "✅" if c["rr"] and c["rr"] >= 2 else "⚠️"
        rr_status = "充足" if c["rr"] and c["rr"] >= 2 else "不足"

        investment_thesis = c["investment_thesis"]
        analyst_target = self.results.get("analyst_target", "") or ""

        # Market-aware ticker hyperlink
        if market == "hk":
            title_ticker = f"[{DataParser.to_query_ticker(ticker, market)}](http://{DataParser.to_query_ticker(ticker, market).lower()})"
        elif market == "a":
            title_ticker = f"[{DataParser.to_query_ticker(ticker, market)}](http://{DataParser.to_query_ticker(ticker, market).lower()})"
        else:
            title_ticker = ticker

        stock_name = r.name or ticker

        # Business composition table — prefer structured revenue_by_product
        revenue_comp = c["revenue_comp"]
        company_profile = self.results.get("company_profile") or {}
        biz_desc = company_profile.get('business', '')
        by_product = company_profile.get('revenue_by_product') or []

        if by_product:
            rows = []
            for item in by_product[:5]:
                name = str(item.get('name', '业务'))[:22]
                rev = str(item.get('revenue', 'N/A'))
                pct = str(item.get('percent', 'N/A'))
                rows.append(f"| {name:<22} │ {rev:>8} │ {pct:>6} |")
            revenue_comp_table = "\n".join(rows)
        elif revenue_comp.get('by_product'):
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

        missing_banner = self._build_missing_data_banner()
        missing_section = (missing_banner + "\n") if missing_banner else ""

        return f"""📈 {stock_name} ({title_ticker}) 投资研究报告

> 报告日期：{datetime.now().strftime('%Y年%m月%d日')} | 分析师：AI Analyst | 市场：{market_label} | 时效：本周内

{missing_section}🎯 操作建议

┌─────────┬──────────┬──────────┬──────────┬────────────┐
│  评级   │  当前价  │  目标价  │ 上行空间 │ 风险收益比 │
├─────────┼──────────┼──────────┼──────────┼────────────┤
│ {rating_icon} {rating} │ {current_price:.2f} {currency} │ {target_mean} │ {upside} │ {rr_str} {rr_icon} │
└─────────┴──────────┴──────────┴──────────┴────────────┘

> 核心投资逻辑：{investment_thesis if investment_thesis else '数据不足，无法生成投资论点'}

**核心业务构成**：

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

机构目标价：均值 {target_mean} | 综合评级：{consensus_rating} | 上涨空间 {upside}
"""

    # ── Section 1: 综合评价维度 ───────────────────────────────────────────────

    def _build_dimension_summary(self) -> str:
        r, c = self.r, self.composite
        macro_score = self.results.get("macro_score")
        gs_metrics = c["gs_metrics"]
        company_profile = self.results.get("company_profile") or {}

        market = self.market
        market_label = self.results["market_label"]
        price_change_display = self.results.get("price_change", "N/A")

        confidence = c["confidence"]
        confidence_detail = c["confidence_detail"]
        weekly_conclusion = c["weekly_conclusion"]

        # Macro
        macro_icon = "⚪"
        if macro_score and macro_score.total >= 60:
            macro_icon, macro_status = "🟢", "优秀"
        elif macro_score and macro_score.total >= 40:
            macro_icon, macro_status = "🟡", "良好"
        else:
            macro_icon, macro_status = "⚪", "中性"

        macro_interp = f"{market_label}{price_change_display}，政策面与风险并存" if price_change_display != "N/A" else "政策面与风险并存"
        # 若有 5日/区间涨跌幅则补充
        change_5d = self.results.get("change_5d", "N/A")
        if change_5d != "N/A":
            macro_interp += f"，5日{change_5d}"

        # Sector
        sector_score = macro_score.sector if macro_score else None
        if isinstance(sector_score, (int, float)):
            sector_pct = sector_score / 30 * 100
            if sector_pct >= 80:
                sector_status, sector_desc = "✅ 良好", "行业高景气"
            elif sector_pct >= 50:
                sector_status, sector_desc = "⚪ 中性", "行业景气中等"
            else:
                sector_status, sector_desc = "⚠️ 偏弱", "行业景气偏弱"
            sector_display = f"{sector_score}/30"
        else:
            sector_status, sector_desc, sector_display = "⚪ 中性", "行业数据待更新", "N/A"

        # Fundamental
        fundamental_icon = "⚪"
        if macro_score and macro_score.total >= 60:
            fundamental_icon, fundamental_status = "🟢", "优秀"
        elif macro_score and macro_score.total >= 40:
            fundamental_icon, fundamental_status = "🟡", "良好"
        else:
            fundamental_icon, fundamental_status = "⚪", "中性"

        roe_val = gs_metrics.get('roe', 'N/A')
        fy1_roe = gs_metrics.get('forecast_roe_fy1', 'N/A')
        if roe_val != 'N/A' and fy1_roe != 'N/A' and roe_val != fy1_roe:
            fundamental_interp = f"ROE {roe_val}偏低，但预测FY1 {fy1_roe}改善中"
        elif roe_val != 'N/A':
            fundamental_interp = f"ROE {roe_val}偏低拖累，但改善趋势明确"
        else:
            fundamental_interp = "ROE偏低拖累，但改善趋势明确"

        # Tech
        tech_icon = "🟢"
        tech_status = "买入"
        if r.sentiment_score >= 70:
            tech_icon, tech_status = "🟢", "买入"
        elif r.sentiment_score >= 40:
            tech_icon, tech_status = "⚪", "观望"
        else:
            tech_icon, tech_status = "🔴", "卖出"
        tech_trend = "空头" if tech_status == "卖出" else "多头" if tech_status == "买入" else "震荡"

        # RR
        rr_str = c["rr_str"]
        rr_icon = "✅" if c["rr"] and c["rr"] >= 2 else "⚠️"
        rr_status = "充足" if c["rr"] and c["rr"] >= 2 else "不足"
        upside_display = c["upside"] or "N/A"

        # Weekly
        weekly_aligned = "⚠️" not in weekly_conclusion
        weekly_icon = "✅" if weekly_aligned else "⚠️"
        weekly_status = "一致" if weekly_aligned else "分歧"

        # Confidence
        confidence_icon = "✅" if confidence >= 80 else "🟡" if confidence >= 60 else "⚠️"

        # Industry
        industry_pos = company_profile.get('industry_position', '行业前列')[:20]

        return f"""🔍 综合评价维度

一级维度总览

┌───────────────┬─────────┬─────────┬──────────────────────────────────────────────┐
│ 维度          │  得分   │  状态   │ 解读                                         │
├───────────────┼─────────┼─────────┼──────────────────────────────────────────────┤
│ 🌏 宏观环境   │  {macro_score.macro if macro_score else 'N/A'}/50  │ {macro_icon} {macro_status} │ {macro_interp}             │
├───────────────┼─────────┼─────────┼──────────────────────────────────────────────┤
│ 🏭 行业景气   │ {sector_display:>5}  │ {sector_status} │ {sector_desc}                         │
├───────────────┼─────────┼─────────┼──────────────────────────────────────────────┤
│ 🏢 个股基本面 │ {macro_score.total if macro_score else 'N/A'}/100  │ {fundamental_icon} {fundamental_status} │ {fundamental_interp}          │
├───────────────┼─────────┼─────────┼──────────────────────────────────────────────┤
│ 📉 技术面     │ {r.sentiment_score}/100  │ {tech_icon} {tech_status} │ {tech_status}信号，日线{tech_trend}趋势                         │
├───────────────┼─────────┼─────────┼──────────────────────────────────────────────┤
│ 💰 风险收益   │ {rr_str}  │ {rr_icon} {rr_status} │ 目标价上行{upside_display}，安全边际{'充足' if c['rr'] and c['rr'] >= 2 else '不足'}                │
├───────────────┼─────────┼─────────┼──────────────────────────────────────────────┤
│ ✅ 周线确认   │   {weekly_icon}    │  {weekly_status}   │ 日线{tech_status}+ 周线{'多头' if weekly_aligned else '空头'}，信号{'可信' if weekly_aligned else '存疑'}                │
├───────────────┼─────────┼─────────┼──────────────────────────────────────────────┤
│ 🎯 置信度     │ {confidence}/100 │   {confidence_icon}    │ {confidence_detail}                                   │
└───────────────┴─────────┴─────────┴──────────────────────────────────────────────┘
"""

    # ── Section 2: 基本面明细 ────────────────────────────────────────────────

    def _build_fundamental_detail(self) -> str:
        c = self.composite
        fundamental_scores = c["fundamental_scores"]
        confidence = c["confidence"]
        confidence_detail = c["confidence_detail"]
        confidence_icon = "✅" if confidence >= 80 else "🟡" if confidence >= 60 else "⚠️"

        def _fs(key: str, max_score: int) -> str:
            return f"{fundamental_scores[key]}/{max_score}"

        def _status(key: str) -> str:
            return fundamental_scores["_status"].get(key, "⚪ 中性")

        def _detail(key: str, fallback: str) -> str:
            return fundamental_scores["_detail"].get(key, fallback)

        return f"""个股基本面明细（动态评分）

┌────────────┬────────┬─────────┬────────────────────────────────────────────┐
│ 二级维度   │  得分  │  状态   │ 评分依据                                   │
├────────────┼────────┼─────────┼────────────────────────────────────────────┤
│ 商业模式   │ {_fs('商业模式', 25)}/25  │ {_status('商业模式')} │ {_detail('商业模式', '行业龙头')}              │
├────────────┼────────┼─────────┼────────────────────────────────────────────┤
│ 护城河     │ {_fs('护城河', 25)}/25  │ {_status('护城河')} │ {_detail('护城河', '市占率领先')} │
├────────────┼────────┼─────────┼────────────────────────────────────────────┤
│ 财务质量   │ {_fs('财务质量', 25)}/25  │ {_status('财务质量')} │ {_detail('财务质量', '数据待更新')}       │
├────────────┼────────┼─────────┼────────────────────────────────────────────┤
│ 管理层     │  {_fs('管理层', 10)}/10  │ {_status('管理层')} │ {_detail('管理层', '战略清晰')}                       │
├────────────┼────────┼─────────┼────────────────────────────────────────────┤
│ 个股消息面 │ {_fs('个股消息面', 15)}/15  │ {_status('个股消息面')} │ {_detail('个股消息面', '获订单')}     │
├────────────┼────────┼─────────┼────────────────────────────────────────────┤
│ 基本面合计 │ {_fs('基本面合计', 100)}/100 │ {_status('基本面合计')} │ 基本面扎实，成长性明确                     │
└────────────┴────────┴─────────┴────────────────────────────────────────────┘

置信度拆解：{confidence_detail} | 合计 {confidence}/100 {confidence_icon}
"""

    # ── Section 3: 财务预测 + GS 指标 ────────────────────────────────────────

    def _build_earnings_section(self) -> str:
        c = self.composite
        market = self.market
        currency = "港元" if market == "hk" else "元"
        gs_metrics = c["gs_metrics"]
        earnings_forecast = self.results.get("earnings_forecast") or {}

        # ROE
        roe_raw = gs_metrics.get('roe', 'N/A')
        fy1_roe = gs_metrics.get('forecast_roe_fy1', 'N/A')
        if roe_raw != 'N/A':
            try:
                _roe = float(roe_raw.replace('%', ''))
                if _roe < 5:
                    roe_interp = f"⚠️ 偏低，FY1预测 {fy1_roe}" if fy1_roe != 'N/A' else "⚠️ 偏低"
                elif _roe < 15:
                    roe_interp = f"🟡 适中，FY1预测 {fy1_roe}" if fy1_roe != 'N/A' else "🟡 适中"
                else:
                    roe_interp = f"🟢 优秀，FY1预测 {fy1_roe}" if fy1_roe != 'N/A' else "🟢 优秀"
            except:
                roe_interp = "数据待更新"
        else:
            roe_interp = "数据待更新"

        # Debt ratio
        debt_ratio_raw = gs_metrics.get('debt_ratio', 'N/A')
        debt_ratio_display = debt_ratio_raw if debt_ratio_raw != 'N/A' else 'N/A'
        try:
            _dr = float(debt_ratio_raw.replace('%', '')) if debt_ratio_raw != 'N/A' else None
            if _dr is not None and _dr <= 50:
                debt_ratio_interp = "🟢 合理，低于50%"
            elif _dr is not None and _dr <= 70:
                debt_ratio_interp = "🟡 偏高"
            elif _dr is not None:
                debt_ratio_interp = "🔴 高杠杆"
            else:
                debt_ratio_interp = "数据待更新"
        except:
            debt_ratio_interp = "数据待更新"

        # PE(TTM)
        pe_ttm = c["pe_ttm"]
        pe_ttm_display = pe_ttm if pe_ttm and pe_ttm != "N/A" else "N/A"
        try:
            _pe = float(pe_ttm) if pe_ttm and pe_ttm != "N/A" else None
            if _pe is not None and _pe <= 15:
                pe_interp = "🟢 估值偏低"
            elif _pe is not None and _pe <= 25:
                pe_interp = "🟡 估值适中"
            elif _pe is not None:
                pe_interp = "🔴 估值偏高"
            else:
                pe_interp = "数据待更新"
        except:
            pe_interp = "数据待更新"

        # PE(FY1)
        pe_fy1_val = gs_metrics.get('forecast_pe_fy1', 'N/A')
        if pe_fy1_val != 'N/A':
            try:
                _pf = float(pe_fy1_val)
                if _pf <= 15:
                    pe_fy1_interp = "🟢 极低估值"
                elif _pf <= 25:
                    pe_fy1_interp = "🟡 合理估值"
                else:
                    pe_fy1_interp = "🔴 估值偏高"
            except:
                pe_fy1_interp = "数据待更新"
        else:
            pe_fy1_interp = "数据待更新"

        # PEG
        peg_result = c["peg_result"]
        peg_display = peg_result['peg_str'] if peg_result['peg_str'] != "N/A" else "-"
        peg_interp = "🟢 合理" if peg_display != "-" else "数据待更新"
        if peg_display != "-":
            try:
                _peg = float(peg_display)
                if _peg < 0:
                    peg_interp = "⚠️ 负增长"
                elif _peg <= 1:
                    peg_interp = "🟢 低估"
                elif _peg <= 2:
                    peg_interp = "🟡 合理"
                else:
                    peg_interp = "🔴 偏高"
            except:
                pass

        # Beta — no hardcoded default, only show when actually retrieved
        beta_val = gs_metrics['beta']
        if beta_val != 'N/A':
            try:
                beta_num = float(beta_val)
                if beta_num > 1.5:
                    beta_interp = "🔵 高波动性（β>1.5）"
                elif beta_num > 1.0:
                    beta_interp = "🔵 中高波动（β>1.0）"
                else:
                    beta_interp = "🟢 低波动（β≤1.0）"
            except:
                beta_interp = "🔵 中高波动"
        else:
            beta_interp = "⚪ 待查询"

        # Market cap
        market_cap_display = self.results.get("market_cap", "N/A")
        if market_cap_display == '数据源限制':
            market_cap_display = 'N/A'
        try:
            _mcap = float(re.sub(r'[^\d.]', '', str(market_cap_display)))
            if _mcap >= 200:
                mcap_size = "大盘"
            elif _mcap >= 50:
                mcap_size = "中小盘"
            else:
                mcap_size = "小盘"
        except:
            mcap_size = self.results["market_label"]

        market_label = self.results["market_label"]

        # Earnings table rows — 取最近 3 年（保留历史 + 预测混合，最多 6 行中末 3 行）
        rows = []
        if earnings_forecast and earnings_forecast.get("years"):
            revenue = earnings_forecast.get('revenue', ['N/A']*6)
            profit = earnings_forecast.get('net_profit', ['N/A']*6)
            profit_growth = earnings_forecast.get('profit_growth', ['N/A']*6)
            eps_list = earnings_forecast.get('eps', ['N/A']*6)
            years = earnings_forecast.get("years", [])
            # 优先取末 3 行（2025A + 2026E + 2027E 或 2026E/2027E/2028E）
            display_years = years[-3:] if len(years) >= 3 else years
            gm_raw = earnings_forecast.get("gross_margin", "N/A")
            gm_val = gm_raw if gm_raw != "N/A" else "N/A"
            for i, year in zip(range(len(years) - len(display_years), len(years)), display_years):
                status = "预测" if 'E' in year else "历史"
                rv = revenue[i] if i < len(revenue) and revenue[i] != 'N/A' else 'N/A'
                pv = profit[i] if i < len(profit) and profit[i] != 'N/A' else 'N/A'
                ev = eps_list[i] if i < len(eps_list) and eps_list[i] != 'N/A' else 'N/A'
                gv = profit_growth[i] if i < len(profit_growth) and profit_growth[i] != 'N/A' else 'N/A'
                gm_cell = gm_val if gm_val != 'N/A' else 'N/A'
                rows.append(f"│ {year}  │   {status}   │ {rv:>8} │ {pv:>8} │     N/A ✅     │ {gm_cell:>6} │ {ev:>6} │ {gv:>8} │")
        else:
            for year, status in [("2025A", "历史"), ("2026E", "预测"), ("2027E", "预测")]:
                rows.append(f"│ {year}  │   {status}   │      N/A │      N/A │     N/A ✅     │    N/A │    N/A │      N/A │")

        # FCF footnote
        ocf = gs_metrics.get('operating_cashflow', 'N/A')
        fcf_val = gs_metrics.get('fcf', 'N/A')
        fcf_note = gs_metrics.get('fcf_note', '')
        fcf_footnote = ""
        if ocf != 'N/A':
            try:
                ocf_float = float(str(ocf).replace('亿', '').replace('B', ''))
                fcf_footnote = f"> 经营现金流估算：OCF(TTM) {ocf_float:.2f}亿{currency}"
                if fcf_val != 'N/A':
                    fcf_footnote += f" | FCF{fcf_note} {fcf_val}"
                fcf_footnote += "\n"
            except:
                if fcf_val != 'N/A':
                    fcf_footnote = f"> 自由现金流{fcf_note}：{fcf_val}\n"
        elif fcf_val != 'N/A':
            fcf_footnote = f"> 自由现金流{fcf_note}：{fcf_val}\n"

        peg_fy1 = gs_metrics.get('forecast_peg_fy1', peg_display)

        # ── Quarterly data from fetch_quarterly_data ──────────────────────────────
        qdata = self.results.get("quarterly_data") or {}
        q_label = qdata.get("quarter_label", "最新季度")
        q_rev = qdata.get("revenue_q")
        q_np = qdata.get("net_profit_q")
        q_rev_yoy = qdata.get("revenue_yoy_q")
        q_np_yoy = qdata.get("net_profit_yoy_q")
        q_gm = qdata.get("gross_margin") or self.results.get("gross_margin") or "N/A"

        def _qdisp(val: str | None) -> str:
            return f"{val} 亿元" if val else "N/A"

        def _qoyd(val: str | None) -> str:
            return val if val else "N/A"

        quarterly_rows = (
            f"│ 营收         │ {_qdisp(q_rev):>8} │ {_qoyd(q_rev_yoy):>8} │\n"
            f"│ 归母净利润   │ {_qdisp(q_np):>8} │ {_qoyd(q_np_yoy):>8} │\n"
            f"│ 毛利率       │      {q_gm:>6} │      N/A   │"
        )

        return f"""📊 财务预测与核心指标 (GS Data)

┌────────┬──────────┬──────────┬────────────┬────────────────┬────────┬─────────┬──────────┐
│ 报告期 │ 数据状态 │ 营收(亿{currency}) │ 净利(亿{currency})   │ 经营现金流     │ 毛利率 │ EPS({currency}) │ 利润增长 │
├────────┼──────────┼──────────┼────────────┼────────────────┼────────┼─────────┼──────────┤
{chr(10).join(rows)}
└────────┴──────────┴──────────┴────────────┴────────────────┴────────┴─────────┴──────────┘

**{q_label} 最新季度数据**：

┌──────────────┬──────────┬──────────┐
│ 指标         │   数值   │   同比   │
├──────────────┼──────────┼──────────┤
{quarterly_rows}
└──────────────┴──────────┴──────────┘

> 注：季度数据来自公司季报，请以实际披露为准
{fcf_footnote}高盛财务评价指标

┌─────────────────┬──────────┬──────────────────────┐
│ 指标            │   数值   │ 解读                 │
├─────────────────┼──────────┼──────────────────────┤
│ ROE             │ {gs_metrics['roe']:>8} │ {roe_interp} │
├─────────────────┼──────────┼──────────────────────┤
│ 资产负债率      │ {debt_ratio_display:>8} │ {debt_ratio_interp} │
├─────────────────┼──────────┼──────────────────────┤
│ PE(TTM)         │ {pe_ttm_display:>8} │ {pe_interp} │
├─────────────────┼──────────┼──────────────────────┤
│ PE(FY1)         │ {pe_fy1_val:>8} │ {pe_fy1_interp}             │
├─────────────────┼──────────┼──────────────────────┤
│ PEG(FY1)        │ {peg_fy1:>8} │ {peg_interp} │
├─────────────────┼──────────┼──────────────────────┤
│ Beta            │ {beta_val:>8} │ {beta_interp} │
├─────────────────┼──────────┼──────────────────────┤
│ 总市值          │ {market_cap_display:>8} │ {market_label}{mcap_size}              │
└─────────────────┴──────────┴──────────────────────┘
"""

    # ── Section 4: 行业对标 ─────────────────────────────────────────────────

    def _build_peer_comparison(self) -> str:
        r, market, ticker, c = self.r, self.market, self.ticker, self.composite
        gs_metrics = c["gs_metrics"]
        earnings_forecast = self.results.get("earnings_forecast") or {}
        company_profile = self.results.get("company_profile") or {}
        peers = c["peers"]
        peg_result = c["peg_result"]
        peg_display = peg_result['peg_str'] if peg_result['peg_str'] != "N/A" else "-"
        actual_pe = c["pe_ttm"] if c["pe_ttm"] and c["pe_ttm"] != "N/A" else "-"
        roe_display = gs_metrics.get('roe', 'N/A')

        industry_pos = company_profile.get('industry_position', '行业前列')[:20]
        biz_desc = company_profile.get('business', '') or ''

        growth_display = 'N/A'
        pg = earnings_forecast.get('profit_growth', [])
        for g in (pg or []):
            if g and g != 'N/A':
                growth_display = str(g)
                break

        market_cap_display = self.results.get("market_cap", "N/A")
        if market_cap_display == '数据源限制':
            market_cap_display = 'N/A'

        # Median PE from peers
        median_pe = "N/A"
        for p in peers:
            if "中位数" in str(p.get("name", "")):
                median_pe = str(p.get("pe", "N/A"))
                break

        # PE comparison icon
        pe_compare_icon = ""
        try:
            def parse_pe(val):
                if not val or val in ("N/A", "—", "-"):
                    return None
                if "-" in val:
                    try:
                        parts = val.split("-")
                        return (float(parts[0]) + float(parts[1])) / 2
                    except:
                        return None
                return float(str(val).replace("倍", ""))

            _cur_pe = float(str(actual_pe).replace("倍", "")) if actual_pe not in ("-", "N/A") else None
            _med_pe = parse_pe(median_pe)
            if _cur_pe is not None and _med_pe is not None:
                if _cur_pe <= _med_pe:
                    pe_compare_icon = "🟢 低PE — "
                elif _cur_pe > _med_pe * 1.3:
                    pe_compare_icon = "🔴 高PE — "
                else:
                    pe_compare_icon = "🟡 中PE — "
        except:
            pass

        current_advantage = f"{pe_compare_icon}{industry_pos}" if pe_compare_icon else industry_pos

        # Build peer rows
        peer_lines = []
        for p in peers:
            peer_lines.append(
                f"│ {str(p.get('name',''))[:8]:>8} │ {str(p.get('pe','N/A')):>5} │ "
                f"{str(p.get('peg','N/A')):>5} │ {str(p.get('roe','N/A'))[:8]:>8} │ "
                f"{str(p.get('mcap','N/A'))[:8]:>8} │ {str(p.get('growth','N/A'))[:8]:>8} │ "
                f"{str(p.get('note',''))[:32]} │"
            )

        stock_name = r.name or ticker
        peer_lines.append(
            f"│ {stock_name[:8]:>8} │ {str(actual_pe):>5} │ {str(peg_display):>5} │ "
            f"{str(roe_display)[:8]:>8} │ {str(market_cap_display)[:8]:>8} │ "
            f"{str(growth_display)[:8]:>8} │ {current_advantage[:32]} │"
        )

        # Peer insight
        peer_insight = ""
        try:
            _cur_pe = float(str(actual_pe).replace("倍", "")) if actual_pe not in ("-", "N/A") else None
            _med_pe = float(median_pe) if median_pe not in ("N/A", "—", "-") else None
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
                    try:
                        _p = float(peg_display)
                        peg_label = '极低' if _p < 0.5 else '偏低' if _p < 1 else '合理'
                        peer_insight += f"PEG {peg_display} {peg_label}。"
                    except:
                        pass
                if growth_display not in ("N/A", "-"):
                    peer_insight += f"利润增速 {growth_display} {'被市场低估' if pe_ratio < 1 else '需关注'}。"
                fy1_roe = gs_metrics.get('forecast_roe_fy1', 'N/A')
                if roe_display != 'N/A':
                    try:
                        _roe = float(roe_display.replace('%', ''))
                        peer_insight += f"ROE {roe_display} 是{'主要短板' if _roe < 5 else '待提升'}。"
                        if fy1_roe != 'N/A':
                            peer_insight += f"但 FY1 预测回升至 {fy1_roe}。"
                    except:
                        pass
        except:
            pass

        # Industry position
        _pos = company_profile.get('industry_position', '行业地位待更新') or '行业地位待更新'
        _customers = company_profile.get('key_customers', '') or ''
        customers_block = f"- **核心客户**：{_customers}" if _customers else ""

        # Concept tags — derive from revenue_by_product when available
        concept_tags = ""
        company_profile = self.results.get("company_profile") or {}
        by_product = company_profile.get("revenue_by_product") or []
        if by_product:
            tag_set = []
            for item in by_product[:4]:
                name = str(item.get("name", "")).strip()
                if name and name not in tag_set and name.lower() != "nan":
                    tag_set.append(name)
            # Add secondary tags based on industry/sector
            sector = company_profile.get("industry_sector", "") or ""
            biz_desc = company_profile.get("business", "") or ""
            if "锂" in sector or "锂" in biz_desc:
                for tag in ["锂矿", "锂盐", "新能源"]:
                    if tag not in tag_set:
                        tag_set.append(tag)
                        if len(tag_set) >= 6:
                            break
            if "民爆" in sector or "民爆" in biz_desc:
                for tag in ["民爆", "爆破工程"]:
                    if tag not in tag_set:
                        tag_set.append(tag)
                        if len(tag_set) >= 6:
                            break
            concept_tags = " | ".join(tag_set[:6])
        if not concept_tags:
            if market == "hk":
                concept_tags = "仓储机器人 | AMR解决方案 | 智慧物流 | AI机器人 | 港股"
            elif market == "us":
                concept_tags = "科技股 | AI | 机器人 | 自动化"
            else:
                concept_tags = "机器人 | 人工智能 | 智慧物流 | 自动化"

        return f"""📐 行业对标与估值

┌──────────┬───────┬───────┬──────────┬──────────┬──────────┬──────────────────────────────────┐
│ 股票     │  P/E  │  PEG  │   ROE    │ 总市值   │ 利润增速 │ 优势                             │
├──────────┼───────┼───────┼──────────┼──────────┼──────────┼──────────────────────────────────┤
{chr(10).join(peer_lines)}
└──────────┴───────┴───────┴──────────┴──────────┴──────────┴──────────────────────────────────┘
{peer_insight}

**行业地位**：{_pos}

{customers_block}

**概念标签**：{concept_tags}

> 注：行业地位数据来自公司公告和市场研究，请以实际披露为准
"""

    # ── Section 5: 实时行情 + 技术指标 ───────────────────────────────────────

    def _build_realtime_section(self) -> str:
        r = self.r
        market = self.market
        current_price = self.results.get("current_price")
        currency = "港元" if market == "hk" else "元"
        price_change = self.results.get("price_change", "N/A")
        change_5d = self.results.get("change_5d", "N/A")
        market_cap = self.results.get("market_cap", "N/A")
        ti = self.results.get("tech_indicators") or getattr(r, '_latest_tech_data', {}) or {}
        ma5 = ti.get('ma5') or ti.get('col_1')
        ma20 = ti.get('ma20') or ti.get('col_2')
        rsi_val = ti.get('rsi') or ti.get('col_5')
        macd_diff = ti.get('macd_diff') or ti.get('col_3')
        macd_dea = ti.get('macd_dea') or ti.get('col_4')
        macd_hist = ti.get('macd_histogram')

        cp = current_price or 0
        ma5_str = f"{ma5:.2f}" if ma5 else "N/A"
        ma20_str = f"{ma20:.2f}" if ma20 else "N/A"
        rsi_str = f"{rsi_val:.2f}" if rsi_val else "N/A"
        macd_golden = (macd_diff > macd_dea) if (macd_diff is not None and macd_dea is not None) else None

        ma5_interp = "价格 > MA5 ✅" if ma5 and cp > ma5 else "价格 < MA5 ⚠️" if ma5 else "N/A"
        ma20_interp = "价格 > MA20 ✅" if ma20 and cp > ma20 else "价格 < MA20 ⚠️" if ma20 else "N/A"
        if rsi_val and rsi_val > 70:
            rsi_interp = "🔴 超买"
        elif rsi_val and rsi_val > 60:
            rsi_interp = "🟡 接近超买"
        elif rsi_val:
            rsi_interp = "🟢 正常"
        else:
            rsi_interp = "N/A"

        macd_signal = "N/A"
        if macd_diff is not None and macd_dea is not None:
            hist_str = f"{macd_hist:.4f}" if macd_hist is not None else "N/A"
            macd_signal = f"DIFF={macd_diff:.3f} DEA={macd_dea:.3f} 柱={hist_str}"

        current_price_str = f"{current_price:.2f} {currency}" if current_price else "数据源限制"
        if market_cap == '数据源限制':
            market_cap = 'N/A'

        tech_sentiment_icon = "🔴 短期偏空" if r.sentiment_score < 40 else "🟡 中性" if r.sentiment_score < 60 else "🟢 偏多"
        macd_interp = ('🟢 金叉' if macd_golden else '🔴 死叉') if macd_golden is not None else 'N/A'

        return f"""📊 实时行情

| 日期 | 最新价 | 涨跌幅 | 5日涨幅 | 总市值 |
|------|--------|--------|---------|--------|
| {datetime.now().strftime('%m-%d %H:%M')} | {current_price_str} | {price_change} | {change_5d} | {market_cap} |

📊 关键技术指标

| 指标 | 值 | 解读 |
|------|-----|------|
| 技术分 | {r.sentiment_score}/100 | {tech_sentiment_icon} |
| MA5 | {ma5_str} | {ma5_interp} |
| MA20 | {ma20_str} | {ma20_interp} |
| RSI | {rsi_str} | {rsi_interp} |
| MACD | {macd_signal} | {macd_interp} |
"""

    # ── Section 6: 风险评估 ─────────────────────────────────────────────────

    def _build_risk_section(self) -> str:
        r, market = self.r, self.market
        gs_metrics = self.composite["gs_metrics"]
        roe_val = gs_metrics.get('roe', 'N/A')
        fy1_roe = gs_metrics.get('forecast_roe_fy1', 'N/A')
        market_cap_display = self.results.get("market_cap", "N/A")

        try:
            _mcap = float(re.sub(r'[^\d.]', '', str(market_cap_display)))
            mcap_size = "大盘" if _mcap >= 200 else "中小盘" if _mcap >= 50 else "小盘"
        except:
            mcap_size = self.results["market_label"]

        sentiment_icon = "🔴 高" if r.sentiment_score < 40 else "🟡 中"

        # Fix 6: Dynamic liquidity/fund risk text based on market cap size + market
        if market == "hk":
            liquidity_text = "港股通成交偏淡，做空机制存在风险"
            fund_text = "南向资金波动影响股价"
        elif mcap_size == "大盘":
            liquidity_text = "大盘蓝筹，流动性好，机构持仓稳定"
            fund_text = "人民币汇率波动对营收有一定影响"
        elif mcap_size == "中小盘":
            liquidity_text = "中小盘股，成交相对清淡，机构持仓比例有限"
            fund_text = "美元收入占比高，汇兑损失风险"
        else:
            liquidity_text = "小盘股，流动性偏低，换手率波动较大"
            fund_text = "营收结构待确认，关注汇率敞口"

        return f"""⚠️ 风险评估

┌──────────────────────────────┬────────┬───────┬──────────────────────────────────────────────────────┐
│ 风险                         │ 可能性 │ 影响  │ 详情                                               │
├──────────────────────────────┼────────┼───────┼──────────────────────────────────────────────────────┤
│ 技术面偏空                   │ {sentiment_icon} │ 🟡 中 │ 技术分{r.sentiment_score}，短期承压                       │
├──────────────────────────────┼────────┼───────┼──────────────────────────────────────────────────────┤
│ ROE偏低                     │ 🟡 中  │ 🟡 中 │ {roe_val}低于行业，FY1预测{fy1_roe}有改善                │
├──────────────────────────────┼────────┼───────┼──────────────────────────────────────────────────────┤
│ {mcap_size}流动性     │ 🟡 中  │ 🟡 中 │ {liquidity_text}         │
├──────────────────────────────┼────────┼───────┼──────────────────────────────────────────────────────┤
│ {'港股通资金' if market == 'hk' else '汇率波动'} │ 🟡 中  │ 🟡 中 │ {fund_text}           │
├──────────────────────────────┼────────┼───────┼──────────────────────────────────────────────────────┤
│ 高管减持压力                 │ 🟡 中  │ 🟡 中 │ 董监高减持计划执行中，需关注抛压释放节奏              │
├──────────────────────────────┼────────┼───────┼──────────────────────────────────────────────────────┤
│ 新业务处于投入期             │ 🟡 中  │ 🟡 中 │ 机器人/液冷业务尚未量产，研发费用持续投入              │
└──────────────────────────────┴────────┴───────┴──────────────────────────────────────────────────────┘
"""

    # ── Section 7: 催化剂 + 关键事件 ─────────────────────────────────────────

    def _build_catalysts_section(self) -> str:
        r, c = self.r, self.composite
        catalysts_list = c["catalysts_list"]
        news_text = self.results.get("news_text", "")
        consensus_rating = self.results.get("consensus_rating", "") or ""
        analyst_target = self.results.get("analyst_target", "") or ""

        # Build catalyst rows
        catalyst_rows = ""
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

            cat_display = ReportGenerator._pad_to_width(ReportGenerator._truncate_to_width(cat, 26), 26)
            timing_display = ReportGenerator._pad_to_width(timing, 10)
            catalyst_rows += f"│ {cat_display} │ {ReportGenerator._pad_to_width(impact, 7):7} │ {timing_display} │ {detail[:44]:44} │\n"
            catalyst_rows += "├──────────────────────────────┼─────────┼────────────┼──────────────────────────────────────────────────────┤\n"

        if catalyst_rows.endswith("├──────────────────────────────┼─────────┼────────────┼──────────────────────────────────────────────────────┤\n"):
            catalyst_rows = catalyst_rows.rstrip()
            catalyst_rows = catalyst_rows[:catalyst_rows.rfind("\n") + 1]

        # Key events from news
        key_events = []
        for line in news_text.splitlines():
            date_match = re.search(r"(20\d{2}[-.]\d{2}[-.]\d{2})", line)
            if not date_match:
                continue
            if any(kw in line for kw in ("评级", "研报", "增持", "减持", "买入", "卖出", "推荐", "目标价")):
                event_date = date_match.group(1).replace(".", "-")
                desc = line.strip().lstrip("- 0123456789.").strip()[:60]
                if desc:
                    key_events.append(f"- {event_date}：{desc}")
                if len(key_events) >= 3:
                    break

        if key_events:
            key_events_str = "\n".join(key_events)
        elif consensus_rating and analyst_target:
            key_events_str = f"- 机构评级：{consensus_rating} | {analyst_target[:50]}"
        else:
            key_events_str = "- 暂无近期关键事件"

        return f"""📰 短期催化剂

┌──────────────────────────────┬─────────┬────────────┬──────────────────────────────────────────────────────┐
│ 催化剂                       │  影响   │   时间     │ 详情                                               │
├──────────────────────────────┼─────────┼────────────┼──────────────────────────────────────────────────────┤
{catalyst_rows}└──────────────────────────────┴─────────┴────────────┴──────────────────────────────────────────────────────┘

**关键事件**：

{key_events_str}
"""

    # ── Section 8: 今日操作建议 ───────────────────────────────────────────────

    def _build_trade_section(self) -> str:
        r, c = self.r, self.composite
        market = self.market
        currency = "港元" if market == "hk" else "元"
        sl = c["sl"]
        tp = c["tp"]
        rating = c["rating"]
        target_price_num = c["target_price_num"]
        upside = c["upside"] or "N/A"
        current_price = self.results.get("current_price") or 0
        investment_thesis = c["investment_thesis"]

        # Stop loss percentage
        try:
            sl_float = float(sl) if sl and sl != "N/A" else 0
            if sl_float and current_price:
                stop_loss_pct = f"-{abs((current_price - sl_float) / current_price * 100):.1f}%"
            else:
                stop_loss_pct = "-5.1%"
        except:
            stop_loss_pct = "-5.1%"

        target_mean = f"{target_price_num:.2f}{currency}" if target_price_num else "N/A"

        # Trade action
        if rating in ("买入", "增持"):
            trade_action = "🟢 逢低吸纳，当前价可小仓建仓" if rating == "买入" else "🔵 分批建仓，等待回调加仓"
        elif rating == "持有":
            trade_action = "🟡 持有观望，等待方向选择"
        elif rating == "减持":
            trade_action = "🟠 减仓观望，等待反转信号"
        else:
            trade_action = "🔴 清仓离场，等待企稳"

        # Trade logic (2 lines)
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

        # Fix 7: Dynamic long-term thresholds based on current price and target price
        if target_price_num and current_price and target_price_num > current_price:
            # Discount entry: buy when price is N% below target
            discount_pct = (target_price_num - current_price) / target_price_num * 100
            # PE at entry price
            pe_ttm = self.composite.get("pe_ttm", "")
            try:
                pe_num = float(str(pe_ttm).replace("倍", "")) if pe_ttm not in ("N/A", "") else None
                pe_entry = f"PE≈{pe_num:.0f}x" if pe_num else "PE待确认"
            except:
                pe_entry = "PE待确认"
            long_term_entry = f"目标价以下{discount_pct:.0f}%建仓（{pe_entry}）"
        else:
            long_term_entry = "目标价附近或以下建仓，关注估值合理性"

        return f"""🎯 今日操作建议

┌──────────┬──────────────────────────────────────────┐
│ 操作     │ {trade_action}                            │
├──────────┼──────────────────────────────────────────┤
│ 仓位     │ 不超过总仓 10%，分 2-3 批入场            │
├──────────┼──────────────────────────────────────────┤
│ 止损线   │ {sl} {currency}（{stop_loss_pct}）                       │
├──────────┼──────────────────────────────────────────┤
│ 目标价   │ {target_mean}（{upside}，机构一致预期）        │
├──────────┼──────────────────────────────────────────┤
│ 逻辑     │ {trade_logic} │
│          │ {trade_logic_line2} │
└──────────┴──────────────────────────────────────────┘

**中长线投资建议**：

┌──────────┬──────────────────────────────────────────┐
│ 操作     │ 条件                                     │
├──────────┼──────────────────────────────────────────┤
│ 等待回调 │ {long_term_entry}              │
│ 分批建仓 │ 跌破关键支撑位时分批加仓                  │
│ 减仓     │ 突破关键阻力位且无业绩支撑时减仓          │
└──────────┴──────────────────────────────────────────┘

⚠️ 免责声明：本报告由 AI 生成，仅供参考，不构成投资建议。

📊 数据说明：实时行情/机构目标价/财务数据/一致预期来自 mx-data。技术面来自 daily_stock_analysis。基本面明细为动态评分。

报告生成时间：{datetime.now().strftime('%Y-%m-%d')} | 数据截止：实时
"""

    # ═══════════════════════════════════════════════════════════════════════════════
    # Public method — delegates to focused section builders
    # ═══════════════════════════════════════════════════════════════════════════════

    def generate_markdown_report(self) -> str:
        """Generate Markdown report following the report_template.md format.

        Returns the complete investment research report as a string.
        """
        parts = [
            self._build_header(),
            self._build_dimension_summary(),
            self._build_fundamental_detail(),
            self._build_earnings_section(),
            self._build_peer_comparison(),
            self._build_realtime_section(),
            self._build_risk_section(),
            self._build_catalysts_section(),
            self._build_trade_section(),
        ]
        return "\n".join(parts)

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

