"""Recover asset operations, positions, claims, dependencies and exit paths.

Input: effect trees from :mod:`fluxscope.summaries` for every public entry
point of the configured instances. Output: model objects
(:mod:`fluxscope.model`) with evidence and status. Everything here is a
*structural* interpretation (rule support); :mod:`fluxscope.llm` may add
proposals afterwards and :mod:`fluxscope.checker` re-checks candidate claims.
"""

from __future__ import annotations

import hashlib
import re
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .evidence import EvidenceRegistry
from .facts import exprs as X
from .facts import namespaced as N
from .facts.effects import Effect, FrameNode, Guard
from .facts.resolver import Instance, Site
from .model import (
    Amount,
    Asset,
    AssetIdentity,
    Boundary,
    Check,
    Condition,
    ContractRef,
    Entity,
    EntryPoint,
    ExitPath,
    ExitStep,
    Flow,
    Operation,
    Participant,
    Position,
    Relation,
    Status,
    Unresolved,
)
from .project import ProjectConfig

SYSTEM_KINDS = ("contract",)


def status(support: str, verification: str = "supported", rule: Optional[str] = None,
           checks: Optional[List[Check]] = None, basis: str = "static", note: Optional[str] = None) -> Status:
    return Status(basis=basis, support=support, verification=verification, rule=rule, checks=checks or [], note=note)


def _h(s: str, n: int = 8) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:n]


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.()\[\]-]+", "_", s)


def parse_cfg_expr(text: str) -> str:
    """``vault`` -> inst:vault, ``strategy.want`` -> state key, ``strategy.erc4626.asset()`` -> getter key."""

    parts = text.split(".")
    if len(parts) == 1:
        return f"inst:{parts[0]}"
    base = f"state:{parts[0]}.{parts[1]}"
    for p in parts[2:]:
        name = p[:-2] if p.endswith("()") else p
        base = f"get:{base}.{name}()"
    return base


@dataclass
class EntryResult:
    instance: Instance
    function: object
    tree: FrameNode
    caller: str  # entity id of the root msg.sender
    op: Optional[Operation] = None


@dataclass
class FlowRec:
    """A flow together with the context it was recovered in."""

    flow: Flow
    op: Operation
    root: Operation
    effect: Effect
    guards: Tuple[Guard, ...]


