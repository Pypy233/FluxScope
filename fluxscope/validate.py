"""Validate analysis-result JSON files against the schema and semantic rules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple

from pydantic import ValidationError

from .model import AnalysisResult, semantic_errors


def validate_data(data: dict) -> Tuple[AnalysisResult | None, List[str]]:
    try:
        result = AnalysisResult.model_validate(data)
    except ValidationError as exc:
        errs = []
        for e in exc.errors():
            loc = ".".join(str(p) for p in e["loc"])
            errs.append(f"{loc}: {e['msg']}")
        return None, errs
    return result, semantic_errors(result)


def validate_file(path: Path) -> List[str]:
    try:
        data = json.loads(Path(path).read_text())
    except json.JSONDecodeError as exc:
        return [f"invalid JSON: {exc}"]
    _, errs = validate_data(data)
    return errs
