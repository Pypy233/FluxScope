# Corpus: third-party Solidity used as analysis input

The FluxScope code is MIT-licensed (`/LICENSE`). The Solidity files under
`corpus/` are third-party code (except `fixtures/receiver-vault`), included
unmodified as input data for the analysis and the evaluation; each keeps its own
licence, given by the SPDX header of the file. The licence texts are in
`corpus/LICENSES/`. Generated reports (`dist/reports/*.html|json`) and recorded
LLM requests (`eval/llm-records/`) embed these files.

| directory | upstream (pinned commit) | files | SPDX licences |
|---|---|---|---|
| `beefy-erc4626/` | beefyfinance/beefy-contracts @ `e33f868` + OpenZeppelin npm packages | 28 | MIT |
| `kaia-yield/` | Kaia yield aggregator, partial snapshot (upstream and commit in `kaia-yield/SNAPSHOT.md`) + OpenZeppelin npm packages | 52 | GPL-3.0 (10), BUSL-1.1 (1), MIT (41) |
| `metamorpho/` | morpho-org/metamorpho @ `f5faa9c` with its submodules | 41 | GPL-2.0-or-later (16), MIT (25) |
| `fixtures/oz-erc4626/` | OpenZeppelin Contracts 5.4.0 | 208 | MIT |
| `fixtures/receiver-vault/` | written for FluxScope tests | 1 | MIT |

Licence texts: `GPL-3.0-only.txt`, `GPL-2.0-or-later.txt`, `BUSL-1.1.txt`
(standard text; the upstream file carries the SPDX identifier only, without
licence parameters), `MIT.txt`, and `MetaMorpho-LICENSE.txt` (the upstream
licence file of MetaMorpho).

Every snapshot directory has a `SNAPSHOT.md` (provenance, scope, what the
analysis configuration states) and, where it was fetched by script, a
`snapshot.lock.json` with the SHA-256 of every file;
`scripts/fetch_snapshot.py <dir>` re-creates the files from upstream.
