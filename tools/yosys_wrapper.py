from __future__ import annotations

from pathlib import Path

from contracts import SynthesisReport
from tools.base import run_command
from tools.log_parser import (
    derive_synthesis_suggestions,
    extract_cell_count,
    extract_critical_path_ns,
    extract_warnings,
)


def synthesize_verilog(
    rtl_path: Path,
    top_module: str,
    strategy: str,
    liberty_path: Path | None = None,
    cwd: Path | None = None,
) -> SynthesisReport:
    rtl_path = rtl_path.resolve()
    liberty_path = liberty_path.resolve() if liberty_path is not None else None
    script = [
        f"read_verilog {rtl_path}",
        f"hierarchy -check -top {top_module}",
        "proc",
        "opt",
    ]
    if liberty_path is not None:
        script.append(f"abc -liberty {liberty_path}")
        script.append(f"stat -liberty {liberty_path}")
    else:
        script.append("stat")

    result = run_command(["yosys", "-p", "; ".join(script)], cwd=cwd)
    raw_log = "\n".join(item for item in [result.stdout, result.stderr] if item)
    warnings = extract_warnings(raw_log)
    if result.returncode == 127:
        warnings.append(result.stderr.strip())
    cell_count = extract_cell_count(raw_log)
    critical_path_ns = extract_critical_path_ns(raw_log)
    suggestions = derive_synthesis_suggestions(warnings, critical_path_ns, strategy)
    return SynthesisReport(
        module_name=top_module,
        passed=result.success,
        strategy=strategy,
        cell_count=cell_count,
        critical_path_ns=critical_path_ns,
        warnings=warnings,
        suggestions=suggestions,
        raw_log=raw_log,
    )
