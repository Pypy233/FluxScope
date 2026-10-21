"""Compare analysis configurations against a hand-annotated ground truth.

Configurations:

* ``rules``     – static analysis + rules/adapters (no model);
* ``llm-only``  – the model reads the source code and answers in the same
                  vocabulary (no extracted facts);
* ``combined``  – static analysis + rules + LLM stage;
* ``combined+checked`` – the combined result without model-proposed
  relations that the checker left ``pending`` / ``contradicted``.

Scores are reported per dimension (operation kind, caller binding, claims,
dependencies, exits, identity questions) so that the contribution of the model
can be read off separately: what it adds that is correct, and what it adds
that is wrong.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# canonicalisation of analysis results
# ---------------------------------------------------------------------------


class Canon:
    def __init__(self, truth: Dict[str, Any], result: Dict[str, Any]):
        self.t = truth
        self.r = result
        self.ents = {e["id"]: e for e in result.get("entities", [])}
        self.assets = {a["id"]: a for a in result.get("assets", [])}
        self.pos = {p["id"]: p for p in result.get("positions", [])}
        self.ali = truth.get("aliases", {})

    def caller(self, eid: Optional[str]) -> str:
        """Caller vocabulary may name a party differently from the holder vocabulary."""

        if eid and eid in self.ali.get("callers", {}):
            return self.ali["callers"][eid]
        return self.entity(eid)

    def entity(self, eid: Optional[str]) -> str:
        if not eid:
            return "-"
        if eid in self.ali.get("entities", {}):
            return self.ali["entities"][eid]
        if eid.startswith("ent:param:"):
            return "any"
        label = self.ents.get(eid, {}).get("label", "")
        for frag, name in self.ali.get("entity_labels", {}).items():
            if frag in label:
                return name
        return eid

    def asset(self, aid: str) -> str:
        return self.ali.get("assets", {}).get(aid, aid)

    def position(self, pid: str) -> str:
        if pid in self.ali.get("positions", {}):
            return self.ali["positions"][pid]
        p = self.pos.get(pid)
        if p is None:
            return pid
        return f"{self.entity(p['holder'])}:{self.asset(p['asset'])}"

    def node(self, nid: str) -> str:
        if nid in self.pos:
            return self.position(nid)
        if nid in self.assets:
            return self.asset(nid)
        return self.entity(nid)


def _holder(x: str) -> str:
    """Relaxed endpoint: a position key holder:asset is reduced to its holder."""

    return x.split(":", 1)[0]


def _relaxed(pairs: Set) -> Set:
    return {(_holder(a), _holder(b)) for a, b in pairs if _holder(a) != _holder(b)}


def _pr(found: Set, truth: Set) -> Dict[str, Any]:
    tp = found & truth
    return {"tp": len(tp), "fp": len(found - truth), "fn": len(truth - found),
            "precision": round(len(tp) / len(found), 3) if found else None,
            "recall": round(len(tp) / len(truth), 3) if truth else None,
            "missed": sorted(map(str, truth - found)), "extra": sorted(map(str, found - truth))}


_DECL = re.compile(r"\b(contract|interface|library|enum)\s+([A-Za-z_]\w*)")
_SIG = re.compile(r"^(.*?)\((.*)\)$")
_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_ELEMENTARY = re.compile(r"^(address|bool|string|bytes\d*|u?int\d*|u?fixed[\dx]*)$")


def declared_types(project: Optional[Path]) -> Tuple[Set[str], Set[str]]:
    """Names of the contracts / interfaces / libraries and of the enums declared in a project's sources."""

    contracts: Set[str] = set()
    enums: Set[str] = set()
    for f in sorted(Path(project).rglob("*.sol")) if project else []:
        if not f.is_file() or {"out", "cache", "node_modules"} & set(f.relative_to(project).parts[:-1]):
            continue
        for kw, name in _DECL.findall(_COMMENT.sub(" ", f.read_text(errors="replace"))):
            (enums if kw == "enum" else contracts).add(name)
    return contracts, enums


