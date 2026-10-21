"""Resolve SlithIR (SSA) values to symbolic expressions in a call context.

A :class:`Frame` is one activation of a function: which configured contract
instance executes it, the actual arguments (already expressed in terms of the
entry frame), and who ``msg.sender`` is. Internal calls keep ``msg.sender``;
calls into another configured instance rebind it to the calling instance.
"""

from __future__ import annotations

import itertools
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from slither.core.cfg.node import Node, NodeType
from slither.core.declarations import Contract, Function, SolidityFunction, SolidityVariable
from slither.core.variables.state_variable import StateVariable
from slither.slithir.operations import (
    Assignment,
    Binary,
    Condition,
    HighLevelCall,
    Index,
    InternalCall,
    InternalDynamicCall,
    Length,
    LibraryCall,
    LowLevelCall,
    Member,
    NewContract,
    Phi,
    PhiCallback,
    Return,
    SolidityCall,
    TypeConversion,
    Unary,
    Unpack,
)
from slither.core.variables.local_variable import LocalVariable
from slither.slithir.variables import Constant, LocalIRVariable, StateIRVariable

from . import exprs as X
from . import namespaced as N

_frame_ids = itertools.count(1)
MAX_DEPTH = 12


@dataclass
class Instance:
    id: str
    contract: Contract
    label: str


@dataclass
class Site:
    """A source location (the evidence for a fact)."""

    file: str
    start_line: int
    end_line: int
    contract: str
    function: str
    snippet: str

    def key(self) -> str:
        return f"{self.file}:{self.start_line}-{self.end_line}"


@dataclass(eq=False)
class Frame:
    instance: Instance
    function: Function
    args: Dict[str, X.Expr]
    sender: X.Expr
    kind: str  # entry | internal | external | library | modifier
    parent: Optional["Frame"] = None
    callsite: Optional[Site] = None
    depth: int = 0
    id: int = field(default_factory=lambda: next(_frame_ids))

    def chain(self) -> List["Frame"]:
        out, f = [], self
        while f is not None:
            out.append(f)
            f = f.parent
        return list(reversed(out))

    def on_stack(self, instance: Instance, function: Function) -> bool:
        f = self
        while f is not None:
            if f.instance.id == instance.id and f.function is function:
                return True
            f = f.parent
        return False

    @property
    def name(self) -> str:
        return f"{self.instance.contract.name}.{self.function.name}"


def ssa_key(v) -> str:
    return getattr(v, "ssa_name", None) or str(getattr(v, "name", v))


