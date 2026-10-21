"""Build the FluxScope viewer into single, self-contained HTML files.

    .venv/bin/python frontend/build.py            # build both outputs
    .venv/bin/python frontend/build.py --check    # build and fail on external references

Outputs
  dist/fluxscope.html         viewer + all examples/*.example.json as built-in cases
  fluxscope/static/app.html   same viewer with an empty data slot (used by fluxscope/report.py)

No Node.js toolchain is needed: CSS and the plain-JS sources under
frontend/src/js are concatenated in file-name order and inlined together with
the JSON schema, so the result works offline from file://.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend" / "src"
VERSION = "0.1.0"


def embed_json(obj) -> str:
    """JSON safe to place inside <script type="application/json">."""
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def load_examples() -> list:
    cases = []
    for path in sorted((ROOT / "examples").glob("*.example.json")):
        cases.append(json.loads(path.read_text()))
    cases.sort(key=lambda c: c["meta"].get("title") or c["meta"]["id"])
    return cases


def assemble(data_payload: dict) -> str:
    template = (SRC / "index.html").read_text()
    css = "\n".join(p.read_text() for p in sorted((SRC / "css").glob("*.css")))
    js_parts = []
    for p in sorted((SRC / "js").glob("*.js")):
        js_parts.append(f"/* ---- {p.name} ---- */\n" + p.read_text())
    js = "\n".join(js_parts)
    if re.search(r"</script", js, re.I):
        raise SystemExit("JS sources must not contain '</script' (breaks inlining)")
    schema = json.loads((ROOT / "schema" / "fluxscope-analysis-0.1.schema.json").read_text())
    html = template
    # the data payload is the most arbitrary text, so it is substituted last
    for marker, value in (
        ("@VERSION@", VERSION),
        ("/*@CSS@*/", css),
        ("/*@SCHEMA@*/", embed_json(schema)),
        ("/*@CONFIG@*/", embed_json({"api": None})),
        ("/*@JS@*/", js),
        ("/*@DATA@*/", embed_json(data_payload)),
    ):
        if marker not in html:
            raise SystemExit(f"template marker {marker} missing")
        html = html.replace(marker, value, 1)
    return html


EXTERNAL = re.compile(r"""(?:src|href)\s*=\s*["'](?:https?:)?//|@import\s+url\(|url\(\s*["']?https?:""", re.I)


def check_offline(html: str, name: str) -> None:
    hits = EXTERNAL.findall(html)
    if hits:
        raise SystemExit(f"{name}: external resource references found: {hits[:5]}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="fail if outputs reference external resources")
    args = ap.parse_args(argv)

    cases = load_examples()
    default = cases[0]["meta"]["id"] if cases else None
    app_html = assemble({"mode": "app", "cases": cases, "default": default})
    tpl_html = assemble({"mode": "app", "cases": []})
    check_offline(app_html, "dist/fluxscope.html")
    check_offline(tpl_html, "fluxscope/static/app.html")

    out1 = ROOT / "dist" / "fluxscope.html"
    out2 = ROOT / "fluxscope" / "static" / "app.html"
    out1.parent.mkdir(parents=True, exist_ok=True)
    out2.parent.mkdir(parents=True, exist_ok=True)
    out1.write_text(app_html)
    out2.write_text(tpl_html)
    print(f"wrote {out1.relative_to(ROOT)} ({len(app_html) // 1024} KiB, {len(cases)} built-in cases)")
    print(f"wrote {out2.relative_to(ROOT)} ({len(tpl_html) // 1024} KiB, report template)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
