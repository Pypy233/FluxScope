"""FluxScope analysis-result data model (schema ``fluxscope/analysis`` v0.1).

This module is the single source of truth for the interchange format between
analyzers (static analysis, execution observation, hand-written examples) and
the interactive viewer. ``python -m fluxscope schema`` regenerates the JSON
schema under ``schema/`` from these models.

Design rules encoded here:

* Every amount carries the asset it is denominated in (``Amount.asset``) and a
  unit; numeric values are optional and stored as strings so that large
  integers survive JSON round-trips. Amounts of different assets are never
  summed by the viewer.
* A share and the underlying it represents are distinct assets and distinct
  positions linked by a ``claim`` relation; the underlying is held (custody) by
  the issuing system, not by the share holder, so it is never double counted.
* A borrowed asset (an inflow to the borrower) and the debt it creates (a
  ``debt`` position) are represented separately.
* Every claim the viewer shows points to evidence (source locations,
  transactions, logs, configuration, standards or model output) and carries a
  ``Status`` that says whether it is a static candidate, an observed execution
  or example data, and what supports it.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_ID = "fluxscope/analysis"
SCHEMA_VERSION = "0.1.0"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------


class Basis(str, Enum):
    """Where a fact comes from. ``static`` facts are candidate behaviour that
    the code supports; ``observed`` facts were seen in a concrete execution."""

    example = "example"
    static = "static"
    observed = "observed"
    config = "config"


class Support(str, Enum):
    """What produced the interpretation."""

    rule = "rule"  # generic structural rule over program facts
    adapter = "adapter"  # standard / protocol adapter (ERC-20, ERC-4626, ...)
    llm = "llm"  # proposed by a language model
    config = "config"  # supplied by the project configuration / deployment
    manual = "manual"  # written by hand (examples, annotations)
    derived = "derived"  # computed from other results (paths, aggregation)


class Verification(str, Enum):
    supported = "supported"  # all checks that apply passed
    partially_supported = "partially_supported"  # some checks pass, some unknown
    pending = "pending"  # proposed, not yet checked / confirmed
    contradicted = "contradicted"  # a check failed
    unresolved = "unresolved"  # depends on something outside the analysis scope


class CheckResult(str, Enum):
    pass_ = "pass"
    fail = "fail"
    unknown = "unknown"


class Check(_Model):
    id: str
    description: str
    result: CheckResult
    detail: Optional[str] = None
    evidence: List[str] = Field(default_factory=list, description="Evidence ids")


class Status(_Model):
    basis: Basis
    support: Support
    verification: Verification
    rule: Optional[str] = Field(None, description="Rule or adapter id that produced this item")
    checks: List[Check] = Field(default_factory=list)
    note: Optional[str] = None


class Amount(_Model):
    """A quantity of exactly one asset. ``value`` / ``raw`` may be unknown."""

    asset: str = Field(..., description="Asset id the amount is denominated in")
    unit: Literal["raw", "decimal", "unknown"] = Field(
        "unknown", description="raw = integer base units, decimal = decimal-adjusted token units"
    )
    value: Optional[str] = Field(None, description="Numeric value as a string; null when unknown")
    expression: Optional[str] = Field(None, description="Symbolic expression, e.g. a source expression")
    note: Optional[str] = None


class ConditionKind(str, Enum):
    require = "require"
    modifier = "modifier"
    branch = "branch"
    access_control = "access_control"
    state = "state"
    external = "external"
    loop = "loop"
    timing = "timing"
    assumption = "assumption"
    other = "other"


class Condition(_Model):
    id: str
    kind: ConditionKind
    text: str = Field(..., description="Human-readable condition")
    expression: Optional[str] = Field(None, description="Source expression, if any")
    negated: bool = Field(False, description="True if the guarded step runs when the expression is false")
    evidence: List[str] = Field(default_factory=list)
    status: Optional[Status] = None


# ---------------------------------------------------------------------------
# Scope and provenance
# ---------------------------------------------------------------------------


class ScopeKind(str, Enum):
    example = "example"
    static_analysis = "static_analysis"
    execution_observation = "execution_observation"
    mixed = "mixed"


class ProjectInfo(_Model):
    name: str
    repository: Optional[str] = None
    commit: Optional[str] = None
    version: Optional[str] = None
    entry_contracts: List[str] = Field(default_factory=list)
    files: List[str] = Field(default_factory=list, description="Files in the analysed snapshot")
    snapshot_sha256: Optional[str] = Field(None, description="Hash over the analysed files")


class ChainInfo(_Model):
    chain_id: Optional[int] = None
    name: Optional[str] = None
    block: Optional[int] = None


class Component(_Model):
    name: str
    version: Optional[str] = None
    role: str = Field(..., description="e.g. compiler, static-analysis, adapter, llm")
    detail: Optional[Dict[str, Any]] = None


class Boundary(_Model):
    """A place where the analysis stops (e.g. only an interface is available)."""

    id: str
    target: str
    reason: str
    evidence: List[str] = Field(default_factory=list)


class Assumption(_Model):
    id: str
    text: str
    source: Literal["config", "standard", "user", "example"]
    evidence: List[str] = Field(default_factory=list)


class Scope(_Model):
    kind: ScopeKind
    project: Optional[ProjectInfo] = None
    chain: Optional[ChainInfo] = None
    build: Optional[Dict[str, Any]] = None
    components: List[Component] = Field(default_factory=list)
    boundaries: List[Boundary] = Field(default_factory=list)
    assumptions: List[Assumption] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class Generator(_Model):
    name: str
    version: str


class Meta(_Model):
    id: str
    title: str
    description: Optional[str] = None
    synthetic: bool = Field(..., description="True for example / synthetic data")
    created_at: Optional[str] = None
    generator: Optional[Generator] = None
    tags: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Graph objects
# ---------------------------------------------------------------------------


class AssetKind(str, Enum):
    token = "token"
    share = "share"
    lp_token = "lp_token"
    debt_token = "debt_token"
    native = "native"
    accounting = "accounting"  # non-transferable internal balance / claim
    nft_position = "nft_position"
    unknown = "unknown"


class AssetIdentity(_Model):
    """How the asset is identified. Display names are never used for identity."""

    kind: Literal["address", "scoped_expression", "contract_self", "native", "example"]
    chain_id: Optional[int] = None
    address: Optional[str] = None
    expression: Optional[str] = Field(None, description="e.g. StrategyERC4626.want")
    contract: Optional[str] = None
    note: Optional[str] = None


class Asset(_Model):
    id: str
    label: str
    symbol: Optional[str] = Field(None, description="Display only; never used to relate assets")
    kind: AssetKind
    standard: Optional[str] = None
    identity: AssetIdentity
    issuer: Optional[str] = Field(None, description="Entity id that issues / accounts for this asset")
    decimals: Optional[int] = None
    description: Optional[str] = None
    status: Status
    evidence: List[str] = Field(default_factory=list)


class EntityKind(str, Enum):
    account = "account"  # externally owned account or any caller
    role = "role"  # address bound to a role (owner, keeper, receiver argument, ...)
    contract = "contract"  # analysed contract instance
    protocol = "protocol"  # grouping of contracts
    external_contract = "external_contract"  # outside analysis scope
    unknown = "unknown"


class ContractRef(_Model):
    name: str
    file: Optional[str] = None
    address: Optional[str] = None
    chain_id: Optional[int] = None


class Entity(_Model):
    id: str
    label: str
    kind: EntityKind
    description: Optional[str] = None
    parent: Optional[str] = Field(None, description="Grouping entity id (e.g. protocol)")
    contract: Optional[ContractRef] = None
    expression: Optional[str] = Field(None, description="Address expression this entity stands for")
    in_scope: bool = True
    status: Optional[Status] = None
    evidence: List[str] = Field(default_factory=list)


class OperationKind(str, Enum):
    transfer = "transfer"
    swap = "swap"
    deposit = "deposit"
    withdraw = "withdraw"
    mint = "mint"
    burn = "burn"
    redeem = "redeem"
    borrow = "borrow"
    repay = "repay"
    add_liquidity = "add_liquidity"
    remove_liquidity = "remove_liquidity"
    stake = "stake"
    unstake = "unstake"
    allocate = "allocate"
    deallocate = "deallocate"
    harvest = "harvest"
    claim = "claim"
    approve = "approve"
    liquidate = "liquidate"
    fee = "fee"
    call = "call"
    other = "other"


class Flow(_Model):
    """One asset movement inside an operation. ``from_``/``to`` may be null for
    mint (no payer) and burn (no recipient)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, use_enum_values=True)

    id: str
    asset: str
    from_: Optional[str] = Field(None, alias="from", description="Payer entity id; null for mint")
    to: Optional[str] = Field(None, description="Recipient entity id; null for burn")
    amount: Amount
    mechanism: Optional[str] = Field(None, description="transfer, transferFrom, mint, burn, native, adapter ...")
    conditions: List[str] = Field(default_factory=list, description="Condition ids of the parent operation")
    status: Optional[Status] = None
    evidence: List[str] = Field(default_factory=list)