class Semantics:
    def __init__(self, cfg: ProjectConfig, instances: Dict[str, Instance], evidence: EvidenceRegistry,
                 resolver, root: str):
        import os
        self.R = resolver
        self.root = os.path.realpath(root).rstrip("/") + "/"
        self._approvals: List[tuple] = []
        self._ext_ops: List[tuple] = []
        self._pending_identity: List[tuple] = []
        self._cond_index: Dict[str, str] = {}
        self.cfg = cfg
        self.instances = instances
        self.ev = evidence
        self.entities: "OrderedDict[str, Entity]" = OrderedDict()
        self.assets: "OrderedDict[str, Asset]" = OrderedDict()
        self.operations: "OrderedDict[str, Operation]" = OrderedDict()
        self.positions: "OrderedDict[str, Position]" = OrderedDict()
        self.relations: "OrderedDict[str, Relation]" = OrderedDict()
        self.exits: "OrderedDict[str, ExitPath]" = OrderedDict()
        self.unresolved: "OrderedDict[str, Unresolved]" = OrderedDict()
        self.boundaries: "OrderedDict[str, Boundary]" = OrderedDict()
        self.flows: List[FlowRec] = []
        self.op_tree: Dict[str, FrameNode] = {}
        self.op_children: Dict[str, List[str]] = defaultdict(list)
        self.op_root: Dict[str, str] = {}
        self.writes: List[Tuple[Effect, Operation]] = []
        self.reads: List[Tuple[Effect, Operation]] = []
        self.calls: Dict[Tuple[str, str], Dict[str, object]] = OrderedDict()
        self.entries: List[EntryResult] = []
        self.non_asset_entries: List[str] = []
        self._alias: Dict[str, str] = {}
        self._adapter_share_keys: Set[str] = set()
        self._call_targets: Set[str] = set()
        self.registries: Dict[Tuple[str, str], List[str]] = {(r.instance, r.variable): list(r.members)
                                                             for r in cfg.registries}
        self.member_alias: Dict[str, str] = {}
        self._member_evidence: Dict[str, object] = {}
        self._member_of: Dict[str, str] = {}
        self._holder_override: Dict[str, str] = {}
        self.ledger_recs: List[tuple] = []
        self._deferred: List[tuple] = []
        self.ledgers: Dict[str, Dict[str, object]] = {}
        self._debtors: Set[Tuple[str, str]] = set()  # (debtor entity, creditor entity) from debt ledgers
        # (paying instance, asset) -> recipients of the instance's own payouts of that asset (see reclassify)
        self._recipients: Dict[Tuple[str, str], Set[str]] = {}
        self._identity_setup()
        self._asset_cfg = {self._alias.get(parse_cfg_expr(a.expression), parse_cfg_expr(a.expression)): a
                           for a in cfg.assets}
        self._base_entities()

    # ================================================================ setup
    def _identity_setup(self) -> None:
        for ident in self.cfg.identities:
            keys = [parse_cfg_expr(x) for x in ident.same]
            rep = keys[0]
            for k in keys[1:]:
                self._alias[k] = rep

    def akey(self, e: X.Expr) -> str:
        k = X.key(e)
        return self._alias.get(k, k)

    def learned(self, summarizer) -> None:
        """Facts the summarizer derived from required conditions: addresses that
        must be registered members (registry configured with one member) and
        parameters that must equal another expression (``token == asset()``)."""

        self.member_alias.update(summarizer.member_alias)
        self._member_evidence.update(summarizer.member_evidence)
        for k, target in summarizer.eq_alias.items():
            tk = X.key(target)
            self._alias.setdefault(k, self._alias.get(tk, tk))
        self._eq_evidence = dict(summarizer.eq_evidence)

    def _base_entities(self) -> None:
        for g in self.cfg.groups:
            self.entities[f"ent:group:{g.id}"] = Entity(
                id=f"ent:group:{g.id}", label=g.label, kind="protocol", description=g.description,
                status=status("config"), evidence=[self.ev.config(f"group:{g.id}", f"Group {g.label}",
                                                                  f"Configured group of instances {', '.join(g.members)}")])
        for i in self.cfg.instances:
            inst = self.instances[i.id]
            site = Site(file=self._contract_file(inst), start_line=inst.contract.source_mapping.lines[0] if inst.contract.source_mapping.lines else 0,
                        end_line=inst.contract.source_mapping.lines[-1] if inst.contract.source_mapping.lines else 0,
                        contract=inst.contract.name, function="", snippet="")
            ev = self.ev.site(site, title=f"Contract {inst.contract.name}")
            self.entities[f"ent:{i.id}"] = Entity(
                id=f"ent:{i.id}", label=i.label or inst.contract.name, kind="contract", description=i.description,
                parent=f"ent:group:{i.group}" if i.group else None,
                contract=ContractRef(name=inst.contract.name, file=self._contract_file(inst), address=i.address,
                                     chain_id=self.cfg.chain.chain_id if self.cfg.chain else None),
                expression=f"address({inst.contract.name})", in_scope=True,
                status=status("config"), evidence=[e for e in [ev] if e])
        self.entities["ent:any"] = Entity(
            id="ent:any", label="Any account", kind="account",
            description="Any externally owned account or contract that calls an entry point without access restriction.",
            status=status("rule", rule="caller.unrestricted"))

    def _contract_file(self, inst: Instance) -> str:
        import os
        path = os.path.realpath(inst.contract.source_mapping.filename.absolute)
        root = self.root if hasattr(self, "root") else ""
        return path[len(root):] if root and path.startswith(root) else path

    # ============================================================ entities
    def entity_for(self, e: Optional[X.Expr], caller: str, site: Optional[Site] = None) -> Optional[str]:
        if e is None:
            return None
        c = X.canonical(e)
        if isinstance(c, X.Ctx) and c.name == "msg.sender":
            return caller
        if isinstance(c, X.Inst):
            return f"ent:{c.instance}"
        if isinstance(c, X.Phi):
            # the contract itself or one of its own custody addresses (``asset == usdc ? pocket : address(this)``):
            # the value stays with the contract
            selves = {o.instance for o in c.options if isinstance(o, X.Inst)}
            if len(selves) == 1 and all(isinstance(o, X.Inst) or isinstance(o, X.State) and o.instance in selves
                                        and str(o.type).startswith("address") for o in c.options):
                return f"ent:{selves.pop()}"
        k = self.akey(c)
        if k in self.member_alias:
            return f"ent:{self.member_alias[k]}"
        if isinstance(c, X.Members) and isinstance(c.registry, X.State):
            return self._members_entity(c.registry)
        eid = None
        if isinstance(c, X.Ctx) and c.name == "tx.origin":
            eid = "ent:tx.origin"
            if eid not in self.entities:
                self.entities[eid] = Entity(id=eid, label="tx.origin (transaction sender)", kind="role",
                                            expression="tx.origin",
                                            description="The externally owned account that signed the transaction.",
                                            status=status("rule", rule="entity.context"))
            return eid
        if isinstance(c, X.Const):
            eid = f"ent:const:{_slug(c.value)}"
            if eid not in self.entities:
                self.entities[eid] = Entity(id=eid, label=c.render(), kind="unknown", expression=c.render(),
                                            status=status("rule", rule="entity.constant"))
            return eid
        if isinstance(c, X.State) and k != X.key(c) and k.startswith("state:"):
            iid, name = k[6:].split(".", 1)
            if iid in self.instances:
                sv = self.instances[iid].contract.get_state_variable_from_name(name)
                c = X.State(iid, self.instances[iid].contract.name, name, str(sv.type) if sv is not None else "")
        if isinstance(c, X.Index) and isinstance(c.base, X.State) and (c.base.instance, c.base.name) in self.registries:
            return self._members_entity(c.base)
        if isinstance(c, X.State):
            eid = f"ent:{c.instance}.{c.name}"
            if eid not in self.entities:
                ext = self._binding_external(c.instance, c.name)
                inst = self.instances[c.instance]
                if ext is not None:
                    self.entities[eid] = Entity(
                        id=eid, label=ext.label, kind=ext.kind, description=ext.description,
                        expression=c.qualified(), in_scope=False, parent="ent:group:external",
                        contract=ContractRef(name=c.type or c.name, address=ext.address),
                        status=status("config"), evidence=[self.ev.config(f"bind:{c.instance}.{c.name}",
                                                                          f"Binding {c.qualified()}", f"{c.qualified()} -> {ext.label}")])
                else:
                    is_contract_type = bool(c.type) and c.type not in ("address", "address payable")
                    kind = "external_contract" if (is_contract_type or k in self._call_targets) else "role"
                    tdesc = f" of type {c.type}" if is_contract_type else ""
                    self.entities[eid] = Entity(
                        id=eid,
                        label=(f"{c.name} ({c.type})" if is_contract_type else
                               f"{c.name.lstrip('_')} of {inst.contract.name}" if kind == "role" else c.name),
                        kind=kind,
                        description=(f"Address stored in {inst.contract.name}.{c.name}{tdesc}. "
                                     "Not bound to an analysed instance; its code is outside the analysis scope."
                                     if kind == "external_contract" else
                                     f"Address stored in {inst.contract.name}.{c.name}; not bound by the configuration."),
                        expression=c.qualified(), in_scope=False,
                        parent="ent:group:external" if kind == "external_contract" else None,
                        contract=ContractRef(name=c.type) if is_contract_type else None,
                        status=status("rule", rule="entity.state-address"),
                        evidence=self._state_var_evidence(inst, c.name))
            return eid
        if isinstance(c, X.Param):
            eid = f"ent:param:{c.instance}.{_slug(c.function)}.{c.name}"
            if eid not in self.entities:
                inst = self.instances[c.instance]
                self.entities[eid] = Entity(id=eid, label=f"`{c.name}` argument of {c.function.split('(')[0]}()",
                                            kind="role", expression=c.name,
                                            description=f"Address chosen by the caller of {inst.contract.name}.{c.function}.",
                                            status=status("rule", rule="entity.parameter"))
            return eid
        if isinstance(c, X.Getter):
            eid = f"ent:get:{_h(k)}"
            if eid not in self.entities:
                self.entities[eid] = Entity(id=eid, label=c.render(), kind="role", expression=c.render(), in_scope=False,
                                            description="Address returned by a call to code outside the analysed instances.",
                                            status=status("rule", rule="entity.external-getter"))
            return eid
        eid = f"ent:expr:{_h(k)}"
        if eid not in self.entities:
            self.entities[eid] = Entity(id=eid, label=c.render()[:60], kind="unknown", expression=c.render(),
                                        description="Address expression that could not be resolved to a known party.",
                                        status=status("rule", "unresolved", rule="entity.unresolved"))
        return eid

    def _members_entity(self, reg: X.State) -> str:
        eid = f"ent:members:{reg.instance}.{reg.name}"
        if eid not in self.entities:
            inst = self.instances[reg.instance]
            members = self.registries.get((reg.instance, reg.name), [])
            names = ", ".join(self.instances[m].label for m in members)
            desc = (f"Any address registered in {inst.contract.name}.{reg.name}. "
                    + (f"Configured members: {names} (other members may exist at run time)."
                       if members else "Membership is managed at run time and not configured for the analysis."))
            ev = self._state_var_evidence(inst, reg.name)
            if members:
                ev.append(self.ev.config(f"registry:{reg.instance}.{reg.name}", f"Registry {inst.contract.name}.{reg.name}",
                                         f"Configured members of {inst.contract.name}.{reg.name}: {names}"))
            self.entities[eid] = Entity(id=eid, label=f"member of {inst.contract.name}.{reg.name}", kind="role",
                                        expression=f"{inst.contract.name}.{reg.name}", description=desc,
                                        status=status("rule", rule="caller.registry"), evidence=ev)
        return eid

    def _bound_external(self, eid: str) -> bool:
        """An external contract the configuration binds to a state variable."""

        return any(b.external is not None and eid == f"ent:{b.instance}.{b.variable}" for b in self.cfg.bindings)

    def _binding_external(self, inst: str, var: str):
        for b in self.cfg.bindings:
            if b.instance == inst and b.variable == var and b.external is not None:
                return b.external
        return None

    def _state_var_evidence(self, inst: Instance, name: str) -> List[str]:
        sv = inst.contract.get_state_variable_from_name(name)
        if sv is None:
            return []
        site = self.site_of(sv.source_mapping, sv.contract, None)
        e = self.ev.site(site, title=f"State variable {sv.contract.name}.{name}")
        return [e] if e else []

    def finalize_entities(self) -> None:
        """Addresses that are used as call targets are contracts, not plain roles."""

        for e in self.entities.values():
            if e.kind != "role" or not e.expression or not e.id.startswith("ent:") or e.id.count(".") != 1:
                continue
            iid, var = e.id[4:].split(".", 1)
            if iid in self.instances and f"state:{iid}.{var}" in self._call_targets:
                e.kind = "external_contract"
                e.label = var
                e.parent = "ent:group:external"
                e.description = (f"Address stored in {self.instances[iid].contract.name}.{var}; called by the analysed code "
                                 "but its implementation is outside the analysis scope.")

    def ensure_external_group(self) -> None:
        if any(e.parent == "ent:group:external" for e in self.entities.values()) and "ent:group:external" not in self.entities:
            self.entities["ent:group:external"] = Entity(
                id="ent:group:external", label="Outside analysis scope", kind="protocol", in_scope=False,
                description="Contracts whose implementation is not part of the analysed snapshot (interfaces only).",
                status=status("rule", rule="scope.boundary"))

    # ============================================================== assets
    def asset_for(self, token: X.Expr) -> str:
        c = X.canonical(token)
        k = self.akey(c)
        if isinstance(c, X.Const) and c.type == "native":
            aid = "asset:native"
            if aid not in self.assets:
                self.assets[aid] = Asset(id=aid, label="Native currency", kind="native",
                                         identity=AssetIdentity(kind="native"), status=status("rule", rule="asset.native"))
            return aid
        if isinstance(c, X.Index):
            # any element of a configurable token list: identified by the list, not the loop index
            k = f"idx:{self.akey(c.base)}[]"
        aid = "asset:" + _slug(re.sub(r"\b(state|get|inst|param|idx|mem):", "", k))
        if k.startswith("phi:") or k.startswith("opaque:") or k.startswith("bin:") or len(aid) > 80:
            aid = f"asset:expr:{_h(k)}"
        if aid in self.assets:
            return aid
        cfgl = self._asset_cfg.get(k)
        label, kind, standard, issuer, ident, desc = c.render(), "token", None, None, None, None
        if isinstance(c, X.Inst):
            inst = self.instances[c.instance]
            from .adapters import _sigs
            erc20 = {"transfer(address,uint256)", "balanceOf(address)"} <= _sigs(inst.contract)
            kind = "share" if erc20 else "accounting"
            standard = "ERC20" if erc20 else None
            issuer = f"ent:{c.instance}"
            label = f"{inst.label} share"
            ident = AssetIdentity(kind="contract_self", contract=inst.contract.name,
                                  address=self._inst_cfg(c.instance).address,
                                  note="Token implemented by the analysed contract itself")
            desc = f"Token minted and burned by {inst.contract.name}."
        elif isinstance(c, X.State) and c.type.startswith("mapping("):
            inst = self.instances[c.instance]
            kind = "accounting"
            issuer = f"ent:{c.instance}"
            label = f"{c.name.lstrip('_')} (ledger of {inst.label})"
            ident = AssetIdentity(kind="scoped_expression", expression=f"{c.qualified()} (ledger)",
                                  note="Per-account amounts recorded in an address-keyed mapping (non-transferable)")
            desc = f"Amounts recorded per account in {c.qualified()}."
        elif isinstance(c, X.State):
            inst = self.instances[c.instance]
            label = c.name
            ident = AssetIdentity(kind="scoped_expression", expression=c.qualified(),
                                  note="Identified by the state variable that stores the token address")
            if k in self._adapter_share_keys:
                kind, standard = "share", "ERC4626"
                issuer = self.entity_for(c, "ent:any")
                desc = f"Shares of the ERC-4626 vault stored in {c.qualified()}."
            else:
                desc = f"Token whose address is stored in {c.qualified()}."
        elif isinstance(c, X.Getter) and c.name == "asset" and not c.args:
            label = f"{X._r(c.base)}.asset()"
            ident = AssetIdentity(kind="scoped_expression", expression=self._qual(c),
                                  note="Underlying asset reported by the ERC-4626 vault; not resolved statically")
            desc = "Underlying asset of an external ERC-4626 vault (EIP-4626 asset())."
        elif isinstance(c, X.Param):
            label = f"token passed as `{c.name}`"
            kind = "unknown"
            ident = AssetIdentity(kind="scoped_expression", expression=f"{c.function}:{c.name}",
                                  note="Any token chosen by the caller")
        elif isinstance(c, X.Index):
            label = f"element of {c.base.render()}[]"
            ident = AssetIdentity(kind="scoped_expression", expression=f"{self._qual(c.base)}[]",
                                  note="Any token taken from a configurable list")
        else:
            ident = AssetIdentity(kind="scoped_expression", expression=self._qual(c))
        if cfgl is not None:
            label = cfgl.label
            desc = cfgl.description or desc
            if cfgl.address:
                ident.address = cfgl.address
                ident.chain_id = self.cfg.chain.chain_id if self.cfg.chain else None
        self.assets[aid] = Asset(
            id=aid, label=label, symbol=cfgl.symbol if cfgl else None, kind=kind, standard=standard,
            identity=ident, issuer=issuer, decimals=cfgl.decimals if cfgl else None, description=desc,
            status=status("rule", rule="asset.identity"),
            evidence=([self.ev.config(f"asset:{cfgl.expression}", f"Asset label for {cfgl.expression}",
                                      f"Display label '{cfgl.label}' from the project configuration (display only).")]
                      if cfgl else []))
        return aid

    def _qual(self, c: X.Expr) -> str:
        if isinstance(c, X.State):
            return c.qualified()
        if isinstance(c, X.Getter):
            return f"{self._qual(c.base)}.{c.name}({', '.join(self._qual(a) for a in c.args)})"
        if isinstance(c, X.Index):
            return f"{self._qual(c.base)}[{c.index.render()}]"
        if isinstance(c, X.Inst):
            return self.instances[c.instance].contract.name
        return c.render()

    def _inst_cfg(self, iid: str):
        return next(i for i in self.cfg.instances if i.id == iid)

    # ================================================================= sites
    def site_of(self, sm, contract, fn) -> Site:
        import os
        path = os.path.realpath(sm.filename.absolute)
        if path.startswith(self.root):
            path = path[len(self.root):]
        lines = sm.lines or [0]
        return Site(file=path, start_line=min(lines), end_line=max(lines), contract=getattr(contract, "name", "") or "",
                    function=getattr(fn, "full_name", "") or "", snippet=(sm.content or "").strip())

    # ======================================================= access control
    def caller_entity(self, tree: FrameNode) -> Tuple[str, List[Guard]]:
        """Who may call an entry point, from require / revert / modifier guards."""

        allowed: Optional[List[X.Expr]] = None
        used: List[Guard] = []
        for g in self.entry_conditions(tree):
            got = _allowed_callers(g)
            if got is not None:
                # several checks: keep their intersection, else the first (the explicit modifier / require)
                allowed = got if allowed is None else [a for a in allowed if a in got] or allowed
                used.append(g)
        if not allowed:
            return "ent:any", []
        if len(allowed) == 1:
            return self.entity_for(allowed[0], "ent:any"), used
        labels = [self.entities[self.entity_for(a, "ent:any")].label for a in allowed]
        eid = f"ent:role:{_h('|'.join(sorted(X.key(a) for a in allowed)))}"
        if eid not in self.entities:
            self.entities[eid] = Entity(id=eid, label=" or ".join(labels), kind="role",
                                        expression=" | ".join(a.render() for a in allowed),
                                        description="Caller restricted by an access-control check to one of several addresses.",
                                        status=status("rule", rule="caller.access-control"),
                                        evidence=[e for e in (self.ev.site(g.site) for g in used) if e])
        return eid, used

    # ========================================================== operations
    def prepare(self, trees) -> None:
        """Register tokens that adapters identify as shares before assets are created."""

        for _, _, tree in trees:
            for n in tree.walk():
                if n.kind == "adapter" and n.executor is not None:
                    self._adapter_share_keys.add(self.akey(n.executor))

    def entry_conditions(self, tree: FrameNode) -> List[Guard]:
        """Requires / reverts that constrain the whole entry point, including
        unconditional internal helpers (``_checkOwner()``, ``_checkManager()``)."""

        out = list(tree.conditions)
        for it in tree.items:
            if isinstance(it, FrameNode) and it.kind in ("internal", "library") and not it.guards:
                out.extend(g for g in self.entry_conditions(it) if g.kind in ("require", "revert", "modifier"))
        return out

    def add_entry(self, inst: Instance, fn, tree: FrameNode, state_reason: Optional[str] = None) -> Optional[Operation]:
        caller, ac_guards = self.caller_entity(tree)
        if state_reason is None:
            er = EntryResult(inst, fn, tree, caller)
            self.entries.append(er)
        else:
            er = next(e for e in self.entries if e.instance is inst and e.function is fn)
        written = {v.name for v in N.written(fn)}
        if state_reason is None and not self._relevant(tree, written, inst.id):
            self.non_asset_entries.append(f"{inst.contract.name}.{fn.full_name}")
            self._deferred.append((inst, fn, tree))
            return None
        oid = f"op:{inst.id}.{fn.full_name}"
        op = self._build(tree, oid, None, None, caller, caller, root_id=oid, entry=(inst, fn))
        if state_reason is not None:
            self.non_asset_entries.remove(f"{inst.contract.name}.{fn.full_name}")
            op.description = op.description or state_reason
        er.op = op
        for reg in self.cfg.registries:
            if reg.instance == inst.id and reg.variable in written:
                uid = f"unr:registry:{inst.id}.{fn.full_name}.{reg.variable}"
                names = ", ".join(self.instances[m].label for m in reg.members)
                self.unresolved[uid] = Unresolved(
                    id=uid, kind="unbound_address",
                    text=(f"{inst.contract.name}.{fn.full_name} changes the registry {reg.variable}; the analysis follows calls "
                          f"to its configured member(s) ({names}) only, other registered addresses are not analysed."),
                    subjects=[oid], evidence=[x for x in [self.ev.site(tree.site)] if x],
                    suggestion="Add the other registered contracts as instances or confirm the registry contents on chain.")
                op.unresolved.append(uid)
        for b in self.cfg.bindings:
            if b.instance == inst.id and b.variable in written:
                uid = f"unr:rebind:{inst.id}.{fn.full_name}.{b.variable}"
                self.unresolved[uid] = Unresolved(
                    id=uid, kind="unbound_address",
                    text=(f"{inst.contract.name}.{fn.full_name} reassigns {b.variable}; calls made through {b.variable} after "
                          "the assignment are analysed with the configured binding, but at run time they reach the new address."),
                    subjects=[oid], evidence=[x for x in [self.ev.site(tree.site)] if x],
                    suggestion="Analyse the replacement contract separately or bind the candidate address.")
                op.unresolved.append(uid)
        if ac_guards:
            op.participants.insert(0, Participant(role="authorized_caller", entity=caller,
                                                  expression=self.entities[caller].expression,
                                                  evidence=[e for e in (self.ev.site(g.site) for g in ac_guards) if e]))
        return op

    def add_state_entries(self) -> None:
        """Entry points that move no assets but change values the asset-moving entry points of the same
        contract read (a merkle root of claimable amounts, a reward cycle, an exchange rate): they change
        what accounts can obtain, so they are operations too. Addresses, flags and role mappings (access
        control, pausing) are not counted."""

        reads: Dict[str, Set[str]] = defaultdict(set)
        for er in self.entries:
            if er.op is not None and self.all_flows(er.op):
                reads[er.instance.id].update(v.name for v in N.read(er.function))
        for inst, fn, tree in self._deferred:
            vals = [v for v in N.written(fn)
                    if v.name in reads.get(inst.id, ()) and _is_value_state(v)]
            if vals:
                names = ", ".join(sorted({v.name for v in vals}))
                self.add_entry(inst, fn, tree, state_reason=(
                    f"Changes {names}, which the contract's asset-moving entry points read: it changes what accounts can obtain "
                    "(no asset moves in this call)."))

    def _relevant(self, tree: FrameNode, written: Set[str] = frozenset(), inst_id: str = "") -> bool:
        for e in tree.effects():
            if e.moves_asset or e.kind in ("ext_call", "ledger"):
                return True
        if any(n.kind == "adapter" for n in tree.walk()):
            return True
        # changes who the configured bindings / registries point to
        cfg_vars = {b.variable for b in self.cfg.bindings if b.instance == inst_id} | \
                   {r.variable for r in self.cfg.registries if r.instance == inst_id}
        return bool(cfg_vars & written)

    def _becomes_op(self, child: FrameNode) -> bool:
        if child.kind == "adapter":
            return True
        if child.kind == "external":
            return child.has_asset_effects or any(e.kind == "ext_call" for e in child.effects())
        if child.kind == "internal" and child.frame is not None and child.frame.function.visibility in ("public", "external"):
            return child.has_asset_effects
        return False

    def _build(self, tree: FrameNode, oid: str, parent: Optional[str], order: Optional[int], caller: str,
               initiator_hint: str, root_id: str, entry=None) -> Operation:
        executor = self.entity_for(tree.executor, caller) if tree.executor is not None else None
        initiator = self.entity_for(tree.initiator, caller) if tree.initiator is not None else initiator_hint
        if entry is not None:
            inst, fn = entry
            label = f"{inst.contract.name}.{fn.name}({', '.join(p.name for p in fn.parameters)})"
            ep = EntryPoint(contract=inst.contract.name, function=fn.name, signature=fn.full_name, visibility=fn.visibility)
            ev0 = self.ev.site(tree.site, title=f"Entry point {inst.contract.name}.{fn.full_name}")
        elif tree.kind == "adapter":
            label = f"{tree.label} on {self.entities[executor].label if executor in self.entities else '?'}"
            ep = None
            ev0 = self.ev.site(tree.site)
        else:
            fn = tree.frame.function
            cname = tree.frame.instance.contract.name
            if tree.kind == "internal" and getattr(fn, "contract_declarer", None) is not None \
                    and fn.contract_declarer.name != cname:
                cname = fn.contract_declarer.name  # inherited implementation (e.g. super.deposit())
            label = f"{cname}.{fn.name}({', '.join(p.name for p in fn.parameters)})"
            ep = EntryPoint(contract=tree.frame.instance.contract.name, function=fn.name, signature=fn.full_name,
                            visibility=fn.visibility)
            ev0 = self.ev.site(tree.site, title=f"Call to {label}")
        op = Operation(id=oid, kind="other", label=label, parent=parent, order=order,
                       transaction="same" if parent else None, entrypoint=ep, initiator=initiator, executor=executor,
                       status=status("adapter" if tree.kind == "adapter" else "rule",
                                     "partially_supported" if tree.kind == "adapter" else "supported",
                                     rule=tree.adapter), evidence=[e for e in [ev0] if e])
        if tree.kind != "adapter" and tree.frame is not None and tree.site is not None and parent is not None:
            fn = tree.frame.function
            dsite = self.site_of(fn.source_mapping, fn.contract_declarer, fn)
            d = self.ev.site(dsite, title=f"Definition of {fn.contract_declarer.name}.{fn.full_name}")
            if d:
                op.evidence.append(d)
        reg = tree.detail.get("registry")
        if reg:
            g = reg["guard"]
            member = self.instances[reg["member"]]
            gev = self.ev.site(g.site)
            note = (f"Call target {reg['target']} is required to be registered ({g.describe()}); the configuration "
                    f"registers {member.label}, so the call is analysed as a call into {member.label}.")
            op.description = note
            op.evidence.extend(x for x in [gev, self.ev.config(f"registry-call:{oid}", "Registry member", note)] if x)
        if tree.adapter:
            op.evidence.append(self.ev.rule(tree.adapter, tree.adapter_note or "", None, tree.detail.get("standard")))
            std = tree.detail.get("standard")
            if std:
                op.evidence.append(self.ev.standard("EIP-4626 Tokenized Vaults", std,
                                                    "Defines asset(), deposit/mint/withdraw/redeem and the caller/receiver/owner roles."))
            isite = tree.detail.get("iface_site")
            if isite is not None:
                e = self.ev.site(isite, title=f"Interface {isite.contract}")
                if e:
                    op.evidence.append(e)
        self.operations[oid] = op
        self.op_tree[oid] = tree
        self.op_root[oid] = root_id
        if parent:
            self.op_children[parent].append(oid)
        for g in tree.conditions:
            self._condition(op, g, kind_override=None)
        self._collect(tree, op, (), caller, root_id)
        if tree.kind == "adapter":
            self._adapter_identity(tree, op, caller)
        op.kind = self._classify(op)
        return op

    def _condition(self, op: Operation, g: Guard, kind_override: Optional[str] = None) -> str:
        kind = kind_override or _guard_kind(g)
        text = g.describe()
        # required membership of an address other than the caller: that address belongs to the
        # registry's holder class (recorded before de-duplication: the same check text may test
        # different addresses, e.g. both sides of a transfer)
        req = required(g)
        if req is not None:
            for conj in X.conjuncts(req):
                m = X.membership(conj)
                if m is not None and not _is_sender(m[1]) and not isinstance(m[1], X.Inst):
                    holder = self.entity_for(m[1], "ent:any")
                    if holder and not holder.startswith("ent:members:") and holder[4:] not in self.instances:
                        self._member_of.setdefault(holder, self._members_entity(m[0]))
        cid_base = f"{op.id}#c:{_h(g.kind + g.text + str(g.negated) + g.site.key())}"
        for c in op.conditions:
            if c.id == cid_base:
                return c.id
        ev = self.ev.site(g.site)
        sat = _evaluate(g, self.registries)
        cst = None
        if sat is not None:
            cst = status("rule", "supported" if sat else "contradicted", rule="condition.evaluate",
                         note="Holds in this call context (caller rebound to the calling instance; registry members "
                              "from the configuration)" if sat else "Cannot hold in this call context")
        resolved = X.canonical(g.expr).render() if not isinstance(g.expr, X.Opaque) else None
        if resolved and (len(resolved) > 120 or resolved.replace(" ", "") == g.text.replace(" ", "")):
            resolved = None  # only keep short resolved forms that add information (e.g. rebound callers)
        op.conditions.append(Condition(id=cid_base, kind=kind, text=text, expression=resolved,
                                       negated=g.negated, evidence=[e for e in [ev] if e], status=cst))
        return cid_base

    def _in_dependency(self, tree: FrameNode) -> bool:
        if tree.frame is None or tree.site is None:
            return False
        fn = tree.frame.function
        f = self.site_of(fn.source_mapping, fn.contract_declarer, fn).file
        return any(f.startswith(d.path.rstrip("/") + "/") for d in self.cfg.dependencies) and tree.kind == "library"

    def _collect(self, tree: FrameNode, op: Operation, guards: Tuple[Guard, ...], caller: str, root_id: str) -> None:
        n_children = len(self.op_children.get(op.id, []))
        for item in tree.items:
            if isinstance(item, FrameNode):
                if item.kind == "modifier":
                    continue
                if self._becomes_op(item):
                    n_children += 1
                    slug = self._child_slug(item)
                    cid = f"{op.id}>{slug}"
                    k = 2
                    while cid in self.operations:
                        cid = f"{op.id}>{slug}#{k}"
                        k += 1
                    sub = self._build(item, cid, op.id, n_children, caller, op.executor, root_id)
                    for g in guards + item.guards:
                        self._condition(sub, g)
                    continue
                if not self._in_dependency(item):
                    for g in item.conditions:
                        self._condition(op, g)
                self._collect(item, op, guards + item.guards, caller, root_id)
                continue
            self._effect(item, op, guards + item.guards, caller, root_id)

    def _child_slug(self, item: FrameNode) -> str:
        if item.kind == "adapter":
            tgt = X.canonical(item.executor)
            return f"{_slug(tgt.render())}.{item.detail.get('function')}"
        fr = item.frame
        return f"{fr.instance.id}.{fr.function.full_name}"

    def _effect(self, e: Effect, op: Operation, guards: Tuple[Guard, ...], caller: str, root_id: str) -> None:
        if e.kind in ("transfer", "mint", "burn", "native"):
            self._flow(e, op, guards, caller, root_id)
        elif e.kind == "approve":
            owner = self.entity_for(e.src, caller)
            spender = self.entity_for(e.dst, caller)
            asset = self.asset_for(e.token)
            amt = self._amount_text(e, "amount")
            g = Guard("state", f"{self.entities[owner].label} approves {self.entities[spender].label} to spend "
                      f"{self.assets[asset].label} ({amt})", e.token, False, e.site)
            cid = self._condition(op, g, kind_override="state")
            c = next(c for c in op.conditions if c.id == cid)
            c.text = g.text
            c.evidence.extend(x for x in self._effect_evidence(e) if x not in c.evidence)
            if not any(p.role == "spender" and p.entity == spender for p in op.participants):
                op.participants.append(Participant(role="spender", entity=spender, expression=self._disp(e, "dst")))
            self._approvals.append((op, e, owner, spender, asset, guards))
        elif e.kind == "ext_call":
            self._external_call(e, op, guards, caller, root_id)
        elif e.kind == "read":
            self.reads.append((e, op))
            if e.target is not None and e.function not in ("balanceOf", "totalSupply", "decimals", "allowance", "symbol", "name"):
                self._record_call(op.executor, e, caller, read=True)
        elif e.kind == "write":
            self.writes.append((e, op))
        elif e.kind == "ledger":
            holder = self.entity_for(e.dst if e.dst is not None else e.src, caller)
            root = self.operations[root_id] if root_id in self.operations else op
            self.ledger_recs.append((e, op, root, holder, tuple(guards), caller))
        elif e.kind == "unresolved":
            uid = f"unr:dispatch:{_h(op.id + str(e.function))}"
            self.unresolved[uid] = Unresolved(id=uid, kind="dynamic_dispatch",
                                              text=f"{e.function} has no implementation in the analysed code; its effects are unknown.",
                                              subjects=[op.id], evidence=[x for x in [self.ev.site(e.site)] if x])
            op.unresolved.append(uid)

    def _record_call(self, src_ent: Optional[str], e: Effect, caller: str, read: bool) -> None:
        tgt = self.entity_for(e.target, caller)
        if not src_ent or not tgt or src_ent == tgt:
            return
        self._call_targets.add(self.akey(e.target))
        rec = self.calls.setdefault((src_ent, tgt), {"functions": OrderedDict(), "evidence": []})
        rec["functions"][e.function] = "read" if read else "call"
        ev = self.ev.site(e.site)
        if ev and ev not in rec["evidence"]:
            rec["evidence"].append(ev)

    def _external_call(self, e: Effect, op: Operation, guards, caller: str, root_id: str) -> None:
        tgt = self.entity_for(e.target, caller)
        self._record_call(op.executor, e, caller, read=False)
        n = len(self.op_children.get(op.id, [])) + 1
        cid = f"{op.id}>{_slug(X.canonical(e.target).render())}.{e.function}"
        k = 2
        while cid in self.operations:
            cid = f"{op.id}>{_slug(X.canonical(e.target).render())}.{e.function}#{k}"
            k += 1
        disp = e.detail.get("display_args") or [a.render() for a in e.args]
        shown = []
        for d, a in zip(disp, e.args):
            c = X.canonical(a)
            shown.append(c.render() if isinstance(c, (X.State, X.Inst, X.Getter, X.Index)) and len(c.render()) < 40 else d)
        args = ", ".join(shown)[:80]
        uid = f"unr:ext:{_h(tgt + str(e.function))}"
        if uid not in self.unresolved:
            self.unresolved[uid] = Unresolved(
                id=uid, kind="external_implementation",
                text=f"{self.entities[tgt].label}.{e.function}() is called but its implementation is outside the analysed "
                     "scope; which assets it moves (if any) is unknown.",
                subjects=[tgt], evidence=[x for x in [self.ev.site(e.site)] if x],
                suggestion="Add the implementation to the snapshot, bind it to an instance, or add a protocol adapter.")
        u = self.unresolved[uid]
        if cid not in u.subjects:
            u.subjects.append(cid)
        sub = Operation(id=cid, kind="call", label=f"{self.entities[tgt].label}.{e.function}({args})", parent=op.id, order=n,
                        transaction="same", initiator=op.executor, executor=tgt,
                        description="External call outside the analysed scope; asset effects unknown.",
                        status=status("rule", "unresolved", rule="call.external"),
                        evidence=[x for x in [self.ev.site(e.site)] if x], unresolved=[uid])
        self.operations[cid] = sub
        self.op_children[op.id].append(cid)
        self.op_root[cid] = root_id
        for g in guards:
            self._condition(sub, g)
        self._ext_ops.append((sub, e))

    def _disp(self, e: Effect, attr: str) -> str:
        d = e.detail.get("display", {}).get(attr)
        if d:
            return d
        v = getattr(e, attr)
        return v.render() if v is not None else ""

    def _amount_text(self, e: Effect, attr: str = "amount") -> str:
        return self._disp(e, attr)

    def _effect_evidence(self, e: Effect) -> List[str]:
        out = []
        s = self.ev.site(e.site)
        if s:
            out.append(s)
        # call chain: call sites of the frames that led here (innermost first, up to 3)
        f = e.frame
        hops = 0
        while f is not None and hops < 3:
            if f.callsite is not None:
                c = self.ev.site(f.callsite)
                if c and c not in out:
                    out.append(c)
            f = f.parent
            hops += 1
        if e.rule:
            impl = e.detail.get("impl_site") if isinstance(e.detail.get("impl_site"), Site) else None
            out.append(self.ev.rule(e.rule, e.rule_note or e.rule, impl, e.detail.get("standard")))
        return out

    def _flow(self, e: Effect, op: Operation, guards, caller: str, root_id: str) -> None:
        asset = self.asset_for(e.token)
        src = self.entity_for(e.src, caller) if e.kind != "mint" else None
        dst = self.entity_for(e.dst, caller) if e.kind != "burn" else None
        expr = self._disp(e, "amount")
        defn = e.detail.get("definition", {}).get("amount")
        note = f"{expr} = {defn}" if defn else None
        full = X.canonical(e.amount).render() if e.amount is not None else None
        if full and full != expr and (not defn or full != defn) and len(full) < 200:
            note = (note + "; " if note else "") + f"in terms of the entry call: {full}"
        cids = [self._condition(op, g) for g in guards]
        mech = {"mint": "mint", "burn": "burn", "native": "native"}.get(e.kind)
        if mech is None:
            mech = "transferFrom" if e.detail.get("allowance") else "transfer"
        verification = "supported"
        if e.rule == "erc4626.call":
            verification = "partially_supported"
        fid = f"{op.id}#f{len(op.inputs) + len(op.outputs) + 1}"
        fl = Flow(id=fid, asset=asset, from_=src, to=dst,
                  amount=Amount(asset=asset, unit="raw", expression=expr or None, note=note),
                  mechanism=mech, conditions=cids,
                  status=status("adapter" if e.rule and e.rule.startswith(("erc", "oz.safe")) else "rule",
                                verification, rule=e.rule, note=e.rule_note),
                  evidence=self._effect_evidence(e))
        if e.kind == "burn" or (src is not None and src != op.executor):
            op.inputs.append(fl)
        else:
            op.outputs.append(fl)
        role_map = []
        if e.kind == "mint":
            role_map.append(("share_receiver", dst, self._disp(e, "dst")))
        elif e.kind == "burn":
            role_map.append(("share_owner", src, self._disp(e, "src")))
        else:
            if src and src != op.executor:
                role_map.append(("payer", src, self._disp(e, "src")))
            if dst and dst != op.executor:
                role_map.append(("receiver", dst, self._disp(e, "dst")))
        for role, ent, ex in role_map:
            if ent and not any(p.role == role and p.entity == ent for p in op.participants):
                op.participants.append(Participant(role=role, entity=ent, expression=ex or None,
                                                   evidence=[x for x in [self.ev.site(e.site)] if x]))
        self.flows.append(FlowRec(fl, op, self.operations[root_id] if root_id in self.operations else op, e, guards))
        if e.kind == "mint" and e.rule == "erc4626.call":
            self._adapter_share_keys.add(self.akey(e.token))

    # ============================================================ ledgers
    def _is_balance_mapping(self, iid: str, name: str) -> bool:
        from .adapters import is_balance_mapping
        return is_balance_mapping(self.instances[iid].contract, name)

    def build_ledgers(self) -> None:
        """Interpret writes to address-keyed amount mappings (rule
        ``structural.ledger``).

        A mapping is a ledger when the analysed entry points both increase and
        decrease it and it changes together with asset flows. Its entries are a
        non-transferable accounting asset held per account. Whether an entry is
        a claim on the contract or a debt to it is decided from the flows in
        the same operation (votes):

        * increase while the contract receives value, or decrease while it pays
          the account -> claim (the contract owes the account);
        * increase while the contract gives the account value, or decrease
          while the account pays the contract -> debt (the account owes it).

        Conflicting or missing votes leave the ledger ambiguous (reported as
        unresolved, no positions)."""

        groups: Dict[Tuple[str, str], List[tuple]] = defaultdict(list)
        for rec in self.ledger_recs:
            e = rec[0]
            groups[(e.token.instance, e.token.name)].append(rec)
        for (iid, name), recs in groups.items():
            st = recs[0][0].token
            issuer = f"ent:{iid}"
            if self._is_balance_mapping(iid, name):
                self._ledger_as_token(iid, recs)
                continue
            incs = [r for r in recs if r[0].detail.get("delta") == "+"]
            decs = [r for r in recs if r[0].detail.get("delta") == "-"]
            roots = {r[2].id for r in recs}
            if not incs or not decs or not any(self.all_flows(self.operations[x]) for x in roots if x in self.operations):
                continue
            aid = self.asset_for(st)
            votes = []
            for r in recs:
                v = self._ledger_vote(r, issuer, recs)
                if v is not None:
                    votes.append(v)
            kinds = {v[0] for v in votes}
            kind = kinds.pop() if len(kinds) == 1 else "ambiguous"
            self.ledgers[aid] = {"kind": kind, "votes": votes, "issuer": issuer, "state": st}
            a = self.assets[aid]
            inst = self.instances[iid]
            why = "; ".join(f"{v[0]}: {v[1]}" for v in votes[:6])
            if kind == "claim":
                a.description = (f"Amounts {inst.label} owes to each account, recorded in {st.qualified()} "
                                 f"(non-transferable claim). Evidence: {why}.")
            elif kind == "debt":
                a.description = (f"Amounts each account owes to {inst.label}, recorded in {st.qualified()}. "
                                 f"Evidence: {why}.")
            else:
                a.description = (f"Per-account amounts recorded in {st.qualified()}; the code does not show whether they "
                                 f"are claims or debts" + (f" (conflicting evidence: {why})" if votes else "") + ".")
                uid = f"unr:ledger:{iid}.{name}"
                self.unresolved[uid] = Unresolved(
                    id=uid, kind="ambiguous_semantics",
                    text=f"{inst.contract.name}.{name} records per-account amounts, but whether an entry is a claim on "
                         f"{inst.label} or a debt to it cannot be decided from the flows" + (f" ({why})" if votes else "") + ".",
                    subjects=[aid], evidence=self._state_var_evidence(inst, name),
                    suggestion="Interpret the ledger from the protocol documentation (or the LLM stage) and review.")
            a.status = status("rule", "supported" if kind != "ambiguous" else "pending", rule="structural.ledger",
                              note=f"Ledger kind: {kind}")
            a.evidence.extend(x for x in self._state_var_evidence(inst, name) if x not in a.evidence)
            self._ledger_flows(aid, recs, kind)
            self._ledger_holders(recs)
            if kind == "claim":
                self.ledgers[aid]["deposit_assets"] = self._deposit_assets(aid, issuer)
            if kind == "debt":
                for r in recs:
                    if r[3] and r[3][4:] in self.instances:
                        self._debtors.add((r[3], issuer))

    def _deposit_assets(self, aid: str, issuer: str) -> Set[str]:
        """Assets an account pays in when its entry is credited (a staking / deposit balance, as opposed to
        a claim recorded when the account gives up something else, e.g. burns shares)."""

        out: Set[str] = set()
        for fr in self.flows:
            f = fr.flow
            if f.asset != aid or f.mechanism != "credit":
                continue
            for g in self.all_flows(fr.root):
                if g.to == issuer and g.mechanism not in ("mint", "burn", "credit", "debit", "ledger-transfer", "set") \
                        and g.from_ and not self._is_downstream(g.from_) and g.asset != aid:
                    out.add(g.asset)
        return out

    def _ledger_vote(self, rec, issuer: str, recs) -> Optional[Tuple[str, str]]:
        e, op, root, holder = rec[:4]
        delta = e.detail.get("delta")
        flows = [f for f in self.all_flows(root) if f.mechanism not in ("credit", "debit", "set")]

        def minted_by_issuer(f):
            return f.mechanism == "mint" and self.assets[f.asset].issuer == issuer

        if delta == "+":
            # payouts explained by a decrease of the same ledger in this operation (immediate settlement)
            settled = {r[3] for r in recs if r[2].id == root.id and r[0].detail.get("delta") == "-"}
            gives = [f for f in flows if f.to == holder and holder not in settled
                     and (f.from_ == issuer or minted_by_issuer(f))]
            gets = [f for f in flows if (f.to == issuer and f.from_ not in (issuer, None))
                    or (f.mechanism == "burn" and self.assets[f.asset].issuer == issuer)]
            if gives and not gets:
                return ("debt", f"{root.label} credits the entry while giving {self.assets[gives[0].asset].label} to the account")
            if gets and not gives:
                return ("claim", f"{root.label} credits the entry while {self.entities[issuer].label} receives "
                                 f"{self.assets[gets[0].asset].label}")
            return None
        if delta == "-":
            pays = [f for f in flows if f.to == holder and f.from_ == issuer]
            repays = [f for f in flows if f.from_ == holder and f.to == issuer]
            if pays and not repays:
                return ("claim", f"{root.label} debits the entry while paying {self.assets[pays[0].asset].label} to the account")
            if repays and not pays:
                return ("debt", f"{root.label} debits the entry while the account pays {self.assets[repays[0].asset].label}")
        return None

    def _ledger_flows(self, aid: str, recs, kind: str) -> None:
        by_op: Dict[str, List[tuple]] = defaultdict(list)
        for r in recs:
            by_op[r[1].id].append(r)
        for oid, rs in by_op.items():
            op = self.operations[oid]
            used = set()
            for i, a in enumerate(rs):
                for j, b in enumerate(rs):
                    if i == j or i in used or j in used:
                        continue
                    if a[0].detail.get("delta") == "-" and b[0].detail.get("delta") == "+" and a[3] != b[3] \
                            and X.key(a[0].amount) == X.key(b[0].amount):
                        used.update((i, j))
                        self._ledger_flow(aid, op, a, "transfer", src=a[3], dst=b[3], kind=kind)
            for i, r in enumerate(rs):
                if i in used:
                    continue
                d = r[0].detail.get("delta")
                if d == "+":
                    self._ledger_flow(aid, op, r, "credit", dst=r[3], kind=kind)
                elif d == "-":
                    self._ledger_flow(aid, op, r, "debit", src=r[3], kind=kind)
                else:
                    self._ledger_flow(aid, op, r, "set", dst=r[3], kind=kind)

    def _ledger_flow(self, aid, op: Operation, rec, mech: str, src=None, dst=None, kind: str = "") -> None:
        e = rec[0]
        cids = [self._condition(op, g) for g in rec[4]]
        expr = self._disp(e, "amount")
        mapping = e.detail.get("mapping")
        what = {"credit": "increases", "debit": "decreases", "transfer": "moves", "set": "sets"}[mech]
        note = f"`{e.detail.get('text', '')}` {what} the {mapping} entry"
        fl = Flow(id=f"{op.id}#f{len(op.inputs) + len(op.outputs) + 1}", asset=aid, from_=src, to=dst,
                  amount=Amount(asset=aid, unit="raw", expression=expr or None,
                                note=None if mech != "set" else "new value of the entry"),
                  mechanism=mech if mech != "transfer" else "ledger-transfer", conditions=cids,
                  status=status("rule", "supported" if kind != "ambiguous" else "pending", rule="structural.ledger",
                                note=note),
                  evidence=self._effect_evidence(e))
        (op.inputs if mech in ("debit", "transfer") else op.outputs).append(fl)
        root = self.operations[self.op_root.get(op.id, op.id)]
        self.flows.append(FlowRec(fl, op, root, e, rec[4]))

    def _ledger_holders(self, recs) -> None:
        """Entries keyed by a stored address (``request.receiver``) belong to the
        same class of holders as the entries created for caller-chosen
        addresses."""

        generic = {self.holder_class(r[3]) for r in recs if r[0].detail.get("delta") == "+"
                   and (r[3] == "ent:any" or r[3].startswith(("ent:param:", "ent:members:")))}
        if len(generic) != 1:
            return
        cls = generic.pop()
        for r in recs:
            ent = self.entities.get(r[3])
            if ent is not None and ent.kind == "unknown" and r[3].startswith("ent:expr:"):
                self._holder_override.setdefault(r[3], cls)

    def _ledger_as_token(self, iid: str, recs) -> None:
        """Inline updates of the ERC-20 balance mapping are token flows."""

        share = self.own_asset(iid)
        tok = X.Inst(iid, self.instances[iid].label)
        # a decrease of one balance and an increase of another by the same amount in the same
        # operation is a transfer (inline ERC-20 transfer), not a burn and a mint
        paired = set()
        by_op: Dict[str, List[int]] = defaultdict(list)
        for i, r in enumerate(recs):
            by_op[r[1].id].append(i)
        for idx in by_op.values():
            for i in idx:
                a = recs[i]
                if i in paired or a[0].detail.get("delta") != "-":
                    continue
                for j in idx:
                    b = recs[j]
                    if j in paired or j == i or b[0].detail.get("delta") != "+":
                        continue
                    if X.key(a[0].amount) == X.key(b[0].amount) and X.key(a[0].src) != X.key(b[0].dst):
                        paired.update((i, j))
                        e = a[0]
                        eff = Effect("transfer", e.site, e.frame, e.guards + tuple(g for g in b[0].guards if g not in e.guards),
                                     token=tok, src=e.src, dst=b[0].dst, amount=e.amount, rule="structural.token-accounting",
                                     rule_note=(f"moves balances in {e.detail.get('mapping')} (read by balanceOf()) from one "
                                                "address to another by the same amount"),
                                     function=e.function, detail=dict(e.detail, paired_site=b[0].site))
                        self._flow(eff, a[1], tuple(a[4]) + tuple(b[4]), a[5], self.op_root.get(a[1].id, a[1].id))
                        break
        for i, r in enumerate(recs):
            if i in paired:
                continue
            e, op = r[0], r[1]
            d = e.detail.get("delta")
            if d not in ("+", "-"):
                continue
            kind = "mint" if d == "+" else "burn"
            eff = Effect(kind, e.site, e.frame, e.guards, token=tok,
                         src=e.src, dst=e.dst, amount=e.amount, rule="structural.token-accounting",
                         rule_note=f"updates the balance mapping {e.detail.get('mapping')} read by balanceOf()",
                         function=e.function, detail=dict(e.detail))
            self._flow(eff, op, r[4], r[5], self.op_root.get(op.id, op.id))

    def reclassify(self) -> None:
        """Recompute operation kinds bottom-up once ledgers are known."""

        rec: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
        for o in self.operations.values():
            for f in o.inputs + o.outputs:
                if f.from_ and f.from_ == o.executor and f.from_[4:] in self.instances and f.to and f.to != f.from_ \
                        and f.mechanism in ("transfer", "transferFrom", "native"):
                    rec[(f.from_, f.asset)].add("ent:any" if f.to.startswith("ent:param:") else f.to)
        self._recipients = dict(rec)

        def visit(oid: str) -> None:
            for c in self.op_children.get(oid, []):
                visit(c)
            op = self.operations[oid]
            if op.kind != "call" or self.op_tree.get(oid) is not None:
                op.kind = self._classify(op)

        for op in [o for o in self.operations.values() if o.parent is None]:
            visit(op.id)

    # ---------------------------------------------------------- identity
    def _adapter_identity(self, tree: FrameNode, op: Operation, caller: str) -> None:
        """approve(X -> vault) before a pull of vault.asset() suggests X == asset()."""

        fn = tree.detail.get("function")
        if fn not in ("deposit", "mint"):
            return
        vault_key = self.akey(tree.executor)
        pulled = None
        for it in tree.items:
            if isinstance(it, Effect) and it.kind == "transfer":
                pulled = it
        if pulled is None:
            return
        self._pending_identity.append((op, tree, vault_key, pulled, caller))

    def finish_identities(self) -> None:
        for op, tree, vault_key, pulled, caller in self._pending_identity:
            pulled_asset = self.asset_for(pulled.token)
            cands = [(aop, e, owner, spender, asset) for (aop, e, owner, spender, asset, g) in self._approvals
                     if self.akey(e.dst) == vault_key and self.op_root.get(aop.id) == self.op_root.get(op.id)
                     and asset != pulled_asset]
            for aop, e, owner, spender, asset in cands:
                rid = f"rel:identity:{_slug(asset)}={_slug(pulled_asset)}"
                if rid in self.relations:
                    ev = self.ev.site(e.site)
                    if ev and ev not in self.relations[rid].evidence:
                        self.relations[rid].evidence.append(ev)
                    continue
                checks = [
                    Check(id="approve-before-pull", result="pass",
                          description=f"{self.assets[asset].label} is approved to {self.entities[spender].label} before the "
                                      f"{tree.detail.get('function')}() call that pulls {self.assets[pulled_asset].label}",
                          evidence=[x for x in [self.ev.site(e.site), self.ev.site(pulled.site)] if x]),
                    Check(id="asset-binding", result="unknown",
                          description=f"The value of {self.assets[pulled_asset].label} is not known statically (external "
                                      "implementation / deployment state); equality must be confirmed from chain state or configuration."),
                ]
                self.relations[rid] = Relation(
                    id=rid, kind="identity", source=asset, target=pulled_asset,
                    label="same token? (candidate)",
                    description=(f"Candidate: {self.assets[asset].label} and {self.assets[pulled_asset].label} denote the same "
                                 "token. Supported by the approval pattern only; not established."),
                    status=status("rule", "pending", rule="identity.approve-before-pull", checks=checks),
                    evidence=[x for x in [self.ev.site(e.site), self.ev.site(pulled.site)] if x])
                uid = f"unr:identity:{_slug(pulled_asset)}"
                self.unresolved[uid] = Unresolved(
                    id=uid, kind="identity",
                    text=(f"Is {self.assets[pulled_asset].label} the same token as {self.assets[asset].label}? The code approves "
                          f"{self.assets[asset].label} to the vault before calling it, but the vault's asset() is not resolved."),
                    subjects=[asset, pulled_asset, rid], evidence=self.relations[rid].evidence,
                    suggestion="Confirm with deployment state and add an 'identities' entry to fluxscope.json.")

    # ------------------------------------------------------- classification
    def descendants(self, oid: str) -> List[Operation]:
        out = []
        for c in self.op_children.get(oid, []):
            out.append(self.operations[c])
            out.extend(self.descendants(c))
        return out

    def all_flows(self, op: Operation) -> List[Flow]:
        fl = list(op.inputs) + list(op.outputs)
        for d in self.descendants(op.id):
            fl.extend(d.inputs + d.outputs)
        return fl

    def _classify(self, op: Operation) -> str:
        tree = self.op_tree.get(op.id)
        if tree is not None and tree.kind == "adapter":
            fn = tree.detail.get("function")
            op.status.rule = "erc4626.call"
            return {"deposit": "deposit", "mint": "deposit", "withdraw": "withdraw", "redeem": "redeem"}.get(fn, "other")
        ex = op.executor
        if self._merkle_payouts(op):
            op.status.rule = "kind.structural:claim"
            op.status.note = ("Kind 'claim': pays an amount the account proves against a merkle root "
                              "(MerkleProof verification on a bytes32[] proof) (structural rule).")
            return "claim"
        own = None
        if ex and ex.startswith("ent:") and ex[4:] in self.instances:
            own = self.asset_for(X.Inst(ex[4:], self.instances[ex[4:]].label))
        mine = op.inputs + op.outputs
        flows = self.all_flows(op)
        mints = [f for f in mine if f.mechanism == "mint" and f.asset == own]
        burns = [f for f in mine if f.mechanism == "burn" and f.asset == own]
        ins = [f for f in flows if f.to == ex and f.mechanism not in ("mint", "burn") and f.asset != own]
        outs = [f for f in mine if f.from_ == ex and f.mechanism not in ("mint", "burn") and f.asset != own]
        deep_outs = [f for f in flows if f.from_ == ex and f.mechanism not in ("mint", "burn")]
        reason = None
        kind = "other"
        kids = [self.operations[c] for c in self.op_children.get(op.id, [])]
        if not mine and len([k for k in kids if k.kind != "call"]) == 1 and tree is not None and tree.kind != "adapter":
            only = [k for k in kids if k.kind != "call"][0]
            if only.executor == ex:
                op.status.rule = f"kind.wrapper:{only.kind}"
                op.status.note = f"Wrapper: delegates to {only.label} in the same contract."
                op.description = op.description or f"Wrapper around {only.label}."
                return only.kind
        lk = lambda f: self.ledgers.get(f.asset, {}).get("kind")  # noqa: E731
        led = [f for f in mine if f.mechanism in ("credit", "debit", "ledger-transfer", "set")]
        value = [f for f in flows if f.mechanism not in ("credit", "debit", "ledger-transfer", "set")]
        ccred = [f for f in led if f.mechanism == "credit" and lk(f) == "claim"]
        cdeb = [f for f in led if f.mechanism == "debit" and lk(f) == "claim"]
        dcred = [f for f in led if f.mechanism == "credit" and lk(f) == "debt"]
        ddeb = [f for f in led if f.mechanism == "debit" and lk(f) == "debt"]
        if burns and ccred:
            by_param = [f for f in burns if f.amount.expression and f.amount.expression in self._param_names(op)]
            kind = "redeem" if by_param else "withdraw"
            op.status.rule = f"kind.structural:{kind}"
            op.status.note = (f"Kind '{kind}': burns the executor's own token and records a claim in "
                              f"{self.assets[ccred[0].asset].label} that is paid out later (structural rule).")
            return kind
        deposited = lambda f: self.ledgers.get(f.asset, {}).get("deposit_assets", set())  # noqa: E731
        if ccred and any(f.to == ex and f.asset in deposited(c) for c in ccred for f in value):
            op.status.rule = "kind.structural:deposit"
            op.status.note = (f"Kind 'deposit': receives an asset and credits the payer's balance in "
                              f"{self.assets[ccred[0].asset].label}, which is paid back in the same asset (structural rule).")
            return "deposit"
        if cdeb and any(f.from_ == ex and (f.to in {d.from_ for d in cdeb} or (f.to or "").startswith("ent:param:"))
                        and f.asset in deposited(d) for d in cdeb for f in value):
            op.status.rule = "kind.structural:withdraw"
            op.status.note = (f"Kind 'withdraw': debits the account's balance in {self.assets[cdeb[0].asset].label} and "
                              "pays back the asset that was deposited for it (structural rule).")
            return "withdraw"
        if cdeb and any(f.from_ == ex and f.to in {d.from_ for d in cdeb} for f in value):
            op.status.rule = "kind.structural:claim"
            op.status.note = (f"Kind 'claim': settles a recorded claim ({self.assets[cdeb[0].asset].label}) and pays the "
                              "account (structural rule).")
            return "claim"
        if dcred and any(f.to in {d.to for d in dcred} and (f.from_ == ex or f.mechanism == "mint") for f in value):
            op.status.rule = "kind.structural:borrow"
            op.status.note = (f"Kind 'borrow': gives the account value and records a debt in "
                              f"{self.assets[dcred[0].asset].label} (structural rule).")
            return "borrow"
        if ddeb and any(f.from_ in {d.from_ for d in ddeb} and f.to == ex for f in value):
            op.status.rule = "kind.structural:repay"
            op.status.note = (f"Kind 'repay': the account pays the executor and its debt in "
                              f"{self.assets[ddeb[0].asset].label} decreases (structural rule).")
            return "repay"
        if led and not value and all(f.mechanism == "ledger-transfer" for f in led if lk(f) in ("claim", "debt")) \
                and any(f.mechanism == "ledger-transfer" and lk(f) in ("claim", "debt") for f in led):
            op.status.rule = "kind.structural:transfer"
            op.status.note = "Kind 'transfer': moves a recorded entry to another account (structural rule)."
            return "transfer"
        if mints and ins:
            kind, reason = "deposit", "issues the executor's own token and receives another asset"
        elif burns and outs:
            by_param = [f for f in burns if f.amount.expression and f.amount.expression in self._param_names(op)]
            pays_param = [f for f in outs if f.amount.expression and f.amount.expression in self._param_names(op)]
            kind = "withdraw" if pays_param and not by_param else "redeem"
            reason = "burns the executor's own token and pays out another asset"
        elif mints and not ins and not burns:
            kind, reason = "mint", "issues the executor's own token without a recognised inflow"
        elif burns and not outs and not mints and self._external_payout(op, burns):
            fname, rcv = self._external_payout(op, burns)
            by_param = [f for f in burns if f.amount.expression and f.amount.expression in self._param_names(op)]
            kind = "redeem" if by_param else "withdraw"
            reason = (f"burns the executor's own token and calls {fname}() with the receiver `{rcv}`: the external "
                      "contract pays out")
        elif burns and not outs and not mints:
            kind, reason = "burn", "burns the executor's own token without a recognised payout"
        else:
            downstream = [f for f in deep_outs if f.to and self._is_downstream(f.to)]
            to_parties = [f for f in deep_outs if f.to and not self._is_downstream(f.to) and f.to != op.initiator]
            back = [f for f in mine if f.to and f.to == op.initiator and f.from_ == ex]
            redeems = [d for d in self.descendants(op.id) if d.kind in ("redeem", "withdraw") and d.initiator == ex]
            deposits = [d for d in self.descendants(op.id) if d.kind == "deposit" and d.initiator == ex]
            calls_out = [d for d in self.descendants(op.id) if d.kind == "call"]
            simple = (mine and not kids and all(f.mechanism in ("transfer", "transferFrom") for f in mine)
                      and len({f.asset for f in mine}) == 1)
            caller_is_contract = op.initiator in self.entities and \
                self.entities[op.initiator].kind in ("contract", "external_contract")
            from_caller = [f for f in mine if f.from_ == op.initiator and f.to == ex
                           and f.mechanism in ("transfer", "transferFrom")]
            kid_kinds = {k.kind for k in kids if k.kind != "call"}
            to_param = [f for f in mine if f.from_ == ex and f.to and f.to.startswith("ent:param:")]
            deep_param_payouts = [f for f in flows if f.to and f.to.startswith(f"ent:param:{op.id[3:].split('.', 1)[0]}.")
                                  and f.mechanism not in ("mint", "burn", "credit", "debit", "set")]
            account = lambda e: e == op.initiator or (e or "").startswith("ent:param:")  # noqa: E731
            pay_in = [f for f in mine if f.to == ex and account(f.from_) and f.mechanism in ("transfer", "transferFrom", "native")]
            pay_out = [f for f in mine if f.from_ == ex and account(f.to) and f.mechanism in ("transfer", "native")]
            if pay_in and pay_out and not led and not mints and not burns \
                    and {f.asset for f in pay_in}.isdisjoint({f.asset for f in pay_out}):
                kind, reason = "swap", "receives one asset from the caller and pays a different asset to the caller or its receiver"
            elif back and (ex, op.initiator) in self._debtors:
                kind, reason = "repay", "pays the calling contract, to which the executor owes a recorded debt"
            elif not mine and kid_kinds and kid_kinds <= {"repay"}:
                kind, reason = "repay", "repays a recorded debt through the nested call"
            elif not mine and kid_kinds and kid_kinds <= {"withdraw", "redeem", "claim"} and deep_param_payouts \
                    and not [f for f in flows if f.to == ex and f.mechanism not in ("mint", "burn")]:
                kind, reason = "withdraw", "has another contract pay out to a receiver chosen by the caller (nested withdrawal)"
            elif simple and caller_is_contract and to_param and len(to_param) == len(mine):
                kind, reason = "withdraw", "pays out to a receiver chosen by the calling contract"
            elif simple and not caller_is_contract:
                kind, reason = "transfer", "moves a single asset"
            elif to_parties or calls_out and not (downstream or deposits or back):
                kind, reason = "other", "pays several parties and/or calls code outside the analysed scope"
            elif from_caller and not back and not to_parties and caller_is_contract:
                kind, reason = "deposit", "receives assets from the calling contract"
            elif self._sole_recipient_payouts(op) and not deposits and not back:
                rcv = self._sole_recipient_payouts(op)[0].to
                kind, reason = "withdraw", (f"returns assets to {self.entities[rcv].label}, the only recipient of the "
                                            "executor's payouts of this asset (whoever triggers it)")
            elif back:
                kind, reason = "withdraw", "returns assets to the calling contract"
            elif redeems and not downstream and not back:
                kind, reason = "deallocate", "pulls assets back from a downstream protocol"
            elif (downstream or deposits) and not back:
                kind, reason = "allocate", "moves the executor's assets into a downstream contract"
        op.status.rule = f"kind.structural:{kind}"
        if reason:
            op.status.note = f"Kind '{kind}': {reason} (structural rule)."
        return kind

    def _external_payout(self, op: Operation, burns) -> Optional[Tuple[str, str]]:
        """(function, receiver) of an external call that receives an address parameter of the entry point
        other than the account whose tokens are burned (``daiJoin.exit(receiver, assets)``,
        ``pool.withdraw(asset, assets, receiver)``)."""

        tree = self.op_tree.get(op.id)
        if tree is None or tree.frame is None:
            return None
        addr = {p.name for p in tree.frame.function.parameters if str(p.type) == "address"}
        burned = {(f.from_ or "").rsplit(".", 1)[-1] for f in burns}
        for e in tree.effects():
            if e.kind != "ext_call":
                continue
            for a in getattr(e, "args", ()) or ():
                r = a.render() if a is not None else ""
                if r in addr and r not in burned:
                    return e.function, r
        return None

    def _merkle_payouts(self, op: Operation) -> List[Flow]:
        """Payouts of an entry point that checks a merkle proof: a ``bytes32[]`` parameter and a call into a
        ``Merkle*`` library (OpenZeppelin MerkleProof, solady MerkleProofLib, ...). The paid amount is an
        entitlement fixed by the root, held by the account until claimed."""

        if op.parent is not None:
            return []
        tree = self.op_tree.get(op.id)
        if tree is None or tree.frame is None:
            return []
        fn = tree.frame.function
        if not any(str(p.type) == "bytes32[]" for p in fn.parameters):
            return []
        libs = set()
        for c in fn.all_library_calls():
            dest = getattr(c, "destination", None) or (c[0] if isinstance(c, tuple) else None)
            libs.add(getattr(dest, "name", ""))
        if not any("merkle" in n.lower() for n in libs):
            return []
        ex = op.executor
        acct = lambda e: e == op.initiator or e == "ent:any" or (e or "").startswith("ent:param:")  # noqa: E731
        return [f for f in self.all_flows(op) if f.from_ == ex and acct(f.to) and f.mechanism in ("transfer", "native")]

    def _param_names(self, op: Operation) -> Set[str]:
        tree = self.op_tree.get(op.id)
        if tree is None or tree.frame is None:
            return set()
        return {p.name for p in tree.frame.function.parameters}

    def _sole_recipient_payouts(self, op: Operation) -> List[Flow]:
        """Flows in which the executor pays an analysed instance that receives all of the executor's own payouts
        of that asset, while someone else triggers the call."""

        ex = op.executor
        return [f for f in op.inputs + op.outputs
                if f.from_ == ex and f.to and f.to != op.initiator and f.to[4:] in self.instances
                and f.mechanism in ("transfer", "transferFrom") and self._recipients.get((ex, f.asset)) == {f.to}]

    def _is_downstream(self, ent: str) -> bool:
        e = self.entities.get(ent)
        return e is not None and e.kind in ("contract", "external_contract")

    # ============================================================ positions
    def holder_class(self, holder: str) -> str:
        """Positions are kept per class of holder: an address chosen by the caller
        (a ``receiver`` / ``owner`` argument) is just another arbitrary account."""

        if holder in self._holder_override:
            return self._holder_override[holder]
        if holder in self._member_of:
            return self._member_of[holder]
        if holder.startswith("ent:param:"):
            return "ent:any"
        return holder

    def position(self, holder: str, asset: str, kind: Optional[str] = None, evidence: Iterable[str] = (),
                 support: str = "derived", rule: str = "position.flow") -> str:
        holder = self.holder_class(holder)
        pid = f"pos:{holder[4:] if holder.startswith('ent:') else holder}:{asset[6:] if asset.startswith('asset:') else asset}"
        a = self.assets[asset]
        if kind is None:
            kind = {"share": "share", "lp_token": "lp", "debt_token": "debt", "accounting": "claim"}.get(a.kind, "custody")
        if pid not in self.positions:
            h = self.entities[holder]
            self.positions[pid] = Position(
                id=pid, holder=holder, asset=asset, kind=kind, label=f"{a.label} held by {h.label}",
                amount=Amount(asset=asset, unit="unknown", note="Static analysis: amount not known"),
                status=status(support, rule=rule), evidence=[])
            self.relations[f"rel:holds:{pid}"] = Relation(
                id=f"rel:holds:{pid}", kind="holds", source=holder, target=pid, asset=asset,
                label="holds", status=status(support, rule=rule), evidence=[])
        p = self.positions[pid]
        r = self.relations[f"rel:holds:{pid}"]
        for e in evidence:
            if e and e not in p.evidence:
                p.evidence.append(e)
            if e and e not in r.evidence:
                r.evidence.append(e)
        return pid

    def build_positions(self) -> None:
        for fr in self.flows:
            f = fr.flow
            if fr.root.kind == "transfer" and fr.op.id == fr.root.id and self.assets[f.asset].kind in ("share", "accounting") \
                    and self.assets[f.asset].issuer == fr.op.executor:
                a = self.assets[f.asset]
                note = (f"Transferable: holders can move {a.label} to other accounts via {fr.root.label}; "
                        "the holder of a position may differ from the original depositor.")
                if note not in (a.description or ""):
                    a.description = ((a.description or "") + " " + note).strip()
                for x in f.evidence[:1]:
                    if x not in a.evidence:
                        a.evidence.append(x)
                continue
            ev = [x for x in f.evidence if x.startswith("ev:src")][:2]
            if f.asset in self.ledgers:
                lk = self.ledgers[f.asset]["kind"]
                if lk == "ambiguous":
                    continue
                for h in (f.to, f.from_):
                    if h:
                        pid = self.position(h, f.asset, kind=lk, evidence=ev, support="rule", rule="position.ledger")
                        if lk == "debt":
                            self.positions[pid].counterparty = self.ledgers[f.asset]["issuer"]
                continue
            if f.to:
                self.position(f.to, f.asset, evidence=ev)
            if f.from_ and f.mechanism != "mint":
                self.position(f.from_, f.asset, evidence=ev)
        for e, op in self.reads:
            if e.function == "balanceOf" and len(e.args) == 1 and e.target is not None:
                holder = self.entity_for(e.args[0], "ent:any")
                if holder is None or holder == "ent:any" or self.entities[holder].kind not in ("contract", "external_contract"):
                    continue
                c = X.canonical(e.target)
                if isinstance(c, X.Index):
                    continue
                asset = self.asset_for(e.target)
                ev = self.ev.site(e.site, description="Balance read used for accounting")
                self.position(holder, asset, evidence=[ev] if ev else [], rule="position.balance-read")

    # ================================================== claims / exits / deps
    def own_asset(self, iid: str) -> str:
        return self.asset_for(X.Inst(iid, self.instances[iid].label))

    def root_ops(self) -> List[Operation]:
        return [o for o in self.operations.values() if o.parent is None]

    def build_claims(self) -> None:
        # (b) exclusive-withdrawal claims between instances
        acct: Dict[Tuple[str, str], str] = {}
        for er in self.entries:
            if er.op is None:
                continue
            j = f"ent:{er.instance.id}"
            caller = er.caller
            if caller == "ent:any" or not caller.startswith("ent:"):
                continue
            if caller[4:] not in self.instances and not self._bound_external(caller):
                continue
            pays = [f for f in er.op.outputs + er.op.inputs if f.from_ == j and f.to == caller and f.mechanism in ("transfer", "transferFrom")]
            if not pays:
                continue
            key = (caller, j)
            if key not in acct:
                aid = f"asset:claim:{er.instance.id}"
                if aid not in self.assets:
                    self.assets[aid] = Asset(
                        id=aid, label=f"Claim on {self.entities[j].label}", kind="accounting", issuer=j,
                        identity=AssetIdentity(kind="scoped_expression", expression=f"{er.instance.contract.name} (non-tokenized)",
                                               note="Non-transferable claim recovered from access control and payouts"),
                        description=(f"{self.entities[caller].label} is the only caller allowed to invoke "
                                     f"{er.instance.contract.name}.{er.function.name}(), which pays assets from "
                                     f"{self.entities[j].label} to it. This is a claim, not an additional amount."),
                        status=status("rule", rule="claim.exclusive-withdrawal"))
                ac = [x for x in er.op.participants if x.role == "authorized_caller"]
                ev = (ac[0].evidence if ac else []) + [x for x in pays[0].evidence if x.startswith("ev:src")][:1]
                acct[key] = self.position(caller, aid, kind="claim", evidence=ev, support="rule",
                                          rule="claim.exclusive-withdrawal")
            pid = acct[key]
            for f in pays:
                tgt = self.position(j, f.asset)
                self._claim(pid, tgt, er.op, f, basis=f"{er.instance.contract.name}.{er.function.name}() pays "
                            f"{self.assets[f.asset].label} ({f.amount.expression}) to the only allowed caller",
                            rule="claim.exclusive-withdrawal", optional_guards=())
            for d in self.descendants(er.op.id):
                for b in d.inputs:
                    if b.mechanism == "burn" and b.from_ == j:
                        tgt = self.position(j, b.asset)
                        self._claim(pid, tgt, er.op, b, basis=f"{er.instance.contract.name}.{er.function.name}() can redeem "
                                    f"{self.assets[b.asset].label} held by {self.entities[j].label}",
                                    rule="claim.exclusive-withdrawal", via=d)
        # (b') the same claim when the only recipient of the payouts is not the only allowed caller
        self._open_triggers: List[Tuple[str, Operation, str]] = []
        for er in self.entries:
            if er.op is None:
                continue
            j = f"ent:{er.instance.id}"
            for f in self._sole_recipient_payouts(er.op):
                k = f.to
                key = (k, j)
                if key not in acct:
                    aid = f"asset:claim:{er.instance.id}"
                    if aid not in self.assets:
                        self.assets[aid] = Asset(
                            id=aid, label=f"Claim on {self.entities[j].label}", kind="accounting", issuer=j,
                            identity=AssetIdentity(kind="scoped_expression", expression=f"{er.instance.contract.name} (non-tokenized)",
                                                   note="Non-transferable claim recovered from payouts"),
                            description=(f"{self.entities[k].label} is the only recipient of {self.entities[j].label}'s payouts "
                                         f"of {self.assets[f.asset].label}. This is a claim, not an additional amount."),
                            status=status("rule", rule="claim.sole-recipient"))
                    ev = [x for x in f.evidence if x.startswith("ev:src")][:1]
                    acct[key] = self.position(k, aid, kind="claim", evidence=ev, support="rule", rule="claim.sole-recipient")
                who = self.entities[er.caller].label if er.caller in self.entities else er.caller
                self._claim(acct[key], self.position(j, f.asset), er.op, f,
                            basis=(f"{er.instance.contract.name}.{er.function.name}() pays {self.assets[f.asset].label} "
                                   f"({f.amount.expression}) to {self.entities[k].label}, the only recipient of such payouts; "
                                   f"{who} can trigger it"),
                            rule="claim.sole-recipient", optional_guards=())
                self._open_triggers.append((acct[key], er.op, who))
        self._acct = acct
        # (a) share claims for tokens issued by analysed instances
        for iid in self.instances:
            share = self.own_asset(iid)
            issuer = f"ent:{iid}"
            for op in self.operations.values():
                if op.executor != issuer:
                    continue
                burns = [f for f in op.inputs if f.mechanism == "burn" and f.asset == share]
                if not burns:
                    continue
                payouts = [f for f in op.outputs if f.from_ == issuer and f.asset != share and f.mechanism != "mint"]
                for b in burns:
                    holder_pos = self.position(b.from_, share)
                    for p in payouts:
                        tgt = self.position(issuer, p.asset)
                        self._claim(holder_pos, tgt, op, p, basis=_basis(p), rule="claim.share-redemption")
                        for d in self.descendants(op.id):
                            if d.initiator != issuer or not d.executor:
                                continue
                            refill = any(f.to == issuer and f.asset == p.asset for f in self.all_flows(d))
                            if not refill:
                                continue
                            if (issuer, d.executor) in acct:
                                self._claim(holder_pos, acct[(issuer, d.executor)], op, p,
                                            basis=f"{_basis(p)}; the shortfall is pulled from {self.entities[d.executor].label}",
                                            rule="claim.share-redemption", via=d)
                            for nb in d.inputs:
                                if nb.mechanism == "burn" and nb.from_ == issuer and nb.asset != share:
                                    self._claim(holder_pos, self.position(issuer, nb.asset), op, p,
                                                basis=f"{_basis(p)}; the shortfall is obtained by redeeming "
                                                      f"{self.assets[nb.asset].label} held by {self.entities[issuer].label}",
                                                rule="claim.share-redemption", via=d)
        # (d) ledger entries: claims settled by a payout, debts repaid by the account
        for aid, info in self.ledgers.items():
            issuer = info["issuer"]
            for fr in self.flows:
                f = fr.flow
                if f.asset != aid or f.mechanism != "debit" or not f.from_:
                    continue
                if any(g.asset == aid and g.mechanism == "credit" for g in self.all_flows(fr.root)):
                    continue  # entry created and settled in the same call
                pos = self.position(f.from_, aid, kind=info["kind"]) if info["kind"] != "ambiguous" else None
                if pos is None:
                    continue
                vflows = [g for g in self.all_flows(fr.root) if g.mechanism not in ("credit", "debit", "ledger-transfer", "set")]
                if info["kind"] == "claim":
                    for g in vflows:
                        if (g.to == f.from_ or (g.to or "").startswith("ent:param:")) and g.from_ == issuer:
                            self._claim(pos, self.position(issuer, g.asset), fr.root, g,
                                        basis=f"{fr.root.label} settles the {self.assets[aid].label} entry and pays "
                                              f"{self.assets[g.asset].label} ({g.amount.expression}) to the account",
                                        rule="claim.ledger")
                elif info["kind"] == "debt":
                    rid = f"rel:debt:{_slug(pos)}"
                    rep = [g for g in vflows if g.from_ == f.from_ and g.to == issuer]
                    if rid not in self.relations:
                        self.relations[rid] = Relation(
                            id=rid, kind="debt", source=pos, target=issuer, asset=aid, label="owes",
                            description=f"{self.entities[self.positions[pos].holder].label} owes "
                                        f"{self.entities[issuer].label} the amounts recorded in {self.assets[aid].label}.",
                            basis_detail="; ".join(v[1] for v in info["votes"][:4]),
                            status=status("rule", "supported", rule="debt.ledger"), evidence=[])
                    r = self.relations[rid]
                    for g in rep[:1]:
                        line = f"repaid in {self.assets[g.asset].label} via {fr.root.label}"
                        if line not in (r.basis_detail or ""):
                            r.basis_detail = f"{r.basis_detail}; {line}" if r.basis_detail else line
                    for x in f.evidence[:2]:
                        if x.startswith("ev:src") and x not in r.evidence:
                            r.evidence.append(x)
        # (e) merkle entitlements: an amount proven against a root, paid by the contract when claimed
        self._merkle_positions: List[Tuple[str, Operation]] = []
        for op in self.root_ops():
            pays = self._merkle_payouts(op)
            if not pays or not op.executor or op.executor[4:] not in self.instances:
                continue
            iid = op.executor[4:]
            aid = f"asset:claim:merkle:{iid}"
            if aid not in self.assets:
                self.assets[aid] = Asset(
                    id=aid, label=f"Merkle entitlement in {self.instances[iid].contract.name}", kind="accounting",
                    issuer=op.executor,
                    identity=AssetIdentity(kind="scoped_expression", expression=f"{self.instances[iid].contract.name} merkle root",
                                           note="Non-tokenized amount fixed by a merkle root and proven by the account"),
                    description="Amounts committed in a merkle root that an account can claim with a proof.",
                    status=status("rule", rule="claim.merkle"))
            pid = self.position("ent:any", aid, kind="claim", support="rule", rule="claim.merkle",
                                evidence=[x for x in op.evidence if x.startswith("ev:src")][:1])
            for f in pays:
                self._claim(pid, self.position(op.executor, f.asset), op, f,
                            basis=f"{op.label} pays {self.assets[f.asset].label} ({f.amount.expression}) for a valid merkle proof",
                            rule="claim.merkle")
            self._merkle_positions.append((pid, op))
        # (c) ERC-4626 shares held by analysed instances
        for fr in self.flows:
            f = fr.flow
            if f.mechanism == "mint" and fr.effect.rule == "erc4626.call" and f.to:
                tree = self.op_tree.get(fr.op.id)
                vault = self.entity_for(tree.executor, "ent:any")
                under = self.asset_for(X.Getter(X.canonical(tree.executor), "asset", ()))
                src = self.position(f.to, f.asset)
                tgt = self.position(vault, under)
                rid = f"rel:claim:{_slug(src)}->{_slug(tgt)}"
                if rid not in self.relations:
                    checks = [
                        Check(id="standard-interface", result="pass",
                              description="Destination type declares the EIP-4626 interface (asset, deposit, mint, withdraw, redeem)",
                              evidence=[x for x in fr.op.evidence if x.startswith(("ev:src", "ev:std"))][:3]),
                        Check(id="implementation", result="unknown",
                              description="Vault implementation is outside the analysed scope; redemption limits, fees and "
                                          "losses are not analysed"),
                    ]
                    self.relations[rid] = Relation(
                        id=rid, kind="claim", source=src, target=tgt, asset=under,
                        label="redeemable (EIP-4626)",
                        basis_detail="EIP-4626: shares are redeemable for asset() through redeem()/withdraw(); "
                                     "convertToAssets() is an ideal-conditions value, not a guaranteed amount.",
                        operation=fr.op.id,
                        status=status("adapter", "partially_supported", rule="claim.erc4626", checks=checks),
                        evidence=[x for x in fr.op.evidence if x.startswith(("ev:src", "ev:std", "ev:rule"))][:4])

    def _claim(self, src: str, tgt: str, op: Operation, flow: Flow, basis: str, rule: str,
               optional_guards=(), via: Optional[Operation] = None) -> None:
        if src == tgt:
            return
        rid = f"rel:claim:{_slug(src)}->{_slug(tgt)}"
        conds = []
        if via is not None:
            for c in via.conditions:
                if c.kind in ("branch", "loop"):
                    conds.append(c)
        for cid in flow.conditions:
            c = next((c for c in self.operations[self._flow_op(flow)].conditions if c.id == cid), None)
            if c is not None and c not in conds:
                conds.append(c)
        ev = [x for x in flow.evidence if x.startswith("ev:src")][:3]
        if rid in self.relations:
            r = self.relations[rid]
            for x in ev:
                if x not in r.evidence:
                    r.evidence.append(x)
            return
        tgt_asset = self.positions[tgt].asset
        rel_asset = flow.asset if self.assets[tgt_asset].kind == "accounting" else tgt_asset
        self.relations[rid] = Relation(
            id=rid, kind="claim", source=src, target=tgt, asset=rel_asset,
            label="claims" if rel_asset == tgt_asset else f"claims ({self.assets[rel_asset].label})",
            basis_detail=basis, operation=op.id, conditions=[c.model_copy() for c in conds][:6],
            status=status("rule", "pending", rule=rule), evidence=ev)

    def _flow_op(self, flow: Flow) -> str:
        return flow.id.split("#f")[0]

    def build_exits(self) -> None:
        for pid, pos in list(self.positions.items()):
            asset = self.assets[pos.asset]
            ops: List[Tuple[Operation, Optional[Operation]]] = []
            ledger = pos.asset in self.ledgers
            if ledger and pos.kind != "claim":
                continue
            consume = ("debit",) if ledger else ("burn",)
            for op in self.root_ops():
                if op.kind not in ("redeem", "withdraw", "deallocate", "burn", "remove_liquidity", "unstake", "repay", "claim"):
                    continue
                if ledger and any(f.mechanism == "credit" and f.asset == pos.asset for f in self.all_flows(op)):
                    continue  # creates and settles the entry in the same call (not an exit of an existing entry)
                tree_burns = [f for f in op.inputs if f.mechanism in consume and f.asset == pos.asset
                              and self.holder_class(f.from_ or "") == pos.holder]
                if tree_burns:
                    ops.append((op, None))
                    continue
                for d in self.descendants(op.id):
                    if any(f.mechanism in consume and f.asset == pos.asset and self.holder_class(f.from_ or "") == pos.holder
                           for f in d.inputs):
                        ops.append((op, d))
                        break
            if asset.kind == "accounting" and pos.kind == "claim":
                for er in self.entries:
                    if er.op is not None and er.caller in (pos.holder, "ent:any") and asset.issuer == f"ent:{er.instance.id}":
                        if any(f.from_ == asset.issuer and f.to == pos.holder for f in er.op.outputs + er.op.inputs):
                            ops.append((er.op, None))
            for op, inner in ops:
                self._exit(pos, op, inner)
        for pid, op in getattr(self, "_merkle_positions", []):
            self._exit(self.positions[pid], op, None)

    def _exit(self, pos: Position, op: Operation, inner: Optional[Operation]) -> None:
        xid = f"exit:{_slug(pos.id[4:])}:{_slug(op.id[3:])}"
        if xid in self.exits:
            return
        steps: List[ExitStep] = []
        actor = op.initiator
        steps.append(ExitStep(order=1, operation=op.id, actor=actor,
                              description=f"{self.entities[actor].label if actor in self.entities else actor} calls {op.label}",
                              transaction=None, optional=False,
                              conditions=[c for c in op.conditions if c.kind not in ("branch", "loop")][:8],
                              evidence=op.evidence[:2]))
        seq = self.descendants(op.id)
        n = 2
        for d in seq:
            if d.kind == "call" and not d.inputs and not d.outputs:
                continue
            if not (d.inputs or d.outputs or self._has_flow_desc(d)):
                continue
            if d.executor == op.executor and d.initiator == op.initiator and d.kind == op.kind and n == 2 and not d.parent != op.id:
                pass
            branch = [c for c in d.conditions if c.kind in ("branch", "loop")]
            steps.append(ExitStep(order=n, operation=d.id, actor=d.initiator,
                                  description=f"{self.entities[d.initiator].label if d.initiator in self.entities else d.initiator} "
                                              f"triggers {d.label}",
                                  transaction="same", optional=bool(branch), conditions=d.conditions[:8], evidence=d.evidence[:2]))
            n += 1
        consumes = [f for f in op.inputs if f.asset == pos.asset and self.holder_class(f.from_ or "") == pos.holder]
        if inner is not None:
            consumes = [f for f in inner.inputs if f.asset == pos.asset]
        if not consumes:
            consumes = [f for d in seq for f in d.inputs if f.asset == pos.asset and f.mechanism in ("burn", "debit")][:1]
        allf = [f for f in self.all_flows(op) if f.mechanism != "mint" and f.asset != pos.asset]
        returns = [f for f in allf if self.holder_class(f.to or "") == pos.holder]
        if not returns:
            system = {e.id for e in self.entities.values() if e.kind == "contract"}
            returns = [f for f in op.outputs if f.mechanism != "mint" and f.to and f.to not in system and f.asset != pos.asset]
        if not returns:
            returns = [f for f in allf if f.to == op.initiator]
        unres = sorted({u for d in [op] + seq for u in d.unresolved})
        adapters = any(d.status.verification == "partially_supported" for d in seq)
        ver = "supported" if not unres and not adapters else "partially_supported"
        label = f"Exit {self.assets[pos.asset].label} via {op.label}"
        self.exits[xid] = ExitPath(
            id=xid, position=pos.id, label=label, steps=steps,
            consumes=[f.model_copy(update={"id": f"{xid}#in{i}"}) for i, f in enumerate(consumes)],
            returns=[f.model_copy(update={"id": f"{xid}#out{i}"}) for i, f in enumerate(returns)],
            conditions=[c for c in op.conditions if c.kind in ("access_control", "modifier", "require")][:8],
            status=status("derived", ver, rule="exit.burn-path"),
            evidence=op.evidence[:3], unresolved=unres)
        rid = f"rel:exit:{_slug(pos.id)}->{_slug(op.id)}"
        self.relations[rid] = Relation(id=rid, kind="exit", source=pos.id, target=op.id, label="exit via",
                                       operation=op.id, status=status("derived", ver, rule="exit.burn-path"),
                                       evidence=op.evidence[:2])

    def _has_flow_desc(self, op: Operation) -> bool:
        return any(d.inputs or d.outputs for d in self.descendants(op.id))

    def build_dependencies(self) -> None:
        # calls relations
        for (src, tgt), rec in self.calls.items():
            if src not in self.entities or tgt not in self.entities:
                continue
            fns = list(rec["functions"].items())
            rid = f"rel:calls:{_slug(src)}->{_slug(tgt)}"
            self.relations[rid] = Relation(
                id=rid, kind="calls", source=src, target=tgt,
                label=", ".join(f"{f}()" for f, _ in fns[:4]) + (" …" if len(fns) > 4 else ""),
                description="Functions called: " + ", ".join(f"{f}() ({k})" for f, k in fns),
                status=status("rule", rule="calls.static"), evidence=rec["evidence"][:6])
        # value dependencies of share redemptions
        for r in list(self.relations.values()):
            if r.kind != "claim" or r.status.rule != "claim.share-redemption":
                continue
            op = self.operations[r.operation]
            for fr in self.flows:
                if fr.op.id != op.id or fr.flow.from_ != op.executor or fr.flow.mechanism == "mint":
                    continue
                self._value_deps(r.source, fr.effect.amount, fr.effect, op)
        # state-dependent accounting (writers of accounting variables read by value expressions)
        # operational dependencies from conditions on external state
        for op in self.operations.values():
            for c in op.conditions:
                pass

    def _value_deps(self, src_pos: str, amount: Optional[X.Expr], eff: Effect, op: Operation) -> None:
        if amount is None:
            return
        seen: Set[str] = set()
        for node in amount.walk():
            if isinstance(node, X.Call) and node.instance:
                tgt = f"ent:{node.instance}"
                rid = f"rel:dep:{_slug(src_pos)}->{_slug(tgt)}"
                if rid in self.relations or rid in seen:
                    continue
                seen.add(rid)
                self.relations[rid] = Relation(
                    id=rid, kind="depends_on", source=src_pos, target=tgt, label=f"value uses {node.render()}",
                    basis_detail=f"The payout amount of {op.label} is computed from {node.render()}",
                    status=status("rule", rule="dependency.value"), evidence=[x for x in [self.ev.site(eff.site)] if x])
                self._accounting_deps(node, tgt)

    def _accounting_deps(self, call: X.Call, inst_ent: str) -> None:
        """Instance value -> state variables -> writers that read external contracts."""

        if call.result is None:
            return
        states = {n for n in X.canonical(call.result).walk() if isinstance(n, X.State)}
        for st in states:
            for e, op in self.writes:
                tgt = e.target
                if not isinstance(tgt, X.State) or tgt.instance != st.instance or tgt.name != st.name:
                    continue
                text = e.detail.get("text", "")
                for g in self._getters_in_write(e):
                    ext = self.entity_for(g.base, "ent:any")
                    if ext is None or ext == inst_ent:
                        continue
                    rid = f"rel:dep:{_slug(inst_ent)}->{_slug(ext)}"
                    ev = self.ev.site(e.site)
                    if rid in self.relations:
                        if ev and ev not in self.relations[rid].evidence:
                            self.relations[rid].evidence.append(ev)
                        continue
                    self.relations[rid] = Relation(
                        id=rid, kind="depends_on", source=inst_ent, target=ext,
                        label=f"{st.name} from {g.base.render()}.{g.name}()",
                        basis_detail=f"Reported value reads {st.name}, which is written from {g.render()} ({text})",
                        status=status("rule", rule="dependency.accounting"), evidence=[x for x in [ev] if x])

    def _getters_in_write(self, e: Effect) -> List[X.Getter]:
        node = e.site
        out = []
        f = e.frame
        for n in f.function.nodes:
            if n.source_mapping.lines and min(n.source_mapping.lines) == node.start_line:
                for ir in n.irs_ssa:
                    if ir.__class__.__name__ == "HighLevelCall":
                        v, _, _ = self.R.resolve_high_level(ir, f)
                        c = X.canonical(v)
                        if isinstance(c, X.Getter):
                            out.append(c)
        return out

    @staticmethod
    def _own_frames(tree: FrameNode):
        """The frame of an operation and its internal / library / modifier
        frames (not the frames of nested calls into other contracts)."""

        yield tree
        for it in tree.items:
            if isinstance(it, FrameNode) and it.kind in ("internal", "library", "modifier"):
                yield from Semantics._own_frames(it)

    def build_operational_dependencies(self) -> None:
        # state-changing calls to code outside the analysed scope
        for sub, e in self._ext_ops:
            src = self.operations[sub.parent].executor if sub.parent in self.operations else None
            tgt = sub.executor
            if not src or not tgt or src == tgt or tgt.startswith("ent:param:") or tgt not in self.entities:
                continue
            rid = f"rel:dep:{_slug(src)}->{_slug(tgt)}"
            ev = self.ev.site(e.site)
            if rid in self.relations:
                r = self.relations[rid]
                if ev and ev not in r.evidence:
                    r.evidence.append(ev)
                continue
            self.relations[rid] = Relation(
                id=rid, kind="depends_on", source=src, target=tgt, label=f"calls {e.function}() outside the analysed scope",
                basis_detail=f"{self.operations[sub.parent].label} calls {self.entities[tgt].label}.{e.function}(), whose "
                             "implementation (and effect on the caller's assets) is not analysed",
                status=status("rule", rule="dependency.external-call"), evidence=[x for x in [ev] if x])
        for op in self.operations.values():
            tree = self.op_tree.get(op.id)
            if tree is None:
                continue
            for g in [g for fr in self._own_frames(tree) for g in fr.conditions]:
                for n in X.canonical(g.expr).walk():
                    if isinstance(n, X.Getter):
                        if n.name in ("balanceOf", "totalSupply", "decimals", "allowance", "symbol", "name", "asset"):
                            continue  # reads of a token / share are not dependencies on another party
                        ext = self.entity_for(n.base, "ent:any")
                        if not ext or ext == op.executor or not op.executor or ext.startswith("ent:param:"):
                            continue
                        rid = f"rel:dep:{_slug(op.executor)}->{_slug(ext)}"
                        ev = self.ev.site(g.site)
                        if rid in self.relations:
                            r = self.relations[rid]
                            if ev and ev not in r.evidence:
                                r.evidence.append(ev)
                            continue
                        self.relations[rid] = Relation(
                            id=rid, kind="depends_on", source=op.executor, target=ext,
                            label=f"guard reads {X.abbrev(n, 2)}",
                            basis_detail=f"{op.label}: {g.describe()}",
                            status=status("rule", rule="dependency.guard"), evidence=[x for x in [ev] if x])

        # what a contract depends on, holders of a claim on its assets depend on too
        # (accounts holding the contract's share or ledger entry; amounts read from, or assets moved to, the party)
        deps = [r for r in self.relations.values() if r.kind == "depends_on" and r.source.startswith("ent:")
                and r.source[4:] in self.instances
                and r.status.rule in ("dependency.accounting", "dependency.external-call")]
        claims = [r for r in self.relations.values() if r.kind == "claim" and r.target in self.positions
                  and r.source in self.positions and self.positions[r.source].holder == "ent:any"]
        for d in deps:
            for c in claims:
                if self.positions[c.target].holder != d.source or self.assets[self.positions[c.source].asset].issuer != d.source:
                    continue
                rid = f"rel:dep:{_slug(c.source)}->{_slug(d.target)}"
                if rid in self.relations:
                    continue
                self.relations[rid] = Relation(
                    id=rid, kind="depends_on", source=c.source, target=d.target,
                    label=f"through {self.entities[d.source].label}",
                    basis_detail=(f"{self.positions[c.source].label} is a claim on {self.positions[c.target].label}, and "
                                  f"{self.entities[d.source].label} depends on {self.entities[d.target].label} ({d.label})"),
                    status=status("derived", rule="dependency.through-issuer"),
                    evidence=list(dict.fromkeys(c.evidence[:2] + d.evidence[:2])))

    def build_bindings(self) -> None:
        for b in self.cfg.bindings:
            src = f"ent:{b.instance}"
            tgt = f"ent:{b.target}" if b.target else self.entity_for(
                X.State(b.instance, self.instances[b.instance].contract.name, b.variable), "ent:any")
            inst = self.instances[b.instance]
            writers = []
            for f in inst.contract.functions:
                if f.visibility in ("public", "external") and not f.is_constructor and any(
                        v.name == b.variable for v in N.written(f)):
                    writers.append(f)
            conds = []
            for f in writers:
                er = next((er for er in self.entries if er.instance.id == b.instance and er.function is f), None)
                who = self.entities[er.caller].label if er else "?"
                site = self.site_of(f.source_mapping, f.contract_declarer, f)
                conds.append(Condition(id=f"rel:binding:{b.instance}.{b.variable}#w:{_slug(f.full_name)}", kind="state",
                                       text=f"{inst.contract.name}.{b.variable} can be changed by {who} via {f.full_name}",
                                       evidence=[x for x in [self.ev.site(site)] if x]))
            ev = self.ev.config(f"bind:{b.instance}.{b.variable}", f"Binding {inst.contract.name}.{b.variable}",
                                f"{inst.contract.name}.{b.variable} -> {self.entities[tgt].label} ({b.source})" + (f". {b.note}" if b.note else ""))
            rid = f"rel:binding:{b.instance}.{b.variable}"
            self.relations[rid] = Relation(
                id=rid, kind="binding", source=src, target=tgt, label=f"{b.variable} =",
                description=f"Configuration binds {inst.contract.name}.{b.variable} to {self.entities[tgt].label}" +
                            (f" ({b.note})" if b.note else ""),
                conditions=conds, status=status("config", basis="config", rule="binding.config"),
                evidence=[ev] + self._state_var_evidence(inst, b.variable))

    def build_missing_exits(self) -> None:
        for pid, op, who in getattr(self, "_open_triggers", []):
            p = self.positions[pid]
            uid = f"unr:open-trigger:{_slug(pid)}:{_slug(op.id[3:])}"
            self.unresolved[uid] = Unresolved(
                id=uid, kind="other",
                text=(f"{who} can trigger {op.label}, which pays out {self.entities[p.holder].label}'s "
                      f"{self.assets[p.asset].label}: check whether third parties may decide when this happens."),
                subjects=[pid, op.id], evidence=op.evidence[:2])
        exited = {x.position for x in self.exits.values()}
        for pid, p in self.positions.items():
            a = self.assets[p.asset]
            if a.kind == "share" and a.issuer and a.issuer[4:] in self.instances and pid not in exited:
                minted = any(f.flow.mechanism == "mint" and f.flow.asset == p.asset for f in self.flows)
                if minted:
                    uid = f"unr:missing-exit:{_slug(pid)}"
                    self.unresolved[uid] = Unresolved(id=uid, kind="missing_exit",
                                                      text=f"No entry point burns {a.label} held by {self.entities[p.holder].label} "
                                                           "and returns another asset.", subjects=[pid])

    def build_boundaries(self) -> None:
        for e in self.entities.values():
            if e.kind == "external_contract" and not e.in_scope:
                bid = f"bnd:{_slug(e.id[4:])}"
                iface = e.contract.name if e.contract else None
                self.boundaries[bid] = Boundary(
                    id=bid, target=e.label,
                    reason=(f"Only the interface {iface} is available; the implementation behind {e.expression} is not part of "
                            "the snapshot." if iface else f"No implementation for {e.expression} in the snapshot."),
                    evidence=e.evidence[:2])


