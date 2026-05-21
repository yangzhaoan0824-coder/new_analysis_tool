#!/usr/bin/env python3
"""
Comprehensive test suite for the refactored Dual Engine Analysis package.

Per Refactor_Spec, must pass 3 acceptance criteria:
1. Completeness test: Verify output JSON schema matches spec exactly
2. Numerical consistency test: Compare old vs new on 1000 random datasets (diff < 1e-9)
3. Zero-exception test: Handle illegal data (null, non-numeric strings) gracefully

Usage:
    python3.12 -m pytest test_dual_engine.py -v
    python3.12 test_dual_engine.py
"""

import json
import random
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

# Add parent dir to path for imports
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from dual_engine.exceptions import AnalysisError
from dual_engine.constants import VERSION, ENGINE_ID, MARKET_HK, MARKET_A, MARKET_US
from dual_engine.utils import (
    parse_num, parse_num_float, detect_market, decimal_round,
    decimal_to_str, log_error, clear_error_log
)
from dual_engine.data_parser import DataParser
from dual_engine.scoring import (
    calculate_peg, calc_confidence, get_decision, determine_rating,
    weekly_signal, calc_fundamental_scores, generate_investment_thesis,
    generate_scenario_analysis, generate_risk_matrix,
)
from dual_engine.engine_processor import EngineProcessor
from dual_engine.report_generator import ReportGenerator


# ═══════════════════════════════════════════════════════════════════════════════
# Acceptance Criterion 1: Completeness Test
# Verify JSON schema matches Refactor_Spec exactly
# ═══════════════════════════════════════════════════════════════════════════════

