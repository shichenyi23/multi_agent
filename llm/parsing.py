from __future__ import annotations

import json
import re
from typing import Any


CODE_BLOCK_RE = re.compile(r"```(?:verilog|systemverilog|sv)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_code_block(text: str) -> str:
    match = CODE_BLOCK_RE.search(text)
    if match:
        return match.group(1).strip() + "\n"
    return text.strip() + "\n"


def extract_json_object(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or start >= end:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None

