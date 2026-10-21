/* FluxScope viewer — Code view: the source on the left, the graph of the code being read on the right.
 *
 * The outline lists the entry points of every analysed contract. Opening one shows its source and,
 * in the graph, only that function (with its calls into other contracts) plus any pinned functions.
 * Lines that move assets or guard an operation are marked in the gutter; the lines of the open
 * function are annotated with what the analysis read from them. Hovering a line emphasises the graph
 * items it supports; hovering a graph item highlights its lines. */
(function () {
  'use strict';
  var AL = window.FluxScope;
  var U = AL.util;
  var h = U.h;

  var CV = AL.codeview = {};
  var STMT_SPAN = 6;     // source ranges up to this many lines are statements; longer ones are definitions
  var pane, outlineBox, filterInput, outlineList, pathEl, filesEl, hintEl, bodyEl;
  var X = null;          // index over the current model
  var file = null;       // path of the file shown
  var rows = [];         // table rows of the file shown, by line - 1
  var hovered = [];      // rows highlighted from the graph
  var selected = [];     // rows of the selected graph item

  // ---------------------------------------------------------------------------
  // index: source lines <-> operations, flows, conditions, calls
  // ---------------------------------------------------------------------------
  function buildIndex(M) {
    var I = { lines: Object.create(null), def: Object.create(null), calls: Object.create(null), rootOf: Object.create(null), hasFlow: Object.create(null), groups: [] };
    function loc(evId) {
      var e = M.get(evId);
      if (!e || !e.source || !M.sources[e.source.file]) return null;
      return { file: e.source.file, start: e.source.start_line, end: e.source.end_line || e.source.start_line };
    }
    function inside(l, d) { return d && l.file === d.file && l.start >= d.start && l.end <= d.end; }
    function add(l, item) {
      var f = I.lines[l.file] || (I.lines[l.file] = Object.create(null));
      for (var n = l.start; n <= l.end; n++) (f[n] || (f[n] = [])).push(Object.assign({ first: n === l.start }, item));
    }
    var text = Object.create(null);
    function lineText(f, n) { return ((text[f] || (text[f] = M.sources[f].content.split('\n')))[n - 1]) || ''; }
    // parents before children so that call sites can be found inside the caller's definition
    var ops = M.c.operation.slice().sort(function (a, b) { return M.opAncestors(a.id).length - M.opAncestors(b.id).length; });
    ops.forEach(function (o) {
      var anc = M.opAncestors(o.id);
      I.rootOf[o.id] = anc.length ? anc[0] : o.id;
      var pdef = o.parent ? I.def[o.parent] : null;
      var best = null, sites = [];
      (o.evidence || []).forEach(function (ev) {
        var l = loc(ev);
        if (!l) return;
        if (pdef && inside(l, pdef)) sites.push(l);
        else if (!best || l.end - l.start > best.end - best.start) best = l;
      });
      if (best) I.def[o.id] = best;
      // evidence inside the caller is the call site, possibly with the checks that resolved the callee;
      // keep the lines that name the called function when there are any
      var fn = o.entrypoint && o.entrypoint.function;
      var named = fn ? sites.filter(function (l) {
        for (var n = l.start; n <= l.end; n++) if (lineText(l.file, n).indexOf(fn + '(') >= 0 || lineText(l.file, n).indexOf(fn + '{') >= 0) return true;
        return false;
      }) : [];
      (named.length ? named : sites).forEach(function (l) {
        (I.calls[o.id] || (I.calls[o.id] = [])).push(l);
        add(l, { k: 'call', id: o.id, op: o.parent });
      });
      (o.inputs || []).concat(o.outputs || []).forEach(function (f) {
        (f.evidence || []).forEach(function (ev) { var l = loc(ev); if (l && l.end - l.start < STMT_SPAN) add(l, { k: 'flow', id: f.id, op: o.id }); });
      });
      (o.conditions || []).forEach(function (c) {
        (c.evidence || []).forEach(function (ev) { var l = loc(ev); if (l && l.end - l.start < STMT_SPAN) add(l, { k: 'cond', id: c.id, op: o.id, cond: c }); });
      });
    });
    M.c.operation.forEach(function (o) { if (!o.parent) I.hasFlow[o.id] = M.flowsUnder(o.id).length > 0; });
    // outline: entry points grouped by the contract that executes them
    var byExec = Object.create(null), order = [];
    M.c.operation.forEach(function (o) {
      if (o.parent) return;
      var g = o.executor || '';
      if (!byExec[g]) { byExec[g] = []; order.push(g); }
      byExec[g].push(o);
    });
    I.groups = order.map(function (g) {
      var e = M.get(g), main = e && e.contract && e.contract.file && M.sources[e.contract.file] ? e.contract.file : null;
      if (!main && e) (e.evidence || []).some(function (ev) { var l = loc(ev); if (l) main = l.file; return !!l; });
      var list = byExec[g].slice().sort(function (a, b) {
        var da = I.def[a.id], db = I.def[b.id];
        var ma = da && da.file === main ? 0 : 1, mb = db && db.file === main ? 0 : 1;
        if (ma !== mb) return ma - mb;
        if (!da || !db) return (da ? 0 : 1) - (db ? 0 : 1);
        return da.file < db.file ? -1 : da.file > db.file ? 1 : da.start - db.start;
      });
      return { id: g, label: e ? e.label : 'Other', file: main, ops: list };
    });
    return I;
  }

  CV.rootOf = function (id) { return X ? X.rootOf[id] || id : id; };
  CV.defOf = function (id) { return X ? X.def[id] || null : null; };

  // ---------------------------------------------------------------------------
  // skeleton
  // ---------------------------------------------------------------------------
  CV.init = function () {
    pane = document.getElementById('code-pane');
    outlineBox = h('div', { id: 'code-outline' });
    filterInput = h('input', { type: 'search', placeholder: 'Filter functions…', spellcheck: 'false', 'data-testid': 'code-filter' });
    outlineBox.appendChild(h('div', { class: 'co-filter' }, filterInput));
    outlineList = h('div', { class: 'co-list', 'data-testid': 'code-outline' });
    outlineBox.appendChild(outlineList);
    pathEl = h('div', { class: 'ch-path', 'data-testid': 'code-path' });
    filesEl = h('select', { id: 'code-files', title: 'Open an embedded source file' });
    var head = h('div', { id: 'code-head' }, [
      h('button', { class: 'btn btn-small', id: 'btn-outline', title: 'Hide or show the function list', text: '☰' }),
      h('button', { class: 'btn btn-small', id: 'btn-code-back', title: 'Back to the previous location', text: '←', disabled: true }),
      pathEl, filesEl
    ]);
    hintEl = h('div', { id: 'code-hint', hidden: true });
    bodyEl = h('div', { id: 'code-body', 'data-testid': 'code-body' });
    pane.appendChild(outlineBox);
    pane.appendChild(h('div', { id: 'code-main' }, [head, hintEl, bodyEl]));

    filterInput.addEventListener('input', U.debounce(renderOutline, 80));
    document.getElementById('btn-outline').addEventListener('click', function () { outlineBox.classList.toggle('folded'); });
    document.getElementById('btn-code-back').addEventListener('click', goBack);
    filesEl.addEventListener('change', function () { if (filesEl.value) CV.showFile(filesEl.value, { push: true }); });
    outlineList.addEventListener('click', function (ev) {
      var inst = ev.target.closest('.co-inst');
      if (inst) { AL.app.select(inst.dataset.entity); CV.openContract(inst.dataset.entity, true); return; }
      var item = ev.target.closest('.co-item');
      if (!item) return;
      if (ev.target.closest('.co-pin')) AL.app.togglePin(item.dataset.op);
      else AL.app.openFunction(item.dataset.op);
    });
    bodyEl.addEventListener('mouseover', onLineHover);
    bodyEl.addEventListener('mouseleave', function () { AL.render.flash([], []); });
    bodyEl.addEventListener('click', onLineClick);
    hintEl.addEventListener('click', function (ev) {
      var a = ev.target.closest('[data-goto]');
      if (!a) return;
      var p = a.dataset.goto.split('#');
      CV.showFile(p[0], { push: true, line: +p[1] });
      markRows(selected, false);
      selected = rowsFor([{ file: p[0], start: +p[1], end: +(p[2] || p[1]) }]);
      markRows(selected, true, 'xsel');
    });
    wireSplitters();
  };

  function wireSplitters() {
    var work = document.getElementById('work-split'), det = document.getElementById('details-split');
    var drag = null;
    work.addEventListener('mousedown', function (ev) { drag = { kind: 'w', x: ev.clientX, w: pane.getBoundingClientRect().width, total: pane.parentElement.getBoundingClientRect().width }; ev.preventDefault(); });
    det.addEventListener('mousedown', function (ev) {
      var slot = document.getElementById('details-slot');
      drag = { kind: 'd', y: ev.clientY, h: slot.getBoundingClientRect().height, total: slot.parentElement.getBoundingClientRect().height };
      ev.preventDefault();
    });
    window.addEventListener('mousemove', function (ev) {
      if (!drag) return;
      if (drag.kind === 'w') {
        var w = Math.max(320, Math.min(drag.total - 320, drag.w + ev.clientX - drag.x));
        document.documentElement.style.setProperty('--code-w', (100 * w / drag.total).toFixed(1) + '%');
      } else {
        var hh = Math.max(80, Math.min(drag.total - 140, drag.h - (ev.clientY - drag.y)));
        document.documentElement.style.setProperty('--details-h', hh + 'px');
      }
    });
    window.addEventListener('mouseup', function () { if (drag) { drag = null; AL.render.fit(); } });
  }

  // ---------------------------------------------------------------------------
  // model / outline
  // ---------------------------------------------------------------------------
  CV.reset = function (M) {
    X = buildIndex(M);
    file = null;
    rows = [];
    hovered = []; selected = [];
    CV.history = [];
    filterInput.value = '';
    U.clear(filesEl);
    var files = Object.keys(M.sources).sort();
    filesEl.appendChild(h('option', { value: '', text: files.length ? 'Files (' + files.length + ')' : 'No source files' }));
    files.forEach(function (f) { filesEl.appendChild(h('option', { value: f, text: f })); });
    filesEl.disabled = !files.length;
    pathEl.textContent = '';
    hintEl.hidden = true;
    U.clear(bodyEl).appendChild(h('div', { class: 'code-empty' }, files.length
      ? 'Pick a function on the left. Its source opens here and the graph on the right shows only what that function does: who calls it, which assets move between whom, and the contracts it calls.'
      : 'This result embeds no source files. Functions can still be opened from the list; the graph shows what they do.'));
    renderOutline();
  };

  CV.hasCode = function (M) {
    return M.c.operation.some(function (o) {
      return !o.parent && (o.evidence || []).some(function (ev) { var e = M.get(ev); return e && e.source && M.sources[e.source.file]; });
    });
  };

  function renderOutline() {
    if (!X) return;
    var M = AL.app.model, st = AL.app.state, q = filterInput.value.trim().toLowerCase();
    var cur = st.code.op, pins = st.code.pins;
    U.clear(outlineList);
    X.groups.forEach(function (g) {
      var list = g.ops.filter(function (o) {
        return !q || (o.label + ' ' + o.kind + ' ' + (o.entrypoint ? o.entrypoint.signature : '')).toLowerCase().indexOf(q) >= 0;
      });
      if (!list.length) return;
      outlineList.appendChild(h('div', { class: 'co-inst', 'data-entity': g.id, 'data-file': g.file || null, title: g.file ? 'Open ' + g.file : g.label }, [
        g.label, g.file ? h('div', { class: 'co-file', text: g.file.split('/').pop() }) : null
      ]));
      var inherited = false;
      list.forEach(function (o) {
        var d = X.def[o.id];
        if (!inherited && g.file && (!d || d.file !== g.file)) {
          inherited = true;
          outlineList.appendChild(h('div', { class: 'co-sub', text: 'inherited' }));
        }
        var quiet = !X.hasFlow[o.id], pinned = pins.indexOf(o.id) >= 0;
        var name = o.entrypoint && o.entrypoint.function ? o.entrypoint.function : o.label;
        outlineList.appendChild(h('div', {
          class: 'co-item' + (o.id === cur && st.code.mode !== 'map' ? ' active' : '') + (pinned ? ' pinned' : '') + (quiet ? ' quiet' : ''),
          'data-op': o.id,
          title: o.label + ' — ' + U.humanize(o.kind) + (o.initiator ? ', called by ' + M.label(o.initiator) : '') + (quiet ? ' (moves no assets)' : '') + (d ? '\n' + d.file + ':' + d.start : '')
        }, [
          h('span', { class: 'co-glyph', text: AL.render.styles.OP_GLYPH[o.kind] || '•' }),
          h('span', { class: 'co-name', text: name }),
          h('span', { class: 'co-kind', text: U.humanize(o.kind) }),
          h('button', { class: 'co-pin', title: pinned ? 'Remove from the graph' : 'Pin: keep this function in the graph while you open others', text: pinned ? 'pinned' : 'pin' })
        ]));
      });
    });
    var active = outlineList.querySelector('.co-item.active');
    if (active) {
      var r = active.getBoundingClientRect(), b = outlineList.getBoundingClientRect();
      if (r.top < b.top || r.bottom > b.bottom) outlineList.scrollTop += r.top - b.top - b.height / 3;
    }
  }

  // ---------------------------------------------------------------------------
  // source
  // ---------------------------------------------------------------------------
  /** Show a file. opts: {line, push (remember the previous location), top (scroll to line 1)} */
  CV.showFile = function (path, opts) {
    opts = opts || {};
    var M = AL.app.model, src = M && M.sources[path];
    if (!src) return false;
    if (path !== file) {
      if (opts.push && file) remember();
      file = path;
      var lines = AL.code.highlight(src.content, src.language === 'solidity' ? 'solidity' : 'text');
      var marks = X.lines[path] || {};
      var html = [];
      for (var i = 0; i < lines.length; i++) {
        var n = i + 1, items = marks[n], cls = '';
        if (items) cls = items.some(function (x) { return x.k === 'flow'; }) ? 'm-flow' : items.some(function (x) { return x.k === 'cond'; }) ? 'm-cond' : 'm-call';
        html.push('<tr data-line="' + n + '" class="' + cls + '"><td class="ln">' + n + '</td><td class="gt"></td><td class="cd">' + (lines[i] || ' ') + '</td></tr>');
      }
      bodyEl.innerHTML = (src.synthetic ? '<div class="note-box warn">Synthetic example source — not from a real project.</div>' : '') +
        '<div class="src-view" data-file="' + U.esc(path) + '"><table>' + html.join('') + '</table></div>';
      rows = Array.prototype.slice.call(bodyEl.querySelectorAll('tr'));
      hovered = []; selected = [];
      var parts = path.split('/');
      pathEl.innerHTML = U.esc(parts.slice(0, -1).join('/') + (parts.length > 1 ? '/' : '')) + '<b>' + U.esc(parts[parts.length - 1]) + '</b>';
      pathEl.title = path;
      filesEl.value = '';
      CV.annotate();
    }
    if (opts.line) scrollTo(opts.line, opts.force);
    else if (opts.top) bodyEl.scrollTop = 0;
    return true;
  };

  function remember() {
    CV.history.push({ file: file, top: bodyEl.scrollTop });
    if (CV.history.length > 40) CV.history.shift();
    document.getElementById('btn-code-back').disabled = false;
  }
  function goBack() {
    var last = CV.history.pop();
    document.getElementById('btn-code-back').disabled = !CV.history.length;
    if (!last) return;
    CV.showFile(last.file);
    bodyEl.scrollTop = last.top;
  }

  function row(n) { return rows[n - 1] || null; }

  function scrollTo(line, force) {
    var r = row(line);
    if (!r) return;
    var top = r.offsetTop, vis = bodyEl.scrollTop, hgt = bodyEl.clientHeight;
    if (!force && top > vis + 20 && top < vis + hgt - 40) return;
    bodyEl.scrollTop = Math.max(0, top - Math.max(40, hgt * 0.22));
  }

  /** Mark the open function(s) and annotate their statements in the file shown. */
  CV.annotate = function () {
    if (!file) return;
    var M = AL.app.model, st = AL.app.state;
    U.$$('tr.lens', bodyEl).forEach(function (a) { a.remove(); });
    rows.forEach(function (r) { r.classList.remove('fn', 'c-flow', 'c-mint', 'c-burn', 'c-cond', 'c-call'); });
    var roots = AL.graph.codeRoots(st);
    if (!roots.length) return;
    var inGraph = Object.create(null);
    roots.forEach(function (r) {
      inGraph[r] = true;
      M.opDescendants(r).forEach(function (d) { inGraph[d.id] = true; });
    });
    var current = Object.create(null);
    if (st.code.op) {
      current[st.code.op] = true;
      M.opDescendants(st.code.op).forEach(function (d) { current[d.id] = true; });
    }
    Object.keys(current).forEach(function (id) {
      var d = X.def[id];
      if (d && d.file === file) for (var n = d.start; n <= d.end; n++) { var r = row(n); if (r) r.classList.add('fn'); }
    });
    // annotations go into a lens row above the statement, indented like it, so long lines never hide them
    var heads = Object.create(null);
    Object.keys(current).forEach(function (id) {
      var d = X.def[id], o = M.get(id);
      if (!d || d.file !== file || !o) return;
      (heads[d.start] || (heads[d.start] = [])).push(headPill(M, o));
    });
    var marks = Object.create(null);
    Object.keys(X.lines[file] || {}).concat(Object.keys(heads)).forEach(function (n) { marks[n] = itemsAt(+n); });
    Object.keys(marks).forEach(function (n) {
      var r = row(+n);
      if (!r) return;
      var seen = Object.create(null), pills = (heads[n] || []).slice();
      marks[n].forEach(function (it) {
        if (!inGraph[it.op] && !(it.k === 'call' && inGraph[it.id])) return;
        var a = annotation(M, it);
        if (!a) return;
        r.classList.add('c-' + a.cls);
        // one pill per statement and reading (nested calls often repeat the same guard)
        var key = a.tag + '|' + a.text;
        if (!it.first || !current[it.k === 'call' ? it.id : it.op] || seen[key]) return;
        seen[key] = true;
        pills.push(h('span', { class: 'ann a-' + a.cls, 'data-k': it.k, 'data-id': it.id, 'data-op': it.op, title: a.title }, [h('b', { text: a.tag }), a.text]));
      });
      if (!pills.length) return;
      var indent = /^\s*/.exec(r.lastChild.textContent)[0].replace(/\t/g, '    ').length;
      var lens = h('tr', { class: 'lens' + (r.classList.contains('fn') ? ' fn' : ''), 'data-line': n }, [
        h('td', { class: 'ln' }), h('td', { class: 'gt' }), h('td', { class: 'cd', style: 'padding-left: calc(8px + ' + indent + 'ch)' }, pills)
      ]);
      r.parentNode.insertBefore(lens, r);
    });
  };

  /** Summary on the function header: what kind of operation the analysis made of it and who may call it. */
  function headPill(M, o) {
    var flows = (o.inputs || []).length + (o.outputs || []).length, kids = (M.opChildren[o.id] || []).length;
    var st = o.status || {};
    var bits = [o.initiator ? 'called by ' + M.label(o.initiator) : 'caller not recorded', U.plural(flows, 'asset flow')];
    if (kids) bits.push(U.plural(kids, 'call') + ' into other functions');
    if (st.support === 'llm') bits.push('kind proposed by the model');
    return h('span', { class: 'ann a-head', 'data-k': 'op', 'data-id': o.id, 'data-op': o.id,
      title: o.label + ' — ' + U.humanize(o.kind) + (st.rule ? ' (' + st.rule + ')' : '') + (st.note ? '\n' + st.note : '') }, [
      h('b', { text: String(o.kind).toUpperCase().replace(/_/g, ' ') }), bits.join(' · ')
    ]);
  }

  function shortEnt(M, id) {
    var e = M.get(id);
    if (!e) return id || '?';
    if (e.kind !== 'contract' && e.kind !== 'external_contract' && e.kind !== 'protocol' && e.expression &&
        (e.kind === 'unknown' || /^[A-Za-z_$][\w$]*$/.test(e.expression))) return e.expression;
    return e.label;
  }

  function annotation(M, it) {
    if (it.k === 'flow') {
      var fr = M.flowById[it.id];
      if (!fr) return null;
      var f = fr.flow, mech = f.mechanism || '';
      var tag, cls;
      if (!f.from) { tag = mech === 'credit' ? 'CREDIT' : 'MINT'; cls = 'mint'; }
      else if (!f.to) { tag = mech === 'debit' ? 'DEBIT' : 'BURN'; cls = 'burn'; }
      else { tag = 'TRANSFER'; cls = 'flow'; }
      var text = M.assetShort(f.asset) + '  ' + (f.from ? shortEnt(M, f.from) : '') + (f.from && f.to ? ' → ' : f.to ? '→ ' : '') + (f.to ? shortEnt(M, f.to) : '');
      return { tag: tag, cls: cls, text: text, title: tag.toLowerCase() + ' ' + M.amountText(f.amount) + (f.from ? ' from ' + M.label(f.from) : '') + (f.to ? ' to ' + M.label(f.to) : '') + ' — in ' + fr.op.label + ' (click to select in the graph)' };
    }
    if (it.k === 'cond') {
      var c = it.cond, op = M.get(it.op);
      if (c.kind === 'access_control') return { tag: 'WHO', cls: 'cond', text: op && op.initiator ? 'only ' + M.label(op.initiator) : c.text, title: c.text };
      if (c.kind === 'branch') return { tag: 'IF', cls: 'cond', text: U.truncate(c.text, 80), title: c.text + ' — the following step runs only in this case' };
      var tag = { external: 'NEEDS', loop: 'LOOP', timing: 'WHEN' }[c.kind] || 'GUARD';
      return { tag: tag, cls: 'cond', text: U.truncate(c.text, 80), title: U.humanize(c.kind) + ': ' + c.text };
    }
    if (it.k === 'call') {
      var o = M.get(it.id);
      if (!o) return null;
      return { tag: 'CALL', cls: 'call', text: o.label + (o.transaction === 'separate' ? ' (separate tx)' : ''), title: 'Calls ' + o.label + ' — ' + U.humanize(o.kind) + ' executed by ' + M.label(o.executor) };
    }
    return null;
  }

  // ---------------------------------------------------------------------------
  // source -> graph
  // ---------------------------------------------------------------------------
  /** Items on a line, without those cited only as part of a call path: a flow or guard of a nested
   *  call that lies inside the definition of one of its callers is shown at the CALL instead. */
  function onPath(it, n) {
    if (it.k === 'call') return false;
    var anc = AL.app.model.opAncestors(it.op);
    for (var i = 0; i < anc.length; i++) {
      var d = X.def[anc[i]];
      if (d && d.file === file && n >= d.start && n <= d.end) return true;
    }
    return false;
  }
  function itemsAt(n) {
    var all = file && X.lines[file] ? X.lines[file][n] || [] : [];
    return all.filter(function (it) { return !onPath(it, n); });
  }

  /** Graph elements supported by the given line items. */
  function graphTargets(items) {
    var G = AL.app.graph, nodes = [], edges = [];
    if (!G) return { nodes: nodes, edges: edges };
    items.forEach(function (it) {
      if (it.k === 'flow') {
        G.edges.forEach(function (e) { if (e.refs.some(function (r) { return r.type === 'flow' && r.id === it.id; })) edges.push(e.id); });
      } else if (it.k === 'cond') {
        if (G.byId[it.op]) nodes.push(it.op);
        var o = AL.app.model.get(it.op);
        if (it.cond.kind === 'access_control' && o && G.byId[o.initiator]) nodes.push(o.initiator);
      } else if (it.k === 'call') {
        if (G.byId[it.id]) nodes.push(it.id);
        G.edges.forEach(function (e) { if (e.kind === 'contains' && e.target === it.id) edges.push(e.id); });
      }
    });
    return { nodes: U.uniq(nodes), edges: U.uniq(edges) };
  }

  function onLineHover(ev) {
    var tr = ev.target.closest && ev.target.closest('tr[data-line]');
    if (!tr) return;
    var ann = ev.target.closest('.ann');
    if (ann && ann.dataset.k === 'op') { AL.render.flash([ann.dataset.id], []); return; }
    var items = itemsAt(+tr.dataset.line);
    if (ann) items = items.filter(function (it) { return it.k === ann.dataset.k && it.id === ann.dataset.id; });
    var t = graphTargets(items);
    AL.render.flash(t.nodes, t.edges);
  }

  function onLineClick(ev) {
    var tr = ev.target.closest && ev.target.closest('tr[data-line]');
    if (!tr || (window.getSelection && String(window.getSelection()).length)) return;
    var n = +tr.dataset.line, app = AL.app, M = app.model, st = app.state;
    var ann = ev.target.closest('.ann');
    if (ann && ann.dataset.k === 'op') { app.select(ann.dataset.id); return; }
    var items = itemsAt(n);
    if (ann) items = items.filter(function (it) { return it.k === ann.dataset.k && it.id === ann.dataset.id; });
    var roots = AL.graph.codeRoots(st);
    var shown = Object.create(null);
    roots.forEach(function (r) { shown[r] = true; M.opDescendants(r).forEach(function (d) { shown[d.id] = true; }); });
    // a statement of a function in the graph: select what it supports
    var mine = items.filter(function (it) { return shown[it.k === 'call' ? it.id : it.op]; });
    if (mine.length) {
      var t = graphTargets(mine);
      if (t.edges.length && mine[0].k === 'flow') { app.selectEdge(t.edges[0]); return; }
      app.select(mine[0].k === 'call' ? mine[0].id : mine[0].op);
      return;
    }
    // otherwise: open the innermost function defined around this line
    // (several operations can share a definition, e.g. an inherited function of two contracts:
    // prefer one already in the graph, then an entry point)
    var best = null, span = Infinity;
    function rank(id) { return (shown[id] ? 0 : 2) + (M.get(id).parent ? 1 : 0); }
    Object.keys(X.def).forEach(function (id) {
      var d = X.def[id], s = d.end - d.start;
      if (d.file !== file || n < d.start || n > d.end) return;
      if (s < span || (s === span && rank(id) < rank(best))) { best = id; span = s; }
    });
    if (!best) return;
    if (shown[best]) app.select(best);
    else app.openFunction(M.get(best).parent ? X.rootOf[best] : best, { keepScroll: true });
  }

  // ---------------------------------------------------------------------------
  // graph -> source
  // ---------------------------------------------------------------------------
  function locsOfEvidence(ids) {
    var M = AL.app.model, out = [];
    (ids || []).forEach(function (id) {
      var e = M.get(id);
      if (e && e.source && M.sources[e.source.file]) out.push({ file: e.source.file, start: e.source.start_line, end: e.source.end_line || e.source.start_line });
    });
    return out;
  }
  function stmts(locs) { return locs.filter(function (l) { return l.end - l.start < STMT_SPAN; }); }

  /** Source locations behind a graph node or edge. */
  function locsFor(target) {
    var M = AL.app.model, out = [];
    if (target.edge) {
      target.edge.refs.forEach(function (r) {
        if (r.type === 'flow') { var fr = M.flowById[r.id]; if (fr) out = out.concat(stmts(locsOfEvidence(fr.flow.evidence))); }
        else if (r.type === 'relation') out = out.concat(locsOfEvidence((M.get(r.id) || {}).evidence));
        else if (r.type === 'contains') out = out.concat(X.calls[r.id] || []);
        else if (r.type === 'initiator') {
          var o = M.get(r.id);
          (o && o.conditions || []).forEach(function (c) { if (c.kind === 'access_control') out = out.concat(locsOfEvidence(c.evidence)); });
        }
      });
      return out;
    }
    var id = target.node, t = M.type(id), G = AL.app.graph;
    if (t === 'operation') {
      if (X.calls[id] && X.calls[id].length) return X.calls[id];
      var d = X.def[id];
      return d ? [{ file: d.file, start: d.start, end: d.start }] : [];
    }
    if (t === 'entity' && G && !G.map) {
      // lines where the entity pays or receives, and the guards that make it the caller
      G.edges.forEach(function (e) {
        if (e.source !== id && e.target !== id) return;
        e.refs.forEach(function (r) {
          if (r.type !== 'flow') return;
          var fr = M.flowById[r.id];
          if (fr && (fr.flow.from === id || fr.flow.to === id)) out = out.concat(stmts(locsOfEvidence(fr.flow.evidence)));
        });
      });
      G.nodes.forEach(function (n) {
        if (n.type !== 'operation' || n.obj.initiator !== id) return;
        (n.obj.conditions || []).forEach(function (c) { if (c.kind === 'access_control') out = out.concat(locsOfEvidence(c.evidence)); });
      });
      return out;
    }
    if (t === 'entity') {
      var e = M.get(id);
      if (e && e.contract && e.contract.file && M.sources[e.contract.file]) return [];
    }
    return out;
  }

  function rowsFor(locs) {
    var out = [];
    locs.forEach(function (l) {
      if (l.file !== file) return;
      for (var n = l.start; n <= l.end; n++) { var r = row(n); if (r) out.push(r); }
    });
    return out;
  }
  function markRows(list, on, cls) { list.forEach(function (r) { r.classList.toggle(cls || 'xhl', on); }); }

  function showHint(locs, verb) {
    var other = locs.filter(function (l) { return l.file !== file; });
    if (!other.length || locs.length > other.length) { hintEl.hidden = true; return; }
    var seen = Object.create(null);
    U.clear(hintEl).appendChild(document.createTextNode(verb + ' in another file:'));
    other.forEach(function (l) {
      var k = l.file + '#' + l.start;
      if (seen[k] || Object.keys(seen).length >= 4) return;
      seen[k] = true;
      hintEl.appendChild(h('a', { class: 'ref', 'data-goto': l.file + '#' + l.start + '#' + l.end, text: l.file.split('/').pop() + ':' + l.start }));
    });
    hintEl.hidden = false;
  }

  /** Graph hover: highlight the lines behind the item (null clears). */
  CV.hoverGraph = function (target) {
    if (!file) return;
    markRows(hovered, false);
    hovered = [];
    if (!target) { showHint([], ''); return; }
    var locs = locsFor(target);
    hovered = rowsFor(locs);
    markRows(hovered, true);
    showHint(locs, 'Defined');
  };

  /** A graph item was selected: bring its source into view. sel: {id} | {edge} */
  CV.reveal = function (sel) {
    if (!X) return;
    var M = AL.app.model;
    markRows(selected, false, 'xsel');
    selected = [];
    var locs = [];
    if (sel && sel.edge) locs = locsFor({ edge: sel.edge });
    else if (sel && sel.id) {
      var t = M.type(sel.id);
      if (t === 'operation') {
        var d = X.def[sel.id];
        // a nested call: its call site when the caller is on screen, otherwise the callee's definition
        var calls = (X.calls[sel.id] || []).filter(function (l) { return l.file === file; });
        if (calls.length) locs = calls;
        else if (d) { CV.showFile(d.file, { push: true }); scrollTo(d.start, true); return; }
      } else if (t === 'entity') {
        var e = M.get(sel.id);
        if (AL.app.graph && AL.app.graph.map && e && e.contract && e.contract.file && M.sources[e.contract.file]) {
          CV.openContract(e.id, true);
          return;
        }
        locs = locsFor({ node: sel.id });
      }
    }
    if (!locs.length) return;
    if (!locs.some(function (l) { return l.file === file; })) CV.showFile(locs[0].file, { push: true });
    selected = rowsFor(locs);
    markRows(selected, true, 'xsel');
    var first = locs.filter(function (l) { return l.file === file; }).sort(function (a, b) { return a.start - b.start; })[0];
    if (first) scrollTo(first.start);
  };

  /** Open the file of a contract entity at its declaration. */
  CV.openContract = function (entId, push) {
    var M = AL.app.model, e = M.get(entId);
    if (!e || !e.contract || !M.sources[e.contract.file]) return false;
    var decl = locsOfEvidence(e.evidence).filter(function (l) { return l.file === e.contract.file; })[0];
    CV.showFile(e.contract.file, { push: push });
    if (decl) scrollTo(decl.start, true); else bodyEl.scrollTop = 0;
    return true;
  };

  /** Open the definition of a function and scroll to it. */
  CV.openDef = function (opId, keepScroll) {
    var d = X && X.def[opId];
    if (!d) return false;
    var changed = d.file !== file;
    CV.showFile(d.file, { push: true });
    if (changed || !keepScroll) scrollTo(d.start, !keepScroll);
    return true;
  };

  /** Refresh after a state change (outline highlight, annotations). */
  CV.update = function () {
    renderOutline();
    CV.annotate();
  };
})();
