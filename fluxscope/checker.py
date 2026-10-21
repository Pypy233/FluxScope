"""Check candidate relations against program facts.

A share claim ("holding S is a redeemable claim on asset A held by I") is not
established by an inflow followed by a mint. Following the design notes, the
checker verifies, per claim:

1. ``deposit-link``   – an entry point takes in A and issues S in the same transaction;
2. ``deposit-roles``  – who pays A and who receives S (reported, both must be bound);
3. ``exit-exists``    – an entry point consumes S and pays out A;
4. ``exit-roles``     – who calls the exit, whose S is burned, who receives A;
5. ``amount-link``    – the payout amount depends on the burned amount (data dependency);
6. ``boundaries``     – external implementations / unresolved items on the exit path.

LLM-proposed claims go through the same checks (``support = llm``).
"""

from __future__ import annotations

from typing import List, Optional

from .facts import exprs as X
from .model import Check, Relation


def check_all(sem) -> None:
    for r in list(sem.relations.values()):
        if r.kind != "claim":
            continue
        if r.status.support == "llm":
            # model proposals are checked like the rule claims of the same shape
            src = sem.positions.get(r.source)
            asset = src.asset if src is not None else ""
            if asset in getattr(sem, "ledgers", {}):
                check_ledger_claim(sem, r)
            elif asset.startswith("asset:claim:"):
                check_exclusive_claim(sem, r)
            else:
                check_claim(sem, r)
        elif r.status.rule == "claim.ledger":
            check_ledger_claim(sem, r)
        elif r.status.rule in ("claim.share-redemption", "claim.exclusive-withdrawal"):
            check_claim(sem, r)


def check_exclusive_claim(sem, r: Relation) -> None:
    """A non-tokenized claim of a contract on another (only it may call an entry
    point of the other that pays it): used for model-proposed claims whose source
    is such a claim position."""

    src, tgt = sem.positions.get(r.source), sem.positions.get(r.target)
    if src is None or tgt is None:
        r.status.checks = [Check(id="positions", result="fail", description="Claim does not connect two known positions")]
        r.status.verification = "contradicted"
        return
    holder, issuer, under = src.holder, tgt.holder, tgt.asset
    L = lambda eid: sem.entities[eid].label if eid in sem.entities else str(eid)  # noqa: E731
    A = lambda aid: sem.assets[aid].label  # noqa: E731
    checks: List[Check] = []
    gated = [o for o in sem.root_ops() if o.executor == issuer
             and any(p.role == "authorized_caller" and p.entity == holder for p in o.participants)]
    checks.append(Check(id="access-control", result="pass" if gated else "fail",
                        description=(f"{len(gated)} entry point(s) of {L(issuer)} can only be called by {L(holder)}: "
                                     + ", ".join(o.label for o in gated[:3])) if gated else
                        f"No entry point of {L(issuer)} is restricted to {L(holder)}",
                        evidence=[e for o in gated[:1] for p in o.participants if p.role == "authorized_caller"
                                  for e in p.evidence][:2]))
    pays = [f for o in sem.root_ops() for f in sem.all_flows(o) if f.from_ == issuer and f.to == holder and f.asset == under]
    checks.append(Check(id="payout", result="pass" if pays else "fail",
                        description=(f"{A(under)} is paid from {L(issuer)} to {L(holder)} ({pays[0].amount.expression})"
                                     if pays else f"No flow of {A(under)} from {L(issuer)} to {L(holder)}"),
                        evidence=[e for f in pays[:1] for e in _ev(f)]))
    _finish(r, checks)


def _terms(e) -> set:
    if e is None:
        return set()
    return {X.key(n) for n in X.canonical(e).walk() if isinstance(n, (X.Param, X.State, X.Member, X.Index))}


