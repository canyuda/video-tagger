"""V2 content analysis: transcript + keyframes -> structured report
(docs/03 §1–§6). Report JSON Schema is the UI rendering contract."""

from sidecar.analyzer.markdown import report_to_markdown
from sidecar.analyzer.pipeline import AnalysisError, Analyzer
from sidecar.analyzer.prompts import (
    REPORT_JSON_SCHEMA,
    Report,
    analysis_messages,
    format_transcript,
)

__all__ = [
    "Analyzer",
    "AnalysisError",
    "Report",
    "REPORT_JSON_SCHEMA",
    "analysis_messages",
    "format_transcript",
    "report_to_markdown",
]
