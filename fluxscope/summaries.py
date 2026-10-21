"""Function effect summaries with call context.

``summarize(frame)`` walks a function body (SlithIR SSA) and returns a tree:

* :class:`Effect` leaves — asset-relevant facts: token transfers, mints,
  burns, approvals, native transfers, external calls that leave the analysed
  instances, external reads and state writes;
* :class:`FrameNode` children — internal calls, library calls, calls into
  other configured instances (with ``msg.sender`` rebound) and adapter-level
  operations of external protocols (e.g. an ERC-4626 ``mint``).

Every node keeps its source site, the local path guards (branch / loop
conditions that dominate it) and the requires / reverts / modifiers that
constrain the frame. Interpretation of these facts happens later
(:mod:`fluxscope.semantics`); this module only records what the code does.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from slither.core.cfg.node import Node, NodeType
from slither.core.declarations import Function, Modifier, SolidityFunction
from slither.core.variables.state_variable import StateVariable
from slither.slithir.operations import (
    Assignment,
    Binary,
    BinaryType,
    Delete,
    EventCall,
    Index,
    HighLevelCall,
    InternalCall,
    LibraryCall,
    LowLevelCall,
    Send,
    SolidityCall,
    Transfer,
)
from slither.slithir.variables import StateIRVariable

from . import adapters
from .facts import exprs as X
from .facts import namespaced as N
from .facts.effects import Effect, FrameNode, Guard
from .facts.resolver import Frame, Instance, Resolver, Site

MAX_DEPTH = 10


def fn_name(frame: Frame) -> str:
    return frame.function.full_name


def _node_order(fn: Function) -> List[Node]:
    return sorted(fn.nodes, key=lambda n: (n.source_mapping.start or 0, n.node_id))


def _is_ledger_mapping(sv) -> bool:
    t = str(getattr(sv, "type", "")).replace(" ", "")
    return t.startswith("mapping(address=>uint") and t.count("mapping(") == 1


class Summarizer:
    def __init__(self, resolver: Resolver, dependencies: List[dict],
                 registries: Optional[Dict[Tuple[str, str], List[str]]] = None):
        self.R = resolver
        self.rules = adapters.Rules(resolver, dependencies)
        self._guards: Dict[int, Tuple[Guard, ...]] = {}
        self.registries = registries or {}
        # facts learned from required conditions (used later by the semantics):
        # key(address expr) -> configured member instance, when the code requires
        # the address to be in a configured registry with a single member ...
        self.member_candidates: Dict[str, str] = resolver.member_candidates
        self.member_evidence: Dict[str, Guard] = {}
        # ... and then calls it (only then is the address treated as that member)
        self.member_alias: Dict[str, str] = resolver.member_alias
        # key(parameter) -> expression it is required to equal
        self.eq_alias: Dict[str, X.Expr] = {}
        self.eq_evidence: Dict[str, Guard] = {}

    # ------------------------------------------------- learned conditions
    def _learn(self, g: Guard) -> None:
        if g.kind == "require":
            required = g.expr
        elif g.kind == "revert":
            required = g.expr if g.negated else X.negate(g.expr)
        else:
            return
        for c in X.conjuncts(required):
            m = X.membership(c)
            if m is not None:
                reg, value = m
                members = self.registries.get((reg.instance, reg.name))
                if members and len(members) == 1 and not (isinstance(value, X.Ctx)):
                    k = X.key(value)
                    self.member_candidates.setdefault(k, members[0])
                    self.member_evidence.setdefault(k, g)
                continue
            if isinstance(c, X.Bin) and c.op == "==":
                for a, b in ((c.left, c.right), (c.right, c.left)):
                    if isinstance(a, X.Param) and isinstance(b, (X.State, X.Inst, X.Getter)):
                        k = X.key(a)
                        self.eq_alias.setdefault(k, b)
                        self.eq_evidence.setdefault(k, g)
                        break

    # ------------------------------------------------------------ guards
    def node_text(self, node: Node) -> str:
        return str(node.expression) if node.expression is not None else (node.source_mapping.content or "").strip()

    def condition_expr(self, node: Node, frame: Frame) -> X.Expr:
        for ir in node.irs_ssa:
            if ir.__class__.__name__ == "Condition":
                return self.R.resolve(ir.value, frame)
        return X.Opaque(self.node_text(node))

    def local_guards(self, node: Node, frame: Frame) -> Tuple[Guard, ...]:
        out: List[Guard] = []
        doms = node.dominators or set()
        for d in sorted(doms, key=lambda n: (n.source_mapping.start or 0, n.node_id)):
            if d is node or d.type not in (NodeType.IF, NodeType.IFLOOP):
                continue
            st, sf = d.son_true, d.son_false
            text = self.node_text(d)
            if d.type == NodeType.IFLOOP:
                if st is not None and (st in doms or st is node):
                    out.append(Guard("loop", text, self.condition_expr(d, frame), False, self.R.site(d, frame.function)))
                continue
            if st is not None and (st in doms or st is node):
                out.append(Guard("branch", text, self.condition_expr(d, frame), False, self.R.site(d, frame.function)))
            elif sf is not None and sf.type != NodeType.ENDIF and (sf in doms or sf is node):
                out.append(Guard("branch", text, self.condition_expr(d, frame), True, self.R.site(d, frame.function)))
        return tuple(out)

    # ------------------------------------------------------------ frames
    def summarize_entry(self, inst: Instance, fn: Function) -> FrameNode:
        frame = self.R.entry_frame(inst, fn)
        node = FrameNode("entry", frame, self.R.site_of(fn.source_mapping, fn.contract_declarer, fn),
                         label=f"{inst.contract.name}.{fn.full_name}",
                         executor=X.Inst(inst.id, inst.label), initiator=frame.sender)
        self._fill(node, frame)
        return node

    def _fill(self, fnode: FrameNode, frame: Frame) -> None:
        fn = frame.function
        # Modifiers: their requires / reverts constrain the frame.
        if not isinstance(fn, Modifier):
            for m in fn.modifiers:
                margs = self._modifier_args(frame, fn, m)
                mframe = self.R.new_frame(frame.instance, m, margs, frame.sender, "modifier", frame, None)
                if not margs:
                    mframe.args = {}
                sub = FrameNode("modifier", mframe, self.R.site_of(m.source_mapping, m.contract_declarer, m), label=m.name)
                self._walk(sub, mframe, modifier=True)
                nested = [g for n in sub.walk() if n.kind in ("modifier", "internal") for g in n.conditions]
                for g in nested:
                    fnode.conditions.append(Guard("modifier" if g.kind == "require" else g.kind, g.text, g.expr,
                                                  g.negated, g.site))
                # effects inside modifiers (rare) are kept
                fnode.items.extend(it for it in sub.items if isinstance(it, Effect) and it.kind != "read")
        self._walk(fnode, frame)

    def _modifier_args(self, frame: Frame, fn, m) -> tuple:
        """Arguments of the modifier invocation in ``fn`` (``onlyRole(REWARDER_ROLE)``), resolved in the caller."""

        for st in getattr(fn, "modifiers_statements", None) or []:
            if getattr(st.modifier, "name", None) != m.name:
                continue
            for node in st.nodes:
                for ir in node.irs:
                    if isinstance(ir, InternalCall) and getattr(ir.function, "name", None) == m.name \
                            and len(ir.arguments) == len(m.parameters):
                        return tuple(self.R.resolve(a, frame) for a in ir.arguments)
        return ()

    def _walk(self, fnode: FrameNode, frame: Frame, modifier: bool = False) -> None:
        fn = frame.function
        refs: Dict[str, tuple] = {}
        for node in _node_order(fn):
            if node.type == NodeType.PLACEHOLDER:
                continue
            guards = self.local_guards(node, frame)
            site = self.R.site(node, fn)
            for ir in node.irs_ssa:
                self._ir(fnode, frame, node, ir, site, guards, modifier)
                self._ledger(fnode, frame, node, ir, site, guards, refs)
            # state writes (namespaced storage fields count as state variables)
            for sv in list(node.state_variables_written) + N.node_written(node):
                if isinstance(sv, (StateVariable, N.NsVar)):
                    fnode.items.append(Effect("write", site, frame, guards, target=X.State(
                        frame.instance.id, frame.instance.contract.name, sv.name, str(sv.type)),
                        detail={"text": self.node_text(node)}))

    def _ledger(self, fnode: FrameNode, frame: Frame, node: Node, ir, site: Site, guards, refs) -> None:
        """Writes to an address-keyed amount mapping (``m[account] += x``,
        ``m[account] = m[account] - x``, ``m[account] = 0``): candidate ledger
        entries, interpreted by the semantics."""

        R = self.R
        if isinstance(ir, Index):
            base = ir.variable_left
            sv = getattr(base, "non_ssa_version", base)
            if not isinstance(sv, StateVariable):
                sv = N.field_of(base, N.references(N._fn_irs(frame.function, True)))
            if sv is not None and _is_ledger_mapping(sv):
                refs[str(ir.lvalue)] = (sv, ir.variable_right)
            return
        lv = getattr(ir, "lvalue", None)
        if isinstance(ir, Delete):
            lv = ir.variable
        if lv is None or str(lv) not in refs or getattr(ir, "lvalue", None) is not None and isinstance(ir, Index):
            return
        sv, keyv = refs[str(lv)]
        mapping = X.State(frame.instance.id, frame.instance.contract.name, sv.name, str(sv.type))
        key = R.resolve(keyv, frame)
        cur = X.Index(mapping, key)
        delta, amount, shown = None, None, None
        if isinstance(ir, Binary) and ir.type in (BinaryType.ADDITION, BinaryType.SUBTRACTION):
            other = ir.variable_right if str(ir.variable_left) == str(lv) else ir.variable_left
            delta = "+" if ir.type == BinaryType.ADDITION else "-"
            amount = R.resolve(other, frame)
            shown = R.display(other, frame)
        elif isinstance(ir, Assignment):
            v = X.canonical(R.resolve(ir.rvalue, frame))
            opts = v.options if isinstance(v, X.Phi) else (v,)
            kinds = set()
            amts = []
            for o in opts:
                if isinstance(o, X.Const) and o.value in ("0", "0x0"):
                    kinds.add("-")
                    amts.append(cur)
                elif isinstance(o, X.Bin) and o.op in ("+", "-") and X.key(o.left) == X.key(cur):
                    kinds.add(o.op)
                    amts.append(o.right)
                else:
                    kinds.add("=")
                    amts.append(o)
            delta = kinds.pop() if len(kinds) == 1 else "="
            amount = X.phi(amts) if delta != "=" else v
            shown = amount.render() if delta != "=" else R.display(ir.rvalue, frame)
        elif isinstance(ir, Delete):
            delta, amount, shown = "-", cur, cur.render()
        if delta is None:
            return
        side = "src" if delta == "-" else "dst"
        fnode.items.append(Effect("ledger", site, frame, guards, token=mapping,
                                  src=key if delta == "-" else None, dst=key if delta != "-" else None,
                                  amount=amount, rule="structural.ledger", function=fn_name(frame),
                                  detail={"delta": delta, "mapping": sv.name, "text": self.node_text(node),
                                          "display": {"amount": shown, side: R.display(keyv, frame)}}))

    def _require(self, fnode: FrameNode, frame: Frame, node: Node, ir: SolidityCall, site: Site,
                 guards: Tuple[Guard, ...]) -> None:
        name = ir.function.name if isinstance(ir.function, SolidityFunction) else str(ir.function)
        if name.startswith(("require", "assert")):
            cond = ir.arguments[0] if ir.arguments else None
            expr = self.R.resolve(cond, frame) if cond is not None else X.Opaque("?")
            text = str(node.expression.arguments[0]) if hasattr(node.expression, "arguments") and node.expression.arguments else self.node_text(node)
            fnode.conditions.append(Guard("require", text, expr, False, site))
            self._learn(fnode.conditions[-1])
        elif name.startswith("revert"):
            branch = [g for g in guards if g.kind == "branch"]
            if branch:
                g = branch[-1]
                fnode.conditions.append(Guard("revert", g.text, g.expr, g.negated, site))
                self._learn(fnode.conditions[-1])

    def _ir(self, fnode: FrameNode, frame: Frame, node: Node, ir, site: Site, guards, modifier: bool) -> None:
        R = self.R
        if isinstance(ir, SolidityCall):
            self._require(fnode, frame, node, ir, site, guards)
            return
        if isinstance(ir, EventCall):
            return
        if isinstance(ir, LibraryCall):
            args = tuple(R.resolve(a, frame) for a in ir.arguments)
            hit = self.rules.library_call(frame, ir, args, site, guards)
            if hit is not None:
                self._annotate(hit, frame, ir.arguments, args)
                fnode.items.extend(hit)
                return
            fn = ir.function
            if isinstance(fn, Function) and fn.is_implemented and frame.depth < MAX_DEPTH and not frame.on_stack(frame.instance, fn):
                self._child(fnode, frame, frame.instance, fn, args, frame.sender, "library", site, guards)
            return
        if isinstance(ir, HighLevelCall):
            self._high_level(fnode, frame, node, ir, site, guards)
            return
        if isinstance(ir, InternalCall):
            fn = ir.function
            if isinstance(fn, Modifier) or getattr(ir, "is_modifier_call", False):
                return
            fn = R.contextual(frame.instance, fn)
            args = tuple(R.resolve(a, frame) for a in ir.arguments)
            hit = self.rules.internal_call(frame, ir, fn, args, site, guards)
            if hit is not None:
                self._annotate(hit, frame, ir.arguments, args)
                fnode.items.extend(hit)
                fnode.conditions.extend(self._implementation_conditions(frame, fn, args, site))
                return
            if isinstance(fn, Function) and fn.is_implemented:
                if frame.depth < MAX_DEPTH and not frame.on_stack(frame.instance, fn):
                    self._child(fnode, frame, frame.instance, fn, args, frame.sender, "internal", site, guards)
            elif isinstance(fn, Function):
                fnode.items.append(Effect("unresolved", site, frame, guards, function=fn.full_name,
                                          detail={"reason": "function has no implementation in scope"}))
            return
        if isinstance(ir, (Transfer, Send)):
            fnode.items.append(Effect("native", site, frame, guards, token=X.Const("ETH", "native"),
                                      src=X.Inst(frame.instance.id, frame.instance.label),
                                      dst=R.resolve(ir.destination, frame),
                                      amount=R.resolve(ir.call_value, frame), rule="native.transfer",
                                      rule_note="Solidity transfer/send of native currency"))
            return
        if isinstance(ir, LowLevelCall):
            dest = R.resolve(ir.destination, frame)
            if ir.call_value is not None:
                fnode.items.append(Effect("native", site, frame, guards, token=X.Const("ETH", "native"),
                                          src=X.Inst(frame.instance.id, frame.instance.label), dst=dest,
                                          amount=R.resolve(ir.call_value, frame), rule="native.call",
                                          rule_note="low-level call with value"))
            else:
                fnode.items.append(Effect("ext_call", site, frame, guards, target=dest, function=ir.function_name,
                                          detail={"low_level": True}))
            return

    def _high_level(self, fnode: FrameNode, frame: Frame, node: Node, ir: HighLevelCall, site: Site, guards) -> None:
        R = self.R
        dest = R.resolve(ir.destination, frame)
        args = tuple(R.resolve(a, frame) for a in ir.arguments)
        ret = R.resolve(ir.lvalue, frame) if ir.lvalue is not None else None
        inst = R.bound_instance(dest)
        via_registry = self.member_candidates.get(X.key(X.canonical(dest))) if inst is not None and not isinstance(
            X.canonical(dest), X.Inst) else None
        if inst is not None:
            fn, sv = R.lookup(inst, ir)
            if fn is not None:
                if frame.depth < MAX_DEPTH and not frame.on_stack(inst, fn):
                    child = self._child(fnode, frame, inst, fn, args, X.Inst(frame.instance.id, frame.instance.label),
                                        "external", site, guards)
                    child.label = f"{inst.contract.name}.{fn.full_name}"
                    if via_registry is not None:
                        g = self.member_evidence[X.key(X.canonical(dest))]
                        child.detail["registry"] = {"member": via_registry, "target": dest.render(), "guard": g}
                return
            if sv is not None:
                return  # public getter read
        disp = [R.display(a, frame) for a in ir.arguments]
        hit = self.rules.external_call(frame, ir, dest, args, ret, site, guards, disp)
        if hit is not None:
            self._annotate(hit, frame, ir.arguments, args)
            fnode.items.extend(hit)
            return
        fn = ir.function
        view = bool(getattr(fn, "view", False) or getattr(fn, "pure", False) or isinstance(fn, StateVariable))
        iface = getattr(getattr(fn, "contract", None), "name", None)
        fnode.items.append(Effect("read" if view else "ext_call", site, frame, guards, target=dest,
                                  function=ir.function_name, args=args,
                                  detail={"interface": iface, "display_args": disp,
                                          "value": R.resolve(ir.call_value, frame) if ir.call_value else None}))
        if ir.call_value is not None and not view:
            fnode.items.append(Effect("native", site, frame, guards, token=X.Const("ETH", "native"),
                                      src=X.Inst(frame.instance.id, frame.instance.label), dst=dest,
                                      amount=R.resolve(ir.call_value, frame), rule="native.call",
                                      rule_note=f"native value sent with {ir.function_name}()"))

    def _implementation_conditions(self, frame: Frame, fn: Function, args, site) -> List[Guard]:
        """Requires inside a rule-matched implementation (e.g. OpenZeppelin
        ``_burn``: balance must cover the amount) still constrain the caller."""

        if frame.depth >= MAX_DEPTH or frame.on_stack(frame.instance, fn):
            return []
        sub_frame = self.R.new_frame(frame.instance, fn, args, frame.sender, "internal", frame, site)
        tmp = FrameNode("internal", sub_frame, site)
        self._fill(tmp, sub_frame)
        out = []
        for n in tmp.walk():
            for g in n.conditions:
                if g.kind in ("require", "revert"):
                    out.append(Guard(g.kind, f"{g.text} (in {fn.contract_declarer.name}.{fn.name})", g.expr, g.negated, g.site))
        return out

    def _annotate(self, hits, frame: Frame, ir_args, args) -> None:
        """Attach call-site display text (variable names, shallow definitions)."""

        effects = []
        for h in hits:
            if isinstance(h, FrameNode):
                effects.extend(e for e in h.items if isinstance(e, Effect))
            else:
                effects.append(h)
        for e in effects:
            for attr in ("amount", "src", "dst", "token"):
                val = getattr(e, attr)
                if val is None:
                    continue
                for v, a in zip(ir_args, args):
                    if a is val:
                        e.detail.setdefault("display", {})[attr] = self.R.display(v, frame)
                        d = self.R.definition(v, frame)
                        if d:
                            e.detail.setdefault("definition", {})[attr] = d
                        break

    def _child(self, fnode: FrameNode, frame: Frame, inst: Instance, fn: Function, args, sender, kind: str,
               site: Site, guards) -> FrameNode:
        sub_frame = self.R.new_frame(inst, fn, args, sender, kind, frame, site)
        child = FrameNode(kind, sub_frame, site, guards, label=f"{fn.name}",
                          executor=X.Inst(inst.id, inst.label), initiator=sender)
        self._fill(child, sub_frame)
        fnode.items.append(child)
        return child
