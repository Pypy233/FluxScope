# Snapshot: Beefy Vault V7 + StrategyERC4626

Real project used as the first end-to-end case of FluxScope.

| | |
|---|---|
| Upstream | https://github.com/beefyfinance/beefy-contracts |
| Commit | `e33f868e2d995c2db9219029c26d31cd8e7d5cb2` |
| Contracts analysed | `contracts/BIFI/vaults/BeefyVaultV7.sol`, `contracts/BIFI/strategies/Common/StrategyERC4626.sol` (+ `BaseAllToNativeFactoryStrat.sol`) |
| Dependencies | `@openzeppelin/contracts-upgradeable` 4.9.6, `@openzeppelin/contracts` 5.4.0 (as pinned by the upstream `yarn.lock`; npm tarball SHA-1 verified) |
| Licence | MIT (Beefy, OpenZeppelin) |
| Build | `foundry.toml` (solc 0.8.23, remappings to `lib/`) |
| Analysis configuration | `fluxscope.json` (instances, bindings vault.strategy ↔ strategy.vault, display labels) |

Only the transitive import closure of the two analysed contracts is included
(28 files). File contents are byte-identical to upstream; two upstream files use
CRLF line endings, which the analyzer normalises in its working copy (recorded
in the result's notes and provenance).

Recreate with `python scripts/fetch_beefy_snapshot.py --out corpus/beefy-erc4626`.

Outside the analysed scope (interfaces only): the external ERC-4626 vault,
`IBeefySwapper`, `IStrategyFactory`, `IFeeConfig`, `IWrappedNative`, `IMerklClaimer`.