class Participant(_Model):
    role: str = Field(..., description="caller, payer, receiver, owner, share_receiver, keeper, ...")
    entity: str
    expression: Optional[str] = None
    evidence: List[str] = Field(default_factory=list)


class EntryPoint(_Model):
    contract: str
    function: str
    signature: Optional[str] = None
    visibility: Optional[str] = None


class Operation(_Model):
    id: str
    kind: OperationKind
    label: str
    description: Optional[str] = None
    parent: Optional[str] = Field(None, description="Enclosing operation id")
    order: Optional[int] = Field(None, description="Position within the parent operation")
    transaction: Optional[Literal["same", "separate"]] = Field(
        None, description="Relation to the parent: executed in the same transaction or a separate one"
    )
    entrypoint: Optional[EntryPoint] = None
    initiator: Optional[str] = Field(None, description="Entity that calls / triggers the operation")
    executor: Optional[str] = Field(None, description="Contract entity that executes it")
    participants: List[Participant] = Field(default_factory=list)
    inputs: List[Flow] = Field(default_factory=list)
    outputs: List[Flow] = Field(default_factory=list)
    conditions: List[Condition] = Field(default_factory=list)
    status: Status
    evidence: List[str] = Field(default_factory=list)
    unresolved: List[str] = Field(default_factory=list)


