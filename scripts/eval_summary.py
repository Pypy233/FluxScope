"""Summarise eval/results/*.json (written by `fluxscope evaluate`) as Markdown.

    python scripts/eval_summary.py [eval/results/beefy-erc4626.json ...] > eval/results/summary.md
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DIMS = [("op_kind", "op kind"), ("caller", "caller"), ("claims", "claims P / R"), ("dependencies", "deps P / R"),
        ("dependencies_relaxed", "deps relaxed P / R"), ("exits", "exits P / R")]


def fmt(row, key):
    if key in ("op_kind", "caller"):
        return cell(row[key])
    p, r = row[f"{key}_precision"], row[f"{key}_recall"]
    return f"{cell(p)} / {cell(r)}"


def cell(s):
    if not s or s.get("mean") is None:
        return "–"
    if s.get("n", 1) > 1 and s["min"] != s["max"]:
        return f"{s['mean']:.2f} ({s['min']:.2f}–{s['max']:.2f})"
    return f"{s['mean']:.2f}"


def contribution(res):
    """Counts over all runs of what the model added to the rules result."""

    out = {}
    for name, c in (res.get("contribution") or {}).items():
        base = name.split("@")[0]
        if base == "llm-only":
            continue
        agg = out.setdefault(base, {"runs": 0, "added_correct": 0, "added_wrong": 0, "lost_correct": 0, "kinds_fixed": 0,
                                    "kinds_broken": 0})
        agg["runs"] += 1
        for dim in ("claims", "dependencies", "exits", "identity_questions"):
            d = c.get(dim) or {}
            for k in ("added_correct", "added_wrong", "lost_correct"):
                agg[k] += len(d.get(k, []))
        for sig, ch in (c.get("operations") or {}).items():
            k = ch.get("kind")
            if k:
                agg["kinds_fixed" if k["now_correct"] else "kinds_broken"] += 1
    return out


def main(paths):
    paths = [Path(p) for p in paths] or sorted(Path("eval/results").glob("*.json"))
    print("| project | config | runs | " + " | ".join(label for _, label in DIMS) + " |")
    print("|---|---|---|" + "---|" * len(DIMS))
    contribs = []
    for p in paths:
        res = json.loads(p.read_text())
        if "summary" not in res:
            continue
        proj = Path(res.get("project", p.stem)).name
        for cfg, row in res["summary"].items():
            print(f"| {proj} | {cfg} | {row['runs']} | " + " | ".join(fmt(row, k) for k, _ in DIMS) + " |")
        for cfg, agg in contribution(res).items():
            contribs.append((proj, cfg, agg))
    if contribs:
        print("\nRelations and kinds the LLM stage changed relative to rules only (summed over runs):\n")
        print("| project | config | runs | correct added | wrong added | correct lost | kinds fixed | kinds broken |")
        print("|---|---|---|---|---|---|---|---|")
        for proj, cfg, a in contribs:
            print(f"| {proj} | {cfg} | {a['runs']} | {a['added_correct']} | {a['added_wrong']} | {a['lost_correct']} | "
                  f"{a['kinds_fixed']} | {a['kinds_broken']} |")


if __name__ == "__main__":
    main(sys.argv[1:])
