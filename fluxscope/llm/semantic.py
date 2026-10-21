"""LLM stage: propose semantic interpretations on top of the extracted facts.

The model sees fact tables (ids, flows, conditions, rule results) plus the
source of the analysed contracts, and returns structured proposals. This
module validates every proposal against the facts before it is merged:

* references (operation / node / fact / evidence ids, source line ranges)
  must exist — otherwise the proposal is rejected and recorded as such;
* the model never edits flows, positions or rule results; it can relabel an
  operation whose structural kind is ``other`` / ``call`` or a structural
  fallback (``mint`` / ``burn`` / single-asset ``transfer`` without a recognised
  counterpart, see ``WEAK_RULES``), add explanations,
  add candidate relations (``support = llm``, ``verification = pending`` until
  the checker runs) and add questions (``unresolved`` items);
* disagreement with a rule is recorded as a question, not applied.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..model import Check, Condition, OperationKind, Relation, Status, Unresolved
from .client import LLMError, make_provider

PROMPT_VERSION = "semantic-v1"
PROMPT_FILE = Path(__file__).parent / "prompts" / "semantic-v1.md"
KINDS = [k.value for k in OperationKind]
RELATION_KINDS = ["claim", "depends_on", "identity"]


def _strict(obj: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": list(obj.keys()), "properties": obj}


_SRC = _strict({"file": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}})
_REFS = {"type": "array", "items": {"type": "string"}}
SCHEMA = _strict({
    "operations": {"type": "array", "items": _strict({
        "op_id": {"type": "string"},
        "kind": {"type": "string", "enum": KINDS},
        "label": {"type": "string"},
        "description": {"type": "string"},
        "fact_refs": _REFS,
        "source_refs": {"type": "array", "items": _SRC},
    })},
    "relations": {"type": "array", "items": _strict({
        "kind": {"type": "string", "enum": RELATION_KINDS},
        "source": {"type": "string"},
        "target": {"type": "string"},
        "operation": {"type": "string", "description": "exit operation id for claims, empty otherwise"},
        "description": {"type": "string"},
        "fact_refs": _REFS,
        "source_refs": {"type": "array", "items": _SRC},
    })},
    "explanations": {"type": "array", "items": _strict({
        "subject": {"type": "string"},
        "text": {"type": "string"},
        "fact_refs": _REFS,
    })},
    "questions": {"type": "array", "items": _strict({
        "kind": {"type": "string", "enum": ["missing_exit", "identity", "ambiguous_semantics", "external_implementation", "other"]},
        "subjects": _REFS,
        "text": {"type": "string"},
        "fact_refs": _REFS,
    })},
})


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


def build_context(sem, cfg, ws, max_ops: int = 400) -> str:
    L = lambda i: (sem.entities.get(i) or sem.assets.get(i) or sem.positions.get(i))  # noqa: E731

    def lab(i):
        x = L(i) if i else None
        return f"{x.label} [{i}]" if x is not None else (i or "-")

    out: List[str] = []
    out.append(f"PROJECT: {cfg.name}")
    if cfg.description:
        out.append(cfg.description)
    out.append("\nINSTANCES AND BINDINGS:")
    for i in cfg.instances:
        out.append(f"- {i.id}: contract {i.contract} ({i.description or ''}) -> entity ent:{i.id}")
    for b in cfg.bindings:
        out.append(f"- binding {b.instance}.{b.variable} = {b.target or (b.external.label if b.external else '?')}")
    out.append("\nASSETS (identity is by expression, never by name):")
    for a in sem.assets.values():
        out.append(f"- {a.id}: {a.label}; kind={a.kind}; identity={a.identity.expression or a.identity.contract or a.identity.kind}")
    out.append("\nENTITIES:")
    for e in sem.entities.values():
        out.append(f"- {e.id}: {e.label}; kind={e.kind}; in_scope={e.in_scope}" + (f"; expr={e.expression}" if e.expression else ""))
    out.append("\nOPERATIONS (rule kind; nested operations run in the same transaction as their parent):")
    for n, o in enumerate(sem.operations.values()):
        if n >= max_ops:
            out.append(f"... {len(sem.operations) - max_ops} more operations omitted")
            break
        head = f"- {o.id}: {o.label}; rule_kind={o.kind}; initiator={lab(o.initiator)}; executor={lab(o.executor)}"
        if o.parent:
            head += f"; parent={o.parent}"
        out.append(head)
        for f in o.inputs + o.outputs:
            out.append(f"    flow {f.id}: {sem.assets[f.asset].label} from {lab(f.from_) if f.from_ else '(mint)'} "
                       f"to {lab(f.to) if f.to else '(burn)'}; amount {f.amount.expression}; {f.mechanism}"
                       + (f"; conditions {','.join(f.conditions)}" if f.conditions else ""))
        for c in o.conditions[:12]:
            out.append(f"    condition {c.id}: [{c.kind}] {c.text}")
        for u in o.unresolved:
            out.append(f"    unresolved {u}")
    out.append("\nPOSITIONS:")
    for p in sem.positions.values():
        out.append(f"- {p.id}: {sem.assets[p.asset].label} held by {lab(p.holder)}; kind={p.kind}")
    out.append("\nRULE RELATIONS:")
    for r in sem.relations.values():
        if r.kind in ("holds",):
            continue
        out.append(f"- {r.id}: {r.kind} {r.source} -> {r.target}; {r.status.verification}; {r.basis_detail or r.label or ''}")
    out.append("\nUNRESOLVED:")
    for u in sem.unresolved.values():
        out.append(f"- {u.id}: [{u.kind}] {u.text}")
    out.append("\nSOURCE CODE (analysed contracts, with line numbers):")
    dep_paths = tuple(d.path.rstrip("/") + "/" for d in cfg.dependencies)
    for path in sorted(sem.ev.files):
        if path.startswith(dep_paths):
            continue
        try:
            lines = ws.read(path).splitlines()
        except OSError:
            continue
        out.append(f"\n=== {path} ===")
        out.extend(f"{i + 1:4d}  {ln}" for i, ln in enumerate(lines))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


# Structural fallbacks that name what the code does to the executor's own token without knowing why
# (no recognised counterpart flow): the model may refine them. Kinds from adapters, standards and the
# ledger rules are never overwritten.
WEAK_RULES = {"kind.structural:other", "kind.structural:mint", "kind.structural:burn", "kind.structural:transfer"}


def _weak_kind(o) -> bool:
    st = o.status
    if st is None or st.support != "rule" or not st.rule:
        return False
    if st.rule in WEAK_RULES:
        return True
    return st.rule.startswith("kind.wrapper:") and st.rule.split(":", 1)[1] in ("other", "mint", "burn", "transfer")


class _Merger:
    def __init__(self, sem, rec, ws):
        self.sem = sem
        self.rec = rec
        self.ws = ws
        self.accepted = 0
        self.rejected: List[Dict[str, Any]] = []
        self.fact_ids = set(sem.operations) | set(sem.relations) | set(sem.ev.items) | set(sem.unresolved)
        for o in sem.operations.values():
            self.fact_ids.update(f.id for f in o.inputs + o.outputs)
            self.fact_ids.update(c.id for c in o.conditions)
        self.nodes = set(sem.entities) | set(sem.assets) | set(sem.positions) | set(sem.operations)
        self.meta = {"provider": rec.provider, "model": rec.model, "prompt_version": rec.prompt_version,
                     "call_id": rec.call_id, "request_sha256": rec.request_sha256}

    def reject(self, what: str, item: Dict[str, Any], reason: str) -> None:
        self.rejected.append({"what": what, "reason": reason, "item": item})

    def refs_ok(self, item: Dict[str, Any], what: str) -> Optional[List[str]]:
        refs = item.get("fact_refs") or []
        bad = [r for r in refs if r not in self.fact_ids]
        if not refs:
            self.reject(what, item, "no fact_refs")
            return None
        if bad:
            self.reject(what, item, f"unknown fact_refs: {bad[:5]}")
            return None
        ev = [r for r in refs if r in self.sem.ev.items]
        for s in item.get("source_refs") or []:
            e = self._source_ref(s)
            if e is None:
                self.reject(what, item, f"invalid source_ref {s}")
                return None
            ev.append(e)
        return ev

    def _source_ref(self, s: Dict[str, Any]) -> Optional[str]:
        from ..facts.resolver import Site

        path, a, b = s.get("file"), int(s.get("start_line", 0)), int(s.get("end_line", 0))
        try:
            lines = self.ws.read(path).splitlines()
        except (OSError, TypeError):
            return None
        if a < 1 or b < a or b > len(lines):
            return None
        snippet = "\n".join(lines[a - 1:b])
        return self.sem.ev.site(Site(file=path, start_line=a, end_line=b, contract="", function="", snippet=snippet),
                                title=f"Source cited by the model: {path}:{a}-{b}")

    def llm_ev(self, title: str, text: str) -> str:
        return self.sem.ev.llm(self.rec.call_id, title, text, self.meta)

    def status(self, verification: str = "pending", checks=None, note=None) -> Status:
        return Status(basis="static", support="llm", verification=verification, rule=f"llm:{PROMPT_VERSION}",
                      checks=checks or [], note=note)

    # ------------------------------------------------------------------
    def operation(self, p: Dict[str, Any]) -> None:
        sem = self.sem
        op = sem.operations.get(p.get("op_id"))
        if op is None:
            return self.reject("operation", p, "unknown op_id")
        ev = self.refs_ok(p, "operation")
        if ev is None:
            return
        kind = p.get("kind")
        if kind not in KINDS:
            return self.reject("operation", p, f"invalid kind {kind}")
        same = [o for o in sem.operations.values() if o.entrypoint and op.entrypoint
                and o.entrypoint.contract == op.entrypoint.contract and o.entrypoint.signature == op.entrypoint.signature]
        if not same:
            same = [op]
        lev = self.llm_ev(f"Model interpretation of {op.label}", f"{kind}: {p.get('description', '')}")
        for o in same:
            text = (p.get("description") or "").strip()
            if text:
                o.description = f"{text} (Model explanation, {self.rec.model}.)"
            if (o.kind in ("other", "call") or _weak_kind(o)) and kind != o.kind:
                rule_kind = o.kind
                o.kind = kind
                o.status = Status(basis=o.status.basis, support="llm", verification="pending", rule=f"llm:{PROMPT_VERSION}",
                                  checks=o.status.checks,
                                  note=f"Kind '{kind}' proposed by the model; structural rule gave '{rule_kind}'.")
            elif kind != o.kind and o is op:
                uid = f"unr:llm:kind:{op.id}"
                sem.unresolved[uid] = Unresolved(
                    id=uid, kind="llm_question", subjects=[op.id], evidence=[lev] + ev,
                    text=f"The model classifies {op.label} as '{kind}' while the structural rule says '{o.kind}'. Kept the rule result.")
            for x in [lev] + ev:
                if x not in o.evidence:
                    o.evidence.append(x)
        self.accepted += 1

    def relation(self, p: Dict[str, Any]) -> None:
        sem = self.sem
        kind, src, tgt = p.get("kind"), p.get("source"), p.get("target")
        if kind not in RELATION_KINDS:
            return self.reject("relation", p, f"invalid kind {kind}")
        if src not in self.nodes or tgt not in self.nodes or src == tgt:
            return self.reject("relation", p, "unknown or identical source/target")
        if kind == "claim" and (src not in sem.positions or tgt not in sem.positions):
            return self.reject("relation", p, "claims must connect two positions")
        if kind == "identity" and (src not in sem.assets or tgt not in sem.assets):
            return self.reject("relation", p, "identity relations must connect two assets")
        ev = self.refs_ok(p, "relation")
        if ev is None:
            return
        lev = self.llm_ev(f"Model proposal: {kind} {src} -> {tgt}", p.get("description", ""))
        existing = [r for r in sem.relations.values() if r.kind == kind and {r.source, r.target} == {src, tgt}]
        if existing:
            r = existing[0]
            r.status.checks.append(Check(id="model-agrees", result="pass",
                                         description=f"{self.rec.model} independently proposed this relation", evidence=[lev]))
            if lev not in r.evidence:
                r.evidence.append(lev)
            self.accepted += 1
            return
        op = p.get("operation") or None
        if op and op not in sem.operations:
            return self.reject("relation", p, "unknown operation")
        rid = f"rel:llm:{kind}:{src}->{tgt}"
        r = Relation(id=rid, kind=kind, source=src, target=tgt, label=f"{kind.replace('_', ' ')} (model)",
                     description=p.get("description"), operation=op,
                     asset=sem.positions[tgt].asset if kind == "claim" else None,
                     status=self.status(), evidence=[lev] + ev)
        sem.relations[rid] = r
        if kind == "depends_on":
            self._check_link(r)
        self.accepted += 1

    def _check_link(self, r: Relation) -> None:
        """A dependency needs at least one program fact between the two sides."""

        sem = self.sem

        def ent(n):
            if n in sem.positions:
                return {sem.positions[n].holder}
            if n in sem.assets:
                return {sem.assets[n].issuer} if sem.assets[n].issuer else set()
            return {n}

        a, b = ent(r.source), ent(r.target)
        link = [k for k in sem.calls if k[0] in a and k[1] in b] or [
            f for fr in sem.flows for f in [fr.flow] if (f.from_ in a and f.to in b) or (f.from_ in b and f.to in a)]
        r.status.checks.append(Check(id="fact-link", result="pass" if link else "unknown",
                                     description="A call or asset flow connects the two sides" if link else
                                     "No call or flow between the two sides was found in the facts"))
        r.status.verification = "partially_supported" if link else "pending"

    def explanation(self, p: Dict[str, Any]) -> None:
        sem = self.sem
        subj = p.get("subject")
        obj = sem.entities.get(subj) or sem.assets.get(subj) or sem.positions.get(subj)
        if obj is None:
            return self.reject("explanation", p, "unknown subject")
        ev = self.refs_ok(p, "explanation")
        if ev is None:
            return
        lev = self.llm_ev(f"Model explanation of {obj.label}", p.get("text", ""))
        note = f"Model explanation ({self.rec.model}): {p.get('text', '').strip()}"
        obj.description = f"{obj.description} {note}".strip() if getattr(obj, "description", None) else note
        for x in [lev] + ev:
            if x not in obj.evidence:
                obj.evidence.append(x)
        self.accepted += 1

    def question(self, p: Dict[str, Any], n: int) -> None:
        sem = self.sem
        subjects = [s for s in p.get("subjects") or [] if s in self.nodes or s in sem.relations]
        if not subjects:
            return self.reject("question", p, "no known subjects")
        ev = self.refs_ok(p, "question")
        if ev is None:
            return
        lev = self.llm_ev(f"Model question {n}", p.get("text", ""))
        uid = f"unr:llm:q{n}"
        sem.unresolved[uid] = Unresolved(id=uid, kind="llm_question", text=p.get("text", "").strip(), subjects=subjects,
                                         evidence=[lev] + ev,
                                         suggestion=f"Raised by {self.rec.model}; confirm against the code and configuration.")
        self.accepted += 1


def merge(sem, data: Dict[str, Any], rec, ws) -> Dict[str, Any]:
    m = _Merger(sem, rec, ws)
    for p in data.get("operations", []):
        m.operation(p)
    for p in data.get("relations", []):
        m.relation(p)
    for p in data.get("explanations", []):
        m.explanation(p)
    for n, p in enumerate(data.get("questions", []), 1):
        m.question(p, n)
    total = sum(len(data.get(k, [])) for k in ("operations", "relations", "explanations", "questions"))
    return {"proposals": total, "accepted": m.accepted, "rejected": m.rejected}


def enrich(sem, cfg, ws, options) -> Dict[str, Any]:
    provider = make_provider(options.llm, options.llm_model, options.llm_cache)
    system = PROMPT_FILE.read_text()
    user = build_context(sem, cfg, ws)
    data, rec = provider.complete_json("semantic", PROMPT_VERSION, system, user, SCHEMA,
                                       sample=getattr(options, "llm_sample", 0))
    stats = merge(sem, data, rec, ws)
    call = rec.as_dict()
    call.update(proposals=stats["proposals"], accepted=stats["accepted"], rejected=stats["rejected"],
                context_chars=len(user))
    return {"provider": rec.provider, "model": rec.model, "prompt_version": PROMPT_VERSION, "calls": [call],
            "proposals": stats["proposals"], "accepted": stats["accepted"]}
