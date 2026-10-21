# Demo script (ICSE tool demonstration, ~4 minutes)

Goal: show that an auditor can go from a real Solidity project to a reviewable
picture of asset and holding relations, and from every relation back to code.

## 0. Setup (before recording)

```bash
python -m fluxscope serve --projects corpus --port 8765    # live analysis
open dist/reports/beefy-erc4626.html                         # offline report (fallback)
```

## 1. Analyse a real project (30 s)

* In the browser at `http://127.0.0.1:8765/`, *Analyze project* → **Beefy Vault
  V7 + StrategyERC4626** → *Run*. The job log shows the real stages: snapshot,
  compile (Slither 0.11.6, solc 0.8.23), facts, semantics, check, assemble.
* Point at the scope banner: *static analysis — candidate behaviour supported by
  code*, pinned commit `e33f868`, files hashed.

## 2. The key interaction: "what is this share?" (90 s)

Task: *Who holds mooToken, and who holds the assets it claims?*

1. *Holdings* view → select **mooToken (vault share)**.
2. Details panel:
   * held by **Any account** (issued by `deposit`, transferable);
   * claims → **want held by BeefyVaultV7** (pro-rata payout
     `r = balance() * _shares / totalSupply() | b + _diff`) and → **Claim on
     StrategyERC4626 held by BeefyVaultV7** (only the vault may call
     `StrategyERC4626.withdraw`);
   * expand: the claim on the strategy → **want held by StrategyERC4626** and
     **ERC-4626 shares held by StrategyERC4626** → **erc4626.asset() held by the
     external ERC-4626 vault** (outside the analysed scope).
   * The share holder never "holds" the underlying: the custody positions belong
     to the vault, the strategy and the external vault.
3. Click a check (e.g. *amount-link*) → the evidence drawer opens
   `BeefyVaultV7.sol` at the payout line.

## 3. Exit path with conditions (45 s)

Task: *What is needed to exit mooToken?*

* *Exits* view → **Exit mooToken via BeefyVaultV7.withdraw(_shares)**:
  1. any account calls `withdraw` (its own shares are burned; OpenZeppelin `_burn`
     requires the balance);
  2. *only if `b < r`*: the vault calls `StrategyERC4626.withdraw` — the strategy's
     `require(msg.sender == vault)` is shown as **satisfied in this call context**;
  3. *only if `wantBal < _amount`*: the strategy redeems ERC-4626 shares (EIP-4626
     adapter, external implementation → *partially supported*).

## 4. What the tool will not assume (30 s)

* *Unresolved* list: `erc4626.asset()` vs `want` — only an approval pattern
  supports the identity, so it stays a **pending candidate**; external swapper /
  Merkl claimer are analysis boundaries; `upgradeStrat()` re-binds the strategy.

## 5. Code change → different graph (30 s)

```bash
# share receiver changed from msg.sender to owner()
python -m fluxscope diff dist/reports/beefy-erc4626.json mutated.json
```

The deposit's mint flow and the share position move from *Any account* to the
vault owner; changing only token labels/symbols does not merge or split assets
(`tests/test_mutations.py`).

## 6. Offline report (15 s)

*Export HTML report* → open the file with the network disabled: same graph,
details and source evidence.
