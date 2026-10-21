# FluxScope — design notes

FluxScope helps smart-contract developers and auditors understand how assets
and positions move through a DeFi system. It is aimed at an ICSE Tool
Demonstration. The tool has three parts that are connected only through a
versioned JSON format:

| Part | Responsibility | Location |
|---|---|---|
| Analysis backend | Recover program facts with Slither, summarise functions across calls and contracts, interpret them with standard/protocol rules and an LLM, check candidate relations, assemble graph data | `fluxscope/` (Python) |
| Data model | Assets, entities, operations, flows, positions, relations, exit paths, evidence, scope, unresolved items | `fluxscope/model.py` → `schema/fluxscope-analysis-0.1.schema.json` |
| Viewer | Interactive, offline, single-file HTML that renders any valid result | `frontend/` → `dist/fluxscope.html`, `fluxscope/static/app.html` |

## Data model semantics (summary)

See the docstrings in `fluxscope/model.py`; the main points:

* **Nodes** are entities (accounts, roles, contracts, protocols, external
  contracts), assets (tokens, shares, LP tokens, debt tokens, accounting
  claims), positions (holder × asset, kind share/custody/lp/debt/...), and
  operations (deposit, swap, redeem, ...).
* **Operations** have multiple input and output flows. A flow is one asset
  moving from a payer entity to a recipient entity (`from` is null for mint,
  `to` is null for burn). Operations nest (`parent`, `order`) and say whether
  they run in the same transaction as the parent or separately.
* **Relations** are typed: `asset_flow`, `holds`, `claim` (a position
  represents a claim on another position), `depends_on`, `exit`, `debt`,
  `collateral`, `binding`, `calls`, `identity`.
* **Status** on every interpretation: `basis` (`example`, `static` = candidate
  behaviour the code supports, `observed` = seen in an execution, `config`),
  `support` (`rule`, `adapter`, `llm`, `config`, `manual`, `derived`),
  `verification` (`supported`, `partially_supported`, `pending`,
  `contradicted`, `unresolved`) and the individual checks with evidence.
* **Amounts** always carry their asset; values are optional strings. Amounts in
  different assets are never added up.
* **Shares vs underlying**: a share position and the custody position of the
  underlying are different positions connected by a `claim` relation; the
  underlying is held by the issuing system, so it must not be counted again
  for the share holder.
* **Borrowing**: the borrowed asset arriving at the borrower (a flow / custody
  position) and the resulting `debt` position are separate objects.
* **Evidence**: source locations (file, contract, function, lines, snippet),
  transactions / logs / traces, configuration, standards, rules, model output.
  Source files can be embedded in `sources[]` for code navigation.
* **Unresolved** items and **boundaries** (e.g. only an interface is available)
  are first-class and shown in the UI.
* Display names (`label`, `symbol`) never establish identity or relationships.

## Viewer

### Build

There is no Node.js toolchain on the build machine, so the viewer is plain
JavaScript (no modules, no npm dependencies) split into files under
`frontend/src/js/` and concatenated by `frontend/build.py` into one HTML file
with all CSS/JS/schema inlined. No external resources (fonts, CDNs) are
referenced, so it works offline.

Build outputs:

* `dist/fluxscope.html` — standalone viewer with the built-in example cases.
* `fluxscope/static/app.html` — the same app with an empty data slot, used by
  the backend to generate reports.

Data and configuration slots in the HTML (the backend fills them by replacing
the element contents; `<` inside JSON is escaped as `<`):

```html
<script type="application/json" id="fluxscope-data">{"mode":"app","cases":[...],"default":"<meta.id>"}</script>
<script type="application/json" id="fluxscope-config">{"api":null}</script>
```

`mode` is `app` (example gallery) or `report` (a generated report, shows a
report banner). When `api` is a URL prefix (set by `python -m fluxscope serve`)
the "Analyze project" panel talks to the backend; when it is null the panel says
that no backend is connected and that results can be imported as JSON. The UI
never simulates analysis progress.