class PositionKind(str, Enum):
    share = "share"  # holds shares / receipt tokens of a system
    custody = "custody"  # holds the asset itself
    lp = "lp"
    debt = "debt"  # owes the asset
    collateral = "collateral"
    claim = "claim"  # non-tokenized claim (internal accounting)
    reward = "reward"


class Position(_Model):
    id: str
    holder: str = Field(..., description="Entity id")
    asset: str = Field(..., description="Asset id")
    kind: PositionKind
    label: Optional[str] = None
    description: Optional[str] = None
    amount: Optional[Amount] = None
    counterparty: Optional[str] = Field(None, description="For debt / collateral: the entity owed to")
    status: Status
    evidence: List[str] = Field(default_factory=list)


class RelationKind(str, Enum):
    asset_flow = "asset_flow"  # asset moves between nodes
    holds = "holds"  # entity holds a position
    claim = "claim"  # a position represents a claim on another position
    depends_on = "depends_on"  # value / redemption depends on another node
    exit = "exit"  # position can be exited through an operation
    debt = "debt"  # holder owes counterparty
    collateral = "collateral"  # position backs a debt
    binding = "binding"  # configuration binding (state variable -> contract)
    calls = "calls"  # cross-contract call edge
    identity = "identity"  # two expressions denote the same asset / entity


