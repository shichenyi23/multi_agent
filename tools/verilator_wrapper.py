from __future__ import annotations

from pathlib import Path

from contracts import LintResult
from tools.base import run_command
from tools.log_parser import extract_errors, extract_warnings


def lint_verilog(
    source_files: list[Path],
    top_module: str | None = None,
    cwd: Path | None = None,
) -> LintResult:
    command = ["verilator", "--lint-only", "-Wall"]
    if top_module:
        command.extend(["--top-module", top_module])
    command.extend(str(path.resolve()) for path in source_files)
    result = run_command(command, cwd=cwd)
    combined = "\n".join(item for item in [result.stdout, result.stderr] if item)
    warnings = extract_warnings(combined)
    errors = extract_errors(combined)
    if result.returncode == 127:
        errors.append(result.stderr.strip())
    return LintResult(
        tool="verilator",
        success=result.success,
        warnings=warnings,
        errors=errors,
        stdout=result.stdout,
        stderr=result.stderr,
    )