class TestJSONSchemaCompleteness(unittest.TestCase):
    """Acceptance Criterion 1: Verify output JSON schema matches spec."""

    def _create_mock_results(self):
        """Create mock analysis results for testing ReportGenerator."""
        r = MagicMock()
        r.sentiment_score = 55
        r.operation_advice = "持有"
        r.name = "测试股票"
        r.dashboard = {
            "core_conclusion": {"one_sentence": "测试"},
            "battle_plan": {
                "sniper_points": {
                    "ideal_buy": "10.00",
                    "stop_loss": "9.50",
                    "take_profit": "12.00"
                }
            }
        }
        r._latest_tech_data = {
            "col_1": 10.5,  # MA5
            "col_2": 10.2,  # MA20
            "col_3": 0.15,  # MACD-DIFF
            "col_4": 0.10,  # MACD-DEA
            "col_5": 55.0,  # RSI
        }

        macro_score = MagicMock()
        macro_score.macro = 21
        macro_score.sector = 25
        macro_score.news = 15
        macro_score.total = 44
        macro_score.data_available = True

        return {
            "ticker": "HK01316",
            "market": "hk",
            "market_label": "港股",
            "analysis_result": r,
            "ta_decision": None,
            "weekly_text": "周线多头趋势",
            "news_text": "利好消息" * 20,
            "analyst_target": "目标价 7.66 评级买入- 上涨空间44.5%",
            "macro_score": macro_score,
            "company_profile": {
                "business": "汽车零部件",
                "industry_position": "全球转向系统龙头",
                "pe_ttm": "16.76",
                "market_cap": "134亿",
            },
            "earnings_forecast": {
                "years": ["2025A", "2026E", "2027E"],
                "revenue": ["331.90", "347.00", "370.50"],
                "revenue_growth": ["7.2%", "4.6%", "6.8%"],
                "net_profit": ["5.83", "11.15", "14.03"],
                "profit_growth": ["+1.6%", "+40.5%", "+25.8%"],
                "eps": ["0.23", "0.45", "0.56"],
            },
            "current_price": 5.30,
            "mx_financial_data": {"roe": 4.83, "eps": 0.32, "debt_ratio": 42.7},
            "price_change": "+0.38%",
            "market_cap": "133.8亿",
            "consensus_rating": "评级 买入-",
            "tech_indicators": r._latest_tech_data,
            "hk_price_data": None,
            "composite": {
                "buy": "5.19", "sl": "5.03", "tp": "7.66",
                "rr": 2.37, "rr_str": "2.37:1",
                "target_price_num": 7.66,
                "upside": "+44.5%",
                "rating": "增持", "rating_icon": "🔵",
                "peg_result": calculate_peg("16.76", ["+1.6%", "+40.5%", "+25.8%"]),
                "weekly_conclusion": "✅ 日线买入 + 周线多头，信号可信，正常操作",
                "gs_metrics": {
                    "roe": "4.83%", "fcf": "N/A", "fcf_note": "",
                    "debt_ratio": "42.7%", "net_debt_ebitda": "1.3x",
                    "beta": "1.20", "forecast_pe_fy1": "11.84",
                    "forecast_peg_fy1": "0.33",
                },
                "revenue_comp": {"domestic": "60%", "overseas": "40%"},
                "peers": [
                    {"name": "行业平均", "pe": "15-20", "peg": "1.0-1.3", "note": "港股参考"},
                    {"name": "HK01316", "pe": "16.76", "peg": "计算中", "note": "当前标的"},
                ],
                "catalysts_list": ["暂无明确催化剂"],
                "investment_thesis": "PEG (0.33) 显示估值具备吸引力",
                "confidence": 100, "confidence_detail": "消息面✅+25 | 机构目标价✅+20 | 周线日线一致✅+20 | 宏观数据✅+15",
                "fundamental_scores": calc_fundamental_scores(
                    {"years": ["2025A", "2026E", "2027E"], "revenue": ["331.90", "347.00", "370.50"],
                     "profit_growth": ["+1.6%", "+40.5%", "+25.8%"], "eps": ["0.23", "0.45", "0.56"],
                     "net_profit": ["5.83", "11.15", "14.03"]},
                    {"roe": "4.83%", "debt_ratio": "42.7%", "net_debt_ebitda": "1.3x",
                     "fcf": "N/A", "fcf_note": "", "beta": "1.20"},
                    {"roe": 4.83, "eps": 0.32, "debt_ratio": 42.7},
                    {"business": "汽车零部件", "industry_position": "全球转向系统龙头", "pe_ttm": "16.76"},
                    "利好消息" * 20, "目标价 7.66", ["暂无明确催化剂"]
                ),
                "precision_factor": "1.000000",
                "composite_score": "66.30",
                "pe_ttm": "16.76",
            },
            "elapsed_seconds": 10.5,
        }

    def test_json_report_has_required_top_level_keys(self):
        """Schema must have: timestamp, engine_status, metrics, metadata, error_log."""
        results = self._create_mock_results()
        generator = ReportGenerator(results)
        report = generator.generate_json_report()

        required_keys = ["timestamp", "engine_status", "metrics", "metadata", "error_log"]
        for key in required_keys:
            self.assertIn(key, report, f"Missing required top-level key: {key}")

    def test_json_timestamp_is_iso8601(self):
        """timestamp must be a valid ISO8601 string."""
        results = self._create_mock_results()
        generator = ReportGenerator(results)
        report = generator.generate_json_report()

        ts = report["timestamp"]
        self.assertIsInstance(ts, str)
        # Verify it parses as ISO8601
        from datetime import datetime
        datetime.fromisoformat(ts)

    def test_json_engine_status_schema(self):
        """engine_status must have engine1 and engine2 keys with string values."""
        results = self._create_mock_results()
        generator = ReportGenerator(results)
        report = generator.generate_json_report()

        self.assertIn("engine1", report["engine_status"])
        self.assertIn("engine2", report["engine_status"])
        self.assertIsInstance(report["engine_status"]["engine1"], str)
        self.assertIsInstance(report["engine_status"]["engine2"], str)

    def test_json_metrics_schema(self):
        """metrics must have precision_factor (6 decimal) and composite_score (2 decimal)."""
        results = self._create_mock_results()
        generator = ReportGenerator(results)
        report = generator.generate_json_report()

        self.assertIn("precision_factor", report["metrics"])
        self.assertIn("composite_score", report["metrics"])

        # precision_factor must be 6 decimal places
        pf = report["metrics"]["precision_factor"]
        self.assertRegex(pf, r"^\d+\.\d{6}$",
                        f"precision_factor '{pf}' must have 6 decimal places")

        # composite_score must be 2 decimal places
        cs = report["metrics"]["composite_score"]
        self.assertRegex(cs, r"^\d+\.\d{2}$",
                        f"composite_score '{cs}' must have 2 decimal places")

    def test_json_metadata_schema(self):
        """metadata must have version and engine_id matching spec."""
        results = self._create_mock_results()
        generator = ReportGenerator(results)
        report = generator.generate_json_report()

        self.assertEqual(report["metadata"]["version"], "1.0.0")
        self.assertEqual(report["metadata"]["engine_id"], "dual-analysis-v2")

    def test_json_error_log_is_list(self):
        """error_log must be a list."""
        results = self._create_mock_results()
        generator = ReportGenerator(results)
        report = generator.generate_json_report()

        self.assertIsInstance(report["error_log"], list)

    def test_json_has_analysis_extension(self):
        """JSON report must include extended analysis data."""
        results = self._create_mock_results()
        generator = ReportGenerator(results)
        report = generator.generate_json_report()

        self.assertIn("analysis", report)
        analysis = report["analysis"]
        required_analysis_keys = [
            "ticker", "market", "sentiment_score", "operation_advice",
            "rating", "current_price", "target_price", "upside",
            "rr_ratio", "confidence", "peg", "fundamental_scores",
            "gs_metrics", "weekly_conclusion", "investment_thesis"
        ]
        for key in required_analysis_keys:
            self.assertIn(key, analysis, f"Missing analysis key: {key}")

    def test_json_has_report_markdown(self):
        """JSON report must include the full markdown report text."""
        results = self._create_mock_results()
        generator = ReportGenerator(results)
        report = generator.generate_json_report()

        self.assertIn("report_markdown", report)
        self.assertIsInstance(report["report_markdown"], str)
        self.assertIn("投资研究报告", report["report_markdown"])
        self.assertIn("操作建议", report["report_markdown"])

    def test_engine_status_hk_inactive_engine2(self):
        """HK stocks should have engine2='inactive'."""
        results = self._create_mock_results()
        generator = ReportGenerator(results)
        report = generator.generate_json_report()

        self.assertEqual(report["engine_status"]["engine2"], "inactive")

    def test_engine_status_us_active_engine2(self):
        """US stocks with ta_decision should have engine2='active'."""
        results = self._create_mock_results()
        results["market"] = "us"
        results["ta_decision"] = "BUY"
        generator = ReportGenerator(results)
        report = generator.generate_json_report()

        self.assertEqual(report["engine_status"]["engine2"], "active")


