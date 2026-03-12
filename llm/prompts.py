from __future__ import annotations

import json
from pathlib import Path

from contracts import LintResult, ModuleSpec, SimulationReport
from llm.backends import PromptRequest


def build_spec_request(request_text: str, module_name_hint: str | None = None) -> PromptRequest:
    return PromptRequest(
        kind="spec_from_request",
        system_prompt=(
            "You are a specification analyst for RTL design. "
            "Convert the user request into strict JSON only."
        ),
        user_prompt=(
            "Return a JSON object with fields: module_name, summary, ports, parameters, "
            "clock_strategy, reset_strategy, timing_requirements, constraints, "
            "functional_spec, test_points, submodules.\n\n"
            f"Request:\n{request_text}"
        ),
        metadata={"request_text": request_text, "module_name_hint": module_name_hint},
    )


def build_rtl_generation_request(spec: ModuleSpec) -> PromptRequest:
    return PromptRequest(
        kind="rtl_generate",
        system_prompt=(
            "You write synthesizable Verilog. "
            "Return only Verilog code, no explanation."
        ),
        user_prompt=(
            "Generate RTL for this module specification. "
            "Use explicit widths, non-blocking assignments in sequential logic, "
            "and avoid latch inference.\n\n"
            f"{json.dumps(spec.to_dict(), indent=2, ensure_ascii=False)}"
        ),
        metadata={"spec": spec},
    )


def build_rtl_repair_request(
    spec: ModuleSpec,
    previous_source: str,
    lint_result: LintResult | None = None,
    sim_report: SimulationReport | None = None,
) -> PromptRequest:
    feedback_chunks = []
    if lint_result is not None:
        feedback_chunks.append(
            "Lint feedback:\n" + json.dumps(lint_result.to_dict(), indent=2, ensure_ascii=False)
        )
    if sim_report is not None:
        feedback_chunks.append(
            "Simulation feedback:\n" + json.dumps(sim_report.to_dict(), indent=2, ensure_ascii=False)
        )
    return PromptRequest(
        kind="rtl_repair",
        system_prompt=(
            "You repair synthesizable Verilog. "
            "Return only the full corrected Verilog module."
        ),
        user_prompt=(
            "Fix the RTL so that it satisfies the specification and resolves the reported issues.\n\n"
            f"Specification:\n{json.dumps(spec.to_dict(), indent=2, ensure_ascii=False)}\n\n"
            f"Current RTL:\n```verilog\n{previous_source}\n```\n\n"
            + "\n\n".join(feedback_chunks)
        ),
        metadata={
            "spec": spec,
            "previous_source": previous_source,
            "lint_result": lint_result,
            "sim_report": sim_report,
        },
    )


def build_tb_generation_request(spec: ModuleSpec, rtl_source: str) -> PromptRequest:
    return PromptRequest(
        kind="tb_generate",
        system_prompt=(
            "You write self-checking Verilog testbenches. "
            "Return only Verilog code, no explanation."
        ),
        user_prompt=(
            "Generate a self-checking testbench with clock/reset generation, "
            "stimulus, automatic checks, PASS/FAIL messaging, and VCD dumping.\n\n"
            f"Specification:\n{json.dumps(spec.to_dict(), indent=2, ensure_ascii=False)}\n\n"
            f"RTL:\n```verilog\n{rtl_source}\n```"
        ),
        metadata={"spec": spec, "rtl_source": rtl_source},
    )


def build_tb_repair_request(
    spec: ModuleSpec,
    rtl_source: str,
    previous_tb: str,
    sim_report: SimulationReport,
) -> PromptRequest:
    return PromptRequest(
        kind="tb_repair",
        system_prompt=(
            "You repair self-checking Verilog testbenches. "
            "Return only the full corrected testbench."
        ),
        user_prompt=(
            "Fix the testbench to improve observability or correct the checking logic.\n\n"
            f"Specification:\n{json.dumps(spec.to_dict(), indent=2, ensure_ascii=False)}\n\n"
            f"RTL:\n```verilog\n{rtl_source}\n```\n\n"
            f"Current testbench:\n```verilog\n{previous_tb}\n```\n\n"
            f"Simulation feedback:\n{json.dumps(sim_report.to_dict(), indent=2, ensure_ascii=False)}"
        ),
        metadata={
            "spec": spec,
            "rtl_source": rtl_source,
            "previous_tb": previous_tb,
            "sim_report": sim_report,
        },
    )


def source_from_path(path: Path) -> str:
    return path.read_text(encoding="utf-8")
