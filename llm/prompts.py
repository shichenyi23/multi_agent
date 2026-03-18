from __future__ import annotations

import json
from pathlib import Path

from contracts import LintResult, ModuleSpec, SimulationReport
from llm.backends import PromptRequest


def build_spec_request(request_text: str, module_name_hint: str | None = None) -> PromptRequest:
    system_prompt = """You are a specification analyst for RTL design.
Your job is to convert user natural language descriptions into a strict JSON design contract.
This contract will be the single source of truth for RTL generation, verification, and debugging.

IMPORTANT REQUIREMENTS:
1. Extract ALL ports explicitly - every signal must have name, direction (input/output), and width
2. Define clock strategy explicitly - which edge (posedge/negedge) samples data
3. Define reset strategy explicitly - polarity (active_high/active_low), synchronization (sync/async)
4. For sequential logic, specify output delay expectations
5. Infer test points from the functional description
6. Always include width for ports (default to 1 if single bit)
7. Use "dir" field (not "direction") for port direction
8. Use "default" field for parameters

Output ONLY valid JSON, no explanation."""
    user_prompt = f"""Convert this user request into a JSON design contract with these EXACT fields:

{{
  "module_name": "string - must be valid Verilog identifier",
  "summary": "string - one line description",
  "ports": [
    {{
      "name": "string - valid Verilog identifier",
      "dir": "input OR output",
      "width": number or string (parameter name),
      "signed": boolean,
      "description": "string"
    }}
  ],
  "parameters": [
    {{
      "name": "string",
      "default": number or expression,
      "description": "string"
    }}
  ],
  "clock_strategy": "posedge_clk OR negedge_clk OR combinational",
  "reset_strategy": "sync_active_high OR sync_active_low OR async_active_high OR async_active_low OR none",
  "timing_requirements": ["list of timing constraints"],
  "constraints": ["list of design constraints"],
  "functional_spec": "string - cycle-accurate behavior description",
  "test_points": ["list of test objectives"],
  "submodules": []
}}

Analyze the request and infer missing information:
- Clock: if not specified, assume posedge_clk for sequential logic
- Reset: if not specified, assume sync_active_low for sequential logic with registers
- Port directions: infer from signal name (clk, rst, en, we -> input; dout, q, valid -> output)
- Widths: infer from context (8-bit -> 8, 32-bit -> 32)

Request:
{request_text}

Output ONLY the JSON, no markdown fences, no explanation."""
    return PromptRequest(
        kind="spec_from_request",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
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
