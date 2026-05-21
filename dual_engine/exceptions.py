"""
Custom exceptions for the Dual Engine Analysis package.

Per Refactor_Spec: If any engine data is missing or format is illegal,
an AnalysisError must be raised and captured in the report's error_log field.
"""


class AnalysisError(Exception):
    """Raised when engine data is missing, format is illegal, or calculation fails.

    Attributes:
        source: The engine or module that caused the error (e.g. 'engine1', 'engine2', 'data_parser')
        detail: Human-readable description of the error
    """

    def __init__(self, source: str, detail: str):
        self.source = source
        self.detail = detail
        super().__init__(f"[{source}] {detail}")

    def to_log_entry(self) -> str:
        """Convert to a log entry string for error_log field."""
        return f"{self.source}: {self.detail}"
