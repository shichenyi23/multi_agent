from __future__ import annotations

from pathlib import Path

from agents.rtl_coder import RTLCoderAgent
from agents.sim_agent import SimulationAgent
from agents.spec_analyst import SpecAnalystAgent
from agents.synth_agent import SynthesisAgent
from agents.tb_agent import TestbenchAgent
from contracts import FailureCategory, Severity, SimulationFailure, SimulationReport, Stage, WorkflowState
from llm.backends import create_backend
from workflow.artifact_store import ArtifactStore


class WorkflowOrchestrator:
    def __init__(
        self,
        backend_name: str | None = None,
        max_lint_retries: int = 2,
        max_sim_retries: int = 2,
    ) -> None:
        backend = create_backend(backend_name)
        self.spec_agent = SpecAnalystAgent(backend=backend)
        self.rtl_agent = RTLCoderAgent(backend=backend)
        self.tb_agent = TestbenchAgent(backend=backend)
        self.sim_agent = SimulationAgent()
        self.synth_agent = SynthesisAgent()
        self.max_lint_retries = max_lint_retries
        self.max_sim_retries = max_sim_retries

    def run(
        self,
        module_dir: Path,
        generate_only: bool = False,
        strategy: str = "area",
        liberty_path: Path | None = None,
    ) -> WorkflowState:
        store = ArtifactStore(module_dir)
        draft = self._load_or_infer_spec(store)
        spec, clarifications = self.spec_agent.analyze(draft)

        module_name = draft.get("module_name", module_dir.name)
        state = WorkflowState(module_name=module_name, stage=Stage.INTAKE)
        state.clarifications = clarifications

        if any(item.severity == Severity.REQUIRED for item in clarifications):
            state.stage = Stage.NEEDS_CLARIFICATION
            store.save_json(store.clarifications_path, {"clarifications": [item.to_dict() for item in clarifications]})
            store.save_json(store.workflow_state_path, state.to_dict())
            return state

        state.stage = Stage.SPEC_READY
        store.save_json(store.clarifications_path, {"clarifications": [item.to_dict() for item in clarifications]})

        rtl = self._ensure_rtl_ready(spec, store, state)
        state.current_rtl = rtl
        state.stage = Stage.RTL_READY
        store.save_json(store.rtl_meta_path, rtl.to_dict())
        if rtl.lint is not None and not rtl.lint.success:
            state.stage = Stage.FAILED
            state.notes.append("RTL lint did not converge within the configured retry budget.")
            store.save_json(store.workflow_state_path, state.to_dict())
            return state

        tb = self._ensure_tb_ready(spec, store, state)
        state.current_tb = tb
        state.stage = Stage.TB_READY
        store.save_json(store.tb_meta_path, tb.to_dict())
        if tb.lint is not None and not tb.lint.success:
            state.stage = Stage.FAILED
            state.notes.append("TB lint did not converge within the configured retry budget.")
            store.save_json(store.workflow_state_path, state.to_dict())
            return state

        if generate_only:
            state.notes.append("Stopped after RTL/TB generation because `generate_only` was requested.")
            store.save_json(store.workflow_state_path, state.to_dict())
            return state

        sim_report, rtl, tb = self._run_simulation_loop(spec, store, state, rtl, tb)
        state.sim_report = sim_report
        state.current_rtl = rtl
        state.current_tb = tb
        if not sim_report.passed:
            state.stage = Stage.FAILED
            state.notes.append("Simulation did not pass. Inspect sim.json for structured failures.")
            store.save_json(store.workflow_state_path, state.to_dict())
            return state

        state.stage = Stage.SIM_PASSED

        synth_report = self.synth_agent.run(spec, rtl, strategy=strategy, liberty_path=liberty_path)
        state.synth_report = synth_report
        state.attempts["synth"] = state.attempts.get("synth", 0) + 1
        store.save_json(store.synth_path, synth_report.to_dict())
        if not synth_report.passed:
            state.stage = Stage.FAILED
            state.notes.append("Synthesis did not pass. Inspect synth.json for structured warnings and suggestions.")
            store.save_json(store.workflow_state_path, state.to_dict())
            return state

        state.stage = Stage.DONE
        store.save_json(store.workflow_state_path, state.to_dict())
        return state

    def _load_or_infer_spec(self, store: ArtifactStore) -> dict:
        if store.has_spec():
            return store.load_spec()
        if store.has_request():
            draft = self.spec_agent.draft_from_request(store.load_request(), module_name_hint=store.module_dir.name)
            if draft is not None:
                store.save_json(store.spec_path, draft)
                return draft
        raise FileNotFoundError(f"No spec.json or request.txt found under {store.module_dir}")

    def _ensure_rtl_ready(self, spec, store: ArtifactStore, state: WorkflowState):
        previous_source = None
        lint_feedback = None
        rtl = None
        for attempt in range(self.max_lint_retries + 1):
            version = f"v{state.attempts.get('rtl', 0)}"
            rtl = (
                self.rtl_agent.repair_from_lint(spec, previous_source, lint_feedback, store.rtl_path, version)
                if previous_source is not None and lint_feedback is not None
                else self.rtl_agent.generate(spec, store.rtl_path, version=version)
            )
            state.attempts["rtl"] = state.attempts.get("rtl", 0) + 1
            store.save_json(store.rtl_meta_path, rtl.to_dict())
            if rtl.lint is None or rtl.lint.success:
                return rtl
            previous_source = Path(rtl.filepath).read_text(encoding="utf-8")
            lint_feedback = rtl.lint
            state.notes.append(f"RTL lint repair attempt {attempt + 1} triggered.")
        return rtl

    def _ensure_tb_ready(self, spec, store: ArtifactStore, state: WorkflowState):
        previous_source = None
        lint_feedback = None
        tb = None
        for attempt in range(self.max_lint_retries + 1):
            version = f"v{state.attempts.get('tb', 0)}"
            tb = (
                self.tb_agent.repair(
                    spec=spec,
                    rtl_path=store.rtl_path,
                    previous_source=previous_source,
                    sim_feedback=lint_feedback,
                    output_path=store.tb_path,
                    version=version,
                )
                if previous_source is not None and lint_feedback is not None
                else self.tb_agent.generate(spec, store.rtl_path, store.tb_path, version=version)
            )
            state.attempts["tb"] = state.attempts.get("tb", 0) + 1
            store.save_json(store.tb_meta_path, tb.to_dict())
            if tb.lint is None or tb.lint.success:
                return tb
            previous_source = Path(tb.filepath).read_text(encoding="utf-8")
            lint_feedback = self._lint_to_sim_feedback(spec.module_name, tb.lint)
            state.notes.append(f"TB lint repair attempt {attempt + 1} triggered.")
        return tb

    def _run_simulation_loop(self, spec, store: ArtifactStore, state: WorkflowState, rtl, tb):
        sim_report = None
        for attempt in range(self.max_sim_retries + 1):
            sim_report = self.sim_agent.run(spec, rtl, tb)
            state.attempts["sim"] = state.attempts.get("sim", 0) + 1
            store.save_json(store.sim_path, sim_report.to_dict())
            if sim_report.passed:
                return sim_report, rtl, tb

            state.notes.append(f"Simulation repair attempt {attempt + 1} triggered.")
            if self._needs_tb_repair(sim_report):
                previous_tb = Path(tb.filepath).read_text(encoding="utf-8")
                tb = self.tb_agent.repair(
                    spec=spec,
                    rtl_path=Path(rtl.filepath),
                    previous_source=previous_tb,
                    sim_feedback=sim_report,
                    output_path=store.tb_path,
                    version=f"v{state.attempts.get('tb', 0)}",
                )
                state.attempts["tb"] = state.attempts.get("tb", 0) + 1
                store.save_json(store.tb_meta_path, tb.to_dict())
                if tb.lint is not None and not tb.lint.success:
                    tb = self._ensure_tb_ready(spec, store, state)
                continue

            previous_rtl = Path(rtl.filepath).read_text(encoding="utf-8")
            rtl = self.rtl_agent.repair_from_simulation(
                spec=spec,
                previous_source=previous_rtl,
                sim_feedback=sim_report,
                output_path=store.rtl_path,
                version=f"v{state.attempts.get('rtl', 0)}",
            )
            state.attempts["rtl"] = state.attempts.get("rtl", 0) + 1
            store.save_json(store.rtl_meta_path, rtl.to_dict())
            if rtl.lint is not None and not rtl.lint.success:
                rtl = self._ensure_rtl_ready(spec, store, state)

            tb = self.tb_agent.generate(
                spec=spec,
                rtl_path=Path(rtl.filepath),
                output_path=store.tb_path,
                version=f"v{state.attempts.get('tb', 0)}",
                sim_feedback=sim_report,
            )
            state.attempts["tb"] = state.attempts.get("tb", 0) + 1
            store.save_json(store.tb_meta_path, tb.to_dict())
            if tb.lint is not None and not tb.lint.success:
                tb = self._ensure_tb_ready(spec, store, state)
        return sim_report, rtl, tb

    @staticmethod
    def _needs_tb_repair(sim_report: SimulationReport) -> bool:
        if "TB_INCOMPLETE" in sim_report.run_log:
            return True
        return any("testbench" in failure.message.lower() for failure in sim_report.failures)

    @staticmethod
    def _lint_to_sim_feedback(module_name: str, lint_result) -> SimulationReport:
        failures = [
            SimulationFailure(category=FailureCategory.SYNTAX_ERROR, message=error)
            for error in lint_result.errors
        ]
        return SimulationReport(module_name=module_name, passed=False, compile_log=lint_result.stderr, failures=failures)