def _signature_canon(result: Dict[str, Any], types: Optional[Tuple[Set[str], Set[str]]] = None):
    """Entry-point keys compared in canonical ABI form: contract and interface types are ``address``,
    enums ``uint8`` (annotations follow the ABI, the analyzer keeps the declared type names)."""

    contracts, enums = (set(types[0]), set(types[1])) if types else (set(), set())
    for s in result.get("sources", []):
        for kw, name in _DECL.findall(_COMMENT.sub(" ", s.get("content") or "")):
            (enums if kw == "enum" else contracts).add(name)

    def one(t: str) -> str:
        t = t.strip()
        base, arr = re.match(r"^([^\[]*)(.*)$", t).groups()
        base = base.split(".")[-1].strip()
        if base == "address payable":
            base = "address"
        elif _ELEMENTARY.match(base):
            pass
        elif base in contracts:
            base = "address"
        elif base in enums:
            base = "uint8"
        return base + arr

    def canon(sig: str) -> str:
        m = _SIG.match(sig)
        if not m or not m.group(2).strip():
            return sig
        return f"{m.group(1)}({','.join(one(t) for t in m.group(2).split(','))})"

    return canon


def score_result(truth: Dict[str, Any], result: Dict[str, Any],
                 types: Optional[Tuple[Set[str], Set[str]]] = None) -> Dict[str, Any]:
    c = Canon(truth, result)
    canon = _signature_canon(result, types)
    roots = {}
    for o in result.get("operations", []):
        if o.get("parent") is None and o.get("entrypoint"):
            sig = canon(f"{o['entrypoint']['contract']}.{o['entrypoint']['signature']}")
            roots[sig] = o
    kind_ok, caller_ok, missing = [], [], []
    by_support = {"rule": [0, 0], "llm": [0, 0], "adapter": [0, 0]}
    details: Dict[str, Dict[str, Any]] = {}
    for sig, exp in truth["operations"].items():
        o = roots.get(canon(sig))
        if o is None:
            missing.append(sig)
            kind_ok.append(False)
            caller_ok.append(False)
            details[sig] = {"kind": None, "caller": None, "kind_ok": False, "caller_ok": False}
            continue
        ok = o["kind"] in exp["kind"]
        kind_ok.append(ok)
        sup = o["status"]["support"]
        if sup in by_support:
            by_support[sup][0] += int(ok)
            by_support[sup][1] += 1
        caller = c.caller(o.get("initiator"))
        caller_ok.append(caller == exp["caller"])
        details[sig] = {"kind": o["kind"], "caller": caller, "kind_ok": ok, "caller_ok": caller == exp["caller"],
                        "support": sup}
    claims = {(c.node(r["source"]), c.node(r["target"])) for r in result.get("relations", []) if r["kind"] == "claim"}
    deps = {(c.node(r["source"]), c.node(r["target"])) for r in result.get("relations", []) if r["kind"] == "depends_on"}
    exits = set()
    for x in result.get("exits", []):
        o = next((o for o in result["operations"] if o["id"] == x["steps"][0]["operation"]), None)
        if o and o.get("entrypoint"):
            exits.add((c.position(x["position"]), canon(f"{o['entrypoint']['contract']}.{o['entrypoint']['signature']}")))
    truth_exits = {(p, canon(s)) for p, sigs in truth["exits"].items() for s in sigs}
    idq = set()
    for u in result.get("unresolved", []):
        if u["kind"] in ("identity", "llm_question"):
            subj = [c.node(s) for s in u.get("subjects", []) if s in c.assets]
            if len(subj) >= 2:
                idq.add(tuple(sorted(subj[:2])))
    for r in result.get("relations", []):
        if r["kind"] == "identity" and r["status"]["verification"] != "supported":
            idq.add(tuple(sorted((c.node(r["source"]), c.node(r["target"])))))
    truth_idq = {tuple(sorted(x)) for x in truth["identity_questions"]}
    return {
        "operation_kind": {"accuracy": round(sum(kind_ok) / len(kind_ok), 3), "n": len(kind_ok),
                           "by_support": {k: v for k, v in by_support.items() if v[1]}},
        "caller": {"accuracy": round(sum(caller_ok) / len(caller_ok), 3), "n": len(caller_ok)},
        "operations_missing": missing,
        "operations": details,
        "claims": _pr(claims, {tuple(x) for x in truth["claims"]}),
        "dependencies": _pr(deps, {tuple(x) for x in truth["dependencies"]}),
        "dependencies_relaxed": _pr(_relaxed(deps), _relaxed({tuple(x) for x in truth["dependencies"]})),
        "exits": _pr(exits, truth_exits),
        "identity_questions": _pr(idq, truth_idq),
    }