def check_ledger_claim(sem, r: Relation) -> None:
    """A recorded entry (ledger) that the issuer settles by paying the account."""

    src, tgt = sem.positions.get(r.source), sem.positions.get(r.target)
    L = lambda eid: sem.entities[eid].label if eid in sem.entities else str(eid)  # noqa: E731
    A = lambda aid: sem.assets[aid].label  # noqa: E731
    if src is None or tgt is None:
        r.status.checks = [Check(id="positions", result="fail", description="Claim does not connect two known positions")]
        r.status.verification = "contradicted"
        return
    ledger, under, issuer = src.asset, r.asset or tgt.asset, tgt.holder
    info = sem.ledgers.get(ledger, {})
    checks: List[Check] = []
    credits = [v for v in info.get("votes", []) if v[0] == "claim" and "credits" in v[1]]
    checks.append(Check(id="credit-link", result="pass" if credits else "unknown",
                        description=credits[0][1] if credits else f"No operation shows what {L(issuer)} receives when an "
                                                                    f"entry of {A(ledger)} is created"))
    op = sem.operations.get(r.operation)
    if op is None:  # model proposal without an operation: any settling entry point
        op = next((o for o in sem.root_ops() if any(f.asset == ledger and f.mechanism == "debit" for f in sem.all_flows(o))
                   and not any(f.asset == ledger and f.mechanism == "credit" for f in sem.all_flows(o))), None)
    flows = sem.all_flows(op) if op else []
    debits = [f for f in flows if f.asset == ledger and f.mechanism == "debit"]
    pays = [f for f in flows if f.from_ == issuer and f.asset == under and any(f.to == d.from_ for d in debits)]
    checks.append(Check(id="exit-exists", result="pass" if debits and pays else "fail",
                        description=(f"{op.label} debits the entry and pays {A(under)} to the account" if debits and pays
                                     else f"No exit found that settles {A(ledger)} with a payout of {A(under)}"),
                        evidence=[e for f in debits[:1] + pays[:1] for e in _ev(f)]))
    if debits and pays:
        d, p = debits[0], pays[0]
        checks.append(Check(id="exit-roles", result="pass",
                            description=f"{L(op.initiator)} calls {op.label}; the entry of {L(d.from_)} is debited; "
                                        f"{A(p.asset)} is paid to {L(p.to)}"
                                        + ("" if op.initiator == d.from_ else " — the caller need not be the account"),
                            evidence=_ev(d) + _ev(p)))
        ed = next((fr.effect for fr in sem.flows if fr.flow.id == d.id), None)
        ep = next((fr.effect for fr in sem.flows if fr.flow.id == p.id), None)
        tp, td = _terms(ep.amount if ep else None), _terms(ed.amount if ed else None)
        shared = tp & td
        checks.append(Check(id="amount-link", result="pass" if shared else "unknown",
                            description=(f"Payout ({p.amount.expression}) and debited amount ({d.amount.expression}) are "
                                         "computed from the same recorded value" if shared else
                                         f"Could not relate the payout ({p.amount.expression}) to the debited amount "
                                         f"({d.amount.expression})"), evidence=_ev(p)))
    unres = sorted({u for x in ([op] + sem.descendants(op.id) if op else []) for u in x.unresolved})
    checks.append(Check(id="boundaries", result="pass" if not unres else "unknown",
                        description="Exit path stays within analysed code" if not unres else
                        f"{len(unres)} unresolved item(s) on the exit path"))
    _finish(r, checks)


def _ev(flow) -> List[str]:
    return [e for e in flow.evidence if e.startswith("ev:src")][:2]


