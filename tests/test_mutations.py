"""Changing the code must change the recovered relations — and only what changed.

Each test copies a project, applies one source edit and re-runs the analysis.
"""

import json

import pytest

from fluxscope.diff import diff
from fluxscope.project import load_config

from .conftest import BEEFY, FIXTURE, analyze, by_id, mutate

pytestmark = pytest.mark.slow

VAULT = "contracts/BIFI/vaults/BeefyVaultV7.sol"
STRAT_BASE = "contracts/BIFI/strategies/Common/BaseAllToNativeFactoryStrat.sol"


def test_beefy_share_receiver_change(beefy, tmp_path):
    """_mint(msg.sender, shares) -> _mint(owner(), shares): the share holder changes."""

    m = mutate(BEEFY, tmp_path / "p", VAULT, "_mint(msg.sender, shares);", "_mint(owner(), shares);")
    after = analyze(m)
    dep = by_id(after["operations"])["op:vault.deposit(uint256)"]
    mint = [f for f in dep["outputs"] if f["mechanism"] == "mint"][0]
    ents = by_id(after["entities"])
    assert ents[mint["to"]]["expression"].endswith("._owner")
    holders = {p["holder"] for p in after["positions"] if p["asset"] == "asset:vault"}
    assert mint["to"] in holders
    claim = by_id(after["relations"])
    roles = [c for r in after["relations"] if r["kind"] == "claim" and r["source"].endswith(":vault")
             for c in r["status"]["checks"] if c["id"] == "deposit-roles"]
    assert any("different parties" in c["description"] for c in roles)
    d = diff(beefy, after)
    assert "flows" in d and "positions" in d


def test_beefy_strategy_access_control_removed(beefy, tmp_path):
    """Without require(msg.sender == vault) in withdraw(), anyone can trigger it. Only the caller constraint
    changes: withdraw() still pays the strategy's assets to the vault, so its kind, the vault's claim on the
    strategy and the exits through withdraw() stay; those exits are now triggered by any account, and the
    result raises a review question about third parties triggering the payout."""

    m = mutate(BEEFY, tmp_path / "p", STRAT_BASE,
               '    function withdraw(uint256 _amount) external {\n        require(msg.sender == vault, "!vault");\n',
               '    function withdraw(uint256 _amount) external {\n')
    after = analyze(m)
    op_b = by_id(beefy["operations"])["op:strategy.withdraw(uint256)"]
    op_a = by_id(after["operations"])["op:strategy.withdraw(uint256)"]
    assert op_b["initiator"] == "ent:vault" and op_a["initiator"] == "ent:any"
    assert op_a["kind"] == op_b["kind"] == "withdraw"

    def payouts(op):
        return {(f["from"], f["to"], f["asset"]) for f in op["inputs"] + op["outputs"] if f["mechanism"] == "transfer"}

    assert payouts(op_a) == payouts(op_b) and ("ent:strategy", "ent:vault") in {p[:2] for p in payouts(op_a)}
    claim = "pos:vault:claim:strategy"

    def exits(r):
        return {x["label"]: x["steps"][0]["actor"] for x in r["exits"] if x["position"] == claim}

    eb, ea = exits(beefy), exits(after)
    assert eb.keys() == ea.keys() and any("withdraw" in k for k in ea)
    assert {eb[k] for k in eb if "withdraw" in k} == {"ent:vault"}
    assert {ea[k] for k in ea if "withdraw" in k} == {"ent:any"}
    assert "rel:claim:pos_vault_claim_strategy->pos_strategy_strategy.want" in by_id(after["relations"])
    assert any(u["id"].startswith("unr:open-trigger:") for u in after["unresolved"])
    assert not any(u["id"].startswith("unr:open-trigger:") for u in beefy["unresolved"])
    d = diff(beefy, after)
    assert "operations" in d and "exits" in d and "unresolved" in d


def test_beefy_exit_removed(tmp_path):
    """Dropping the burn from withdraw() removes the exit and the share claim."""

    m = mutate(BEEFY, tmp_path / "p", VAULT, "        _burn(msg.sender, _shares);\n", "")
    after = analyze(m)
    assert not [x for x in after["exits"] if x["position"] == "pos:any:vault"]
    assert any(u["kind"] == "missing_exit" for u in after["unresolved"])
    assert not [r for r in after["relations"] if r["kind"] == "claim" and r["source"] == "pos:any:vault"]


def test_fixture_receiver_change(fixture_result, tmp_path):
    """enter(): _issue(receiver, ...) -> _issue(msg.sender, ...)."""

    before = by_id(fixture_result["operations"])["op:vault.enter(uint256,address)"]
    m = mutate(FIXTURE, tmp_path / "p", "src/ReceiverVault.sol", "_issue(receiver, shares);", "_issue(msg.sender, shares);")
    after = by_id(analyze(m)["operations"])["op:vault.enter(uint256,address)"]
    to_before = [f["to"] for f in before["outputs"] if f["mechanism"] == "mint"][0]
    to_after = [f["to"] for f in after["outputs"] if f["mechanism"] == "mint"][0]
    assert to_before.endswith(".receiver") and to_after == "ent:any"


def test_fixture_symbol_change_does_not_merge_assets(tmp_path):
    """Giving two different tokens the same label/symbol must not merge them;
    only an explicit identity (deployment fact) does."""

    m = mutate(FIXTURE, tmp_path / "p", "src/ReceiverVault.sol", 'Ledger("Strategy share", "sU")', 'Ledger("Vault share", "vU")')
    cfg = json.loads((m / "fluxscope.json").read_text())
    cfg["identities"] = []
    cfg["assets"] = [
        {"expression": "vault.asset", "label": "USDT", "symbol": "USDT"},
        {"expression": "strategy.asset", "label": "USDT", "symbol": "USDT"},
        {"expression": "vault", "label": "vU", "symbol": "vU"},
        {"expression": "strategy", "label": "vU", "symbol": "vU"},
    ]
    (m / "fluxscope.json").write_text(json.dumps(cfg))
    after = analyze(m, load_config(m))
    labels = [a["id"] for a in after["assets"] if a["label"] == "USDT"]
    assert sorted(labels) == ["asset:strategy.asset", "asset:vault.asset"]
    shares = [a["id"] for a in after["assets"] if a["label"] == "vU"]
    assert sorted(shares) == ["asset:strategy", "asset:vault"]


def test_fixture_identity_from_config(fixture_result):
    ids = [a["id"] for a in fixture_result["assets"]]
    assert "asset:vault.asset" in ids and "asset:strategy.asset" not in ids
    assert any("Same token" in a["text"] for a in fixture_result["scope"]["assumptions"])


def test_fixture_structural_token_rule(fixture_result):
    """_issue/_destroy are recognised by their state updates, not by name."""

    flows = [f for o in fixture_result["operations"] for f in o["inputs"] + o["outputs"]]
    rules = {f["status"]["rule"] for f in flows if f["mechanism"] in ("mint", "burn")}
    assert rules == {"structural.token-accounting"}
    rel = by_id(fixture_result["relations"])
    assert "rel:claim:pos_vault_strategy->pos_strategy_vault.asset" in rel
    assert "rel:claim:pos_any_vault->pos_vault_strategy" in rel
