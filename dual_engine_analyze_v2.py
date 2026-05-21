#!/usr/bin/env python3
"""
双引擎联动分析脚本 v2 (Refactored)
用法:
  python3.12 dual_engine_analyze.py TSLA          # 美股（双引擎）
  python3.12 dual_engine_analyze.py 600519        # A 股（单引擎 + 周线验证）
  python3.12 dual_engine_analyze.py HK02050       # 港股（单引擎 + 周线验证）
  python3.12 dual_engine_analyze.py TSLA AAPL     # 批量

市场判断：
  - HK 前缀 → 港股
  - 纯数字 6 位 → A 股
  - 其他 → 美股（触发 trading-agents）

Refactored per Refactor_Spec:
  - Modular: DataParser / EngineProcessor / ReportGenerator
  - Decimal precision for all financial/scoring calculations
  - Structured JSON output + Markdown report
  - Custom AnalysisError with error_log capture
"""

import sys
import os
import json

# ── Output unbuffering ────────────────────────────────────────────────────────
os.environ["PYTHONUNBUFFERED"] = "1"
if sys.version_info >= (3, 7):
    sys.stdout.reconfigure(line_buffering=True)
if sys.version_info >= (3, 3):
    sys.stderr.reconfigure(line_buffering=True)

# ── Load zshrc env ────────────────────────────────────────────────────────────
from dual_engine.utils import _load_zshrc_env, clear_error_log
_load_zshrc_env()

# ── Import refactored modules ─────────────────────────────────────────────────
from dual_engine.exceptions import AnalysisError
from dual_engine.engine_processor import EngineProcessor
from dual_engine.report_generator import ReportGenerator
from dual_engine.fetchers import save_to_investment_db, save_to_notion
from dual_engine.utils import ERROR_LOG


def analyze(ticker: str):
    """Main analysis entry point - uses refactored modular architecture."""
    import time
    start_time = time.time()

    try:
        # ── Step 1-3: Dual engine processing ──
        processor = EngineProcessor(ticker)
        results = processor.process()

        # ── Step 4: Generate reports ──
        generator = ReportGenerator(results)

        # Structured JSON report
        json_report = generator.generate_json_report()

        # Markdown report (also prints to stdout)
        markdown_report = json_report["report_markdown"]
        print(markdown_report)

        # Save JSON report
        json_path = f"/tmp/dual_engine_{ticker}_{results.get('current_price', 'na')}.json"
        try:
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(json_report, f, ensure_ascii=False, indent=2, default=str)
            print(f"\n📄 JSON 报告已保存：{json_path}")
        except Exception as e:
            print(f"   ⚠️ JSON 报告保存失败：{e}")

        # ── Step 5: Auto-archive ──
        print(f"   Step 4/5: 自动存档...")
        r = results["analysis_result"]
        ta_decision = results.get("ta_decision")
        macro_score = results.get("macro_score")
        save_to_investment_db(ticker, r, ta_decision, macro_score)
        save_to_notion(ticker, markdown_report)

    except AnalysisError as e:
        # Catch custom AnalysisError - report it in error_log
        error_entry = e.to_log_entry()
        ERROR_LOG.append(error_entry)
        print(f"\n❌ 分析错误：{error_entry}")

        # Generate error JSON report
        error_report = {
            "timestamp": __import__("datetime").datetime.now().isoformat(),
            "engine_status": {"engine1": "error", "engine2": "inactive"},
            "metrics": {"precision_factor": "0.000000", "composite_score": "0.00"},
            "metadata": {"version": "1.0.0", "engine_id": "dual-analysis-v2"},
            "error_log": ERROR_LOG,
        }
        print(json.dumps(error_report, ensure_ascii=False, indent=2))
        return

    except Exception as e:
        # Unexpected errors
        ERROR_LOG.append(f"unexpected: {str(e)}")
        print(f"\n❌ 未预期的错误：{e}")
        import traceback
        traceback.print_exc()
        return

    # ── Summary ──
    total_time = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"✅ 分析完成，总耗时：{total_time:.1f}秒")
    if ERROR_LOG:
        print(f"\n⚠️ 本次分析遇到的问题 ({len(ERROR_LOG)}个)：")
        for err in ERROR_LOG:
            print(f"  - {err}")
    else:
        print("\n✅ 所有步骤执行成功，无错误")


if __name__ == "__main__":
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["TSLA"]
    for t in tickers:
        analyze(t.strip())
