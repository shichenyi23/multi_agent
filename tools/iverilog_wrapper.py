from __future__ import annotations

from pathlib import Path

from contracts import FailureCategory, SimulationFailure, SimulationReport
from tools.base import run_command
from tools.log_parser import classify_sim_failures, suggest_instrumentation


def run_simulation(
    module_name: str,
    rtl_path: Path,
    tb_path: Path,
    workdir: Path,
) -> SimulationReport:
    rtl_path = rtl_path.resolve()
    tb_path = tb_path.resolve()
    binary_path = (workdir / f"{module_name}.out").resolve()
    compile_result = run_command(
        ["iverilog", "-g2012", "-o", str(binary_path), str(rtl_path), str(tb_path)],
        cwd=workdir,
    )
    compile_log = "\n".join(item for item in [compile_result.stdout, compile_result.stderr] if item)
    if not compile_result.success:
        failures = classify_sim_failures(compile_log)
        if not failures:
            failures = [
                SimulationFailure(
                    category=FailureCategory.SYNTAX_ERROR,
                    message=compile_log.strip() or "Compilation failed without a parsed error line.",
                )
            ]
        return SimulationReport(
            module_name=module_name,
            passed=False,
            compile_log=compile_log,
            failures=failures,
            suggested_instrumentation=suggest_instrumentation(failures),
        )

    run_result = run_command(["vvp", str(binary_path)], cwd=workdir)
    run_log = "\n".join(item for item in [run_result.stdout, run_result.stderr] if item)
    failures = classify_sim_failures(run_log)
    if "TB_INCOMPLETE" in run_log:
        failures.append(
            SimulationFailure(
                category=FailureCategory.SIM_MISMATCH,
                message="Testbench reported TB_INCOMPLETE. Add stimulus and scoreboard generation.",
            )
        )
    passed = run_result.success and not failures and "PASS" in run_log
    return SimulationReport(
        module_name=module_name,
        passed=passed,
        compile_log=compile_log,
        run_log=run_log,
        failures=failures,
        suggested_instrumentation=suggest_instrumentation(failures),
        vcd_path=str(workdir / "wave.vcd") if (workdir / "wave.vcd").exists() else None,
    )
