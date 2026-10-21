"""End-to-end checks of the analysis backend on the pinned Beefy snapshot."""

from pathlib import Path

import pytest

from fluxscope.model import AnalysisResult, semantic_errors

from .conftest import BEEFY, by_id

pytestmark = pytest.mark.slow


def test_result_is_valid(beefy):
    r = AnalysisResult.model_validate(beefy)
    assert semantic_errors(r) == []
    assert beefy["scope"]["kind"] == "static_analysis"
    assert beefy["meta"]["synthetic"] is False
    assert beefy["scope"]["project"]["commit"] == "e33f868e2d995c2db9219029c26d31cd8e7d5cb2"


def test_evidence_lines_match_source(beefy):
    """Every source evidence points at the lines that contain its snippet
    (guards against CRLF / offset problems)."""

    files = {s["path"]: s["content"].splitlines() for s in beefy["sources"]}
    checked = 0
    for e in beefy["evidence"]:
        loc = e.get("source")
        if not loc or not loc.get("snippet") or loc["file"] not in files:
            continue
        lines = files[loc["file"]][loc["start_line"] - 1: loc["end_line"]]
        first = loc["snippet"].splitlines()[0].strip()[:40]
        assert any(first in ln for ln in lines), (e["id"], first, lines[:2])
        checked += 1
    assert checked > 30


def test_deposit_and_redeem_flows(beefy):
    ops = by_id(beefy["operations"])
    dep = ops["op:vault.deposit(uint256)"]
    assert dep["kind"] == "deposit"
    mech = {(f["asset"], f.get("from"), f.get("to"), f["mechanism"]) for f in dep["inputs"] + dep["outputs"]}
    assert ("asset:strategy.want", "ent:any", "ent:vault", "transferFrom") in mech
    assert ("asset:vault", None, "ent:any", "mint") in mech
    # payer and share receiver are recorded separately as participants
    roles = {(p["role"], p["entity"]) for p in dep["participants"]}
    assert ("payer", "ent:any") in roles and ("share_receiver", "ent:any") in roles
    # earn() inside deposit allocates to the strategy in the same transaction
    earn = ops["op:vault.deposit(uint256)>vault.earn()"]
    assert earn["kind"] == "allocate" and earn["transaction"] == "same"
    red = ops["op:vault.withdraw(uint256)"]
    assert red["kind"] == "redeem"
    sw = ops["op:vault.withdraw(uint256)>strategy.withdraw(uint256)"]
    assert sw["initiator"] == "ent:vault" and sw["executor"] == "ent:strategy"
    assert any(c["kind"] == "branch" and "b < r" in c["text"] for c in sw["conditions"])


def test_roles_and_access_control(beefy):
    ops = by_id(beefy["operations"])
    ents = by_id(beefy["entities"])
    assert ops["op:strategy.withdraw(uint256)"]["initiator"] == "ent:vault"
    owner = ops["op:vault.upgradeStrat()"]["initiator"]
    assert ents[owner]["expression"].endswith("._owner")
    panic = ops["op:strategy.panic()"]["initiator"]
    assert "keeper" in ents[panic]["label"]
    # require(msg.sender == vault) evaluated in the vault -> strategy call context
    nested = ops["op:vault.withdraw(uint256)>strategy.withdraw(uint256)"]
    sat = [c for c in nested["conditions"] if c.get("status") and c["status"]["verification"] == "supported"]
    assert sat, "access check should be satisfied when the vault is the caller"


def test_claim_chain_and_checks(beefy):
    rel = by_id(beefy["relations"])
    share_claim = rel["rel:claim:pos_any_vault->pos_vault_strategy.want"]
    checks = {c["id"]: c["result"] for c in share_claim["status"]["checks"]}
    assert checks["deposit-link"] == "pass"
    assert checks["exit-exists"] == "pass"
    assert checks["amount-link"] == "pass"
    assert share_claim["status"]["verification"] in ("supported", "partially_supported")
    assert "rel:claim:pos_any_vault->pos_vault_claim_strategy" in rel
    excl = rel["rel:claim:pos_vault_claim_strategy->pos_strategy_strategy.want"]
    assert excl["status"]["verification"] == "supported"
    assert "rel:claim:pos_vault_claim_strategy->pos_strategy_strategy.erc4626" in rel
    erc = rel["rel:claim:pos_strategy_strategy.erc4626->pos_strategy.erc4626_strategy.erc4626.asset()"]
    assert erc["status"]["support"] == "adapter" and erc["status"]["verification"] == "partially_supported"


def test_share_and_underlying_not_double_counted(beefy):
    pos = by_id(beefy["positions"])
    assets = by_id(beefy["assets"])
    share = pos["pos:any:vault"]
    assert assets[share["asset"]]["kind"] == "share"
    # the underlying custody belongs to the issuing system, not to the share holder
    custody = [p for p in beefy["positions"] if p["asset"] == "asset:strategy.want" and p["kind"] == "custody"]
    assert {p["holder"] for p in custody} >= {"ent:vault", "ent:strategy"}
    claims = [r for r in beefy["relations"] if r["kind"] == "claim" and r["source"] == "pos:any:vault"]
    assert all(pos[r["target"]]["holder"] != share["holder"] for r in claims)


def test_identity_not_assumed_from_names(beefy):
    assets = by_id(beefy["assets"])
    assert "asset:strategy.want" in assets and "asset:strategy.erc4626.asset()" in assets
    ident = [r for r in beefy["relations"] if r["kind"] == "identity"]
    assert ident and ident[0]["status"]["verification"] == "pending"
    assert any(u["kind"] == "identity" for u in beefy["unresolved"])


