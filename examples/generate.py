"""Generate the synthetic example cases and test fixtures.

All data written by this script is SYNTHETIC: the Solidity snippets, addresses
and transaction hashes are invented to exercise the viewer and must not be read
as describing any real project. Line ranges in the evidence are computed from
the embedded synthetic sources, so evidence click-through highlights the right
code.

    .venv/bin/python examples/generate.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fluxscope.model import AnalysisResult, semantic_errors  # noqa: E402

HEADER = "// SYNTHETIC EXAMPLE CODE — not from a real project\n"


def S(basis: str, support: str, verification: str, rule: Optional[str] = None,
      checks: Optional[List[Dict[str, Any]]] = None, note: Optional[str] = None) -> Dict[str, Any]:
    d: Dict[str, Any] = {"basis": basis, "support": support, "verification": verification}
    if rule:
        d["rule"] = rule
    if checks:
        d["checks"] = checks
    if note:
        d["note"] = note
    return d


def chk(cid: str, description: str, result: str, detail: Optional[str] = None, evidence: Optional[List[str]] = None):
    d: Dict[str, Any] = {"id": cid, "description": description, "result": result}
    if detail:
        d["detail"] = detail
    if evidence:
        d["evidence"] = evidence
    return d


def amt(asset: str, expression: Optional[str] = None, value: Optional[str] = None, unit: str = "unknown",
        note: Optional[str] = None) -> Dict[str, Any]:
    d: Dict[str, Any] = {"asset": asset, "unit": unit}
    if value is not None:
        d["value"] = value
    if expression:
        d["expression"] = expression
    if note:
        d["note"] = note
    return d


def flow(fid: str, asset: str, frm: Optional[str], to: Optional[str], amount: Dict[str, Any],
         mechanism: Optional[str] = None, evidence: Optional[List[str]] = None,
         conditions: Optional[List[str]] = None, status: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    d: Dict[str, Any] = {"id": fid, "asset": asset, "from": frm, "to": to, "amount": amount}
    if mechanism:
        d["mechanism"] = mechanism
    if evidence:
        d["evidence"] = evidence
    if conditions:
        d["conditions"] = conditions
    if status:
        d["status"] = status
    return d


def cond(cid: str, kind: str, text: str, expression: Optional[str] = None, evidence: Optional[List[str]] = None,
         negated: bool = False, status: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    d: Dict[str, Any] = {"id": cid, "kind": kind, "text": text}
    if expression:
        d["expression"] = expression
    if negated:
        d["negated"] = True
    if evidence:
        d["evidence"] = evidence
    if status:
        d["status"] = status
    return d


class Case:
    def __init__(self, meta: Dict[str, Any], scope: Dict[str, Any]):
        self.d: Dict[str, Any] = {
            "schema": "fluxscope/analysis",
            "schema_version": "0.1.0",
            "meta": meta,
            "scope": scope,
            "assets": [], "entities": [], "operations": [], "positions": [], "relations": [],
            "exits": [], "evidence": [], "sources": [], "unresolved": [], "tasks": [],
        }
        self._src: Dict[str, List[str]] = {}

    # -- sources & evidence --------------------------------------------------
    def source(self, path: str, content: str) -> None:
        text = HEADER + content.strip("\n") + "\n"
        self._src[path] = text.split("\n")
        self.d["sources"].append({
            "path": path, "language": "solidity", "synthetic": True,
            "sha256": hashlib.sha256(text.encode()).hexdigest(), "content": text,
        })

    def lines(self, path: str, start: str, end: Optional[str] = None, count: Optional[int] = None,
              occurrence: int = 1):
        lines = self._src[path]
        seen = 0
        s = None
        for i, line in enumerate(lines):
            if start in line:
                seen += 1
                if seen == occurrence:
                    s = i
                    break
        if s is None:
            raise KeyError(f"{path}: marker {start!r} not found")
        if end is not None:
            e = next(i for i in range(s, len(lines)) if end in lines[i])
        elif count is not None:
            e = s + count - 1
        else:
            e = s
        return s + 1, e + 1, "\n".join(lines[s:e + 1])

    def ev_src(self, eid: str, title: str, path: str, start: str, end: Optional[str] = None,
               count: Optional[int] = None, contract: Optional[str] = None, function: Optional[str] = None,
               description: Optional[str] = None, occurrence: int = 1) -> str:
        s, e, snippet = self.lines(path, start, end, count, occurrence)
        loc: Dict[str, Any] = {"file": path, "start_line": s, "end_line": e, "snippet": snippet}
        if contract:
            loc["contract"] = contract
        if function:
            loc["function"] = function
        d: Dict[str, Any] = {"id": eid, "kind": "source", "title": title, "synthetic": True, "source": loc}
        if description:
            d["description"] = description
        self.d["evidence"].append(d)
        return eid

    def ev_exec(self, eid: str, kind: str, title: str, tx: str, block: int, log_index: Optional[int] = None,
                call_path: Optional[str] = None, data: Optional[Dict[str, Any]] = None,
                description: Optional[str] = None, chain_id: int = 31337) -> str:
        ex: Dict[str, Any] = {"chain_id": chain_id, "tx_hash": tx, "block": block}
        if log_index is not None:
            ex["log_index"] = log_index
        if call_path:
            ex["call_path"] = call_path
        if data:
            ex["data"] = data
        d: Dict[str, Any] = {"id": eid, "kind": kind, "title": title, "synthetic": True, "execution": ex}
        if description:
            d["description"] = description
        self.d["evidence"].append(d)
        return eid

    def ev_other(self, eid: str, kind: str, title: str, description: Optional[str] = None,
                 reference: Optional[str] = None, llm: Optional[Dict[str, Any]] = None) -> str:
        d: Dict[str, Any] = {"id": eid, "kind": kind, "title": title, "synthetic": True}
        if description:
            d["description"] = description
        if reference:
            d["reference"] = reference
        if llm:
            d["llm"] = llm
        self.d["evidence"].append(d)
        return eid

    # -- graph objects -------------------------------------------------------
    def add(self, coll: str, obj: Dict[str, Any]) -> str:
        self.d[coll].append(obj)
        return obj["id"]

    def write(self, path: Path) -> None:
        result = AnalysisResult.model_validate(self.d)
        errs = semantic_errors(result)
        if errs:
            raise SystemExit(f"{path}: semantic errors:\n  " + "\n  ".join(errs))
        path.write_text(json.dumps(self.d, indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {path.relative_to(ROOT)}")


EXAMPLE_SCOPE_NOTE = "Synthetic example data for exercising the viewer. Names, code, addresses and transactions are invented."


# =============================================================================
# Example A — nested vault
# =============================================================================


def example_nested_vault() -> Case:
    c = Case(
        meta={
            "id": "example-nested-vault",
            "title": "Example A — Nested vault (synthetic)",
            "description": "A user deposits Token U into a vault, the vault issues vU shares, a keeper allocates "
                           "the idle assets to a strategy that issues sU shares to the vault and supplies the "
                           "assets to an external lending market L (receipt lU). Redemption unwinds the layers.",
            "synthetic": True,
            "created_at": "2026-10-20T00:00:00Z",
            "generator": {"name": "examples/generate.py", "version": "0.1.0"},
            "tags": ["example", "vault", "nested", "strategy"],
        },
        scope={
            "kind": "example",
            "project": {"name": "Example yield system (synthetic)", "version": "example-1",
                        "entry_contracts": ["ExampleVault", "ExampleStrategy"],
                        "files": ["example/ExampleVault.sol", "example/ExampleStrategy.sol", "example/IMarketL.sol"]},
            "chain": {"chain_id": 31337, "name": "local test chain (synthetic)", "block": 1200},
            "components": [{"name": "hand-written example", "role": "example"}],
            "boundaries": [{"id": "bnd:marketL", "target": "Lending market L (IMarketL)",
                            "reason": "Only the interface IMarketL is available; the market implementation is outside the analysis scope.",
                            "evidence": ["ev:a:imarket"]}],
            "assumptions": [{"id": "asm:a:erc20", "text": "Token U behaves as a standard ERC-20 token (no fee on transfer, no rebasing).",
                             "source": "example"}],
            "notes": [EXAMPLE_SCOPE_NOTE],
        },
    )

    c.source("example/ExampleVault.sol", """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {IERC20} from "./IERC20.sol";
import {ExampleStrategy} from "./ExampleStrategy.sol";

/// Example vault: users deposit Token U and receive vU shares.
contract ExampleVault {
    IERC20 public immutable asset;            // Token U (example)
    ExampleStrategy public strategy;          // bound by configuration
    address public keeper;
    bool public paused;

    mapping(address => uint256) public balanceOf;   // vU shares
    uint256 public totalSupply;

    modifier whenNotPaused() {
        require(!paused, "paused");
        _;
    }

    modifier onlyKeeper() {
        require(msg.sender == keeper, "!keeper");
        _;
    }

    function totalAssets() public view returns (uint256) {
        return asset.balanceOf(address(this)) + strategy.totalAssets();
    }

    function deposit(uint256 assets) external whenNotPaused returns (uint256 shares) {
        require(assets > 0, "zero");
        uint256 supply = totalSupply;
        shares = supply == 0 ? assets : assets * supply / totalAssets();
        asset.transferFrom(msg.sender, address(this), assets);
        _mint(msg.sender, shares);
    }

    function allocate(uint256 amount) external onlyKeeper {
        require(amount <= asset.balanceOf(address(this)), "idle");
        asset.transfer(address(strategy), amount);
        strategy.deposit(amount);
    }

    function redeem(uint256 shares) external returns (uint256 assets) {
        assets = shares * totalAssets() / totalSupply;
        _burn(msg.sender, shares);
        uint256 idle = asset.balanceOf(address(this));
        if (idle < assets) {
            strategy.withdraw(assets - idle);
        }
        asset.transfer(msg.sender, assets);
    }

    function _mint(address to, uint256 amount) internal {
        totalSupply += amount;
        balanceOf[to] += amount;
    }

    function _burn(address from, uint256 amount) internal {
        balanceOf[from] -= amount;   // reverts if `from` holds fewer shares
        totalSupply -= amount;
    }
}
""")
    c.source("example/ExampleStrategy.sol", """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {IERC20} from "./IERC20.sol";
import {IMarketL} from "./IMarketL.sol";

/// Example strategy: issues sU shares to its vault and supplies Token U to market L.
contract ExampleStrategy {
    IERC20 public immutable asset;               // Token U (example)
    IMarketL public immutable market;            // lending market L (interface only)
    address public immutable vault;

    mapping(address => uint256) public balanceOf;   // sU shares
    uint256 public totalSupply;

    modifier onlyVault() {
        require(msg.sender == vault, "!vault");
        _;
    }

    function totalAssets() public view returns (uint256) {
        return asset.balanceOf(address(this)) + market.balanceOf(address(this));
    }

    function deposit(uint256 amount) external onlyVault {
        uint256 supply = totalSupply;
        uint256 shares = supply == 0 ? amount : amount * supply / (totalAssets() - amount);
        totalSupply += shares;
        balanceOf[vault] += shares;
        asset.approve(address(market), amount);
        market.supply(address(asset), amount, address(this));
    }

    function withdraw(uint256 amount) external onlyVault {
        uint256 shares = amount * totalSupply / totalAssets();
        balanceOf[vault] -= shares;
        totalSupply -= shares;
        uint256 idle = asset.balanceOf(address(this));
        if (idle < amount) {
            market.withdraw(address(asset), amount - idle, address(this));
        }
        asset.transfer(vault, amount);
    }
}
""")
    c.source("example/IMarketL.sol", """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// Interface only: the implementation of lending market L is outside the analysis scope.
interface IMarketL {
    /// Pulls `amount` of `token` from msg.sender and credits receipt tokens (lU) to `onBehalfOf`.
    function supply(address token, uint256 amount, address onBehalfOf) external;

    /// Burns receipt tokens of msg.sender and sends `amount` of `token` to `to`.
    function withdraw(address token, uint256 amount, address to) external returns (uint256);