def _basis(p: Flow) -> str:
    t = p.amount.expression or "?"
    if p.amount.note:
        return f"payout {p.amount.note}"
    return f"payout {t}"


def _guard_kind(g: Guard) -> str:
    if g.kind in ("branch",):
        return "branch"
    if g.kind == "loop":
        return "loop"
    c = X.canonical(g.expr)
    names = {n.name for n in c.walk() if isinstance(n, X.Ctx)}
    if _allowed_callers(g) or (g.kind in ("require", "modifier") and isinstance(c, X.Bin) and c.op == "=="
                               and any(isinstance(n, X.Inst) for n in (c.left, c.right))):
        return "access_control"
    if "block.timestamp" in names or "block.number" in names:
        return "timing"
    if any(isinstance(n, X.Getter) for n in c.walk()):
        return "external"
    if g.kind == "modifier":
        return "modifier"
    if any(isinstance(n, X.State) for n in c.walk()):
        return "state"
    return "require"


def _is_sender(e: X.Expr) -> bool:
    return isinstance(e, X.Ctx) and e.name == "msg.sender"


def _allowed_callers(g: Guard) -> Optional[List[X.Expr]]:
    """Allowed msg.sender values implied by a require / revert guard, if any."""

    c = X.canonical(g.expr)
    if g.kind in ("require", "modifier"):
        return _eq_set(c)
    if g.kind == "revert":
        # revert if cond  ==>  allowed when cond is false
        cond = c if not g.negated else X.Un("!", c)
        return _neq_set(cond)
    return None


