"""Kaia yield aggregator (partial snapshot): ledgers, registries and debts on a real project.

The snapshot is not redistributed (BUSL-1.1 / GPL-3.0 sources); these tests
are skipped unless it was fetched with ``python scripts/fetch_snapshot.py
corpus/kaia-yield``.
"""

import time

import pytest

from fluxscope.model import AnalysisResult, semantic_errors

from .conftest import ROOT, analyze, by_id

pytestmark = pytest.mark.slow
KAIA = ROOT / "corpus" / "kaia-yield"


@pytest.fixture(scope="module")
def se():
    if not (KAIA / "src" / "superearn" / "core" / "CooldownVault.sol").exists():
        pytest.skip("Kaia yield snapshot not fetched (scripts/fetch_snapshot.py corpus/kaia-yield)")
    t = time.time()
    r = analyze(KAIA)
    r["_seconds"] = time.time() - t
    return r


def test_valid_and_fast(se):
    secs = se.pop("_seconds")
    assert semantic_errors(AnalysisResult.model_validate(se)) == []
    assert se["scope"]["project"]["commit"] == "ddbfd27e1fab5f7c3a759aca5a4565e22235c9b6"
    # nested Yearn strategy code used to make symbolic amounts grow exponentially
    assert secs < 60, secs


def test_membership_access_control(se):
    ops, ents = by_id(se["operations"]), by_id(se["entities"])
    assert ops["op:cooldown.deposit(uint256,address)"]["initiator"] == "ent:members:cooldown._authorizedAddresses"
    assert ops["op:cooldown.predeposit(uint256)"]["initiator"] == "ent:members:cooldown._strategiesSet"
    assert "CustomYearnStrategy" in ents["ent:members:cooldown._strategiesSet"]["description"]
    ops_label = ents[ops["op:customVault.depositToCustomStrategy(address,address,uint256)"]["initiator"]]["label"]
    assert "member of CustomVault.operators" in ops_label and "governance of CustomVault" in ops_label
    # the strategy calling the cooldown vault satisfies onlyStrategy through the configured registry
    nested = [o for o in se["operations"] if o.get("parent") and o["label"].startswith("CooldownVault.instantRedeem")]
    assert nested and all(o["initiator"] == "ent:strategy" for o in nested)
    assert any(c.get("status") and c["status"]["verification"] == "supported" for o in nested for c in o["conditions"])


def test_registry_call_targets(se):
    ops = by_id(se["operations"])
    sub = ops["op:customVault.depositToCustomStrategy(address,address,uint256)>customStrategy.deposit(address,uint256)"]
    assert sub["executor"] == "ent:customStrategy" and "registered" in sub["description"]
    # token == asset() in the caller resolves the custom strategy's token parameter
    assert [f["asset"] for f in sub["inputs"]] == ["asset:strategy.externalUnderlyingToken"]
    # transfer recipients that must be authorized holders are a holder class, not the configured member
    t = ops["op:cooldown.transfer(address,uint256)"]
    assert t["inputs"][0]["to"] != "ent:strategy"


def test_ledgers_claims_and_debts(se):
    assets, pos, ops = by_id(se["assets"]), by_id(se["positions"]), by_id(se["operations"])
    rel = by_id(se["relations"])
    req = assets["asset:cooldown._pendingReceiverAssets"]
    assert req["kind"] == "accounting" and req["status"]["rule"] == "structural.ledger"
    assert pos["pos:any:cooldown._pendingReceiverAssets"]["kind"] == "claim"
    assert ops["op:cooldown.claim(uint256,uint256)"]["kind"] == "claim"
    assert ops["op:cooldown.redeem(uint256,address,address)"]["kind"] == "redeem"
    assert ops["op:cooldown.withdraw(uint256,address,address)"]["kind"] == "withdraw"
    assert ops["op:cooldown.updateRedeemReceiver(uint256,address)"]["kind"] == "transfer"
    claim = rel["rel:claim:pos_any_cooldown._pendingReceiverAssets->pos_cooldown_strategy.externalUnderlyingToken"]
    assert claim["status"]["rule"] == "claim.ledger" and claim["status"]["verification"] == "supported"
    assert claim["operation"] == "op:cooldown.claim(uint256,uint256)"
    # predeposit: shares minted against a recorded debt; repaid by the strategy
    debt = pos["pos:strategy:cooldown.strategyDebtOutstanding"]
    assert debt["kind"] == "debt" and debt["counterparty"] == "ent:cooldown"
    assert "rel:debt:pos_strategy_cooldown.strategyDebtOutstanding" in rel
    assert ops["op:cooldown.predeposit(uint256)"]["kind"] == "borrow"
    assert ops["op:cooldown.retrieveDebt(uint256)"]["kind"] == "repay"
    assert ops["op:strategy.repayPredepositDebt(uint256)"]["kind"] == "repay"
    # a ledger whose entries are neither clearly claims nor debts is reported, not guessed
    assert any(u["id"] == "unr:ledger:cooldown.strategyShortfall" for u in se["unresolved"])
    assert not [p for p in se["positions"] if p["asset"] == "asset:cooldown.strategyShortfall"]


def test_exits_and_external_claims(se):
    exits = {(x["position"], x["steps"][0]["operation"]) for x in se["exits"]}
    assert ("pos:any:cooldown._pendingReceiverAssets", "op:cooldown.claim(uint256,uint256)") in exits
    assert ("pos:members:cooldown._authorizedAddresses:cooldown", "op:cooldown.redeem(uint256,address,address)") in exits
    # only the Yearn vault (external, bound by the configuration) may call strategy.withdraw
    rel = by_id(se["relations"])
    assert "rel:claim:pos_strategy.vault_claim_strategy->pos_strategy_cooldown" in rel
    assert any(b["target"] == "Yearn V2 vault (Kaia)" for b in se["scope"]["boundaries"])


def test_initializers_skipped(se):
    facts = next(s for s in se["provenance"]["stages"] if s["stage"] == "facts")
    assert "CustomVault.initialize(address,address,string,string)" in facts["skipped_initializers"]
    assert not any(o["id"].endswith(".initialize(address,address,string,string)") for o in se["operations"])


def test_super_call_dispatches_to_instance_overrides(se):
    """CooldownVault.depositFor calls super.depositFor (ERC20Wrapper), whose _mint must reach
    CooldownVault's _beforeTokenTransfer override (authorized holders only)."""

    ops = by_id(se["operations"])
    inner = [o for o in se["operations"] if o["id"].startswith("op:cooldown.deposit(uint256,address)>")
             and any(f["mechanism"] == "mint" for f in o["outputs"])]
    assert inner and any("_authorizedAddresses.contains(account)" in c["text"] for c in inner[0]["conditions"])
    mint = next(f for f in inner[0]["outputs"] if f["mechanism"] == "mint")
    assert ops and any(p["id"] == "pos:members:cooldown._authorizedAddresses:cooldown" for p in se["positions"])
    assert mint["to"] == "ent:param:cooldown.deposit(uint256_address).receiver"