    function balanceOf(address account) external view returns (uint256);
}
""")
    V, ST, IM = "example/ExampleVault.sol", "example/ExampleStrategy.sol", "example/IMarketL.sol"

    # evidence
    c.ev_src("ev:a:vault-asset", "ExampleVault.asset (Token U)", V, "IERC20 public immutable asset", contract="ExampleVault")
    c.ev_src("ev:a:vault-shares", "vU share ledger", V, "mapping(address => uint256) public balanceOf", count=2, contract="ExampleVault")
    c.ev_src("ev:a:strategy-var", "ExampleVault.strategy", V, "ExampleStrategy public strategy", contract="ExampleVault")
    c.ev_src("ev:a:keeper", "onlyKeeper modifier", V, "modifier onlyKeeper", end="_;", contract="ExampleVault")
    c.ev_src("ev:a:paused", "whenNotPaused modifier", V, "modifier whenNotPaused", end="_;", contract="ExampleVault")
    c.ev_src("ev:a:totalassets", "ExampleVault.totalAssets()", V, "function totalAssets()", end="}", contract="ExampleVault", function="totalAssets()")
    c.ev_src("ev:a:deposit", "ExampleVault.deposit()", V, "function deposit(uint256 assets)", count=7, contract="ExampleVault", function="deposit(uint256)")
    c.ev_src("ev:a:deposit-zero", "require(assets > 0)", V, "require(assets > 0", contract="ExampleVault", function="deposit(uint256)")
    c.ev_src("ev:a:deposit-shares", "share amount", V, "shares = supply == 0", contract="ExampleVault", function="deposit(uint256)")
    c.ev_src("ev:a:deposit-pull", "asset.transferFrom(msg.sender, vault, assets)", V, "asset.transferFrom(msg.sender", contract="ExampleVault", function="deposit(uint256)")
    c.ev_src("ev:a:deposit-mint", "_mint(msg.sender, shares)", V, "_mint(msg.sender, shares)", contract="ExampleVault", function="deposit(uint256)")
    c.ev_src("ev:a:mint-impl", "ExampleVault._mint()", V, "function _mint(", end="}", contract="ExampleVault", function="_mint(address,uint256)")
    c.ev_src("ev:a:burn-impl", "ExampleVault._burn()", V, "function _burn(", end="}", contract="ExampleVault", function="_burn(address,uint256)")
    c.ev_src("ev:a:allocate", "ExampleVault.allocate()", V, "function allocate(", count=5, contract="ExampleVault", function="allocate(uint256)")
    c.ev_src("ev:a:allocate-idle", "require(amount <= idle)", V, "require(amount <= asset.balanceOf", contract="ExampleVault", function="allocate(uint256)")
    c.ev_src("ev:a:allocate-transfer", "asset.transfer(strategy, amount)", V, "asset.transfer(address(strategy)", contract="ExampleVault", function="allocate(uint256)")
    c.ev_src("ev:a:allocate-call", "strategy.deposit(amount)", V, "strategy.deposit(amount)", contract="ExampleVault", function="allocate(uint256)")
    c.ev_src("ev:a:redeem", "ExampleVault.redeem()", V, "function redeem(", count=9, contract="ExampleVault", function="redeem(uint256)")
    c.ev_src("ev:a:redeem-assets", "assets = shares * totalAssets() / totalSupply", V, "assets = shares * totalAssets()", contract="ExampleVault", function="redeem(uint256)")
    c.ev_src("ev:a:redeem-burn", "_burn(msg.sender, shares)", V, "_burn(msg.sender, shares)", contract="ExampleVault", function="redeem(uint256)")
    c.ev_src("ev:a:redeem-branch", "if (idle < assets) strategy.withdraw(...)", V, "if (idle < assets)", count=3, contract="ExampleVault", function="redeem(uint256)")
    c.ev_src("ev:a:redeem-pay", "asset.transfer(msg.sender, assets)", V, "asset.transfer(msg.sender, assets)", contract="ExampleVault", function="redeem(uint256)")
    c.ev_src("ev:a:s-onlyvault", "onlyVault modifier", ST, "modifier onlyVault", end="_;", contract="ExampleStrategy")
    c.ev_src("ev:a:s-shares", "sU share ledger", ST, "mapping(address => uint256) public balanceOf", count=2, contract="ExampleStrategy")
    c.ev_src("ev:a:s-totalassets", "ExampleStrategy.totalAssets()", ST, "function totalAssets()", end="}", contract="ExampleStrategy", function="totalAssets()")
    c.ev_src("ev:a:s-deposit", "ExampleStrategy.deposit()", ST, "function deposit(uint256 amount)", count=8, contract="ExampleStrategy", function="deposit(uint256)")
    c.ev_src("ev:a:s-mint", "sU credited to vault", ST, "totalSupply += shares", count=2, contract="ExampleStrategy", function="deposit(uint256)")
    c.ev_src("ev:a:s-supply", "market.supply(asset, amount, strategy)", ST, "market.supply(", contract="ExampleStrategy", function="deposit(uint256)")
    c.ev_src("ev:a:s-withdraw", "ExampleStrategy.withdraw()", ST, "function withdraw(uint256 amount)", count=10, contract="ExampleStrategy", function="withdraw(uint256)")
    c.ev_src("ev:a:s-burn", "sU debited from vault", ST, "balanceOf[vault] -= shares", count=2, contract="ExampleStrategy", function="withdraw(uint256)")
    c.ev_src("ev:a:s-mwithdraw", "market.withdraw(asset, amount - idle, strategy)", ST, "if (idle < amount)", count=3, contract="ExampleStrategy", function="withdraw(uint256)")
    c.ev_src("ev:a:s-pay", "asset.transfer(vault, amount)", ST, "asset.transfer(vault, amount)", contract="ExampleStrategy", function="withdraw(uint256)")
    c.ev_src("ev:a:imarket", "IMarketL interface", IM, "interface IMarketL", end="balanceOf(address account)", contract="IMarketL")
    c.ev_src("ev:a:imarket-supply", "IMarketL.supply()", IM, "/// Pulls `amount`", count=2, contract="IMarketL", function="supply(address,uint256,address)")
    c.ev_src("ev:a:imarket-withdraw", "IMarketL.withdraw()", IM, "/// Burns receipt tokens", count=2, contract="IMarketL", function="withdraw(address,uint256,address)")
    c.ev_exec("ev:a:tx-deposit", "transaction", "Deposit transaction (synthetic)", "0xexample000000000000000000000000000000000000000000000000000000a001", 1001,
              call_path="User → ExampleVault.deposit(1000e18)", data={"assets": "1000000000000000000000", "shares": "1000000000000000000000"},
              description="Synthetic trace of a first deposit; shares equal assets because totalSupply was 0.")
    c.ev_exec("ev:a:log-transfer", "log", "Transfer(User → Vault, 1000 U) log (synthetic)", "0xexample000000000000000000000000000000000000000000000000000000a001", 1001, log_index=0,
              data={"event": "Transfer", "from": "0x00000000000000000000000000000000000000aa", "to": "0x00000000000000000000000000000000000000b1", "value": "1000000000000000000000"})
    c.ev_exec("ev:a:tx-allocate", "trace", "Allocate trace (synthetic)", "0xexample000000000000000000000000000000000000000000000000000000a002", 1100,
              call_path="Keeper → ExampleVault.allocate(800e18) → ExampleStrategy.deposit(800e18) → MarketL.supply(U, 800e18, strategy)",
              data={"amount": "800000000000000000000"})
    c.ev_other("ev:a:cfg-binding", "config", "Deployment binding: ExampleVault.strategy = ExampleStrategy",
               description="Synthetic deployment configuration binding the vault's strategy variable to the ExampleStrategy instance.")
    c.ev_other("ev:a:llm-liquidity", "llm", "Model note on market liquidity",
               description="Suggested by a language model (synthetic example): redemption may revert if market L lacks liquidity; no check exists in ExampleStrategy.withdraw.",
               llm={"model": "example-model", "prompt_version": "example", "call_id": "example-1"})

    # assets
    c.add("assets", {"id": "asset:U", "label": "Token U (example)", "symbol": "U", "kind": "token", "standard": "ERC20",
                     "identity": {"kind": "example", "expression": "ExampleVault.asset", "address": "0x00000000000000000000000000000000000000c0", "chain_id": 31337},
                     "decimals": 18, "description": "Base asset deposited by users. Identity is the configured token address, not its name.",
                     "status": S("example", "manual", "supported"), "evidence": ["ev:a:vault-asset"]})
    c.add("assets", {"id": "asset:vU", "label": "vU vault share", "symbol": "vU", "kind": "share",
                     "identity": {"kind": "contract_self", "contract": "ExampleVault", "expression": "ExampleVault.balanceOf"},
                     "issuer": "ent:vault", "decimals": 18,
                     "description": "Share ledger kept inside ExampleVault. Represents a pro-rata claim on ExampleVault.totalAssets().",
                     "status": S("static", "rule", "supported", rule="share-ledger"), "evidence": ["ev:a:vault-shares", "ev:a:mint-impl", "ev:a:burn-impl"]})
    c.add("assets", {"id": "asset:sU", "label": "sU strategy share", "symbol": "sU", "kind": "share",
                     "identity": {"kind": "contract_self", "contract": "ExampleStrategy", "expression": "ExampleStrategy.balanceOf"},
                     "issuer": "ent:strategy",
                     "description": "Share ledger kept inside ExampleStrategy; only the vault is credited.",
                     "status": S("static", "rule", "supported", rule="share-ledger"), "evidence": ["ev:a:s-shares"]})
    c.add("assets", {"id": "asset:lU", "label": "lU market receipt", "symbol": "lU", "kind": "share",
                     "identity": {"kind": "scoped_expression", "expression": "IMarketL(ExampleStrategy.market) receipt"},
                     "issuer": "ent:marketL",
                     "description": "Receipt credited by lending market L. Semantics taken from interface comments only.",
                     "status": S("static", "adapter", "unresolved", rule="interface-doc",
                                 note="Implementation of market L is outside the analysis scope."),
                     "evidence": ["ev:a:imarket-supply"]})

    # entities
    c.add("entities", {"id": "ent:user", "label": "User (any account)", "kind": "account",
                       "description": "Any externally owned account or contract calling deposit / redeem.",
                       "expression": "msg.sender", "evidence": ["ev:a:deposit"]})
    c.add("entities", {"id": "ent:keeper", "label": "Keeper", "kind": "role", "expression": "ExampleVault.keeper",
                       "description": "Address stored in ExampleVault.keeper; only it can call allocate().", "evidence": ["ev:a:keeper"]})
    c.add("entities", {"id": "ent:sys", "label": "Example yield system", "kind": "protocol",
                       "description": "Contracts of the analysed project."})
    c.add("entities", {"id": "ent:vault", "label": "ExampleVault", "kind": "contract", "parent": "ent:sys",
                       "contract": {"name": "ExampleVault", "file": V, "address": "0x00000000000000000000000000000000000000b1", "chain_id": 31337},
                       "evidence": ["ev:a:deposit"]})
    c.add("entities", {"id": "ent:strategy", "label": "ExampleStrategy", "kind": "contract", "parent": "ent:sys",
                       "contract": {"name": "ExampleStrategy", "file": ST, "address": "0x00000000000000000000000000000000000000b2", "chain_id": 31337},
                       "evidence": ["ev:a:s-deposit"]})
    c.add("entities", {"id": "ent:marketL", "label": "Lending market L", "kind": "external_contract", "in_scope": False,
                       "contract": {"name": "IMarketL", "file": IM},
                       "description": "External lending market. Only its interface is available.", "evidence": ["ev:a:imarket"]})

    # operations ------------------------------------------------------------
    ex_obs = S("observed", "manual", "supported", note="Seen in the synthetic deposit transaction.")
    st_rule = S("static", "rule", "supported")
    c.add("operations", {
        "id": "op:deposit", "kind": "deposit", "label": "ExampleVault.deposit(assets)",
        "description": "User pays Token U to the vault and receives newly minted vU shares.",
        "entrypoint": {"contract": "ExampleVault", "function": "deposit", "signature": "deposit(uint256)", "visibility": "external"},
        "initiator": "ent:user", "executor": "ent:vault",
        "participants": [{"role": "caller", "entity": "ent:user", "expression": "msg.sender", "evidence": ["ev:a:deposit-pull"]},
                         {"role": "payer", "entity": "ent:user", "expression": "msg.sender", "evidence": ["ev:a:deposit-pull"]},
                         {"role": "share_receiver", "entity": "ent:user", "expression": "msg.sender", "evidence": ["ev:a:deposit-mint"]}],
        "conditions": [cond("c:a:dep-paused", "modifier", "Vault is not paused", "!paused", ["ev:a:paused"]),
                       cond("c:a:dep-zero", "require", "assets > 0", "assets > 0", ["ev:a:deposit-zero"])],
        "status": S("observed", "manual", "supported", checks=[
            chk("chk:a:dep-in", "An inflow of Token U from the caller precedes the mint", "pass", evidence=["ev:a:deposit-pull"]),
            chk("chk:a:dep-mint", "vU shares are minted to the same caller", "pass", evidence=["ev:a:deposit-mint"]),
        ]),
        "evidence": ["ev:a:deposit", "ev:a:tx-deposit"],
    })
    c.add("operations", {
        "id": "op:deposit.pull", "kind": "transfer", "label": "transferFrom U: user → vault", "parent": "op:deposit", "order": 1,
        "transaction": "same", "executor": "ent:vault", "initiator": "ent:vault",
        "inputs": [flow("f:a:dep-in", "asset:U", "ent:user", "ent:vault",
                        amt("asset:U", "assets", "1000", "decimal", note="value from synthetic transaction"),
                        "transferFrom", ["ev:a:deposit-pull", "ev:a:log-transfer"], status=ex_obs)],
        "status": ex_obs, "evidence": ["ev:a:deposit-pull", "ev:a:log-transfer"],
    })
    c.add("operations", {
        "id": "op:deposit.mint", "kind": "mint", "label": "mint vU → user", "parent": "op:deposit", "order": 2,
        "transaction": "same", "executor": "ent:vault",
        "outputs": [flow("f:a:dep-mint", "asset:vU", None, "ent:user",
                         amt("asset:vU", "supply == 0 ? assets : assets * supply / totalAssets()", "1000", "decimal"),
                         "mint", ["ev:a:deposit-mint", "ev:a:deposit-shares"], status=ex_obs)],
        "status": ex_obs, "evidence": ["ev:a:deposit-mint", "ev:a:mint-impl", "ev:a:tx-deposit"],
    })
    c.add("operations", {
        "id": "op:allocate", "kind": "allocate", "label": "ExampleVault.allocate(amount)",
        "description": "Keeper moves idle Token U from the vault into the strategy, which supplies it to market L. "
                       "Runs in a separate transaction from user deposits.",
        "entrypoint": {"contract": "ExampleVault", "function": "allocate", "signature": "allocate(uint256)", "visibility": "external"},
        "initiator": "ent:keeper", "executor": "ent:vault",
        "participants": [{"role": "caller", "entity": "ent:keeper", "expression": "msg.sender == keeper", "evidence": ["ev:a:keeper"]}],
        "conditions": [cond("c:a:alloc-keeper", "access_control", "Caller must be the keeper", "msg.sender == keeper", ["ev:a:keeper"]),
                       cond("c:a:alloc-idle", "require", "amount <= idle Token U in the vault", "amount <= asset.balanceOf(address(this))", ["ev:a:allocate-idle"])],
        "status": S("observed", "manual", "supported", note="Seen in the synthetic allocate trace."),
        "evidence": ["ev:a:allocate", "ev:a:tx-allocate"],
    })
    c.add("operations", {
        "id": "op:allocate.transfer", "kind": "transfer", "label": "transfer U: vault → strategy", "parent": "op:allocate", "order": 1,
        "transaction": "same", "executor": "ent:vault",
        "inputs": [flow("f:a:alloc-tx", "asset:U", "ent:vault", "ent:strategy", amt("asset:U", "amount", "800", "decimal"),
                        "transfer", ["ev:a:allocate-transfer"], status=S("observed", "manual", "supported"))],
        "status": S("observed", "manual", "supported"), "evidence": ["ev:a:allocate-transfer", "ev:a:tx-allocate"],
    })
    c.add("operations", {
        "id": "op:allocate.sdeposit", "kind": "deposit", "label": "ExampleStrategy.deposit(amount)", "parent": "op:allocate", "order": 2,
        "transaction": "same", "initiator": "ent:vault", "executor": "ent:strategy",
        "entrypoint": {"contract": "ExampleStrategy", "function": "deposit", "signature": "deposit(uint256)", "visibility": "external"},
        "participants": [{"role": "caller", "entity": "ent:vault", "expression": "msg.sender == vault", "evidence": ["ev:a:s-onlyvault"]},
                         {"role": "share_receiver", "entity": "ent:vault", "expression": "vault", "evidence": ["ev:a:s-mint"]}],
        "conditions": [cond("c:a:sdep-vault", "access_control", "Caller must be the vault", "msg.sender == vault", ["ev:a:s-onlyvault"])],
        "status": S("observed", "manual", "supported"), "evidence": ["ev:a:s-deposit", "ev:a:allocate-call"],
    })
    c.add("operations", {
        "id": "op:allocate.smint", "kind": "mint", "label": "credit sU → vault", "parent": "op:allocate.sdeposit", "order": 1,
        "transaction": "same", "executor": "ent:strategy",
        "outputs": [flow("f:a:smint", "asset:sU", None, "ent:vault", amt("asset:sU", "supply == 0 ? amount : amount * supply / (totalAssets() - amount)"),
                         "ledger-credit", ["ev:a:s-mint"], status=st_rule)],
        "status": st_rule, "evidence": ["ev:a:s-mint"],
    })
    c.add("operations", {
        "id": "op:allocate.supply", "kind": "deposit", "label": "IMarketL.supply(U, amount, strategy)", "parent": "op:allocate.sdeposit", "order": 2,
        "transaction": "same", "initiator": "ent:strategy", "executor": "ent:marketL",
        "participants": [{"role": "payer", "entity": "ent:strategy", "expression": "msg.sender"},
                         {"role": "receipt_receiver", "entity": "ent:strategy", "expression": "address(this)", "evidence": ["ev:a:s-supply"]}],
        "inputs": [flow("f:a:supply-in", "asset:U", "ent:strategy", "ent:marketL", amt("asset:U", "amount", "800", "decimal"),
                        "adapter:interface-doc", ["ev:a:s-supply", "ev:a:imarket-supply"], status=S("observed", "manual", "supported"))],
        "outputs": [flow("f:a:supply-out", "asset:lU", None, "ent:strategy", amt("asset:lU", "per market L exchange rate"),
                         "mint", ["ev:a:imarket-supply"], status=S("static", "adapter", "unresolved", note="Receipt minting is described by the interface only."))],
        "status": S("static", "adapter", "unresolved", rule="interface-doc"),
        "evidence": ["ev:a:s-supply", "ev:a:imarket-supply"], "unresolved": ["unr:a:market-impl"],
    })
    c.add("operations", {
        "id": "op:redeem", "kind": "redeem", "label": "ExampleVault.redeem(shares)",
        "description": "Holder burns vU shares and receives Token U. If the vault's idle balance is insufficient, "
                       "the vault first pulls the shortfall from the strategy, which may withdraw from market L.",
        "entrypoint": {"contract": "ExampleVault", "function": "redeem", "signature": "redeem(uint256)", "visibility": "external"},
        "initiator": "ent:user", "executor": "ent:vault",
        "participants": [{"role": "caller", "entity": "ent:user", "expression": "msg.sender"},
                         {"role": "share_owner", "entity": "ent:user", "expression": "msg.sender", "evidence": ["ev:a:redeem-burn"]},
                         {"role": "asset_receiver", "entity": "ent:user", "expression": "msg.sender", "evidence": ["ev:a:redeem-pay"]}],
        "conditions": [cond("c:a:red-bal", "require", "Caller holds at least `shares` vU (checked by the burn underflow)", "balanceOf[msg.sender] >= shares", ["ev:a:burn-impl"]),
                       cond("c:a:red-idle", "branch", "Idle Token U in the vault is below the redemption amount", "idle < assets", ["ev:a:redeem-branch"]),
                       cond("c:a:red-liq", "external", "Market L has enough liquidity to honour the withdrawal", None, ["ev:a:llm-liquidity"],
                            status=S("static", "llm", "pending"))],
        "status": S("static", "rule", "supported", checks=[
            chk("chk:a:red-burn", "vU of the caller is burned", "pass", evidence=["ev:a:redeem-burn"]),
            chk("chk:a:red-pay", "Token U is paid to the caller", "pass", evidence=["ev:a:redeem-pay"]),
            chk("chk:a:red-dep", "Payout depends on the burned share amount", "pass", "assets = shares * totalAssets() / totalSupply", ["ev:a:redeem-assets"]),
            chk("chk:a:red-ext", "All called implementations are in scope", "unknown", "market L withdraw is interface-only", ["ev:a:imarket-withdraw"]),
        ]),
        "evidence": ["ev:a:redeem"], "unresolved": ["unr:a:market-impl", "unr:a:liquidity"],
    })
    c.add("operations", {
        "id": "op:redeem.burn", "kind": "burn", "label": "burn vU from holder", "parent": "op:redeem", "order": 1, "transaction": "same",
        "executor": "ent:vault",
        "inputs": [flow("f:a:red-burn", "asset:vU", "ent:user", None, amt("asset:vU", "shares"), "burn", ["ev:a:redeem-burn", "ev:a:burn-impl"], status=st_rule)],
        "status": st_rule, "evidence": ["ev:a:redeem-burn"],
    })
    c.add("operations", {
        "id": "op:redeem.swithdraw", "kind": "withdraw", "label": "ExampleStrategy.withdraw(assets - idle)", "parent": "op:redeem", "order": 2,
        "transaction": "same", "initiator": "ent:vault", "executor": "ent:strategy",
        "entrypoint": {"contract": "ExampleStrategy", "function": "withdraw", "signature": "withdraw(uint256)", "visibility": "external"},
        "conditions": [cond("c:a:sw-branch", "branch", "Runs only if idle Token U in the vault < assets", "idle < assets", ["ev:a:redeem-branch"]),
                       cond("c:a:sw-vault", "access_control", "Caller must be the vault", "msg.sender == vault", ["ev:a:s-onlyvault"])],
        "status": st_rule, "evidence": ["ev:a:s-withdraw", "ev:a:redeem-branch"],
    })
    c.add("operations", {
        "id": "op:redeem.sburn", "kind": "burn", "label": "debit sU from vault", "parent": "op:redeem.swithdraw", "order": 1, "transaction": "same",
        "executor": "ent:strategy",
        "inputs": [flow("f:a:sburn", "asset:sU", "ent:vault", None, amt("asset:sU", "amount * totalSupply / totalAssets()"), "ledger-debit", ["ev:a:s-burn"], status=st_rule)],
        "status": st_rule, "evidence": ["ev:a:s-burn"],
    })
    c.add("operations", {
        "id": "op:redeem.mwithdraw", "kind": "withdraw", "label": "IMarketL.withdraw(U, amount - idle, strategy)", "parent": "op:redeem.swithdraw", "order": 2,
        "transaction": "same", "initiator": "ent:strategy", "executor": "ent:marketL",
        "conditions": [cond("c:a:mw-branch", "branch", "Runs only if idle Token U in the strategy < amount", "idle < amount", ["ev:a:s-mwithdraw"])],
        "inputs": [flow("f:a:mw-burn", "asset:lU", "ent:strategy", None, amt("asset:lU", "per market L exchange rate"), "burn", ["ev:a:imarket-withdraw"],
                        status=S("static", "adapter", "unresolved"))],
        "outputs": [flow("f:a:mw-out", "asset:U", "ent:marketL", "ent:strategy", amt("asset:U", "amount - idle"), "adapter:interface-doc",
                         ["ev:a:s-mwithdraw", "ev:a:imarket-withdraw"], status=S("static", "adapter", "unresolved"))],
        "status": S("static", "adapter", "unresolved", rule="interface-doc"), "evidence": ["ev:a:s-mwithdraw", "ev:a:imarket-withdraw"],
        "unresolved": ["unr:a:market-impl"],
    })
    c.add("operations", {
        "id": "op:redeem.spay", "kind": "transfer", "label": "transfer U: strategy → vault", "parent": "op:redeem.swithdraw", "order": 3, "transaction": "same",
        "executor": "ent:strategy",
        "inputs": [flow("f:a:spay", "asset:U", "ent:strategy", "ent:vault", amt("asset:U", "amount"), "transfer", ["ev:a:s-pay"], status=st_rule)],
        "status": st_rule, "evidence": ["ev:a:s-pay"],
    })
    c.add("operations", {
        "id": "op:redeem.pay", "kind": "transfer", "label": "transfer U: vault → holder", "parent": "op:redeem", "order": 3, "transaction": "same",
        "executor": "ent:vault",
        "outputs": [flow("f:a:red-pay", "asset:U", "ent:vault", "ent:user", amt("asset:U", "shares * totalAssets() / totalSupply"), "transfer",
                         ["ev:a:redeem-pay", "ev:a:redeem-assets"], status=st_rule)],
        "status": st_rule, "evidence": ["ev:a:redeem-pay"],
    })

    # positions ---------------------------------------------------------------
    c.add("positions", {"id": "pos:user-vU", "holder": "ent:user", "asset": "asset:vU", "kind": "share", "label": "vU held by user",
                        "amount": amt("asset:vU", "balanceOf[user]", "1000", "decimal", note="after the synthetic deposit"),
                        "description": "Pro-rata claim on everything the vault accounts for in totalAssets().",
                        "status": S("observed", "manual", "supported"), "evidence": ["ev:a:deposit-mint", "ev:a:tx-deposit"]})
    c.add("positions", {"id": "pos:vault-U", "holder": "ent:vault", "asset": "asset:U", "kind": "custody", "label": "Idle U in vault",
                        "amount": amt("asset:U", "asset.balanceOf(vault)", "200", "decimal", note="1000 deposited − 800 allocated"),
                        "status": S("observed", "manual", "supported"), "evidence": ["ev:a:totalassets", "ev:a:tx-allocate"]})
    c.add("positions", {"id": "pos:vault-sU", "holder": "ent:vault", "asset": "asset:sU", "kind": "share", "label": "sU held by vault",
                        "amount": amt("asset:sU", "strategy.balanceOf[vault]"),
                        "status": S("static", "rule", "supported"), "evidence": ["ev:a:s-mint"]})
    c.add("positions", {"id": "pos:strategy-U", "holder": "ent:strategy", "asset": "asset:U", "kind": "custody", "label": "Idle U in strategy",
                        "amount": amt("asset:U", "asset.balanceOf(strategy)"),
                        "status": S("static", "rule", "supported"), "evidence": ["ev:a:s-totalassets"]})
    c.add("positions", {"id": "pos:strategy-lU", "holder": "ent:strategy", "asset": "asset:lU", "kind": "share", "label": "lU held by strategy",
                        "amount": amt("asset:lU", "market.balanceOf(strategy)"),
                        "status": S("static", "adapter", "unresolved"), "evidence": ["ev:a:s-supply", "ev:a:s-totalassets"]})
    c.add("positions", {"id": "pos:market-U", "holder": "ent:marketL", "asset": "asset:U", "kind": "custody", "label": "U supplied to market L",
                        "amount": amt("asset:U", "unknown (market internals)"),
                        "description": "Held by market L and possibly lent to its borrowers; outside the analysis scope.",
                        "status": S("static", "adapter", "unresolved"), "evidence": ["ev:a:imarket-supply"]})

    # relations ---------------------------------------------------------------
    c.add("relations", {"id": "rel:a:claim-vU-vaultU", "kind": "claim", "source": "pos:user-vU", "target": "pos:vault-U",
                        "label": "pro-rata claim", "basis_detail": "assets = shares * totalAssets() / totalSupply; totalAssets() includes asset.balanceOf(vault)",
                        "status": S("static", "rule", "supported", rule="share-redeem-pro-rata", checks=[
                            chk("chk:a:c1-in", "Deposit links U inflow to vU mint", "pass", evidence=["ev:a:deposit-pull", "ev:a:deposit-mint"]),
                            chk("chk:a:c1-exit", "An exit burns vU and pays U", "pass", evidence=["ev:a:redeem-burn", "ev:a:redeem-pay"]),
                            chk("chk:a:c1-amt", "Payout amount depends on burned shares", "pass", evidence=["ev:a:redeem-assets"])]),
                        "evidence": ["ev:a:redeem-assets", "ev:a:totalassets"]})
    c.add("relations", {"id": "rel:a:claim-vU-sU", "kind": "claim", "source": "pos:user-vU", "target": "pos:vault-sU",
                        "label": "pro-rata claim (via strategy)", "basis_detail": "totalAssets() adds strategy.totalAssets(); redeem pulls the shortfall via strategy.withdraw",
                        "status": S("static", "rule", "supported", rule="share-redeem-pro-rata"),
                        "evidence": ["ev:a:totalassets", "ev:a:redeem-branch"]})
    c.add("relations", {"id": "rel:a:claim-sU-stratU", "kind": "claim", "source": "pos:vault-sU", "target": "pos:strategy-U",
                        "label": "claim", "basis_detail": "ExampleStrategy.totalAssets() includes asset.balanceOf(strategy)",
                        "status": S("static", "rule", "supported"), "evidence": ["ev:a:s-totalassets", "ev:a:s-pay"]})
    c.add("relations", {"id": "rel:a:claim-sU-lU", "kind": "claim", "source": "pos:vault-sU", "target": "pos:strategy-lU",
                        "label": "claim", "basis_detail": "ExampleStrategy.totalAssets() includes market.balanceOf(strategy)",
                        "status": S("static", "rule", "supported"), "evidence": ["ev:a:s-totalassets", "ev:a:s-mwithdraw"]})
    c.add("relations", {"id": "rel:a:claim-lU-marketU", "kind": "claim", "source": "pos:strategy-lU", "target": "pos:market-U",
                        "label": "redeemable (per interface)", "basis_detail": "IMarketL.withdraw burns receipts and returns the token",
                        "status": S("static", "adapter", "unresolved", note="Depends on the unseen market implementation."),
                        "evidence": ["ev:a:imarket-withdraw"]})
    c.add("relations", {"id": "rel:a:dep-vU-strategy", "kind": "depends_on", "source": "asset:vU", "target": "ent:strategy",
                        "label": "share value reads strategy.totalAssets()",
                        "status": S("static", "rule", "supported"), "evidence": ["ev:a:totalassets"]})
    c.add("relations", {"id": "rel:a:dep-sU-market", "kind": "depends_on", "source": "asset:sU", "target": "ent:marketL",
                        "label": "share value reads market.balanceOf()",
                        "status": S("static", "rule", "supported"), "evidence": ["ev:a:s-totalassets"]})
    c.add("relations", {"id": "rel:a:dep-lU-market", "kind": "depends_on", "source": "asset:lU", "target": "ent:marketL",
                        "label": "redeemability depends on market liquidity",
                        "status": S("static", "llm", "pending"), "evidence": ["ev:a:llm-liquidity"]})
    c.add("relations", {"id": "rel:a:bind-strategy", "kind": "binding", "source": "ent:vault", "target": "ent:strategy",
                        "label": "ExampleVault.strategy", "status": S("config", "config", "supported"),
                        "evidence": ["ev:a:strategy-var", "ev:a:cfg-binding"]})
    c.add("relations", {"id": "rel:a:calls-vs", "kind": "calls", "source": "ent:vault", "target": "ent:strategy",
                        "label": "deposit / withdraw / totalAssets", "status": S("static", "rule", "supported"),
                        "evidence": ["ev:a:allocate-call", "ev:a:redeem-branch"]})
    c.add("relations", {"id": "rel:a:calls-sm", "kind": "calls", "source": "ent:strategy", "target": "ent:marketL",
                        "label": "supply / withdraw / balanceOf", "status": S("static", "rule", "supported"),
                        "evidence": ["ev:a:s-supply", "ev:a:s-mwithdraw"]})
    c.add("relations", {"id": "rel:a:exit-vU", "kind": "exit", "source": "pos:user-vU", "target": "op:redeem",
                        "label": "redeem", "status": S("static", "rule", "supported"), "evidence": ["ev:a:redeem"]})
    c.add("relations", {"id": "rel:a:exit-sU", "kind": "exit", "source": "pos:vault-sU", "target": "op:redeem.swithdraw",
                        "label": "strategy withdraw (vault only)", "status": S("static", "rule", "supported"), "evidence": ["ev:a:s-withdraw"]})
    c.add("relations", {"id": "rel:a:holds-user", "kind": "holds", "source": "ent:user", "target": "pos:user-vU",
                        "label": "holds", "status": S("observed", "manual", "supported"), "evidence": ["ev:a:tx-deposit"]})

    # exits -------------------------------------------------------------------
    c.add("exits", {
        "id": "exit:a:vU", "position": "pos:user-vU", "label": "Redeem vU for Token U",
        "steps": [
            {"order": 1, "operation": "op:redeem", "actor": "ent:user", "transaction": "same",
             "description": "Holder calls redeem(shares); shares are burned from msg.sender, so only the holder can exit its own position.",
             "conditions": [cond("c:a:x1", "require", "Caller holds at least `shares` vU", "balanceOf[msg.sender] >= shares", ["ev:a:burn-impl"])],
             "evidence": ["ev:a:redeem", "ev:a:redeem-burn"]},
            {"order": 2, "operation": "op:redeem.swithdraw", "actor": "ent:vault", "transaction": "same", "optional": True,
             "description": "Only if the vault's idle U is below the redemption amount.",
             "conditions": [cond("c:a:x2", "branch", "idle U in vault < assets", "idle < assets", ["ev:a:redeem-branch"])],
             "evidence": ["ev:a:redeem-branch", "ev:a:s-withdraw"]},
            {"order": 3, "operation": "op:redeem.mwithdraw", "actor": "ent:strategy", "transaction": "same", "optional": True,
             "description": "Only if the strategy's idle U is below the requested amount; relies on market L liquidity.",
             "conditions": [cond("c:a:x3", "branch", "idle U in strategy < amount", "idle < amount", ["ev:a:s-mwithdraw"]),
                            cond("c:a:x3b", "external", "Market L can pay out (implementation not analysed)", None, ["ev:a:imarket-withdraw"])],
             "evidence": ["ev:a:s-mwithdraw"]},
            {"order": 4, "operation": "op:redeem.pay", "actor": "ent:vault", "transaction": "same",
             "description": "Vault transfers Token U to the holder.", "evidence": ["ev:a:redeem-pay"]},
        ],
        "consumes": [flow("f:a:x-c", "asset:vU", "ent:user", None, amt("asset:vU", "shares"), "burn", ["ev:a:redeem-burn"])],
        "returns": [flow("f:a:x-r", "asset:U", "ent:vault", "ent:user", amt("asset:U", "shares * totalAssets() / totalSupply"), "transfer", ["ev:a:redeem-pay"])],
        "conditions": [cond("c:a:x-all", "external", "Downstream liquidity in market L when the vault and strategy idle balances are insufficient", None, ["ev:a:llm-liquidity"])],
        "status": S("static", "rule", "partially_supported", checks=[
            chk("chk:a:x-actor", "Exit actor equals share owner (burn from msg.sender)", "pass", evidence=["ev:a:redeem-burn"]),
            chk("chk:a:x-recv", "Asset receiver equals caller", "pass", evidence=["ev:a:redeem-pay"]),
            chk("chk:a:x-ext", "Downstream withdrawal implementation analysed", "unknown", "IMarketL is interface-only", ["ev:a:imarket"])]),
        "evidence": ["ev:a:redeem"], "unresolved": ["unr:a:market-impl", "unr:a:liquidity"],
    })
    c.add("exits", {
        "id": "exit:a:sU", "position": "pos:vault-sU", "label": "Vault withdraws from strategy",
        "steps": [{"order": 1, "operation": "op:redeem.swithdraw", "actor": "ent:vault", "transaction": "same",
                   "description": "Only callable by the vault (onlyVault); in this code it is reached through ExampleVault.redeem.",
                   "conditions": [cond("c:a:xs1", "access_control", "msg.sender == vault", "msg.sender == vault", ["ev:a:s-onlyvault"])],
                   "evidence": ["ev:a:s-withdraw"]},
                  {"order": 2, "operation": "op:redeem.mwithdraw", "actor": "ent:strategy", "transaction": "same", "optional": True,
                   "conditions": [cond("c:a:xs2", "branch", "idle U in strategy < amount", "idle < amount", ["ev:a:s-mwithdraw"])],
                   "evidence": ["ev:a:s-mwithdraw"]}],
        "returns": [flow("f:a:xs-r", "asset:U", "ent:strategy", "ent:vault", amt("asset:U", "amount"), "transfer", ["ev:a:s-pay"])],
        "status": S("static", "rule", "partially_supported"), "evidence": ["ev:a:s-withdraw"], "unresolved": ["unr:a:market-impl"],
    })

    # unresolved --------------------------------------------------------------
    c.add("unresolved", {"id": "unr:a:market-impl", "kind": "external_implementation",
                         "text": "Lending market L is only available as the IMarketL interface. Receipt minting, exchange rate and withdrawal behaviour are taken from interface comments.",
                         "subjects": ["ent:marketL", "asset:lU", "op:allocate.supply", "op:redeem.mwithdraw"], "evidence": ["ev:a:imarket"],
                         "suggestion": "Add the market implementation to the analysis scope or bind it to a known adapter."})
    c.add("unresolved", {"id": "unr:a:liquidity", "kind": "llm_question",
                         "text": "Can redemption revert when market L has insufficient liquidity? ExampleStrategy.withdraw has no fallback.",
                         "subjects": ["exit:a:vU", "op:redeem.mwithdraw"], "evidence": ["ev:a:llm-liquidity", "ev:a:s-mwithdraw"]})

    # tasks -------------------------------------------------------------------
    c.add("tasks", {"id": "task:a:holder", "question": "Who actually holds the Token U after a deposit and an allocation?",
                    "focus": ["asset:U"], "view": "holdings",
                    "answer": "ExampleVault (idle), ExampleStrategy (idle) and lending market L (supplied). The user holds vU shares — a claim on these positions, not Token U itself."})
    c.add("tasks", {"id": "task:a:share", "question": "What does the user's vU position represent, and which positions back it?",
                    "focus": ["pos:user-vU"], "view": "holdings",
                    "answer": "A pro-rata claim on ExampleVault.totalAssets(): idle U in the vault plus sU held by the vault, which in turn claims idle U in the strategy and lU at market L."})
    c.add("tasks", {"id": "task:a:deps", "question": "Which contracts and protocols does the vU share depend on?",
                    "focus": ["asset:vU"], "view": "dependencies",
                    "answer": "ExampleStrategy (totalAssets) and, through sU, lending market L, whose implementation is outside the analysis scope."})
    c.add("tasks", {"id": "task:a:exit", "question": "Which operations are needed to exit a vU position and who performs them?",
                    "focus": ["exit:a:vU"], "view": "exits",
                    "answer": "The holder calls redeem; the vault may call strategy.withdraw if idle U is insufficient; the strategy may withdraw from market L; the vault pays U to the holder. All in one transaction."})
    c.add("tasks", {"id": "task:a:status", "question": "Which steps were observed in a transaction and which are only static candidates?",
                    "focus": ["op:deposit", "op:allocate", "op:redeem"], "view": "flows",
                    "answer": "Deposit and allocate were observed (synthetic traces); redeem and the market-L withdrawal are static candidates."})
    return c


# =============================================================================
# Example B — swap + multi-asset liquidity
# =============================================================================


def example_swap_lp() -> Case:
    c = Case(
        meta={
            "id": "example-swap-lp",
            "title": "Example B — Swap branches and multi-asset liquidity (synthetic)",
            "description": "A zap contract splits the user's Token X into two swap branches (X→Y in pool XY, X→Z in pool XZ), "
                           "merges Y and Z into pool YZ to mint LP-YZ for the user, and lets the user stake the LP in a farm. "
                           "Exiting the LP returns both Y and Z.",
            "synthetic": True,
            "created_at": "2026-10-20T00:00:00Z",
            "generator": {"name": "examples/generate.py", "version": "0.1.0"},
            "tags": ["example", "swap", "liquidity", "multi-asset"],
        },
        scope={
            "kind": "example",
            "project": {"name": "Example zap & AMM (synthetic)", "version": "example-1",
                        "entry_contracts": ["ExampleZap", "ExamplePair", "ExampleFarm"],
                        "files": ["example/ExampleZap.sol", "example/ExamplePair.sol", "example/ExampleFarm.sol"]},
            "chain": {"chain_id": 31337, "name": "local test chain (synthetic)", "block": 2400},
            "components": [{"name": "hand-written example", "role": "example"}],
            "boundaries": [{"id": "bnd:b:reward", "target": "Farm reward token",
                            "reason": "Reward emission is not modelled in this example.", "evidence": ["ev:b:farm"]}],
            "assumptions": [{"id": "asm:b:pairs", "text": "All three pools run the same ExamplePair code (constant-product AMM).", "source": "example"}],
            "notes": [EXAMPLE_SCOPE_NOTE],
        },
    )
    c.source("example/ExampleZap.sol", """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {IERC20} from "./IERC20.sol";
