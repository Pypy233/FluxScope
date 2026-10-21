"""Analysis pipeline: ``analyze_project(project_path, config) -> AnalysisResult``.

Stages (each recorded in ``result.provenance.stages``):

1. snapshot  – copy the project, normalise line endings, hash the files
2. compile   – crytic-compile + Slither
3. facts     – per entry point effect trees with call context (summaries)
4. semantics – operations, flows, positions, claims, exits, dependencies (rules/adapters)
5. llm       – optional semantic proposals (fluxscope.llm), never rewriting facts
6. check     – candidate relation checks (fluxscope.checker)
7. assemble  – AnalysisResult with evidence and embedded sources
"""

from __future__ import annotations

import datetime as _dt
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import __version__
from .evidence import EvidenceRegistry
from .facts import exprs as X
from .facts.resolver import Instance, Resolver
from .model import (
    AnalysisResult,
    Assumption,
    ChainInfo,
    Component,
    Generator,
    Meta,
    ProjectInfo,
    Provenance,
    Scope,
    Task,
)
from .project import ProjectConfig, compile_workspace, load_config, prepare_workspace, tool_versions
from .semantics import Semantics
from .summaries import Summarizer


@dataclass
class AnalysisOptions:
    llm: str = "none"  # none | anthropic | claude-cli | exchange | openai | replay
    llm_model: Optional[str] = None
    llm_cache: Optional[Path] = None
    llm_sample: int = 0  # > 0: an independent repeat of the same request
    embed_sources: bool = True
    workdir: Optional[Path] = None
    progress: Optional[Callable[[str, str], None]] = None  # (stage, message)