# ---------------------------------------------------------------------------
# LLM-only baseline
# ---------------------------------------------------------------------------

BASELINE_VERSION = "baseline-v1"
BASELINE_PROMPT = """You are helping a smart-contract auditor. Read the Solidity source and describe its asset operations
and holdings using ONLY the vocabulary given. For each listed entry point give the economic kind and who may call it.
List claims (a position can obtain part of another position's asset through an exit), dependencies (value or availability
depends on another party), exits (entry points that exit a position) and asset identity questions you cannot settle from
the code. Positions are written holder:asset with the given holder and asset keys. Answer in English."""


def _baseline_schema(kinds: List[str]) -> Dict[str, Any]:
    def strict(props):
        return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}

    pair = {"type": "array", "items": {"type": "string"}}
    return strict({
        "operations": {"type": "array", "items": strict({"signature": {"type": "string"},
                                                         "kind": {"type": "string", "enum": kinds},
                                                         "caller": {"type": "string"}})},
        "claims": {"type": "array", "items": pair},
        "dependencies": {"type": "array", "items": pair},
        "exits": {"type": "array", "items": strict({"position": {"type": "string"},
                                                    "operations": {"type": "array", "items": {"type": "string"}}})},
        "identity_questions": {"type": "array", "items": pair},
    })


def baseline_sources(truth: Dict[str, Any], project: Path) -> List[Path]:
    """Source files shown to the LLM-only baseline: ``truth["sources"]``
    (glob patterns relative to the project) or every ``.sol`` file outside
    vendored dependency folders."""

    pats = truth.get("sources")
    if pats:
        found = {q for pat in pats for q in project.glob(pat) if q.is_file()}
        return sorted(found)
    skip = ("lib/", "node_modules/")
    return sorted(q for q in project.rglob("*.sol") if not q.relative_to(project).as_posix().startswith(skip))


def run_baseline(truth: Dict[str, Any], provider, project: Path, sample: int = 0) -> Dict[str, Any]:
    from .model import OperationKind

    files = []
    for p in baseline_sources(truth, project):
        rel = p.relative_to(project).as_posix()
        files.append(f"=== {rel} ===\n" + p.read_text().replace("\r\n", "\n"))
    user = (f"VOCABULARY:\n{json.dumps(truth['vocabulary'], indent=1)}\n\n"
            f"ENTRY POINTS:\n" + "\n".join(truth["operations"]) + "\n\nSOURCE:\n" + "\n\n".join(files))
    schema = _baseline_schema([k.value for k in OperationKind])
    data, rec = provider.complete_json("baseline", BASELINE_VERSION, BASELINE_PROMPT, user, schema, sample=sample)
    ops_truth = truth["operations"]
    kind_ok = caller_ok = 0
    got = {o["signature"]: o for o in data.get("operations", [])}
    details: Dict[str, Dict[str, Any]] = {}
    for sig, exp in ops_truth.items():
        o = got.get(sig)
        k_ok = bool(o) and o["kind"] in exp["kind"]
        c_ok = bool(o) and o["caller"] == exp["caller"]
        kind_ok += int(k_ok)
        caller_ok += int(c_ok)
        details[sig] = {"kind": o["kind"] if o else None, "caller": o["caller"] if o else None,
                        "kind_ok": k_ok, "caller_ok": c_ok}
    exits = {(x["position"], s) for x in data.get("exits", []) for s in x.get("operations", [])}
    return {
        "operation_kind": {"accuracy": round(kind_ok / len(ops_truth), 3), "n": len(ops_truth)},
        "caller": {"accuracy": round(caller_ok / len(ops_truth), 3), "n": len(ops_truth)},
        "operations": details,
        "claims": _pr({tuple(x[:2]) for x in data.get("claims", []) if len(x) >= 2}, {tuple(x) for x in truth["claims"]}),
        "dependencies": _pr({tuple(x[:2]) for x in data.get("dependencies", []) if len(x) >= 2},
                            {tuple(x) for x in truth["dependencies"]}),
        "dependencies_relaxed": _pr(_relaxed({tuple(x[:2]) for x in data.get("dependencies", []) if len(x) >= 2}),
                                    _relaxed({tuple(x) for x in truth["dependencies"]})),
        "exits": _pr(exits, {(p, s) for p, sigs in truth["exits"].items() for s in sigs}),
        "identity_questions": _pr({tuple(sorted(x[:2])) for x in data.get("identity_questions", []) if len(x) >= 2},
                                  {tuple(sorted(x)) for x in truth["identity_questions"]}),
        "llm_call": rec.as_dict(),
    }


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------


