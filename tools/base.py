from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess


@dataclass(slots=True)
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        return self.returncode == 0


def run_command(command: list[str], cwd: Path | None = None) -> CommandResult:
    executable = shutil.which(command[0])
    if executable is None:
        return CommandResult(
            command=command,
            returncode=127,
            stdout="",
            stderr=f"Tool `{command[0]}` was not found in PATH.",
        )

    process = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        check=False,
        capture_output=True,
        text=True,
    )
    return CommandResult(
        command=command,
        returncode=process.returncode,
        stdout=process.stdout,
        stderr=process.stderr,
    )