def check_claim(sem, r: Relation) -> None:
    src = sem.positions.get(r.source)
    tgt = sem.positions.get(r.target)
    checks: List[Check] = []
    if src is None or tgt is None:
        r.status.checks = [Check(id="positions", result="fail", description="Claim does not connect two known positions")]
        r.status.verification = "contradicted"
        return
    share, under = src.asset, (r.asset or tgt.asset)
    holder, issuer = src.holder, tgt.holder
    via_claim = sem.assets[tgt.asset].kind == "accounting"
    if via_claim:
        issuer = sem.operations[r.operation].executor if r.operation in sem.operations else issuer
    L = lambda eid: sem.entities[eid].label if eid in sem.entities else str(eid)  # noqa: E731
    A = lambda aid: sem.assets[aid].label  # noqa: E731

    if r.status.rule == "claim.exclusive-withdrawal":
        op = sem.operations.get(r.operation)
        ac = [p for p in op.participants if p.role == "authorized_caller"] if op else []
        checks.append(Check(id="access-control", result="pass" if ac and ac[0].entity == holder else "fail",
                            description=f"{op.label if op else '?'} can only be called by {L(holder)}",
                            evidence=ac[0].evidence if ac else []))
        pays = [f for f in sem.all_flows(op) if f.to == holder and f.from_ == issuer] if op else []
        checks.append(Check(id="payout", result="pass" if pays else "fail",
                            description=f"{op.label if op else '?'} pays {', '.join(sorted({A(f.asset) for f in pays})) or '-'} "
                                        f"from {L(issuer)} to {L(holder)}",
                            evidence=[e for f in pays for e in _ev(f)][:3]))
        unres = sorted({u for d in ([op] + sem.descendants(op.id) if op else []) for u in d.unresolved})
        checks.append(Check(id="boundaries", result="pass" if not unres else "unknown",
                            description="No unresolved external implementation on the payout path" if not unres else
                            f"{len(unres)} unresolved item(s) on the payout path: " + "; ".join(
                                sem.unresolved[u].text for u in unres)[:300]))
        _finish(r, checks)
        return

    root_ops = sem.root_ops()
    deposits = []
    for op in root_ops:
        mints = [f for f in sem.all_flows(op) if f.mechanism == "mint" and f.asset == share]
        ins = [f for f in sem.all_flows(op) if f.to == issuer and f.mechanism in ("transfer", "transferFrom") and f.asset == under]
        if mints and ins:
            deposits.append((op, mints[0], ins[0]))
    checks.append(Check(
        id="deposit-link", result="pass" if deposits else "fail",
        description=(f"{len(deposits)} entry point(s) take in {A(under)} and issue {A(share)} in the same transaction: "
                     + ", ".join(op.label for op, _, _ in deposits[:3])) if deposits else
        f"No entry point takes in {A(under)} and issues {A(share)} in one transaction",
        evidence=[e for _, m, i in deposits[:2] for e in _ev(i) + _ev(m)][:4]))
    if deposits:
        op, m, i = deposits[0]
        bound = m.to is not None and i.from_ is not None and not m.to.startswith("ent:expr:") and not i.from_.startswith("ent:expr:")
        same = m.to == i.from_
        checks.append(Check(
            id="deposit-roles", result="pass" if bound else "unknown",
            description=f"In {op.label}: {L(i.from_)} pays {A(under)} ({i.amount.expression}); "
                        f"{L(m.to)} receives {A(share)} ({m.amount.expression})"
                        + ("" if same else " — payer and share receiver are different parties"),
            evidence=_ev(i) + _ev(m)))

    if via_claim:
        backing = sem.assets[tgt.asset].issuer
        alloc = [f for op in root_ops for f in sem.all_flows(op)
                 if f.from_ == issuer and f.to == backing and f.asset == under]
        checks.append(Check(
            id="allocation-link", result="pass" if alloc else "fail",
            description=(f"{L(issuer)} moves {A(under)} to {L(backing)} (e.g. {alloc[0].amount.expression}), so part of the "
                         f"underlying is held there" if alloc else f"No flow of {A(under)} from {L(issuer)} to {L(backing)} found"),
            evidence=[e for f in alloc[:1] for e in _ev(f)]))
    exit_op = sem.operations.get(r.operation)
    burns = [f for f in exit_op.inputs if f.mechanism == "burn" and f.asset == share] if exit_op else []
    pays = [f for f in exit_op.outputs if f.from_ == issuer and f.asset == under] if exit_op else []
    if not pays and exit_op is not None:
        pays = [f for f in exit_op.outputs if f.from_ == issuer]
    checks.append(Check(
        id="exit-exists", result="pass" if burns and pays else "fail",
        description=(f"{exit_op.label} burns {A(share)} and pays out {', '.join(sorted({A(f.asset) for f in pays}))}"
                     if burns and pays else f"No exit found that burns {A(share)} and pays out {A(under)}"),
        evidence=[e for f in burns[:1] + pays[:1] for e in _ev(f)]))
    if burns and pays:
        b, p = burns[0], pays[0]
        caller = exit_op.initiator
        checks.append(Check(
            id="exit-roles", result="pass" if b.from_ == caller or sem.holder_class(b.from_ or "") == holder else "unknown",
            description=f"{L(caller)} calls {exit_op.label}; {A(share)} is burned from {L(b.from_)}; "
                        f"{A(p.asset)} is paid to {L(p.to)}"
                        + ("" if b.from_ == caller else " — shares are burned from a party other than the caller"),
            evidence=_ev(b) + _ev(p)))
        dep = _amount_depends(sem, exit_op, b, p)
        checks.append(Check(
            id="amount-link", result="pass" if dep else ("unknown" if dep is None else "fail"),
            description=(f"Payout amount ({p.amount.expression}) data-depends on the burned amount ({b.amount.expression})"
                         if dep else f"Could not show that the payout depends on the burned amount ({b.amount.expression})"),
            evidence=_ev(p)))
    unres = sorted({u for d in ([exit_op] + sem.descendants(exit_op.id) if exit_op else []) for u in d.unresolved})
    partial = any(d.status.verification == "partially_supported" for d in sem.descendants(exit_op.id)) if exit_op else False
    detail = []
    if unres:
        detail.append(f"{len(unres)} unresolved item(s)")
    if partial:
        detail.append("steps that rely on external implementations (adapter semantics)")
    checks.append(Check(id="boundaries", result="pass" if not detail else "unknown",
                        description="Exit path stays within analysed code" if not detail else
                        "Exit path contains " + " and ".join(detail)))
    _finish(r, checks)