DIMENSIONS = ("claims", "dependencies", "exits", "identity_questions")


def contribution(rules: Dict[str, Any], other: Dict[str, Any]) -> Dict[str, Any]:
    """What a configuration changes relative to ``rules``: items it adds that
    are correct / wrong, and correct items it loses."""

    out: Dict[str, Any] = {}
    for dim in DIMENSIONS:
        a, b = rules.get(dim) or {}, other.get(dim) or {}
        if not a or not b:
            continue
        a_tp = set(_found_correct(a))
        b_tp = set(_found_correct(b))
        out[dim] = {"added_correct": sorted(b_tp - a_tp),
                    "added_wrong": sorted(set(b.get("extra", [])) - set(a.get("extra", []))),
                    "lost_correct": sorted(a_tp - b_tp)}
    ops = {}
    for sig, d in (other.get("operations") or {}).items():
        r = (rules.get("operations") or {}).get(sig, {})
        for field_ in ("kind", "caller"):
            if d.get(f"{field_}_ok") != r.get(f"{field_}_ok"):
                ops.setdefault(sig, {})[field_] = {"rules": r.get(field_), "this": d.get(field_),
                                                    "now_correct": d.get(f"{field_}_ok")}
    out["operations"] = ops
    return out


def _found_correct(d: Dict[str, Any]) -> List[str]:
    # truth = tp + missed; tp items are those not in "missed"
    return [x for x in d.get("_truth", []) if x not in set(d.get("missed", []))]


def evaluate(truth_path: Path, configs: List[str], llm: str = "none", llm_model: Optional[str] = None,
             llm_cache: Optional[Path] = None, runs: int = 1) -> Dict[str, Any]:
    from .analyze import AnalysisOptions, analyze_project
    from .llm.client import LLMError, LLMPending, make_provider

    truth = json.loads(Path(truth_path).read_text())
    project = (ROOT / truth["project"]).resolve()
    types = declared_types(project)
    out: Dict[str, Any] = {"truth": str(truth_path), "project": truth["project"], "configs": {}}
    truth_sets = {
        "claims": sorted(map(str, {tuple(x) for x in truth["claims"]})),
        "dependencies": sorted(map(str, {tuple(x) for x in truth["dependencies"]})),
        "exits": sorted(map(str, {(p, s_) for p, sigs in truth["exits"].items() for s_ in sigs})),
        "identity_questions": sorted(map(str, {tuple(sorted(x)) for x in truth["identity_questions"]})),
    }
    jobs: List[Tuple[str, str, int]] = []
    for cfg in configs:
        if cfg == "rules":
            jobs.append((cfg, cfg, 0))
        else:
            for k in range(max(1, runs)):
                jobs.append((cfg if k == 0 else f"{cfg}@{k + 1}", cfg, k))
    for name, cfg, sample in jobs:
        try:
            if cfg == "rules":
                res = analyze_project(project).to_json_dict()
                sc = score_result(truth, res, types)
            elif cfg == "combined":
                if llm == "none":
                    raise LLMError("needs --llm")
                res = analyze_project(project, options=AnalysisOptions(llm=llm, llm_model=llm_model,
                                                                       llm_cache=llm_cache, llm_sample=sample))
                d = res.to_json_dict()
                sc = score_result(truth, d, types)
                sc["llm_call"] = (d.get("provenance") or {}).get("llm_calls", [{}])[0]
                # the same result with model-proposed relations that the checker could not support removed
                checked = dict(d, relations=[r for r in d["relations"] if not (
                    r["status"]["support"] == "llm" and r["status"]["verification"] in ("pending", "contradicted"))])
                cs = score_result(truth, checked, types)
                for dim in DIMENSIONS:
                    cs[dim]["_truth"] = truth_sets[dim]
                cs["llm_call"] = sc["llm_call"]
                sc["_checked"] = cs
            elif cfg == "llm-only":
                if llm == "none":
                    raise LLMError("needs --llm")
                sc = run_baseline(truth, make_provider(llm, llm_model, llm_cache), project, sample=sample)
            else:
                raise ValueError(f"unknown configuration {cfg}")
            for dim in DIMENSIONS:
                sc[dim]["_truth"] = truth_sets[dim]
            checked = sc.pop("_checked", None)
            out["configs"][name] = sc
            if checked is not None:
                out["configs"][name.replace("combined", "combined+checked", 1)] = checked
        except LLMPending as exc:
            out["configs"][name] = {"error": f"waiting for {len(exc.paths)} LLM answer(s)",
                                    "pending": [str(p) for p in exc.paths]}
        except Exception as exc:  # noqa: BLE001 - keep the other configurations
            out["configs"][name] = {"error": f"{type(exc).__name__}: {exc}"}
    rules = out["configs"].get("rules")
    if rules and "error" not in rules:
        out["contribution"] = {n: contribution(rules, sc) for n, sc in out["configs"].items()
                               if n != "rules" and "error" not in sc}
    out["summary"] = summarize(out["configs"])
    return out