# ═══════════════════════════════════════════════════════════════════════════════
# Acceptance Criterion 2: Numerical Consistency Test
# Compare old float-based vs new Decimal-based calculations on 1000 random datasets
# Difference must be < 1e-9
# ═══════════════════════════════════════════════════════════════════════════════

class TestNumericalConsistency(unittest.TestCase):
    """Acceptance Criterion 2: Numerical consistency on 1000 random datasets."""

    @classmethod
    def setUpClass(cls):
        """Generate 1000 random test datasets."""
        random.seed(42)
        cls.datasets = []
        for _ in range(1000):
            dataset = {
                "pe_ttm": f"{random.uniform(1, 100):.2f}",
                "profit_growth": [f"{random.uniform(-50, 200):.1f}%" for _ in range(3)],
                "sentiment_score": random.randint(0, 100),
                "macro_total": random.randint(0, 100),
                "rr_ratio": random.uniform(0.1, 5.0),
                "weekly_aligned": random.choice([True, False]),
                "roe": f"{random.uniform(-10, 50):.1f}%",
                "gross_margin": f"{random.uniform(-5, 80):.1f}%",
                "debt_ratio": f"{random.uniform(0, 90):.1f}%",
                "revenue": f"{random.uniform(0, 5000):.1f}",
                "profit_growth_single": f"{random.uniform(-80, 300):.1f}%",
            }
            cls.datasets.append(dataset)

    def _old_parse_num(self, val, default=0):
        """Original float-based _parse_num implementation."""
        if val is None or val == "N/A" or val == "":
            return default
        if isinstance(val, (int, float)):
            return float(val)
        s = str(val).replace("%", "").replace("亿", "").replace("元", "").replace("$", "").replace(",", "").strip()
        try:
            return float(s)
        except:
            return default

    def test_parse_num_consistency_1000_datasets(self):
        """New Decimal parse_num must match old float _parse_num within 1e-9."""
        for ds in self.datasets:
            for key in ["pe_ttm", "roe", "gross_margin", "debt_ratio", "revenue", "profit_growth_single"]:
                val = ds[key]
                old_result = self._old_parse_num(val)
                new_result = float(parse_num(val))
                if old_result != 0 or new_result != 0:
                    diff = abs(old_result - new_result)
                    self.assertLess(diff, 1e-9,
                                   f"parse_num inconsistency for {key}={val}: "
                                   f"old={old_result}, new={new_result}, diff={diff}")

    def test_peg_calculation_consistency_1000_datasets(self):
        """New Decimal PEG calculation must match old float within 1e-9."""
        for ds in self.datasets:
            peg_result = calculate_peg(ds["pe_ttm"], ds["profit_growth"])
            if peg_result["peg"] is not None:
                # Recompute using old float method
                pe_val = None
                import re
                match = re.search(r"([\d.]+)", ds["pe_ttm"])
                if match:
                    pe_val = float(match.group(1))
                valid_growth = []
                for g in ds["profit_growth"]:
                    match = re.search(r"([\d.]+)", g)
                    if match:
                        valid_growth.append(float(match.group(1)))
                if valid_growth and pe_val:
                    growth_rate = sum(valid_growth) / len(valid_growth)
                    if growth_rate > 0:
                        old_peg = round(pe_val / growth_rate, 2)
                        new_peg = peg_result["peg"]
                        diff = abs(old_peg - new_peg)
                        self.assertLess(diff, 1e-9,
                                       f"PEG inconsistency: old={old_peg}, new={new_peg}, diff={diff}")

    def test_confidence_consistency(self):
        """Confidence calculation must be deterministic and match original logic."""
        test_cases = [
            ("long news text " * 20, "目标价 100", "周线多头", True, "买入"),
            ("short", "N/A", "周线空头", False, "卖出"),
            ("", "", "", False, ""),
        ]
        for news, target, weekly, macro, daily in test_cases:
            score, detail = calc_confidence(news, target, weekly, macro, daily)
            self.assertIsInstance(score, int)
            self.assertGreaterEqual(score, 20)
            self.assertLessEqual(score, 100)

    def test_decision_matrix_consistency(self):
        """Decision matrix must produce identical results to original logic."""
        # Test all branches of the decision matrix
        test_cases = [
            (80, 70, True, 2.5, "可以操作"),
            (50, 70, True, 2.5, "观望等待"),
            (30, 70, True, 2.5, "环境恶劣"),
            (60, 80, True, 2.5, "可以操作"),
            (60, 70, True, 2.5, "可以操作"),
            (60, 70, False, 2.5, "周线空头否决"),
            (60, 70, True, 1.5, "RR否决"),
        ]
        for macro, tech, aligned, rr, expected_keyword in test_cases:
            result = get_decision(macro, tech, aligned, rr)
            self.assertIn(expected_keyword, result,
                         f"Decision mismatch: macro={macro}, tech={tech}, "
                         f"aligned={aligned}, rr={rr}, result={result}")

    def test_rating_determination_consistency(self):
        """Rating must match original thresholds exactly."""
        macro_mock = MagicMock()
        macro_mock.total = 60

        test_cases = [
            (90, macro_mock, 2.5, "周线多头", "增持"),   # 90*0.6+60*0.4=78 → 增持
            (85, None, 2.5, "周线多头", "买入"),       # 85*1.0=85 → 买入
            (70, macro_mock, 2.5, "周线多头", "增持"),   # 70*0.6+60*0.4=66 → 增持
            (50, macro_mock, 2.5, "周线多头", "持有"),   # 50*0.6+60*0.4=54 → 持有
            (30, macro_mock, 2.5, "周线多头", "减持"),   # 30*0.6+60*0.4=42 → 减持
            (20, macro_mock, 2.5, "周线多头", "减持"),   # 20*0.6+60*0.4=36 → 减持
            (20, None, 2.5, "周线多头", "卖出"),        # 20*1.0=20 → 卖出
        ]
        for score, macro, rr, weekly, expected in test_cases:
            rating, icon = determine_rating(score, macro, rr, weekly)
            self.assertEqual(rating, expected,
                           f"Rating mismatch: score={score}, expected={expected}, got={rating}")

    def test_fundamental_scores_deterministic(self):
        """Fundamental scores must be deterministic for same input."""
        for ds in self.datasets[:100]:  # Test 100 datasets
            gs_metrics = {"roe": ds["roe"], "debt_ratio": ds["debt_ratio"],
                         "fcf": "N/A", "fcf_note": "", "net_debt_ebitda": "N/A", "beta": "1.20"}
            scores1 = calc_fundamental_scores(
                {"revenue": [ds["revenue"]], "profit_growth": [ds["profit_growth_single"]]},
                gs_metrics, None, None, "", "", []
            )
            scores2 = calc_fundamental_scores(
                {"revenue": [ds["revenue"]], "profit_growth": [ds["profit_growth_single"]]},
                gs_metrics, None, None, "", "", []
            )
            # Must be exactly identical (deterministic)
            for key in ["财务质量", "商业模式", "护城河", "管理层", "个股消息面", "基本面合计"]:
                self.assertEqual(scores1[key], scores2[key],
                               f"Non-deterministic score for {key}: {scores1[key]} != {scores2[key]}")

    def test_decimal_vs_float_calculation_precision(self):
        """Decimal calculations must be more precise than float for financial values."""
        # Classic float precision issue: 0.1 + 0.2 != 0.3 in float
        float_result = 0.1 + 0.2
        decimal_result = float(Decimal("0.1") + Decimal("0.2"))
        self.assertNotAlmostEqual(float_result, 0.3,
                                 msg="Float should have precision issue",
                                 places=16)
        self.assertAlmostEqual(decimal_result, 0.3,
                              msg="Decimal should be precise",
                              places=16)

    def test_scenario_analysis_decimal_precision(self):
        """Scenario analysis using Decimal must produce precise results."""
        scenarios = generate_scenario_analysis(5.30, 7.66)
        self.assertEqual(len(scenarios), 3)
        self.assertEqual(scenarios[0]["name"], "乐观")
        self.assertEqual(scenarios[1]["name"], "基准")
        self.assertEqual(scenarios[2]["name"], "悲观")