import {ExamplePair} from "./ExamplePair.sol";

/// Example zap: turns Token X into LP-YZ in one transaction.
contract ExampleZap {
    IERC20 public immutable tokenX;
    IERC20 public immutable tokenY;
    IERC20 public immutable tokenZ;
    ExamplePair public immutable pairXY;   // swaps X <-> Y
    ExamplePair public immutable pairXZ;   // swaps X <-> Z
    ExamplePair public immutable pairYZ;   // liquidity pool, issues LP-YZ

    function zapIn(uint256 amountX, uint256 minLp) external returns (uint256 lp) {
        tokenX.transferFrom(msg.sender, address(this), amountX);
        uint256 half = amountX / 2;

        // branch 1: X -> Y
        tokenX.transfer(address(pairXY), half);
        uint256 outY = pairXY.swap(address(tokenX), address(this));

        // branch 2: X -> Z
        tokenX.transfer(address(pairXZ), amountX - half);
        uint256 outZ = pairXZ.swap(address(tokenX), address(this));

        // merge: Y + Z -> LP-YZ, minted directly to the caller
        tokenY.transfer(address(pairYZ), outY);
        tokenZ.transfer(address(pairYZ), outZ);
        lp = pairYZ.mint(msg.sender);
        require(lp >= minLp, "slippage");

        _refund(tokenY, msg.sender);
        _refund(tokenZ, msg.sender);
    }

    function _refund(IERC20 token, address to) internal {
        uint256 dust = token.balanceOf(address(this));
        if (dust > 0) {
            token.transfer(to, dust);
        }
    }
}
""")
    c.source("example/ExamplePair.sol", """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {IERC20} from "./IERC20.sol";

