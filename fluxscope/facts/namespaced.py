"""Namespaced storage (ERC-7201 and similar layouts).

OpenZeppelin 5 upgradeable contracts (and many others) keep their state in a
struct placed at a fixed storage slot::

    function _getERC20Storage() private pure returns (ERC20Storage storage $) {
        assembly { $.slot := ERC20StorageLocation }
    }
    ...
    ERC20Storage storage $ = _getERC20Storage();
    $._balances[from] = fromBalance - value;

Slither sees no state variable in such code. A field of a namespaced struct
is a state variable of the contract in every respect that matters here, so it
is represented as :class:`NsVar` (``name`` = field name, ``type`` = field
type) wherever the analysis looks at state variables: ledgers, writes, reads,
access-control state and expressions (``X.State``).
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set

from slither.core.declarations import Function, Structure
from slither.core.solidity_types import UserDefinedType
from slither.slithir.operations import Assignment, Binary, Delete, Index, InternalCall, Member, Phi, Unary
from slither.slithir.variables import ReferenceVariable

NS_PREFIX = "ns:"


class NsVar:
    """A field of a namespaced storage struct, used where a ``StateVariable`` is expected."""

    def __init__(self, struct: str, name: str, type_) -> None:
        self.struct = struct
        self.name = name
        self.type = type_
        self.visibility = "internal"
        self.is_constant = False
        self.expression = None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"NsVar({self.struct}.{self.name})"


_GETTER: Dict[int, Optional[str]] = {}


def getter_struct(fn) -> Optional[str]:
    """Struct name if ``fn`` returns a pointer to a namespaced storage struct: an internal function without
    parameters whose single return value is a storage struct set in inline assembly."""

    if not isinstance(fn, Function):
        return None
    k = id(fn)
    if k in _GETTER:
        return _GETTER[k]
    out = None
    rets = fn.returns
    if fn.visibility in ("internal", "private") and not fn.parameters and len(rets) == 1 \
            and rets[0].location == "storage" and isinstance(rets[0].type, UserDefinedType) \
            and isinstance(rets[0].type.type, Structure) and fn.contains_assembly:
        out = rets[0].type.type.name
    _GETTER[k] = out
    return out


def _pointer_names(irs: Iterable) -> Dict[str, str]:
    """Variables (temporaries and storage-pointer locals, SSA versions included) that hold a namespaced
    struct pointer -> struct name."""

    ptr: Dict[str, str] = {}
    irs = list(irs)
    for ir in irs:
        if isinstance(ir, InternalCall) and ir.lvalue is not None:
            s = getter_struct(ir.function)
            if s:
                ptr[str(ir.lvalue)] = s
    changed = True
    while changed:
        changed = False
        for ir in irs:
            if isinstance(ir, Assignment) and str(ir.rvalue) in ptr and str(ir.lvalue) not in ptr:
                ptr[str(ir.lvalue)] = ptr[str(ir.rvalue)]
                base = getattr(ir.lvalue, "non_ssa_version", None)
                if base is not None:
                    ptr[f"name:{base.name}"] = ptr[str(ir.rvalue)]
                changed = True
            elif isinstance(ir, Phi) and str(ir.lvalue) not in ptr and any(str(v) in ptr for v in ir.rvalues):
                ptr[str(ir.lvalue)] = next(ptr[str(v)] for v in ir.rvalues if str(v) in ptr)
                changed = True
    return ptr


def _is_ptr(v, ptr: Dict[str, str]) -> Optional[str]:
    if str(v) in ptr:
        return ptr[str(v)]
    base = getattr(v, "non_ssa_version", None)
    name = getattr(base, "name", None) or getattr(v, "name", None)
    return ptr.get(f"name:{name}") if name else None


def references(irs: Iterable) -> Dict[str, NsVar]:
    """Reference variables that point into a namespaced field (the field itself, an index into it, a member
    of an indexed struct) -> the field."""

    irs = list(irs)
    ptr = _pointer_names(irs)
    if not ptr:
        return {}
    refs: Dict[str, NsVar] = {}
    for ir in irs:
        if isinstance(ir, Member):
            s = _is_ptr(ir.variable_left, ptr)
            if s:
                refs[str(ir.lvalue)] = NsVar(s, str(ir.variable_right), ir.lvalue.type)
            elif str(ir.variable_left) in refs:
                refs[str(ir.lvalue)] = refs[str(ir.variable_left)]
        elif isinstance(ir, Index) and str(ir.variable_left) in refs:
            refs[str(ir.lvalue)] = refs[str(ir.variable_left)]
    return refs


def field_of(v, refs: Dict[str, NsVar]) -> Optional[NsVar]:
    return refs.get(str(v))


def node_written(node, ssa: bool = False) -> List[NsVar]:
    irs = node.irs_ssa if ssa else node.irs
    refs = references(_fn_irs(node.function, ssa))
    out: Dict[str, NsVar] = {}
    for ir in irs:
        lv = ir.variable if isinstance(ir, Delete) else getattr(ir, "lvalue", None)
        if lv is None or not isinstance(lv, ReferenceVariable) or str(lv) not in refs:
            continue
        if isinstance(ir, (Assignment, Binary, Unary, Delete)):
            f = refs[str(lv)]
            out[f.name] = f
    return list(out.values())


_FN_IRS: Dict[tuple, list] = {}


def _fn_irs(fn, ssa: bool) -> list:
    k = (id(fn), ssa)
    if k not in _FN_IRS:
        _FN_IRS[k] = [ir for n in fn.nodes for ir in (n.irs_ssa if ssa else n.irs)]
    return _FN_IRS[k]


def _callees(fn) -> List[Function]:
    seen: Set[int] = set()
    out: List[Function] = []
    todo = [fn]
    while todo:
        f = todo.pop()
        if id(f) in seen or not isinstance(f, Function):
            continue
        seen.add(id(f))
        out.append(f)
        for c in list(f.internal_calls) + list(f.modifiers):
            g = getattr(c, "function", c)
            if isinstance(g, Function):
                todo.append(g)
    return out


_WRITTEN: Dict[int, List[NsVar]] = {}
_READ: Dict[int, List[NsVar]] = {}


def fields_written(fn) -> List[NsVar]:
    """Namespaced fields written by ``fn`` or the internal functions and modifiers it calls."""

    if id(fn) not in _WRITTEN:
        out: Dict[str, NsVar] = {}
        for f in _callees(fn):
            for n in f.nodes:
                for v in node_written(n):
                    out[v.name] = v
        _WRITTEN[id(fn)] = list(out.values())
    return _WRITTEN[id(fn)]


def fields_read(fn) -> List[NsVar]:
    """Namespaced fields accessed by ``fn`` or its internal callees."""

    if id(fn) not in _READ:
        out: Dict[str, NsVar] = {}
        for f in _callees(fn):
            for v in references(_fn_irs(f, False)).values():
                out[v.name] = v
        _READ[id(fn)] = list(out.values())
    return _READ[id(fn)]


def written(fn) -> list:
    """``all_state_variables_written`` plus namespaced fields."""

    return list(fn.all_state_variables_written()) + fields_written(fn)


def read(fn) -> list:
    """``all_state_variables_read`` plus namespaced fields."""

    return list(fn.all_state_variables_read()) + fields_read(fn)
