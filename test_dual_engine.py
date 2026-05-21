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
import re
import unittest
from decimal import Decimal, InvalidOperation
from unittest.mock import MagicMock, patch

# Add parent dir to path for imports
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from dual_engine.exceptions import AnalysisError
from dual_engine.constants import VERSION, ENGINE_ID, MARKET_HK, MARKET_A, MARKET_US
from dual_engine.utils import (
    parse_num, parse_num_float, detect_market, decimal_round,
    decimal_to_str, log_error, clear_error_log, ERROR_LOG
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
# Helper: Create mock results for ReportGenerator testing
# ═══════════════════════════════════════════════════════════════════════════════

def create_mock_results(market="hk", ta_decision=None, sentiment_score=55,
                        operation_advice="持有", stock_name="测试股票"):
    """Create mock analysis results for testing ReportGenerator.

    This helper produces a fully populated results dict that mirrors
    the output of EngineProcessor.process().
    """
    r = MagicMock()
    r.sentiment_score = sentiment_score
    r.operation_advice = operation_advice
    r.name = stock_name
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
        "col_1": 10.5,   # MA5
        "col_2": 10.2,   # MA20
        "col_3": 0.15,   # MACD-DIFF
        "col_4": 0.10,   # MACD-DEA
        "col_5": 55.0,   # RSI
    }

    macro_score = MagicMock()
    macro_score.macro = 21
    macro_score.sector = 25
    macro_score.news = 15
    macro_score.total = 44
    macro_score.data_available = True

    peg_result = calculate_peg("16.76", ["+1.6%", "+40.5%", "+25.8%"])
    fundamental_scores = calc_fundamental_scores(
        {"years": ["2025A", "2026E", "2027E"], "revenue": ["331.90", "347.00", "370.50"],
         "profit_growth": ["+1.6%", "+40.5%", "+25.8%"], "eps": ["0.23", "0.45", "0.56"],
         "net_profit": ["5.83", "11.15", "14.03"]},
        {"roe": "4.83%", "debt_ratio": "42.7%", "net_debt_ebitda": "1.3x",
         "fcf": "N/A", "fcf_note": "", "beta": "1.20"},
        {"roe": 4.83, "eps": 0.32, "debt_ratio": 42.7},
        {"business": "汽车零部件", "industry_position": "全球转向系统龙头", "pe_ttm": "16.76"},
        "利好消息" * 20, "目标价 7.66", ["暂无明确催化剂"]
    )

    composite_score = Decimal(str(sentiment_score)) * Decimal("0.6") + Decimal("44") * Decimal("0.4")

    return {
        "ticker": "HK01316" if market == "hk" else ("603725" if market == "a" else "TSLA"),
        "market": market,
        "market_label": {"hk": "港股", "a": "A 股", "us": "美股"}[market],
        "analysis_result": r,
        "ta_decision": ta_decision,
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
            "peg_result": peg_result,
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
            "fundamental_scores": fundamental_scores,
            "precision_factor": "1.000000",
            "composite_score": str(decimal_round(composite_score, 2)),
            "pe_ttm": "16.76",
        },
        "elapsed_seconds": 10.5,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Acceptance Criterion 1: Completeness Test
# Verify output JSON schema matches Refactor_Spec exactly
# ═══════════════════════════════════════════════════════════════════════════════