class Resolver:
    def __init__(self, project_root: str, instances: Dict[str, Instance],
                 bindings: Dict[Tuple[str, str], X.Expr]):
        self.root = os.path.realpath(project_root).rstrip("/") + "/"
        self.instances = instances
        self.bindings = bindings
        self._defs: Dict[int, Dict[str, Tuple[object, Node]]] = {}
        self._memo: Dict[Tuple[int, str], X.Expr] = {}
        self._returns: Dict[tuple, X.Expr] = {}
        self._active: set = set()
        self._ctx_fn: Dict[Tuple[int, int], Function] = {}
        # registry members (filled by the summarizer from required membership checks)
        self.member_candidates: Dict[str, str] = {}
        self.member_alias: Dict[str, str] = {}

    # ------------------------------------------------------------------ sites
    def site(self, node: Node, function: Optional[Function] = None) -> Site:
        sm = node.source_mapping
        fn = function or node.function
        return self.site_of(sm, getattr(fn, "contract_declarer", None) or getattr(fn, "contract", None), fn)

    def site_of(self, sm, contract, fn) -> Site:
        path = os.path.realpath(sm.filename.absolute)
        if path.startswith(self.root):
            path = path[len(self.root):]
        lines = sm.lines or [0]
        snippet = sm.content or ""
        return Site(
            file=path,
            start_line=min(lines),
            end_line=max(lines),
            contract=getattr(contract, "name", "") or "",
            function=getattr(fn, "full_name", "") or "",
            snippet=snippet.strip(),
        )

    # ------------------------------------------------------------- def maps
    def defs(self, fn: Function) -> Dict[str, Tuple[object, Node]]:
        d = self._defs.get(id(fn))
        if d is None:
            d = {}
            for node in fn.nodes:
                for ir in node.irs_ssa:
                    lv = getattr(ir, "lvalue", None)
                    if lv is not None:
                        d[ssa_key(lv)] = (ir, node)
            self._defs[id(fn)] = d
        return d

    def contextual(self, inst: Instance, fn) -> object:
        """The copy of ``fn`` that belongs to the instance's contract.

        Slither builds the IR of an inherited function once per contract, with
        internal calls to virtual functions bound in that contract. A call that
        names the declaring contract's copy (``super.f()`` in a derived
        contract) would otherwise dispatch callees such as
        ``_beforeTokenTransfer`` to the base implementation instead of the
        instance's override."""

        if not isinstance(fn, Function) or getattr(fn, "contract", None) is inst.contract:
            return fn
        key = (id(inst.contract), id(fn))
        hit = self._ctx_fn.get(key)
        if hit is None:
            hit = fn
            decl = getattr(fn, "contract_declarer", None)
            for f in inst.contract.functions:
                if f.full_name == fn.full_name and getattr(f, "contract_declarer", None) is decl:
                    hit = f
                    break
            self._ctx_fn[key] = hit
        return hit

    # ---------------------------------------------------------------- state
    def state(self, inst: Instance, sv: StateVariable) -> X.Expr:
        b = self.bindings.get((inst.id, sv.name))
        if b is not None:
            return b
        if getattr(sv, "is_constant", False) and sv.expression is not None:
            return X.Const(str(sv.expression), str(sv.type))
        return X.State(inst.id, inst.contract.name, sv.name, str(sv.type))

    # ---------------------------------------------------------------- values
    def resolve(self, v, frame: Frame) -> X.Expr:
        if v is None:
            return X.Opaque("?")
        if isinstance(v, Constant):
            return X.Const(str(v.value), str(v.type))
        if isinstance(v, SolidityVariable):
            if v.name == "msg.sender":
                return frame.sender
            if v.name == "this":
                return X.Inst(frame.instance.id, frame.instance.label)
            return X.Ctx(v.name)
        if isinstance(v, StateIRVariable):
            return self.state(frame.instance, v.non_ssa_version)
        if isinstance(v, StateVariable):
            return self.state(frame.instance, v)
        if isinstance(v, Contract):
            return X.Opaque(v.name)
        if isinstance(v, Function):
            return X.Opaque(v.name)
        k = ssa_key(v)
        mk = (frame.id, k)
        if mk in self._memo:
            return self._memo[mk]
        if mk in self._active:
            return X.Opaque(getattr(v, "name", k))
        self._active.add(mk)
        try:
            out = X.bound(self._resolve_var(v, k, frame))
        finally:
            self._active.discard(mk)
        self._memo[mk] = out
        return out

    def _param(self, name: str, frame: Frame) -> Optional[X.Expr]:
        if name in frame.args:
            return frame.args[name]
        return None

    def _resolve_var(self, v, k: str, frame: Frame) -> X.Expr:
        base = getattr(v, "non_ssa_version", None)
        name = getattr(base, "name", None) or getattr(v, "name", k)
        params = {p.name for p in frame.function.parameters}
        d = self.defs(frame.function).get(k)
        if d is None:
            if name in params:
                return self._param(name, frame) or X.Opaque(name)
            return X.Opaque(name)
        ir, node = d
        if isinstance(ir, Phi) and node.type == NodeType.ENTRYPOINT and name in params:
            return self._param(name, frame) or X.Opaque(name)
        return self.resolve_ir(ir, node, frame)

    def resolve_ir(self, ir, node: Node, frame: Frame) -> X.Expr:
        r = self.resolve
        if isinstance(ir, Assignment):
            return r(ir.rvalue, frame)
        if isinstance(ir, TypeConversion):
            return r(ir.variable, frame)
        if isinstance(ir, Binary):
            return X.Bin(ir.type.value, r(ir.variable_left, frame), r(ir.variable_right, frame))
        if isinstance(ir, Unary):
            return X.Un(ir.type.value, r(ir.rvalue, frame))
        if isinstance(ir, (Phi, PhiCallback)):
            lv = ir.lvalue
            if isinstance(lv, StateIRVariable):
                return self.state(frame.instance, lv.non_ssa_version)
            opts = []
            for rv in ir.rvalues:
                d = self.defs(frame.function).get(ssa_key(rv))
                if d is None:
                    continue
                opts.append(r(rv, frame))
            return X.phi(opts) if opts else X.Opaque(lv.name)
        if isinstance(ir, LibraryCall):
            args = tuple(r(a, frame) for a in ir.arguments)
            fn = ir.function
            if getattr(ir.destination, "name", "") == "EnumerableSet" and ir.function_name == "contains" and len(args) == 2:
                return X.InSet(args[0], args[1])
            if isinstance(fn, Function) and fn.is_implemented:
                res = self.call_return(frame, frame.instance, fn, args, frame.sender, "library")
                return X.Call(f"{ir.destination.name}.{fn.name}", args, res)
            return X.Call(f"{ir.destination.name}.{ir.function_name}", args, None)
        if isinstance(ir, HighLevelCall):
            return self.resolve_high_level(ir, frame)[0]
        if isinstance(ir, InternalCall):
            fn = self.contextual(frame.instance, ir.function)
            args = tuple(r(a, frame) for a in ir.arguments)
            ns = N.getter_struct(fn)
            if ns:
                return X.State(frame.instance.id, frame.instance.contract.name, N.NS_PREFIX + ns, ns)
            if isinstance(fn, Function) and fn.is_implemented:
                res = self.call_return(frame, frame.instance, fn, args, frame.sender, "internal")
                return X.Call(fn.name, args, res)
            return X.Call(getattr(fn, "name", "?"), args, None)
        if isinstance(ir, SolidityCall):
            fname = ir.function.name if isinstance(ir.function, SolidityFunction) else str(ir.function)
            args = tuple(r(a, frame) for a in ir.arguments)
            if fname.startswith("balance(address)"):
                return X.Getter(args[0], "balance", ())
            return X.Call(fname.split("(")[0], args, None)
        if isinstance(ir, Member):
            base = r(ir.variable_left, frame)
            if isinstance(base, X.State) and base.name.startswith(N.NS_PREFIX):
                # a field of a namespaced storage struct is a state variable of the contract
                return X.State(base.instance, base.contract, str(ir.variable_right), str(ir.lvalue.type))
            return X.Member(base, str(ir.variable_right))
        if isinstance(ir, Index):
            return X.Index(r(ir.variable_left, frame), r(ir.variable_right, frame))
        if isinstance(ir, Length):
            return X.Member(r(ir.value, frame), "length")
        if isinstance(ir, Unpack):
            return X.Member(r(ir.tuple, frame), f"[{ir.index}]")
        if isinstance(ir, NewContract):
            return X.Opaque(f"new {ir.contract_name}")
        if isinstance(ir, (LowLevelCall, InternalDynamicCall)):
            return X.Opaque(str(ir.lvalue))
        return X.Opaque(str(getattr(ir, "lvalue", "?")))

    def bound_instance(self, e: X.Expr) -> Optional[Instance]:
        """The configured instance a call target denotes: an instance address,
        or an address the code requires to be in a registry whose configured
        member is that instance."""

        c = X.canonical(e)
        if isinstance(c, X.Inst):
            return self.instances.get(c.instance)
        k = X.key(c)
        if k in self.member_candidates:
            self.member_alias[k] = self.member_candidates[k]
            return self.instances.get(self.member_candidates[k])
        return None

    def resolve_high_level(self, ir: HighLevelCall, frame: Frame):
        """Returns (value expression, callee instance or None, callee function or None)."""

        r = self.resolve
        dest = r(ir.destination, frame)
        args = tuple(r(a, frame) for a in ir.arguments)
        inst = self.bound_instance(dest)
        name = ir.function_name
        if inst is not None:
            fn, sv = self.lookup(inst, ir)
            label = f"{X._r(dest)}.{name}"
            if sv is not None:
                return self.state(inst, sv), inst, None
            if fn is not None:
                res = self.call_return(frame, inst, fn, args, X.Inst(frame.instance.id, frame.instance.label), "external")
                return X.Call(label, args, res, inst.id), inst, fn
        return X.Getter(dest, name, args), None, None

    def lookup(self, inst: Instance, ir: HighLevelCall):
        fn = ir.function
        sig = getattr(fn, "solidity_signature", None)
        target = None
        if sig:
            target = inst.contract.get_function_from_signature(sig)
        if target is None:
            for f in inst.contract.functions:
                if f.name == ir.function_name and f.is_implemented and f.visibility in ("public", "external"):
                    target = f
                    break
        if target is not None and target.is_implemented:
            return target, None
        sv = inst.contract.get_state_variable_from_name(ir.function_name)
        if sv is not None and sv.visibility == "public":
            return None, sv
        return None, None

    # -------------------------------------------------------------- display
    def display(self, v, frame: Frame) -> str:
        """How an auditor would write the value at the call site."""

        if isinstance(v, (LocalIRVariable, LocalVariable)):
            base = getattr(v, "non_ssa_version", None) or v
            if base.name:
                return base.name
        if isinstance(v, StateIRVariable):
            return v.non_ssa_version.name
        if isinstance(v, StateVariable):
            return v.name
        return self.shallow(v, frame).render()

    def definition(self, v, frame: Frame) -> Optional[str]:
        """Shallow definition of a named local (one level of local variables)."""

        if isinstance(v, (LocalIRVariable, LocalVariable)):
            base = getattr(v, "non_ssa_version", None) or v
            params = {p.name for p in frame.function.parameters}
            if base.name in params:
                return None
            d = self.shallow(v, frame).render()
            return d if d != base.name else None
        return None

    def shallow(self, v, frame: Frame, top: bool = True, depth: int = 0) -> X.Expr:
        if depth > 8:
            return X.Opaque(str(getattr(v, "name", v)))
        if isinstance(v, Constant):
            return X.Const(str(v.value), str(v.type))
        if isinstance(v, SolidityVariable):
            return self.resolve(v, frame) if v.name != "msg.sender" or frame.kind == "entry" else X.Ctx("msg.sender")
        if isinstance(v, StateIRVariable):
            return X.Opaque(v.non_ssa_version.name)
        if isinstance(v, (StateVariable, Contract, Function)):
            return X.Opaque(v.name)
        base = getattr(v, "non_ssa_version", None)
        name = getattr(base, "name", None) or getattr(v, "name", "?")
        if isinstance(v, LocalIRVariable) and not top:
            return X.Opaque(name)
        d = self.defs(frame.function).get(ssa_key(v))
        if d is None:
            return X.Opaque(name)
        ir, node = d
        sh = lambda x, t=False: self.shallow(x, frame, t, depth + 1)  # noqa: E731
        if isinstance(ir, Phi):
            if node.type == NodeType.ENTRYPOINT:
                return X.Opaque(name)
            opts = [sh(rv, top) for rv in ir.rvalues if self.defs(frame.function).get(ssa_key(rv)) is not None]
            return X.phi(opts) if opts else X.Opaque(name)
        if isinstance(ir, Assignment):
            return sh(ir.rvalue)
        if isinstance(ir, TypeConversion):
            return sh(ir.variable, top)
        if isinstance(ir, Binary):
            return X.Bin(ir.type.value, sh(ir.variable_left), sh(ir.variable_right))
        if isinstance(ir, Unary):
            return X.Un(ir.type.value, sh(ir.rvalue))
        if isinstance(ir, LibraryCall):
            return X.Call(f"{ir.destination.name}.{ir.function_name}", tuple(sh(a) for a in ir.arguments))
        if isinstance(ir, HighLevelCall):
            return X.Getter(sh(ir.destination), ir.function_name, tuple(sh(a) for a in ir.arguments))
        if isinstance(ir, InternalCall):
            return X.Call(getattr(ir.function, "name", "?"), tuple(sh(a) for a in ir.arguments))
        if isinstance(ir, Member):
            return X.Member(sh(ir.variable_left), str(ir.variable_right))
        if isinstance(ir, Index):
            return X.Index(sh(ir.variable_left), sh(ir.variable_right))
        if isinstance(ir, SolidityCall):
            fname = ir.function.name if isinstance(ir.function, SolidityFunction) else str(ir.function)
            return X.Call(fname.split("(")[0], tuple(sh(a) for a in ir.arguments))
        return X.Opaque(name)

    # ------------------------------------------------------------- returns
    def call_return(self, caller: Frame, inst: Instance, fn: Function, args: Tuple[X.Expr, ...],
                    sender: X.Expr, kind: str) -> Optional[X.Expr]:
        if caller.depth >= MAX_DEPTH or caller.on_stack(inst, fn):
            return None
        # ERC-2771 meta-transactions: _msgSender() is the relayed original sender when a trusted forwarder
        # calls, otherwise msg.sender; for access control both stand for the account that initiated the call
        if fn.name == "_msgSender" and fn.contract_declarer.name.startswith("ERC2771Context"):
            return sender
        mk = (inst.id, id(fn), tuple(X.key(a) for a in args), X.key(sender))
        if mk in self._returns:
            return self._returns[mk]
        self._returns[mk] = None  # recursion guard
        frame = self.new_frame(inst, fn, args, sender, kind, caller, None)
        values: List[X.Expr] = []
        for node in fn.nodes:
            for ir in node.irs_ssa:
                if isinstance(ir, Return):
                    if ir.values:
                        values.append(self.resolve(ir.values[0], frame))
        if not values and fn.returns:
            rv = fn.returns[0]
            last = None
            for node in fn.nodes:
                for ir in node.irs_ssa:
                    lv = getattr(ir, "lvalue", None)
                    if lv is not None and getattr(getattr(lv, "non_ssa_version", None), "name", None) == rv.name:
                        last = (ir, node)
            if last:
                values.append(self.resolve_ir(last[0], last[1], frame))
        out = X.bound(X.phi(values)) if values else None
        self._returns[mk] = out
        return out

    def new_frame(self, inst: Instance, fn: Function, args: Tuple[X.Expr, ...], sender: X.Expr,
                  kind: str, parent: Optional[Frame], callsite: Optional[Site]) -> Frame:
        amap = {}
        for p, a in zip(fn.parameters, args):
            if p.name:
                amap[p.name] = a
        return Frame(inst, fn, amap, sender, kind, parent, callsite, (parent.depth + 1) if parent else 0)

    def entry_frame(self, inst: Instance, fn: Function) -> Frame:
        args = {p.name: X.Param(inst.id, fn.full_name, p.name) for p in fn.parameters if p.name}
        return Frame(inst, fn, args, X.Ctx("msg.sender"), "entry")
