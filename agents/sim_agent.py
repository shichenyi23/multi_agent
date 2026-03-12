from __future__ import annotations

from pathlib import Path

from agents.base import BaseAgent
from contracts import ModuleSpec, RTLArtifact, SimulationReport, TestbenchArtifact
from tools.iverilog_wrapper import run_simulation


class SimulationAgent(BaseAgent):
    name = "simulation_agent"

    def run(
        self,
        spec: ModuleSpec,
        rtl: RTLArtifact,
        testbench: TestbenchArtifact,
    ) -> SimulationReport:
        return run_simulation(
            module_name=spec.module_name,
            rtl_path=Path(rtl.filepath),
            tb_path=Path(testbench.filepath),
            workdir=Path(testbench.filepath).parent,
        )