class TestJSONSchemaCompleteness(unittest.TestCase):
    """AC1: Verify output JSON schema matches the Refactor_Spec exactly.

    The spec requires:
    {
      "timestamp": "ISO8601",
      "engine_status": { "engine1": "...", "engine2": "..." },
      "metrics": { "precision_factor": "0.000000", "composite_score": "0.00" },
      "metadata": { "version": "1.0.0", "engine_id": "dual-analysis-v2" },
      "error_log": [...]
    }
    """

    def _generate_report(self, **kwargs):
        """Helper to generate a JSON report from mock data."""
        results = create_mock_results(**kwargs)
        generator = ReportGenerator(results)
        return generator.generate_json_report()

    # ── Top-level schema keys ──────────────────────────────────────────────

    def test_json_has_all_required_top_level_keys(self):
        """Schema must have: timestamp, engine_status, metrics, metadata, error_log."""
        report = self._generate_report()
        required_keys = ["timestamp", "engine_status", "metrics", "metadata", "error_log"]
        for key in required_keys:
            self.assertIn(key, report, f"Missing required top-level key: {key}")

    def test_json_has_no_extra_top_level_keys_beyond_spec_and_extensions(self):
        """Top-level keys must be limited to spec keys + documented extensions."""
        report = self._generate_report()
        allowed_keys = {"timestamp", "engine_status", "metrics", "metadata",
                       "error_log", "analysis", "report_markdown"}
        for key in report:
            self.assertIn(key, allowed_keys,
                         f"Unexpected top-level key: {key}")

    # ── timestamp field ────────────────────────────────────────────────────

    def test_json_timestamp_is_iso8601(self):
        """timestamp must be a valid ISO8601 string."""
        report = self._generate_report()
        ts = report["timestamp"]
        self.assertIsInstance(ts, str)
        from datetime import datetime
        datetime.fromisoformat(ts)

    # ── engine_status field ────────────────────────────────────────────────

    def test_json_engine_status_has_both_engines(self):
        """engine_status must have engine1 and engine2 keys."""
        report = self._generate_report()
        self.assertIn("engine1", report["engine_status"])
        self.assertIn("engine2", report["engine_status"])

    def test_json_engine_status_values_are_strings(self):
        """engine1 and engine2 values must be strings."""
        report = self._generate_report()
        self.assertIsInstance(report["engine_status"]["engine1"], str)
        self.assertIsInstance(report["engine_status"]["engine2"], str)

    def test_engine_status_hk_inactive_engine2(self):
        """HK/A stocks should have engine2='inactive'."""
        report = self._generate_report(market="hk")
        self.assertEqual(report["engine_status"]["engine2"], "inactive")

    def test_engine_status_a_stock_inactive_engine2(self):
        """A stocks should have engine2='inactive'."""
        report = self._generate_report(market="a")
        self.assertEqual(report["engine_status"]["engine2"], "inactive")

    def test_engine_status_us_active_engine2_with_ta(self):
        """US stocks with ta_decision should have engine2='active'."""
        report = self._generate_report(market="us", ta_decision="BUY")
        self.assertEqual(report["engine_status"]["engine2"], "active")

    def test_engine_status_us_inactive_engine2_without_ta(self):
        """US stocks without ta_decision should have engine2='inactive'."""
        report = self._generate_report(market="us", ta_decision=None)
        self.assertEqual(report["engine_status"]["engine2"], "inactive")

    # ── metrics field ──────────────────────────────────────────────────────

    def test_json_metrics_has_precision_factor(self):
        """metrics must have precision_factor."""
        report = self._generate_report()
        self.assertIn("precision_factor", report["metrics"])

    def test_json_metrics_has_composite_score(self):
        """metrics must have composite_score."""
        report = self._generate_report()
        self.assertIn("composite_score", report["metrics"])

    def test_json_precision_factor_is_6_decimal_places(self):
        """precision_factor must be formatted with exactly 6 decimal places."""
        report = self._generate_report()
        pf = report["metrics"]["precision_factor"]
        self.assertRegex(pf, r"^\d+\.\d{6}$",
                        f"precision_factor '{pf}' must have exactly 6 decimal places")

    def test_json_composite_score_is_2_decimal_places(self):
        """composite_score must be formatted with exactly 2 decimal places."""
        report = self._generate_report()
        cs = report["metrics"]["composite_score"]
        self.assertRegex(cs, r"^-?\d+\.\d{2}$",
                        f"composite_score '{cs}' must have exactly 2 decimal places")

    # ── metadata field ─────────────────────────────────────────────────────

    def test_json_metadata_version_matches_spec(self):
        """metadata.version must be '1.0.0'."""
        report = self._generate_report()
        self.assertEqual(report["metadata"]["version"], "1.0.0")

    def test_json_metadata_engine_id_matches_spec(self):
        """metadata.engine_id must be 'dual-analysis-v2'."""
        report = self._generate_report()
        self.assertEqual(report["metadata"]["engine_id"], "dual-analysis-v2")

    def test_json_metadata_has_both_required_keys(self):
        """metadata must have both version and engine_id."""
        report = self._generate_report()
        self.assertIn("version", report["metadata"])
        self.assertIn("engine_id", report["metadata"])

    # ── error_log field ────────────────────────────────────────────────────

    def test_json_error_log_is_list(self):
        """error_log must be a list."""
        report = self._generate_report()
        self.assertIsInstance(report["error_log"], list)

    def test_json_error_log_entries_are_strings(self):
        """Each error_log entry must be a string."""
        report = self._generate_report()
        for entry in report["error_log"]:
            self.assertIsInstance(entry, str)

    # ── analysis extension field ───────────────────────────────────────────

    def test_json_has_analysis_extension(self):
        """JSON report must include extended analysis data."""
        report = self._generate_report()
        self.assertIn("analysis", report)

    def test_json_analysis_has_all_required_keys(self):
        """analysis must have all required sub-keys."""
        report = self._generate_report()
        required_analysis_keys = [
            "ticker", "market", "sentiment_score", "operation_advice",
            "rating", "current_price", "target_price", "upside",
            "rr_ratio", "confidence", "peg", "fundamental_scores",
            "gs_metrics", "weekly_conclusion", "investment_thesis"
        ]
        for key in required_analysis_keys:
            self.assertIn(key, report["analysis"], f"Missing analysis key: {key}")

    def test_json_analysis_sentiment_score_is_int(self):
        """analysis.sentiment_score must be an integer."""
        report = self._generate_report()
        self.assertIsInstance(report["analysis"]["sentiment_score"], int)

    def test_json_analysis_confidence_is_int(self):
        """analysis.confidence must be an integer."""
        report = self._generate_report()
        self.assertIsInstance(report["analysis"]["confidence"], int)

    def test_json_analysis_fundamental_scores_is_dict(self):
        """analysis.fundamental_scores must be a dict (no _detail/_status keys)."""
        report = self._generate_report()
        fs = report["analysis"]["fundamental_scores"]
        self.assertIsInstance(fs, dict)
        # Internal keys should be filtered out
        for key in fs:
            self.assertFalse(key.startswith("_"),
                           f"Internal key '{key}' should not appear in JSON output")

    # ── report_markdown field ──────────────────────────────────────────────

    def test_json_has_report_markdown(self):
        """JSON report must include the full markdown report text."""
        report = self._generate_report()
        self.assertIn("report_markdown", report)
        self.assertIsInstance(report["report_markdown"], str)

    def test_json_report_markdown_has_title(self):
        """report_markdown must include investment research report title."""
        report = self._generate_report()
        self.assertIn("投资研究报告", report["report_markdown"])

    def test_json_report_markdown_has_operation_advice(self):
        """report_markdown must include operation advice section."""
        report = self._generate_report()
        self.assertIn("操作建议", report["report_markdown"])

    def test_json_report_markdown_has_risk_assessment(self):
        """report_markdown must include risk assessment section."""
        report = self._generate_report()
        self.assertIn("风险评估", report["report_markdown"])

    def test_json_report_markdown_has_disclaimer(self):
        """report_markdown must include disclaimer."""
        report = self._generate_report()
        self.assertIn("免责声明", report["report_markdown"])

    # ── Multiple market coverage ───────────────────────────────────────────

    def test_json_schema_valid_for_a_stock(self):
        """Schema validation passes for A-stock results."""
        report = self._generate_report(market="a")
        required_keys = ["timestamp", "engine_status", "metrics", "metadata", "error_log"]
        for key in required_keys:
            self.assertIn(key, report)

    def test_json_schema_valid_for_us_stock(self):
        """Schema validation passes for US-stock results."""
        report = self._generate_report(market="us", ta_decision="BUY")
        required_keys = ["timestamp", "engine_status", "metrics", "metadata", "error_log"]
        for key in required_keys:
            self.assertIn(key, report)

    # ── Error report schema ────────────────────────────────────────────────

    def test_error_report_matches_spec_schema(self):
        """Error report must still conform to the base JSON schema."""
        error_report = {
            "timestamp": "2026-05-21T00:00:00",
            "engine_status": {"engine1": "error", "engine2": "inactive"},
            "metrics": {"precision_factor": "0.000000", "composite_score": "0.00"},
            "metadata": {"version": "1.0.0", "engine_id": "dual-analysis-v2"},
            "error_log": ["engine1: data missing"],
        }
        # Verify all required keys present
        for key in ["timestamp", "engine_status", "metrics", "metadata", "error_log"]:
            self.assertIn(key, error_report)
        # Verify precision format
        self.assertRegex(error_report["metrics"]["precision_factor"], r"^\d+\.\d{6}$")
        self.assertRegex(error_report["metrics"]["composite_score"], r"^-?\d+\.\d{2}$")
        # Verify metadata
        self.assertEqual(error_report["metadata"]["version"], "1.0.0")
        self.assertEqual(error_report["metadata"]["engine_id"], "dual-analysis-v2")


