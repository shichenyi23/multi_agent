from __future__ import annotations

import re

from contracts import FailureCategory, SimulationFailure

WARNING_RE = re.compile(r"(?mi)^(?:%Warning|Warning:)\s*(.+)$")
ERROR_RE = re.compile(r"(?mi)^(?:%Error|Error:)\s*(.+)$")
TIME_RE = re.compile(r"\b(?:time=)?(\d+)\s*(?:ps|ns)?\b", re.IGNORECASE)
CELL_COUNT_RE = re.compile(r"Number of cells:\s+(\d+)")
CRITICAL_PATH_RE = re.compile(r"(?:critical path|worst delay)[^\d]*(\d+(?:\.\d+)?)\s*ns", re.IGNORECASE)


def extract_warnings(log: str) -> list[str]:
    return [item.strip() for item in WARNING_RE.findall(log)]


def extract_errors(log: str) -> list[str]:
    return [item.strip() for item in ERROR_RE.findall(log)]


def classify_sim_failures(log: str) -> list[SimulationFailure]:
    failures: list[SimulationFailure] = []
    for raw_line in log.splitlines():
        line = raw_line.strip()
        normalized = line.lower()
        if not line:
            continue
        if "fail" not in normalized and "error" not in normalized and "mismatch" not in normalized:
            continue

        category = FailureCategory.SIM_MISMATCH
        if "width" in normalized:
            category = FailureCategory.WIDTH_MISMATCH
        elif "latch" in normalized:
            category = FailureCategory.LATCH_INFERRED
        elif "timing" in normalized:
            category = FailureCategory.TIMING_VIOLATION
        elif " x" in normalized or normalized.endswith("x") or "'x" in normalized:
            category = FailureCategory.X_PROPAGATION
        elif "tool" in normalized or "not found" in normalized:
            category = FailureCategory.TOOL_ERROR

        match = TIME_RE.search(line)
        failures.append(
            SimulationFailure(
                category=category,
                message=line,
                time_ps=int(match.group(1)) if match else None,
            )
        )
    return failures


def suggest_instrumentation(failures: list[SimulationFailure]) -> list[str]:
    suggestions: list[str] = []
    for failure in failures:
        if failure.category == FailureCategory.X_PROPAGATION:
            suggestions.append("Add $monitor on state, inputs, and outputs to catch X propagation earlier.")
        elif failure.category == FailureCategory.SIM_MISMATCH:
            suggestions.append("Add $display around the failing transaction with expected and actual values.")
        elif failure.category == FailureCategory.WIDTH_MISMATCH:
            suggestions.append("Log all packed signal widths at elaboration time.")
    return list(dict.fromkeys(suggestions))


def extract_cell_count(log: str) -> int | None:
    match = CELL_COUNT_RE.search(log)
    return int(match.group(1)) if match else None


def extract_critical_path_ns(log: str) -> float | None:
    match = CRITICAL_PATH_RE.search(log)
    return float(match.group(1)) if match else None


def derive_synthesis_suggestions(
    warnings: list[str],
    critical_path_ns: float | None,
    strategy: str,
) -> list[str]:
    suggestions: list[str] = []
    warning_blob = " ".join(warnings).lower()
    if "latch" in warning_blob:
        suggestions.append("Replace incomplete combinational assignments or move the path into sequential logic.")
    if critical_path_ns is not None and strategy == "timing":
        suggestions.append("Consider adding pipeline stages or simplifying the longest combinational path.")
    if strategy == "area":
        suggestions.append("Review datapath widths and resource sharing opportunities before duplicating logic.")
    return list(dict.fromkeys(suggestions))