@dataclass
class _Stages:
    items: List[Dict] = field(default_factory=list)
    progress: Optional[Callable[[str, str], None]] = None

    def run(self, name: str, fn, *a, **kw):
        if self.progress:
            self.progress(name, "started")
        t = time.time()
        rec = {"stage": name, "status": "ok"}
        try:
            out = fn(*a, **kw)
            return out
        except Exception as exc:
            rec["status"] = "failed"
            rec["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            rec["seconds"] = round(time.time() - t, 3)
            self.items.append(rec)
            if self.progress:
                self.progress(name, rec["status"])

    def note(self, **kw):
        self.items[-1].update(kw)


def analyze_project(project_path: Path, config: Optional[ProjectConfig] = None,
                    options: Optional[AnalysisOptions] = None) -> AnalysisResult:
    options = options or AnalysisOptions()
    project_path = Path(project_path)
    cfg = config or load_config(project_path)
    stages = _Stages(progress=options.progress)

    ws = stages.run("snapshot", prepare_workspace, project_path, options.workdir)
    stages.note(files=len(ws.files), normalized_line_endings=ws.normalized, sha256=ws.snapshot_sha256)
    try:
        sl = stages.run("compile", compile_workspace, ws, cfg)
        versions = tool_versions(sl)
        stages.note(contracts=len(sl.contracts), versions=versions)

        instances: Dict[str, Instance] = {}
        for i in cfg.instances:
            matches = [c for c in sl.contracts if c.name == i.contract and not c.is_interface]
            if not matches:
                raise ValueError(f"instance {i.id}: contract {i.contract} not found in the compiled project")
            instances[i.id] = Instance(i.id, matches[0], i.label or i.contract)
        bindings = {}
        for b in cfg.bindings:
            if b.target:
                if b.target not in instances:
                    raise ValueError(f"binding {b.instance}.{b.variable}: unknown instance {b.target}")
                bindings[(b.instance, b.variable)] = X.Inst(b.target, instances[b.target].label)
        resolver = Resolver(str(ws.root), instances, bindings)
        registries = {}
        for reg in cfg.registries:
            if reg.instance not in instances or any(m not in instances for m in reg.members):
                raise ValueError(f"registry {reg.instance}.{reg.variable}: unknown instance")
            registries[(reg.instance, reg.variable)] = list(reg.members)
        summarizer = Summarizer(resolver, [d.model_dump() for d in cfg.dependencies], registries)
        evidence = EvidenceRegistry()
        sem = Semantics(cfg, instances, evidence, resolver, str(ws.root))

        initializers: List[str] = []

        def facts():
            trees = []
            for iid, inst in instances.items():
                for fn in inst.contract.functions:
                    if fn.visibility not in ("public", "external") or fn.view or fn.pure or fn.is_constructor:
                        continue
                    if not fn.is_implemented or f"{inst.contract.name}.{fn.name}" in cfg.exclude_entrypoints:
                        continue
                    tree = summarizer.summarize_entry(inst, fn)
                    if _is_initializer(fn) and not tree.has_asset_effects:
                        # one-time setup before the analysed deployment state exists; kept only if it moves
                        # assets (an initial mint or deposit)
                        initializers.append(f"{inst.contract.name}.{fn.full_name}")
                        continue
                    trees.append((inst, fn, tree))
            return trees

        trees = stages.run("facts", facts)
        stages.note(entry_points=len(trees), effects=sum(sum(1 for _ in t.effects()) for _, _, t in trees),
                    skipped_initializers=initializers)
        sem.learned(summarizer)

        def semantics():
            sem.prepare(trees)
            for inst, fn, tree in trees:
                sem.add_entry(inst, fn, tree)
            sem.add_state_entries()
            sem.build_ledgers()
            sem.reclassify()
            sem.finish_identities()
            sem.build_positions()
            sem.build_claims()
            sem.build_exits()
            sem.build_dependencies()
            sem.build_operational_dependencies()
            sem.build_bindings()
            sem.build_missing_exits()
            sem.finalize_entities()
            sem.ensure_external_group()
            sem.build_boundaries()

        stages.run("semantics", semantics)
        stages.note(operations=len(sem.operations), positions=len(sem.positions), relations=len(sem.relations))

        llm_calls: List[Dict] = []
        components = [
            Component(name="slither-analyzer", version=versions.get("slither-analyzer"), role="static-analysis"),
            Component(name="crytic-compile", version=versions.get("crytic-compile"), role="build"),
            Component(name="solc", version=versions.get("solc"), role="compiler"),
            Component(name="fluxscope.rules", version=__version__, role="adapter",
                      detail={"rules": ["oz.erc20.internal", "structural.token-accounting", "oz.safeerc20", "erc20.call",
                                        "erc4626.call", "claim.share-redemption", "claim.exclusive-withdrawal",
                                        "claim.erc4626", "identity.approve-before-pull", "kind.structural"]}),
        ]
        if options.llm and options.llm != "none":
            from .llm.semantic import enrich

            def llm_stage():
                return enrich(sem, cfg, ws, options)

            info = stages.run("llm", llm_stage)
            llm_calls = info.get("calls", [])
            stages.note(provider=info.get("provider"), model=info.get("model"), proposals=info.get("proposals"),
                        accepted=info.get("accepted"))
            components.append(Component(name=f"llm:{info.get('provider')}", version=info.get("model"), role="llm",
                                        detail={"prompt_version": info.get("prompt_version")}))

        from .checker import check_all

        stages.run("check", check_all, sem)

        def assemble():
            return _assemble(sem, cfg, ws, components, stages, llm_calls, options)

        result = stages.run("assemble", assemble)
        result.provenance.stages = stages.items
        return result
    finally:
        ws.cleanup()


def _is_initializer(fn) -> bool:
    """Deployment-time initializers (OpenZeppelin ``Initializable``): callable
    once, before the analysed deployment state exists."""

    for m in fn.modifiers:
        if m.name in ("initializer", "reinitializer") and getattr(m.contract_declarer, "name", "") == "Initializable":
            return True
    return False


def _prune(sem: Semantics) -> None:
    """Drop assets / entities that nothing refers to (created while probing)."""

    used = set()
    for o in sem.operations.values():
        used.update(x for x in (o.initiator, o.executor) if x)
        used.update(p.entity for p in o.participants)
        for f in o.inputs + o.outputs:
            used.update(x for x in (f.asset, f.from_, f.to) if x)
    for p in sem.positions.values():
        used.update(x for x in (p.holder, p.asset, p.counterparty) if x)
    for r in sem.relations.values():
        used.update(x for x in (r.source, r.target, r.asset) if x)
    for x in sem.exits.values():
        used.update(s.actor for s in x.steps if s.actor)
    for u in sem.unresolved.values():
        used.update(u.subjects)
    for a in list(sem.assets.values()):
        if a.id in used and a.issuer:
            used.add(a.issuer)
    changed = True
    while changed:
        changed = False
        for e in sem.entities.values():
            if e.id in used and e.parent and e.parent not in used:
                used.add(e.parent)
                changed = True
    for aid in [a for a in sem.assets if a not in used]:
        del sem.assets[aid]
    for eid in [e for e in sem.entities if e not in used and not e.startswith("ent:group:")]:
        del sem.entities[eid]
    for eid in [e for e in sem.entities if e.startswith("ent:group:") and e not in used]:
        del sem.entities[eid]


def _assemble(sem: Semantics, cfg: ProjectConfig, ws, components, stages, llm_calls, options) -> AnalysisResult:
    _prune(sem)
    now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()
    assumptions = []
    for b in cfg.bindings:
        tgt = b.target or (b.external.label if b.external else "?")
        assumptions.append(Assumption(id=f"asm:bind:{b.instance}.{b.variable}", source="config",
                                      text=f"{b.instance}.{b.variable} is bound to {tgt} ({b.source})" + (f": {b.note}" if b.note else ""),
                                      evidence=[f"ev:cfg:bind:{b.instance}.{b.variable}"] if f"ev:cfg:bind:{b.instance}.{b.variable}" in sem.ev.items else []))
    for k, ident in enumerate(cfg.identities):
        assumptions.append(Assumption(id=f"asm:identity:{k}", source="config",
                                      text=f"Same token: {' = '.join(ident.same)} ({ident.source})" + (f": {ident.note}" if ident.note else "")))
    notes = list(cfg.notes)
    if ws.normalized:
        notes.append("Line endings normalised to LF before compilation for: " + ", ".join(ws.normalized))
    if sem.non_asset_entries:
        notes.append("Entry points without asset effects (not shown as operations): " + ", ".join(sem.non_asset_entries))
    notes.append("Static analysis: relations describe behaviour the code supports (candidates), not observed executions. "
                 "Amounts are symbolic expressions.")
    tasks = _tasks(sem, cfg)
    result = AnalysisResult(
        meta=Meta(id=f"analysis:{_slug(cfg.name)}:{ws.snapshot_sha256[:10]}", title=cfg.name, description=cfg.description,
                  synthetic=False, created_at=now, generator=Generator(name="fluxscope", version=__version__),
                  tags=["static-analysis"] + (["llm"] if llm_calls else [])),
        scope=Scope(kind="static_analysis",
                    project=ProjectInfo(name=cfg.name, repository=cfg.repository, commit=cfg.commit, version=cfg.version,
                                        entry_contracts=[i.contract for i in cfg.instances],
                                        files=[f["path"] for f in ws.files], snapshot_sha256=ws.snapshot_sha256),
                    chain=ChainInfo(**cfg.chain.model_dump()) if cfg.chain else None,
                    build={"framework": cfg.build.framework, "target": cfg.build.target},
                    components=components, boundaries=list(sem.boundaries.values()), assumptions=assumptions, notes=notes),
        assets=list(sem.assets.values()),
        entities=list(sem.entities.values()),
        operations=list(sem.operations.values()),
        positions=list(sem.positions.values()),
        relations=list(sem.relations.values()),
        exits=list(sem.exits.values()),
        evidence=list(sem.ev.items.values()),
        sources=sem.ev.sources(ws.read) if options.embed_sources else [],
        unresolved=list(sem.unresolved.values()),
        tasks=tasks,
        provenance=Provenance(stages=[], llm_calls=llm_calls),
    )
    return result


def _slug(s: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()


def _tasks(sem: Semantics, cfg: ProjectConfig) -> List[Task]:
    tasks: List[Task] = []
    for t in cfg.tasks:
        tasks.append(Task(**t))
    for a in sem.assets.values():
        if a.kind != "share" or not a.issuer or a.issuer[4:] not in sem.instances:
            continue
        pos = [p for p in sem.positions.values() if p.asset == a.id]
        if not pos:
            continue
        claims = [r for r in sem.relations.values() if r.kind == "claim" and r.source in {p.id for p in pos}]
        tasks.append(Task(id=f"task:holders:{a.id}", question=f"Who holds {a.label}, and who holds the assets it claims?",
                          focus=[a.id], view="holdings",
                          description="Select the share and follow its claim relations to the custody positions."))
        tasks.append(Task(id=f"task:exit:{a.id}", question=f"Which operations are needed to exit {a.label}, and under which conditions?",
                          focus=[p.id for p in pos][:1], view="exits"))
        if claims:
            tasks.append(Task(id=f"task:deps:{a.id}", question=f"Which contracts and external protocols does {a.label} depend on?",
                              focus=[a.id], view="dependencies"))
    if sem.unresolved:
        tasks.append(Task(id="task:unresolved", question="Which parts of the system could not be resolved by the analysis?",
                          focus=[u.subjects[0] for u in sem.unresolved.values() if u.subjects][:3], view="all"))
    return tasks
