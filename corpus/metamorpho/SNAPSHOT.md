# Snapshot: MetaMorpho (held-out case)

ERC-4626 vault that allocates its asset across Morpho Blue lending markets
(supply / withdraw queues, curator caps, allocator reallocation, fee shares).
Added as a **held-out** real project: the analyzer was not changed for it, and
the annotation (`eval/metamorpho.truth.json`) was written and frozen before
its first run.

| | |
|---|---|
| Upstream | https://github.com/morpho-org/metamorpho |
| Commit | `f5faa9c21b1396c291b471a6a5ad9407d23486a9` (the MetaMorpho revision pinned by the `foundry.lock` of the Kaia yield aggregator case, `corpus/kaia-yield`) |
| Contract analysed | `src/MetaMorpho.sol` |
| Dependencies | `lib/openzeppelin-contracts` @ `932fddf` (5.0.0), `lib/morpho-blue` @ `55d2d99` (interfaces and libraries only) — the submodule commits pinned upstream |
| Licence | GPL-2.0-or-later (MetaMorpho, Morpho Blue), MIT (OpenZeppelin) |
| Build | `foundry.toml` (solc 0.8.21, evm paris) |
| Configuration | one instance; Morpho Blue (`MORPHO`) is left unbound: an external contract known only by its interface |

Recreate with `python scripts/fetch_snapshot.py corpus/metamorpho` (41 files,
SHA-256 in `snapshot.lock.json`).
