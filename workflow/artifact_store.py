from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ArtifactStore:
    def __init__(self, module_dir: Path) -> None:
        self.module_dir = module_dir
        self.module_dir.mkdir(parents=True, exist_ok=True)

    @property
    def spec_path(self) -> Path:
        return self.module_dir / "spec.json"

    @property
    def request_path(self) -> Path:
        return self.module_dir / "request.txt"

    @property
    def clarifications_path(self) -> Path:
        return self.module_dir / "clarifications.json"

    @property
    def rtl_path(self) -> Path:
        return self.module_dir / "generated_rtl.v"

    @property
    def tb_path(self) -> Path:
        return self.module_dir / "generated_tb.v"

    @property
    def rtl_meta_path(self) -> Path:
        return self.module_dir / "rtl_meta.json"

    @property
    def tb_meta_path(self) -> Path:
        return self.module_dir / "tb_meta.json"

    @property
    def sim_path(self) -> Path:
        return self.module_dir / "sim.json"

    @property
    def synth_path(self) -> Path:
        return self.module_dir / "synth.json"

    @property
    def workflow_state_path(self) -> Path:
        return self.module_dir / "workflow_state.json"

    def load_spec(self) -> dict[str, Any]:
        return self._read_json(self.spec_path)

    def has_spec(self) -> bool:
        return self.spec_path.exists()

    def has_request(self) -> bool:
        return self.request_path.exists()

    def load_request(self) -> str:
        return self.request_path.read_text(encoding="utf-8")

    def save_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))
