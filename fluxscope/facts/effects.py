"""Data structures for function effect summaries (see :mod:`fluxscope.summaries`)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

from . import exprs as X
from .resolver import Frame, Site


@dataclass
class Guard:
    kind: str  # branch | loop | require | revert | modifier
    text: str
    expr: X.Expr
    negated: bool
    site: Site

    def describe(self) -> str:
        if self.kind == "loop":
            return f"repeated while {self.text}"
        if self.kind == "branch":
            return f"only if not ({self.text})" if self.negated else f"only if {self.text}"
        if self.kind == "revert":
            return f"reverts if {self.text}"
        return f"requires {self.text}"


@dataclass
class Effect:
    kind: str  # transfer | mint | burn | approve | native | ext_call | read | write | unresolved
    site: Site
    frame: Frame
    guards: Tuple[Guard, ...] = ()
    token: Optional[X.Expr] = None
    src: Optional[X.Expr] = None
    dst: Optional[X.Expr] = None
    amount: Optional[X.Expr] = None
    rule: Optional[str] = None
    rule_note: Optional[str] = None
    target: Optional[X.Expr] = None
    function: Optional[str] = None
    args: Tuple[X.Expr, ...] = ()
    detail: Dict[str, object] = field(default_factory=dict)

    @property
    def moves_asset(self) -> bool:
        return self.kind in ("transfer", "mint", "burn", "native")


@dataclass
class FrameNode:
    kind: str  # entry | internal | library | external | adapter
    frame: Optional[Frame]
    site: Optional[Site]
    guards: Tuple[Guard, ...] = ()
    items: List[Union["FrameNode", Effect]] = field(default_factory=list)
    conditions: List[Guard] = field(default_factory=list)
    label: str = ""
    executor: Optional[X.Expr] = None  # who executes (instance / external contract)
    initiator: Optional[X.Expr] = None  # who calls it
    adapter: Optional[str] = None
    adapter_note: Optional[str] = None
    detail: Dict[str, object] = field(default_factory=dict)

    def walk(self):
        yield self
        for it in self.items:
            if isinstance(it, FrameNode):
                yield from it.walk()

    def effects(self):
        for it in self.items:
            if isinstance(it, FrameNode):
                yield from it.effects()
            else:
                yield it

    @property
    def has_asset_effects(self) -> bool:
        return any(e.moves_asset for e in self.effects())
