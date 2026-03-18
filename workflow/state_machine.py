from __future__ import annotations

import logging
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
        logging.info(f"开始处理模块: {module_dir.name}")
        store = ArtifactStore(module_dir)
        draft = self._load_or_infer_spec(store)

        # Auto-fix common spec issues before analysis
        draft = self._auto_fix_spec(draft)
        store.save_json(store.spec_path, draft)

        spec, clarifications = self.spec_agent.analyze(draft)

        module_name = draft.get("module_name", module_dir.name)
        state = WorkflowState(module_name=module_name, stage=Stage.INTAKE)
        state.clarifications = clarifications
        logging.info(f"规格分析完成: {module_name}")

        # Auto-fix clarifications if possible, instead of stopping
        if any(item.severity == Severity.REQUIRED for item in clarifications):
            fixed_draft, fixed = self._try_fix_clarifications(draft, clarifications)
            if fixed:
                logging.info("自动修复了规格问题")
                draft = fixed_draft
                store.save_json(store.spec_path, draft)
                spec, clarifications = self.spec_agent.analyze(draft)
                state.clarifications = clarifications

        # If still have REQUIRED clarifications, try one more time with repair
        if any(item.severity == Severity.REQUIRED for item in clarifications):
            # Try to regenerate spec with clarification feedback
            if self.spec_agent.backend is not None:
                logging.info("尝试重新生成规格...")
                from llm.prompts import build_spec_request
                request = build_spec_request(store.load_request(), module_name_hint=module_name)
                response = self.spec_agent.backend.generate(request)
                if response:
                    import json
                    from llm.parsing import extract_json_object
                    new_draft = extract_json_object(response)
                    if new_draft:
                        new_draft = self._auto_fix_spec(new_draft)
                        store.save_json(store.spec_path, new_draft)
                        spec, clarifications = self.spec_agent.analyze(new_draft)
                        state.clarifications = clarifications

        # Final check - if still has REQUIRED clarifications, proceed with best effort
        if any(item.severity == Severity.REQUIRED for item in clarifications):
            # Log warning but try to continue with partial spec
            logging.warning(f"仍有澄清未解决: {[c.field for c in clarifications]}, 尝试继续...")
            state.notes.append(f"未解决的澄清: {[c.field for c in clarifications]}")

        state.stage = Stage.SPEC_READY
        store.save_json(store.clarifications_path, {"clarifications": [item.to_dict() for item in clarifications]})

        logging.info("开始生成RTL...")
        rtl = self._ensure_rtl_ready(spec, store, state)
        state.current_rtl = rtl
        state.stage = Stage.RTL_READY
        store.save_json(store.rtl_meta_path, rtl.to_dict())
        logging.info(f"RTL生成完成, lint: {rtl.lint.success if rtl.lint else 'N/A'}")
        if rtl.lint is not None and not rtl.lint.success:
            state.stage = Stage.FAILED
            state.notes.append("RTL lint did not converge within the configured retry budget.")
            store.save_json(store.workflow_state_path, state.to_dict())
            logging.error("RTL lint失败")
            return state

        logging.info("开始生成Testbench...")
        tb = self._ensure_tb_ready(spec, store, state)
        state.current_tb = tb
        state.stage = Stage.TB_READY
        store.save_json(store.tb_meta_path, tb.to_dict())
        logging.info(f"Testbench生成完成, lint: {tb.lint.success if tb.lint else 'N/A'}")
        if tb.lint is not None and not tb.lint.success:
            state.stage = Stage.FAILED
            state.notes.append("TB lint did not converge within the configured retry budget.")
            store.save_json(store.workflow_state_path, state.to_dict())
            logging.error("TB lint失败")
            return state

        if generate_only:
            state.notes.append("Stopped after RTL/TB generation because `generate_only` was requested.")
            store.save_json(store.workflow_state_path, state.to_dict())
            return state

        logging.info("开始仿真...")
        sim_report, rtl, tb = self._run_simulation_loop(spec, store, state, rtl, tb)
        state.sim_report = sim_report
        state.current_rtl = rtl
        state.current_tb = tb
        logging.info(f"仿真完成, passed: {sim_report.passed}")
        if not sim_report.passed:
            state.stage = Stage.FAILED
            state.notes.append("Simulation did not pass. Inspect sim.json for structured failures.")
            store.save_json(store.workflow_state_path, state.to_dict())
            logging.error(f"仿真失败: {len(sim_report.failures)} failures")
            return state

        state.stage = Stage.SIM_PASSED

        logging.info("开始综合...")
        synth_report = self.synth_agent.run(spec, rtl, strategy=strategy, liberty_path=liberty_path)
        state.synth_report = synth_report
        state.attempts["synth"] = state.attempts.get("synth", 0) + 1
        store.save_json(store.synth_path, synth_report.to_dict())
        logging.info(f"综合完成, passed: {synth_report.passed}, cells: {synth_report.cell_count}")
        if not synth_report.passed:
            state.stage = Stage.FAILED
            state.notes.append("Synthesis did not pass. Inspect synth.json for structured warnings and suggestions.")
            store.save_json(store.workflow_state_path, state.to_dict())
            logging.error("综合失败")
            return state

        state.stage = Stage.DONE
        logging.info(f"全部完成! 模块: {module_name}, 单元数: {synth_report.cell_count}")
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
    def _auto_fix_spec(draft: dict) -> dict:
        """Automatically fix common spec issues."""
        import copy
        draft = copy.deepcopy(draft)

        # Fix ports: ensure all ports have 'dir' field
        ports = draft.get("ports", [])
        for port in ports:
            # Handle both "direction" and missing direction
            if "direction" in port and "dir" not in port:
                port["dir"] = port.pop("direction")
            # Try to infer direction from name
            if "dir" not in port or not port.get("dir"):
                name = port.get("name", "").lower()
                if name in ["clk", "clock", "rst", "rst_n", "reset", "en", "enable", "we", "wr_en", "rd_en"]:
                    port["dir"] = "input"
                elif name in ["dout", "data_out", "q", "out", "full", "empty", "cout", "valid"]:
                    port["dir"] = "output"
                else:
                    # Default to input for unknown ports
                    port["dir"] = "input"

            # Ensure width exists, default to 1
            if "width" not in port:
                port["width"] = 1

        # Fix parameters: ensure all have 'default' field
        params = draft.get("parameters", [])
        for param in params:
            if "default" not in param and "value" in param:
                param["default"] = param.pop("value")
            elif "default" not in param:
                param["default"] = 0

        return draft

    def _try_fix_clarifications(self, draft: dict, clarifications: list) -> tuple[dict, bool]:
        """Try to automatically fix clarifications."""
        import copy
        draft = copy.deepcopy(draft)

        fixed = False
        for cl in clarifications:
            if cl.severity != Severity.REQUIRED:
                continue

            field = cl.field
            # Handle ports[index] format
            if field.startswith("ports[") and field.endswith("]"):
                try:
                    idx = int(field[6:-1])
                    if 0 <= idx < len(draft.get("ports", [])):
                        port = draft["ports"][idx]
                        # Try to infer missing fields
                        if "name" not in port or not port.get("name"):
                            # Generate a name based on index
                            port_names = ["clk", "rst_n", "en", "data", "addr", "dout", "we", "wr_en", "rd_en"]
                            port["name"] = port_names[idx] if idx < len(port_names) else f"signal_{idx}"
                        if "dir" not in port:
                            name = port.get("name", "").lower()
                            if name in ["clk", "clock", "rst", "rst_n", "reset", "en", "we"]:
                                port["dir"] = "input"
                            else:
                                port["dir"] = "output"
                        if "width" not in port:
                            port["width"] = 1
                        fixed = True
                        logging.info(f"自动修复端口 {idx}: {port.get('name')}")
                except (ValueError, IndexError):
                    pass

        return draft, fixed

    @staticmethod
    def _lint_to_sim_feedback(module_name: str, lint_result) -> SimulationReport:
        failures = [
            SimulationFailure(category=FailureCategory.SYNTAX_ERROR, message=error)
            for error in lint_result.errors
        ]
        return SimulationReport(module_name=module_name, passed=False, compile_log=lint_result.stderr, failures=failures)