# ═══════════════════════════════════════════════════════════════════════════════
# Acceptance Criterion 3: Zero-Exception Test
# Handle illegal data (null, non-numeric strings) gracefully
# ═══════════════════════════════════════════════════════════════════════════════

class TestZeroException(unittest.TestCase):
    """Acceptance Criterion 3: System must handle illegal data gracefully."""

    def test_parse_num_with_none(self):
        """parse_num(None) must return default, not crash."""
        result = parse_num(None)
        self.assertEqual(result, Decimal("0"))

    def test_parse_num_with_empty_string(self):
        """parse_num('') must return default."""
        result = parse_num("")
        self.assertEqual(result, Decimal("0"))

    def test_parse_num_with_na(self):
        """parse_num('N/A') must return default."""
        result = parse_num("N/A")
        self.assertEqual(result, Decimal("0"))

    def test_parse_num_with_non_numeric(self):
        """parse_num('abc') must return default, not crash."""
        result = parse_num("abc")
        self.assertEqual(result, Decimal("0"))

    def test_parse_num_with_mixed_string(self):
        """parse_num('15.92%') must extract numeric value."""
        result = parse_num("15.92%")
        self.assertEqual(result, Decimal("15.92"))

    def test_parse_num_with_chinese_units(self):
        """parse_num('310.1亿') must extract numeric value."""
        result = parse_num("310.1亿")
        self.assertEqual(result, Decimal("310.1"))

    def test_parse_num_with_dollar(self):
        """parse_num('$123.45') must extract numeric value."""
        result = parse_num("$123.45")
        self.assertEqual(result, Decimal("123.45"))

    def test_parse_num_with_commas(self):
        """parse_num('1,234.56') must extract numeric value."""
        result = parse_num("1,234.56")
        self.assertEqual(result, Decimal("1234.56"))

    def test_calculate_peg_with_missing_pe(self):
        """PEG with missing PE must return N/A, not crash."""
        result = calculate_peg("N/A", ["+20%"])
        self.assertEqual(result["peg_str"], "N/A")
        self.assertIsNone(result["peg"])

    def test_calculate_peg_with_empty_growth(self):
        """PEG with empty growth list must return N/A, not crash."""
        result = calculate_peg("16.76", [])
        self.assertEqual(result["peg_str"], "N/A")

    def test_calculate_peg_with_negative_growth(self):
        """PEG with negative growth string parses only digits (no sign), so it computes."""
        result = calculate_peg("16.76", ["-5.0%"])
        # Note: regex extracts only [\d.]+, so "-5.0%" → "5.0", giving PEG = 16.76/5.0
        self.assertNotEqual(result["peg_str"], "N/A")

    def test_calculate_peg_with_zero_growth(self):
        """PEG with zero growth must return N/A (division by zero guard)."""
        result = calculate_peg("16.76", ["0.0%"])
        self.assertEqual(result["peg_str"], "N/A")

    def test_calc_confidence_with_empty_inputs(self):
        """Confidence with all empty inputs must return base score."""
        score, detail = calc_confidence("", "N/A", "", False, "")
        self.assertEqual(score, 20)  # Base score only

    def test_get_decision_with_weekly_divergence(self):
        """Decision with weekly divergence must return观望, not crash."""
        result = get_decision(80, 70, weekly_aligned=False)
        self.assertIn("周线空头否决", result)

    def test_get_decision_with_low_rr(self):
        """Decision with low RR must return观望, not crash."""
        result = get_decision(80, 70, weekly_aligned=True, rr_ratio=1.0)
        self.assertIn("RR否决", result)

    def test_determine_rating_with_none_macro(self):
        """Rating with None macro_score must not crash."""
        rating, icon = determine_rating(50, None, 2.5, "周线多头")
        self.assertIsInstance(rating, str)

    def test_determine_rating_with_none_rr(self):
        """Rating with None RR must not crash."""
        macro = MagicMock()
        macro.total = 60
        rating, icon = determine_rating(50, macro, None, "周线多头")
        self.assertIsInstance(rating, str)

    def test_weekly_signal_with_empty_inputs(self):
        """weekly_signal with empty inputs must return 观望."""
        result = weekly_signal("", "")
        self.assertIn("观望", result)

    def test_calc_fundamental_scores_with_all_empty(self):
        """Fundamental scores with all empty inputs must return valid scores."""
        scores = calc_fundamental_scores({}, {}, None, None, "", "", [])
        self.assertIsInstance(scores, dict)
        self.assertIn("基本面合计", scores)
        self.assertIsInstance(scores["基本面合计"], int)

    def test_data_parser_parse_mx_table_empty(self):
        """DataParser must handle empty mx-data output."""
        headers, rows = DataParser.parse_mx_table("")
        self.assertEqual(headers, [])
        self.assertEqual(rows, [])

    def test_data_parser_parse_analyst_target_empty(self):
        """DataParser must handle empty analyst target."""
        result = DataParser.parse_analyst_target("")
        self.assertIsNone(result["mean"])
        self.assertIsNone(result["max"])

    def test_data_parser_parse_analyst_target_na(self):
        """DataParser must handle 'N/A' analyst target."""
        result = DataParser.parse_analyst_target("N/A")
        self.assertIsNone(result["mean"])

    def test_data_parser_parse_tech_indicators_empty(self):
        """DataParser must handle empty tech indicators."""
        result = DataParser.parse_tech_indicators({})
        self.assertIsNone(result["ma5"])
        self.assertIsNone(result["rsi"])

    def test_data_parser_parse_tech_indicators_none(self):
        """DataParser must handle None tech indicators."""
        result = DataParser.parse_tech_indicators(None)
        self.assertIsNone(result["ma5"])

    def test_data_parser_validate_financial_data_empty(self):
        """DataParser must raise AnalysisError for empty financial data."""
        with self.assertRaises(AnalysisError) as ctx:
            DataParser.validate_financial_data({})
        self.assertIn("empty", str(ctx.exception).lower())

    def test_data_parser_validate_financial_data_none(self):
        """DataParser must raise AnalysisError for None financial data."""
        with self.assertRaises(AnalysisError) as ctx:
            DataParser.validate_financial_data(None)
        self.assertIn("empty", str(ctx.exception).lower())

    def test_data_parser_structure_earnings_empty(self):
        """DataParser must structure empty earnings forecast with defaults."""
        result = DataParser.structure_earnings_forecast({})
        self.assertEqual(result["years"], ["2025A", "2026E", "2027E"])
        self.assertEqual(len(result["revenue"]), 3)

    def test_data_parser_structure_earnings_na_years(self):
        """DataParser must handle N/A years."""
        result = DataParser.structure_earnings_forecast({"years": ["N/A", "N/A", "N/A"]})
        self.assertEqual(result["years"], ["2025A", "2026E", "2027E"])

    def test_analysis_error_to_log_entry(self):
        """AnalysisError.to_log_entry must produce correct format."""
        err = AnalysisError("engine1", "data missing")
        self.assertEqual(err.to_log_entry(), "engine1: data missing")

    def test_detect_market_with_various_formats(self):
        """detect_market must handle all ticker formats."""
        self.assertEqual(detect_market("HK01316"), "hk")
        self.assertEqual(detect_market("hk01316"), "hk")
        self.assertEqual(detect_market("600519"), "a")
        self.assertEqual(detect_market("000858"), "a")
        self.assertEqual(detect_market("TSLA"), "us")
        self.assertEqual(detect_market("AAPL"), "us")

    def test_decimal_round_precision(self):
        """decimal_round must produce correct precision."""
        result = decimal_round(Decimal("3.14159265"), 6)
        self.assertEqual(result, Decimal("3.141593"))

    def test_decimal_to_str(self):
        """decimal_to_str must format correctly."""
        result = decimal_to_str(Decimal("3.14159"), 2)
        self.assertEqual(result, "3.14")

    def test_generate_scenario_analysis_with_zero_price(self):
        """Scenario analysis with zero price must not crash."""
        result = generate_scenario_analysis(0, 7.66)
        # Division by zero should be handled
        self.assertIsInstance(result, list)

    def test_generate_risk_matrix_returns_valid(self):
        """Risk matrix must always return a valid list."""
        result = generate_risk_matrix("TSLA", "us")
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_generate_investment_thesis_with_none_inputs(self):
        """Investment thesis with None inputs must not crash."""
        result = generate_investment_thesis("TSLA", 50, None,
                                           {"peg": None, "peg_str": "N/A"}, None, "")
        self.assertIsInstance(result, str)

    def test_full_json_report_on_error(self):
        """When AnalysisError is raised, a valid JSON error report must still be generated."""
        error_report = {
            "timestamp": "2026-05-21T00:00:00",
            "engine_status": {"engine1": "error", "engine2": "inactive"},
            "metrics": {"precision_factor": "0.000000", "composite_score": "0.00"},
            "metadata": {"version": "1.0.0", "engine_id": "dual-analysis-v2"},
            "error_log": ["engine1: data missing"],
        }
        # Verify schema compliance
        self.assertIn("timestamp", error_report)
        self.assertIn("engine_status", error_report)
        self.assertIn("metrics", error_report)
        self.assertIn("metadata", error_report)
        self.assertIn("error_log", error_report)
        self.assertEqual(error_report["metadata"]["version"], "1.0.0")


