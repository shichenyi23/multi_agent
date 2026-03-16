from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from workflow.state_machine import WorkflowOrchestrator

# Configure logging for command-line usage
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)


def summarize_state(state) -> dict:
    summary = {
        "module_name": state.module_name,
        "stage": state.stage.value,
        "attempts": state.attempts,
        "notes": state.notes,
    }
    if state.current_rtl is not None:
        summary["rtl"] = {
            "path": state.current_rtl.filepath,
            "version": state.current_rtl.version,
            "lint_success": state.current_rtl.lint.success if state.current_rtl.lint else None,
        }
    if state.current_tb is not None:
        summary["tb"] = {
            "path": state.current_tb.filepath,
            "version": state.current_tb.version,
            "lint_success": state.current_tb.lint.success if state.current_tb.lint else None,
        }
    if state.sim_report is not None:
        summary["simulation"] = {
            "passed": state.sim_report.passed,
            "vcd_path": state.sim_report.vcd_path,
            "failure_count": len(state.sim_report.failures),
        }
    if state.synth_report is not None:
        summary["synthesis"] = {
            "passed": state.synth_report.passed,
            "strategy": state.synth_report.strategy,
            "cell_count": state.synth_report.cell_count,
            "critical_path_ns": state.synth_report.critical_path_ns,
            "warning_count": len(state.synth_report.warnings),
        }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the LLM4EDA scaffold workflow.")
    parser.add_argument("module_dir", type=Path, help="Artifact directory containing spec.json")
    parser.add_argument(
        "--generate-only",
        action="store_true",
        help="Stop after RTL and TB generation.",
    )
    parser.add_argument(
        "--strategy",
        default="area",
        choices=["area", "timing"],
        help="Synthesis strategy to record in the report.",
    )
    parser.add_argument(
        "--liberty",
        type=Path,
        default=None,
        help="Optional liberty file for Yosys/ABC.",
    )
    parser.add_argument(
        "--backend",
        default=None,
        help="LLM backend name. Supported today: `rule-based`, `openai-compatible`.",
    )
    parser.add_argument(
        "--max-lint-retries",
        type=int,
        default=2,
        help="Maximum repair attempts after lint failures.",
    )
    parser.add_argument(
        "--max-sim-retries",
        type=int,
        default=2,
        help="Maximum repair attempts after simulation failures.",
    )
    parser.add_argument(
        "--full-json",
        action="store_true",
        help="Print the full workflow state instead of the condensed summary.",
    )
    args = parser.parse_args()

    orchestrator = WorkflowOrchestrator(
        backend_name=args.backend,
        max_lint_retries=args.max_lint_retries,
        max_sim_retries=args.max_sim_retries,
    )
    state = orchestrator.run(
        module_dir=args.module_dir,
        generate_only=args.generate_only,
        strategy=args.strategy,
        liberty_path=args.liberty,
    )
    payload = state.to_dict() if args.full_json else summarize_state(state)
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