def _eq_set(c: X.Expr) -> Optional[List[X.Expr]]:
    m = X.membership(c)
    if m is not None and _is_sender(m[1]):
        return [X.Members(m[0])]
    if isinstance(c, X.Bin):
        if c.op == "==":
            if _is_sender(c.left) and not _is_sender(c.right):
                return [c.right]
            if _is_sender(c.right) and not _is_sender(c.left):
                return [c.left]
        if c.op == "||":
            a, b = _eq_set(c.left), _eq_set(c.right)
            if a is not None and b is not None:
                return a + [x for x in b if x not in a]
        if c.op == "&&":
            return _eq_set(c.left) or _eq_set(c.right)
    return None


def _neq_set(c: X.Expr) -> Optional[List[X.Expr]]:
    if isinstance(c, X.Bin):
        if c.op == "!=":
            if _is_sender(c.left):
                return [c.right]
            if _is_sender(c.right):
                return [c.left]
        if c.op == "&&":
            a, b = _neq_set(c.left), _neq_set(c.right)
            if a is not None and b is not None:
                return a + [x for x in b if x not in a]
    if isinstance(c, X.Un) and c.op == "!":
        return _eq_set(c.expr)
    return None


def required(g: Guard) -> Optional[X.Expr]:
    """The predicate a require / modifier / revert guard enforces."""

    if g.kind in ("require", "modifier"):
        return X.canonical(g.expr)
    if g.kind == "revert":
        return X.canonical(g.expr) if g.negated else X.negate(g.expr)
    return None


