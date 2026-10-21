/* FluxScope viewer — details panel for the current selection. */
(function () {
  'use strict';
  var AL = window.FluxScope;
  var U = AL.util;
  var h = U.h;

  var D = AL.details = {};
  var SHARE_ASSET = { share: true, lp_token: true, accounting: true };
  var SHARE_POS = { share: true, lp: true, claim: true };

  // ---------------------------------------------------------------------------
  // small building blocks
  // ---------------------------------------------------------------------------
  D.refChip = function (M, id, label) {
    var t = M.type(id) || 'unknown';
    var known = !!M.get(id);
    return h('span', { class: 'ref-chip', 'data-ref': id, title: (AL.TYPE_LABEL[t] || t) + ' · ' + id + (known ? '' : ' (not in this result)') }, [
      h('span', { class: 'type-dot ' + t, text: AL.TYPE_LETTER[t] || '?' }),
      h('span', { class: 'rc-label', text: label || M.label(id) })
    ]);
  };
  var chip = D.refChip;

  function evLabel(M, id) {
    var e = M.get(id);
    if (!e) return id + ' (missing)';
    if (e.source) {
      var f = e.source.file.split('/').pop();
      return f + ':' + e.source.start_line + (e.source.end_line && e.source.end_line !== e.source.start_line ? '–' + e.source.end_line : '');
    }
    if (e.execution && e.execution.tx_hash) return e.kind + ' ' + U.shortHash(e.execution.tx_hash) + (e.execution.log_index !== undefined ? '#' + e.execution.log_index : '');
    return e.kind + ': ' + U.truncate(e.title, 28);
  }

  D.evChips = function (M, ids, what) {
    ids = U.uniq(ids || []);
    if (!ids.length) {
      return h('span', { class: 'ev-missing', 'data-testid': 'evidence-missing', title: 'No source location, transaction or other evidence supports this item in the data.' },
        '⚠ No evidence attached' + (what ? ' to this ' + what : ''));
    }
    var box = h('div', { class: 'ev-chips' });
    ids.forEach(function (id) {
      var e = M.get(id);
      var cur = AL.code.currentEvidence() === id;
      box.appendChild(h('button', { class: 'ev-chip k-' + (e ? e.kind : 'missing') + (cur ? ' active' : ''), 'data-ev': id, title: e ? e.title : 'Missing evidence ' + id, text: evLabel(M, id) }));
    });
    return box;
  };
  var evChips = D.evChips;

  function badges(status, extra) {
    var row = h('div', { class: 'd-badges' });
    if (status) {
      row.appendChild(h('span', { class: 'badge basis-' + status.basis, 'data-testid': 'badge-basis', title: AL.SCOPE_EXPLAIN[status.basis === 'static' ? 'static_analysis' : status.basis === 'observed' ? 'execution_observation' : 'example'] || '', text: AL.BASIS_LABEL[status.basis] || status.basis }));
      row.appendChild(h('span', { class: 'badge ver-' + status.verification, text: (AL.VERIFICATION_GLYPH[status.verification] || '') + ' ' + (AL.VERIFICATION_LABEL[status.verification] || status.verification) }));
      row.appendChild(h('span', { class: 'badge support', text: 'by ' + (AL.SUPPORT_LABEL[status.support] || status.support) + (status.rule ? ' · ' + status.rule : '') }));
    }
    (extra || []).forEach(function (b) { if (b) row.appendChild(b); });
    return row;
  }

  function sec(title, children, opt) {
    opt = opt || {};
    var head = h('h4', {}, [title, opt.count !== undefined ? h('span', { class: 'count', text: '(' + opt.count + ')' }) : null]);
    return h('div', { class: 'd-sec' + (opt.key ? ' key' : ''), 'data-section': opt.id || null }, [head].concat(children));
  }

  function header(typeLabel, kind, title, sub, status, extra) {
    return h('div', { class: 'd-head' }, [
      h('div', { class: 'd-type' }, [typeLabel, kind ? h('span', { class: 'mini-chip', text: U.humanize(kind) }) : null]),
      h('div', { class: 'd-title', 'data-testid': 'details-title', text: title }),
      sub ? h('div', { class: 'd-sub', text: sub }) : null,
      badges(status, extra)
    ]);
  }

  function amountEl(M, a) {
    if (!a) return h('span', { class: 'muted', text: 'amount not recorded' });
    var sym = M.assetShort(a.asset);
    var hasVal = a.value !== undefined && a.value !== null && a.value !== '';
    var el = h('span', { class: 'amount', title: 'Denominated in ' + M.label(a.asset) + (a.unit ? ' · unit: ' + a.unit : '') + (a.note ? ' · ' + a.note : '') });
    if (hasVal) el.appendChild(document.createTextNode(a.value + ' '));
    else if (a.expression) el.appendChild(document.createTextNode(a.expression + ' '));
    else el.appendChild(document.createTextNode('unknown '));
    el.appendChild(h('span', { class: 'unit', text: sym }));
    if (hasVal && a.unit === 'raw') el.appendChild(h('span', { class: 'muted', text: ' (raw units)' }));
    if (hasVal && a.expression) el.appendChild(h('span', { class: 'muted', text: '  = ' + a.expression }));
    return el;
  }
  D.amountEl = amountEl;

  function condEl(M, c) {
    var st = c.status ? h('span', { class: 'badge ver-' + c.status.verification, style: 'margin-left:4px', text: AL.VERIFICATION_LABEL[c.status.verification] }) : null;
    return h('div', { class: 'cond kind-' + c.kind }, [
      h('span', { class: 'ck', text: U.humanize(c.kind) }),
      (c.negated ? 'NOT ' : '') + c.text,
      c.expression && c.expression !== c.text ? h('div', { class: 'mono muted', text: c.expression }) : null,
      st,
      evChips(M, c.evidence, 'condition')
    ]);
  }

  function condList(M, conds, emptyText) {
    if (!conds || !conds.length) return [h('div', { class: 'muted small', text: emptyText || 'No conditions recorded.' })];
    return conds.map(function (c) { return condEl(M, c); });
  }

  function checksEl(M, status) {
    if (!status || !status.checks || !status.checks.length) return null;
    return h('div', {}, status.checks.map(function (c) {
      var g = { pass: '✓', fail: '✗', unknown: '?' }[c.result] || '?';
      return h('div', { class: 'check' }, [
        h('span', { class: 'cr ' + c.result, text: g, title: c.result }),
        h('div', {}, [c.description, c.detail ? h('div', { class: 'muted small mono', text: c.detail }) : null, evChips(M, c.evidence, 'check')])
      ]);
    }));
  }

  function statusSection(M, status, title) {
    if (!status) return null;
    var kids = [];
    var explain = {
      static: 'Candidate behaviour: the code supports it, but no execution was observed.',
      observed: 'Seen in a concrete execution (transaction, log or trace).',
      example: 'Synthetic example data.',
      config: 'Comes from the project / deployment configuration.'
    }[status.basis];
    if (explain) kids.push(h('div', { class: 'small muted', text: explain }));
    if (status.note) kids.push(h('div', { class: 'small', style: 'margin-top:4px', text: status.note }));
    var ch = checksEl(M, status);
    if (ch) kids.push(h('div', { style: 'margin-top:6px' }, ch));
    return sec(title || 'Status & checks', kids, { id: 'status' });
  }

  function evidenceSection(M, ids, what) {
    return sec('Evidence', [evChips(M, ids, what)], { id: 'evidence', count: U.uniq(ids || []).length });
  }

  function unresolvedSection(M, ids) {
    var list = M.unresolvedFor(ids);
    if (!list.length) return null;
    return sec('Unresolved', list.map(function (u) {
      return h('div', { class: 'unres-item', 'data-ref': u.id }, [h('div', { class: 'ui-kind', text: U.humanize(u.kind) }), u.text]);
    }), { count: list.length, id: 'unresolved' });
  }

  function dirKind(f) { return !f.from ? 'mint' : !f.to ? 'burn' : null; }

  function flowRow(M, f, op, showOp) {
    var dk = dirKind(f);
    var tag = dk || (f.__dir === 'out' ? 'out' : 'in');
    var path = h('div', {}, [
      f.from ? chip(M, f.from) : h('span', { class: 'muted', text: '(minted)' }),
      ' → ',
      f.to ? chip(M, f.to) : h('span', { class: 'muted', text: '(burned)' })
    ]);
    return h('div', { class: 'flow-row', 'data-flow': f.id }, [
      h('span', { class: 'flow-dir ' + tag, text: tag.toUpperCase() }),
      h('div', {}, [
        h('div', {}, [chip(M, f.asset, M.assetShort(f.asset)), ' ', amountEl(M, f.amount), f.mechanism ? h('span', { class: 'muted small', text: '  via ' + f.mechanism }) : null]),
        path,
        showOp && op ? h('div', { class: 'small muted' }, ['in ', chip(M, op.id)]) : null,
        f.conditions && f.conditions.length ? h('div', { class: 'small muted', text: 'only if: ' + f.conditions.map(function (cid) {
          var c = op && (op.conditions || []).filter(function (x) { return x.id === cid; })[0];
          return c ? c.text : cid;
        }).join('; ') }) : null,
        f.status ? badges(f.status) : null,
        evChips(M, f.evidence, 'flow')
      ])
    ]);
  }
  D.flowRow = flowRow;

  function flowsOf(op) {
    return (op.inputs || []).map(function (f) { return Object.assign({ __dir: 'in' }, f); })
      .concat((op.outputs || []).map(function (f) { return Object.assign({ __dir: 'out' }, f); }));
  }

  function visibilityNote(M, id) {
    var app = AL.app;
    if (!app || app.isVisible(id)) return null;
    var pv = AL.graph.preferredView(M, id, app.state.view);
    return h('div', { class: 'note-box info', 'data-testid': 'not-visible' }, [
      'Not shown in the current graph (view, filters or collapsed levels). ',
      h('button', { class: 'link-btn', 'data-action': 'reveal', 'data-target': id, text: 'Reveal in ' + AL.graph.VIEWS[pv].label + ' view' })
    ]);
  }

  // ---------------------------------------------------------------------------
  // share / backing summary — the key interaction
  // ---------------------------------------------------------------------------
  function backingTree(M, posId, depth, seen) {
    if (depth > 6 || seen[posId]) return null;
    seen[posId] = true;
    var claims = (M.relFrom[posId] || []).filter(function (r) { return r.kind === 'claim'; });
    var deps = (M.relFrom[posId] || []).filter(function (r) { return r.kind === 'depends_on'; });
    if (!claims.length && !deps.length) return null;
    var ul = h('div', { class: 'backing', style: 'margin-left:' + (depth ? 12 : 0) + 'px; border-left:' + (depth ? '2px solid #e4dcfb' : '0') + '; padding-left:' + (depth ? 8 : 0) + 'px' });
    claims.forEach(function (r) {
      var t = M.get(r.target);
      var row = h('div', { class: 'item-card', 'data-claim': r.id }, [
        h('div', { class: 'ic-head' }, [
          h('span', { class: 'mini-chip', text: 'claims' }),
          chip(M, r.target),
          t ? h('span', { class: 'muted small' }, ['held by ', chip(M, t.holder)]) : null
        ]),
        h('div', { class: 'ic-sub' }, [r.label && r.label !== 'claim' ? r.label : null, r.basis_detail ? h('div', { class: 'mono', text: r.basis_detail }) : null]),
        r.conditions && r.conditions.length ? h('div', {}, condList(M, r.conditions)) : null,
        badges(r.status, [h('span', { class: 'ref', 'data-ref': r.id, text: 'relation details', style: 'font-size:11px' })]),
        evChips(M, r.evidence, 'claim')
      ]);
      ul.appendChild(row);
      var sub = backingTree(M, r.target, depth + 1, seen);
      if (sub) ul.appendChild(sub);
    });
    deps.forEach(function (r) {
      ul.appendChild(h('div', { class: 'item-card' }, [
        h('div', { class: 'ic-head' }, [h('span', { class: 'mini-chip', text: 'depends on' }), chip(M, r.target)]),
        r.label ? h('div', { class: 'ic-sub', text: r.label }) : null,
        badges(r.status),
        evChips(M, r.evidence, 'dependency')
      ]));
    });
    return ul;
  }

  function dependsList(M, ids) {
    var out = [];
    ids.forEach(function (id) {
      (M.relFrom[id] || []).forEach(function (r) { if (r.kind === 'depends_on' && out.indexOf(r) < 0) out.push(r); });
    });
    return out;
  }

  /** Walk claims transitively and collect every dependency target (protocols / contracts). */
  function transitiveDeps(M, startIds) {
    var seen = Object.create(null), out = [], q = startIds.slice();
    while (q.length) {
      var id = q.shift();
      if (seen[id]) continue;
      seen[id] = true;
      (M.relFrom[id] || []).forEach(function (r) {
        if (r.kind === 'claim') { q.push(r.target); var t = M.get(r.target); if (t && t.asset) q.push(t.asset); }
        if (r.kind === 'depends_on') { out.push({ rel: r, via: id }); q.push(r.target); }
      });
      var p = M.get(id);
      if (M.type(id) === 'position' && p && p.asset && !seen[p.asset]) q.push(p.asset);
    }
    return out;
  }

  function exitCard(M, x) {
    var steps = (x.steps || []).slice().sort(function (a, b) { return a.order - b.order; });
    var actors = U.uniq(steps.map(function (s) { return s.actor; }));
    return h('div', { class: 'item-card', 'data-exit-card': x.id }, [
      h('div', { class: 'ic-head' }, [h('span', { class: 'ic-title', text: x.label }), badges(x.status)]),
      h('div', { class: 'ic-sub' }, [
        U.plural(steps.length, 'step') + (steps.some(function (s) { return s.optional; }) ? ' (some conditional)' : '') + ' · actors: ',
        actors.length ? actors.map(function (a) { return chip(M, a); }) : h('span', { class: 'muted', text: 'not recorded' })
      ]),
      x.returns && x.returns.length ? h('div', { class: 'ic-sub' }, ['returns: '].concat(x.returns.map(function (f) { return h('span', {}, [amountEl(M, f.amount), f.to ? h('span', {}, [' to ', chip(M, f.to)]) : null, '  ']); }))) : null,
      h('div', { style: 'margin-top:5px; display:flex; gap:10px' }, [
        h('button', { class: 'link-btn', 'data-action': 'show-exit', 'data-target': x.id, text: 'Show exit path' }),
        h('span', { class: 'ref', 'data-ref': x.id, text: 'step details' })
      ])
    ]);
  }

  function shareSummary(M, asset, positions, focusPos) {
    var rows = [];
    var holders = positions.map(function (p) {
      return h('div', { class: p.id === focusPos ? 'hold-group' : 'hold-group' }, [
        chip(M, p.holder), ' ', p.amount ? amountEl(M, p.amount) : null, ' ',
        p.id === focusPos ? h('span', { class: 'mini-chip', text: 'this position' }) : chip(M, p.id, 'position'),
        evChips(M, p.evidence, 'position')
      ]);
    });
    rows.push(h('div', { class: 'ss-row', 'data-testid': 'share-holders' }, [h('div', { class: 'ss-k', text: 'Held by' }), h('div', {}, holders.length ? holders : [h('span', { class: 'muted', text: 'No holder positions recorded.' })])]));
    var issuer = asset.issuer ? h('div', {}, [chip(M, asset.issuer), h('span', { class: 'muted small', text: ' issues / accounts for this asset' })]) : null;
    if (issuer) rows.push(h('div', { class: 'ss-row' }, [h('div', { class: 'ss-k', text: 'Issued by' }), issuer]));
    var trees = h('div', {});
    var any = false;
    (focusPos ? positions.filter(function (p) { return p.id === focusPos; }) : positions).forEach(function (p) {
      var t = backingTree(M, p.id, 0, Object.create(null));
      if (t) {
        any = true;
        if (!focusPos && positions.length > 1) trees.appendChild(h('div', { class: 'small muted', style: 'margin:4px 0 2px' }, ['for ', chip(M, p.id)]));
        trees.appendChild(t);
      }
    });
    rows.push(h('div', { class: 'ss-row', 'data-testid': 'share-claims' }, [h('div', { class: 'ss-k', text: 'Claims on' }), any ? trees : h('span', { class: 'note-box warn', style: 'margin:0', text: 'No claim relation recorded — what this share is redeemable for is not established by the data.' })]));
    var deps = transitiveDeps(M, (focusPos ? [focusPos] : positions.map(function (p) { return p.id; })).concat([asset.id]));
    var depTargets = U.uniq(deps.map(function (d) { return d.rel.target; }));
    rows.push(h('div', { class: 'ss-row', 'data-testid': 'share-deps' }, [h('div', { class: 'ss-k', text: 'Depends on' }),
      depTargets.length ? h('div', {}, depTargets.map(function (t) {
        var rs = deps.filter(function (d) { return d.rel.target === t; });
        var ent = M.get(t);
        return h('div', { class: 'hold-group' }, [chip(M, t), ent && ent.in_scope === false ? h('span', { class: 'badge scope-out', text: 'outside analysis scope' }) : null,
          h('div', { class: 'small muted' }, rs.map(function (d) { return (d.rel.label || 'depends on') + ' (via ' + M.label(d.via) + ')'; }).join('; ')),
          evChips(M, [].concat.apply([], rs.map(function (d) { return d.rel.evidence || []; })), 'dependency')]);
      })) : h('span', { class: 'muted', text: 'No dependency relations recorded.' })]));
    var exits = [];
    (focusPos ? [focusPos] : positions.map(function (p) { return p.id; })).forEach(function (pid) { (M.exitsByPos[pid] || []).forEach(function (x) { if (exits.indexOf(x) < 0) exits.push(x); }); });
    rows.push(h('div', { class: 'ss-row', 'data-testid': 'share-exits' }, [h('div', { class: 'ss-k', text: 'Exit' }),
      exits.length ? h('div', {}, exits.map(function (x) { return exitCard(M, x); })) : h('span', { class: 'note-box warn', style: 'margin:0', text: 'No exit path recorded for this position.' })]));
    rows.push(h('div', { class: 'ss-note' }, 'A share is a claim on the positions it points to, not additional funds. Amounts are shown per asset and are never added across assets or across share and underlying.'));
    return sec('Share summary — who holds it, what backs it, how to exit', [h('div', { class: 'share-summary' }, rows)], { key: true, id: 'share-summary' });
  }

  function debtSummary(M, asset, positions, focusPos) {
    var rows = [];
    var shown = focusPos ? positions.filter(function (p) { return p.id === focusPos; }) : positions;
    rows.push(h('div', { class: 'ss-row', 'data-testid': 'debt-holders' }, [h('div', { class: 'ss-k', text: 'Owed by' }),
      h('div', {}, shown.map(function (p) {
        return h('div', { class: 'hold-group' }, [chip(M, p.holder), ' ', p.id === focusPos ? h('span', { class: 'mini-chip', text: 'this position' }) : chip(M, p.id, 'position'),
          p.counterparty ? h('span', {}, [h('span', { class: 'muted small', text: '  owes  ' }), chip(M, p.counterparty)]) : null, evChips(M, p.evidence, 'position')]);
      }))]));
    var rels = [];
    shown.forEach(function (p) { (M.relFrom[p.id] || []).forEach(function (r) { if (r.kind === 'debt') rels.push(r); }); });
    rows.push(h('div', { class: 'ss-row', 'data-testid': 'debt-basis' }, [h('div', { class: 'ss-k', text: 'Why a debt' }),
      rels.length ? h('div', {}, rels.map(function (r) {
        return h('div', { class: 'hold-group' }, [h('span', { class: 'ref', 'data-ref': r.id, text: r.description || r.label || 'debt' }), ' ', badges(r.status),
          r.basis_detail ? h('div', { class: 'small muted', text: r.basis_detail }) : null, evChips(M, r.evidence, 'debt')]);
      })) : h('span', { class: 'muted', text: 'No debt relation recorded.' })]));
    var ops = [];
    M.flows.forEach(function (fr) {
      if (fr.flow.asset !== asset.id) return;
      var root = fr.op;
      while (root.parent && M.get(root.parent)) root = M.get(root.parent);
      var what = { credit: 'increases', debit: 'decreases', 'ledger-transfer': 'moves', mint: 'increases', burn: 'decreases' }[fr.flow.mechanism] || fr.flow.mechanism;
      var key = root.id + '|' + what;
      if (ops.some(function (x) { return x.key === key; })) return;
      ops.push({ key: key, op: root, what: what });
    });
    rows.push(h('div', { class: 'ss-row', 'data-testid': 'debt-ops' }, [h('div', { class: 'ss-k', text: 'Changed by' }),
      ops.length ? h('div', {}, ops.map(function (x) {
        return h('div', { class: 'hold-group' }, [chip(M, x.op.id), h('span', { class: 'muted small', text: '  ' + x.what + ' the recorded debt (' + x.op.kind + ')' })]);
      })) : h('span', { class: 'muted', text: 'No operation changes this record.' })]));
    rows.push(h('div', { class: 'ss-note' }, 'A debt is an obligation of the holder, recorded separately from the assets it received; the two are never netted or added together.'));
    return sec('Debt summary — who owes what to whom', [h('div', { class: 'share-summary' }, rows)], { key: true, id: 'debt-summary' });
  }

  // ---------------------------------------------------------------------------
  // per-type renderers
  // ---------------------------------------------------------------------------
  function renderAsset(M, a) {
    var out = [];
    var positions = M.posByAsset[a.id] || [];
    out.push(header('Asset', a.kind, a.label, a.symbol ? 'symbol ' + a.symbol + ' (display only)' : null, a.status));
    out.push(visibilityNote(M, a.id));
    if (positions.length && positions.every(function (p) { return p.kind === 'debt'; })) out.push(debtSummary(M, a, positions, null));
    else if (SHARE_ASSET[a.kind] || positions.some(function (p) { return SHARE_POS[p.kind]; })) out.push(shareSummary(M, a, positions, null));
    var idn = a.identity || {};
    out.push(sec('Identity', [
      h('div', { class: 'kv' }, [
        h('div', { class: 'k', text: 'identified by' }), h('div', { class: 'v', text: idn.kind }),
        idn.expression ? h('div', { class: 'k', text: 'expression' }) : null, idn.expression ? h('div', { class: 'v mono', text: idn.expression }) : null,
        idn.address ? h('div', { class: 'k', text: 'address' }) : null, idn.address ? h('div', { class: 'v mono', text: idn.address + (idn.chain_id ? ' (chain ' + idn.chain_id + ')' : '') }) : null,
        idn.contract ? h('div', { class: 'k', text: 'contract' }) : null, idn.contract ? h('div', { class: 'v', text: idn.contract }) : null,
        a.standard ? h('div', { class: 'k', text: 'standard' }) : null, a.standard ? h('div', { class: 'v', text: a.standard }) : null,
        a.decimals !== undefined ? h('div', { class: 'k', text: 'decimals' }) : null, a.decimals !== undefined ? h('div', { class: 'v', text: String(a.decimals) }) : null
      ]),
      idn.note ? h('div', { class: 'small muted', text: idn.note }) : null,
      h('div', { class: 'small muted', style: 'margin-top:5px', text: 'Labels and symbols are for display only; they never relate two assets.' })
    ]));
    if (a.description) out.push(sec('Description', [h('div', { class: 'd-text', text: a.description })]));
    if (!(SHARE_ASSET[a.kind])) {
      out.push(sec('Holders', positions.length ? positions.map(function (p) {
        return h('div', { class: 'hold-group' }, [chip(M, p.holder), ' ', h('span', { class: 'mini-chip', text: p.kind }), ' ', p.amount ? amountEl(M, p.amount) : null, ' ', chip(M, p.id, 'position')]);
      }) : [h('div', { class: 'muted small', text: 'No positions in this asset.' })], { count: positions.length, id: 'holders' }));
    }
    var moving = M.flows.filter(function (f) { return f.flow.asset === a.id; });
    if (moving.length) {
      var byOp = U.uniq(moving.map(function (f) { return f.op.id; }));
      out.push(sec('Operations moving this asset', byOp.map(function (oid) {
        var fs = moving.filter(function (f) { return f.op.id === oid; });
        var tags = U.uniq(fs.map(function (f) { return dirKind(f.flow) || 'transfer'; }));
        return h('div', { class: 'hold-group' }, [chip(M, oid), ' ', h('span', { class: 'muted small', text: tags.join(', ') })]);
      }), { count: byOp.length }));
    }
    out.push(relationsSection(M, a.id));
    out.push(statusSection(M, a.status));
    out.push(unresolvedSection(M, [a.id]));
    out.push(evidenceSection(M, a.evidence, 'asset'));
    return out;
  }

  function relationsSection(M, id, exclude) {
    var rels = M.relationsOf(id).filter(function (r) { return !exclude || exclude.indexOf(r.kind) < 0; });
    if (!rels.length) return null;
    return sec('Relations', rels.map(function (r) {
      var outgoing = r.source === id;
      return h('div', { class: 'hold-group' }, [
        h('span', { class: 'mini-chip', text: U.humanize(r.kind) + (outgoing ? ' →' : ' ←') }), ' ',
        chip(M, outgoing ? r.target : r.source), ' ',
        h('span', { class: 'ref small', 'data-ref': r.id, text: r.label || 'details' }),
        h('span', { class: 'badge basis-' + (r.status && r.status.basis), style: 'margin-left:5px', text: r.status ? AL.BASIS_LABEL[r.status.basis] : '' })
      ]);
    }), { count: rels.length, id: 'relations' });
  }

  function renderPosition(M, p) {
    var out = [];
    var a = M.get(p.asset) || { id: p.asset, label: p.asset, kind: 'unknown' };
    out.push(header('Position', p.kind, p.label || M.label(p.id), 'held by ' + M.label(p.holder) + ' · ' + M.label(p.asset), p.status));
    out.push(visibilityNote(M, p.id));
    if (p.kind === 'debt') out.push(debtSummary(M, a, M.posByAsset[p.asset] || [p], p.id));
    else if (SHARE_POS[p.kind] || SHARE_ASSET[a.kind]) out.push(shareSummary(M, a, M.posByAsset[p.asset] || [p], p.id));
    var kv = [
      h('div', { class: 'k', text: 'holder' }), h('div', { class: 'v' }, chip(M, p.holder)),
      h('div', { class: 'k', text: 'asset' }), h('div', { class: 'v' }, chip(M, p.asset)),
      h('div', { class: 'k', text: 'amount' }), h('div', { class: 'v' }, amountEl(M, p.amount))
    ];
    if (p.counterparty) kv.push(h('div', { class: 'k', text: 'owed to' }), h('div', { class: 'v' }, chip(M, p.counterparty)));
    out.push(sec('Position', [h('div', { class: 'kv' }, kv), p.description ? h('div', { class: 'd-text small', style: 'margin-top:6px', text: p.description }) : null]));
    if (p.kind === 'debt') {
      out.push(h('div', { class: 'note-box bad', 'data-testid': 'debt-note' }, 'Obligation, not an asset of the holder: ' + M.label(p.holder) + ' owes this amount' + (p.counterparty ? ' to ' + M.label(p.counterparty) : '') + '. Any borrowed tokens the holder received are a separate custody position and are not netted against this debt.'));
    }
    if (p.kind === 'custody') {
      var claimers = (M.relTo[p.id] || []).filter(function (r) { return r.kind === 'claim'; });
      out.push(sec('Claimed by (backs these shares)', claimers.length ? claimers.map(function (r) {
        var sp = M.get(r.source);
        return h('div', { class: 'hold-group' }, [chip(M, r.source), sp ? h('span', { class: 'muted small' }, [' held by ', chip(M, sp.holder)]) : null, r.basis_detail ? h('div', { class: 'small mono muted', text: r.basis_detail }) : null, evChips(M, r.evidence, 'claim')]);
      }).concat([h('div', { class: 'ss-note', style: 'margin-top:4px', text: 'The holder keeps custody; the shares above are claims on this balance. Count the asset once — here.' })]) :
        [h('div', { class: 'muted small', text: 'No share claims this position.' })], { count: claimers.length, id: 'claimed-by' }));
      var borrowedFrom = M.flows.filter(function (f) { return f.flow.asset === p.asset && f.flow.to === p.holder && f.op.kind === 'borrow'; });
      if (borrowedFrom.length) {
        out.push(h('div', { class: 'note-box warn', 'data-testid': 'borrowed-note' }, ['Received through borrowing (', borrowedFrom.map(function (f) { return chip(M, f.op.id); }), '). The matching obligation is a separate debt position; this balance is not the holder’s equity.']));
      }
    }
    var changes = M.flows.filter(function (f) { return f.flow.asset === p.asset && (f.flow.to === p.holder || f.flow.from === p.holder); });
    if (changes.length) {
      out.push(sec('Operations changing this position', changes.map(function (f) {
        var inc = f.flow.to === p.holder;
        return h('div', { class: 'hold-group' }, [h('span', { class: 'flow-dir ' + (inc ? 'out' : 'in'), text: inc ? '+ ' + (f.flow.from ? 'IN' : 'MINT') : '− ' + (f.flow.to ? 'OUT' : 'BURN') }), ' ', chip(M, f.op.id), ' ', amountEl(M, f.flow.amount)]);
      }), { count: changes.length }));
    }
    out.push(relationsSection(M, p.id, SHARE_POS[p.kind] ? ['claim', 'depends_on'] : null));
    out.push(statusSection(M, p.status));
    out.push(unresolvedSection(M, [p.id, p.asset]));
    out.push(evidenceSection(M, p.evidence, 'position'));
    return out;
  }

  var HOLD_GROUPS = [
    ['custody', 'Assets held (custody)'], ['share', 'Shares / receipts (claims on other positions)'], ['lp', 'LP positions (claims on pool reserves)'],
    ['claim', 'Non-tokenised claims'], ['collateral', 'Collateral'], ['reward', 'Rewards'], ['debt', 'Debts owed (obligations, not assets)']
  ];

  function renderEntity(M, e) {
    var out = [];
    var extra = [e.in_scope === false ? h('span', { class: 'badge scope-out', text: 'outside analysis scope' }) : h('span', { class: 'badge', text: 'in scope' })];
    out.push(header('Entity', e.kind, e.label, e.expression ? 'address expression: ' + e.expression : null, e.status, extra));
    out.push(visibilityNote(M, e.id));
    if (e.description) out.push(sec('Description', [h('div', { class: 'd-text', text: e.description })]));
    var kv = [];
    if (e.contract) {
      kv.push(h('div', { class: 'k', text: 'contract' }), h('div', { class: 'v', text: e.contract.name }));
      if (e.contract.file) kv.push(h('div', { class: 'k', text: 'file' }), h('div', { class: 'v mono' }, M.sources[e.contract.file] ? h('span', { class: 'ref', 'data-file': e.contract.file, text: e.contract.file }) : e.contract.file));
      if (e.contract.address) kv.push(h('div', { class: 'k', text: 'address' }), h('div', { class: 'v mono', text: e.contract.address + (e.contract.chain_id ? ' (chain ' + e.contract.chain_id + ')' : '') }));
    }
    if (e.parent) kv.push(h('div', { class: 'k', text: 'part of' }), h('div', { class: 'v' }, [chip(M, e.parent), ' ', h('button', { class: 'link-btn small', 'data-action': 'toggle-group', 'data-target': e.parent, text: AL.app.state.collapsedGroups[e.parent] ? 'expand group' : 'collapse group' })]));
    var kids = M.entChildren[e.id] || [];
    if (kids.length) kv.push(h('div', { class: 'k', text: 'members' }), h('div', { class: 'v' }, kids.map(function (k) { return chip(M, k.id); }).concat([' ', h('button', { class: 'link-btn small', 'data-action': 'toggle-group', 'data-target': e.id, text: AL.app.state.collapsedGroups[e.id] ? 'expand group' : 'collapse group' })])));
    if (kv.length) out.push(sec('Entity', [h('div', { class: 'kv' }, kv)]));
    if (e.in_scope === false) out.push(h('div', { class: 'note-box warn', text: 'Outside the analysis scope: behaviour attributed to this entity comes from interfaces, standards, configuration or model proposals, not from analysed code.' }));

    var positions = M.posByHolder[e.id] || [];
    var groups = HOLD_GROUPS.map(function (g) {
      var ps = positions.filter(function (p) { return p.kind === g[0]; });
      if (!ps.length) return null;
      return h('div', { class: 'hold-group', 'data-hold-kind': g[0] }, [h('div', { class: 'hg-title', text: g[1] })].concat(ps.map(function (p) {
        return h('div', {}, [chip(M, p.asset, M.assetShort(p.asset)), ' ', amountEl(M, p.amount), ' ', chip(M, p.id, 'position')]);
      })));
    }).filter(Boolean);
    out.push(sec('Holdings', groups.length ? groups.concat([h('div', { class: 'ss-note', text: 'Listed per asset. Amounts in different assets — and shares versus the underlying they claim — are never summed.' })]) : [h('div', { class: 'muted small', text: 'No positions held.' })], { count: positions.length, id: 'holdings' }));

    var initiated = M.c.operation.filter(function (o) { return o.initiator === e.id; });
    var executed = M.c.operation.filter(function (o) { return o.executor === e.id; });
    var roles = [];
    M.c.operation.forEach(function (o) { (o.participants || []).forEach(function (p) { if (p.entity === e.id) roles.push({ op: o, role: p.role, expr: p.expression }); }); });
    if (initiated.length || executed.length || roles.length) {
      var kids2 = [];
      if (initiated.length) kids2.push(h('div', { class: 'hold-group' }, [h('div', { class: 'hg-title', text: 'Calls / triggers' })].concat(initiated.map(function (o) { return chip(M, o.id); }))));
      if (executed.length) kids2.push(h('div', { class: 'hold-group' }, [h('div', { class: 'hg-title', text: 'Executes' })].concat(executed.map(function (o) { return chip(M, o.id); }))));
      if (roles.length) kids2.push(h('div', { class: 'hold-group' }, [h('div', { class: 'hg-title', text: 'Roles in operations' })].concat(roles.map(function (r) {
        return h('div', { class: 'small' }, [h('span', { class: 'mini-chip', text: r.role }), ' in ', chip(M, r.op.id), r.expr ? h('span', { class: 'mono muted', text: '  ' + r.expr }) : null]);
      }))));
      out.push(sec('Operations', kids2, { id: 'operations' }));
    }
    out.push(relationsSection(M, e.id));
    out.push(unresolvedSection(M, [e.id]));
    out.push(evidenceSection(M, e.evidence, 'entity'));
    return out;
  }

  function renderOperation(M, o) {
    var out = [];
    var crumbs = M.opAncestors(o.id);
    var sub = o.entrypoint ? o.entrypoint.contract + '.' + (o.entrypoint.signature || o.entrypoint.function) + (o.entrypoint.visibility ? ' · ' + o.entrypoint.visibility : '') : null;
    out.push(header('Operation', o.kind, o.label, sub, o.status, [o.transaction ? h('span', { class: 'badge', text: o.transaction === 'separate' ? 'separate transaction' : 'same transaction as parent' }) : null]));
    out.push(visibilityNote(M, o.id));
    if (crumbs.length) out.push(sec('Part of', [h('div', {}, crumbs.map(function (c, i) { return h('span', {}, [i ? ' › ' : '', chip(M, c)]); }))]));
    var st = AL.app.state;
    if (st.view === 'code') {
      var root = AL.codeview.rootOf(o.id), pinned = st.code.pins.indexOf(root) >= 0;
      out.push(h('div', { class: 'd-sec', style: 'display:flex; gap:12px; flex-wrap:wrap' }, [
        h('button', { class: 'btn btn-small', 'data-action': 'open-def', 'data-target': o.id, text: 'Show source' }),
        h('button', { class: 'btn btn-small', 'data-action': 'toggle-pin', 'data-target': root, text: pinned ? 'Unpin from graph' : 'Pin to graph' })
      ]));
    } else {
      out.push(h('div', { class: 'd-sec', style: 'display:flex; gap:12px; flex-wrap:wrap' }, [
        h('button', { class: 'btn btn-small', 'data-action': 'only-op', 'data-target': o.id, 'data-testid': 'only-op', text: 'Show only this operation in Flows' }),
        st.opFilter ? h('button', { class: 'link-btn small', 'data-action': 'all-ops', 'data-target': o.id, text: 'Show all operations' }) : null,
        h('button', { class: 'link-btn small', 'data-action': 'open-code', 'data-target': o.id, text: 'Open in Code view' })
      ]));
    }
    if (o.description) out.push(sec('Description', [h('div', { class: 'd-text', text: o.description })]));
    var pk = [];
    if (o.initiator) pk.push(h('div', { class: 'k', text: 'initiator' }), h('div', { class: 'v' }, chip(M, o.initiator)));
    if (o.executor) pk.push(h('div', { class: 'k', text: 'executor' }), h('div', { class: 'v' }, chip(M, o.executor)));
    (o.participants || []).forEach(function (p) {
      pk.push(h('div', { class: 'k', text: U.humanize(p.role) }), h('div', { class: 'v' }, [chip(M, p.entity), p.expression ? h('span', { class: 'mono muted', text: '  ' + p.expression }) : null, p.evidence && p.evidence.length ? evChips(M, p.evidence) : null]));
    });
    if (pk.length) out.push(sec('Participants', [h('div', { class: 'kv' }, pk)], { id: 'participants' }));
    var own = flowsOf(o);
    var nested = M.flowsUnder(o.id).filter(function (f) { return f.op.id !== o.id; });
    var flowKids = own.length ? own.map(function (f) { return flowRow(M, f, o, false); }) : [h('div', { class: 'muted small', text: nested.length ? 'No direct flows; see sub-operations.' : 'No asset flows recorded.' })];
    out.push(sec('Inputs & outputs', flowKids, { count: own.length, id: 'flows' }));
    var children = M.opChildren[o.id] || [];
    if (children.length) {
      out.push(sec('Sub-operations', children.map(function (c) {
        var cf = M.flowsUnder(c.id);
        return h('div', { class: 'item-card' + (c.conditions && c.conditions.some(function (k) { return k.kind === 'branch'; }) ? ' optional' : '') }, [
          h('div', { class: 'ic-head' }, [h('span', { class: 'mini-chip', text: (c.order !== undefined ? c.order + '. ' : '') + U.humanize(c.kind) }), chip(M, c.id)]),
          h('div', { class: 'ic-sub', text: (c.transaction === 'separate' ? 'separate transaction' : 'same transaction') + ' · ' + U.plural(cf.length, 'flow') +
            (c.conditions && c.conditions.length ? ' · only if: ' + c.conditions.filter(function (k) { return k.kind === 'branch'; }).map(function (k) { return k.text; }).join('; ') : '') })
        ]);
      }).concat([h('button', { class: 'link-btn small', 'data-action': 'toggle-op', 'data-target': o.id, text: AL.app.state.expandedOps[o.id] ? 'Collapse in graph' : 'Expand in graph' })]), { count: children.length, id: 'subops' }));
      if (nested.length) out.push(sec('All flows including sub-operations', nested.map(function (f) { return flowRow(M, Object.assign({ __dir: f.dir }, f.flow), f.op, true); }), { count: nested.length }));
    }
    out.push(sec('Conditions', condList(M, o.conditions), { count: (o.conditions || []).length, id: 'conditions' }));
    var exits = M.exitsByOp[o.id] || [];
    if (exits.length) out.push(sec('Step of exit paths', exits.map(function (x) { return exitCard(M, x); }), { count: exits.length }));
    out.push(relationsSection(M, o.id));
    out.push(statusSection(M, o.status));
    out.push(unresolvedSection(M, [o.id]));
    out.push(evidenceSection(M, o.evidence, 'operation'));
    return out;
  }

  function renderRelation(M, r) {
    var out = [];
    out.push(header('Relation', r.kind, r.label || U.humanize(r.kind), null, r.status));
    out.push(visibilityNote(M, r.id));
    var kv = [
      h('div', { class: 'k', text: 'from' }), h('div', { class: 'v' }, chip(M, r.source)),
      h('div', { class: 'k', text: 'to' }), h('div', { class: 'v' }, chip(M, r.target))
    ];
    if (r.asset) kv.push(h('div', { class: 'k', text: 'asset' }), h('div', { class: 'v' }, chip(M, r.asset)));
    if (r.amount) kv.push(h('div', { class: 'k', text: 'amount' }), h('div', { class: 'v' }, amountEl(M, r.amount)));
    if (r.operation) kv.push(h('div', { class: 'k', text: 'operation' }), h('div', { class: 'v' }, chip(M, r.operation)));
    if (r.basis_detail) kv.push(h('div', { class: 'k', text: 'basis' }), h('div', { class: 'v mono', text: r.basis_detail }));
    out.push(sec('Relation', [h('div', { class: 'kv' }, kv), r.description ? h('div', { class: 'd-text small', style: 'margin-top:6px', text: r.description }) : null]));
    if (r.kind === 'claim') out.push(h('div', { class: 'ss-note', style: 'margin:8px 14px' }, 'A claim links a share-like position to the position it can be redeemed against. The claimed position stays with its holder; it is not counted again for the share holder.'));
    if (r.kind === 'debt') out.push(h('div', { class: 'note-box bad' }, 'Debt: the source position is an obligation owed to the target.'));
    out.push(sec('Conditions', condList(M, r.conditions), { count: (r.conditions || []).length }));
    out.push(statusSection(M, r.status));
    out.push(unresolvedSection(M, [r.id]));
    out.push(evidenceSection(M, r.evidence, 'relation'));
    return out;
  }

  function renderExit(M, x) {
    var out = [];
    out.push(header('Exit path', null, x.label, 'exits ' + M.label(x.position), x.status));
    out.push(h('div', { class: 'd-sec' }, [h('button', { class: 'btn btn-small', 'data-action': 'show-exit', 'data-target': x.id, text: 'Highlight exit path in graph' })]));
    out.push(sec('Position', [chip(M, x.position)]));
    var steps = (x.steps || []).slice().sort(function (a, b) { return a.order - b.order; });
    out.push(sec('Steps', steps.map(function (s) {
      return h('div', { class: 'step' + (s.optional ? ' optional' : ''), 'data-step': s.order }, [
        h('div', { class: 'sn', text: String(s.order) }),
        h('div', { class: 'st-body' }, [
          chip(M, s.operation),
          h('div', { class: 'st-meta' }, ['actor: ', s.actor ? chip(M, s.actor) : h('span', { class: 'muted', text: 'not recorded' }),
            s.transaction ? h('span', { class: 'sep-dot', text: s.transaction === 'separate' ? 'separate transaction' : 'same transaction' }) : null,
            s.optional ? h('span', { class: 'sep-dot', text: 'conditional step' }) : null]),
          s.description ? h('div', { class: 'small', style: 'margin-top:3px', text: s.description }) : null,
          s.conditions && s.conditions.length ? h('div', {}, condList(M, s.conditions)) : null,
          evChips(M, s.evidence, 'step')
        ])
      ]);
    }), { count: steps.length, id: 'steps' }));
    var fk = [];
    (x.consumes || []).forEach(function (f) { fk.push(flowRow(M, Object.assign({ __dir: 'in' }, f), null, false)); });
    (x.returns || []).forEach(function (f) { fk.push(flowRow(M, Object.assign({ __dir: 'out' }, f), null, false)); });
    out.push(sec('Consumes / returns', fk.length ? fk : [h('div', { class: 'muted small', text: 'Not recorded.' })]));
    out.push(sec('Known conditions (whole path)', condList(M, x.conditions, 'No path-level conditions recorded; see each step.')));
    out.push(statusSection(M, x.status));
    out.push(unresolvedSection(M, [x.id]));
    out.push(evidenceSection(M, x.evidence, 'exit path'));
    return out;
  }

  function renderUnresolved(M, u) {
    var out = [];
    out.push(header('Unresolved', u.kind, U.truncate(u.text, 90), null, null));
    out.push(sec('Description', [h('div', { class: 'd-text', text: u.text }), u.suggestion ? h('div', { class: 'small muted', style: 'margin-top:5px', text: 'Suggestion: ' + u.suggestion }) : null]));
    out.push(sec('Affects', (u.subjects || []).length ? u.subjects.map(function (s) { return chip(M, s); }) : [h('span', { class: 'muted', text: 'No subjects listed.' })]));
    out.push(evidenceSection(M, u.evidence, 'item'));
    return out;
  }

  function refCard(M, ref) {
    if (ref.type === 'relation') {
      var r = M.get(ref.id);
      return h('div', { class: 'item-card' }, [
        h('div', { class: 'ic-head' }, [h('span', { class: 'mini-chip', text: U.humanize(r.kind) }), chip(M, r.id)]),
        r.basis_detail ? h('div', { class: 'ic-sub mono', text: r.basis_detail }) : null,
        r.amount ? h('div', { class: 'ic-sub' }, amountEl(M, r.amount)) : null,
        r.conditions && r.conditions.length ? h('div', {}, condList(M, r.conditions)) : null,
        badges(r.status), evChips(M, r.evidence, 'relation')
      ]);
    }
    if (ref.type === 'flow') {
      var fr = M.flowById[ref.id];
      return h('div', { class: 'item-card' }, [flowRow(M, Object.assign({ __dir: fr.dir }, fr.flow), fr.op, true)]);
    }
    if (ref.type === 'holder' || ref.type === 'denominated') {
      var p = M.get(ref.id);
      return h('div', { class: 'item-card' }, [
        h('div', { class: 'ic-head' }, [chip(M, p.holder), ref.type === 'holder' ? ' holds ' : ' balance in ', chip(M, ref.type === 'holder' ? p.id : p.asset)]),
        h('div', { class: 'ic-sub' }, amountEl(M, p.amount)),
        h('div', { class: 'ic-sub muted', text: ref.type === 'holder' ? 'Derived from the position’s holder field.' : 'The position is a balance of this asset.' }),
        evChips(M, p.evidence, 'position')
      ]);
    }
    if (ref.type === 'exit' || ref.type === 'exit-return') {
      return exitCard(M, M.get(ref.id));
    }
    if (ref.type === 'contains') {
      var c = M.get(ref.id);
      return h('div', { class: 'item-card' }, [h('div', { class: 'ic-head' }, ['sub-operation ', chip(M, c.id)]), h('div', { class: 'ic-sub', text: (c.transaction === 'separate' ? 'separate transaction' : 'same transaction') }), evChips(M, c.evidence, 'operation')]);
    }
    if (ref.type === 'initiator') {
      var o = M.get(ref.id);
      return h('div', { class: 'item-card' }, [h('div', { class: 'ic-head' }, [chip(M, o.initiator), ' calls ', chip(M, o.id)]), evChips(M, o.evidence, 'operation')]);
    }
    return h('div', { class: 'item-card', text: JSON.stringify(ref) });
  }

  function renderEdge(M, e) {
    var out = [];
    var kind = AL.graph.EDGE_KINDS[e.kind] || { label: e.kind };
    var title = kind.label + (e.asset ? ' · ' + M.label(e.asset) : '');
    out.push(header('Graph edge', e.kind + (e.sub ? ' (' + e.sub + ')' : ''), title, kind.desc, e.status));
    out.push(sec('Between', [h('div', {}, [chip(M, e.source), ' → ', chip(M, e.target)]),
      e.refs.length > 1 ? h('div', { class: 'small muted', style: 'margin-top:4px', text: 'This edge aggregates ' + e.refs.length + ' items (e.g. flows of collapsed sub-operations). Each is listed below with its own amount and asset.' }) : null]));
    if (e.map) {
      // system map: which functions move these assets, each opens in the Code view
      var byOp = Object.create(null), order = [];
      e.refs.forEach(function (r) {
        var root = AL.codeview.rootOf(r.op);
        if (!byOp[root]) { byOp[root] = []; order.push(root); }
        var fr = M.flowById[r.id];
        if (fr) byOp[root].push(M.assetShort(fr.flow.asset));
      });
      out.push(sec('Functions', order.map(function (id) {
        return h('div', { class: 'item-card' }, [h('div', { class: 'ic-head' }, [chip(M, id)]), h('div', { class: 'ic-sub', text: U.uniq(byOp[id]).join(', ') })]);
      }), { count: order.length, id: 'map-functions' }));
    }
    out.push(sec('Underlying items', e.refs.map(function (r) { return refCard(M, r); }), { count: e.refs.length, id: 'refs' }));
    return out;
  }

  function renderMapNode(M, n) {
    var out = [];
    out.push(header('Group', null, n.obj.label, 'Every account and role that pays or receives assets (callers, receivers, owners, privileged roles, members of address sets), shown as one node on the system map. Open a function to see which of them it involves.', null));
    out.push(sec('Members', (n.members || []).map(function (id) { return h('div', {}, chip(M, id)); }), { count: (n.members || []).length }));
    return out;
  }

  function renderOverview(M) {
    var d = M.data, sc = M.scope || {};
    var counts = [['assets', M.c.asset.length], ['entities', M.c.entity.length], ['operations', M.c.operation.length], ['positions', M.c.position.length],
      ['relations', M.c.relation.length], ['exit paths', M.c.exit.length], ['evidence items', M.c.evidence.length], ['source files', (d.sources || []).length], ['unresolved', M.c.unresolved.length]];
    var out = [];
    var codeView = AL.app.state && AL.app.state.view === 'code';
    out.push(h('div', { class: 'd-empty' }, [
      h('h2', { text: M.meta.title || M.meta.id }),
      h('div', { class: 'd-badges', style: 'margin-bottom:8px' }, [h('span', { class: 'mini-chip ' + sc.kind, text: AL.SCOPE_LABEL[sc.kind] || sc.kind }), M.meta.synthetic ? h('span', { class: 'mini-chip example', text: 'synthetic' }) : null]),
      M.meta.description ? h('p', { text: M.meta.description }) : null,
      h('p', { class: 'small', text: AL.SCOPE_EXPLAIN[sc.kind] || '' }),
      h('div', { class: 'kv', style: 'margin:10px 0' }, [].concat.apply([], counts.map(function (c) { return [h('div', { class: 'k', text: c[0] }), h('div', { class: 'v', text: String(c[1]) })]; }))),
      codeView ? h('p', { class: 'small' }, 'Pick a function in the list on the left: its source opens and the graph shows only that function. Hover a marked line to find what it does in the graph, or hover a graph item to find its lines. Pin functions to build the picture up step by step.') :
      h('p', { class: 'small' }, 'Select a node or edge in the graph to see participants, amounts, conditions and evidence. Shares and LP tokens show who holds them, which positions back them, what they depend on and how to exit.'),
      M.c.task.length ? h('p', { class: 'small' }, 'Try one of the user tasks in the left panel.') : null
    ]));
    return out;
  }

  D.render = function (container, sel) {
    var M = AL.app.model;
    U.clear(container);
    if (!M) return;
    var parts;
    if (!sel) parts = renderOverview(M);
    else if (sel.edge) parts = renderEdge(M, sel.edge);
    else {
      var t = M.type(sel.id), x = M.get(sel.id);
      var gn = AL.app.graph && AL.app.graph.byId[sel.id];
      if (!x && gn && gn.synthetic) parts = renderMapNode(M, gn);
      else if (!x) parts = [h('div', { class: 'd-empty', text: 'Item "' + sel.id + '" is not part of this result.' })];
      else if (t === 'asset') parts = renderAsset(M, x);
      else if (t === 'entity') parts = renderEntity(M, x);
      else if (t === 'operation') parts = renderOperation(M, x);
      else if (t === 'position') parts = renderPosition(M, x);
      else if (t === 'relation') parts = renderRelation(M, x);
      else if (t === 'exit') parts = renderExit(M, x);
      else if (t === 'unresolved') parts = renderUnresolved(M, x);
      else parts = [h('div', { class: 'd-empty', text: 'Nothing to show for ' + sel.id })];
    }
    U.append(container, parts.filter(Boolean));
    container.scrollTop = 0;
  };
})();
