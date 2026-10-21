"""Symbolic expressions over program values.

Expressions are produced by :mod:`fluxscope.facts.resolver` from SlithIR and are
always expressed in terms of the *entry frame* of an analysed call: parameters
of the public entry point, the root ``msg.sender``, configured contract
instances and their state variables. They serve two purposes:

* identity: deciding which asset / account an address expression denotes
  (``canonical`` strips call wrappers down to the resolved value);
* display: showing amounts and conditions the way an auditor reads them
  (``render`` keeps calls such as ``balance()`` folded).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Iterator, Optional, Tuple

# Expressions larger than this (tree nodes, shared sub-terms counted per use)
# are summarised by ``bound``: nested protocol code (e.g. a strategy's
# ``estimatedTotalAssets()`` used inside several branches) otherwise makes
# amount expressions grow exponentially.
MAX_NODES = 250


class Expr:
    __slots__ = ()

    def children(self) -> Tuple["Expr", ...]:
        return ()

    def walk(self) -> Iterator["Expr"]:
        yield self
        for c in self.children():
            yield from c.walk()

    def render(self) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def __str__(self) -> str:
        return self.render()


@dataclass(frozen=True)
class Const(Expr):
    value: str
    type: str = ""

    def render(self) -> str:
        if self.type == "address" and self.value in ("0", "0x0"):
            return "address(0)"
        return self.value


@dataclass(frozen=True)
class Ctx(Expr):
    """Transaction context: the root ``msg.sender``, ``tx.origin``, ``block.*``."""

    name: str

    def render(self) -> str:
        return self.name


@dataclass(frozen=True)
class Inst(Expr):
    """A configured contract instance (``address(this)`` inside it)."""

    instance: str
    label: str = field(default="", compare=False)

    def render(self) -> str:
        return f"address({self.label or self.instance})"


@dataclass(frozen=True)
class Param(Expr):
    """A parameter of the root entry point."""

    instance: str
    function: str
    name: str

    def render(self) -> str:
        return self.name


@dataclass(frozen=True)
class State(Expr):
    instance: str
    contract: str
    name: str
    type: str = field(default="", compare=False)

    def render(self) -> str:
        return self.name

    def qualified(self) -> str:
        return f"{self.contract}.{self.name}"


@dataclass(frozen=True)
class Getter(Expr):
    """Result of a call to code outside the analysed instances."""

    base: Expr
    name: str
    args: Tuple[Expr, ...] = ()

    def children(self) -> Tuple[Expr, ...]:
        return (self.base,) + self.args

    def render(self) -> str:
        return f"{_r(self.base)}.{self.name}({', '.join(a.render() for a in self.args)})"


@dataclass(frozen=True)
class Call(Expr):
    """Result of a call whose body was analysed. ``result`` is the resolved
    return value (None when it could not be resolved)."""

    name: str
    args: Tuple[Expr, ...] = ()
    result: Optional[Expr] = field(default=None, compare=False)
    instance: str = field(default="", compare=False)  # set for calls into another configured instance

    def children(self) -> Tuple[Expr, ...]:
        return self.args + ((self.result,) if self.result is not None else ())

    def render(self) -> str:
        return f"{self.name}({', '.join(a.render() for a in self.args)})"


_PREC = {"||": 1, "&&": 2, "==": 3, "!=": 3, "<": 4, ">": 4, "<=": 4, ">=": 4,
         "|": 5, "^": 6, "&": 7, "<<": 8, ">>": 8, "+": 9, "-": 9, "*": 10, "/": 10, "%": 10, "**": 11}


@dataclass(frozen=True)
class Bin(Expr):
    op: str
    left: Expr
    right: Expr

    def children(self) -> Tuple[Expr, ...]:
        return (self.left, self.right)

    def render(self) -> str:
        p = _PREC.get(self.op, 0)

        def side(e: Expr, right: bool) -> str:
            s = e.render()
            if isinstance(e, Bin):
                q = _PREC.get(e.op, 0)
                if q < p or (right and q == p and self.op in ("-", "/", "%")):
                    return f"({s})"
            return s

        return f"{side(self.left, False)} {self.op} {side(self.right, True)}"


@dataclass(frozen=True)
class Un(Expr):
    op: str
    expr: Expr

    def children(self) -> Tuple[Expr, ...]:
        return (self.expr,)

    def render(self) -> str:
        inner = self.expr.render()
        if isinstance(self.expr, Bin):
            inner = f"({inner})"
        return f"{self.op}{inner}"


@dataclass(frozen=True)
class Phi(Expr):
    """One of several values (merge of branches)."""

    options: Tuple[Expr, ...]

    def children(self) -> Tuple[Expr, ...]:
        return self.options

    def render(self) -> str:
        return " | ".join(o.render() for o in self.options)


@dataclass(frozen=True)
class Member(Expr):
    base: Expr
    member: str

    def children(self) -> Tuple[Expr, ...]:
        return (self.base,)

    def render(self) -> str:
        return f"{_r(self.base)}.{self.member}"


@dataclass(frozen=True)
class Index(Expr):
    base: Expr
    index: Expr

    def children(self) -> Tuple[Expr, ...]:
        return (self.base, self.index)

    def render(self) -> str:
        return f"{_r(self.base)}[{self.index.render()}]"


@dataclass(frozen=True)
class InSet(Expr):
    """Membership test ``set.contains(value)`` on an address set
    (OpenZeppelin ``EnumerableSet``)."""

    set: Expr
    value: Expr

    def children(self) -> Tuple[Expr, ...]:
        return (self.set, self.value)

    def render(self) -> str:
        return f"{_r(self.set)}.contains({self.value.render()})"


@dataclass(frozen=True)
class Members(Expr):
    """Any address that is a member of an address registry (an address set,
    a ``mapping(address => bool)`` or an address array)."""

    registry: Expr

    def children(self) -> Tuple[Expr, ...]:
        return (self.registry,)

    def render(self) -> str:
        return f"member of {self.registry.render()}"


@dataclass(frozen=True)
class Opaque(Expr):
    text: str

    def render(self) -> str:
        return self.text


@dataclass(frozen=True)
class Big(Expr):
    """Summary of an expression above ``MAX_NODES``: an abbreviated rendering
    plus a structural digest that keeps distinct expressions distinct."""

    text: str
    digest: str

    def render(self) -> str:
        return self.text


def _memo(e: Expr, name: str, fn):
    d = getattr(e, "__dict__", None)
    if d is None:
        return fn(e)
    v = d.get(name)
    if v is None:
        v = fn(e)
        object.__setattr__(e, name, v)
    return v


def size(e: Expr) -> int:
    """Tree size, saturating just above ``MAX_NODES``."""

    def f(x):
        n = 1
        for c in x.children():
            n += size(c)
            if n > MAX_NODES:
                return MAX_NODES + 1
        return n

    return _memo(e, "_size", f)


def digest(e: Expr) -> str:
    def f(x):
        h = hashlib.sha1(type(x).__name__.encode())
        for k, v in sorted(getattr(x, "__dict__", {}).items()):
            if k.startswith("_"):
                continue
            if isinstance(v, Expr):
                h.update(digest(v).encode())
            elif isinstance(v, tuple):
                h.update(b"(" + ",".join(digest(i) if isinstance(i, Expr) else str(i) for i in v).encode() + b")")
            else:
                h.update(f"{k}={v}".encode())
        return h.hexdigest()

    return _memo(e, "_digest", f)


def abbrev(e: Expr, depth: int = 3) -> str:
    """Rendering limited to ``depth`` levels (deeper terms become '…')."""

    if depth <= 0 and e.children():
        return "…"
    d = depth - 1
    if isinstance(e, Call):
        return f"{e.name}({', '.join(abbrev(a, d) for a in e.args)})"
    if isinstance(e, Getter):
        return f"{abbrev(e.base, d)}.{e.name}({', '.join(abbrev(a, d) for a in e.args)})"
    if isinstance(e, Bin):
        return f"({abbrev(e.left, d)} {e.op} {abbrev(e.right, d)})"
    if isinstance(e, Un):
        return f"{e.op}{abbrev(e.expr, d)}"
    if isinstance(e, Phi):
        opts = [abbrev(o, d) for o in e.options[:3]]
        return " | ".join(opts + (["…"] if len(e.options) > 3 else []))
    if isinstance(e, Member):
        return f"{abbrev(e.base, d)}.{e.member}"
    if isinstance(e, Index):
        return f"{abbrev(e.base, d)}[{abbrev(e.index, d)}]"
    return e.render()


def bound(e: Optional[Expr], limit: int = MAX_NODES) -> Optional[Expr]:
    """Summarise oversized expressions, keeping the outer structure that the
    semantics relies on (external getters, negation, struct members) and
    summarising only their oversized operands."""

    if e is None or size(e) <= limit:
        return e
    small = max(limit // 4, 8)
    if isinstance(e, Getter):
        g = Getter(bound(e.base, small), e.name, tuple(bound(a, small) for a in e.args))
        if size(g) <= limit:
            return g
    if isinstance(e, Un):
        return Un(e.op, bound(e.expr, limit - 1))
    if isinstance(e, Member):
        return Member(bound(e.base, limit - 1), e.member)
    return Big(abbrev(e), digest(canonical(e)))


def _r(e: Expr) -> str:
    if isinstance(e, Inst):
        return e.label or e.instance
    if isinstance(e, Bin):
        return f"({e.render()})"
    return e.render()


def phi(options) -> Expr:
    flat = []
    for o in options:
        if isinstance(o, Phi):
            flat.extend(o.options)
        else:
            flat.append(o)
    uniq = {}
    for o in flat:
        uniq.setdefault(key(o), o)
    if len(uniq) == 1:
        return next(iter(uniq.values()))
    # canonical order: SlithIR phi operands come in unstable order
    return Phi(tuple(uniq[k] for k in sorted(uniq)))


def canonical(e: Expr) -> Expr:
    """Strip folded calls down to their resolved values (for identity)."""

    return _memo(e, "_canon", _canonical)


def _canonical(e: Expr) -> Expr:
    if isinstance(e, Call):
        return canonical(e.result) if e.result is not None else e
    if isinstance(e, Phi):
        return phi(canonical(o) for o in e.options)
    if isinstance(e, Getter):
        return Getter(canonical(e.base), e.name, tuple(canonical(a) for a in e.args))
    if isinstance(e, Bin):
        return Bin(e.op, canonical(e.left), canonical(e.right))
    if isinstance(e, Un):
        return Un(e.op, canonical(e.expr))
    if isinstance(e, Member):
        return Member(canonical(e.base), e.member)
    if isinstance(e, Index):
        return Index(canonical(e.base), canonical(e.index))
    if isinstance(e, InSet):
        return InSet(canonical(e.set), canonical(e.value))
    return e


def key(e: Expr) -> str:
    """Stable textual identity of a canonical expression."""

    return _memo(e, "_key", lambda x: _key(canonical(x)))


def _key(e: Expr) -> str:
    if isinstance(e, Big):
        return f"big:{e.digest}"
    if isinstance(e, Inst):
        return f"inst:{e.instance}"
    if isinstance(e, State):
        return f"state:{e.instance}.{e.name}"
    if isinstance(e, Param):
        return f"param:{e.instance}.{e.function}.{e.name}"
    if isinstance(e, Ctx):
        return f"ctx:{e.name}"
    if isinstance(e, Const):
        return f"const:{e.value}"
    if isinstance(e, Getter):
        return f"get:{key(e.base)}.{e.name}({','.join(key(a) for a in e.args)})"
    if isinstance(e, Phi):
        return "phi:{" + "|".join(sorted(key(o) for o in e.options)) + "}"
    if isinstance(e, Member):
        return f"mem:{key(e.base)}.{e.member}"
    if isinstance(e, Index):
        return f"idx:{key(e.base)}[{key(e.index)}]"
    if isinstance(e, Bin):
        return f"bin:({key(e.left)}{e.op}{key(e.right)})"
    if isinstance(e, Un):
        return f"un:{e.op}{key(e.expr)}"
    if isinstance(e, Call):
        return f"call:{e.name}({','.join(key(a) for a in e.args)})"
    if isinstance(e, InSet):
        return f"inset:{key(e.set)}[{key(e.value)}]"
    if isinstance(e, Members):
        return f"members:{key(e.registry)}"
    return f"opaque:{e.render()}"


def membership(e: Expr) -> Optional[Tuple["State", Expr]]:
    """(registry state variable, tested value) if ``e`` is a membership test:
    ``set.contains(v)`` on a state address set or ``m[v]`` on a state
    ``mapping(address => bool)``."""

    c = canonical(e)
    if isinstance(c, InSet) and isinstance(c.set, State):
        return c.set, c.value
    if isinstance(c, Index) and isinstance(c.base, State) and c.base.type.replace(" ", "") == "mapping(address=>bool)":
        return c.base, c.index
    # authorisation flag kept as a number (``require(wards[msg.sender] == 1)``)
    if isinstance(c, Bin) and c.op == "==":
        for a, b in ((c.left, c.right), (c.right, c.left)):
            if isinstance(a, Index) and isinstance(a.base, State) and isinstance(b, Const) \
                    and a.base.type.replace(" ", "").startswith("mapping(address=>uint") \
                    and a.base.type.count("mapping(") == 1 and b.value not in ("0", "0x0"):
                return State(a.base.instance, a.base.contract, a.base.name, "mapping(address=>bool)"), a.index
    # role membership kept in a struct (OpenZeppelin AccessControl: _roles[role].members[account] /
    # _roles[role].hasRole[account]): the registry is the member mapping of that role
    if isinstance(c, Index) and isinstance(c.base, Member) and isinstance(c.base.base, Index) \
            and isinstance(c.base.base.base, State) and c.base.member in ("members", "hasRole"):
        s = c.base.base.base
        return State(s.instance, s.contract, f"{s.name}[{c.base.base.index.render()}].{c.base.member}",
                     "mapping(address=>bool)"), c.index
    return None


def negate(e: Expr) -> Expr:
    """Logical negation with De Morgan and comparison flipping."""

    c = canonical(e)
    if isinstance(c, Un) and c.op == "!":
        return c.expr
    if isinstance(c, Bin):
        flip = {"==": "!=", "!=": "==", "<": ">=", ">=": "<", ">": "<=", "<=": ">"}
        if c.op in flip:
            return Bin(flip[c.op], c.left, c.right)
        if c.op == "&&":
            return Bin("||", negate(c.left), negate(c.right))
        if c.op == "||":
            return Bin("&&", negate(c.left), negate(c.right))
    return Un("!", c)


def conjuncts(e: Expr) -> Iterator[Expr]:
    c = canonical(e)
    if isinstance(c, Bin) and c.op == "&&":
        yield from conjuncts(c.left)
        yield from conjuncts(c.right)
    else:
        yield c


def mentions(e: Expr, pred) -> bool:
    return any(pred(x) for x in canonical(e).walk()) or any(pred(x) for x in e.walk())