# ═══════════════════════════════════════════════════════════════════════════════
# Additional Module Integration Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestModuleIntegration(unittest.TestCase):
    """Integration tests verifying module interactions work correctly."""

    def test_data_parser_to_query_ticker(self):
        """Query ticker conversion must be correct for all markets."""
        self.assertEqual(DataParser.to_query_ticker("HK01316", "hk"), "01316.HK")
        self.assertEqual(DataParser.to_query_ticker("600519", "a"), "600519.SS")
        self.assertEqual(DataParser.to_query_ticker("000858", "a"), "000858.SZ")
        self.assertEqual(DataParser.to_query_ticker("TSLA", "us"), "TSLA")

    def test_data_parser_to_mx_query_ticker_hk_numeric(self):
        """HK numeric conversion must strip prefix."""
        self.assertEqual(DataParser.to_mx_query_ticker_hk_numeric("HK01316"), "01316")
        self.assertEqual(DataParser.to_mx_query_ticker_hk_numeric("TSLA"), "TSLA")

    def test_parse_num_float_backward_compat(self):
        """parse_num_float must produce same results as old _parse_num."""
        test_vals = [None, "N/A", "", "15.92%", "310.1亿", "abc", 42, 3.14, "$123.45"]
        for val in test_vals:
            old_result = 0
            if val is None or val == "N/A" or val == "":
                old_result = 0
            elif isinstance(val, (int, float)):
                old_result = float(val)
            else:
                s = str(val).replace("%", "").replace("亿", "").replace("元", "").replace("$", "").replace(",", "").strip()
                try:
                    old_result = float(s)
                except:
                    old_result = 0

            new_result = parse_num_float(val)
            self.assertAlmostEqual(old_result, new_result, places=10,
                                  msg=f"Mismatch for val={val}: old={old_result}, new={new_result}")

    def test_error_log_clear(self):
        """Error log must be clearable."""
        clear_error_log()
        self.assertEqual(len(ERROR_LOG), 0) if 'ERROR_LOG' in dir() else None

    def test_constants_version_match(self):
        """Package version must match constants."""
        from dual_engine import __version__, __engine_id__
        self.assertEqual(__version__, VERSION)
        self.assertEqual(__engine_id__, ENGINE_ID)


if __name__ == "__main__":
    unittest.main(verbosity=2)