def _amount_depends(sem, op, burn, pay) -> Optional[bool]:
    """Is the payout linked to the burned amount? Uses Slither's data dependency
    in either direction (redeem: payout from shares; withdraw: shares from
    payout) and falls back to shared entry-point inputs of the symbolic amounts."""

    tree = sem.op_tree.get(op.id)
    if tree is None or tree.frame is None:
        return None
    fn = tree.frame.function
    try:
        from slither.analyses.data_dependency.data_dependency import is_dependent
    except Exception:  # pragma: no cover
        is_dependent = None
    names = {v.name: v for v in list(fn.variables) + list(fn.parameters) if v.name}
    a, b = names.get(burn.amount.expression or ""), names.get(pay.amount.expression or "")
    if is_dependent is not None and a is not None and b is not None:
        if is_dependent(b, a, fn) or is_dependent(a, b, fn):
            return True
    exprs = {}
    for fr in sem.flows:
        if fr.flow.id in (burn.id, pay.id) and fr.effect.amount is not None:
            exprs[fr.flow.id] = {n for n in X.canonical(fr.effect.amount).walk() if isinstance(n, X.Param)} | \
                                {n for n in fr.effect.amount.walk() if isinstance(n, X.Param)}
    if burn.id in exprs and pay.id in exprs:
        return bool(exprs[burn.id] & exprs[pay.id])
    return None


def _finish(r: Relation, checks: List[Check]) -> None:
    r.status.checks = checks
    res = [c.result for c in checks]
    if "fail" in res:
        r.status.verification = "contradicted" if sum(1 for x in res if x == "fail") > 1 or \
            any(c.id in ("exit-exists", "access-control") and c.result == "fail" for c in checks) else "partially_supported"
    elif "unknown" in res:
        r.status.verification = "partially_supported"
    else:
        r.status.verification = "supported"
