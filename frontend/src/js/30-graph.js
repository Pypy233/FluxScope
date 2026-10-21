/* FluxScope viewer — compose the visible graph for the current view, filters
 * and expand/collapse state. Pure function of (Model, state); no DOM access. */
(function () {
  'use strict';
  var AL = window.FluxScope;
  var U = AL.util;

  // Edge kinds: graph-level categories. Relations keep their own kind; others are derived.
  var EDGE_KINDS = {
    flow: { label: 'Asset flow', color: '#2f6fed', marker: 'arrow', desc: 'Asset moves from payer → operation → recipient' },
    asset_flow: { label: 'Asset flow (relation)', color: '#2f6fed', marker: 'arrow', desc: 'Asset movement recorded as a relation' },
    holds: { label: 'Holds', color: '#475569', marker: 'dot', desc: 'Entity holds the position' },
    claim: { label: 'Claim on', color: '#7c3aed', marker: 'diamond', desc: 'Position represents a claim on another position' },
    depends_on: { label: 'Depends on', color: '#ea580c', marker: 'open', desc: 'Value or redemption depends on the target' },
    exit: { label: 'Exit via', color: '#be123c', marker: 'arrow', desc: 'Position is exited through the operation' },
    exit_step: { label: 'Exit step', color: '#e11d48', marker: 'arrow', desc: 'Next operation of an exit path' },
    returns: { label: 'Returns', color: '#0f9d58', marker: 'arrow', desc: 'Asset returned to the recipient when exiting' },
    debt: { label: 'Owes', color: '#b91c1c', marker: 'arrow', desc: 'Debt position owed to the counterparty' },
    collateral: { label: 'Secures', color: '#a16207', marker: 'diamond', desc: 'Position is collateral for a debt' },
    binding: { label: 'Binding', color: '#0f766e', marker: 'open', desc: 'Configuration binds a variable to a contract' },
    calls: { label: 'Calls', color: '#64748b', marker: 'open', desc: 'Cross-contract call' },
    identity: { label: 'Same as', color: '#0891b2', marker: 'none', desc: 'Two expressions denote the same object' },
    initiates: { label: 'Triggers', color: '#94a3b8', marker: 'open', desc: 'Entity calls / triggers the operation' },
    contains: { label: 'Sub-operation', color: '#b4bccb', marker: 'open', desc: 'Nested step of an operation' },
    denominated: { label: 'Denominated in', color: '#cbd5e1', marker: 'none', desc: 'Position is a balance of the asset' }
  };

  var VIEWS = {
    code: { label: 'Code', nodes: ['entity', 'operation'], defaultOff: [], edges: ['flow', 'asset_flow', 'initiates', 'contains'], keepIsolated: ['operation'] },
    flows: { label: 'Flows', nodes: ['entity', 'operation'], defaultOff: [], edges: ['flow', 'asset_flow', 'initiates', 'contains'], keepIsolated: ['operation'] },
    holdings: { label: 'Holdings', nodes: ['entity', 'position', 'asset'], defaultOff: ['asset'], edges: ['holds', 'claim', 'debt', 'collateral', 'denominated'], depth: ['claim', 'collateral'], keepIsolated: ['position'] },
    dependencies: { label: 'Dependencies', nodes: ['entity', 'position', 'asset', 'operation'], defaultOff: [], edges: ['claim', 'depends_on', 'collateral', 'debt', 'binding', 'calls', 'identity'], depth: ['claim', 'depends_on', 'collateral'], keepIsolated: [] },
    exits: { label: 'Exits', nodes: ['entity', 'position', 'operation'], defaultOff: [], edges: ['exit', 'exit_step', 'initiates', 'returns'], keepIsolated: [] },
    all: { label: 'All', nodes: ['entity', 'operation', 'position', 'asset'], defaultOff: [], edges: Object.keys(EDGE_KINDS).filter(function (k) { return k !== 'exit_step' && k !== 'returns'; }), keepIsolated: ['operation', 'position'] }
  };

  var BASIS_RANK = { observed: 3, config: 2, example: 1, static: 0 };

  /** Root operations shown in the Code view: the function being read plus the pinned ones. */
  function codeRoots(st) {
    var c = st.code || {};
    if (c.mode === 'map') return [];
    return U.uniq((c.pins || []).concat(c.op ? [c.op] : []));
  }

  function build(M, st) {
    var codeView = st.view === 'code';
    if (codeView && !codeRoots(st).length) return buildMap(M, st);
    var flowLike = codeView || st.view === 'flows' || st.view === 'all';
    var shownRoots = null;
    if (codeView) { shownRoots = Object.create(null); codeRoots(st).forEach(function (id) { shownRoots[id] = true; }); }
    var V = VIEWS[st.view] || VIEWS.all;
    var types = st.nodeTypes[st.view];
    var nodes = Object.create(null);
    var edges = Object.create(null);
    var edgeList = [];
    var rawOps = (st.view === 'exits' || st.view === 'dependencies');

    function edgeOK(kind) { return V.edges.indexOf(kind) >= 0 && !st.hiddenEdgeKinds[kind]; }
    function statusOK(s) { return !s || (!st.hiddenBasis[s.basis] && !st.hiddenVerification[s.verification]); }
    function typeOK(t) { return V.nodes.indexOf(t) >= 0 && types[t]; }

    function entRep(id) {
      var anc = M.entAncestors(id);
      for (var i = 0; i < anc.length; i++) if (st.collapsedGroups[anc[i]]) return anc[i];
      return id;
    }
    function opRep(id) {
      if (rawOps) return id;
      var chain = M.opAncestors(id).concat([id]);
      for (var i = 0; i < chain.length - 1; i++) if (!st.expandedOps[chain[i]]) return chain[i];
      return id;
    }

    function opShown(id) {
      if (shownRoots) {
        var a = M.opAncestors(id);
        return !!shownRoots[a.length ? a[0] : id];
      }
      // the root-operation filter belongs to the Flows / All views
      if (!st.opFilter || (st.view !== 'flows' && st.view !== 'all')) return true;
      var anc = M.opAncestors(id);
      return !!st.opFilter[anc.length ? anc[0] : id];
    }

    /** Map a raw object id to the id of the graph node that represents it (or null). */
    function rep(id) {
      var t = M.type(id);
      if (!t) return null;
      if (t === 'entity') id = entRep(id);
      else if (t === 'operation') { if (!opShown(id)) return null; id = opRep(id); }
      else if (t !== 'position' && t !== 'asset') return null;
      if (!typeOK(t)) return null;
      var x = M.get(id);
      if (t !== 'entity' && x && !statusOK(x.status)) return null;
      return id;
    }

    function ensureNode(id) {
      if (nodes[id]) return nodes[id];
      var t = M.type(id), x = M.get(id);
      var n = { id: id, type: t, obj: x, handles: [] };
      if (t === 'entity' && M.entChildren[id] && st.collapsedGroups[id]) {
        n.group = true;
        n.memberCount = countEntityDescendants(M, id);
      }
      if (t === 'entity' && x.parent) {
        var anc = M.entAncestors(id);
        n.groupTag = anc[anc.length - 1];
      }
      describeNode(M, n);
      nodes[id] = n;
      return n;
    }

    function addEdge(kind, src, tgt, ref, opt) {
      if (!edgeOK(kind)) return null;
      var s = rep(src), t = rep(tgt);
      if (!s || !t || s === t) return null;
      opt = opt || {};
      var key = kind + '|' + s + '|' + t + '|' + (opt.asset || '') + '|' + (opt.sub || '');
      var e = edges[key];
      if (!e) {
        e = edges[key] = { id: 'e' + edgeList.length, key: key, kind: kind, source: s, target: t, asset: opt.asset || null, sub: opt.sub || null, refs: [], labels: [], status: null };
        edgeList.push(e);
        ensureNode(s); ensureNode(t);
      }
      e.refs.push(ref);
      if (opt.label) e.labels.push(opt.label);
      if (opt.status) {
        if (!e.status || (BASIS_RANK[opt.status.basis] || 0) > (BASIS_RANK[e.status.basis] || 0)) e.status = opt.status;
      }
      return e;
    }

    // ---- operations visible as nodes ------------------------------------------
    var noFlowOps = 0;
    if (typeOK('operation') && flowLike) {
      M.c.operation.forEach(function (o) {
        if (opRep(o.id) !== o.id || !statusOK(o.status) || !opShown(o.id)) return;
        // the Flows view is about asset movements: operations without any flow in their subtree are left out
        // (the Code view keeps the opened function itself; its calls that move nothing are marked in the source)
        if ((st.view === 'flows' || (codeView && o.parent)) && !M.flowsUnder(o.id).length) { noFlowOps++; return; }
        ensureNode(o.id);
      });
    }
    // ---- positions as nodes ----------------------------------------------------
    if (typeOK('position') && (st.view === 'holdings' || st.view === 'all')) {
      M.c.position.forEach(function (p) { if (statusOK(p.status)) ensureNode(p.id); });
    }

    // ---- flows ------------------------------------------------------------------
    var mergedAssets = false;
    if (edgeOK('flow')) {
      var visibleFlows = M.flows.filter(function (fr) {
        // Code view: a collapsed call stands for itself only; its inner flows appear when it is expanded
        if (codeView && opRep(fr.op.id) !== fr.op.id) return false;
        return statusOK(fr.flow.status || fr.op.status) && opShown(fr.op.id);
      });
      // Dense graphs: draw one edge per (payer, operation) / (operation, recipient) pair and list the
      // assets on it instead of one edge per asset. Amounts are never combined; details list every flow.
      var keys = Object.create(null), nkeys = 0;
      visibleFlows.forEach(function (fr) {
        var f = fr.flow, opN = opRep(fr.op.id);
        if (f.from) { var k1 = rep(f.from) + '>' + opN + '|' + f.asset; if (!keys[k1]) { keys[k1] = 1; nkeys++; } }
        if (f.to) { var k2 = opN + '>' + rep(f.to) + '|' + f.asset; if (!keys[k2]) { keys[k2] = 1; nkeys++; } }
      });
      mergedAssets = st.mergeAssets === 'on' || (st.mergeAssets !== 'off' && nkeys > 60);
      visibleFlows.forEach(function (fr) {
        var f = fr.flow, s = f.status || fr.op.status;
        var opN = opRep(fr.op.id);
        var lbl = mergedAssets ? M.assetShort(f.asset) : M.amountText(f.amount);
        var ref = { type: 'flow', id: f.id, op: fr.op.id };
        var asset = mergedAssets ? null : f.asset;
        if (f.from) addEdge('flow', f.from, opN, ref, { asset: asset, sub: f.to ? 'in' : 'burn', label: (f.to ? '' : 'burn ') + lbl, status: s });
        if (f.to) addEdge('flow', opN, f.to, ref, { asset: asset, sub: f.from ? 'out' : 'mint', label: (f.from ? '' : 'mint ') + lbl, status: s });
      });
    }

    // ---- relations ----------------------------------------------------------------
    M.c.relation.forEach(function (r) {
      if (!statusOK(r.status)) return;
      var lbl = r.label || (r.amount ? M.amountText(r.amount) : '');
      if (r.kind === 'asset_flow' && r.amount && r.label) lbl = r.label + ' · ' + M.amountText(r.amount);
      addEdge(r.kind, r.source, r.target, { type: 'relation', id: r.id }, { asset: r.asset, label: lbl, status: r.status });
    });

    // ---- derived holds / denominated --------------------------------------------
    if (edgeOK('holds')) {
      M.c.position.forEach(function (p) {
        if (!statusOK(p.status)) return;
        var explicit = (M.relTo[p.id] || []).some(function (r) { return r.kind === 'holds' && r.source === p.holder; });
        if (!explicit) addEdge('holds', p.holder, p.id, { type: 'holder', id: p.id }, { status: p.status });
      });
    }
    if (edgeOK('denominated') && typeOK('asset')) {
      M.c.position.forEach(function (p) {
        if (statusOK(p.status)) addEdge('denominated', p.id, p.asset, { type: 'denominated', id: p.id }, { status: p.status });
      });
    }

    // ---- exits --------------------------------------------------------------------
    if (st.view === 'exits') {
      M.c.exit.forEach(function (x) {
        if (!statusOK(x.status)) return;
        var steps = (x.steps || []).slice().sort(function (a, b) { return a.order - b.order; });
        if (!steps.length) { ensureNodeIf(x.position); return; }
        var hasRel = (M.relFrom[x.position] || []).some(function (r) { return r.kind === 'exit' && r.target === steps[0].operation; });
        if (!hasRel) addEdge('exit', x.position, steps[0].operation, { type: 'exit', id: x.id }, { label: x.label, status: x.status });
        for (var i = 0; i + 1 < steps.length; i++) {
          var nxt = steps[i + 1];
          var lbl = nxt.optional ? 'if ' + ((nxt.conditions && nxt.conditions[0] && (nxt.conditions[0].expression || nxt.conditions[0].text)) || 'condition holds') : 'then';
          if (nxt.transaction === 'separate') lbl += ' (separate tx)';
          addEdge('exit_step', steps[i].operation, nxt.operation, { type: 'exit', id: x.id, step: nxt.order }, { label: lbl, status: x.status });
        }
        steps.forEach(function (s) {
          if (s.actor) addEdge('initiates', s.actor, s.operation, { type: 'exit', id: x.id, step: s.order }, { label: s.transaction === 'separate' ? 'calls (separate tx)' : 'calls', status: x.status });
        });
        var last = steps[steps.length - 1].operation;
        (x.returns || []).forEach(function (f) {
          if (f.to) addEdge('returns', last, f.to, { type: 'exit-return', id: x.id, flow: f.id }, { asset: f.asset, label: M.amountText(f.amount), status: x.status });
        });
      });
    }

    function ensureNodeIf(id) { var r = rep(id); if (r) ensureNode(r); }

    // ---- initiators / sub-operations (flows, all, code) ---------------------------
    if (flowLike) {
      Object.keys(nodes).forEach(function (id) {
        var n = nodes[id];
        if (n.type !== 'operation') return;
        var o = n.obj;
        if (o.initiator) {
          var ini = rep(o.initiator);
          var hasFlow = ini && edgeList.some(function (e) { return e.kind === 'flow' && e.source === ini && e.target === id; });
          if (!hasFlow) addEdge('initiates', o.initiator, id, { type: 'initiator', id: id },
            { label: o.transaction === 'separate' ? 'triggers (separate tx)' : 'triggers', status: o.status });
        }
        if (st.expandedOps[id]) {
          (M.opChildren[id] || []).forEach(function (c) {
            if (!statusOK(c.status) || (codeView && !M.flowsUnder(c.id).length)) return;
            addEdge('contains', id, c.id, { type: 'contains', id: c.id },
              { label: (c.order !== undefined && c.order !== null ? c.order + ' · ' : '') + (c.transaction === 'separate' ? 'separate tx' : 'same tx'), status: c.status });
          });
        }
      });
    }

    // ---- dependency depth -----------------------------------------------------------
    var depInfo = null;
    if (V.depth) depInfo = applyDepth(V, st, nodes, edgeList);

    // ---- remove isolated nodes ------------------------------------------------------
    var deg = Object.create(null);
    edgeList.forEach(function (e) { if (!e.hidden) { deg[e.source] = (deg[e.source] || 0) + 1; deg[e.target] = (deg[e.target] || 0) + 1; } });
    Object.keys(nodes).forEach(function (id) {
      var n = nodes[id];
      if (n.hidden) return;
      if (!deg[id] && V.keepIsolated.indexOf(n.type) < 0) n.hidden = true;
    });

    // ---- handles ----------------------------------------------------------------------
    Object.keys(nodes).forEach(function (id) {
      var n = nodes[id];
      if (n.hidden) return;
      if (codeView && n.type === 'operation' && M.opChildren[id] && !st.expandedOps[id]) {
        var inner = M.flowsUnder(id).length - (n.obj.inputs || []).length - (n.obj.outputs || []).length;
        if (inner > 0) n.sub = n.sub + ' · ' + U.plural(inner, 'flow') + ' inside';
      }
      if (n.group) n.handles.push({ action: 'expand-group', target: id, text: '+' + n.memberCount, title: 'Expand group (' + n.memberCount + ' members)' });
      if (n.type === 'operation' && !rawOps && M.opChildren[id]) {
        if (st.expandedOps[id]) n.handles.push({ action: 'collapse-op', target: id, text: '−', title: 'Collapse sub-operations' });
        else n.handles.push({ action: 'expand-op', target: id, text: '+' + M.opChildren[id].length, title: 'Show ' + U.plural(M.opChildren[id].length, 'sub-operation') + (codeView ? ' and the assets they move' : '') });
      }
      if (depInfo && depInfo.children[id]) {
        var hiddenKids = depInfo.children[id].filter(function (c) { return !depInfo.visible[c]; });
        if (hiddenKids.length) n.handles.push({ action: 'expand-dep', target: id, text: '+' + hiddenKids.length, title: 'Show ' + U.plural(hiddenKids.length, 'dependency', 'dependencies') + ' of the next level' });
        else n.handles.push({ action: 'collapse-dep', target: id, text: '−', title: 'Hide dependencies below this node' });
      }
    });

    var outNodes = [], outEdges = [];
    Object.keys(nodes).forEach(function (id) { if (!nodes[id].hidden) outNodes.push(nodes[id]); });
    edgeList.forEach(function (e) {
      if (e.hidden || nodes[e.source].hidden || nodes[e.target].hidden) return;
      e.label = edgeLabel(M, e);
      outEdges.push(e);
    });
    // Stable order: by type then input order, helps layout determinism.
    var order = { entity: 0, operation: 1, position: 2, asset: 3 };
    var idx = Object.create(null);
    ['entity', 'operation', 'position', 'asset'].forEach(function (t) { M.c[t].forEach(function (x, i) { idx[x.id] = i; }); });
    outNodes.sort(function (a, b) { return (order[a.type] - order[b.type]) || ((idx[a.id] || 0) - (idx[b.id] || 0)); });
    return { nodes: outNodes, edges: outEdges, byId: nodes, depInfo: depInfo, view: st.view, mergedAssets: mergedAssets, noFlowOps: noFlowOps, roots: codeView ? codeRoots(st) : null };
  }

  // ---- system map -------------------------------------------------------------------
  var PARTY = { contract: true, external_contract: true, protocol: true };
  var USERS = 'map:users';

  /** Compact overview for the Code view: contracts as nodes, every account and role as one node, and one
   *  edge per pair of parties listing the assets any operation moves between them (arrows show the
   *  directions). Amounts are not combined. A contract that moves assets only with users also shows the
   *  external contracts its value depends on, so that boundaries stay visible. */
  function buildMap(M, st) {
    function party(id) {
      var e = M.get(id);
      if (!e) return null;
      return PARTY[e.kind] ? id : USERS;
    }
    function statusOK(s) { return !s || (!st.hiddenBasis[s.basis] && !st.hiddenVerification[s.verification]); }
    var nodes = Object.create(null), edges = Object.create(null), edgeList = [], members = Object.create(null);
    function node(id) {
      if (nodes[id]) return nodes[id];
      var n;
      if (id === USERS) n = { id: id, type: 'entity', synthetic: true, handles: [], obj: { id: id, kind: 'account', label: 'Users & roles' } };
      else n = { id: id, type: 'entity', obj: M.get(id), handles: [] };
      describeNode(M, n);
      return (nodes[id] = n);
    }
    // layout direction: users first, then callers before the contracts they call (from nested operations)
    var calls = Object.create(null);
    M.c.operation.forEach(function (o) {
      var p = o.parent && M.get(o.parent);
      if (!p) return;
      var a = party(p.executor), b = party(o.executor);
      if (a && b && a !== b) calls[a + '|' + b] = (calls[a + '|' + b] || 0) + 1;
    });
    function forward(a, b) {
      if (a === USERS || b === USERS) return a === USERS;
      var ab = calls[a + '|' + b] || 0, ba = calls[b + '|' + a] || 0;
      return ab >= ba;
    }
    function edge(kind, a, b, key) {
      var e = edges[key];
      if (!e) {
        e = edges[key] = { id: 'e' + edgeList.length, key: key, kind: kind, source: a, target: b, asset: null, sub: null, refs: [], labels: [], status: null, map: true, assets: [] };
        edgeList.push(e);
        node(a); node(b);
      }
      return e;
    }
    M.flows.forEach(function (fr) {
      var f = fr.flow, o = fr.op;
      if (!statusOK(f.status || o.status)) return;
      var a = f.from ? party(f.from) : party(o.executor), b = f.to ? party(f.to) : party(o.executor);
      [f.from, f.to].forEach(function (x) { if (x && party(x) === USERS) members[x] = true; });
      if (!a || !b || a === b) return;
      var fwd = forward(a, b);
      var e = edge('flow', fwd ? a : b, fwd ? b : a, 'flow|' + (a < b ? a + '|' + b : b + '|' + a));
      e.dirs = e.dirs || { fwd: false, back: false };
      e.dirs[e.source === a ? 'fwd' : 'back'] = true;
      e.refs.push({ type: 'flow', id: f.id, op: o.id });
      if (e.assets.indexOf(f.asset) < 0) e.assets.push(f.asset);
      var s = f.status || o.status;
      if (s && (!e.status || (BASIS_RANK[s.basis] || 0) > (BASIS_RANK[e.status.basis] || 0))) e.status = s;
    });
    // contracts in scope stay on the map even when no flow touches them
    M.c.entity.forEach(function (e) { if (e.kind === 'contract' && e.in_scope !== false) node(e.id); });
    // a contract without asset flows to other contracts: show what its value depends on
    var linked = Object.create(null);
    edgeList.forEach(function (e) { if (e.source !== USERS && e.target !== USERS) linked[e.source] = linked[e.target] = true; });
    M.c.relation.forEach(function (r) {
      if (r.kind !== 'depends_on' || !statusOK(r.status)) return;
      var s = M.get(r.source), t = M.get(r.target);
      if (!s || !t || s.kind !== 'contract' || linked[r.source] || (t.kind !== 'external_contract' && t.kind !== 'protocol')) return;
      var e = edge('depends_on', r.source, r.target, 'dep|' + r.source + '|' + r.target);
      e.refs.push({ type: 'relation', id: r.id });
      e.labels.push(r.label || 'depends on');
      e.status = r.status;
    });
    // labels: tokens and shares first, accounting ledgers after them
    var KIND_RANK = { token: 0, native: 0, share: 1, lp_token: 1, debt_token: 2, accounting: 3 };
    edgeList.forEach(function (e) {
      if (e.kind !== 'flow') { e.label = edgeLabel(M, e); return; }
      e.assets.sort(function (x, y) { return (KIND_RANK[(M.get(x) || {}).kind] || 0) - (KIND_RANK[(M.get(y) || {}).kind] || 0); });
      e.labels = e.assets.map(function (id) {
        var a = M.get(id);
        return a && a.kind === 'accounting' ? String(a.label).replace(/\s*\(.*\)\s*$/, '') : M.assetShort(id);
      });
      e.sub = 'out';
      e.label = edgeLabel(M, e);
    });
    if (nodes[USERS]) {
      var ids = Object.keys(members);
      nodes[USERS].members = ids;
      nodes[USERS].sub = U.plural(ids.length, 'account or role', 'accounts and roles');
    }
    var counts = Object.create(null);
    M.c.operation.forEach(function (o) { if (!o.parent && o.executor) counts[o.executor] = (counts[o.executor] || 0) + 1; });
    var outNodes = Object.keys(nodes).map(function (id) {
      var n = nodes[id];
      if (counts[id]) n.sub = n.sub + ' · ' + U.plural(counts[id], 'function');
      return n;
    });
    var rank = function (n) { return n.id === USERS ? 0 : n.obj.kind === 'contract' ? 1 : 2; };
    var idx = Object.create(null);
    M.c.entity.forEach(function (x, i) { idx[x.id] = i; });
    outNodes.sort(function (a, b) { return rank(a) - rank(b) || ((idx[a.id] || 0) - (idx[b.id] || 0)); });
    return { nodes: outNodes, edges: edgeList, byId: nodes, depInfo: null, view: st.view, map: true, mergedAssets: true, noFlowOps: 0, roots: [] };
  }

  function countEntityDescendants(M, id) {
    var n = 0;
    (function walk(x) { (M.entChildren[x] || []).forEach(function (c) { n++; walk(c.id); }); })(id);
    return n;
  }

  function applyDepth(V, st, nodes, edgeList) {
    var kinds = V.depth;
    var children = Object.create(null), indeg = Object.create(null), incident = Object.create(null);
    edgeList.forEach(function (e) {
      if (kinds.indexOf(e.kind) < 0) return;
      (children[e.source] || (children[e.source] = [])).push(e.target);
      indeg[e.target] = (indeg[e.target] || 0) + 1;
      incident[e.source] = incident[e.target] = true;
    });
    Object.keys(children).forEach(function (k) { children[k] = U.uniq(children[k]); });
    var depth = Object.create(null), visible = Object.create(null), parent = Object.create(null);
    var ids = Object.keys(nodes);
    var queue = [];
    ids.forEach(function (id) { if (incident[id] && !indeg[id]) { depth[id] = 0; queue.push(id); } });
    // BFS for depth (for nodes in cycles without roots, seed later)
    function bfs(q) {
      for (var i = 0; i < q.length; i++) {
        var u = q[i];
        (children[u] || []).forEach(function (c) {
          if (depth[c] === undefined) { depth[c] = depth[u] + 1; parent[c] = u; q.push(c); }
        });
      }
    }
    bfs(queue);
    ids.forEach(function (id) { if (incident[id] && depth[id] === undefined) { depth[id] = 0; bfs([id]); } });
    // visibility
    var vq = [];
    ids.forEach(function (id) {
      if (!incident[id] || depth[id] === 0) { visible[id] = true; if (incident[id]) vq.push(id); }
    });
    var lim = st.depLimit;
    for (var i = 0; i < vq.length; i++) {
      var u = vq[i];
      var allow = st.depExpanded[u] || (!st.depCollapsed[u] && (depth[u] + 1) <= lim);
      if (!allow) continue;
      (children[u] || []).forEach(function (c) { if (!visible[c]) { visible[c] = true; vq.push(c); } });
    }
    ids.forEach(function (id) { if (!visible[id]) nodes[id].hidden = true; });
    edgeList.forEach(function (e) { if (!visible[e.source] || !visible[e.target]) e.hidden = true; });
    return { depth: depth, visible: visible, children: children, parent: parent };
  }

  function describeNode(M, n) {
    var x = n.obj;
    if (n.type === 'entity') {
      n.label = x.label;
      if (n.group) n.sub = 'group · ' + U.plural(n.memberCount, 'member');
      else {
        var bits = [U.humanize(x.kind)];
        if (x.contract && x.contract.name && x.contract.name !== x.label) bits.push(x.contract.name);
        if (x.in_scope === false) bits.push('out of scope');
        n.sub = bits.join(' · ');
      }
      n.tag = x.in_scope === false ? 'OUT OF SCOPE' : null;
    } else if (n.type === 'operation') {
      n.label = x.label;
      var sub = [U.humanize(x.kind)];
      if (x.initiator) sub.push('by ' + M.label(x.initiator));
      n.sub = sub.join(' · ');
      n.tag = x.kind.toUpperCase().replace(/_/g, ' ');
    } else if (n.type === 'position') {
      n.label = M.label(x.asset);
      n.sub = U.humanize(x.kind) + ' · ' + M.label(x.holder);
      n.tag = x.kind.toUpperCase();
    } else if (n.type === 'asset') {
      n.label = x.label;
      n.sub = U.humanize(x.kind) + (x.standard ? ' · ' + x.standard : '');
      n.tag = x.kind === 'share' || x.kind === 'lp_token' ? 'SHARE' : null;
    }
    n.status = x && x.status ? x.status : null;
  }

  var QUIET = { holds: true, denominated: true };

  function edgeLabel(M, e) {
    if (QUIET[e.kind]) return '';
    var uniq = U.uniq(e.labels.filter(function (l) { return l && !(e.kind === 'initiates' && (l === 'triggers' || l === 'calls')); }));
    if (!uniq.length) return '';
    if (e.kind === 'flow' && !e.asset && uniq.length > 1) {
      var shown = uniq.slice(0, 3).join(' · ');
      return uniq.length > 3 ? shown + ' (+' + (uniq.length - 3) + ')' : shown;
    }
    var first = uniq[0];
    return uniq.length > 1 ? first + '  (+' + (uniq.length - 1) + ')' : first;
  }

  /** Which view best shows an object of the given type. */
  function preferredView(M, id, current) {
    var t = M.type(id);
    var x = M.get(id);
    if (t === 'operation') return current === 'all' || current === 'flows' || current === 'code' ? current : 'flows';
    if (t === 'position') return ['holdings', 'all', 'dependencies', 'exits'].indexOf(current) >= 0 ? current : 'holdings';
    if (t === 'asset') return ['dependencies', 'all', 'holdings'].indexOf(current) >= 0 ? current : 'all';
    if (t === 'exit') return 'exits';
    if (t === 'relation') {
      var k = x.kind;
      if (k === 'claim' || k === 'holds' || k === 'debt' || k === 'collateral') return current === 'dependencies' && k !== 'holds' ? current : 'holdings';
      if (k === 'depends_on' || k === 'binding' || k === 'calls' || k === 'identity') return 'dependencies';
      if (k === 'exit') return 'exits';
      return 'all';
    }
    return current;
  }

  AL.graph = { EDGE_KINDS: EDGE_KINDS, VIEWS: VIEWS, build: build, preferredView: preferredView, codeRoots: codeRoots, MAP_USERS: USERS };
})();
