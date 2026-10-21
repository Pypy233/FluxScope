"""Embed analysis results into the single-file viewer (offline HTML report)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Union

from .model import AnalysisResult

STATIC = Path(__file__).resolve().parent / "static"
TEMPLATE = STATIC / "app.html"

_DATA_RE = re.compile(r'(<script type="application/json" id="fluxscope-data">)(.*?)(</script>)', re.S)
_CONFIG_RE = re.compile(r'(<script type="application/json" id="fluxscope-config">)(.*?)(</script>)', re.S)


def _json_for_html(obj: Any) -> str:
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    # keep the payload inert inside <script>: no "</script", "<!--" or line separators
    return text.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _as_dict(r: Union[AnalysisResult, Dict[str, Any]]) -> Dict[str, Any]:
    return r.to_json_dict() if isinstance(r, AnalysisResult) else r


def template_text(template: Optional[Path] = None) -> str:
    path = Path(template) if template else TEMPLATE
    if not path.exists():
        raise FileNotFoundError(f"viewer template {path} not found; build it with `python frontend/build.py`")
    return path.read_text(encoding="utf-8")


def render_html(results: Iterable[Union[AnalysisResult, Dict[str, Any]]], mode: str = "report",
                api: Optional[str] = None, template: Optional[Path] = None, extra: Optional[Dict[str, Any]] = None) -> str:
    cases = [_as_dict(r) for r in results]
    tpl = template_text(template)
    data = {"mode": mode, "cases": cases}
    if cases:
        data["default"] = cases[0]["meta"]["id"]
    if extra:
        data.update(extra)
    if not _DATA_RE.search(tpl) or not _CONFIG_RE.search(tpl):
        raise ValueError("viewer template has no fluxscope-data / fluxscope-config slots")
    out = _DATA_RE.sub(lambda m: m.group(1) + _json_for_html(data) + m.group(3), tpl, count=1)
    out = _CONFIG_RE.sub(lambda m: m.group(1) + _json_for_html({"api": api}) + m.group(3), out, count=1)
    return out


def write_report(result: Union[AnalysisResult, Dict[str, Any]], out: Path, template: Optional[Path] = None) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html([result], mode="report", template=template), encoding="utf-8")
    return out
