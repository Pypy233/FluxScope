# Connecting an analyzer to FluxScope

The viewer only reads the versioned result format `fluxscope/analysis` 0.1.0
(`schema/fluxscope-analysis-0.1.schema.json`, generated from
`fluxscope/model.py`). Any analyzer — the built-in Slither pipeline, a trace
replayer, a different static analyzer — plugs in by emitting that JSON.

```
analyzer ──► result.json ──► python -m fluxscope validate result.json
                         └─► python -m fluxscope report result.json --out report.html
                         └─► viewer: "Import JSON…"
```

## Minimum content

| Field | Required | Notes |
|---|---|---|
| `schema`, `schema_version` | yes | `"fluxscope/analysis"`, `"0.1.0"` |
| `meta.id`, `meta.title`, `meta.synthetic` | yes | `synthetic: true` for examples / generated test data |
| `scope.kind` | yes | `example`, `static_analysis`, `execution_observation` or `mixed` |
| `assets`, `entities` | for any graph | every flow, position and relation refers to them by id |
| `operations` | for flows | each with `inputs` / `outputs` flows; nest with `parent` |
| `positions`, `relations` | for holdings / dependencies | `holds`, `claim`, `depends_on`, `debt`, … |
| `evidence` | strongly recommended | items without evidence are shown with an explicit "no evidence" warning |
| `sources` | optional | embedded files enable line-highlighted code navigation |

`python -m fluxscope validate` checks the JSON schema and the rules a schema
cannot express: dangling ids, amounts denominated in the wrong asset, debt
positions denominated in shares, claims that point at the same position.

## Rules the producer must follow

1. **Identity is not a name.** Two assets are the same only if the producer
   knows it (same address on the same chain, or an explicit identity fact).
   Use `identity` relations with `status.verification = "pending"` for
   candidates.
2. **Amounts carry their asset.** `Amount.asset` must equal the asset of the
   flow / position. Leave `value` null when unknown; put symbolic expressions
   in `expression`. Never add amounts of different assets.
3. **Shares are claims.** Model a share holding and the underlying held by the
   issuing system as two positions joined by a `claim` relation. Do not give
   the share holder a custody position of the underlying.
4. **Debt is separate.** A borrow produces an inflow of the borrowed asset
   (flow + custody position) *and* a `debt` position (with `counterparty`)
   joined to the creditor by a `debt` relation; link collateral with a
   `collateral` relation. Recorded per-account amounts that are not tokens
   (pending redemptions, strategy debt) are `accounting` assets whose entries
   are `claim` or `debt` positions; the built-in pipeline emits their changes
   as flows with mechanism `credit` / `debit` / `ledger-transfer`.
5. **Say what kind of fact it is.** `status.basis` is `static` (candidate
   behaviour supported by code), `observed` (seen in an execution, with
   transaction / log / trace evidence), `example` or `config`. `status.support`
   names the producer (`rule`, `adapter`, `llm`, `config`, `manual`,
   `derived`) and `status.checks` lists what was verified.
6. **Report gaps.** Put missing implementations in `scope.boundaries` and
   open questions in `unresolved` instead of guessing.

## Execution observations

A trace-based analyzer can emit the same objects with `basis: "observed"`:
flows carry concrete `amount.value` (raw units, string), evidence of kind
`transaction`, `log` or `trace` with `execution.tx_hash`, `block`,
`log_index` and `call_path`, and `scope.chain` with the chain id and block.
Static candidates and observed paths can coexist in one result
(`scope.kind = "mixed"`); the viewer draws candidates dashed and observed
paths solid.

## The built-in Slither pipeline as a reference

`fluxscope/analyze.py` shows the full path from a project to this format:

| Stage | Module | Output |
|---|---|---|
| snapshot | `project.py` | normalised copy, file hashes |
| compile | `project.py` | Slither (crytic-compile) |
| facts | `facts/resolver.py`, `summaries.py` | per-entry-point effect trees with call context |
| rules / adapters | `adapters/__init__.py` | token transfers, mints, burns, ERC-4626 operations |
| semantics | `semantics.py` | operations, flows, positions, claims, exits, dependencies |
| llm (optional) | `llm/semantic.py` | validated proposals (`support: llm`) |
| check | `checker.py` | per-claim checks |
| assemble | `analyze.py` | `AnalysisResult` with evidence and sources |

New protocol knowledge goes into `adapters/` as a rule that (a) matches on the
callee's interface **and** implementation or package version, not just its
name, and (b) records a rule id and note that end up in the evidence.
