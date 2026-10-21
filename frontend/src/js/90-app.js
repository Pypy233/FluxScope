/* FluxScope viewer — application state, wiring, import / export. */
(function () {
  'use strict';
  var AL = window.FluxScope;
  var U = AL.util;
  var h = U.h;
  var S = AL.sidebar;

  var app = AL.app = {
    cases: [],
    caseIndex: -1,
    model: null,
    graph: null,
    state: null,
    config: {},
    schema: null,
    mode: 'app'
  };

  function freshState(M, prev) {
    var nodeTypes = {};
    Object.keys(AL.graph.VIEWS).forEach(function (v) {
      var V = AL.graph.VIEWS[v];
      nodeTypes[v] = {};
      V.nodes.forEach(function (t) { nodeTypes[v][t] = V.defaultOff.indexOf(t) < 0; });
    });
    var rootOps = M.c.operation.filter(function (o) { return !o.parent; }).length;
    var view = rootOps && rootOps <= 12 ? 'flows' : (M.c.position.length ? 'holdings' : (rootOps ? 'flows' : 'all'));
    // results with embedded sources open in the Code view: source next to a small graph
    if (AL.codeview.hasCode(M)) view = 'code';
    return {
      view: view,
      code: { mode: 'map', op: null, pins: [] },
      nodeTypes: nodeTypes,
      hiddenEdgeKinds: {}, hiddenBasis: {}, hiddenVerification: {},
      collapsedGroups: {}, expandedOps: {},
      depLimit: 2, depExpanded: {}, depCollapsed: {},
      selection: null,
      related: prev ? prev.related : 'both',
      labels: prev ? prev.labels : 'auto',
      dir: prev ? prev.dir : 'auto',
      mergeAssets: 'auto',
      opFilter: null,
      focus: null, focusEdges: null, task: null, showAnswer: {},
      search: ''
    };
  }

  // ---------------------------------------------------------------------------
  // cases
  // ---------------------------------------------------------------------------
  app.addCase = function (data, source, warnings) {
    app.cases.push({ data: data, source: source, warnings: warnings || [] });
    return app.cases.length - 1;
  };

  app.loadCase = function (i) {
    if (i < 0 || i >= app.cases.length) return;
    app.caseIndex = i;
    var c = app.cases[i];
    app.model = new AL.Model(c.data);
    app.state = freshState(app.model, app.state);
    AL.code.reset(app.model);
    AL.codeview.reset(app.model);
    document.getElementById('search-input').value = '';
    renderTop();
    S.renderCases(app);
    S.renderTasks(app);
    S.renderGroups(app);
    S.renderUnresolved(app);
    S.renderScope(app);
    S.renderSearch(app);
    app.refresh({ fit: true });
    if (app.state.view === 'code') openFirstFile();
  };

  function openFirstFile() {
    var M = app.model;
    var first = M.c.entity.filter(function (e) { return e.kind === 'contract' && e.in_scope !== false && e.contract && M.sources[e.contract.file]; })[0];
    if (first) AL.codeview.openContract(first.id, false);
  }

  app.removeCase = function (i) {
    app.cases.splice(i, 1);
    if (!app.cases.length) { app.caseIndex = -1; app.model = null; renderEmpty(); S.renderCases(app); return; }
    app.loadCase(Math.min(i, app.cases.length - 1));
  };

  function renderEmpty() {
    U.clear(document.getElementById('graph'));
    AL.render.init(document.getElementById('graph'), handlers);
    var em = document.getElementById('graph-empty');
    em.hidden = false;
    em.textContent = 'No analysis result loaded. Import a FluxScope JSON file (left panel) or drop it on this page.';
    U.clear(document.getElementById('details')).appendChild(h('div', { class: 'd-empty', text: 'Nothing selected.' }));
    U.clear(document.getElementById('scope-banner'));
  }

  // ---------------------------------------------------------------------------
  // rendering
  // ---------------------------------------------------------------------------
  function renderTop() {
    var M = app.model, sc = M.scope || {}, p = sc.project || {}, ch = sc.chain || {};
    var b = U.clear(document.getElementById('scope-banner'));
    b.appendChild(h('span', { class: 'scope-chip ' + sc.kind, title: AL.SCOPE_EXPLAIN[sc.kind] || '', text: (AL.SCOPE_LABEL[sc.kind] || sc.kind) + (M.meta.synthetic ? ' · SYNTHETIC' : '') }));
    var bits = [];
    if (p.name) bits.push('<b>' + U.esc(p.name) + '</b>');
    if (p.commit) bits.push('commit ' + U.esc(String(p.commit).slice(0, 10)));
    else if (p.version) bits.push(U.esc(p.version));
    if (ch.name || ch.chain_id !== undefined) bits.push(U.esc(ch.name || ('chain ' + ch.chain_id)) + (ch.block !== undefined ? ' @ block ' + ch.block : ''));
    bits.push(U.esc(AL.SCOPE_EXPLAIN[sc.kind] || ''));
    b.appendChild(h('span', { class: 'scope-text', html: bits.join(' · ') }));
    U.$$('#view-switch button').forEach(function (btn) { btn.classList.toggle('active', btn.dataset.view === app.state.view); });
    document.title = (M.meta.title || 'FluxScope') + ' — FluxScope';
  }

  // The Code view puts the source next to the graph and the details under the graph. In a report the
  // left panel is hidden there by default (the gallery needs it for its cases). The left-panel toggle
  // remembers its state per kind of view.
  var leftHidden = { code: null, graph: false };
  function layoutFor(view) {
    var code = view === 'code';
    document.getElementById('app').dataset.view = code ? 'code' : 'graph';
    document.getElementById('code-pane').hidden = !code;
    document.getElementById('work-split').hidden = !code;
    document.getElementById('details-split').hidden = !code;
    var details = document.getElementById('details'), slot = document.getElementById('details-slot');
    if (code && details.parentElement !== slot) slot.appendChild(details);
    if (!code && details.parentElement === slot) document.getElementById('main').appendChild(details);
    if (leftHidden.code === null) leftHidden.code = app.mode === 'report';
    document.getElementById('main').classList.toggle('no-left', leftHidden[code ? 'code' : 'graph']);
  }

  function renderCodeTools() {
    var M = app.model, st = app.state, box = U.clear(document.getElementById('code-tools'));
    if (st.view !== 'code') return;
    var c = st.code, fns = AL.graph.codeRoots(Object.assign({}, st, { code: Object.assign({}, c, { mode: 'fn' }) }));
    box.appendChild(h('div', { class: 'seg' }, [
      h('button', { class: c.mode === 'map' ? 'active' : '', 'data-code-mode': 'map', 'data-testid': 'code-map', title: 'Contracts, users and the assets moved between them', text: 'System map' }),
      h('button', { class: c.mode !== 'map' ? 'active' : '', 'data-code-mode': 'fn', 'data-testid': 'code-fn', disabled: !fns.length, title: fns.length ? 'Only the open function and pinned functions' : 'Open a function in the source panel', text: 'Functions' + (fns.length ? ' (' + fns.length + ')' : '') })
    ]));
    fns.forEach(function (id) {
      var o = M.get(id), pinned = c.pins.indexOf(id) >= 0;
      if (!o) return;
      box.appendChild(h('span', { class: 'pin-chip' + (id === c.op ? ' current' : ''), 'data-open-op': id, title: o.label + (pinned ? ' (pinned)' : ' (open, not pinned)') }, [
        h('span', { text: (o.entrypoint && o.entrypoint.function) || o.label }),
        pinned ? h('button', { 'data-unpin': id, title: 'Remove from the graph', text: '×' })
               : h('button', { 'data-pin': id, title: 'Pin: keep it in the graph while opening other functions', text: 'pin' })
      ]));
    });
  }

  /** Rebuild graph + panels. opts: {fit, anchor, keepDetails} */
  app.refresh = function (opts) {
    opts = opts || {};
    var M = app.model, st = app.state;
    if (!M) return;
    U.$$('#view-switch button').forEach(function (btn) { btn.classList.toggle('active', btn.dataset.view === st.view); });
    document.getElementById('depth-value').textContent = st.depLimit === Infinity ? '∞' : String(st.depLimit);
    layoutFor(st.view);
    var depthOn = !!AL.graph.VIEWS[st.view].depth;
    ['btn-depth-dec', 'btn-depth-inc'].forEach(function (id) { document.getElementById(id).disabled = !depthOn; });
    app.graph = AL.graph.build(M, st);
    var em = document.getElementById('graph-empty');
    em.hidden = app.graph.nodes.length > 0;
    if (!app.graph.nodes.length) em.textContent = st.view === 'code' ? 'No asset flows between contracts and users were found.' : 'Nothing to show in the ' + AL.graph.VIEWS[st.view].label + ' view with the current filters. Try another view or reset filters.';
    AL.render.draw(app.graph, { fit: opts.fit !== false && !opts.anchor, anchor: opts.anchor, labels: st.labels, dir: st.dir });
    var stats = app.graph.nodes.length + ' nodes · ' + app.graph.edges.length + ' edges';
    if (app.graph.map) stats += ' · one edge per direction lists every asset moved';
    if (app.graph.mergedAssets) stats += ' · flows of several assets between the same pair share one edge';
    if (app.graph.noFlowOps) stats += ' · ' + (st.view === 'code' ? U.plural(app.graph.noFlowOps, 'call') + ' that move no assets not drawn (CALL marks in the source)' : U.plural(app.graph.noFlowOps, 'operation') + ' without asset flows not shown');
    if (st.opFilter && (st.view === 'flows' || st.view === 'all')) {
      var roots = M.c.operation.filter(function (o) { return !o.parent; });
      stats += ' · showing ' + roots.filter(function (o) { return st.opFilter[o.id]; }).length + ' of ' + roots.length + ' root operations';
    }
    document.getElementById('graph').classList.toggle('dense', app.graph.edges.length > 45);
    document.getElementById('graph-stats').textContent = stats;
    S.renderFilters(app);
    S.renderLegend(app);
    S.renderGroups(app);
    applyHighlight();
    if (!opts.keepDetails) renderDetails();
    if (st.view === 'code') { renderCodeTools(); AL.codeview.update(); }
  };

  // ---------------------------------------------------------------------------
  // Code view
  // ---------------------------------------------------------------------------
  /** Open a function in the Code view: its source on the left, its graph (and pinned ones) on the right. */
  app.openFunction = function (opId, opts) {
    opts = opts || {};
    var M = app.model, st = app.state;
    if (!M || !M.get(opId)) return;
    var root = AL.codeview.rootOf(opId);
    var viewChanged = st.view !== 'code';
    st.view = 'code';
    st.code.mode = 'fn';
    st.code.op = root;
    // the function and its direct calls; deeper calls open as the reader follows them
    st.expandedOps[root] = true;
    M.opAncestors(opId).forEach(function (a) { st.expandedOps[a] = true; });
    st.selection = { id: opId };
    st.focus = null; st.focusExit = null; st.task = null;
    if (viewChanged) renderTop();
    app.refresh({ fit: true });
    if (opId === root) AL.codeview.openDef(root, opts.keepScroll);
    else AL.codeview.reveal({ id: opId });
    S.renderTasks(app);
  };

  app.togglePin = function (opId) {
    var st = app.state, c = st.code;
    var i = c.pins.indexOf(opId);
    if (i >= 0) c.pins.splice(i, 1);
    else {
      c.pins.push(opId);
      if (c.mode === 'map') { c.mode = 'fn'; if (!c.op) c.op = opId; }
      st.expandedOps[opId] = true;
    }
    if (!AL.graph.codeRoots(st).length) c.mode = 'map';
    app.refresh({ fit: true, keepDetails: true });
  };

  app.setCodeMode = function (mode) {
    var c = app.state.code;
    if (mode === 'fn' && !AL.graph.codeRoots(Object.assign({}, app.state, { code: Object.assign({}, c, { mode: 'fn' }) })).length) return;
    c.mode = mode;
    app.state.selection = null;
    app.refresh({ fit: true });
  };

  function renderDetails() {
    var sel = app.state.selection;
    var arg = null;
    if (sel && sel.edgeKey) {
      var e = findEdge(sel.edgeKey);
      arg = e ? { edge: e } : (sel.edge ? { edge: sel.edge } : null);
    } else if (sel && sel.id) arg = { id: sel.id };
    AL.details.render(document.getElementById('details'), arg);
  }

  function findEdge(key) {
    if (!app.graph) return null;
    for (var i = 0; i < app.graph.edges.length; i++) if (app.graph.edges[i].key === key) return app.graph.edges[i];
    return null;
  }

  /** Map arbitrary ids to visible graph node ids. */
  function graphNodesFor(id) {
    var M = app.model, G = app.graph;
    if (!G) return [];
    if (G.byId[id] && !G.byId[id].hidden) return [id];
    var t = M.type(id), x = M.get(id);
    var out = [];
    if (t === 'relation') { out = graphNodesFor(x.source).concat(graphNodesFor(x.target)); }
    else if (t === 'exit') {
      out = graphNodesFor(x.position);
      (x.steps || []).forEach(function (s) { out = out.concat(graphNodesFor(s.operation)); if (s.actor) out = out.concat(graphNodesFor(s.actor)); });
      (x.returns || []).forEach(function (f) { if (f.to) out = out.concat(graphNodesFor(f.to)); });
    } else if (t === 'operation') {
      // nearest visible ancestor
      var anc = M.opAncestors(id).reverse();
      for (var i = 0; i < anc.length; i++) if (G.byId[anc[i]] && !G.byId[anc[i]].hidden) return [anc[i]];
    } else if (t === 'entity') {
      var ea = M.entAncestors(id).reverse();
      for (var j = 0; j < ea.length; j++) if (G.byId[ea[j]] && !G.byId[ea[j]].hidden) return [ea[j]];
    } else if (t === 'unresolved') {
      (x.subjects || []).forEach(function (s) { out = out.concat(graphNodesFor(s)); });
    }
    return U.uniq(out);
  }

  app.isVisible = function (id) {
    var G = app.graph, M = app.model;
    if (!G) return false;
    var t = M.type(id);
    if (t === 'relation') return G.edges.some(function (e) { return e.refs.some(function (r) { return r.type === 'relation' && r.id === id; }); });
    if (t === 'exit') return app.state.view === 'exits' && graphNodesFor(id).length > 0;
    return !!(G.byId[id] && !G.byId[id].hidden);
  };

  function related(startIds, mode) {
    var G = app.graph;
    var nodes = Object.create(null), edges = Object.create(null);
    startIds.forEach(function (id) { nodes[id] = true; });
    function walk(down) {
      var q = startIds.slice(), seen = Object.create(null);
      startIds.forEach(function (id) { seen[id] = true; });
      while (q.length) {
        var u = q.shift();
        G.edges.forEach(function (e) {
          var from = down ? e.source : e.target, to = down ? e.target : e.source;
          if (from !== u) return;
          edges[e.id] = true;
          nodes[to] = true;
          if (!seen[to]) { seen[to] = true; q.push(to); }
        });
      }
    }
    if (mode === 'down' || mode === 'both') walk(true);
    if (mode === 'up' || mode === 'both') walk(false);
    return { nodes: nodes, edges: edges };
  }

  function applyHighlight() {
    var st = app.state, G = app.graph;
    if (!G) return;
    var hl = { selNode: null, selEdge: null, nodes: null, edges: null, matches: null };
    if (st.search) {
      hl.matches = Object.create(null);
      app.model.search(st.search, 200).forEach(function (r) { graphNodesFor(r.id).forEach(function (n) { hl.matches[n] = true; }); });
    }
    if (st.focus) {
      hl.nodes = Object.create(null); hl.edges = Object.create(null);
      st.focus.forEach(function (id) { graphNodesFor(id).forEach(function (n) { hl.nodes[n] = true; }); });
      G.edges.forEach(function (e) {
        var inFocus = hl.nodes[e.source] && hl.nodes[e.target];
        var exitRef = st.focusExit && e.refs.some(function (r) { return (r.type === 'exit' || r.type === 'exit-return') && r.id === st.focusExit; });
        var relRef = e.refs.some(function (r) { return st.focus.indexOf(r.id) >= 0; });
        if (exitRef || relRef || (inFocus && !st.focusExit)) hl.edges[e.id] = true;
      });
    }
    var sel = st.selection;
    if (sel && sel.edgeKey) {
      var e = findEdge(sel.edgeKey);
      if (e) {
        hl.selEdge = e.id;
        if (!st.focus) {
          hl.nodes = Object.create(null); hl.edges = Object.create(null);
          hl.nodes[e.source] = hl.nodes[e.target] = true; hl.edges[e.id] = true;
        }
      }
    } else if (sel && sel.id) {
      var ids = graphNodesFor(sel.id);
      if (ids.length === 1 && ids[0] === sel.id) hl.selNode = sel.id;
      else if (ids.length === 1) hl.selNode = ids[0];
      // the Code view graph is already small: no dimming of everything but the related paths
      if (!st.focus && st.related !== 'off' && st.view !== 'code' && ids.length) {
        var r = related(ids, st.related);
        hl.nodes = r.nodes; hl.edges = r.edges;
      } else if (!st.focus && ids.length > 1) {
        hl.nodes = Object.create(null); ids.forEach(function (n) { hl.nodes[n] = true; }); hl.edges = Object.create(null);
      }
    }
    AL.render.highlight(hl);
  }

  // ---------------------------------------------------------------------------
  // selection / navigation
  // ---------------------------------------------------------------------------
  app.select = function (id, opts) {
    opts = opts || {};
    var M = app.model;
    if (!M) return;
    // Code view: an operation outside the functions in the graph is opened as a function
    if (app.state.view === 'code' && M.type(id) === 'operation') {
      if (app.state.code.mode === 'map' || AL.graph.codeRoots(app.state).indexOf(AL.codeview.rootOf(id)) < 0) { app.openFunction(id); return; }
      // a nested call of a function in the graph: open the levels down to it
      var hidden = M.opAncestors(id).filter(function (a) { return !app.state.expandedOps[a]; });
      if (hidden.length) {
        hidden.forEach(function (a) { app.state.expandedOps[a] = true; });
        app.state.selection = { id: id };
        app.refresh({ anchor: hidden[0] });
        AL.codeview.reveal({ id: id });
        return;
      }
    }
    app.state.selection = { id: id };
    if (!opts.keepFocus) { app.state.focus = null; app.state.focusExit = null; if (!opts.keepTask) app.state.task = null; }
    if (opts.reveal && !app.isVisible(id)) {
      app.reveal(id, { silent: true });
    } else {
      applyHighlight();
      renderDetails();
    }
    var g = graphNodesFor(id);
    if (g.length) AL.render.centerOn(g[0]);
    if (!opts.keepTask) S.renderTasks(app);
    if (app.state.view === 'code') AL.codeview.reveal({ id: id });
  };

  app.selectEdge = function (graphEdgeId) {
    var e = app.graph.edges.filter(function (x) { return x.id === graphEdgeId; })[0];
    if (!e) return;
    app.state.selection = { edgeKey: e.key, edge: e };
    app.state.focus = null; app.state.focusExit = null;
    applyHighlight();
    renderDetails();
    if (app.state.view === 'code') AL.codeview.reveal({ edge: e });
  };

  app.clearSelection = function () {
    app.state.selection = null;
    app.state.focus = null; app.state.focusExit = null; app.state.task = null;
    applyHighlight();
    renderDetails();
    S.renderTasks(app);
    if (app.state.view === 'code') AL.codeview.reveal(null);
  };

  /** Make an object visible: switch view, expand ancestors, dependency path, reset filters if needed. */
  app.reveal = function (id, opts) {
    opts = opts || {};
    var M = app.model, st = app.state;
    var t = M.type(id), x = M.get(id);
    if (!x) return;
    if (st.view === 'code' && t === 'operation') { app.openFunction(id); return; }
    var targets = [id];
    if (t === 'relation') targets = [x.source, x.target];
    if (t === 'exit') targets = [x.position];
    if (t === 'unresolved') targets = (x.subjects || []).slice(0, 1);
    var pv = AL.graph.preferredView(M, id, st.view);
    if (!app.isVisible(id)) st.view = pv;
    targets.forEach(function (tid) {
      var tt = M.type(tid);
      if (tt === 'operation') M.opAncestors(tid).forEach(function (a) { st.expandedOps[a] = true; });
      if (tt === 'entity' || tt === 'position') {
        var holder = tt === 'position' ? M.get(tid).holder : tid;
        M.entAncestors(holder).forEach(function (a) { delete st.collapsedGroups[a]; });
      }
    });
    app.graph = AL.graph.build(M, st);
    // dependency path
    var di = app.graph.depInfo;
    if (di) {
      targets.forEach(function (tid) {
        var cur = tid, n = 0;
        while (di.parent[cur] && n++ < 50) { cur = di.parent[cur]; st.depExpanded[cur] = true; delete st.depCollapsed[cur]; }
      });
      app.graph = AL.graph.build(M, st);
    }
    var visible = targets.every(function (tid) { return app.graph.byId[tid] && !app.graph.byId[tid].hidden; }) || t === 'exit' && app.isVisible(id);
    if (!visible) {
      app.resetFilters({ silent: true });
      toast('Filters were reset so the item can be shown.');
    }
    app.refresh({ fit: !opts.keepView });
    var g = graphNodesFor(id);
    if (g.length) AL.render.centerOn(g[0]);
  };

  app.setView = function (v) {
    if (!AL.graph.VIEWS[v]) return;
    var was = app.state.view;
    app.state.view = v;
    app.refresh({ fit: true });
    renderTop();
    if (v === 'code' && was !== 'code') {
      var sel = app.state.selection;
      if (sel && sel.id && app.model.type(sel.id) === 'operation') app.openFunction(sel.id);
      else if (!document.querySelector('#code-body .src-view')) openFirstFile();
    }
  };

  app.resetFilters = function (opts) {
    var st = app.state, fresh = freshState(app.model, st);
    st.nodeTypes = fresh.nodeTypes;
    st.hiddenEdgeKinds = {}; st.hiddenBasis = {}; st.hiddenVerification = {};
    if (!(opts && opts.silent)) app.refresh({ fit: true });
  };

  app.toggleGroup = function (gid, collapsed) {
    if (collapsed === undefined) collapsed = !app.state.collapsedGroups[gid];
    if (collapsed) app.state.collapsedGroups[gid] = true; else delete app.state.collapsedGroups[gid];
    app.refresh({ anchor: gid });
  };

  app.toggleOp = function (oid) {
    if (app.state.expandedOps[oid]) {
      delete app.state.expandedOps[oid];
      app.model.opDescendants(oid).forEach(function (d) { delete app.state.expandedOps[d.id]; });
    } else app.state.expandedOps[oid] = true;
    app.refresh({ anchor: oid });
  };

  /** Restrict the Flows view to the root operation containing `oid` and expand it one level. */
  app.onlyOperation = function (oid) {
    var M = app.model, st = app.state;
    var anc = M.opAncestors(oid);
    var root = anc.length ? anc[0] : oid;
    st.opFilter = {};
    st.opFilter[root] = true;
    st.expandedOps[root] = true;
    anc.forEach(function (a) { st.expandedOps[a] = true; });
    if (st.view !== 'flows' && st.view !== 'all') st.view = 'flows';
    st.selection = { id: oid };
    app.refresh({ fit: true });
    renderTop();
  };

  app.applyTask = function (tid) {
    var M = app.model, t = M.get(tid);
    if (!t) return;
    var st = app.state;
    st.task = tid;
    if (t.view && AL.graph.VIEWS[t.view]) st.view = t.view;
    // reveal all focus items in that view
    (t.focus || []).forEach(function (f) {
      var tt = M.type(f);
      if (tt === 'operation') {
        M.opAncestors(f).forEach(function (a) { st.expandedOps[a] = true; });
        if (M.opChildren[f]) st.expandedOps[f] = true;
      }
    });
    st.depLimit = Infinity;
    var focus = [];
    (t.focus || []).forEach(function (f) {
      focus.push(f);
      var tt = M.type(f), x = M.get(f);
      if (tt === 'asset') (M.posByAsset[f] || []).forEach(function (p) { focus.push(p.id, p.holder); });
      if (tt === 'operation') {
        if (x.initiator) focus.push(x.initiator);
        M.opDescendants(f).forEach(function (d) { focus.push(d.id); });
        M.flowsUnder(f).forEach(function (fr) { if (fr.flow.from) focus.push(fr.flow.from); if (fr.flow.to) focus.push(fr.flow.to); });
      }
      if (tt === 'position') {
        focus.push(x.holder);
        (M.relFrom[f] || []).forEach(function (r) { if (r.kind === 'claim' || r.kind === 'depends_on' || r.kind === 'collateral' || r.kind === 'debt' || r.kind === 'exit') focus.push(r.target); });
      }
    });
    st.selection = t.focus && t.focus.length ? { id: t.focus[0] } : null;
    app.graph = AL.graph.build(M, st);
    var firstExit = (t.focus || []).filter(function (f) { return M.type(f) === 'exit'; })[0];
    st.focus = U.uniq(focus);
    st.focusExit = firstExit || null;
    app.refresh({ fit: true });
    renderTop();
    S.renderTasks(app);
  };

  app.showExit = function (xid) {
    var st = app.state;
    st.view = 'exits';
    st.selection = { id: xid };
    st.focus = [xid];
    st.focusExit = xid;
    app.refresh({ fit: true });
    renderTop();
  };

  app.showEvidence = function (evId) {
    AL.code.show(evId);
    U.$$('.ev-chip').forEach(function (c) { c.classList.toggle('active', c.dataset.ev === evId); });
  };

  // ---------------------------------------------------------------------------
  // import / export
  // ---------------------------------------------------------------------------
  function toast(msg, ms) {
    var t = document.getElementById('toast');
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { t.hidden = true; }, ms || 3200);
  }
  app.toast = toast;

  function extractFromHTML(text) {
    var doc = new DOMParser().parseFromString(text, 'text/html');
    var el = doc.getElementById('fluxscope-data');
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  }

  /** Import a JSON (or exported HTML report) text. Returns true on success. */
  app.importText = function (text, name, source) {
    var data;
    text = String(text || '');
    var trimmed = text.replace(/^﻿/, '').trim();
    if (!trimmed) { S.showImportErrors(name, [{ path: '(file)', message: 'the file is empty' }]); return false; }
    if (trimmed[0] === '<') {
      var payload = extractFromHTML(trimmed);
      if (!payload || !payload.cases || !payload.cases.length) {
        S.showImportErrors(name, [{ path: '(file)', message: 'this HTML file does not contain FluxScope case data' }]);
        return false;
      }
      var okAny = false;
      payload.cases.forEach(function (c, i) { okAny = importData(c, name + (payload.cases.length > 1 ? ' #' + (i + 1) : ''), source || 'imported') || okAny; });
      return okAny;
    }
    try { data = JSON.parse(trimmed); } catch (e) {
      S.showImportErrors(name, [{ path: '(file)', message: 'not valid JSON: ' + e.message }]);
      return false;
    }
    return importData(data, name, source || 'imported');
  };

  function importData(data, name, source) {
    var res = AL.validate(data, app.schema);
    if (!res.ok) { S.showImportErrors(name, res.errors, res.warnings); return false; }
    var i = app.addCase(data, source, res.warnings);
    app.loadCase(i);
    toast('Loaded "' + (data.meta.title || data.meta.id) + '"' + (res.warnings.length ? ' with ' + U.plural(res.warnings.length, 'warning') : '') + '.');
    return true;
  }

  app.importFile = function (file) {
    var reader = new FileReader();
    reader.onload = function () { app.importText(reader.result, file.name, 'imported'); };
    reader.onerror = function () { S.showImportErrors(file.name, [{ path: '(file)', message: 'could not read the file' }]); };
    reader.readAsText(file);
  };

  app.exportJSON = function () {
    var c = app.cases[app.caseIndex];
    if (!c) return;
    U.download(U.safeFileName(c.data.meta.id) + '.json', JSON.stringify(c.data, null, 2) + '\n', 'application/json');
    toast('Exported ' + U.safeFileName(c.data.meta.id) + '.json');
  };

  app.buildReportHTML = function () {
    var c = app.cases[app.caseIndex];
    if (!c) return null;
    var doc = AL.pristine.cloneNode(true);
    var payload = { mode: 'report', cases: [c.data], default: c.data.meta.id, exported_at: new Date().toISOString(), exported_from: c.data.meta.title || c.data.meta.id };
    doc.querySelector('#fluxscope-data').textContent = U.embedJSON(payload);
    doc.querySelector('#fluxscope-config').textContent = U.embedJSON({ api: null });
    return '<!DOCTYPE html>\n' + doc.outerHTML + '\n';
  };

  app.exportHTML = function () {
    var c = app.cases[app.caseIndex];
    if (!c) return;
    var html = app.buildReportHTML();
    U.download(U.safeFileName(c.data.meta.id) + '.report.html', html, 'text/html');
    toast('Exported offline report ' + U.safeFileName(c.data.meta.id) + '.report.html');
  };

  // ---------------------------------------------------------------------------
  // wiring
  // ---------------------------------------------------------------------------
  var handlers = {
    onSelectNode: function (id) { app.select(id); },
    onSelectEdge: function (id) { app.selectEdge(id); },
    onBackground: function () { app.clearSelection(); },
    onHover: function (target) { if (app.state && app.state.view === 'code') AL.codeview.hoverGraph(target); },
    onHandle: function (hd) {
      var st = app.state;
      if (hd.action === 'expand-group') return app.toggleGroup(hd.target, false);
      if (hd.action === 'expand-op' || hd.action === 'collapse-op') return app.toggleOp(hd.target);
      if (hd.action === 'expand-dep') { st.depExpanded[hd.target] = true; delete st.depCollapsed[hd.target]; }
      if (hd.action === 'collapse-dep') { st.depCollapsed[hd.target] = true; delete st.depExpanded[hd.target]; }
      app.refresh({ anchor: hd.target });
    },
    onGroupTag: function (gid) { app.toggleGroup(gid, true); }
  };

  function wire() {
    U.$$('#view-switch button').forEach(function (b) { b.addEventListener('click', function () { app.setView(b.dataset.view); }); });
    document.getElementById('btn-export-json').addEventListener('click', app.exportJSON);
    document.getElementById('btn-export-html').addEventListener('click', app.exportHTML);
    document.getElementById('btn-fit').addEventListener('click', function () { AL.render.fit({ all: true }); });
    document.getElementById('btn-toggle-left').addEventListener('click', function () {
      var k = app.state && app.state.view === 'code' ? 'code' : 'graph';
      leftHidden[k] = !leftHidden[k];
      document.getElementById('main').classList.toggle('no-left', leftHidden[k]);
      setTimeout(function () { AL.render.fit(); }, 0);
    });
    document.getElementById('code-tools').addEventListener('click', function (ev) {
      var b = ev.target.closest('[data-code-mode], [data-pin], [data-unpin], [data-open-op]');
      if (!b) return;
      if (b.dataset.codeMode) app.setCodeMode(b.dataset.codeMode);
      else if (b.dataset.pin || b.dataset.unpin) app.togglePin(b.dataset.pin || b.dataset.unpin);
      else app.openFunction(b.dataset.openOp);
    });
    document.getElementById('layout-dir').addEventListener('change', function (ev) { app.state.dir = ev.target.value; app.refresh({ fit: true }); });
    document.getElementById('btn-zoom-in').addEventListener('click', function () { AL.render.zoomBy(1.25); });
    document.getElementById('btn-zoom-out').addEventListener('click', function () { AL.render.zoomBy(0.8); });
    document.getElementById('btn-depth-dec').addEventListener('click', function () {
      var st = app.state;
      st.depLimit = st.depLimit === Infinity ? 3 : Math.max(0, st.depLimit - 1);
      st.depExpanded = {}; st.depCollapsed = {};
      app.refresh({ fit: true });
    });
    document.getElementById('btn-depth-inc').addEventListener('click', function () {
      var st = app.state;
      st.depLimit = st.depLimit === Infinity ? Infinity : st.depLimit + 1;
      if (st.depLimit > 8) st.depLimit = Infinity;
      app.refresh({ fit: true });
    });
    document.getElementById('btn-expand-all').addEventListener('click', function () {
      var st = app.state, M = app.model;
      st.collapsedGroups = {};
      M.c.operation.forEach(function (o) { if (M.opChildren[o.id]) st.expandedOps[o.id] = true; });
      st.depLimit = Infinity; st.depExpanded = {}; st.depCollapsed = {};
      app.refresh({ fit: true });
    });
    document.getElementById('btn-collapse-all').addEventListener('click', function () {
      var st = app.state, M = app.model;
      st.expandedOps = {};
      st.collapsedGroups = {};
      M.c.entity.forEach(function (e) { if (M.entChildren[e.id] && !e.parent) st.collapsedGroups[e.id] = true; });
      st.depLimit = 1; st.depExpanded = {}; st.depCollapsed = {};
      app.refresh({ fit: true });
    });
    document.getElementById('related-mode').addEventListener('change', function (ev) { app.state.related = ev.target.value; applyHighlight(); });
    document.getElementById('label-mode').addEventListener('change', function (ev) { app.state.labels = ev.target.value; app.refresh({ fit: false }); });
    var fi = document.getElementById('file-input');
    fi.addEventListener('change', function () { if (fi.files && fi.files[0]) app.importFile(fi.files[0]); fi.value = ''; });
    document.getElementById('btn-paste').addEventListener('click', function () { S.showPaste(function (txt, name) { return app.importText(txt, name, 'pasted'); }); });
    var si = document.getElementById('search-input');
    si.addEventListener('input', U.debounce(function () { app.state.search = si.value.trim(); S.renderSearch(app); applyHighlight(); }, 120));
    si.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter') { var first = U.$('#search-results .search-item'); if (first) first.click(); }
      if (ev.key === 'Escape') { si.value = ''; app.state.search = ''; S.renderSearch(app); applyHighlight(); }
    });
    // delegated clicks for references, evidence, actions
    document.addEventListener('click', function (ev) {
      var t = ev.target.closest ? ev.target : null;
      if (!t || !app.model) return;
      var ref = t.closest('[data-ref]');
      var evb = t.closest('[data-ev]');
      var act = t.closest('[data-action]');
      var file = t.closest('[data-file]');
      if (act && act.tagName !== 'g' && act.dataset.target) {
        ev.preventDefault();
        var a = act.dataset.action, tg = act.dataset.target;
        if (a === 'show-exit') app.showExit(tg);
        else if (a === 'reveal') app.reveal(tg);
        else if (a === 'toggle-group') app.toggleGroup(tg);
        else if (a === 'toggle-op') app.toggleOp(tg);
        else if (a === 'only-op') app.onlyOperation(tg);
        else if (a === 'all-ops') { app.state.opFilter = null; app.refresh({ fit: true }); }
        else if (a === 'open-code') app.openFunction(tg);
        else if (a === 'open-def') { if (!AL.codeview.openDef(tg)) app.toast('The source of this function is not embedded.'); }
        else if (a === 'toggle-pin') { app.togglePin(tg); renderDetails(); }
        return;
      }
      if (evb) { ev.preventDefault(); app.showEvidence(evb.dataset.ev); return; }
      if (file && file.classList.contains('ref')) { AL.code.showFile(file.dataset.file); return; }
      if (ref && !t.closest('#graph')) {
        ev.preventDefault();
        var id = ref.dataset.ref;
        var M = app.model;
        if (!M.get(id)) { toast('"' + id + '" is not part of this result.'); return; }
        if (M.type(id) === 'unresolved') {
          app.state.selection = { id: id };
          app.state.focus = (M.get(id).subjects || []).slice();
          app.state.focusExit = null;
          applyHighlight(); renderDetails();
          return;
        }
        app.select(id, { reveal: true });
      }
    });
    U.$$('.side-section.collapsible h3').forEach(function (hd) {
      hd.addEventListener('click', function () { hd.parentElement.classList.toggle('folded'); });
    });
    ['sec-filters', 'sec-groups', 'sec-scope'].forEach(function (id) { document.getElementById(id).classList.add('folded'); });
    // drag & drop
    var overlay = document.getElementById('drop-overlay'), depth = 0;
    window.addEventListener('dragenter', function (ev) { if (hasFiles(ev)) { depth++; overlay.hidden = false; ev.preventDefault(); } });
    window.addEventListener('dragleave', function () { depth = Math.max(0, depth - 1); if (!depth) overlay.hidden = true; });
    window.addEventListener('dragover', function (ev) { if (hasFiles(ev)) ev.preventDefault(); });
    window.addEventListener('drop', function (ev) {
      depth = 0; overlay.hidden = true;
      if (!hasFiles(ev)) return;
      ev.preventDefault();
      var f = ev.dataTransfer.files[0];
      if (f) app.importFile(f);
    });
    window.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape' && !document.querySelector('.modal')) app.clearSelection();
      if (ev.key === '/' && document.activeElement && ['INPUT', 'TEXTAREA', 'SELECT'].indexOf(document.activeElement.tagName) < 0) { ev.preventDefault(); si.focus(); }
    });
    window.addEventListener('resize', U.debounce(function () { if (app.graph) AL.render.fit(); }, 200));
  }
  function hasFiles(ev) { return ev.dataTransfer && Array.prototype.indexOf.call(ev.dataTransfer.types || [], 'Files') >= 0; }

  // ---------------------------------------------------------------------------
  // boot
  // ---------------------------------------------------------------------------
  function boot() {
    app.schema = U.readJSONScript('fluxscope-schema');
    app.config = U.readJSONScript('fluxscope-config') || { api: null };
    var payload = U.readJSONScript('fluxscope-data') || { mode: 'app', cases: [] };
    app.mode = payload.mode || 'app';
    AL.render.init(document.getElementById('graph'), handlers);
    AL.code.init();
    AL.codeview.init();
    wire();
    S.renderAnalyze(app);
    var src = app.mode === 'report' ? 'report' : 'built-in';
    var problems = [];
    (payload.cases || []).forEach(function (d) {
      var res = AL.validate(d, app.schema);
      if (res.ok) app.addCase(d, src, res.warnings);
      else problems.push({ name: (d && d.meta && (d.meta.title || d.meta.id)) || 'embedded case', res: res });
    });
    if (app.mode === 'report') {
      var rb = document.getElementById('report-banner');
      rb.hidden = false;
      rb.innerHTML = '<b>Offline report</b> · exported ' + U.esc(payload.exported_at ? payload.exported_at.replace('T', ' ').slice(0, 19) + ' UTC' : '') +
        (payload.exported_from ? ' from “' + U.esc(payload.exported_from) + '”' : '') +
        ' · contains the analysis data and embedded evidence; no network access is needed.';
    }
    if (app.cases.length) {
      var idx = 0;
      if (payload.default) app.cases.forEach(function (c, i) { if (c.data.meta.id === payload.default) idx = i; });
      app.loadCase(idx);
    } else {
      renderEmpty();
      S.renderCases(app);
    }
    if (problems.length) S.showImportErrors(problems[0].name, problems[0].res.errors, problems[0].res.warnings);
    document.documentElement.setAttribute('data-fluxscope-ready', '1');
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