### Backend API contract (implemented in `fluxscope/server/`)

* `GET  {api}/projects` → `[{"id","name","path","description"}]` known projects.
* `POST {api}/jobs` body `{"project": "<id or path>", "llm": "none|openai|anthropic|replay"}` → `{"id","status"}`.
* `GET  {api}/jobs/{id}` → `{"id","status":"queued|running|succeeded|failed","stage","log":[...],"error"}`.
* `GET  {api}/jobs/{id}/result` → analysis result JSON.
* `GET  {api}/jobs/{id}/report` → offline HTML report.

### Layout and interaction

Desktop layout, English UI. Results that embed their sources open in the
**Code** view; the other views show the whole system.

**Code view** (an auditor reads part of the code at a time, and a demo builds
the picture up, so the graph follows the code being read instead of showing the
final graph at once):

* Left: the entry points of each analysed contract (inherited ones marked),
  then the source with line numbers. Gutter marks show lines that move assets
  (blue / green / red), guard an operation (amber) or call another function
  (grey). The open function is shaded; above its statements, lens rows say what
  the analysis read from them: `TRANSFER` / `MINT` / `BURN` / `CREDIT` /
  `DEBIT` with asset and parties, `WHO` (access control), `GUARD`, `IF`
  (branch), `NEEDS` (external precondition), `LOOP`, `CALL`, and on the header
  the operation kind, caller and counts (with *kind proposed by the model* when
  the LLM stage named it). A flow or guard of a nested call that the analyzer
  cites along the call path is shown at the `CALL`, not repeated on the
  caller's line.
* Right, nothing open: the *system map* — contracts in scope (and external
  ones they move assets with), every account and role as one node, one edge
  per pair of parties listing the assets moved (arrowheads per direction),
  oriented users → callers → callees. A contract that moves assets only with
  users also shows the external contracts its value depends on, so boundaries
  stay visible (MetaMorpho → Morpho Blue).
* Right, a function open: only that function and the pinned ones — caller,
  flows with amounts, and direct calls as collapsed nodes (`+N` opens the next
  level and its flows). Calls that move no assets are not drawn; the source
  keeps their `CALL` marks. Details of the selection sit under the graph.
* Linking: hovering a marked line emphasises the graph items it supports;
  hovering a graph item highlights its lines (or names the other file they are
  in); clicking a line, annotation or graph item selects it and brings the
  other side into view. Back navigation returns to the previous file position.

**Whole-system views:**

* Top bar: tool name, prototype label, scope banner (example data / static
  analysis candidate / observed execution), project/commit/chain info, view
  switch, export buttons.
* Left: case selector, JSON import (file, drag & drop, paste), analyze panel,
  search, filters, legend, user tasks, unresolved items.
* Centre: zoomable/pannable auto-laid-out graph; bottom drawer with evidence
  and source code (line numbers, highlighted range).
* Right: details of the selected node or relation: participants, amounts,
  conditions, status checks, evidence, related paths, exit paths.

Views are presets over node types and relation kinds: **Flows** (entities +
operations, flow edges), **Holdings** (entities + positions, holds / claim /
debt / collateral), **Dependencies** (claim, depends_on, binding, calls),
**Exits** (exit relations and exit-path operations), **All**.

Edge width never encodes amounts. Relation kinds are distinguished by colour and
marker; `static` (candidate) relations are dashed and `observed` relations are
solid; example data is flagged globally.

Expand/collapse works on three hierarchies: entity groups (`entity.parent`),
nested operations (`operation.parent`), and dependency depth (claim /
depends_on chains revealed level by level).

The key interaction: selecting a share (asset or position) shows who holds it,
which underlying positions it claims (and who holds those), what it depends on,
the exit operations with actors and known conditions, and the evidence behind
each statement.
