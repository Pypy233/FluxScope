# FluxScope viewer (front-end)

A single-file, offline HTML viewer for FluxScope analysis results
(`schema/fluxscope-analysis-0.1.schema.json`). It renders any valid result with
the same code — there is no case-specific logic.

## Build

No Node.js is needed. The sources are plain browser JavaScript and CSS:

```
frontend/src/index.html      template with slots for CSS, JS, schema, data, config
frontend/src/css/app.css
frontend/src/js/00-core.js   namespace, utilities, captures the pristine DOM for export
frontend/src/js/10-validate.js  JSON-schema subset validator + semantic checks (port of model.semantic_errors)
frontend/src/js/20-model.js     indexes over one result
frontend/src/js/30-graph.js     view composition: views, filters, expand/collapse, dependency depth
frontend/src/js/40-layout.js    layered layout (cycle breaking, layering, crossing reduction, placement)
frontend/src/js/50-render.js    SVG rendering, pan/zoom, highlighting
frontend/src/js/60-code.js      evidence drawer and source viewer
frontend/src/js/70-details.js   details panel (share summary, flows, exits, checks, evidence)
frontend/src/js/80-sidebar.js   cases, import, analyze panel, search, tasks, filters, legend
frontend/src/js/90-app.js       state, wiring, import/export, boot
```

```
.venv/bin/python examples/generate.py     # regenerate examples + test fixtures (synthetic)
.venv/bin/python frontend/build.py        # writes dist/fluxscope.html and fluxscope/static/app.html
```

`dist/fluxscope.html` embeds `examples/*.example.json` as built-in cases.
`fluxscope/static/app.html` has an empty data slot; the backend fills

```html
<script type="application/json" id="fluxscope-data">{"mode":"report","cases":[RESULT]}</script>
<script type="application/json" id="fluxscope-config">{"api":null}</script>
```

(escape `<` as `<` inside the JSON). With `"api": "/api"` the Analyze
panel talks to the backend API described in `docs/design.md`.

## Use

Open `dist/fluxscope.html` directly from disk (works without network).
Import a result with *Import JSON…*, *Paste…* or drag & drop; exported HTML
reports can be imported too. *Export JSON* writes the current case;
*Export HTML report* writes a self-contained report of the current case.

## Test

```
.venv/bin/python -m pytest tests/test_frontend_e2e.py -q
```

Runs headless Chrome via Playwright (`channel="chrome"`), aborts every
http(s) request and fails on any attempted request or JavaScript error.
Screenshots land in `dist/screenshots/`.