/// Example constant-product pair. The pair's own ERC-20 balance ledger is the LP token.
contract ExamplePair {
    IERC20 public immutable token0;
    IERC20 public immutable token1;
    uint256 public reserve0;
    uint256 public reserve1;
    mapping(address => uint256) public balanceOf;   // LP shares
    uint256 public totalSupply;

    /// Swaps whatever amount of `tokenIn` was sent to the pair; sends the output to `to`.
    function swap(address tokenIn, address to) external returns (uint256 out) {
        bool zeroIn = tokenIn == address(token0);
        uint256 amountIn = (zeroIn ? token0 : token1).balanceOf(address(this)) - (zeroIn ? reserve0 : reserve1);
        uint256 rIn = zeroIn ? reserve0 : reserve1;
        uint256 rOut = zeroIn ? reserve1 : reserve0;
        out = amountIn * 997 * rOut / (rIn * 1000 + amountIn * 997);
        (zeroIn ? token1 : token0).transfer(to, out);
        _sync();
    }

    /// Mints LP for the tokens sent since the last sync.
    function mint(address to) external returns (uint256 liquidity) {
        uint256 a0 = token0.balanceOf(address(this)) - reserve0;
        uint256 a1 = token1.balanceOf(address(this)) - reserve1;
        liquidity = _min(a0 * totalSupply / reserve0, a1 * totalSupply / reserve1);
        totalSupply += liquidity;
        balanceOf[to] += liquidity;
        _sync();
    }

    /// Burns LP sent to the pair and returns both tokens pro rata.
    function burn(address to) external returns (uint256 amount0, uint256 amount1) {
        uint256 liquidity = balanceOf[address(this)];
        amount0 = liquidity * reserve0 / totalSupply;
        amount1 = liquidity * reserve1 / totalSupply;
        balanceOf[address(this)] -= liquidity;
        totalSupply -= liquidity;
        token0.transfer(to, amount0);
        token1.transfer(to, amount1);
        _sync();
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        return true;
    }

    function _sync() internal {
        reserve0 = token0.balanceOf(address(this));
        reserve1 = token1.balanceOf(address(this));
    }

    function _min(uint256 a, uint256 b) internal pure returns (uint256) {
        return a < b ? a : b;
    }
}
""")
    c.source("example/ExampleFarm.sol", """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {IERC20} from "./IERC20.sol";

