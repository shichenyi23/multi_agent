from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

JsonValue = dict[str, Any] | list[Any] | str | int | float | bool | None


def _clean(value: Any) -> JsonValue:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items() if item is not None}
    return value


class Serializable:
    def to_dict(self) -> dict[str, Any]:
        return _clean(asdict(self))


class Stage(str, Enum):
    INTAKE = "intake"
    NEEDS_CLARIFICATION = "needs_clarification"
    SPEC_READY = "spec_ready"
    RTL_READY = "rtl_ready"
    TB_READY = "tb_ready"
    SIM_PASSED = "sim_passed"
    SYNTH_PASSED = "synth_passed"
    DONE = "done"
    FAILED = "failed"


class Severity(str, Enum):
    REQUIRED = "required"
    RECOMMENDED = "recommended"
    OPTIONAL = "optional"


class FailureCategory(str, Enum):
    SYNTAX_ERROR = "syntax_error"
    WIDTH_MISMATCH = "width_mismatch"
    LATCH_INFERRED = "latch_inferred"
    SIM_MISMATCH = "sim_mismatch"
    X_PROPAGATION = "x_propagation"
    TIMING_VIOLATION = "timing_violation"
    AREA_OVER_BUDGET = "area_over_budget"
    TOOL_ERROR = "tool_error"


@dataclass(slots=True)
class PortSpec(Serializable):
    name: str
    dir: str
    width: int | str = 1
    signed: bool = False
    description: str = ""


@dataclass(slots=True)
class ParameterSpec(Serializable):
    name: str
    default: int | str
    description: str = ""


@dataclass(slots=True)
class ClarificationRequest(Serializable):
    field: str
    question: str
    severity: Severity = Severity.REQUIRED
    rationale: str = ""


@dataclass(slots=True)
class ModuleSpec(Serializable):
    module_name: str
    ports: list[PortSpec]
    functional_spec: str
    summary: str = ""
    parameters: list[ParameterSpec] = field(default_factory=list)
    clock_strategy: str = ""
    reset_strategy: str = ""
    timing_requirements: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    test_points: list[str] = field(default_factory=list)
    submodules: list[str] = field(default_factory=list)


@dataclass(slots=True)
class LintResult(Serializable):
    tool: str
    success: bool
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""


@dataclass(slots=True)
class RTLArtifact(Serializable):
    module_name: str
    version: str
    filepath: str
    lint: LintResult | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TestbenchArtifact(Serializable):
    module_name: str
    version: str
    filepath: str
    lint: LintResult | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SimulationFailure(Serializable):
    category: FailureCategory
    message: str
    time_ps: int | None = None
    signal_hints: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SimulationReport(Serializable):
    module_name: str
    passed: bool
    compile_log: str = ""
    run_log: str = ""
    failures: list[SimulationFailure] = field(default_factory=list)
    suggested_instrumentation: list[str] = field(default_factory=list)
    vcd_path: str | None = None


@dataclass(slots=True)
class SynthesisReport(Serializable):
    module_name: str
    passed: bool
    strategy: str
    cell_count: int | None = None
    critical_path_ns: float | None = None
    warnings: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    raw_log: str = ""


@dataclass(slots=True)
class WorkflowState(Serializable):
    module_name: str
    stage: Stage
    clarifications: list[ClarificationRequest] = field(default_factory=list)
    current_rtl: RTLArtifact | None = None
    current_tb: TestbenchArtifact | None = None
    sim_report: SimulationReport | None = None
    synth_report: SynthesisReport | None = None
    attempts: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