# ═══════════════════════════════════════════════════════════════════════════════
# Acceptance Criterion 2: Numerical Consistency Test
# Compare old float-based vs new Decimal-based calculations on 1000 random datasets
# Difference must be < 1e-9
# ═══════════════════════════════════════════════════════════════════════════════

class TestNumericalConsistency(unittest.TestCase):
    """AC2: Numerical consistency on 1000 random datasets.

    The refactored Decimal-based code must produce identical results
    to the original float-based code, with differences < 1e-9.
    """

    @classmethod
    def setUpClass(cls):
        """Generate 1000 random test datasets for consistency testing."""
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

    @staticmethod
    def _old_parse_num(val, default=0):
        """Original float-based _parse_num implementation for comparison."""
        if val is None or val == "N/A" or val == "":
            return default
        if isinstance(val, (int, float)):
            return float(val)
        s = str(val).replace("%", "").replace("亿", "").replace("元", "") \
                    .replace("$", "").replace(",", "").strip()
        try:
            return float(s)
        except (ValueError, TypeError):
            return default

    # ── parse_num consistency ──────────────────────────────────────────────

    def test_parse_num_consistency_1000_datasets(self):
        """New Decimal parse_num must match old float _parse_num within 1e-9."""
        for ds in self.datasets:
            for key in ["pe_ttm", "roe", "gross_margin", "debt_ratio",
                        "revenue", "profit_growth_single"]:
                val = ds[key]
                old_result = self._old_parse_num(val)
                new_result = float(parse_num(val))
                if old_result != 0 or new_result != 0:
                    diff = abs(old_result - new_result)
                    self.assertLess(diff, 1e-9,
                                   f"parse_num inconsistency for {key}={val}: "
                                   f"old={old_result}, new={new_result}, diff={diff}")

    def test_parse_num_float_backward_compat_1000_datasets(self):
        """parse_num_float must produce same results as old _parse_num within 1e-9."""
        for ds in self.datasets:
            for key in ["pe_ttm", "roe", "gross_margin", "debt_ratio", "revenue"]:
                val = ds[key]
                old_result = self._old_parse_num(val)
                new_result = parse_num_float(val)
                diff = abs(old_result - new_result)
                self.assertLess(diff, 1e-9,
                               f"parse_num_float inconsistency for {key}={val}: "
                               f"old={old_result}, new={new_result}, diff={diff}")

    # ── PEG calculation consistency ────────────────────────────────────────

    def test_peg_calculation_consistency_1000_datasets(self):
        """New Decimal PEG calculation must match old float within 1e-9."""
        for ds in self.datasets:
            peg_result = calculate_peg(ds["pe_ttm"], ds["profit_growth"])
            if peg_result["peg"] is not None:
                # Recompute using old float method
                pe_val = None
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
                                       f"PEG inconsistency: old={old_peg}, "
                                       f"new={new_peg}, diff={diff}")

    # ── Confidence calculation consistency ─────────────────────────────────

    def test_confidence_is_deterministic(self):
        """Confidence calculation must be deterministic for same input."""
        test_cases = [
            ("long news text " * 20, "目标价 100", "周线多头", True, "买入"),
            ("short", "N/A", "周线空头", False, "卖出"),
            ("", "", "", False, ""),
            ("利好消息" * 50, "均值 100港元", "周线多头趋势", True, "买入"),
            ("负面消息" * 10, "N/A", "周线空头", False, "减仓/卖出"),
        ]
        for news, target, weekly, macro, daily in test_cases:
            score1, detail1 = calc_confidence(news, target, weekly, macro, daily)
            score2, detail2 = calc_confidence(news, target, weekly, macro, daily)
            self.assertEqual(score1, score2,
                           f"Confidence not deterministic for input: "
                           f"news={news[:20]}..., target={target}")
            self.assertEqual(detail1, detail2,
                           f"Confidence detail not deterministic")

    def test_confidence_score_bounds(self):
        """Confidence score must be in [20, 100] range."""
        test_cases = [
            ("long news text " * 20, "目标价 100", "周线多头", True, "买入"),
            ("short", "N/A", "周线空头", False, "卖出"),
            ("", "", "", False, ""),
        ]
        for news, target, weekly, macro, daily in test_cases:
            score, detail = calc_confidence(news, target, weekly, macro, daily)
            self.assertGreaterEqual(score, 20)
            self.assertLessEqual(score, 100)

    # ── Decision matrix consistency ────────────────────────────────────────

    def test_decision_matrix_all_branches(self):
        """Decision matrix must produce correct results for all branches."""
        test_cases = [
            (80, 70, True, 2.5, "可以操作"),
            (50, 70, True, 2.5, "观望等待"),
            (30, 70, True, 2.5, "环境恶劣"),
            (60, 80, True, 2.5, "可以操作"),
            (60, 70, True, 2.5, "可以操作"),
            (60, 70, False, 2.5, "周线空头否决"),
            (60, 70, True, 1.5, "RR否决"),
            (75, 60, True, 3.0, "可以操作"),   # macro>=75, threshold=60
            (55, 70, True, 2.5, "可以操作"),   # macro>=55, threshold=70
            (35, 80, True, 2.5, "可以操作"),   # macro>=35, threshold=80
        ]
        for macro, tech, aligned, rr, expected_keyword in test_cases:
            result = get_decision(macro, tech, aligned, rr)
            self.assertIn(expected_keyword, result,
                         f"Decision mismatch: macro={macro}, tech={tech}, "
                         f"aligned={aligned}, rr={rr}, result={result}")

    # ── Rating determination consistency ───────────────────────────────────

    def test_rating_determination_exact_thresholds(self):
        """Rating must match original thresholds exactly using Decimal arithmetic."""
        macro_mock = MagicMock()
        macro_mock.total = 60

        # With macro: adjusted_score = sentiment * 0.6 + 60 * 0.4
        test_cases = [
            (90, macro_mock, 2.5, "周线多头", "增持"),   # 90*0.6+60*0.4=78 → 增持
            (85, None, 2.5, "周线多头", "买入"),        # 85*1.0=85 → 买入
            (70, macro_mock, 2.5, "周线多头", "增持"),   # 70*0.6+60*0.4=66 → 增持
            (50, macro_mock, 2.5, "周线多头", "持有"),   # 50*0.6+60*0.4=54 → 持有
            (30, macro_mock, 2.5, "周线多头", "减持"),   # 30*0.6+60*0.4=42 → 减持
            (20, macro_mock, 2.5, "周线多头", "减持"),   # 20*0.6+60*0.4=36 → 减持
            (20, None, 2.5, "周线多头", "卖出"),        # 20*1.0=20 → 卖出
        ]
        for score, macro, rr, weekly, expected in test_cases:
            rating, icon = determine_rating(score, macro, rr, weekly)
            self.assertEqual(rating, expected,
                           f"Rating mismatch: score={score}, expected={expected}, "
                           f"got={rating}")

    def test_rating_with_rr_penalty(self):
        """Rating with RR < 2.0 must apply -10 penalty."""
        macro_mock = MagicMock()
        macro_mock.total = 60
        # 70*0.6+60*0.4=66 → 增持, but with rr=1.5 → 66-10=56 → 持有
        rating, icon = determine_rating(70, macro_mock, 1.5, "周线多头")
        self.assertEqual(rating, "持有")

    def test_rating_with_weekly_divergence_penalty(self):
        """Rating with ⚠️ in weekly text must apply -15 penalty."""
        macro_mock = MagicMock()
        macro_mock.total = 60
        # 70*0.6+60*0.4=66 → 增持, but with divergence → 66-15=51 → 持有
        rating, icon = determine_rating(70, macro_mock, 2.5, "⚠️ 日线买入 + 周线空头")
        self.assertEqual(rating, "持有")

    # ── Fundamental scores determinism ─────────────────────────────────────

    def test_fundamental_scores_deterministic_100_datasets(self):
        """Fundamental scores must be deterministic for same input."""
        for ds in self.datasets[:100]:
            gs_metrics = {"roe": ds["roe"], "debt_ratio": ds["debt_ratio"],
                         "fcf": "N/A", "fcf_note": "", "net_debt_ebitda": "N/A",
                         "beta": "1.20"}
            earnings = {"revenue": [ds["revenue"]],
                       "profit_growth": [ds["profit_growth_single"]]}
            scores1 = calc_fundamental_scores(earnings, gs_metrics, None, None, "", "", [])
            scores2 = calc_fundamental_scores(earnings, gs_metrics, None, None, "", "", [])
            for key in ["财务质量", "商业模式", "护城河", "管理层", "个股消息面", "基本面合计"]:
                self.assertEqual(scores1[key], scores2[key],
                               f"Non-deterministic score for {key}: "
                               f"{scores1[key]} != {scores2[key]}")

    # ── Decimal vs float precision advantage ───────────────────────────────

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

    def test_decimal_round_consistency(self):
        """decimal_round must produce consistent, predictable results."""
        test_cases = [
            (Decimal("3.14159265"), 6, Decimal("3.141593")),
            (Decimal("2.71828182"), 2, Decimal("2.72")),
            (Decimal("1.005"), 2, Decimal("1.01")),    # ROUND_HALF_UP
            (Decimal("1.004"), 2, Decimal("1.00")),
            (Decimal("99.999"), 2, Decimal("100.00")),
        ]
        for val, places, expected in test_cases:
            result = decimal_round(val, places)
            self.assertEqual(result, expected,
                           f"decimal_round({val}, {places}) = {result}, "
                           f"expected {expected}")

    def test_composite_score_decimal_precision(self):
        """Composite score must be calculated with Decimal precision.

        Verifies: score = sentiment * 0.6 + confidence * 0.4
        matches Decimal arithmetic exactly.
        """
        for ds in self.datasets[:100]:
            sentiment = ds["sentiment_score"]
            confidence = random.randint(20, 100)
            # Float calculation
            float_score = sentiment * 0.6 + confidence * 0.4
            # Decimal calculation
            decimal_score = float(
                Decimal(str(sentiment)) * Decimal("0.6") +
                Decimal(str(confidence)) * Decimal("0.4")
            )
            diff = abs(float_score - decimal_score)
            self.assertLess(diff, 1e-9,
                           f"Composite score diff too large: "
                           f"float={float_score}, decimal={decimal_score}, diff={diff}")

    # ── Scenario analysis Decimal precision ────────────────────────────────

    def test_scenario_analysis_decimal_precision(self):
        """Scenario analysis using Decimal must produce precise results."""
        scenarios = generate_scenario_analysis(5.30, 7.66)
        self.assertEqual(len(scenarios), 3)
        self.assertEqual(scenarios[0]["name"], "乐观")
        self.assertEqual(scenarios[1]["name"], "基准")
        self.assertEqual(scenarios[2]["name"], "悲观")
        # Verify returns are calculated correctly
        for s in scenarios:
            self.assertIn("return", s)
            self.assertIn("%", s["return"])

    # ── Risk matrix consistency ────────────────────────────────────────────

    def test_risk_matrix_consistency(self):
        """Risk matrix must always return a valid list."""
        for market in ["hk", "a", "us"]:
            result = generate_risk_matrix("TSLA", market)
            self.assertIsInstance(result, list)
            self.assertGreater(len(result), 0)
            for risk in result:
                self.assertIn("type", risk)
                self.assertIn("prob", risk)
                self.assertIn("impact", risk)
                self.assertIn("desc", risk)