def test_exit_paths(beefy):
    exits = [x for x in beefy["exits"] if x["position"] == "pos:any:vault"]
    labels = {x["label"] for x in exits}
    assert any("withdraw(" in l for l in labels)
    ex = next(x for x in exits if "BeefyVaultV7.withdraw(" in x["label"])
    assert ex["steps"][0]["actor"] == "ent:any"
    assert any(s["optional"] for s in ex["steps"][1:])
    assert ex["returns"] and ex["returns"][0]["asset"] == "asset:strategy.want"


def test_boundaries_and_unresolved(beefy):
    kinds = {u["kind"] for u in beefy["unresolved"]}
    assert "external_implementation" in kinds
    targets = " ".join(b["target"] for b in beefy["scope"]["boundaries"])
    assert "erc4626" in targets
    # upgradeStrat reassigns the bound strategy variable
    assert any(u["id"].startswith("unr:rebind:") for u in beefy["unresolved"])


def test_every_flow_and_relation_has_evidence(beefy):
    for o in beefy["operations"]:
        assert o["evidence"], o["id"]
        for f in o["inputs"] + o["outputs"]:
            assert f["evidence"], f["id"]
    for r in beefy["relations"]:
        assert r["evidence"], r["id"]


def test_report_embeds_result(beefy, tmp_path):
    from fluxscope.report import TEMPLATE, write_report

    if not TEMPLATE.exists():
        pytest.skip("viewer template not built")
    out = write_report(beefy, tmp_path / "r.html")
    html = out.read_text()
    assert beefy["meta"]["id"] in html
    assert "</script" not in html.split('id="fluxscope-data">', 1)[1].split("</script>", 1)[0]


def test_openzeppelin_erc4626_roles():
    """Unmodified OpenZeppelin 5.4.0 ERC4626: caller, receiver and owner are kept apart."""

    from .conftest import ROOT, analyze

    r = analyze(ROOT / "corpus" / "fixtures" / "oz-erc4626")
    ops = by_id(r["operations"])
    assert {ops[k]["kind"] for k in ("op:vault.deposit(uint256,address)", "op:vault.mint(uint256,address)")} == {"deposit"}
    assert ops["op:vault.redeem(uint256,address,address)"]["kind"] == "redeem"
    assert ops["op:vault.withdraw(uint256,address,address)"]["kind"] == "withdraw"
    red = ops["op:vault.redeem(uint256,address,address)"]
    burn = [f for f in red["inputs"] if f["mechanism"] == "burn"][0]
    pay = [f for f in red["outputs"] if f["mechanism"] == "transfer"][0]
    assert burn["from"].endswith(".owner") and pay["to"].endswith(".receiver") and red["initiator"] == "ent:any"
    rules = {f["status"]["rule"] for o in r["operations"] for f in o["inputs"] + o["outputs"]}
    assert {"oz.erc20.internal", "oz.safeerc20"} <= rules
    claim = [x for x in r["relations"] if x["kind"] == "claim"][0]
    assert claim["status"]["verification"] == "supported"
    assert any("exceeds" in c["text"] or "max" in c["text"] for c in red["conditions"])


def test_metamorpho_held_out():
    """Held-out real project (no analyzer changes): roles, fee shares, Morpho Blue as a boundary."""

    from .conftest import ROOT, analyze

    r = analyze(ROOT / "corpus" / "metamorpho")
    assert semantic_errors(AnalysisResult.model_validate(r)) == []
    ops = by_id(r["operations"])
    ents = by_id(r["entities"])
    assert ops["op:vault.deposit(uint256,address)"]["kind"] == "deposit"
    assert ops["op:vault.redeem(uint256,address,address)"]["kind"] == "redeem"
    alloc = ents[ops["op:vault.reallocate(MarketAllocation[])"]["initiator"]]["label"]
    assert "member of MetaMorpho.isAllocator" in alloc and "owner of MetaMorpho" in alloc
    fee = [f for f in ops["op:vault.setFee(uint256)"]["outputs"] if f["mechanism"] == "mint"]
    assert fee and fee[0]["to"] == "ent:vault.feeRecipient"
    assert any("MORPHO" in b["target"] for b in r["scope"]["boundaries"])
    assert any(u["kind"] == "external_implementation" and "supply()" in u["text"] for u in r["unresolved"])


def test_evaluation_matches_abi_signatures(tmp_path):
    """Annotations name entry points with ABI types; the analyzer keeps declared type names."""

    from fluxscope.evaluate import declared_types, score_result

    (tmp_path / "I.sol").write_text("// a contract bytes note\ninterface IStrategy {}\nenum Mode { A }\n"
                                    "struct OperatorSet { address a; }\n")
    op = {"id": "op:1", "parent": None, "kind": "deposit", "initiator": None, "status": {"support": "rule"},
          "entrypoint": {"contract": "M", "signature": "f(IStrategy[],Mode,OperatorSet,bytes,address payable)"}}
    truth = {"operations": {"M.f(address[],uint8,OperatorSet,bytes,address)": {"kind": ["deposit"], "caller": "-"}},
             "claims": [], "dependencies": [], "exits": {}, "identity_questions": []}
    sc = score_result(truth, {"operations": [op]}, declared_types(tmp_path))
    assert sc["operations_missing"] == [] and sc["operation_kind"]["accuracy"] == 1.0
    assert score_result(truth, {"operations": [op]})["operations_missing"]  # without the declarations: no match
