# Evaluation: what the LLM adds to static analysis

This note describes how the numbers in `eval/results/` were produced and how
to read them. Regenerate everything (no model calls; recorded answers are
replayed) with `scripts/build_all.sh`, and the table below with
`python scripts/eval_summary.py`.

## Projects and annotations

| project | role | annotation |
|---|---|---|
| Beefy Vault V7 + StrategyERC4626 (`corpus/beefy-erc4626`) | development project of the first milestone | `eval/beefy-erc4626.truth.json` (one documented post-hoc addition: strategy ERC-4626 share exits) |
| Kaia yield aggregator, local yield path only (`corpus/kaia-yield`, partial snapshot) | development project for ledgers, registries, debts | `eval/kaia-yield.truth.json`, written and frozen (sha256 `0beec1a4…`, copy in `eval/frozen/`) **before** the first analyzer run; aliases (naming only) added afterwards |
| MetaMorpho (`corpus/metamorpho`) | **held-out**: no analyzer change was made for it (its rules-only scores are the same before and after the changes of 2026-10-21) | `eval/metamorpho.truth.json`, frozen (sha256 `a91ad626…`, copy in `eval/frozen/`) before the first run; aliases added afterwards |

All three annotations were written by the tool authors from the source code
(and, for the Kaia yield aggregator, the upstream README). They are not independent; a second
annotator is needed before numbers are reported in a paper. The Kaia yield aggregator was used
while developing the ledger / registry / membership features, so its rules-only
numbers are optimistic; MetaMorpho is the honest generality check.

Each annotation lists, in a small vocabulary of holders, roles and assets:
operation kinds (all acceptable labels) and callers of the asset-relevant entry
points, claims (`position → position it can obtain assets from through an
exit`), dependencies (`node → party its value or availability depends on`),
exits (`position → entry points that consume it`) and identity questions.
`aliases` map analyzer ids to that vocabulary; they never change the annotation.

## Configurations

* **rules** — static analysis (Slither facts, call-context summaries) and the
  rule / adapter layer; no model.
* **llm-only** — the model reads the in-scope Solidity sources and answers in the
  annotation vocabulary (prompt `baseline-v1`). No extracted facts.
* **combined** — rules + the LLM stage (prompt `semantic-v1`): the model sees the
  fact tables and the sources and may relabel operations the rules could not
  name or named only by a structural fallback (`other`; `mint` / `burn` /
  single-asset `transfer` without a recognised counterpart), propose relations
  between existing fact ids, explanations and questions. Proposals are
  validated against the facts; kinds from adapters, standards and ledger rules
  are never overwritten.
* **combined+checked** — the combined result without model-proposed relations
  that the checker left `pending` or `contradicted` (no fact links them).

Each LLM configuration was run three times (independent requests, `sample`
0–2 in the request hash).

## How the model was run (subscription, no API key)

Model: Claude Opus 5.5 (`claude-opus-5-5`), through the user's Claude
subscription. Each request was written by the `exchange` provider to
`eval/llm-records/<project>/pending/<hash>.request.md` and answered by a fresh
Claude Code subagent that was instructed to read only that file, follow its
system prompt and output schema, and write `<hash>.response.json`. The next run
validated each answer against the schema and stored it under the request hash
(`eval/llm-records/<project>/<hash>.json`, including `answered_by`).

Fidelity caveats: the answering agent runs with Claude Code's own system prompt
in addition to the request's system prompt, and its restriction to the request
file is by instruction (the agents made only reads of the request file and one
write). Answers are not sampled with fixed temperature or seeds. The
`claude-cli` provider (`claude -p --json-schema … --tools ""`) is the
non-interactive equivalent for running the same requests from a terminal; it is
unit-tested against a fake CLI but was not used for these numbers.

### Naming of the second project

