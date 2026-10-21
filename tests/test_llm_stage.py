"""The LLM stage: proposals are validated against facts, recorded, and replayable.

No live model is called here. The provider below returns a HAND-WRITTEN TEST
FIXTURE (not model output) that mixes valid proposals with invalid ones, so the
validation and merge rules can be checked deterministically.
"""

import json

import pytest

from fluxscope.analyze import AnalysisOptions, analyze_project
from fluxscope.llm import client as llm_client
from fluxscope.llm.semantic import SCHEMA

from .conftest import BEEFY, by_id

pytestmark = pytest.mark.slow

FIXTURE_RESPONSE = {
    "operations": [
        {"op_id": "op:strategy.harvest()", "kind": "harvest", "label": "Compound rewards",
         "description": "TEST FIXTURE TEXT: swaps rewards to want, pays fees in native and re-deposits.",
         "fact_refs": ["op:strategy.harvest()"], "source_refs": [
             {"file": "contracts/BIFI/strategies/Common/BaseAllToNativeFactoryStrat.sol", "start_line": 145, "end_line": 162}]},
        {"op_id": "op:vault.deposit(uint256)", "kind": "swap", "label": "wrong on purpose",
         "description": "TEST FIXTURE TEXT: disagrees with the rule.", "fact_refs": ["op:vault.deposit(uint256)"], "source_refs": []},
        {"op_id": "op:does-not-exist", "kind": "deposit", "label": "x", "description": "x", "fact_refs": ["op:vault.earn()"],
         "source_refs": []},
        {"op_id": "op:vault.earn()", "kind": "allocate", "label": "x", "description": "no refs", "fact_refs": [],
         "source_refs": []},
    ],
    "relations": [
        {"kind": "identity", "source": "asset:strategy.want", "target": "asset:strategy.erc4626.asset()",
         "operation": "", "description": "TEST FIXTURE TEXT: same as the rule candidate.",
         "fact_refs": ["rel:identity:asset_strategy.want=asset_strategy.erc4626.asset()"], "source_refs": []},
        {"kind": "depends_on", "source": "ent:strategy", "target": "ent:strategy.swapper", "operation": "",
         "description": "TEST FIXTURE TEXT: harvest proceeds depend on the swapper.",
         "fact_refs": ["op:strategy.harvest()"], "source_refs": []},
        {"kind": "claim", "source": "ent:any", "target": "pos:vault:strategy.want", "operation": "",
         "description": "invalid: source is not a position", "fact_refs": ["op:vault.withdraw(uint256)"], "source_refs": []},
    ],
    "explanations": [
        {"subject": "asset:vault", "text": "TEST FIXTURE TEXT: priced as balance() / totalSupply().",
         "fact_refs": ["op:vault.withdraw(uint256)"]},
    ],
    "questions": [
        {"kind": "external_implementation", "subjects": ["ent:strategy.swapper"],
         "text": "TEST FIXTURE TEXT: which routes does the swapper use?", "fact_refs": ["op:strategy.harvest()"]},
        {"kind": "other", "subjects": ["ent:nobody"], "text": "unknown subject", "fact_refs": ["op:strategy.harvest()"]},
    ],
}


class FixtureProvider(llm_client.Provider):
    name = "test-fixture"

    def _call(self, system, user, schema, **meta):
        assert "OPERATIONS" in user and "SOURCE CODE" in user
        assert schema == SCHEMA
        return json.dumps(FIXTURE_RESPONSE), {"input_tokens": 0, "output_tokens": 0}, "fixture"


@pytest.fixture(scope="module")
def llm_run(tmp_path_factory):
    cache = tmp_path_factory.mktemp("llm-cache")
    orig = llm_client.make_provider

    def fake(name, model, cache_dir):
        if name == "test-fixture":
            return FixtureProvider("fixture-model", cache_dir)
        return orig(name, model, cache_dir)

    import fluxscope.llm.semantic as sm

    sm.make_provider = fake
    try:
        r1 = analyze_project(BEEFY, options=AnalysisOptions(llm="test-fixture", llm_cache=cache)).to_json_dict()
        r2 = analyze_project(BEEFY, options=AnalysisOptions(llm="replay", llm_cache=cache)).to_json_dict()
    finally:
        sm.make_provider = orig
    return r1, r2, cache


def test_valid_proposals_merged(llm_run):
    r, _, _ = llm_run
    ops = by_id(r["operations"])
    h = ops["op:strategy.harvest()"]
    assert h["kind"] == "harvest" and h["status"]["support"] == "llm" and h["status"]["verification"] == "pending"
    assert any(e.startswith("ev:llm:") for e in h["evidence"])
    # cited source lines became source evidence
    ev = by_id(r["evidence"])
    assert any(ev[e]["kind"] == "source" and ev[e]["source"]["start_line"] == 145 for e in h["evidence"] if e in ev)
    # rule results are not overwritten by a disagreeing proposal
    assert ops["op:vault.deposit(uint256)"]["kind"] == "deposit"
    assert any(u["id"] == "unr:llm:kind:op:vault.deposit(uint256)" for u in r["unresolved"])


def test_relations_checked(llm_run):
    r, _, _ = llm_run
    rel = by_id(r["relations"])
    ident = rel["rel:identity:asset_strategy.want=asset_strategy.erc4626.asset()"]
    assert any(c["id"] == "model-agrees" for c in ident["status"]["checks"])
    assert ident["status"]["verification"] == "pending"
    # the rules already find strategy -> swapper (state-changing external call): the proposal is
    # recorded as agreement on the rule relation instead of a second, model-supported relation
    dep = rel["rel:dep:ent_strategy->ent_strategy.swapper"]
    assert dep["status"]["support"] == "rule"
    assert any(c["id"] == "model-agrees" for c in dep["status"]["checks"])
    assert "rel:llm:depends_on:ent:strategy->ent:strategy.swapper" not in rel


def test_invalid_proposals_rejected_and_recorded(llm_run):
    r, _, _ = llm_run
    call = r["provenance"]["llm_calls"][0]
    reasons = [x["reason"] for x in call["rejected"]]
    assert "unknown op_id" in reasons
    assert "no fact_refs" in reasons
    assert any("source/target" in x or "positions" in x for x in reasons)
    assert "no known subjects" in reasons
    assert call["accepted"] == 6
    assert call["provider"] == "test-fixture" and call["prompt_version"] == "semantic-v1"
    comps = [c for c in r["scope"]["components"] if c["role"] == "llm"]
    assert comps and comps[0]["version"] == "fixture-model"


def test_replay_reproduces_result(llm_run):
    r1, r2, cache = llm_run
    assert len(list(cache.glob("*.json"))) == 1
    assert r2["provenance"]["llm_calls"][0]["cached"] is True
    strip = lambda d: {k: v for k, v in d.items() if k not in ("meta", "provenance")}  # noqa: E731
    assert json.dumps(strip(r1), sort_keys=True) == json.dumps(strip(r2), sort_keys=True)


def test_replay_without_recording_fails(tmp_path):
    with pytest.raises(llm_client.LLMError):
        llm_client.make_provider("replay", None, tmp_path)
