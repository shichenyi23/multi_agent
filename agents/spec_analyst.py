from __future__ import annotations

from typing import Any

from agents.base import BaseAgent
from contracts import (
    ClarificationRequest,
    ModuleSpec,
    ParameterSpec,
    PortSpec,
    Severity,
)
from llm.parsing import extract_json_object
from llm.prompts import build_spec_request


class SpecAnalystAgent(BaseAgent):
    name = "spec_analyst"

    def draft_from_request(
        self,
        request_text: str,
        module_name_hint: str | None = None,
    ) -> dict[str, Any] | None:
        if self.backend is None:
            return None
        response = self.backend.generate(build_spec_request(request_text, module_name_hint))
        if response is None:
            return None
        return extract_json_object(response)

    def analyze(self, draft: dict[str, Any]) -> tuple[ModuleSpec | None, list[ClarificationRequest]]:
        clarifications = self._collect_clarifications(draft)
        if any(item.severity == Severity.REQUIRED for item in clarifications):
            return None, clarifications

        spec = ModuleSpec(
            module_name=draft["module_name"],
            summary=draft.get("summary", ""),
            ports=[self._parse_port(item) for item in draft["ports"]],
            parameters=[self._parse_parameter(item) for item in draft.get("parameters", [])],
            clock_strategy=draft.get("clock_strategy", ""),
            reset_strategy=draft.get("reset_strategy", ""),
            timing_requirements=list(draft.get("timing_requirements", [])),
            constraints=list(draft.get("constraints", [])),
            functional_spec=draft["functional_spec"],
            test_points=list(draft.get("test_points", [])),
            submodules=list(draft.get("submodules", [])),
        )
        return spec, clarifications

    def _collect_clarifications(self, draft: dict[str, Any]) -> list[ClarificationRequest]:
        items: list[ClarificationRequest] = []
        if not draft.get("module_name"):
            items.append(
                ClarificationRequest(
                    field="module_name",
                    question="What is the module name?",
                    rationale="The orchestrator needs a stable directory and top-module identifier.",
                )
            )
        if not draft.get("ports"):
            items.append(
                ClarificationRequest(
                    field="ports",
                    question="Please provide the full port list with directions and widths.",
                    rationale="RTL and testbench generation cannot start without a stable interface.",
                )
            )
        if not draft.get("functional_spec"):
            items.append(
                ClarificationRequest(
                    field="functional_spec",
                    question="What is the intended functional behavior of the module?",
                    rationale="The coder and testbench agents both require the target behavior.",
                )
            )

        ports = draft.get("ports", [])
        for index, port in enumerate(ports):
            if "name" not in port or "dir" not in port:
                items.append(
                    ClarificationRequest(
                        field=f"ports[{index}]",
                        question=f"Port entry {index} is missing a name or direction.",
                        rationale="Every port needs a stable identifier and direction.",
                    )
                )
            if "width" not in port:
                items.append(
                    ClarificationRequest(
                        field=f"ports[{index}].width",
                        question=f"What is the width of port `{port.get('name', index)}`?",
                        rationale="Implicit widths create silent RTL/testbench mismatches.",
                    )
                )

        has_clock = any(port.get("name") in {"clk", "clock"} for port in ports) or bool(
            draft.get("clock_strategy")
        )
        has_reset = any("rst" in str(port.get("name", "")) for port in ports) or bool(
            draft.get("reset_strategy")
        )
        if has_clock and not draft.get("clock_strategy"):
            items.append(
                ClarificationRequest(
                    field="clock_strategy",
                    question="Please define the clocking strategy, for example `posedge_clk`.",
                    severity=Severity.RECOMMENDED,
                    rationale="Clocking semantics should be explicit before RTL generation.",
                )
            )
        if has_clock and not has_reset:
            items.append(
                ClarificationRequest(
                    field="reset_strategy",
                    question="Does the sequential logic need a reset, and if so what polarity/type?",
                    severity=Severity.RECOMMENDED,
                    rationale="Reset behavior affects both RTL and testbench initialization.",
                )
            )
        if "ram" in str(draft.get("functional_spec", "")).lower() and not draft.get(
            "timing_requirements"
        ):
            items.append(
                ClarificationRequest(
                    field="timing_requirements",
                    question="Please specify the RAM read/write latency requirements.",
                    severity=Severity.RECOMMENDED,
                    rationale="Memory timing is ambiguous unless latency is stated explicitly.",
                )
            )
        return items

    @staticmethod
    def _parse_port(data: dict[str, Any]) -> PortSpec:
        return PortSpec(
            name=data["name"],
            dir=data["dir"],
            width=data.get("width", 1),
            signed=bool(data.get("signed", False)),
            description=data.get("description", ""),
        )

    @staticmethod
    def _parse_parameter(data: dict[str, Any]) -> ParameterSpec:
        return ParameterSpec(
            name=data["name"],
            default=data["default"],
            description=data.get("description", ""),
        )
