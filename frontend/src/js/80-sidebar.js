/* FluxScope viewer — left panel: cases, import, analyze, search, tasks,
 * filters, legend, groups, unresolved items, scope; plus modal helpers. */
(function () {
  'use strict';
  var AL = window.FluxScope;
  var U = AL.util;
  var h = U.h;
  var S = AL.sidebar = {};

  // ---------------------------------------------------------------------------
  // modal
  // ---------------------------------------------------------------------------
  S.modal = function (title, body, buttons) {
    var root = document.getElementById('modal-root');
    var close = function () { U.clear(root); document.removeEventListener('keydown', onKey); };
    function onKey(ev) { if (ev.key === 'Escape') close(); }
    document.addEventListener('keydown', onKey);
    var foot = h('div', { class: 'modal-foot' });
    (buttons || [{ label: 'Close', primary: true }]).forEach(function (b) {
      foot.appendChild(h('button', { class: 'btn' + (b.primary ? ' btn-primary' : ''), 'data-testid': b.testid || null, text: b.label, on: { click: function () { if (!b.onClick || b.onClick() !== false) close(); } } }));
    });
    var m = h('div', { class: 'modal-backdrop', on: { click: function (ev) { if (ev.target === m) close(); } } }, [
      h('div', { class: 'modal', role: 'dialog', 'aria-label': title, 'data-testid': 'modal' }, [
        h('div', { class: 'modal-head' }, [h('h2', { text: title }), h('button', { class: 'btn btn-small', text: '✕', title: 'Close', on: { click: close } })]),
        h('div', { class: 'modal-body' }, body),
        foot
      ])
    ]);
    U.clear(root).appendChild(m);
    return close;
  };

  S.showImportErrors = function (name, errors, warnings) {
    var body = [
      h('p', {}, ['The file ', h('b', { text: name }), ' could not be loaded. Nothing was changed.']),
      h('p', { class: 'small muted', text: errors.length + ' problem(s) found' + (errors.length >= 150 ? ' (first 150 shown)' : '') + '. Paths point into the JSON document; ids of list items are shown in braces.' }),
      h('ul', { class: 'err-list', 'data-testid': 'import-errors' }, errors.map(function (e) {
        return h('li', {}, [h('div', { class: 'ep', text: e.path }), h('div', { text: e.message })]);
      }))
    ];
    if (warnings && warnings.length) {
      body.push(h('details', {}, [h('summary', { text: warnings.length + ' warning(s)' }),
        h('ul', { class: 'err-list warnings' }, warnings.map(function (e) { return h('li', {}, [h('div', { class: 'ep', text: e.path }), h('div', { text: e.message })]); }))]));
    }
    body.push(h('p', { class: 'small muted' }, ['The format is defined by ', h('span', { class: 'mono', text: 'schema/fluxscope-analysis-0.1.schema.json' }), '. You can check a file with ', h('span', { class: 'mono', text: 'python -m fluxscope validate FILE' }), '.']));
    S.modal('Could not import ' + name, body);
  };

  S.showPaste = function (onImport) {
    var ta = h('textarea', { placeholder: 'Paste a FluxScope analysis-result JSON document here…', 'data-testid': 'paste-text' });
    S.modal('Paste analysis JSON', [ta], [
      { label: 'Cancel' },
      { label: 'Import', primary: true, testid: 'paste-import', onClick: function () { onImport(ta.value, 'pasted JSON'); } }
    ]);
    setTimeout(function () { ta.focus(); }, 0);
  };

  // ---------------------------------------------------------------------------
  // cases
  // ---------------------------------------------------------------------------
  S.renderCases = function (app) {
    var list = U.clear(document.getElementById('case-list'));
    app.cases.forEach(function (c, i) {
      var sc = (c.data.scope || {}).kind;
      var item = h('div', { class: 'case-item' + (i === app.caseIndex ? ' active' : ''), 'data-case': String(i), title: c.data.meta.description || '', on: { click: function () { app.loadCase(i); } } }, [
        h('div', { class: 'case-title', text: c.data.meta.title || c.data.meta.id }),
        h('div', { class: 'case-meta' }, [
          h('span', { class: 'mini-chip ' + sc, text: AL.SCOPE_LABEL[sc] || sc }),
          h('span', { class: 'mini-chip', text: c.source }),
          c.warnings && c.warnings.length ? h('span', { class: 'mini-chip', title: 'Imported with warnings', text: '⚠ ' + c.warnings.length }) : null
        ]),
        c.source !== 'built-in' && c.source !== 'report' ? h('button', { class: 'case-remove', title: 'Remove this case from the list', text: '×', on: { click: function (ev) { ev.stopPropagation(); app.removeCase(i); } } }) : null
      ]);
      list.appendChild(item);
    });
    if (!app.cases.length) list.appendChild(h('div', { class: 'hint', text: 'No cases loaded. Import an analysis-result JSON to start.' }));
  };

  // ---------------------------------------------------------------------------
  // analyze (backend) panel
  // ---------------------------------------------------------------------------
  S.renderAnalyze = function (app) {
    var box = U.clear(document.getElementById('analyze-panel'));
    var api = app.config && app.config.api;
    if (!api) {
      box.appendChild(h('div', { class: 'analyze-off', 'data-testid': 'analyze-off', html:
        '<b>No analysis backend connected.</b> This page only displays results. ' +
        'To analyse a project, run <code>python -m fluxscope serve</code> and open the page it serves, ' +
        'or run <code>python -m fluxscope analyze …</code> and import the resulting JSON here.' }));
      return;
    }
    var projSel = h('select', { 'data-testid': 'analyze-project' }, [h('option', { value: '', text: 'Loading projects…' })]);
    var custom = h('input', { type: 'text', placeholder: 'or a project path on the server', 'data-testid': 'analyze-path' });
    var llm = h('select', { 'data-testid': 'analyze-llm' }, ['none', 'replay', 'openai', 'anthropic'].map(function (m) {
      return h('option', { value: m, text: { none: 'No LLM (rules and adapters only)', replay: 'Replay recorded LLM outputs', openai: 'OpenAI', anthropic: 'Anthropic' }[m] });
    }));
    var run = h('button', { class: 'btn btn-primary btn-small', text: 'Run analysis', 'data-testid': 'analyze-run' });
    var jobBox = h('div', {});
    box.appendChild(h('div', { class: 'analyze-form' }, [
      h('label', { text: 'Project' }), projSel, custom,
      h('label', { text: 'Semantic interpretation' }), llm, run, jobBox
    ]));
    fetch(api.replace(/\/$/, '') + '/projects').then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(function (list) {
      U.clear(projSel);
      if (!list.length) projSel.appendChild(h('option', { value: '', text: 'No registered projects' }));
      list.forEach(function (p) { projSel.appendChild(h('option', { value: p.id, title: p.description || p.path || '', text: p.name || p.id })); });
    }).catch(function (e) {
      U.clear(projSel).appendChild(h('option', { value: '', text: 'Cannot reach backend: ' + e.message }));
    });
    run.addEventListener('click', function () {
      var project = custom.value.trim() || projSel.value;
      if (!project) { jobBox.textContent = 'Choose a project first.'; return; }
      run.disabled = true;
      U.clear(jobBox).appendChild(h('div', { class: 'job-box', text: 'Submitting…' }));
      fetch(api.replace(/\/$/, '') + '/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ project: project, llm: llm.value }) })
        .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.detail || ('HTTP ' + r.status)); return j; }); })
        .then(function (job) { poll(app, api, job.id, jobBox, function () { run.disabled = false; }); })
        .catch(function (e) { run.disabled = false; U.clear(jobBox).appendChild(h('div', { class: 'job-box' }, [h('span', { class: 'job-status failed', text: 'failed to submit: ' }), e.message])); });
    });
  };

  function poll(app, api, id, jobBox, done) {
    var base = api.replace(/\/$/, '');
    fetch(base + '/jobs/' + encodeURIComponent(id)).then(function (r) { return r.json(); }).then(function (job) {
      U.clear(jobBox).appendChild(h('div', { class: 'job-box', 'data-testid': 'job-box' }, [
        h('div', {}, ['Job ', h('span', { class: 'mono', text: job.id }), ': ', h('span', { class: 'job-status ' + job.status, text: job.status }), job.stage ? h('span', { class: 'muted', text: ' · ' + job.stage }) : null]),
        job.error ? h('div', { class: 'job-status failed', text: job.error }) : null,
        (job.log && job.log.length) ? h('div', { class: 'job-log', text: job.log.slice(-12).join('\n') }) : null
      ]));
      if (job.status === 'succeeded') {
        done();
        fetch(base + '/jobs/' + encodeURIComponent(id) + '/result').then(function (r) { return r.text(); }).then(function (txt) {
          app.importText(txt, 'analysis job ' + id, 'backend');
        });
      } else if (job.status === 'failed') {
        done();
      } else {
        setTimeout(function () { poll(app, api, id, jobBox, done); }, 1000);
      }
    }).catch(function (e) {
      done();
      jobBox.appendChild(h('div', { class: 'job-status failed', text: 'Lost contact with backend: ' + e.message }));
    });
  }

  // ---------------------------------------------------------------------------
  // search
  // ---------------------------------------------------------------------------
  S.renderSearch = function (app) {
    var box = U.clear(document.getElementById('search-results'));
    var M = app.model;
    var q = app.state.search;
    if (!M || !q) return;
    var res = M.search(q, 40);
    if (!res.length) { box.appendChild(h('div', { class: 'hint', text: 'No matches in this case.' })); return; }
    res.forEach(function (r) {
      var x = M.get(r.id);
      var sub = r.type === 'position' ? U.humanize(x.kind) + ' · held by ' + M.label(x.holder) :
        r.type === 'operation' ? U.humanize(x.kind) + (x.entrypoint ? ' · ' + x.entrypoint.contract : '') :
          r.type === 'entity' ? U.humanize(x.kind) : r.type === 'asset' ? U.humanize(x.kind) + (x.symbol ? ' · ' + x.symbol : '') : 'exit path';
      box.appendChild(h('div', { class: 'search-item', 'data-ref': r.id, 'data-testid': 'search-item' }, [
        h('span', { class: 'type-dot ' + r.type, text: AL.TYPE_LETTER[r.type] }),
        h('div', {}, [h('div', { class: 'si-label', text: M.label(r.id) }), h('div', { class: 'si-sub', text: sub })])
      ]));
    });
    if (res.length >= 40) box.appendChild(h('div', { class: 'hint', text: 'Showing the first 40 matches.' }));
  };

  // ---------------------------------------------------------------------------
  // tasks
  // ---------------------------------------------------------------------------
  S.renderTasks = function (app) {
    var box = U.clear(document.getElementById('task-list'));
    var M = app.model;
    if (!M || !M.c.task.length) { box.appendChild(h('div', { class: 'hint', text: 'This case defines no user tasks.' })); return; }
    M.c.task.forEach(function (t) {
      var active = app.state.task === t.id;
      var ans = h('div', { class: 'task-answer', hidden: !app.state.showAnswer[t.id], text: t.answer || 'No expected answer recorded.' });
      var item = h('div', { class: 'task-item' + (active ? ' active' : ''), 'data-task': t.id }, [
        h('div', { class: 'task-q', text: t.question }),
        t.description ? h('div', { class: 'small muted', text: t.description }) : null,
        h('div', { class: 'task-tools' }, [
          t.view ? h('span', { text: 'view: ' + t.view }) : null,
          t.answer ? h('button', { class: 'link-btn', text: app.state.showAnswer[t.id] ? 'Hide expected answer' : 'Show expected answer', on: { click: function (ev) {
            ev.stopPropagation();
            app.state.showAnswer[t.id] = !app.state.showAnswer[t.id];
            S.renderTasks(app);
          } } }) : null
        ]),
        ans
      ]);
      item.addEventListener('click', function () { app.applyTask(t.id); });
      box.appendChild(item);
    });
  };

  // ---------------------------------------------------------------------------
  // filters
  // ---------------------------------------------------------------------------
  function check(label, checked, onChange, count, testid) {
    var cb = h('input', { type: 'checkbox', 'data-testid': testid || null });
    cb.checked = checked;
    cb.addEventListener('change', function () { onChange(cb.checked); });
    return h('label', {}, [cb, label, count !== undefined ? h('span', { class: 'count', text: String(count) }) : null]);
  }

  S.renderFilters = function (app) {
    var box = U.clear(document.getElementById('filter-panel'));
    var M = app.model, st = app.state;
    if (!M) return;
    var V = AL.graph.VIEWS[st.view];
    var counts = M.counts();
    var g1 = h('div', { class: 'filter-group' }, [h('div', { class: 'fg-title', text: 'Node types in ' + V.label + ' view' })]);
    V.nodes.forEach(function (t) {
      g1.appendChild(check(AL.TYPE_LABEL[t], !!st.nodeTypes[st.view][t], function (v) { st.nodeTypes[st.view][t] = v; app.refresh(); }, counts.nodeType[t], 'filter-node-' + t));
    });
    var gc = app.graph ? graphKindCounts(app.graph) : {};
    var g2 = h('div', { class: 'filter-group' }, [h('div', { class: 'fg-title', text: 'Relation kinds' })]);
    V.edges.forEach(function (k) {
      var meta = AL.graph.EDGE_KINDS[k];
      var dataCount = k === 'flow' ? counts.relKind.flow : counts.relKind[k];
      if (!dataCount && !gc[k] && ['holds', 'initiates', 'contains', 'exit_step', 'returns', 'denominated'].indexOf(k) < 0) return;
      g2.appendChild(check(meta.label, !st.hiddenEdgeKinds[k], function (v) { st.hiddenEdgeKinds[k] = !v; app.refresh(); }, gc[k] || 0, 'filter-edge-' + k));
    });
    var g3 = h('div', { class: 'filter-group' }, [h('div', { class: 'fg-title', text: 'Basis' })]);
    ['example', 'static', 'observed', 'config'].forEach(function (b) {
      if (!counts.basis[b]) return;
      g3.appendChild(check(AL.BASIS_LABEL[b], !st.hiddenBasis[b], function (v) { st.hiddenBasis[b] = !v; app.refresh(); }, counts.basis[b], 'filter-basis-' + b));
    });
    var g4 = h('div', { class: 'filter-group' }, [h('div', { class: 'fg-title', text: 'Verification' })]);
    ['supported', 'partially_supported', 'pending', 'contradicted', 'unresolved'].forEach(function (b) {
      if (!counts.verification[b]) return;
      g4.appendChild(check(AL.VERIFICATION_GLYPH[b] + ' ' + AL.VERIFICATION_LABEL[b], !st.hiddenVerification[b], function (v) { st.hiddenVerification[b] = !v; app.refresh(); }, counts.verification[b], 'filter-ver-' + b));
    });
    box.appendChild(g1); box.appendChild(g2); box.appendChild(g3); box.appendChild(g4);
    if (st.view === 'flows' || st.view === 'all') {
      var roots = M.c.operation.filter(function (o) { return !o.parent; });
      var g5 = h('div', { class: 'filter-group', 'data-testid': 'op-filter' }, [h('div', { class: 'fg-title' }, ['Root operations ',
        h('button', { class: 'link-btn small', text: 'all', on: { click: function () { st.opFilter = null; app.refresh({ fit: true }); } } }), ' · ',
        h('button', { class: 'link-btn small', text: 'none', on: { click: function () { st.opFilter = {}; app.refresh({ fit: true }); } } })])]);
      roots.forEach(function (o) {
        var lab = h('label', { title: o.label }, []);
        var cb = h('input', { type: 'checkbox' });
        cb.checked = !st.opFilter || !!st.opFilter[o.id];
        cb.addEventListener('change', function () {
          if (!st.opFilter) { st.opFilter = {}; roots.forEach(function (r) { st.opFilter[r.id] = true; }); }
          if (cb.checked) st.opFilter[o.id] = true; else delete st.opFilter[o.id];
          app.refresh({ fit: true });
        });
        U.append(lab, [cb, h('span', { style: 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:190px', text: o.label }),
          h('button', { class: 'link-btn small', style: 'margin-left:auto', text: 'only', on: { click: function (ev) { ev.preventDefault(); app.onlyOperation(o.id); } } })]);
        g5.appendChild(lab);
      });
      box.appendChild(g5);
    }
    box.appendChild(h('div', { class: 'filter-actions' }, [h('button', { class: 'btn btn-small', text: 'Reset filters', 'data-testid': 'filter-reset', on: { click: function () { app.resetFilters(); } } })]));
  };

  function graphKindCounts(G) {
    var c = {};
    G.edges.forEach(function (e) { c[e.kind] = (c[e.kind] || 0) + 1; });
    return c;
  }

  // ---------------------------------------------------------------------------
  // legend
  // ---------------------------------------------------------------------------
  function swatchNode(type, kind) {
    var st = AL.render.styles;
    var svg = U.s('svg', { width: 30, height: 18, viewBox: '0 0 30 18' });
    if (type === 'entity') {
      var es = st.ENTITY_STYLE[kind] || st.ENTITY_STYLE.unknown;
      svg.appendChild(U.s('rect', { x: 1, y: 2, width: 28, height: 14, rx: 4, fill: es.fill, stroke: es.stroke, 'stroke-width': 1.4, 'stroke-dasharray': es.dash || null }));
      svg.appendChild(U.s('circle', { cx: 8, cy: 9, r: 4.5, fill: es.stroke }));
    } else if (type === 'operation') {
      svg.appendChild(U.s('rect', { x: 1, y: 2, width: 28, height: 14, rx: 7, fill: '#fff8ea', stroke: '#d98a1a', 'stroke-width': 1.4 }));
      svg.appendChild(U.s('circle', { cx: 8, cy: 9, r: 4.5, fill: '#d98a1a' }));
    } else if (type === 'position') {
      var pc = st.POSITION_COLOR[kind] || '#64748b';
      svg.appendChild(U.s('rect', { x: 1, y: 2, width: 28, height: 14, rx: 2, fill: '#fff', stroke: pc, 'stroke-width': 1.4 }));
      svg.appendChild(U.s('rect', { x: 1.5, y: 2.5, width: 4, height: 13, fill: pc }));
    } else if (type === 'asset') {
      svg.appendChild(U.s('path', { d: 'M6,2 H28 V16 H6 L1,9 Z', fill: '#f6f8fb', stroke: '#64748b', 'stroke-width': 1.4 }));
    }
    return svg;
  }
  function swatchEdge(kind, dash, colorOverride) {
    var meta = AL.graph.EDGE_KINDS[kind] || {};
    var col = colorOverride || meta.color || '#64748b';
    var svg = U.s('svg', { width: 34, height: 14, viewBox: '0 0 34 14' });
    svg.appendChild(U.s('line', { x1: 2, y1: 7, x2: 27, y2: 7, stroke: col, 'stroke-width': 2, 'stroke-dasharray': dash || null }));
    var mk = meta.marker;
    if (mk === 'arrow' || colorOverride) svg.appendChild(U.s('path', { d: 'M25,2.5 L32,7 L25,11.5 Z', fill: col }));
    else if (mk === 'open') svg.appendChild(U.s('path', { d: 'M25,2.5 L32,7 L25,11.5', fill: 'none', stroke: col, 'stroke-width': 1.6 }));
    else if (mk === 'diamond') svg.appendChild(U.s('path', { d: 'M26,7 L29.5,3.5 L33,7 L29.5,10.5 Z', fill: '#fff', stroke: col, 'stroke-width': 1.4 }));
    else if (mk === 'dot') svg.appendChild(U.s('circle', { cx: 29, cy: 7, r: 3, fill: col }));
    return svg;
  }

  S.renderLegend = function (app) {
    var box = U.clear(document.getElementById('legend'));
    var M = app.model;
    if (!M) return;
    var counts = M.counts();
    var g = h('div', { class: 'legend-group' }, [h('div', { class: 'lg-title', text: 'Nodes' })]);
    Object.keys(counts.entityKind).forEach(function (k) {
      g.appendChild(h('div', { class: 'legend-row' }, [swatchNode('entity', k), 'Entity · ' + U.humanize(k), h('span', { class: 'count', text: String(counts.entityKind[k]) })]));
    });
    if (counts.nodeType.operation) g.appendChild(h('div', { class: 'legend-row' }, [swatchNode('operation'), 'Operation', h('span', { class: 'count', text: String(counts.nodeType.operation) })]));
    Object.keys(counts.positionKind).forEach(function (k) {
      g.appendChild(h('div', { class: 'legend-row' }, [swatchNode('position', k), 'Position · ' + U.humanize(k), h('span', { class: 'count', text: String(counts.positionKind[k]) })]));
    });
    if (counts.nodeType.asset) g.appendChild(h('div', { class: 'legend-row' }, [swatchNode('asset'), 'Asset', h('span', { class: 'count', text: String(counts.nodeType.asset) })]));
    box.appendChild(g);
    var V = AL.graph.VIEWS[app.state.view];
    var gc = app.graph ? graphKindCounts(app.graph) : {};
    var g2 = h('div', { class: 'legend-group' }, [h('div', { class: 'lg-title', text: 'Edges in ' + V.label + ' view' })]);
    V.edges.forEach(function (k) {
      if (!gc[k]) return;
      var meta = AL.graph.EDGE_KINDS[k];
      var dash = (k === 'binding' || k === 'calls' || k === 'initiates' || k === 'contains') ? '2 3' : null;
      g2.appendChild(h('div', { class: 'legend-row', title: meta.desc }, [swatchEdge(k, dash), meta.label, h('span', { class: 'count', text: String(gc[k]) })]));
      if (k === 'flow') {
        g2.appendChild(h('div', { class: 'legend-row' }, [swatchEdge('flow', null, AL.render.styles.MINT_COLOR), 'Mint (no payer)']));
        g2.appendChild(h('div', { class: 'legend-row' }, [swatchEdge('flow', null, AL.render.styles.BURN_COLOR), 'Burn (no recipient)']));
      }
    });
    if (!Object.keys(gc).length) g2.appendChild(h('div', { class: 'hint', text: 'No edges visible.' }));
    box.appendChild(g2);
    var g3 = h('div', { class: 'legend-group' }, [h('div', { class: 'lg-title', text: 'Status' }),
      h('div', { class: 'legend-row' }, [swatchEdge('calls', null, '#475569'), 'Observed / example / config: solid']),
      h('div', { class: 'legend-row' }, [swatchEdge('calls', '6 4', '#475569'), 'Static candidate: dashed']),
      h('div', { class: 'legend-row small muted', text: 'Badges: ✓ supported · ◐ partial · ? pending · ✗ contradicted · ! unresolved' }),
      h('div', { class: 'legend-row small muted', text: 'Edge width never encodes amounts.' })]);
    box.appendChild(g3);
  };

  // ---------------------------------------------------------------------------
  // groups, unresolved, scope
  // ---------------------------------------------------------------------------
  S.renderGroups = function (app) {
    var box = U.clear(document.getElementById('group-list'));
    var M = app.model;
    if (!M) return;
    var groups = M.c.entity.filter(function (e) { return M.entChildren[e.id]; });
    if (!groups.length) { box.appendChild(h('div', { class: 'hint', text: 'No entity groups in this case.' })); return; }
    groups.forEach(function (g) {
      var cb = h('input', { type: 'checkbox', 'data-testid': 'group-' + g.id });
      cb.checked = !!app.state.collapsedGroups[g.id];
      cb.addEventListener('change', function () { app.toggleGroup(g.id, cb.checked); });
      box.appendChild(h('label', { class: 'group-item' }, [cb, 'Collapse ', h('b', { text: g.label }), h('span', { class: 'muted', text: ' (' + (M.entChildren[g.id] || []).length + ')' })]));
    });
  };

  S.renderUnresolved = function (app) {
    var box = U.clear(document.getElementById('unresolved-list'));
    var M = app.model;
    if (!M) return;
    var any = false;
    M.c.unresolved.forEach(function (u) {
      any = true;
      box.appendChild(h('div', { class: 'unres-item', 'data-ref': u.id, 'data-testid': 'unresolved-item' }, [h('div', { class: 'ui-kind', text: U.humanize(u.kind) }), U.truncate(u.text, 160)]));
    });
    ((M.scope || {}).boundaries || []).forEach(function (b) {
      any = true;
      box.appendChild(h('div', { class: 'unres-item boundary', title: b.reason }, [h('div', { class: 'ui-kind', text: 'analysis boundary' }), h('b', { text: b.target }), ' — ' + b.reason,
        b.evidence && b.evidence.length ? AL.details.evChips(M, b.evidence) : null]));
    });
    if (!any) box.appendChild(h('div', { class: 'hint', text: 'No unresolved items or boundaries recorded.' }));
  };

  S.renderScope = function (app) {
    var box = U.clear(document.getElementById('scope-panel'));
    var M = app.model;
    if (!M) return;
    var sc = M.scope || {}, p = sc.project || {}, ch = sc.chain || {};
    var kv = [];
    function row(k, v, mono) { if (v !== undefined && v !== null && v !== '') kv.push(h('div', { class: 'k', text: k }), h('div', { class: 'v' + (mono ? ' mono' : ''), text: String(v) })); }
    row('scope', AL.SCOPE_LABEL[sc.kind] || sc.kind);
    row('project', p.name);
    row('repository', p.repository, true);
    row('commit', p.commit, true);
    row('version', p.version);
    row('entry contracts', (p.entry_contracts || []).join(', '));
    row('files', (p.files || []).length || null);
    row('snapshot', p.snapshot_sha256 ? U.shortHash(p.snapshot_sha256) : null, true);
    row('chain', [ch.name, ch.chain_id !== undefined ? 'id ' + ch.chain_id : null, ch.block !== undefined ? 'block ' + ch.block : null].filter(Boolean).join(' · '));
    row('generator', M.meta.generator ? M.meta.generator.name + ' ' + M.meta.generator.version : null);
    row('created', M.meta.created_at);
    box.appendChild(h('div', { class: 'kv' }, kv));
    if ((sc.components || []).length) box.appendChild(h('div', { style: 'margin-top:8px' }, [h('div', { class: 'fg-title muted small', text: 'Components' })].concat(sc.components.map(function (c) {
      return h('div', { class: 'small' }, [h('b', { text: c.name }), c.version ? ' ' + c.version : '', h('span', { class: 'muted', text: ' · ' + c.role })]);
    }))));
    if ((sc.assumptions || []).length) box.appendChild(h('div', { style: 'margin-top:8px' }, [h('div', { class: 'fg-title muted small', text: 'Assumptions' })].concat(sc.assumptions.map(function (a) {
      return h('div', { class: 'small' }, [h('span', { class: 'mini-chip', text: a.source }), ' ', a.text]);
    }))));
    if ((sc.notes || []).length) box.appendChild(h('ul', { class: 'issue-list', style: 'margin-top:8px' }, sc.notes.map(function (n) { return h('li', { text: n }); })));
    var prov = M.data.provenance;
    if (prov && (prov.stages || []).length) {
      box.appendChild(h('div', { style: 'margin-top:8px' }, [h('div', { class: 'fg-title muted small', text: 'Pipeline stages' })].concat(prov.stages.map(function (s) {
        return h('div', { class: 'small mono', text: (s.name || s.stage || 'stage') + (s.duration_s !== undefined ? ' · ' + s.duration_s + 's' : '') + (s.items !== undefined ? ' · ' + s.items + ' items' : '') });
      }))));
    }
    if (prov && (prov.llm_calls || []).length) box.appendChild(h('div', { class: 'small', style: 'margin-top:6px', text: U.plural(prov.llm_calls.length, 'recorded LLM call') }));
    var c = app.cases[app.caseIndex];
    if (c && c.warnings && c.warnings.length) {
      box.appendChild(h('details', { style: 'margin-top:8px' }, [h('summary', { class: 'small', text: U.plural(c.warnings.length, 'import warning') }),
        h('ul', { class: 'issue-list' }, c.warnings.slice(0, 60).map(function (w) { return h('li', { class: 'small' }, [h('span', { class: 'mono', text: w.path }), ': ' + w.message]); }))]));
    }
  };
})();