class Relation(_Model):
    id: str
    kind: RelationKind
    source: str = Field(..., description="Node id (entity, asset, position or operation)")
    target: str
    label: Optional[str] = None
    description: Optional[str] = None
    asset: Optional[str] = None
    amount: Optional[Amount] = None
    operation: Optional[str] = None
    basis_detail: Optional[str] = Field(None, description="e.g. 'pro-rata of Vault.balance()'")
    conditions: List[Condition] = Field(default_factory=list)
    status: Status
    evidence: List[str] = Field(default_factory=list)


class ExitStep(_Model):
    order: int
    operation: str = Field(..., description="Operation id")
    actor: Optional[str] = Field(None, description="Entity that must perform / trigger the step")
    description: Optional[str] = None
    transaction: Optional[Literal["same", "separate"]] = None
    optional: bool = Field(False, description="Step only runs under a branch condition")
    conditions: List[Condition] = Field(default_factory=list)
    evidence: List[str] = Field(default_factory=list)


class ExitPath(_Model):
    id: str
    position: str = Field(..., description="Position id that is exited")
    label: str
    steps: List[ExitStep]
    consumes: List[Flow] = Field(default_factory=list)
    returns: List[Flow] = Field(default_factory=list)
    conditions: List[Condition] = Field(default_factory=list)
    status: Status
    evidence: List[str] = Field(default_factory=list)
    unresolved: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


class EvidenceKind(str, Enum):
    source = "source"
    transaction = "transaction"
    log = "log"
    trace = "trace"
    config = "config"
    standard = "standard"
    rule = "rule"
    llm = "llm"


class SourceLocation(_Model):
    file: str
    contract: Optional[str] = None
    function: Optional[str] = None
    start_line: int
    end_line: int
    snippet: Optional[str] = None


class ExecutionRef(_Model):
    chain_id: Optional[int] = None
    tx_hash: Optional[str] = None
    block: Optional[int] = None
    log_index: Optional[int] = None
    call_path: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


class Evidence(_Model):
    id: str
    kind: EvidenceKind
    title: str
    description: Optional[str] = None
    synthetic: bool = False
    source: Optional[SourceLocation] = None
    execution: Optional[ExecutionRef] = None
    reference: Optional[str] = Field(None, description="URL or document reference (standards, configs)")
    llm: Optional[Dict[str, Any]] = Field(None, description="model, prompt_version, call_id")


class SourceFile(_Model):
    path: str
    sha256: Optional[str] = None
    language: str = "solidity"
    synthetic: bool = False
    content: str


class UnresolvedKind(str, Enum):
    external_implementation = "external_implementation"
    unbound_address = "unbound_address"
    identity = "identity"
    missing_exit = "missing_exit"
    ambiguous_semantics = "ambiguous_semantics"
    dynamic_dispatch = "dynamic_dispatch"
    llm_question = "llm_question"
    other = "other"


class Unresolved(_Model):
    id: str
    kind: UnresolvedKind
    text: str
    subjects: List[str] = Field(default_factory=list, description="Ids of affected nodes")
    evidence: List[str] = Field(default_factory=list)
    suggestion: Optional[str] = None


class Task(_Model):
    id: str
    question: str
    description: Optional[str] = None
    focus: List[str] = Field(default_factory=list, description="Node ids to select / highlight")
    view: Optional[str] = Field(None, description="Suggested view: flows, holdings, dependencies, all")
    answer: Optional[str] = Field(None, description="Expected answer, for checking the interaction")


class Provenance(_Model):
    """Pipeline record so a wrong relation can be traced to its stage."""

    stages: List[Dict[str, Any]] = Field(default_factory=list)
    llm_calls: List[Dict[str, Any]] = Field(default_factory=list)