def summarize(configs: Dict[str, Any]) -> Dict[str, Any]:
    """Mean / min / max per configuration over its repeated runs."""

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for name, sc in configs.items():
        if "error" not in sc:
            groups.setdefault(name.split("@")[0], []).append(sc)
    order = ["rules", "llm-only", "combined", "combined+checked"]
    groups = dict(sorted(groups.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99))
    out = {}
    for cfg, scs in groups.items():
        row = {"runs": len(scs)}
        for key, get in (("op_kind", lambda x: x["operation_kind"]["accuracy"]),
                         ("caller", lambda x: x["caller"]["accuracy"])):
            vals = [get(x) for x in scs]
            row[key] = _stats(vals)
        for dim in DIMENSIONS + ("dependencies_relaxed",):
            for m in ("precision", "recall"):
                vals = [x[dim][m] for x in scs if dim in x and x[dim][m] is not None]
                row[f"{dim}_{m}"] = _stats(vals)
        out[cfg] = row
    return out


def _stats(vals: List[float]) -> Dict[str, Any]:
    if not vals:
        return {"mean": None}
    return {"mean": round(sum(vals) / len(vals), 3), "min": min(vals), "max": max(vals), "n": len(vals)}


def format_table(res: Dict[str, Any]) -> str:
    rows = ["| config | op kind acc | caller acc | claims P/R | deps P/R | exits P/R | identity Q R |", "|---|---|---|---|---|---|---|"]

    def pr(d):
        if not d:
            return "-"
        return f"{d['precision'] if d['precision'] is not None else '-'} / {d['recall'] if d['recall'] is not None else '-'}"

    for name, sc in res["configs"].items():
        if "error" in sc:
            rows.append(f"| {name} | not run: {sc['error']} | | | | | |")
            continue
        rows.append(f"| {name} | {sc['operation_kind']['accuracy']} | {sc['caller']['accuracy']} | {pr(sc['claims'])} | "
                    f"{pr(sc['dependencies'])} | {pr(sc['exits'])} | {sc['identity_questions']['recall']} |")
    for cfg, row in (res.get("summary") or {}).items():
        if row["runs"] < 2:
            continue

        def m(k):
            v = row[k]["mean"]
            return "-" if v is None else f"{v}"

        rows.append(f"| {cfg} (mean of {row['runs']}) | {m('op_kind')} | {m('caller')} | "
                    f"{m('claims_precision')} / {m('claims_recall')} | {m('dependencies_precision')} / "
                    f"{m('dependencies_recall')} | {m('exits_precision')} / {m('exits_recall')} | "
                    f"{m('identity_questions_recall')} |")
    return "\n".join(rows)