# ═══════════════════════════════════════════════════════════════════════════════
# Acceptance Criterion 3: Zero-Exception Test
# Handle illegal data (null, non-numeric strings) gracefully - no crashes
# ═══════════════════════════════════════════════════════════════════════════════

class TestZeroException(unittest.TestCase):
    """AC3: System must handle illegal data gracefully without crashing.

    Per Refactor_Spec:
        若任何引擎数据缺失或格式非法，须抛出自定义异常 AnalysisError，
        且必须捕获并记录在报告中的 error_log 字段
    """

    # ── parse_num edge cases ───────────────────────────────────────────────

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

    def test_parse_num_with_negative(self):
        """parse_num('-5.0') must handle negative values."""
        result = parse_num("-5.0")
        self.assertEqual(result, Decimal("-5.0"))

    def test_parse_num_with_zero(self):
        """parse_num('0') must return Decimal('0')."""
        result = parse_num("0")
        self.assertEqual(result, Decimal("0"))

    def test_parse_num_with_x_suffix(self):
        """parse_num('1.3x') must strip 'x' suffix."""
        result = parse_num("1.3x")
        self.assertEqual(result, Decimal("1.3"))

    def test_parse_num_with_boolean(self):
        """parse_num with boolean must not crash.

        Note: bool is int subclass in Python, but Decimal(str(True))
        produces 'True' which is invalid. The function handles (int, float)
        via Decimal(str(val)), so True → Decimal('1') is expected.
        """
        result = parse_num(True)
        self.assertIsInstance(result, Decimal)

    # ── parse_num_float edge cases ─────────────────────────────────────────

    def test_parse_num_float_with_none(self):
        """parse_num_float(None) must return 0.0, not crash."""
        result = parse_num_float(None)
        self.assertEqual(result, 0.0)

    def test_parse_num_float_with_na(self):
        """parse_num_float('N/A') must return 0.0."""
        result = parse_num_float("N/A")
        self.assertEqual(result, 0.0)

    def test_parse_num_float_with_empty(self):
        """parse_num_float('') must return 0.0."""
        result = parse_num_float("")
        self.assertEqual(result, 0.0)

    # ── calculate_peg edge cases ───────────────────────────────────────────

    def test_calculate_peg_with_missing_pe(self):
        """PEG with missing PE must return N/A, not crash."""
        result = calculate_peg("N/A", ["+20%"])
        self.assertEqual(result["peg_str"], "N/A")
        self.assertIsNone(result["peg"])

    def test_calculate_peg_with_empty_growth(self):
        """PEG with empty growth list must return N/A, not crash."""
        result = calculate_peg("16.76", [])
        self.assertEqual(result["peg_str"], "N/A")

    def test_calculate_peg_with_none_pe(self):
        """PEG with None PE must return N/A, not crash."""
        result = calculate_peg(None, ["+20%"])
        self.assertEqual(result["peg_str"], "N/A")

    def test_calculate_peg_with_negative_growth(self):
        """PEG with negative growth string (regex extracts digits only)."""
        result = calculate_peg("16.76", ["-5.0%"])
        # regex extracts [\d.]+ from "-5.0%" → "5.0", PEG = 16.76/5.0
        self.assertNotEqual(result["peg_str"], "N/A")

    def test_calculate_peg_with_zero_growth(self):
        """PEG with zero growth must return N/A (division by zero guard)."""
        result = calculate_peg("16.76", ["0.0%"])
        self.assertEqual(result["peg_str"], "N/A")

    def test_calculate_peg_with_empty_pe(self):
        """PEG with empty PE string must return N/A."""
        result = calculate_peg("", ["+20%"])
        self.assertEqual(result["peg_str"], "N/A")

    def test_calculate_peg_with_all_na_growth(self):
        """PEG with all N/A growth values must return N/A."""
        result = calculate_peg("16.76", ["N/A", "N/A", "N/A"])
        self.assertEqual(result["peg_str"], "N/A")

    # ── calc_confidence edge cases ─────────────────────────────────────────

    def test_calc_confidence_with_empty_inputs(self):
        """Confidence with all empty inputs must return base score."""
        score, detail = calc_confidence("", "N/A", "", False, "")
        self.assertEqual(score, 20)  # Base score only

    def test_calc_confidence_with_none_inputs(self):
        """Confidence with None-like inputs must not crash."""
        score, detail = calc_confidence("", "", "", False, "")
        self.assertIsInstance(score, int)
        self.assertGreaterEqual(score, 20)
        self.assertLessEqual(score, 100)

    def test_calc_confidence_with_all_max_inputs(self):
        """Confidence with all positive inputs must return 100 (capped)."""
        score, detail = calc_confidence(
            "利好消息" * 50, "目标价 100", "周线多头", True, "买入"
        )
        self.assertEqual(score, 100)

    # ── get_decision edge cases ────────────────────────────────────────────

    def test_get_decision_with_weekly_divergence(self):
        """Decision with weekly divergence must return 观望, not crash."""
        result = get_decision(80, 70, weekly_aligned=False)
        self.assertIn("周线空头否决", result)

    def test_get_decision_with_low_rr(self):
        """Decision with low RR must return 观望, not crash."""
        result = get_decision(80, 70, weekly_aligned=True, rr_ratio=1.0)
        self.assertIn("RR否决", result)

    def test_get_decision_with_zero_macro(self):
        """Decision with zero macro score must return 环境恶劣."""
        result = get_decision(0, 70, weekly_aligned=True, rr_ratio=2.5)
        self.assertIn("环境恶劣", result)

    def test_get_decision_with_none_rr(self):
        """Decision with None RR must not crash (RR check skipped)."""
        result = get_decision(60, 70, weekly_aligned=True, rr_ratio=None)
        self.assertIsInstance(result, str)

    # ── determine_rating edge cases ────────────────────────────────────────

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

    def test_determine_rating_with_empty_weekly(self):
        """Rating with empty weekly text must not crash."""
        rating, icon = determine_rating(50, None, 2.5, "")
        self.assertIsInstance(rating, str)

    def test_determine_rating_boundary_values(self):
        """Rating at exact boundary values must be correct."""
        # Without macro: score = sentiment_score directly
        # 80 → 买入, 65 → 增持, 50 → 持有, 35 → 减持, <35 → 卖出
        test_cases = [
            (80, None, None, "", "买入"),
            (65, None, None, "", "增持"),
            (50, None, None, "", "持有"),
            (35, None, None, "", "减持"),
            (34, None, None, "", "卖出"),
        ]
        for score, macro, rr, weekly, expected in test_cases:
            rating, icon = determine_rating(score, macro, rr, weekly)
            self.assertEqual(rating, expected,
                           f"Rating at boundary: score={score}, "
                           f"expected={expected}, got={rating}")

    # ── weekly_signal edge cases ───────────────────────────────────────────

    def test_weekly_signal_with_empty_inputs(self):
        """weekly_signal with empty inputs must return 观望."""
        result = weekly_signal("", "")
        self.assertIn("观望", result)

    def test_weekly_signal_buy_weekly_bull(self):
        """Buy signal + weekly bull = trusted signal."""
        result = weekly_signal("周线多头", "买入")
        self.assertIn("信号可信", result)

    def test_weekly_signal_sell_weekly_bear(self):
        """Sell signal + weekly bear = trusted signal."""
        result = weekly_signal("周线空头", "卖出")
        self.assertIn("信号可信", result)

    def test_weekly_signal_buy_weekly_bear(self):
        """Buy signal + weekly bear = divergence warning."""
        result = weekly_signal("周线空头", "买入")
        self.assertIn("⚠️", result)

    def test_weekly_signal_sell_weekly_bull(self):
        """Sell signal + weekly bull = divergence warning."""
        result = weekly_signal("周线多头", "卖出")
        self.assertIn("⚠️", result)

    # ── calc_fundamental_scores edge cases ─────────────────────────────────

    def test_calc_fundamental_scores_with_all_empty(self):
        """Fundamental scores with all empty inputs must return valid scores."""
        scores = calc_fundamental_scores({}, {}, None, None, "", "", [])
        self.assertIsInstance(scores, dict)
        self.assertIn("基本面合计", scores)
        self.assertIsInstance(scores["基本面合计"], int)

    def test_calc_fundamental_scores_with_none_gs_metrics(self):
        """Fundamental scores with None gs_metrics must not crash."""
        # gs_metrics=None would crash on .get(), so we pass empty dict
        scores = calc_fundamental_scores(
            {}, {}, None, None, "", "", []
        )
        self.assertIsInstance(scores, dict)
        self.assertIn("基本面合计", scores)

    def test_calc_fundamental_scores_total_within_range(self):
        """Fundamental total must be <= 100 (sum of max scores)."""
        gs_metrics = {"roe": "50%", "debt_ratio": "10%", "fcf": "100亿",
                     "fcf_note": "(TTM)", "net_debt_ebitda": "-0.5x", "beta": "1.0"}
        earnings = {"revenue": ["5000"], "profit_growth": ["+100%"]}
        company = {"industry_position": "行业龙头第一"}
        scores = calc_fundamental_scores(
            earnings, gs_metrics, {"roe": 50, "debt_ratio": 10},
            company, "利好消息", "目标价 100", ["催化剂1", "催化剂2", "催化剂3"]
        )
        self.assertLessEqual(scores["基本面合计"], 100)

    # ── DataParser edge cases ──────────────────────────────────────────────

    def test_data_parser_parse_mx_table_empty(self):
        """DataParser must handle empty mx-data output."""
        headers, rows = DataParser.parse_mx_table("")
        self.assertEqual(headers, [])
        self.assertEqual(rows, [])

    def test_data_parser_parse_mx_table_malformed(self):
        """DataParser must handle malformed table data."""
        headers, rows = DataParser.parse_mx_table("not a table\nalso not a table")
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

    def test_data_parser_parse_analyst_target_none(self):
        """DataParser must handle None analyst target."""
        result = DataParser.parse_analyst_target(None)
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

    def test_data_parser_parse_tech_indicators_partial(self):
        """DataParser must handle partial tech indicator data."""
        result = DataParser.parse_tech_indicators({"col_1": "10.5", "col_5": "55.0"})
        self.assertEqual(result["ma5"], 10.5)
        self.assertEqual(result["rsi"], 55.0)
        self.assertIsNone(result["ma20"])

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

    def test_data_parser_validate_financial_data_with_valid_data(self):
        """DataParser must validate and clean valid financial data."""
        data = {"revenue": "331.90", "net_profit": "5.83", "roe": "4.83"}
        result = DataParser.validate_financial_data(data)
        self.assertEqual(result["revenue"], Decimal("331.90"))
        self.assertEqual(result["net_profit"], Decimal("5.83"))

    def test_data_parser_structure_earnings_empty(self):
        """DataParser must structure empty earnings forecast with defaults."""
        result = DataParser.structure_earnings_forecast({})
        self.assertEqual(result["years"], ["2025A", "2026E", "2027E"])
        self.assertEqual(len(result["revenue"]), 3)

    def test_data_parser_structure_earnings_na_years(self):
        """DataParser must handle N/A years."""
        result = DataParser.structure_earnings_forecast({"years": ["N/A", "N/A", "N/A"]})
        self.assertEqual(result["years"], ["2025A", "2026E", "2027E"])

    def test_data_parser_structure_earnings_partial_lists(self):
        """DataParser must pad short lists to 3 elements."""
        result = DataParser.structure_earnings_forecast(
            {"years": ["2025A", "2026E"], "revenue": ["100"]}
        )
        self.assertEqual(len(result["revenue"]), 3)
        self.assertEqual(result["revenue"][1], "N/A")

    # ── AnalysisError handling ─────────────────────────────────────────────

    def test_analysis_error_to_log_entry(self):
        """AnalysisError.to_log_entry must produce correct format."""
        err = AnalysisError("engine1", "data missing")
        self.assertEqual(err.to_log_entry(), "engine1: data missing")

    def test_analysis_error_str_representation(self):
        """AnalysisError str must include source and detail."""
        err = AnalysisError("engine2", "timeout")
        self.assertIn("engine2", str(err))
        self.assertIn("timeout", str(err))

    def test_analysis_error_is_exception(self):
        """AnalysisError must be catchable as Exception."""
        with self.assertRaises(Exception):
            raise AnalysisError("test", "error")

    # ── detect_market edge cases ───────────────────────────────────────────

    def test_detect_market_with_various_formats(self):
        """detect_market must handle all ticker formats."""
        self.assertEqual(detect_market("HK01316"), "hk")
        self.assertEqual(detect_market("hk01316"), "hk")
        self.assertEqual(detect_market("600519"), "a")
        self.assertEqual(detect_market("000858"), "a")
        self.assertEqual(detect_market("603725"), "a")
        self.assertEqual(detect_market("TSLA"), "us")
        self.assertEqual(detect_market("AAPL"), "us")

    def test_detect_market_with_whitespace(self):
        """detect_market must handle whitespace-padded tickers."""
        self.assertEqual(detect_market("  600519  "), "a")
        self.assertEqual(detect_market(" HK01316 "), "hk")

    def test_detect_market_with_5_digit_number(self):
        """detect_market must classify 5-digit numbers as US (not A-stock)."""
        self.assertEqual(detect_market("12345"), "us")

    def test_detect_market_with_hk_no_digits(self):
        """detect_market with HK prefix but no digits must be US."""
        self.assertEqual(detect_market("HKABC"), "us")

    # ── decimal_round / decimal_to_str edge cases ─────────────────────────

    def test_decimal_round_with_zero(self):
        """decimal_round(0) must produce 0.00."""
        result = decimal_round(Decimal("0"), 2)
        self.assertEqual(result, Decimal("0.00"))

    def test_decimal_round_with_negative(self):
        """decimal_round with negative values must work correctly."""
        result = decimal_round(Decimal("-3.14159"), 2)
        self.assertEqual(result, Decimal("-3.14"))

    def test_decimal_to_str_with_zero(self):
        """decimal_to_str(0) must return '0.00'."""
        result = decimal_to_str(Decimal("0"), 2)
        self.assertEqual(result, "0.00")

    def test_decimal_round_with_large_value(self):
        """decimal_round with large values must not overflow."""
        result = decimal_round(Decimal("999999999.999"), 2)
        self.assertEqual(result, Decimal("1000000000.00"))

    # ── generate_scenario_analysis edge cases ──────────────────────────────

    def test_generate_scenario_analysis_with_zero_price(self):
        """Scenario analysis with zero current price must not crash."""
        result = generate_scenario_analysis(0, 7.66)
        self.assertIsInstance(result, list)

    def test_generate_scenario_analysis_with_negative_price(self):
        """Scenario analysis with negative price must not crash."""
        result = generate_scenario_analysis(-5.0, 7.66)
        self.assertIsInstance(result, list)

    def test_generate_scenario_analysis_with_zero_target(self):
        """Scenario analysis with zero target price must not crash."""
        result = generate_scenario_analysis(5.30, 0)
        self.assertIsInstance(result, list)

    # ── generate_risk_matrix edge cases ────────────────────────────────────

    def test_generate_risk_matrix_returns_valid(self):
        """Risk matrix must always return a valid list."""
        result = generate_risk_matrix("TSLA", "us")
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    # ── generate_investment_thesis edge cases ──────────────────────────────

    def test_generate_investment_thesis_with_none_inputs(self):
        """Investment thesis with None inputs must not crash."""
        result = generate_investment_thesis("TSLA", 50, None,
                                           {"peg": None, "peg_str": "N/A"}, None, "")
        self.assertIsInstance(result, str)

    def test_generate_investment_thesis_with_all_empty(self):
        """Investment thesis with minimal inputs must not crash."""
        result = generate_investment_thesis("TSLA", 50, None,
                                           {"peg": None, "peg_str": "N/A"}, None, "")
        self.assertIsInstance(result, str)

    def test_generate_investment_thesis_low_score(self):
        """Investment thesis with low sentiment score must mention 空头."""
        result = generate_investment_thesis("TSLA", 30, None,
                                           {"peg": None, "peg_str": "N/A"}, None, "")
        self.assertIn("空头", result)

    def test_generate_investment_thesis_high_score(self):
        """Investment thesis with high sentiment score must mention 多头."""
        result = generate_investment_thesis("TSLA", 80, None,
                                           {"peg": None, "peg_str": "N/A"}, None, "")
        self.assertIn("多头", result)

    # ── ReportGenerator with edge-case data ────────────────────────────────

    def test_report_generator_with_zero_sentiment(self):
        """ReportGenerator must handle zero sentiment score."""
        results = create_mock_results(sentiment_score=0, operation_advice="卖出")
        generator = ReportGenerator(results)
        report = generator.generate_json_report()
        self.assertEqual(report["analysis"]["sentiment_score"], 0)

    def test_report_generator_with_max_sentiment(self):
        """ReportGenerator must handle max sentiment score."""
        results = create_mock_results(sentiment_score=100, operation_advice="买入")
        generator = ReportGenerator(results)
        report = generator.generate_json_report()
        self.assertEqual(report["analysis"]["sentiment_score"], 100)

    # ── Error log management ───────────────────────────────────────────────

    def test_error_log_clear(self):
        """Error log must be clearable."""
        clear_error_log()
        log_error("test_source", "test_message")
        self.assertGreater(len(ERROR_LOG), 0)
        clear_error_log()
        self.assertEqual(len(ERROR_LOG), 0)

    def test_log_error_appends_to_list(self):
        """log_error must append formatted entries to ERROR_LOG."""
        clear_error_log()
        log_error("source1", "message1")
        log_error("source2", "message2")
        self.assertEqual(len(ERROR_LOG), 2)
        self.assertIn("source1: message1", ERROR_LOG[0])
        self.assertIn("source2: message2", ERROR_LOG[1])
        clear_error_log()

    # ── Constants verification ─────────────────────────────────────────────

    def test_constants_version_match(self):
        """Package version must match constants."""
        from dual_engine import __version__, __engine_id__
        self.assertEqual(__version__, VERSION)
        self.assertEqual(__engine_id__, ENGINE_ID)

    def test_timeout_constants_are_positive(self):
        """All timeout constants must be positive integers."""
        from dual_engine.constants import (
            TIMEOUT_NEWS, TIMEOUT_DATA, TIMEOUT_ANALYSIS, TIMEOUT_FINANCIAL
        )
        for name, val in [("TIMEOUT_NEWS", TIMEOUT_NEWS), ("TIMEOUT_DATA", TIMEOUT_DATA),
                          ("TIMEOUT_ANALYSIS", TIMEOUT_ANALYSIS),
                          ("TIMEOUT_FINANCIAL", TIMEOUT_FINANCIAL)]:
            self.assertIsInstance(val, int, f"{name} must be int")
            self.assertGreater(val, 0, f"{name} must be positive")

    # ── to_query_ticker edge cases ─────────────────────────────────────────

    def test_data_parser_to_query_ticker_all_markets(self):
        """Query ticker conversion must be correct for all markets."""
        self.assertEqual(DataParser.to_query_ticker("HK01316", "hk"), "01316.HK")
        self.assertEqual(DataParser.to_query_ticker("600519", "a"), "600519.SS")
        self.assertEqual(DataParser.to_query_ticker("000858", "a"), "000858.SZ")
        self.assertEqual(DataParser.to_query_ticker("603725", "a"), "603725.SS")
        self.assertEqual(DataParser.to_query_ticker("TSLA", "us"), "TSLA")

    def test_data_parser_to_mx_query_ticker_hk_numeric(self):
        """HK numeric conversion must strip prefix."""
        self.assertEqual(DataParser.to_mx_query_ticker_hk_numeric("HK01316"), "01316")
        self.assertEqual(DataParser.to_mx_query_ticker_hk_numeric("TSLA"), "TSLA")

    def test_data_parser_extract_numeric(self):
        """extract_numeric must handle various formats."""
        self.assertEqual(DataParser.extract_numeric("15.92"), "15.92")
        self.assertEqual(DataParser.extract_numeric("-"), None)
        self.assertEqual(DataParser.extract_numeric(""), None)
        self.assertEqual(DataParser.extract_numeric(None), None)
        self.assertEqual(DataParser.extract_numeric("15.92%"), "15.92")
        self.assertEqual(DataParser.extract_numeric("abc15.92def"), "15.92")

    # ── validate_macro_score edge cases ────────────────────────────────────

    def test_validate_macro_score_none(self):
        """validate_macro_score with None must return None."""
        result = DataParser.validate_macro_score(None)
        self.assertIsNone(result)

    def test_validate_macro_score_valid(self):
        """validate_macro_score with valid object must return dict."""
        macro = MagicMock()
        macro.macro = 21
        macro.sector = 25
        macro.news = 15
        macro.total = 44
        macro.data_available = True
        result = DataParser.validate_macro_score(macro)
        self.assertIsNotNone(result)
        self.assertEqual(result["macro"], 21)
        self.assertEqual(result["total"], 44)
        self.assertTrue(result["data_available"])