class AnalysisResult(_Model):
    schema_: Literal["fluxscope/analysis"] = Field(SCHEMA_ID, alias="schema")
    schema_version: Literal["0.1.0"] = SCHEMA_VERSION
    meta: Meta
    scope: Scope
    assets: List[Asset] = Field(default_factory=list)
    entities: List[Entity] = Field(default_factory=list)
    operations: List[Operation] = Field(default_factory=list)
    positions: List[Position] = Field(default_factory=list)
    relations: List[Relation] = Field(default_factory=list)
    exits: List[ExitPath] = Field(default_factory=list)
    evidence: List[Evidence] = Field(default_factory=list)
    sources: List[SourceFile] = Field(default_factory=list)
    unresolved: List[Unresolved] = Field(default_factory=list)
    tasks: List[Task] = Field(default_factory=list)
    provenance: Optional[Provenance] = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True, use_enum_values=True)

    def to_json_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


def json_schema() -> Dict[str, Any]:
    schema = AnalysisResult.model_json_schema(by_alias=True, mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"https://fluxscope.dev/schema/analysis-{SCHEMA_VERSION}.json"
    schema["title"] = "FluxScope analysis result"
    return schema


# ---------------------------------------------------------------------------
# Referential / semantic validation beyond the JSON schema
# ---------------------------------------------------------------------------


def semantic_errors(result: AnalysisResult) -> List[str]:
    """Checks that a JSON schema cannot express: dangling ids and the
    accounting rules from the module docstring."""

    errors: List[str] = []
    assets = {a.id for a in result.assets}
    entities = {e.id for e in result.entities}
    ops = {o.id for o in result.operations}
    positions = {p.id for p in result.positions}
    evidence = {e.id for e in result.evidence}
    unresolved = {u.id for u in result.unresolved}
    nodes = assets | entities | ops | positions

    all_ids: List[str] = []
    for coll in (result.assets, result.entities, result.operations, result.positions,
                 result.relations, result.exits, result.evidence, result.unresolved):
        all_ids.extend(x.id for x in coll)
    seen = set()
    for i in all_ids:
        if i in seen:
            errors.append(f"duplicate id '{i}'")
        seen.add(i)

    def ev(where: str, ids: List[str]) -> None:
        for i in ids:
            if i not in evidence:
                errors.append(f"{where}: unknown evidence '{i}'")

    def amount(where: str, a: Optional[Amount]) -> None:
        if a is not None and a.asset not in assets:
            errors.append(f"{where}: amount uses unknown asset '{a.asset}'")

    for a in result.assets:
        if a.issuer and a.issuer not in entities:
            errors.append(f"asset {a.id}: unknown issuer '{a.issuer}'")
        ev(f"asset {a.id}", a.evidence)
    for e in result.entities:
        if e.parent and e.parent not in entities:
            errors.append(f"entity {e.id}: unknown parent '{e.parent}'")
        ev(f"entity {e.id}", e.evidence)
    for o in result.operations:
        if o.parent and o.parent not in ops:
            errors.append(f"operation {o.id}: unknown parent '{o.parent}'")
        for ref in (o.initiator, o.executor):
            if ref and ref not in entities:
                errors.append(f"operation {o.id}: unknown entity '{ref}'")
        cond_ids = {c.id for c in o.conditions}
        for f in o.inputs + o.outputs:
            if f.asset not in assets:
                errors.append(f"operation {o.id} flow {f.id}: unknown asset '{f.asset}'")
            if f.amount.asset != f.asset:
                errors.append(f"operation {o.id} flow {f.id}: amount asset differs from flow asset")
            for ref in (f.from_, f.to):
                if ref and ref not in entities:
                    errors.append(f"operation {o.id} flow {f.id}: unknown entity '{ref}'")
            for c in f.conditions:
                if c not in cond_ids:
                    errors.append(f"operation {o.id} flow {f.id}: unknown condition '{c}'")
            ev(f"operation {o.id} flow {f.id}", f.evidence)
        for p in o.participants:
            if p.entity not in entities:
                errors.append(f"operation {o.id}: unknown participant '{p.entity}'")
        for c in o.conditions:
            ev(f"operation {o.id} condition {c.id}", c.evidence)
        for u in o.unresolved:
            if u not in unresolved:
                errors.append(f"operation {o.id}: unknown unresolved item '{u}'")
        ev(f"operation {o.id}", o.evidence)
    for p in result.positions:
        if p.holder not in entities:
            errors.append(f"position {p.id}: unknown holder '{p.holder}'")
        if p.asset not in assets:
            errors.append(f"position {p.id}: unknown asset '{p.asset}'")
        if p.amount and p.amount.asset != p.asset:
            errors.append(f"position {p.id}: amount must be denominated in the position asset")
        if p.counterparty and p.counterparty not in entities:
            errors.append(f"position {p.id}: unknown counterparty '{p.counterparty}'")
        ev(f"position {p.id}", p.evidence)
    by_pos = {p.id: p for p in result.positions}
    by_asset = {a.id: a for a in result.assets}
    for r in result.relations:
        for ref in (r.source, r.target):
            if ref not in nodes:
                errors.append(f"relation {r.id}: unknown node '{ref}'")
        if r.asset and r.asset not in assets:
            errors.append(f"relation {r.id}: unknown asset '{r.asset}'")
        if r.operation and r.operation not in ops:
            errors.append(f"relation {r.id}: unknown operation '{r.operation}'")
        amount(f"relation {r.id}", r.amount)
        if r.kind == RelationKind.claim.value:
            s, t = by_pos.get(r.source), by_pos.get(r.target)
            if s and t and s.asset == t.asset and s.holder == t.holder:
                errors.append(f"relation {r.id}: a claim cannot point to the same position")
        if r.kind == RelationKind.debt.value:
            s = by_pos.get(r.source)
            if s and s.kind != PositionKind.debt.value:
                errors.append(f"relation {r.id}: debt relations must start at a debt position")
        ev(f"relation {r.id}", r.evidence)
    for p in result.positions:
        if p.kind == PositionKind.debt.value:
            a = by_asset.get(p.asset)
            if a and a.kind in (AssetKind.share.value, AssetKind.lp_token.value):
                errors.append(f"position {p.id}: a debt position cannot be denominated in a share/LP asset")
    for x in result.exits:
        if x.position not in positions:
            errors.append(f"exit {x.id}: unknown position '{x.position}'")
        for s in x.steps:
            if s.operation not in ops:
                errors.append(f"exit {x.id}: unknown operation '{s.operation}'")
            if s.actor and s.actor not in entities:
                errors.append(f"exit {x.id}: unknown actor '{s.actor}'")
        for f in x.consumes + x.returns:
            if f.asset not in assets:
                errors.append(f"exit {x.id}: unknown asset '{f.asset}'")
        ev(f"exit {x.id}", x.evidence)
    for u in result.unresolved:
        for s in u.subjects:
            if s not in nodes and s not in {r.id for r in result.relations} and s not in {x.id for x in result.exits}:
                errors.append(f"unresolved {u.id}: unknown subject '{s}'")
    sources = {s.path for s in result.sources}
    for e in result.evidence:
        if e.kind == EvidenceKind.source.value and e.source is None:
            errors.append(f"evidence {e.id}: source evidence needs a source location")
        if e.source and sources and e.source.file not in sources and not e.source.snippet:
            errors.append(f"evidence {e.id}: file '{e.source.file}' is not embedded and no snippet given")
    for t in result.tasks:
        for f in t.focus:
            if f not in nodes and f not in {r.id for r in result.relations} and f not in {x.id for x in result.exits}:
                errors.append(f"task {t.id}: unknown focus '{f}'")
    return errors