/// Example farm: records staked LP-YZ in an internal ledger (not a token).
contract ExampleFarm {
    IERC20 public immutable lpToken;              // LP-YZ
    mapping(address => uint256) public staked;    // internal accounting, not transferable

    function stake(uint256 amount) external {
        lpToken.transferFrom(msg.sender, address(this), amount);
        staked[msg.sender] += amount;
    }

    function unstake(uint256 amount) external {
        staked[msg.sender] -= amount;
        lpToken.transfer(msg.sender, amount);
    }
}
""")
    Z, P, F = "example/ExampleZap.sol", "example/ExamplePair.sol", "example/ExampleFarm.sol"
    TX = "0xexample000000000000000000000000000000000000000000000000000000b001"
    c.ev_src("ev:b:zap", "ExampleZap.zapIn()", Z, "function zapIn(", end="_refund(tokenZ", contract="ExampleZap", function="zapIn(uint256,uint256)")
    c.ev_src("ev:b:pull", "tokenX.transferFrom(msg.sender, zap, amountX)", Z, "tokenX.transferFrom(msg.sender", contract="ExampleZap", function="zapIn(uint256,uint256)")
    c.ev_src("ev:b:half", "split amountX in two branches", Z, "uint256 half = amountX / 2", contract="ExampleZap", function="zapIn(uint256,uint256)")
    c.ev_src("ev:b:br1", "branch 1: X → Y via pair XY", Z, "// branch 1", count=3, contract="ExampleZap", function="zapIn(uint256,uint256)")
    c.ev_src("ev:b:br2", "branch 2: X → Z via pair XZ", Z, "// branch 2", count=3, contract="ExampleZap", function="zapIn(uint256,uint256)")
    c.ev_src("ev:b:merge", "merge: Y + Z → pair YZ, LP minted to caller", Z, "// merge", count=4, contract="ExampleZap", function="zapIn(uint256,uint256)")
    c.ev_src("ev:b:lp-mint-call", "pairYZ.mint(msg.sender)", Z, "lp = pairYZ.mint(msg.sender)", contract="ExampleZap", function="zapIn(uint256,uint256)")
    c.ev_src("ev:b:slippage", "require(lp >= minLp)", Z, "require(lp >= minLp", contract="ExampleZap", function="zapIn(uint256,uint256)")
    c.ev_src("ev:b:refund", "_refund leftovers to caller", Z, "function _refund(", end="}", contract="ExampleZap", function="_refund(address,address)")
    c.ev_src("ev:b:pair-ledger", "LP ledger in ExamplePair", P, "mapping(address => uint256) public balanceOf", count=2, contract="ExamplePair")
    c.ev_src("ev:b:pair-swap", "ExamplePair.swap()", P, "function swap(", end="_sync();", contract="ExamplePair", function="swap(address,address)")
    c.ev_src("ev:b:pair-mint", "ExamplePair.mint()", P, "function mint(", end="_sync();", contract="ExamplePair", function="mint(address)", occurrence=1)
    c.ev_src("ev:b:pair-burn", "ExamplePair.burn()", P, "function burn(", count=10, contract="ExamplePair", function="burn(address)")
    c.ev_src("ev:b:pair-burn-amt", "amounts pro rata to LP burned", P, "amount0 = liquidity * reserve0", count=2, contract="ExamplePair", function="burn(address)")
    c.ev_src("ev:b:pair-reserves", "reserves", P, "uint256 public reserve0", count=2, contract="ExamplePair")
    c.ev_src("ev:b:farm", "ExampleFarm", F, "contract ExampleFarm", end="internal accounting", contract="ExampleFarm")
    c.ev_src("ev:b:stake", "ExampleFarm.stake()", F, "function stake(", count=4, contract="ExampleFarm", function="stake(uint256)")
    c.ev_src("ev:b:unstake", "ExampleFarm.unstake()", F, "function unstake(", count=4, contract="ExampleFarm", function="unstake(uint256)")
    c.ev_exec("ev:b:tx-zap", "trace", "zapIn trace (synthetic)", TX, 2301,
              call_path="User → ExampleZap.zapIn(100e18, 1) → PairXY.swap → PairXZ.swap → PairYZ.mint(user)",
              data={"amountX": "100000000000000000000", "outY": "49100000000000000000", "outZ": "24300000000000000000", "lp": "34500000000000000000"})
    c.ev_exec("ev:b:log-swap1", "log", "Swap(X→Y) log at pair XY (synthetic)", TX, 2301, log_index=3, data={"event": "Swap", "in": "50 X", "out": "49.1 Y"})
    c.ev_exec("ev:b:log-swap2", "log", "Swap(X→Z) log at pair XZ (synthetic)", TX, 2301, log_index=6, data={"event": "Swap", "in": "50 X", "out": "24.3 Z"})
    c.ev_exec("ev:b:log-mint", "log", "Mint(LP-YZ → user) log (synthetic)", TX, 2301, log_index=9, data={"event": "Mint", "to": "user", "liquidity": "34.5"})

    obs = S("observed", "manual", "supported", note="Seen in the synthetic zapIn trace.")
    st = S("static", "rule", "supported")
    for aid, label, sym in [("asset:X", "Token X (example)", "X"), ("asset:Y", "Token Y (example)", "Y"), ("asset:Z", "Token Z (example)", "Z")]:
        c.add("assets", {"id": aid, "label": label, "symbol": sym, "kind": "token", "standard": "ERC20",
                         "identity": {"kind": "example", "expression": f"ExampleZap.token{sym}", "chain_id": 31337},
                         "status": S("example", "manual", "supported"), "evidence": ["ev:b:zap"]})
    c.add("assets", {"id": "asset:LP", "label": "LP-YZ pool share", "symbol": "LP-YZ", "kind": "lp_token",
                     "identity": {"kind": "contract_self", "contract": "ExamplePair (pair YZ)", "expression": "pairYZ.balanceOf"},
                     "issuer": "ent:pairYZ", "description": "Pro-rata claim on both reserves of pair YZ.",
                     "status": S("static", "rule", "supported", rule="share-ledger"), "evidence": ["ev:b:pair-ledger", "ev:b:pair-mint"]})
    c.add("assets", {"id": "asset:stake", "label": "Farm stake record", "kind": "accounting",
                     "identity": {"kind": "contract_self", "contract": "ExampleFarm", "expression": "ExampleFarm.staked"},
                     "issuer": "ent:farm", "description": "Internal, non-transferable ledger of staked LP-YZ.",
                     "status": S("static", "rule", "supported"), "evidence": ["ev:b:farm"]})

    c.add("entities", {"id": "ent:b:user", "label": "User (any account)", "kind": "account", "expression": "msg.sender", "evidence": ["ev:b:pull"]})
    c.add("entities", {"id": "ent:zap", "label": "ExampleZap", "kind": "contract", "contract": {"name": "ExampleZap", "file": Z}, "evidence": ["ev:b:zap"]})
    c.add("entities", {"id": "ent:amm", "label": "Example AMM", "kind": "protocol", "description": "Three instances of ExamplePair."})
    c.add("entities", {"id": "ent:pairXY", "label": "Pair XY", "kind": "contract", "parent": "ent:amm", "contract": {"name": "ExamplePair", "file": P}, "evidence": ["ev:b:pair-swap"]})
    c.add("entities", {"id": "ent:pairXZ", "label": "Pair XZ", "kind": "contract", "parent": "ent:amm", "contract": {"name": "ExamplePair", "file": P}, "evidence": ["ev:b:pair-swap"]})
    c.add("entities", {"id": "ent:pairYZ", "label": "Pair YZ (LP issuer)", "kind": "contract", "parent": "ent:amm", "contract": {"name": "ExamplePair", "file": P}, "evidence": ["ev:b:pair-mint"]})
    c.add("entities", {"id": "ent:farm", "label": "ExampleFarm", "kind": "contract", "contract": {"name": "ExampleFarm", "file": F}, "evidence": ["ev:b:farm"]})
    c.add("entities", {"id": "ent:xyLPs", "label": "Liquidity providers of pairs XY / XZ", "kind": "role",
                       "description": "Owners of the swap pools' reserves; not modelled in detail."})

    c.add("operations", {
        "id": "op:zap", "kind": "add_liquidity", "label": "ExampleZap.zapIn(amountX, minLp)",
        "description": "Single transaction: pull X, two swap branches, merge into pair YZ, mint LP to the caller, refund dust.",
        "entrypoint": {"contract": "ExampleZap", "function": "zapIn", "signature": "zapIn(uint256,uint256)", "visibility": "external"},
        "initiator": "ent:b:user", "executor": "ent:zap",
        "participants": [{"role": "caller", "entity": "ent:b:user", "expression": "msg.sender"},
                         {"role": "payer", "entity": "ent:b:user", "expression": "msg.sender", "evidence": ["ev:b:pull"]},
                         {"role": "lp_receiver", "entity": "ent:b:user", "expression": "msg.sender", "evidence": ["ev:b:lp-mint-call"]}],
        "conditions": [cond("c:b:slip", "require", "Minted LP ≥ minLp (slippage bound)", "lp >= minLp", ["ev:b:slippage"])],
        "status": obs, "evidence": ["ev:b:zap", "ev:b:tx-zap"],
    })
    c.add("operations", {"id": "op:zap.pull", "kind": "transfer", "label": "transferFrom X: user → zap", "parent": "op:zap", "order": 1, "transaction": "same",
                         "executor": "ent:zap",
                         "inputs": [flow("f:b:pull", "asset:X", "ent:b:user", "ent:zap", amt("asset:X", "amountX", "100", "decimal"), "transferFrom", ["ev:b:pull"], status=obs)],
                         "status": obs, "evidence": ["ev:b:pull"]})
    c.add("operations", {"id": "op:zap.swapY", "kind": "swap", "label": "swap X → Y (pair XY)", "parent": "op:zap", "order": 2, "transaction": "same",
                         "initiator": "ent:zap", "executor": "ent:pairXY",
                         "description": "Branch 1 of the split.",
                         "inputs": [flow("f:b:s1in", "asset:X", "ent:zap", "ent:pairXY", amt("asset:X", "half = amountX / 2", "50", "decimal"), "transfer", ["ev:b:br1", "ev:b:log-swap1"], status=obs)],
                         "outputs": [flow("f:b:s1out", "asset:Y", "ent:pairXY", "ent:zap", amt("asset:Y", "outY (constant-product formula)", "49.1", "decimal"), "transfer", ["ev:b:pair-swap", "ev:b:log-swap1"], status=obs)],
                         "status": obs, "evidence": ["ev:b:br1", "ev:b:pair-swap", "ev:b:log-swap1"]})
    c.add("operations", {"id": "op:zap.swapZ", "kind": "swap", "label": "swap X → Z (pair XZ)", "parent": "op:zap", "order": 3, "transaction": "same",
                         "initiator": "ent:zap", "executor": "ent:pairXZ",
                         "description": "Branch 2 of the split.",
                         "inputs": [flow("f:b:s2in", "asset:X", "ent:zap", "ent:pairXZ", amt("asset:X", "amountX - half", "50", "decimal"), "transfer", ["ev:b:br2", "ev:b:log-swap2"], status=obs)],
                         "outputs": [flow("f:b:s2out", "asset:Z", "ent:pairXZ", "ent:zap", amt("asset:Z", "outZ (constant-product formula)", "24.3", "decimal"), "transfer", ["ev:b:pair-swap", "ev:b:log-swap2"], status=obs)],
                         "status": obs, "evidence": ["ev:b:br2", "ev:b:pair-swap", "ev:b:log-swap2"]})
    c.add("operations", {"id": "op:zap.addliq", "kind": "add_liquidity", "label": "add Y + Z to pair YZ, mint LP", "parent": "op:zap", "order": 4, "transaction": "same",
                         "initiator": "ent:zap", "executor": "ent:pairYZ",
                         "description": "Merge of both branches: two input assets, one LP output.",
                         "inputs": [flow("f:b:al-y", "asset:Y", "ent:zap", "ent:pairYZ", amt("asset:Y", "outY", "49.1", "decimal"), "transfer", ["ev:b:merge"], status=obs),
                                    flow("f:b:al-z", "asset:Z", "ent:zap", "ent:pairYZ", amt("asset:Z", "outZ", "24.3", "decimal"), "transfer", ["ev:b:merge"], status=obs)],
                         "outputs": [flow("f:b:al-lp", "asset:LP", None, "ent:b:user", amt("asset:LP", "min(a0 * totalSupply / reserve0, a1 * totalSupply / reserve1)", "34.5", "decimal"), "mint",
                                          ["ev:b:pair-mint", "ev:b:lp-mint-call", "ev:b:log-mint"], status=obs)],
                         "status": obs, "evidence": ["ev:b:merge", "ev:b:pair-mint", "ev:b:log-mint"]})
    c.add("operations", {"id": "op:zap.refund", "kind": "transfer", "label": "refund leftover Y / Z", "parent": "op:zap", "order": 5, "transaction": "same",
                         "executor": "ent:zap",
                         "conditions": [cond("c:b:dust", "branch", "Only if the zap holds a non-zero balance", "dust > 0", ["ev:b:refund"])],
                         "outputs": [flow("f:b:rf-y", "asset:Y", "ent:zap", "ent:b:user", amt("asset:Y", "dust"), "transfer", ["ev:b:refund"], ["c:b:dust"], status=st),
                                     flow("f:b:rf-z", "asset:Z", "ent:zap", "ent:b:user", amt("asset:Z", "dust"), "transfer", ["ev:b:refund"], ["c:b:dust"], status=st)],
                         "status": st, "evidence": ["ev:b:refund"]})
    c.add("operations", {"id": "op:stake", "kind": "stake", "label": "ExampleFarm.stake(amount)", "initiator": "ent:b:user", "executor": "ent:farm",
                         "description": "Separate, optional transaction: LP-YZ moves into the farm, the user gets an internal stake record.",
                         "entrypoint": {"contract": "ExampleFarm", "function": "stake", "signature": "stake(uint256)", "visibility": "external"},
                         "participants": [{"role": "caller", "entity": "ent:b:user", "expression": "msg.sender"}],
                         "inputs": [flow("f:b:st-in", "asset:LP", "ent:b:user", "ent:farm", amt("asset:LP", "amount"), "transferFrom", ["ev:b:stake"], status=st)],
                         "outputs": [flow("f:b:st-rec", "asset:stake", None, "ent:b:user", amt("asset:stake", "amount"), "ledger-credit", ["ev:b:stake"], status=st)],
                         "status": st, "evidence": ["ev:b:stake"]})
    c.add("operations", {"id": "op:unstake", "kind": "unstake", "label": "ExampleFarm.unstake(amount)", "initiator": "ent:b:user", "executor": "ent:farm",
                         "entrypoint": {"contract": "ExampleFarm", "function": "unstake", "signature": "unstake(uint256)", "visibility": "external"},
                         "conditions": [cond("c:b:unst", "require", "Caller has at least `amount` staked (underflow check)", "staked[msg.sender] >= amount", ["ev:b:unstake"])],
                         "inputs": [flow("f:b:un-rec", "asset:stake", "ent:b:user", None, amt("asset:stake", "amount"), "ledger-debit", ["ev:b:unstake"], status=st)],
                         "outputs": [flow("f:b:un-out", "asset:LP", "ent:farm", "ent:b:user", amt("asset:LP", "amount"), "transfer", ["ev:b:unstake"], status=st)],
                         "status": st, "evidence": ["ev:b:unstake"]})
    c.add("operations", {"id": "op:removeliq", "kind": "remove_liquidity", "label": "PairYZ.burn(to) — remove liquidity",
                         "description": "User sends LP-YZ to the pair and calls burn; the pair returns Y and Z pro rata (two outputs).",
                         "initiator": "ent:b:user", "executor": "ent:pairYZ",
                         "entrypoint": {"contract": "ExamplePair", "function": "burn", "signature": "burn(address)", "visibility": "external"},
                         "participants": [{"role": "caller", "entity": "ent:b:user", "expression": "msg.sender"},
                                          {"role": "asset_receiver", "entity": "ent:b:user", "expression": "to"}],
                         "conditions": [cond("c:b:burn-pre", "state", "LP must first be transferred to the pair (separate call in the same or an earlier transaction)", "balanceOf[address(this)]", ["ev:b:pair-burn"])],
                         "inputs": [flow("f:b:rl-lp", "asset:LP", "ent:b:user", None, amt("asset:LP", "liquidity"), "burn", ["ev:b:pair-burn"], status=st)],
                         "outputs": [flow("f:b:rl-y", "asset:Y", "ent:pairYZ", "ent:b:user", amt("asset:Y", "liquidity * reserve0 / totalSupply"), "transfer", ["ev:b:pair-burn-amt"], status=st),
                                     flow("f:b:rl-z", "asset:Z", "ent:pairYZ", "ent:b:user", amt("asset:Z", "liquidity * reserve1 / totalSupply"), "transfer", ["ev:b:pair-burn-amt"], status=st)],
                         "status": st, "evidence": ["ev:b:pair-burn"]})

    c.add("positions", {"id": "pos:b:user-LP", "holder": "ent:b:user", "asset": "asset:LP", "kind": "lp", "label": "LP-YZ held by user",
                        "amount": amt("asset:LP", "pairYZ.balanceOf[user]", "34.5", "decimal"),
                        "status": obs, "evidence": ["ev:b:log-mint"]})
    c.add("positions", {"id": "pos:b:user-stake", "holder": "ent:b:user", "asset": "asset:stake", "kind": "claim", "label": "Stake record of user",
                        "amount": amt("asset:stake", "staked[user]"), "description": "Exists only if the user staked; replaces the wallet LP position.",
                        "status": st, "evidence": ["ev:b:stake"]})
    c.add("positions", {"id": "pos:b:farm-LP", "holder": "ent:farm", "asset": "asset:LP", "kind": "custody", "label": "LP-YZ held by farm",
                        "status": st, "evidence": ["ev:b:stake"]})
    c.add("positions", {"id": "pos:b:yz-Y", "holder": "ent:pairYZ", "asset": "asset:Y", "kind": "custody", "label": "Y reserve of pair YZ",
                        "amount": amt("asset:Y", "reserve0"), "status": st, "evidence": ["ev:b:pair-reserves"]})
    c.add("positions", {"id": "pos:b:yz-Z", "holder": "ent:pairYZ", "asset": "asset:Z", "kind": "custody", "label": "Z reserve of pair YZ",
                        "amount": amt("asset:Z", "reserve1"), "status": st, "evidence": ["ev:b:pair-reserves"]})
    c.add("positions", {"id": "pos:b:xy-X", "holder": "ent:pairXY", "asset": "asset:X", "kind": "custody", "label": "X reserve of pair XY",
                        "description": "The X paid into branch 1 now belongs to pair XY's reserves (its LPs), not to the user.",
                        "amount": amt("asset:X", "reserve of X"), "status": obs, "evidence": ["ev:b:log-swap1"]})
    c.add("positions", {"id": "pos:b:xz-X", "holder": "ent:pairXZ", "asset": "asset:X", "kind": "custody", "label": "X reserve of pair XZ",
                        "amount": amt("asset:X", "reserve of X"), "status": obs, "evidence": ["ev:b:log-swap2"]})

    c.add("relations", {"id": "rel:b:claim-Y", "kind": "claim", "source": "pos:b:user-LP", "target": "pos:b:yz-Y", "label": "pro-rata claim",
                        "basis_detail": "burn: amount0 = liquidity * reserve0 / totalSupply",
                        "status": S("static", "rule", "supported", rule="share-redeem-pro-rata"), "evidence": ["ev:b:pair-burn-amt"]})
    c.add("relations", {"id": "rel:b:claim-Z", "kind": "claim", "source": "pos:b:user-LP", "target": "pos:b:yz-Z", "label": "pro-rata claim",
                        "basis_detail": "burn: amount1 = liquidity * reserve1 / totalSupply",
                        "status": S("static", "rule", "supported", rule="share-redeem-pro-rata"), "evidence": ["ev:b:pair-burn-amt"]})
    c.add("relations", {"id": "rel:b:claim-stake", "kind": "claim", "source": "pos:b:user-stake", "target": "pos:b:farm-LP", "label": "1:1 claim on staked LP",
                        "basis_detail": "unstake transfers back exactly `amount`", "status": st, "evidence": ["ev:b:unstake"]})
    c.add("relations", {"id": "rel:b:claim-farmLP-Y", "kind": "claim", "source": "pos:b:farm-LP", "target": "pos:b:yz-Y", "label": "pro-rata claim",
                        "status": st, "evidence": ["ev:b:pair-burn-amt"]})
    c.add("relations", {"id": "rel:b:claim-farmLP-Z", "kind": "claim", "source": "pos:b:farm-LP", "target": "pos:b:yz-Z", "label": "pro-rata claim",
                        "status": st, "evidence": ["ev:b:pair-burn-amt"]})
    c.add("relations", {"id": "rel:b:dep-lp", "kind": "depends_on", "source": "asset:LP", "target": "ent:pairYZ", "label": "reserves and price of pair YZ",
                        "status": st, "evidence": ["ev:b:pair-reserves"]})
    c.add("relations", {"id": "rel:b:dep-y", "kind": "depends_on", "source": "op:zap.addliq", "target": "op:zap.swapY", "label": "uses Y output of branch 1",
                        "status": obs, "evidence": ["ev:b:merge"]})
    c.add("relations", {"id": "rel:b:dep-z", "kind": "depends_on", "source": "op:zap.addliq", "target": "op:zap.swapZ", "label": "uses Z output of branch 2",
                        "status": obs, "evidence": ["ev:b:merge"]})
    c.add("relations", {"id": "rel:b:dep-xyreserve", "kind": "depends_on", "source": "pos:b:xy-X", "target": "ent:xyLPs", "label": "owned by pool LPs",
                        "status": S("example", "manual", "pending", note="LP ownership of pairs XY/XZ not modelled."), "evidence": []})
    c.add("relations", {"id": "rel:b:exit-lp", "kind": "exit", "source": "pos:b:user-LP", "target": "op:removeliq", "label": "remove liquidity",
                        "status": st, "evidence": ["ev:b:pair-burn"]})
    c.add("relations", {"id": "rel:b:exit-stake", "kind": "exit", "source": "pos:b:user-stake", "target": "op:unstake", "label": "unstake",
                        "status": st, "evidence": ["ev:b:unstake"]})
    c.add("relations", {"id": "rel:b:flow-swap-reserve", "kind": "asset_flow", "source": "ent:zap", "target": "pos:b:xy-X", "asset": "asset:X",
                        "amount": amt("asset:X", "half", "50", "decimal"), "operation": "op:zap.swapY", "label": "X added to reserve",
                        "status": obs, "evidence": ["ev:b:log-swap1"]})

    c.add("exits", {"id": "exit:b:lp", "position": "pos:b:user-LP", "label": "Remove liquidity → Y + Z",
                    "steps": [{"order": 1, "operation": "op:removeliq", "actor": "ent:b:user", "transaction": "same",
                               "description": "Transfer LP-YZ to the pair, then call burn(to); both happen in one transaction when called through a router, otherwise in two calls.",
                               "conditions": [cond("c:b:x1", "state", "LP balance of the pair equals the LP being removed", "balanceOf[address(this)]", ["ev:b:pair-burn"])],
                               "evidence": ["ev:b:pair-burn"]}],
                    "consumes": [flow("f:b:x-c", "asset:LP", "ent:b:user", None, amt("asset:LP", "liquidity"), "burn", ["ev:b:pair-burn"])],
                    "returns": [flow("f:b:x-ry", "asset:Y", "ent:pairYZ", "ent:b:user", amt("asset:Y", "liquidity * reserve0 / totalSupply"), "transfer", ["ev:b:pair-burn-amt"]),
                                flow("f:b:x-rz", "asset:Z", "ent:pairYZ", "ent:b:user", amt("asset:Z", "liquidity * reserve1 / totalSupply"), "transfer", ["ev:b:pair-burn-amt"])],
                    "status": st, "evidence": ["ev:b:pair-burn"]})
    c.add("exits", {"id": "exit:b:stake", "position": "pos:b:user-stake", "label": "Unstake, then remove liquidity",
                    "steps": [{"order": 1, "operation": "op:unstake", "actor": "ent:b:user", "transaction": "separate",
                               "conditions": [cond("c:b:xs1", "require", "staked[msg.sender] >= amount", "staked[msg.sender] >= amount", ["ev:b:unstake"])],
                               "evidence": ["ev:b:unstake"]},
                              {"order": 2, "operation": "op:removeliq", "actor": "ent:b:user", "transaction": "separate",
                               "description": "Same as the LP exit once the LP is back in the user's wallet.", "evidence": ["ev:b:pair-burn"]}],
                    "consumes": [flow("f:b:xs-c", "asset:stake", "ent:b:user", None, amt("asset:stake", "amount"), "ledger-debit", ["ev:b:unstake"])],
                    "returns": [flow("f:b:xs-ry", "asset:Y", "ent:pairYZ", "ent:b:user", amt("asset:Y", "pro rata"), "transfer", ["ev:b:pair-burn-amt"]),
                                flow("f:b:xs-rz", "asset:Z", "ent:pairYZ", "ent:b:user", amt("asset:Z", "pro rata"), "transfer", ["ev:b:pair-burn-amt"])],
                    "status": st, "evidence": ["ev:b:unstake", "ev:b:pair-burn"]})

    c.add("unresolved", {"id": "unr:b:price", "kind": "ambiguous_semantics",
                         "text": "Swap outputs depend on pool reserves at execution time. Only the final LP amount is bounded (minLp); each branch has no own slippage limit.",
                         "subjects": ["op:zap.swapY", "op:zap.swapZ"], "evidence": ["ev:b:slippage"]})
    c.add("unresolved", {"id": "unr:b:reward", "kind": "other", "text": "Farm rewards are not modelled; the stake record is treated as a claim on the staked LP only.",
                         "subjects": ["pos:b:user-stake", "ent:farm"], "evidence": ["ev:b:farm"]})

    c.add("tasks", {"id": "task:b:branches", "question": "Into which branches is the user's Token X split, and where do they merge?",
                    "focus": ["op:zap"], "view": "flows",
                    "answer": "Half goes X→Y through pair XY, the rest X→Z through pair XZ; Y and Z merge in pair YZ, which mints LP-YZ to the user."})
    c.add("tasks", {"id": "task:b:lp", "question": "Which assets does the LP-YZ position give a claim on, and who holds them?",
                    "focus": ["pos:b:user-LP"], "view": "holdings",
                    "answer": "Pro-rata claims on the Y and Z reserves held by pair YZ. The LP is not counted in addition to those reserves."})
    c.add("tasks", {"id": "task:b:xholder", "question": "Who holds the Token X the user paid in?",
                    "focus": ["asset:X"], "view": "holdings",
                    "answer": "Pairs XY and XZ (their reserves). The user no longer has a claim on that X."})
    c.add("tasks", {"id": "task:b:exit", "question": "What does exiting the staked position return, and how many steps does it take?",
                    "focus": ["exit:b:stake"], "view": "exits",
                    "answer": "Two separate calls: unstake (returns LP-YZ), then remove liquidity (returns Y and Z)."})
    return c


# =============================================================================
# Example C — lending: collateral, borrow, debt, repay
# =============================================================================


def example_lending() -> Case:
    c = Case(
        meta={
            "id": "example-lending",
            "title": "Example C — Collateralised borrowing (synthetic)",
            "description": "A user supplies Token C as collateral (receipt cC), borrows Token D, and later repays. "
                           "The borrowed D in the user's wallet and the debt it creates are separate objects.",
            "synthetic": True,
            "created_at": "2026-10-20T00:00:00Z",
            "generator": {"name": "examples/generate.py", "version": "0.1.0"},
            "tags": ["example", "lending", "debt"],
        },
        scope={
            "kind": "example",
            "project": {"name": "Example lending pool (synthetic)", "version": "example-1", "entry_contracts": ["ExampleLendingPool"],
                        "files": ["example/ExampleLendingPool.sol"]},
            "chain": {"chain_id": 31337, "name": "local test chain (synthetic)", "block": 3600},
            "components": [{"name": "hand-written example", "role": "example"}],
            "boundaries": [{"id": "bnd:c:oracle", "target": "Price oracle", "reason": "Oracle implementation not in scope; prices are read through IOracle.",
                            "evidence": ["ev:c:hf"]}],
            "notes": [EXAMPLE_SCOPE_NOTE],
        },
    )
    c.source("example/ExampleLendingPool.sol", """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {IERC20} from "./IERC20.sol";