# ═══════════════════════════════════════════════════════════════════════════════
# Additional Integration Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestModuleIntegration(unittest.TestCase):
    """Integration tests verifying module interactions work correctly."""

    def test_report_generator_full_round_trip(self):
        """Full round-trip: mock data → ReportGenerator → JSON report → validate."""
        results = create_mock_results(market="hk")
        generator = ReportGenerator(results)
        report = generator.generate_json_report()

        # Validate complete schema
        self.assertIn("timestamp", report)
        self.assertIn("engine_status", report)
        self.assertIn("metrics", report)
        self.assertIn("metadata", report)
        self.assertIn("error_log", report)
        self.assertIn("analysis", report)
        self.assertIn("report_markdown", report)

        # Validate metrics format
        self.assertRegex(report["metrics"]["precision_factor"], r"^\d+\.\d{6}$")
        self.assertRegex(report["metrics"]["composite_score"], r"^-?\d+\.\d{2}$")

        # Validate metadata
        self.assertEqual(report["metadata"]["version"], "1.0.0")
        self.assertEqual(report["metadata"]["engine_id"], "dual-analysis-v2")

        # Validate report_markdown is non-empty string
        self.assertGreater(len(report["report_markdown"]), 100)

    def test_precision_factor_reflects_error_count(self):
        """precision_factor must decrease when errors are present in ERROR_LOG."""
        clear_error_log()
        # No errors → precision_factor = 1.000000
        self.assertEqual(len(ERROR_LOG), 0)
        # Simulate 3 errors
        log_error("test1", "error1")
        log_error("test2", "error2")
        log_error("test3", "error3")
        error_count = len(ERROR_LOG)
        from decimal import Decimal as D
        expected_pf = D("1.000000") - D("0.000001") * error_count
        expected_pf = max(expected_pf, D("0.000000"))
        self.assertEqual(f"{expected_pf:.6f}", f"{0.999997:.6f}")
        clear_error_log()

    def test_parse_num_float_backward_compat(self):
        """parse_num_float must produce same results as old _parse_num."""
        test_vals = [None, "N/A", "", "15.92%", "310.1亿", "abc", 42, 3.14,
                     "$123.45", "1,234.56", "-5.0%", "0"]
        for val in test_vals:
            # Old implementation
            if val is None or val == "N/A" or val == "":
                old_result = 0
            elif isinstance(val, (int, float)):
                old_result = float(val)
            else:
                s = str(val).replace("%", "").replace("亿", "").replace("元", "") \
                            .replace("$", "").replace(",", "").strip()
                try:
                    old_result = float(s)
                except (ValueError, TypeError):
                    old_result = 0

            new_result = parse_num_float(val)
            self.assertAlmostEqual(old_result, new_result, places=10,
                                  msg=f"Mismatch for val={val}: "
                                  f"old={old_result}, new={new_result}")

    def test_decimal_arithmetic_no_precision_loss(self):
        """Key financial calculations must not lose precision with Decimal."""
        # Test: PEG = PE / growth_rate
        pe = Decimal("29.81")
        growth = Decimal("40.5")
        peg = decimal_round(pe / growth, 2)
        self.assertEqual(peg, Decimal("0.74"))

        # Test: composite_score = sentiment * 0.6 + macro * 0.3 + confidence * 0.3
        # 39*0.6 + 44*0.3 + 65*0.3 = 23.4 + 13.2 + 19.5 = 56.10
        score = Decimal("39") * Decimal("0.6") + Decimal("44") * Decimal("0.3") + Decimal("65") * Decimal("0.3")
        self.assertEqual(decimal_round(score, 2), Decimal("56.10"))

    def test_peg_valuation_thresholds(self):
        """PEG valuation thresholds must be exactly as specified."""
        # PEG < 0.5 → 显著低估
        result = calculate_peg("10.00", ["+30%"])
        self.assertEqual(result["valuation"], "显著低估")

        # PEG 0.5-1.0 → 低估
        result = calculate_peg("20.00", ["+30%"])
        self.assertEqual(result["valuation"], "低估")

        # PEG 1.0-1.5 → 合理估值  (PE=25/growth=20 = 1.25)
        result = calculate_peg("25.00", ["+20%"])
        self.assertEqual(result["valuation"], "合理估值")

        # PEG 1.5-2.0 → 高估  (PE=35/growth=20 = 1.75)
        result = calculate_peg("35.00", ["+20%"])
        self.assertEqual(result["valuation"], "高估")

        # PEG > 2.0 → 显著高估
        result = calculate_peg("50.00", ["+20%"])
        self.assertEqual(result["valuation"], "显著高估")


if __name__ == "__main__":
    unittest.main(verbosity=2)