def _evaluate(g: Guard, registries=None) -> Optional[bool]:
    """Evaluate decidable access checks in the call context: instance ==
    instance, and membership of an instance in a configured registry."""

    req = required(g)
    if req is None:
        return None
    return _truth(req, registries or {})


def _truth(c: X.Expr, regs) -> Optional[bool]:
    if isinstance(c, X.Bin) and c.op in ("&&", "||"):
        a, b = _truth(c.left, regs), _truth(c.right, regs)
        if c.op == "&&":
            return False if False in (a, b) else (True if a is True and b is True else None)
        return True if True in (a, b) else (False if a is False and b is False else None)
    if isinstance(c, X.Un) and c.op == "!":
        v = _truth(c.expr, regs)
        return None if v is None else not v
    m = X.membership(c)
    if m is not None:
        reg, value = m
        members = regs.get((reg.instance, reg.name))
        if isinstance(value, X.Inst) and members and value.instance in members:
            return True
        return None
    if isinstance(c, X.Bin) and c.op in ("==", "!=") and isinstance(c.left, X.Inst) and isinstance(c.right, X.Inst):
        same = c.left.instance == c.right.instance
        return same if c.op == "==" else not same
    return None


def _is_value_state(v) -> bool:
    """A state variable holding an amount, rate, time or hash (not an address, flag or role mapping)."""

    ty = str(v.type)
    if ty.startswith("mapping("):
        ty = ty.rsplit("=>", 1)[-1].strip(" )")
    return not (ty.startswith(("address", "bool", "contract ")) or ty in ("address", "bool")) and \
        not any(x in v.name.lower() for x in ("role", "admin", "owner", "paused", "guardian", "operator"))
