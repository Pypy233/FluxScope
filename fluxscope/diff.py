"""Semantic diff of two analysis results (e.g. before / after a code change)."""

from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple


def _labels(d: Dict[str, Any]) -> Dict[str, str]:
    out = {}
    for coll in ("assets", "entities", "positions", "operations"):
        for x in d.get(coll, []):
            out[x["id"]] = x.get("label") or x["id"]
    return out


def facts(d: Dict[str, Any]) -> Dict[str, Set[Tuple]]:
    L = _labels(d)
    lab = lambda i: L.get(i, i) if i else "-"  # noqa: E731
    out: Dict[str, Set[Tuple]] = {k: set() for k in ("flows", "operations", "positions", "relations", "exits", "unresolved", "assets")}
    for a in d.get("assets", []):
        ident = a.get("identity", {})
        out["assets"].add((ident.get("expression") or ident.get("contract") or a["id"], a["kind"]))
    for o in d.get("operations", []):
        out["operations"].add((o["id"], o["kind"], lab(o.get("initiator"))))
        for f in o.get("inputs", []) + o.get("outputs", []):
            out["flows"].add((o["id"], lab(f["asset"]), lab(f.get("from")), lab(f.get("to")), f.get("mechanism") or ""))
    for p in d.get("positions", []):
        out["positions"].add((lab(p["holder"]), lab(p["asset"]), p["kind"]))
    for r in d.get("relations", []):
        if r["kind"] in ("holds",):
            continue
        out["relations"].add((r["kind"], lab(r["source"]), lab(r["target"]), r["status"]["verification"]))
    for x in d.get("exits", []):
        trigger = lab(x["steps"][0].get("actor")) if x.get("steps") else "-"
        out["exits"].add((lab(x["position"]), x["label"], f"triggered by {trigger}", x["status"]["verification"]))
    for u in d.get("unresolved", []):
        out["unresolved"].add((u["kind"], u["text"]))
    return out


def diff(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Dict[str, List[Tuple]]]:
    fa, fb = facts(a), facts(b)
    res = {}
    for k in fa:
        added = sorted(fb[k] - fa[k])
        removed = sorted(fa[k] - fb[k])
        if added or removed:
            res[k] = {"added": added, "removed": removed}
    return res


def format_diff(d: Dict[str, Dict[str, List[Tuple]]]) -> str:
    if not d:
        return "no semantic differences"
    lines = []
    for k, v in d.items():
        lines.append(f"{k}:")
        for t in v["removed"]:
            lines.append("  - " + " | ".join(str(x) for x in t))
        for t in v["added"]:
            lines.append("  + " + " | ".join(str(x) for x in t))
    return "\n".join(lines)
