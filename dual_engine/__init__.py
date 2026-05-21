"""
Dual Engine Analysis Package - Refactored v2

Modular decomposition of the monolithic dual_engine_analyze.py script.

Architecture:
    DataParser       - Cleans and standardizes raw input data
    EngineProcessor  - Core dual-engine cross-validation logic
    ReportGenerator  - Structured JSON + Markdown report output

All financial/scoring calculations use decimal.Decimal for precision.
"""

from dual_engine.exceptions import AnalysisError
from dual_engine.data_parser import DataParser
from dual_engine.engine_processor import EngineProcessor
from dual_engine.report_generator import ReportGenerator

__version__ = "1.0.0"
__engine_id__ = "dual-analysis-v2"
