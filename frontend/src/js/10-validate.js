/* FluxScope viewer — validation of imported analysis results.
 * 1) the embedded JSON schema (subset emitted by pydantic: $ref/$defs, type, enum,
 *    const, required, properties, additionalProperties, items, anyOf, nullable);
 * 2) a port of semantic_errors() from fluxscope/model.py (ids and accounting rules). */
(function () {
  'use strict';
  var AL = window.FluxScope;
  var MAX_ERRORS = 150;

  function typeOf(v) {
    if (v === null) return 'null';
    if (Array.isArray(v)) return 'array';
    if (typeof v === 'number') return Number.isInteger(v) ? 'integer' : 'number';
    return typeof v;
  }

  function typeMatches(v, t) {
    var actual = typeOf(v);
    if (t === 'number') return actual === 'number' || actual === 'integer';
    return actual === t;
  }

  function describe(v) {
    var t = typeOf(v);
    if (t === 'string') return 'string "' + AL.util.truncate(v, 40) + '"';
    if (t === 'object' || t === 'array') return t;
    return t + ' ' + String(v);
  }

  function Validator(schema) {
    this.root = schema;
    this.errors = [];
    this.warnings = [];
  }

  Validator.prototype.resolve = function (node) {
    var guard = 0;
    while (node && node.$ref && guard++ < 20) {
      var ref = node.$ref;
      if (ref.indexOf('#/$defs/') !== 0) throw new Error('unsupported $ref ' + ref);
      node = this.root.$defs[ref.slice(8)];
    }
    return node;
  };

  Validator.prototype.err = function (path, msg) {
    if (this.errors.length < MAX_ERRORS) this.errors.push({ path: path || '(root)', message: msg });
  };

  /** Validate without recording, used to choose among anyOf branches. */
  Validator.prototype.probe = function (value, node) {
    var sub = new Validator(this.root);
    sub.check(value, node, '');
    return sub.errors.length === 0;
  };

  Validator.prototype.check = function (value, node, path) {
    node = this.resolve(node);
    if (!node) return;
    var self = this;

    if (node.anyOf) {
      for (var i = 0; i < node.anyOf.length; i++) {
        if (this.probe(value, node.anyOf[i])) return;
      }
      // Report against the most plausible branch: the non-null one when value is not null.
      var branches = node.anyOf.filter(function (b) { b = self.resolve(b); return !(b && b.type === 'null'); });
      if (branches.length === 1 && value !== null) this.check(value, branches[0], path);
      else this.err(path, 'does not match any allowed form (got ' + describe(value) + ')');
      return;
    }

    if (node.type) {
      var types = Array.isArray(node.type) ? node.type : [node.type];
      if (!types.some(function (t) { return typeMatches(value, t); })) {
        this.err(path, 'must be ' + types.join(' or ') + ' (got ' + describe(value) + ')');
        return;
      }
    }
    if (node.const !== undefined && value !== node.const) {
      this.err(path, 'must be "' + node.const + '" (got ' + describe(value) + ')');
    }
    if (node.enum && node.enum.indexOf(value) < 0) {
      this.err(path, 'must be one of: ' + node.enum.join(', ') + ' (got ' + describe(value) + ')');
    }
    var t = typeOf(value);
    if (t === 'object') {
      var props = node.properties || {};
      (node.required || []).forEach(function (k) {
        if (!(k in value)) self.err(path, 'missing required property "' + k + '"');
      });
      Object.keys(value).forEach(function (k) {
        var p = path ? path + '.' + k : k;
        if (props[k]) self.check(value[k], props[k], p);
        else if (node.additionalProperties === false) {
          if (self.warnings.length < MAX_ERRORS) self.warnings.push({ path: p, message: 'unknown property (ignored)' });
        } else if (node.additionalProperties && typeof node.additionalProperties === 'object') {
          self.check(value[k], node.additionalProperties, p);
        }
      });
    } else if (t === 'array' && node.items) {
      value.forEach(function (item, i) {
        var label = path + '[' + i + ']';
        if (item && typeof item === 'object' && typeof item.id === 'string') label += '{' + AL.util.truncate(item.id, 40) + '}';
        self.check(item, node.items, label);
      });
    }
  };

  // ---------------------------------------------------------------------------
  // Semantic checks (port of fluxscope.model.semantic_errors)
  // ---------------------------------------------------------------------------
  function semantic(d) {
    var errors = [];
    function E(path, msg) { if (errors.length < MAX_ERRORS) errors.push({ path: path, message: msg }); }
    function ids(arr) { var s = Object.create(null); (arr || []).forEach(function (x) { if (x && x.id) s[x.id] = x; }); return s; }
    var assets = ids(d.assets), entities = ids(d.entities), ops = ids(d.operations), positions = ids(d.positions);
    var evidence = ids(d.evidence), unresolved = ids(d.unresolved), relations = ids(d.relations), exits = ids(d.exits);
    function isNode(id) { return !!(assets[id] || entities[id] || ops[id] || positions[id]); }

    var seen = Object.create(null);
    ['assets', 'entities', 'operations', 'positions', 'relations', 'exits', 'evidence', 'unresolved'].forEach(function (coll) {
      (d[coll] || []).forEach(function (x, i) {
        if (seen[x.id]) E(coll + '[' + i + ']', 'duplicate id "' + x.id + '" (also used in ' + seen[x.id] + ')');
        else seen[x.id] = coll;
      });
    });
    function ev(path, list) {
      (list || []).forEach(function (e) { if (!evidence[e]) E(path, 'unknown evidence "' + e + '"'); });
    }
    function ent(path, id, what) { if (id && !entities[id]) E(path, 'unknown ' + (what || 'entity') + ' "' + id + '"'); }

    (d.assets || []).forEach(function (a) {
      var p = 'asset ' + a.id;
      if (a.issuer) ent(p, a.issuer, 'issuer');
      ev(p, a.evidence);
    });
    (d.entities || []).forEach(function (e) {
      var p = 'entity ' + e.id;
      if (e.parent && !entities[e.parent]) E(p, 'unknown parent "' + e.parent + '"');
      ev(p, e.evidence);
    });
    // parent cycles
    (d.entities || []).forEach(function (e) {
      var cur = e, n = 0;
      while (cur && cur.parent && n++ < 100) { cur = entities[cur.parent]; if (cur === e) { E('entity ' + e.id, 'parent chain forms a cycle'); break; } }
    });
    (d.operations || []).forEach(function (o) {
      var p = 'operation ' + o.id;
      if (o.parent && !ops[o.parent]) E(p, 'unknown parent "' + o.parent + '"');
      ent(p, o.initiator); ent(p, o.executor);
      var condIds = Object.create(null);
      (o.conditions || []).forEach(function (c) { condIds[c.id] = true; ev(p + ' condition ' + c.id, c.evidence); });
      (o.inputs || []).concat(o.outputs || []).forEach(function (f) {
        var fp = p + ' flow ' + f.id;
        if (!assets[f.asset]) E(fp, 'unknown asset "' + f.asset + '"');
        if (f.amount && f.amount.asset !== f.asset) E(fp, 'amount is denominated in "' + f.amount.asset + '" but the flow moves "' + f.asset + '"');
        ent(fp, f.from); ent(fp, f.to);
        (f.conditions || []).forEach(function (c) { if (!condIds[c]) E(fp, 'unknown condition "' + c + '"'); });
        ev(fp, f.evidence);
      });
      (o.participants || []).forEach(function (pt) { ent(p, pt.entity, 'participant'); });
      (o.unresolved || []).forEach(function (u) { if (!unresolved[u]) E(p, 'unknown unresolved item "' + u + '"'); });
      ev(p, o.evidence);
    });
    (d.operations || []).forEach(function (o) {
      var cur = o, n = 0;
      while (cur && cur.parent && n++ < 100) { cur = ops[cur.parent]; if (cur === o) { E('operation ' + o.id, 'parent chain forms a cycle'); break; } }
    });
    (d.positions || []).forEach(function (p) {
      var pp = 'position ' + p.id;
      if (!entities[p.holder]) E(pp, 'unknown holder "' + p.holder + '"');
      if (!assets[p.asset]) E(pp, 'unknown asset "' + p.asset + '"');
      if (p.amount && p.amount.asset !== p.asset) E(pp, 'amount must be denominated in the position asset');
      if (p.counterparty) ent(pp, p.counterparty, 'counterparty');
      if (p.kind === 'debt' && assets[p.asset] && (assets[p.asset].kind === 'share' || assets[p.asset].kind === 'lp_token')) {
        E(pp, 'a debt position cannot be denominated in a share/LP asset');
      }
      ev(pp, p.evidence);
    });
    (d.relations || []).forEach(function (r) {
      var rp = 'relation ' + r.id;
      [r.source, r.target].forEach(function (x) { if (!isNode(x)) E(rp, 'unknown node "' + x + '"'); });
      if (r.asset && !assets[r.asset]) E(rp, 'unknown asset "' + r.asset + '"');
      if (r.operation && !ops[r.operation]) E(rp, 'unknown operation "' + r.operation + '"');
      if (r.amount && !assets[r.amount.asset]) E(rp, 'amount uses unknown asset "' + r.amount.asset + '"');
      if (r.kind === 'claim') {
        var s = positions[r.source], t = positions[r.target];
        if (s && t && s.asset === t.asset && s.holder === t.holder) E(rp, 'a claim cannot point to the same position');
      }
      if (r.kind === 'debt') {
        var sp = positions[r.source];
        if (sp && sp.kind !== 'debt') E(rp, 'debt relations must start at a debt position');
      }
      ev(rp, r.evidence);
    });
    (d.exits || []).forEach(function (x) {
      var xp = 'exit ' + x.id;
      if (!positions[x.position]) E(xp, 'unknown position "' + x.position + '"');
      (x.steps || []).forEach(function (s) {
        if (!ops[s.operation]) E(xp, 'unknown operation "' + s.operation + '"');
        if (s.actor) ent(xp, s.actor, 'actor');
      });
      (x.consumes || []).concat(x.returns || []).forEach(function (f) { if (!assets[f.asset]) E(xp, 'unknown asset "' + f.asset + '"'); });
      ev(xp, x.evidence);
    });
    (d.unresolved || []).forEach(function (u) {
      (u.subjects || []).forEach(function (s) {
        if (!isNode(s) && !relations[s] && !exits[s]) E('unresolved ' + u.id, 'unknown subject "' + s + '"');
      });
    });
    var sources = Object.create(null), nsrc = 0;
    (d.sources || []).forEach(function (s) { sources[s.path] = true; nsrc++; });
    (d.evidence || []).forEach(function (e) {
      if (e.kind === 'source' && !e.source) E('evidence ' + e.id, 'source evidence needs a source location');
      if (e.source && nsrc && !sources[e.source.file] && !e.source.snippet) E('evidence ' + e.id, 'file "' + e.source.file + '" is not embedded and no snippet is given');
    });
    (d.tasks || []).forEach(function (t) {
      (t.focus || []).forEach(function (f) {
        if (!isNode(f) && !relations[f] && !exits[f]) E('task ' + t.id, 'unknown focus "' + f + '"');
      });
    });
    return errors;
  }

  /**
   * Validate parsed data. Returns {ok, errors:[{path,message}], warnings:[...]}.
   */
  AL.validate = function (data, schema) {
    var errors = [], warnings = [];
    if (!data || typeof data !== 'object' || Array.isArray(data)) {
      return { ok: false, errors: [{ path: '(root)', message: 'expected a JSON object (a FluxScope analysis result)' }], warnings: [] };
    }
    if (data.schema !== undefined && data.schema !== AL.SUPPORTED_SCHEMA) {
      errors.push({ path: 'schema', message: 'not a FluxScope analysis result: schema is "' + data.schema + '", expected "' + AL.SUPPORTED_SCHEMA + '"' });
      return { ok: false, errors: errors, warnings: warnings };
    }
    var ver = data.schema_version;
    if (ver === undefined) {
      warnings.push({ path: 'schema_version', message: 'missing; assuming 0.1.0' });
    } else if (typeof ver !== 'string' || !AL.SUPPORTED_VERSION.test(ver)) {
      errors.push({ path: 'schema_version', message: 'unsupported schema version "' + ver + '"; this viewer reads version 0.1.x' });
      return { ok: false, errors: errors, warnings: warnings };
    }
    var probe = data;
    if (ver && ver !== '0.1.0') {
      warnings.push({ path: 'schema_version', message: 'version ' + ver + ' is newer than 0.1.0; unknown fields are ignored' });
      probe = Object.assign({}, data, { schema_version: '0.1.0' });
    }
    if (schema) {
      var v = new Validator(schema);
      v.check(probe, schema, '');
      errors = errors.concat(v.errors);
      warnings = warnings.concat(v.warnings);
    }
    if (!errors.length) errors = errors.concat(semantic(data));
    return { ok: errors.length === 0, errors: errors, warnings: warnings };
  };

  AL.validate.semantic = semantic;
})();
