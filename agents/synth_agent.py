from __future__ import annotations

from pathlib import Path

from agents.base import BaseAgent
from contracts import ModuleSpec, RTLArtifact, SynthesisReport
from tools.yosys_wrapper import synthesize_verilog


class SynthesisAgent(BaseAgent):
    name = "synthesis_agent"

    def run(
        self,
        spec: ModuleSpec,
        rtl: RTLArtifact,
        strategy: str = "area",
        liberty_path: Path | None = None,
    ) -> SynthesisReport:
        return synthesize_verilog(
            rtl_path=Path(rtl.filepath),
            top_module=spec.module_name,
            strategy=strategy,
            liberty_path=liberty_path,
            cwd=Path(rtl.filepath).parent,
        )