import {IOracle} from "./IOracle.sol";

/// Example lending pool with one collateral token (C) and one borrowable token (D).
contract ExampleLendingPool {
    IERC20 public immutable collateralToken;   // Token C (example)
    IERC20 public immutable debtAsset;         // Token D (example)
    IOracle public immutable oracle;           // external price source

    mapping(address => uint256) public collateralShares;   // cC receipt ledger
    mapping(address => uint256) public debtOf;             // debt D ledger (not transferable)
    uint256 public constant LTV_BPS = 7500;

    function supply(uint256 amount) external {
        collateralToken.transferFrom(msg.sender, address(this), amount);
        collateralShares[msg.sender] += amount;
    }

    function borrow(uint256 amount) external {
        debtOf[msg.sender] += amount;
        require(_healthy(msg.sender), "undercollateralised");
        debtAsset.transfer(msg.sender, amount);
    }

    function repay(uint256 amount, address onBehalfOf) external {
        debtAsset.transferFrom(msg.sender, address(this), amount);
        debtOf[onBehalfOf] -= amount;
    }

    function withdraw(uint256 amount) external {
        collateralShares[msg.sender] -= amount;
        require(_healthy(msg.sender), "undercollateralised");
        collateralToken.transfer(msg.sender, amount);
    }

    function liquidate(address user, uint256 repayAmount) external {
        require(!_healthy(user), "healthy");
        debtAsset.transferFrom(msg.sender, address(this), repayAmount);
        debtOf[user] -= repayAmount;
        uint256 seized = oracle.quote(address(debtAsset), address(collateralToken), repayAmount) * 105 / 100;
        collateralShares[user] -= seized;
        collateralToken.transfer(msg.sender, seized);
    }

    function _healthy(address user) internal view returns (bool) {
        uint256 value = oracle.quote(address(collateralToken), address(debtAsset), collateralShares[user]);
        return debtOf[user] * 10000 <= value * LTV_BPS;
    }
}
""")
    L = "example/ExampleLendingPool.sol"
    c.ev_src("ev:c:ledgers", "collateral and debt ledgers", L, "mapping(address => uint256) public collateralShares", count=2, contract="ExampleLendingPool")
    c.ev_src("ev:c:supply", "ExampleLendingPool.supply()", L, "function supply(", count=4, contract="ExampleLendingPool", function="supply(uint256)")
    c.ev_src("ev:c:borrow", "ExampleLendingPool.borrow()", L, "function borrow(", count=5, contract="ExampleLendingPool", function="borrow(uint256)")
    c.ev_src("ev:c:borrow-debt", "debtOf[msg.sender] += amount", L, "debtOf[msg.sender] += amount", contract="ExampleLendingPool", function="borrow(uint256)")
    c.ev_src("ev:c:borrow-pay", "debtAsset.transfer(msg.sender, amount)", L, "debtAsset.transfer(msg.sender, amount)", contract="ExampleLendingPool", function="borrow(uint256)")
    c.ev_src("ev:c:repay", "ExampleLendingPool.repay()", L, "function repay(", count=4, contract="ExampleLendingPool", function="repay(uint256,address)")
    c.ev_src("ev:c:withdraw", "ExampleLendingPool.withdraw()", L, "function withdraw(", count=5, contract="ExampleLendingPool", function="withdraw(uint256)")
    c.ev_src("ev:c:liq", "ExampleLendingPool.liquidate()", L, "function liquidate(", count=8, contract="ExampleLendingPool", function="liquidate(address,uint256)")
    c.ev_src("ev:c:hf", "_healthy(): oracle-priced LTV check", L, "function _healthy(", end="}", contract="ExampleLendingPool", function="_healthy(address)")
    c.ev_exec("ev:c:tx-borrow", "transaction", "Borrow transaction (synthetic)", "0xexample000000000000000000000000000000000000000000000000000000c002", 3501,
              call_path="User → ExampleLendingPool.borrow(600e18)", data={"amount": "600000000000000000000"})

    st = S("static", "rule", "supported")
    obs = S("observed", "manual", "supported")
    c.add("assets", {"id": "asset:C", "label": "Token C (example)", "symbol": "C", "kind": "token", "standard": "ERC20",
                     "identity": {"kind": "example", "expression": "ExampleLendingPool.collateralToken"}, "status": S("example", "manual", "supported"), "evidence": ["ev:c:ledgers"]})
    c.add("assets", {"id": "asset:D", "label": "Token D (example)", "symbol": "D", "kind": "token", "standard": "ERC20",
                     "identity": {"kind": "example", "expression": "ExampleLendingPool.debtAsset"}, "status": S("example", "manual", "supported"), "evidence": ["ev:c:ledgers"]})
    c.add("assets", {"id": "asset:cC", "label": "cC collateral receipt", "kind": "share",
                     "identity": {"kind": "contract_self", "contract": "ExampleLendingPool", "expression": "collateralShares"},
                     "issuer": "ent:pool", "description": "Internal ledger of supplied collateral.", "status": st, "evidence": ["ev:c:ledgers"]})
    c.add("assets", {"id": "asset:debtD", "label": "Debt in Token D", "kind": "debt_token",
                     "identity": {"kind": "contract_self", "contract": "ExampleLendingPool", "expression": "debtOf"},
                     "issuer": "ent:pool", "description": "Non-transferable obligation denominated in Token D. Not an asset of the holder.",
                     "status": st, "evidence": ["ev:c:ledgers"]})

    c.add("entities", {"id": "ent:c:user", "label": "Borrower (any account)", "kind": "account", "expression": "msg.sender"})
    c.add("entities", {"id": "ent:pool", "label": "ExampleLendingPool", "kind": "contract", "contract": {"name": "ExampleLendingPool", "file": L}, "evidence": ["ev:c:supply"]})
    c.add("entities", {"id": "ent:oracle", "label": "Price oracle", "kind": "external_contract", "in_scope": False, "description": "IOracle; implementation not analysed."})
    c.add("entities", {"id": "ent:liquidator", "label": "Liquidator (any account)", "kind": "account", "expression": "msg.sender of liquidate()"})

    c.add("operations", {"id": "op:c:supply", "kind": "deposit", "label": "supply(amount) — collateral", "initiator": "ent:c:user", "executor": "ent:pool",
                         "entrypoint": {"contract": "ExampleLendingPool", "function": "supply", "signature": "supply(uint256)"},
                         "inputs": [flow("f:c:sup-in", "asset:C", "ent:c:user", "ent:pool", amt("asset:C", "amount", "1000", "decimal"), "transferFrom", ["ev:c:supply"], status=obs)],
                         "outputs": [flow("f:c:sup-out", "asset:cC", None, "ent:c:user", amt("asset:cC", "amount", "1000", "decimal"), "ledger-credit", ["ev:c:supply"], status=obs)],
                         "status": obs, "evidence": ["ev:c:supply"]})
    c.add("operations", {"id": "op:c:borrow", "kind": "borrow", "label": "borrow(amount)", "initiator": "ent:c:user", "executor": "ent:pool",
                         "description": "Two separate outputs: Token D paid to the borrower and a debt record denominated in D.",
                         "entrypoint": {"contract": "ExampleLendingPool", "function": "borrow", "signature": "borrow(uint256)"},
                         "conditions": [cond("c:c:hf", "require", "Position stays healthy: debt ≤ 75% of oracle-priced collateral", "debtOf[user] * 10000 <= value * LTV_BPS", ["ev:c:hf"])],
                         "outputs": [flow("f:c:bor-d", "asset:D", "ent:pool", "ent:c:user", amt("asset:D", "amount", "600", "decimal"), "transfer", ["ev:c:borrow-pay", "ev:c:tx-borrow"], status=obs),
                                     flow("f:c:bor-debt", "asset:debtD", None, "ent:c:user", amt("asset:debtD", "amount", "600", "decimal"), "ledger-credit", ["ev:c:borrow-debt"], status=obs)],
                         "status": obs, "evidence": ["ev:c:borrow", "ev:c:tx-borrow"]})
    c.add("operations", {"id": "op:c:repay", "kind": "repay", "label": "repay(amount, onBehalfOf)", "initiator": "ent:c:user", "executor": "ent:pool",
                         "entrypoint": {"contract": "ExampleLendingPool", "function": "repay", "signature": "repay(uint256,address)"},
                         "participants": [{"role": "payer", "entity": "ent:c:user", "expression": "msg.sender"},
                                          {"role": "debtor", "entity": "ent:c:user", "expression": "onBehalfOf", "evidence": ["ev:c:repay"]}],
                         "inputs": [flow("f:c:rep-d", "asset:D", "ent:c:user", "ent:pool", amt("asset:D", "amount"), "transferFrom", ["ev:c:repay"], status=st),
                                    flow("f:c:rep-debt", "asset:debtD", "ent:c:user", None, amt("asset:debtD", "amount"), "ledger-debit", ["ev:c:repay"], status=st)],
                         "status": st, "evidence": ["ev:c:repay"]})
    c.add("operations", {"id": "op:c:withdraw", "kind": "withdraw", "label": "withdraw(amount) — collateral", "initiator": "ent:c:user", "executor": "ent:pool",
                         "entrypoint": {"contract": "ExampleLendingPool", "function": "withdraw", "signature": "withdraw(uint256)"},
                         "conditions": [cond("c:c:whf", "require", "Position stays healthy after removing collateral", "_healthy(msg.sender)", ["ev:c:hf"])],
                         "inputs": [flow("f:c:wd-cc", "asset:cC", "ent:c:user", None, amt("asset:cC", "amount"), "ledger-debit", ["ev:c:withdraw"], status=st)],
                         "outputs": [flow("f:c:wd-c", "asset:C", "ent:pool", "ent:c:user", amt("asset:C", "amount"), "transfer", ["ev:c:withdraw"], status=st)],
                         "status": st, "evidence": ["ev:c:withdraw"]})
    c.add("operations", {"id": "op:c:liquidate", "kind": "liquidate", "label": "liquidate(user, repayAmount)", "initiator": "ent:liquidator", "executor": "ent:pool",
                         "entrypoint": {"contract": "ExampleLendingPool", "function": "liquidate", "signature": "liquidate(address,uint256)"},
                         "conditions": [cond("c:c:lq", "require", "Borrower is unhealthy", "!_healthy(user)", ["ev:c:liq"])],
                         "inputs": [flow("f:c:lq-d", "asset:D", "ent:liquidator", "ent:pool", amt("asset:D", "repayAmount"), "transferFrom", ["ev:c:liq"], status=st),
                                    flow("f:c:lq-debt", "asset:debtD", "ent:c:user", None, amt("asset:debtD", "repayAmount"), "ledger-debit", ["ev:c:liq"], status=st),
                                    flow("f:c:lq-cc", "asset:cC", "ent:c:user", None, amt("asset:cC", "seized"), "ledger-debit", ["ev:c:liq"], status=st)],
                         "outputs": [flow("f:c:lq-c", "asset:C", "ent:pool", "ent:liquidator", amt("asset:C", "quote(repayAmount) * 105 / 100"), "transfer", ["ev:c:liq"], status=st)],
                         "status": st, "evidence": ["ev:c:liq"]})

    c.add("positions", {"id": "pos:c:user-cC", "holder": "ent:c:user", "asset": "asset:cC", "kind": "share", "label": "cC collateral receipt of borrower",
                        "amount": amt("asset:cC", "collateralShares[user]", "1000", "decimal"), "status": obs, "evidence": ["ev:c:supply"]})
    c.add("positions", {"id": "pos:c:pool-C", "holder": "ent:pool", "asset": "asset:C", "kind": "custody", "label": "C held by pool",
                        "amount": amt("asset:C", "collateralToken.balanceOf(pool)"), "status": st, "evidence": ["ev:c:supply"]})
    c.add("positions", {"id": "pos:c:user-D", "holder": "ent:c:user", "asset": "asset:D", "kind": "custody", "label": "Borrowed D in borrower's wallet",
                        "amount": amt("asset:D", "amount", "600", "decimal"),
                        "description": "Token D the borrower received. It is matched by a separate debt position; it is not the borrower's own equity.",
                        "status": obs, "evidence": ["ev:c:borrow-pay", "ev:c:tx-borrow"]})
    c.add("positions", {"id": "pos:c:user-debt", "holder": "ent:c:user", "asset": "asset:debtD", "kind": "debt", "counterparty": "ent:pool",
                        "label": "Debt of borrower (in D)", "amount": amt("asset:debtD", "debtOf[user]", "600", "decimal"),
                        "description": "Obligation owed to the pool. Interest accrual is not modelled in this example.",
                        "status": obs, "evidence": ["ev:c:borrow-debt"]})
    c.add("positions", {"id": "pos:c:pool-D", "holder": "ent:pool", "asset": "asset:D", "kind": "custody", "label": "Available D liquidity",
                        "amount": amt("asset:D", "debtAsset.balanceOf(pool)"), "status": st, "evidence": ["ev:c:borrow-pay"]})

    c.add("relations", {"id": "rel:c:claim", "kind": "claim", "source": "pos:c:user-cC", "target": "pos:c:pool-C", "label": "1:1 claim (if healthy)",
                        "basis_detail": "withdraw transfers `amount` of C for `amount` of cC",
                        "conditions": [cond("c:c:rc", "require", "Withdrawal keeps the position healthy", "_healthy(msg.sender)", ["ev:c:hf"])],
                        "status": st, "evidence": ["ev:c:withdraw"]})
    c.add("relations", {"id": "rel:c:debt", "kind": "debt", "source": "pos:c:user-debt", "target": "ent:pool", "label": "owes D to pool",
                        "status": obs, "evidence": ["ev:c:borrow-debt"]})
    c.add("relations", {"id": "rel:c:collat", "kind": "collateral", "source": "pos:c:user-cC", "target": "pos:c:user-debt", "label": "secures debt",
                        "basis_detail": "_healthy() compares debtOf with oracle value of collateralShares",
                        "status": st, "evidence": ["ev:c:hf"]})
    c.add("relations", {"id": "rel:c:dep-oracle", "kind": "depends_on", "source": "pos:c:user-debt", "target": "ent:oracle", "label": "health factor uses oracle price",
                        "status": S("static", "rule", "unresolved", note="Oracle implementation not in scope."), "evidence": ["ev:c:hf"]})
    c.add("relations", {"id": "rel:c:exit-cc", "kind": "exit", "source": "pos:c:user-cC", "target": "op:c:withdraw", "label": "withdraw", "status": st, "evidence": ["ev:c:withdraw"]})
    c.add("relations", {"id": "rel:c:exit-debt", "kind": "exit", "source": "pos:c:user-debt", "target": "op:c:repay", "label": "repay", "status": st, "evidence": ["ev:c:repay"]})

    c.add("exits", {"id": "exit:c:collateral", "position": "pos:c:user-cC", "label": "Repay debt, then withdraw collateral",
                    "steps": [{"order": 1, "operation": "op:c:repay", "actor": "ent:c:user", "transaction": "separate", "optional": True,
                               "description": "Needed only while the remaining collateral would not keep the position healthy.",
                               "conditions": [cond("c:c:x1", "branch", "Outstanding debt makes the withdrawal unhealthy", "!_healthy(after withdraw)", ["ev:c:hf"])],
                               "evidence": ["ev:c:repay"]},
                              {"order": 2, "operation": "op:c:withdraw", "actor": "ent:c:user", "transaction": "separate",
                               "conditions": [cond("c:c:x2", "require", "Position healthy after withdrawal (oracle price)", "_healthy(msg.sender)", ["ev:c:hf"])],
                               "evidence": ["ev:c:withdraw"]}],
                    "consumes": [flow("f:c:x-c", "asset:cC", "ent:c:user", None, amt("asset:cC", "amount"), "ledger-debit", ["ev:c:withdraw"])],
                    "returns": [flow("f:c:x-r", "asset:C", "ent:pool", "ent:c:user", amt("asset:C", "amount"), "transfer", ["ev:c:withdraw"])],
                    "status": st, "evidence": ["ev:c:withdraw"], "unresolved": ["unr:c:oracle"]})
    c.add("exits", {"id": "exit:c:debt", "position": "pos:c:user-debt", "label": "Repay debt",
                    "steps": [{"order": 1, "operation": "op:c:repay", "actor": "ent:c:user", "transaction": "same",
                               "description": "Anyone may repay on behalf of the borrower (onBehalfOf).", "evidence": ["ev:c:repay"]}],
                    "consumes": [flow("f:c:xd-c", "asset:D", "ent:c:user", "ent:pool", amt("asset:D", "amount"), "transferFrom", ["ev:c:repay"])],
                    "status": st, "evidence": ["ev:c:repay"]})

    c.add("unresolved", {"id": "unr:c:oracle", "kind": "external_implementation", "text": "Health checks depend on IOracle.quote(); the oracle implementation and its update conditions are not analysed.",
                         "subjects": ["ent:oracle", "rel:c:dep-oracle", "exit:c:collateral"], "evidence": ["ev:c:hf"]})
    c.add("unresolved", {"id": "unr:c:interest", "kind": "other", "text": "No interest accrual exists in this example; real markets grow debt over time.",
                         "subjects": ["pos:c:user-debt"]})

    c.add("tasks", {"id": "task:c:borrowed", "question": "Is the borrowed Token D the borrower's own money?",
                    "focus": ["pos:c:user-D"], "view": "holdings",
                    "answer": "No. The borrower holds 600 D in the wallet and, separately, a debt of 600 D owed to the pool. They are separate positions and are not netted."})
    c.add("tasks", {"id": "task:c:collateral", "question": "Who holds the collateral Token C, and what is needed to get it back?",
                    "focus": ["exit:c:collateral"], "view": "exits",
                    "answer": "The pool holds C; the borrower holds cC. Repay enough debt so the position stays healthy, then withdraw."})
    c.add("tasks", {"id": "task:c:debtdeps", "question": "What does the debt position depend on?",
                    "focus": ["pos:c:user-debt"], "view": "dependencies",
                    "answer": "It is secured by the cC position and its health check depends on the external price oracle."})
    return c


# =============================================================================
# Fixtures (tests only)
# =============================================================================


def fixture_unseen() -> Case:
    """A topology that differs from every example: one router fanning out to
    two strategies with a rebalance cycle between them, observed-execution
    scope, no embedded sources (snippet fallback only) and items without
    evidence. Ids deliberately use a different naming scheme."""

    c = Case(
        meta={"id": "fixture-router-fanout", "title": "Fixture — Router fan-out with rebalance cycle (synthetic)",
              "description": "Test fixture for import: a router splits deposits across two strategies; a rebalance moves funds between them.",
              "synthetic": True, "tags": ["fixture"]},
        scope={"kind": "execution_observation", "chain": {"chain_id": 31337, "name": "fixture chain", "block": 77},
               "notes": ["Synthetic fixture used by the end-to-end tests."]},
    )
    c.d["sources"] = []
    c.d["evidence"].append({"id": "x1", "kind": "trace", "title": "Router deposit trace (synthetic)", "synthetic": True,
                            "execution": {"chain_id": 31337, "tx_hash": "0xfixture0000000000000000000000000000000000000000000000000000000001", "block": 70,
                                          "call_path": "Alice → Router.deposit → S1.invest / S2.invest"}})
    c.d["evidence"].append({"id": "x2", "kind": "source", "title": "Router.rebalance (snippet only)", "synthetic": True,
                            "source": {"file": "fixture/Router.sol", "start_line": 40, "end_line": 43, "contract": "Router", "function": "rebalance",
                                       "snippet": "function rebalance(uint256 amt) external onlyOps {\n    s1.divest(amt);\n    s2.invest(amt);\n}"}})
    o = S("observed", "manual", "supported")
    c.d["assets"] += [
        {"id": "n-tok", "label": "Token W (fixture)", "kind": "token", "identity": {"kind": "example"}, "status": o},
        {"id": "n-rs", "label": "rW router share", "kind": "share", "identity": {"kind": "contract_self", "contract": "Router"}, "issuer": "n-router", "status": o},
    ]
    c.d["entities"] += [
        {"id": "n-alice", "label": "Alice", "kind": "account"},
        {"id": "n-ops", "label": "Ops multisig", "kind": "role"},
        {"id": "n-router", "label": "Router", "kind": "contract"},
        {"id": "n-s1", "label": "Strategy S1", "kind": "contract"},
        {"id": "n-s2", "label": "Strategy S2", "kind": "contract"},
        {"id": "n-ext", "label": "Venue E (external)", "kind": "external_contract", "in_scope": False},
    ]
    c.d["operations"] += [
        {"id": "o-dep", "kind": "deposit", "label": "Router.deposit", "initiator": "n-alice", "executor": "n-router",
         "inputs": [flow("fl1", "n-tok", "n-alice", "n-router", amt("n-tok", value="10", unit="decimal"), evidence=["x1"])],
         "outputs": [flow("fl2", "n-rs", None, "n-alice", amt("n-rs", value="10", unit="decimal"), "mint", ["x1"])],
         "status": o, "evidence": ["x1"]},
        {"id": "o-s1", "kind": "allocate", "label": "invest → S1", "parent": "o-dep", "order": 1, "transaction": "same", "executor": "n-router",
         "inputs": [flow("fl3", "n-tok", "n-router", "n-s1", amt("n-tok", value="6", unit="decimal"), evidence=["x1"])], "status": o, "evidence": ["x1"]},
        {"id": "o-s2", "kind": "allocate", "label": "invest → S2", "parent": "o-dep", "order": 2, "transaction": "same", "executor": "n-router",
         "inputs": [flow("fl4", "n-tok", "n-router", "n-s2", amt("n-tok", value="4", unit="decimal"), evidence=["x1"])], "status": o, "evidence": ["x1"]},
        {"id": "o-ext", "kind": "deposit", "label": "S2 → Venue E", "executor": "n-s2",
         "inputs": [flow("fl5", "n-tok", "n-s2", "n-ext", amt("n-tok"))], "status": S("static", "llm", "pending")},
        {"id": "o-reb", "kind": "transfer", "label": "Router.rebalance", "initiator": "n-ops", "executor": "n-router",
         "inputs": [flow("fl6", "n-tok", "n-s1", "n-s2", amt("n-tok", "amt"), evidence=["x2"]),
                    flow("fl7", "n-tok", "n-s2", "n-s1", amt("n-tok", "amt (reverse direction)"))],
         "status": S("static", "rule", "partially_supported"), "evidence": ["x2"]},
    ]
    c.d["positions"] += [
        {"id": "p-a", "holder": "n-alice", "asset": "n-rs", "kind": "share", "status": o, "evidence": ["x1"]},
        {"id": "p-1", "holder": "n-s1", "asset": "n-tok", "kind": "custody", "status": o},
        {"id": "p-2", "holder": "n-s2", "asset": "n-tok", "kind": "custody", "status": o},
        {"id": "p-e", "holder": "n-ext", "asset": "n-tok", "kind": "custody", "status": S("static", "llm", "pending")},
    ]
    c.d["relations"] += [
        {"id": "r1", "kind": "claim", "source": "p-a", "target": "p-1", "status": o, "evidence": ["x1"]},
        {"id": "r2", "kind": "claim", "source": "p-a", "target": "p-2", "status": o},
        {"id": "r3", "kind": "depends_on", "source": "p-2", "target": "n-ext", "status": S("static", "llm", "pending")},
        {"id": "r4", "kind": "claim", "source": "p-2", "target": "p-e", "status": S("static", "llm", "pending")},
    ]
    c.d["exits"] += [{"id": "x-a", "position": "p-a", "label": "Router.withdraw (not observed)", "steps": [{"order": 1, "operation": "o-dep", "actor": "n-alice"}],
                      "status": S("static", "llm", "pending", note="Exit operation not present in the trace.")}]
    c.d["unresolved"] += [{"id": "u1", "kind": "missing_exit", "text": "No withdraw entrypoint was observed for rW shares.", "subjects": ["n-rs", "p-a"]}]
    c.d["tasks"] += [{"id": "t1", "question": "Which strategies back Alice's rW share?", "focus": ["p-a"], "view": "holdings",
                      "answer": "Strategy S1 and Strategy S2; S2 further depends on Venue E."}]
    return c


def write_invalid_fixtures(dirpath: Path) -> None:
    (dirpath / "invalid-malformed.json").write_text('{"schema": "fluxscope/analysis", "schema_version": "0.1.0", "meta": {"id": "x",, }\n')
    bad_schema = {"schema": "fluxscope/analysis", "schema_version": "0.1.0",
                  "meta": {"id": "bad-schema", "synthetic": True},
                  "scope": {"kind": "guesswork"},
                  "assets": [{"id": "a1", "label": "A", "kind": "token", "identity": {"kind": "example"},
                              "status": {"basis": "static", "support": "rule", "verification": "maybe"}}]}
    (dirpath / "invalid-schema.json").write_text(json.dumps(bad_schema, indent=2) + "\n")
    dangling = {"schema": "fluxscope/analysis", "schema_version": "0.1.0",
                "meta": {"id": "bad-refs", "title": "Dangling references", "synthetic": True},
                "scope": {"kind": "example"},
                "assets": [{"id": "a1", "label": "A", "kind": "token", "identity": {"kind": "example"},
                            "status": {"basis": "example", "support": "manual", "verification": "supported"}}],
                "entities": [{"id": "e1", "label": "E", "kind": "account"}],
                "operations": [{"id": "o1", "kind": "transfer", "label": "t",
                                "inputs": [{"id": "f1", "asset": "a1", "from": "e1", "to": "e-missing", "amount": {"asset": "a-missing", "unit": "raw"}}],
                                "status": {"basis": "example", "support": "manual", "verification": "supported"},
                                "evidence": ["ev-missing"]}]}
    (dirpath / "invalid-dangling.json").write_text(json.dumps(dangling, indent=2) + "\n")
    version = {"schema": "fluxscope/analysis", "schema_version": "9.0.0", "meta": {"id": "v", "title": "Future", "synthetic": True}, "scope": {"kind": "example"}}
    (dirpath / "invalid-version.json").write_text(json.dumps(version, indent=2) + "\n")
    print("wrote tests/fixtures/invalid-*.json")


def main() -> None:
    ex = ROOT / "examples"
    example_nested_vault().write(ex / "nested-vault.example.json")
    example_swap_lp().write(ex / "swap-lp.example.json")
    example_lending().write(ex / "lending.example.json")
    fx = ROOT / "tests" / "fixtures"
    fx.mkdir(parents=True, exist_ok=True)
    fixture_unseen().write(fx / "unseen-topology.json")
    write_invalid_fixtures(fx)


if __name__ == "__main__":
    main()