The second real project is published under a neutral name ("Kaia yield
aggregator, partial snapshot"): only its local yield path is included, not the
complete project. The third-party files are unchanged (their paths keep the
upstream directory names) and the upstream repository and commit are recorded in
`corpus/kaia-yield/SNAPSHOT.md` and `snapshot.json`. The rename changed the
semantic-stage requests of this project (they contain the case name and group
label), so its three `semantic` answers were produced again on 2026-10-20 with
the same protocol; the three `baseline` requests contain only the source and
were unchanged, so their recorded answers are reused. Earlier answers for this
project (to requests that no longer occur: earlier analyzer states, the previous
name) are not part of the repository. The frozen annotation copies in `eval/frozen/` have the project name
replaced; their README gives the SHA-256 at freeze time and of the copies.

### Analyzer changes of 2026-10-21

After the first evaluation, the analyzer gained generic rules found missing on
further projects (not on the three projects here): `SafeTransferLib` and
partial ERC-20 interfaces, pairing of inline balance updates into transfers,
staking ledgers (deposit / withdraw of a recorded asset), payouts by an
external call that burns the caller's position, contract-instructed and nested
withdrawals to caller-chosen receivers, entry points that only change
value-carrying state, `AccessControl` roles with modifier arguments, ERC-2771
`_msgSender()`, and refinement of weak structural kinds by the LLM stage. The
semantic-stage requests contain the facts, so all nine `semantic` answers were
produced again with the same protocol; the earlier ones are kept, unused, in
`eval/llm-records/<project>/superseded/`. The `baseline` (llm-only) requests
contain only the source and are unchanged. The rules-only and llm-only scores
of the three projects are identical before and after the changes; the combined
scores changed (slightly) because the facts and the answers changed.

### Analyzer changes of 2026-10-21 (second set)

Developed on further projects outside this repository: namespaced storage (ERC-7201; OpenZeppelin 5
upgradeable contracts), ERC-20 balance mappings behind an inherited `balanceOf` declaration, generic
safe-transfer libraries and `safeTransferETH`, numeric authorisation flags (`wards[msg.sender] == 1`),
asset-moving initializers, a structural swap rule, payouts to caller-chosen receivers, custody addresses
of a contract, merkle-proof entitlements and dependencies of share holders through the issuer. The
Beefy and Kaia yield aggregator requests are unchanged by them; MetaMorpho's three `semantic` requests
changed and were answered again with the same protocol (earlier answers in `superseded/`). MetaMorpho's
rules-only dependency recall rose from 0.50 to 1.00 (share holders depend on Morpho Blue through the vault);
its other rules-only scores are unchanged.

## Scoring

Exact match on canonical keys, per dimension (entry points are matched by
their ABI signature: contract and interface parameter types count as
`address`, enums as `uint8`, structs by name): operation kind and caller
accuracy over the annotated entry points, precision / recall for claims,
dependencies and exits. `deps relaxed` reduces position endpoints to their
holder (`strategy:want → strategy`) on both sides, because the model often
states a dependency for a position where the annotation names the holder.
`contribution` in each result file lists, per run, the relations the LLM stage
added that are correct / wrong and the operation kinds it fixed or broke,
relative to rules only.

## Results

`eval/results/summary.md` (generated) and the table in the README. Per run,
`eval/results/<project>.json` holds the scores, every extra / missed item per
dimension, the per-operation kinds and callers, the LLM call record (request
hash, who answered, proposals accepted / rejected) and the contribution
relative to rules only.

Observations across the three projects:

1. **Kinds.** The LLM stage fixed every operation kind the structural rules
   left as `other` (Beefy harvest entry points, 3 per run) and never changed a
   correct rule kind (rule results are not overwritten).
2. **Resolution beyond the rules.** In every Kaia yield run the model proposed
   CustomVault's claim on CustomStrategy's USDT where the rules only know
   "token passed as `token`"; the checker confirms it (access control +
   payout facts).
3. **Noise.** Most model-added relations are not in the annotation. The
   checker removes the ones without supporting facts (8 / 12 / 14 of
   14 / 31 / 20 over three runs) and keeps the correct additions; the rest are
   partly plausible but unannotated.
4. **Fact-anchored proposals limit recall.** On MetaMorpho the facts contain no
   node for the vault's supply position inside Morpho Blue (no adapter), so the
   combined configuration cannot express the claim the LLM-only answers
   contain. Some proposals are rejected because they cite positions or
   entities as `fact_refs`, which the prompt does not allow (6 of 148 on
   MetaMorpho).
5. **LLM-only** answers are fluent and often right on kinds, callers and claims,
   but over-report exits and treat dependencies as position-to-position value
   links; nothing in them can be traced to a program fact.

Annotation disagreements found while reviewing results (left unchanged in the
frozen annotations): `CooldownVault.transferFrom` may be called by any account
(only `from` / `to` must be authorized holders); in Beefy, `upgradeStrat()` also
exits the strategy's ERC-4626 shares (through `retireStrat()`).
