# Snapshot: Kaia yield aggregator (partial snapshot)

Second real project of FluxScope: a multi-layer yield stack with two-step
withdrawals, strategy debt and registries. Only the local yield path on Kaia is
included (52 files), not the complete project; it is referred to by this neutral
name in the tool's documentation and results.

| | |
|---|---|
| Upstream | https://github.com/superearn-io/superearn-core-public (public source release) |
| Commit | `ddbfd27e1fab5f7c3a759aca5a4565e22235c9b6` (2026-07-01) |
| Contracts analysed | `CooldownVault`, `CustomYearnStrategy` (+ `BaseCooldownStrategy`, vendored Yearn `BaseStrategy`), `CustomVault`, `CustomStrategy` |
| Dependencies | `@openzeppelin/contracts` 5.3.0, `@openzeppelin/contracts-upgradeable` 4.9.4 (versions pinned by upstream `package.json`; npm tarball SHA-1 verified) |
| Licences | per-file SPDX headers, unchanged: GPL-3.0 (10 files), BUSL-1.1 (`src/superearn/core/lib/TimelockExecutionLib.sol`), MIT (41 files, OpenZeppelin and interfaces). Upstream ships no licence file; the licence texts are in `corpus/LICENSES/`. Included unmodified for non-production research use. |
| Build | `foundry.toml` (solc 0.8.29; upstream uses `via_ir`, which only changes code generation and is off here) |
| Analysis configuration | `fluxscope.json` (instances, bindings, registries, one code-derived identity, display labels) |

The files are byte-identical to upstream (SHA-256 per file in
`snapshot.lock.json`). Re-create them from upstream with:

```bash
python scripts/fetch_snapshot.py corpus/kaia-yield
```

## What the configuration states (and why)

* **Bindings** — `CustomYearnStrategy.want` / `cooldownVault` → CooldownVault
  (the Kaia Yearn vault's token is the CooldownVault, see upstream
  `DEPLOYED_CONTRACTS.md`), `customVault` / `externalShareToken` → CustomVault
  (constructor), `CustomVault.customYearnStrategy` → CustomYearnStrategy,
  `CustomStrategy.remoteVault` → CustomVault (checked by
  `CustomVault.addCustomStrategy`). `CustomYearnStrategy.vault` is the Yearn V2
  vault, an **external** party (Vyper, not in the repository).
* **Registries** — `CustomVault.isCustomStrategy` / `customStrategies`,
  `CooldownVault._strategiesSet` and `_authorizedAddresses` contain the
  analysed instances listed. On chain they contain more members (the router,
  the Yearn vault, `StrategyOriginVault`, other custom strategies); calls are
  followed only into the configured members and every function that changes a
  registry is reported as unresolved.
* **Identity** — `strategy.externalUnderlyingToken`, `cooldown._underlying`
  and `customVault._asset` are the same token: established by the constructors
  (`CustomVault(_customVault).asset()` is passed in and checked against
  `cooldownVault.asset()`), recorded with `source: "code"`.

## Outside the analysed scope

Yearn V2 vault (`report()`, `debtOutstanding()`, user deposits into the
vault), the router, health checks, `IExternalAssetsProvider`
implementations, allow-listed execution targets of `CustomStrategy` /
`BaseCooldownStrategy`, and the cross-chain path (`StrategyOriginVault`,
`OriginVault`, `RemoteVault`).
