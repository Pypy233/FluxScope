/* FluxScope viewer — indexed, read-only view over one analysis result. */
(function () {
  'use strict';
  var AL = window.FluxScope;
  var U = AL.util;

  function push(map, k, v) { (map[k] || (map[k] = [])).push(v); }

  function Model(data) {
    var self = this;
    this.data = data;
    this.meta = data.meta || {};
    this.scope = data.scope || {};
    var C = this.c = {
      asset: data.assets || [], entity: data.entities || [], operation: data.operations || [],
      position: data.positions || [], relation: data.relations || [], exit: data.exits || [],
      evidence: data.evidence || [], unresolved: data.unresolved || [], task: data.tasks || []
    };
    this.byId = Object.create(null);
    this.typeById = Object.create(null);
    Object.keys(C).forEach(function (t) {
      C[t].forEach(function (x) { self.byId[x.id] = x; self.typeById[x.id] = t; });
    });
    this.sources = Object.create(null);
    (data.sources || []).forEach(function (s) { self.sources[s.path] = s; });

    // hierarchy
    this.opChildren = Object.create(null);
    this.entChildren = Object.create(null);
    C.operation.forEach(function (o) { if (o.parent && self.byId[o.parent]) push(self.opChildren, o.parent, o); });
    Object.keys(this.opChildren).forEach(function (k) {
      self.opChildren[k].sort(function (a, b) { return (a.order || 0) - (b.order || 0); });
    });
    C.entity.forEach(function (e) { if (e.parent && self.byId[e.parent]) push(self.entChildren, e.parent, e); });

    // flows
    this.flows = [];
    this.flowById = Object.create(null);
    C.operation.forEach(function (o) {
      (o.inputs || []).forEach(function (f) { var r = { flow: f, op: o, dir: 'in' }; self.flows.push(r); self.flowById[f.id] = r; });
      (o.outputs || []).forEach(function (f) { var r = { flow: f, op: o, dir: 'out' }; self.flows.push(r); self.flowById[f.id] = r; });
    });

    // positions / relations / exits
    this.posByAsset = Object.create(null);
    this.posByHolder = Object.create(null);
    C.position.forEach(function (p) { push(self.posByAsset, p.asset, p); push(self.posByHolder, p.holder, p); });
    this.relFrom = Object.create(null);
    this.relTo = Object.create(null);
    C.relation.forEach(function (r) { push(self.relFrom, r.source, r); push(self.relTo, r.target, r); });
    this.exitsByPos = Object.create(null);
    this.exitsByOp = Object.create(null);
    C.exit.forEach(function (x) {
      push(self.exitsByPos, x.position, x);
      (x.steps || []).forEach(function (s) { push(self.exitsByOp, s.operation, x); });
    });
    this.unresolvedBySubject = Object.create(null);
    C.unresolved.forEach(function (u) { (u.subjects || []).forEach(function (s) { push(self.unresolvedBySubject, s, u); }); });
    C.operation.forEach(function (o) {
      (o.unresolved || []).forEach(function (uid) {
        var u = self.byId[uid];
        if (u && (self.unresolvedBySubject[o.id] || []).indexOf(u) < 0) push(self.unresolvedBySubject, o.id, u);
      });
    });
    C.exit.forEach(function (x) {
      (x.unresolved || []).forEach(function (uid) {
        var u = self.byId[uid];
        if (u && (self.unresolvedBySubject[x.id] || []).indexOf(u) < 0) push(self.unresolvedBySubject, x.id, u);
      });
    });

    // evidence back-references: evidence id -> [{id, label}]
    this.evUsers = Object.create(null);
    function cite(owner, list) { (list || []).forEach(function (e) { push(self.evUsers, e, owner); }); }
    C.asset.forEach(function (x) { cite(x.id, x.evidence); });
    C.entity.forEach(function (x) { cite(x.id, x.evidence); });
    C.position.forEach(function (x) { cite(x.id, x.evidence); });
    C.relation.forEach(function (x) {
      cite(x.id, x.evidence);
      (x.conditions || []).forEach(function (c) { cite(x.id, c.evidence); });
      ((x.status || {}).checks || []).forEach(function (c) { cite(x.id, c.evidence); });
    });
    C.operation.forEach(function (o) {
      cite(o.id, o.evidence);
      (o.conditions || []).forEach(function (c) { cite(o.id, c.evidence); });
      (o.inputs || []).concat(o.outputs || []).forEach(function (f) { cite(o.id, f.evidence); });
      (o.participants || []).forEach(function (p) { cite(o.id, p.evidence); });
      ((o.status || {}).checks || []).forEach(function (c) { cite(o.id, c.evidence); });
    });
    C.exit.forEach(function (x) {
      cite(x.id, x.evidence);
      (x.steps || []).forEach(function (s) { cite(x.id, s.evidence); (s.conditions || []).forEach(function (c) { cite(x.id, c.evidence); }); });
    });
    C.unresolved.forEach(function (x) { cite(x.id, x.evidence); });
    Object.keys(this.evUsers).forEach(function (k) { self.evUsers[k] = U.uniq(self.evUsers[k]); });

    this.buildSearch();
  }

  Model.prototype.get = function (id) { return this.byId[id] || null; };
  Model.prototype.type = function (id) { return this.typeById[id] || null; };

  Model.prototype.label = function (id) {
    var x = this.byId[id];
    if (!x) return id || '';
    var t = this.typeById[id];
    if (t === 'position') return x.label || (this.label(x.asset) + ' @ ' + this.label(x.holder));
    if (t === 'relation') return x.label || (U.humanize(x.kind) + ': ' + this.label(x.source) + ' → ' + this.label(x.target));
    if (t === 'evidence') return x.title || id;
    if (t === 'unresolved') return U.truncate(x.text, 60);
    if (t === 'task') return x.question;
    return x.label || x.title || id;
  };

  Model.prototype.assetShort = function (id) {
    var a = this.byId[id];
    if (!a) return id;
    return a.symbol || a.label || id;
  };

  Model.prototype.opAncestors = function (id) {
    var out = [], cur = this.byId[id], n = 0;
    while (cur && cur.parent && n++ < 64) { out.unshift(cur.parent); cur = this.byId[cur.parent]; }
    return out;
  };
  Model.prototype.entAncestors = Model.prototype.opAncestors;

  Model.prototype.opDescendants = function (id) {
    var out = [], self = this;
    (function walk(x) { (self.opChildren[x] || []).forEach(function (c) { out.push(c); walk(c.id); }); })(id);
    return out;
  };

  /** All flows of an operation and its descendants: [{flow, op, dir}]. */
  Model.prototype.flowsUnder = function (id) {
    var ids = Object.create(null);
    ids[id] = true;
    this.opDescendants(id).forEach(function (o) { ids[o.id] = true; });
    return this.flows.filter(function (f) { return ids[f.op.id]; });
  };

  Model.prototype.statusOf = function (id) {
    var x = this.byId[id];
    return x && x.status ? x.status : null;
  };

  Model.prototype.relationsOf = function (id) {
    return (this.relFrom[id] || []).concat(this.relTo[id] || []);
  };

  Model.prototype.evidenceOf = function (id) {
    var x = this.byId[id];
    return x ? (x.evidence || []) : [];
  };

  Model.prototype.unresolvedFor = function (ids) {
    var self = this, out = [];
    (Array.isArray(ids) ? ids : [ids]).forEach(function (i) { (self.unresolvedBySubject[i] || []).forEach(function (u) { if (out.indexOf(u) < 0) out.push(u); }); });
    return out;
  };

  /** Format an amount: value with asset unit, else expression; always names the asset. */
  Model.prototype.amountText = function (a) {
    if (!a) return '';
    var sym = this.assetShort(a.asset);
    if (a.value !== undefined && a.value !== null && a.value !== '') {
      return a.value + ' ' + sym + (a.unit === 'raw' ? ' (raw units)' : '');
    }
    if (a.expression) return a.expression + ' ' + sym;
    return 'unknown ' + sym;
  };

  Model.prototype.buildSearch = function () {
    var self = this;
    var items = [];
    function add(id, type, fields) {
      items.push({ id: id, type: type, text: fields.filter(Boolean).join(' \u0001 ').toLowerCase() });
    }
    this.c.asset.forEach(function (a) {
      add(a.id, 'asset', [a.id, a.label, a.symbol, a.kind, a.standard, a.identity && a.identity.expression, a.identity && a.identity.address, a.identity && a.identity.contract]);
    });
    this.c.entity.forEach(function (e) {
      add(e.id, 'entity', [e.id, e.label, e.kind, e.expression, e.description, e.contract && e.contract.name, e.contract && e.contract.address]);
    });
    this.c.operation.forEach(function (o) {
      add(o.id, 'operation', [o.id, o.label, o.kind, o.description, o.entrypoint && (o.entrypoint.contract + '.' + o.entrypoint.function), o.entrypoint && o.entrypoint.signature]);
    });
    this.c.position.forEach(function (p) {
      add(p.id, 'position', [p.id, self.label(p.id), p.kind, self.label(p.asset), self.assetShort(p.asset), self.label(p.holder), p.description]);
    });
    this.c.exit.forEach(function (x) { add(x.id, 'exit', [x.id, x.label]); });
    this.searchItems = items;
  };

  Model.prototype.search = function (q, limit) {
    q = String(q || '').trim().toLowerCase();
    if (!q) return [];
    var terms = q.split(/\s+/);
    var out = [];
    this.searchItems.forEach(function (it) {
      var ok = terms.every(function (t) { return it.text.indexOf(t) >= 0; });
      if (!ok) return;
      var score = it.text.indexOf(terms[0]);
      out.push({ id: it.id, type: it.type, score: score });
    });
    out.sort(function (a, b) { return a.score - b.score; });
    return out.slice(0, limit || 40);
  };

  /** Counts used by filter / legend panels. */
  Model.prototype.counts = function () {
    var c = { relKind: {}, nodeType: {}, basis: {}, verification: {}, entityKind: {}, positionKind: {}, assetKind: {}, opKind: {} };
    function inc(m, k) { if (k) m[k] = (m[k] || 0) + 1; }
    var self = this;
    ['asset', 'entity', 'operation', 'position'].forEach(function (t) { c.nodeType[t] = self.c[t].length; });
    this.c.relation.forEach(function (r) { inc(c.relKind, r.kind); });
    c.relKind.flow = this.flows.length;
    this.c.entity.forEach(function (e) { inc(c.entityKind, e.kind); });
    this.c.position.forEach(function (p) { inc(c.positionKind, p.kind); });
    this.c.asset.forEach(function (a) { inc(c.assetKind, a.kind); });
    this.c.operation.forEach(function (o) { inc(c.opKind, o.kind); });
    function st(s) { if (s) { inc(c.basis, s.basis); inc(c.verification, s.verification); } }
    ['asset', 'operation', 'position', 'relation', 'exit'].forEach(function (t) { self.c[t].forEach(function (x) { st(x.status); }); });
    this.flows.forEach(function (f) { st(f.flow.status); });
    return c;
  };

  AL.Model = Model;
})();
