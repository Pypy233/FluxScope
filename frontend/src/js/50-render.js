/* FluxScope viewer — SVG rendering, pan/zoom and graph interaction. */
(function () {
  'use strict';
  var AL = window.FluxScope;
  var U = AL.util;
  var s = U.s;

  var FONT = getComputedStyle(document.documentElement).getPropertyValue('--font') || 'sans-serif';
  var LABEL_FONT = '600 12.5px ' + FONT;
  var SUB_FONT = '10.5px ' + FONT;
  var EDGE_FONT = '10.5px ' + FONT;

  var ENTITY_STYLE = {
    account: { fill: '#eaf1ff', stroke: '#3b6fd8', icon: 'A' },
    role: { fill: '#e7f6f3', stroke: '#129a8a', icon: 'R' },
    contract: { fill: '#f2f4f8', stroke: '#4a5568', icon: 'C' },
    protocol: { fill: '#f3eefe', stroke: '#7c4ddb', icon: 'P' },
    external_contract: { fill: '#fafafa', stroke: '#8b95a5', icon: 'X', dash: '5 3' },
    unknown: { fill: '#ffffff', stroke: '#a3abb9', icon: '?', dash: '2 3' }
  };
  var POSITION_COLOR = { share: '#7c3aed', custody: '#15803d', lp: '#4f46e5', debt: '#d6334a', collateral: '#a16207', claim: '#c2418a', reward: '#ca8a04' };
  var OP_GLYPH = {
    transfer: '→', swap: '⇄', deposit: '↓', withdraw: '↑', mint: '+', burn: '−', redeem: '↺', borrow: '⤓', repay: '⤒',
    add_liquidity: '⊕', remove_liquidity: '⊖', stake: '▣', unstake: '▢', allocate: '⇉', deallocate: '⇇', harvest: '❋',
    claim: '✓', approve: '✎', liquidate: '⚡', fee: '%', call: '⋯', other: '•'
  };
  var VER_COLOR = { supported: '#15803d', partially_supported: '#b7950b', pending: '#7c4ddb', contradicted: '#c62828', unresolved: '#d97706' };
  var MINT_COLOR = '#16a34a', BURN_COLOR = '#dc2626';

  var R = AL.render = {};
  var svg, viewport, gEdges, gLabels, gNodes, handlers;
  var view = { x: 40, y: 40, k: 1 };
  var current = null; // {graph, layout, nodeEls, edgeEls, labelEls}
  var orientation = 'LR';

  function fitScale(lay, rect) {
    return Math.min((rect.width - 80) / Math.max(lay.width, 1), (rect.height - 80) / Math.max(lay.height, 1));
  }

  R.init = function (svgEl, h) {
    svg = svgEl;
    handlers = h;
    U.clear(svg);
    var defs = s('defs');
    Object.keys(AL.graph.EDGE_KINDS).forEach(function (k) {
      var m = AL.graph.EDGE_KINDS[k];
      defs.appendChild(marker('m-' + k, m.marker, m.color));
    });
    defs.appendChild(marker('m-mint', 'arrow', MINT_COLOR));
    defs.appendChild(marker('m-burn', 'arrow', BURN_COLOR));
    defs.appendChild(marker('m-sel', 'arrow', '#1d4ed8'));
    svg.appendChild(defs);
    viewport = s('g', { class: 'viewport' });
    gEdges = s('g', { class: 'edges' });
    gLabels = s('g', { class: 'edge-labels' });
    gNodes = s('g', { class: 'nodes' });
    viewport.appendChild(gEdges);
    viewport.appendChild(gNodes);
    viewport.appendChild(gLabels);
    svg.appendChild(viewport);
    installPanZoom();
  };

  function marker(id, kind, color) {
    var m = s('marker', { id: id, viewBox: '0 0 12 12', refX: kind === 'dot' ? 6 : 10.5, refY: 6, markerWidth: 11, markerHeight: 11, markerUnits: 'userSpaceOnUse', orient: 'auto-start-reverse' });
    if (kind === 'arrow') m.appendChild(s('path', { d: 'M1,1.5 L11,6 L1,10.5 Z', fill: color }));
    else if (kind === 'open') m.appendChild(s('path', { d: 'M1.5,1.5 L10.5,6 L1.5,10.5', fill: 'none', stroke: color, 'stroke-width': 1.6 }));
    else if (kind === 'diamond') m.appendChild(s('path', { d: 'M0.8,6 L6,1.6 L11.2,6 L6,10.4 Z', fill: '#fff', stroke: color, 'stroke-width': 1.5 }));
    else if (kind === 'dot') m.appendChild(s('circle', { cx: 6, cy: 6, r: 3.6, fill: color }));
    return m;
  }

  // -------------------------------------------------------------------------
  // sizing
  // -------------------------------------------------------------------------
  function sizeNode(n) {
    var padL = (n.type === 'entity' || n.type === 'operation') ? 38 : (n.type === 'position' ? 18 : 16);
    var padR = 16;
    var maxText = 230 - padL - padR;
    var lw = U.textWidth(n.label || '', LABEL_FONT), sw = U.textWidth(n.sub || '', SUB_FONT);
    var tw = Math.min(maxText, Math.max(lw, sw));
    n.w = Math.max(n.type === 'asset' ? 110 : 132, Math.ceil(tw + padL + padR));
    n.h = n.type === 'asset' ? 40 : 46;
    n.padL = padL;
    n.labelText = U.fitText(n.label || '', LABEL_FONT, n.w - padL - padR);
    n.subText = U.fitText(n.sub || '', SUB_FONT, n.w - padL - padR);
  }

  // -------------------------------------------------------------------------
  // drawing
  // -------------------------------------------------------------------------
  R.draw = function (graph, opts) {
    opts = opts || {};
    var anchor = null;
    if (opts.anchor && current && current.layout.pos[opts.anchor]) {
      var p0 = current.layout.pos[opts.anchor];
      anchor = { id: opts.anchor, sx: p0.x * view.k + view.x, sy: p0.y * view.k + view.y };
    }
    graph.nodes.forEach(sizeNode);
    var lnodes = graph.nodes.map(function (n) {
      // value usually originates at accounts, then roles: start cycle breaking there so the story reads in order
      var prio = n.type === 'entity' ? ({ account: 0, role: 1 }[n.obj.kind] !== undefined ? { account: 0, role: 1 }[n.obj.kind] : 2) : 3;
      return { id: n.id, w: n.w, h: n.h + (n.groupTag ? 4 : 0), prio: prio };
    });
    var ledges = graph.edges.map(function (e) { return { id: e.id, source: e.source, target: e.target }; });
    var labelMode = opts.labels === undefined ? 'auto' : opts.labels;
    var labels = labelMode !== 'none';
    var dir = opts.dir || 'auto';
    var lay;
    if (dir === 'auto') {
      // pick the orientation that lets the graph be drawn largest in the current canvas
      var lr = AL.layout(lnodes, ledges, { dir: 'LR', layerGap: labels ? 130 : 80 });
      var tb = AL.layout(lnodes, ledges, { dir: 'TB', layerGap: labels ? 74 : 48, nodeGap: 30 });
      var rect = svg.getBoundingClientRect();
      var kl = fitScale(lr, rect), kt = fitScale(tb, rect);
      lay = kt > kl * 1.12 ? tb : lr;
    } else {
      lay = AL.layout(lnodes, ledges, dir === 'TB' ? { dir: 'TB', layerGap: labels ? 74 : 48, nodeGap: 30 } : { dir: 'LR', layerGap: labels ? 130 : 80 });
    }
    orientation = lay.dir;
    U.clear(gEdges); U.clear(gLabels); U.clear(gNodes);
    var nodeEls = Object.create(null), edgeEls = Object.create(null), labelEls = Object.create(null);

    graph.edges.forEach(function (e) {
      var pts = lay.routes[e.id];
      if (!pts) return;
      var el = drawEdge(e, pts);
      gEdges.appendChild(el);
      edgeEls[e.id] = el;
    });
    // labels avoid nodes and each other
    var labelBoxes = graph.nodes.filter(function (n) { return lay.pos[n.id]; }).map(function (n) {
      var p = lay.pos[n.id];
      return { x: p.x - n.w / 2 - 2, y: p.y - n.h / 2 - 14, w: n.w + 4, h: n.h + 24 };
    });
    // dense graphs: keep labels but only show those of highlighted edges
    svg.classList.toggle('dense-labels', labelMode === 'auto' && graph.edges.length > 45);
    if (labels) {
      graph.edges.forEach(function (e) {
        if (!e.label || !lay.routes[e.id]) return;
        var lab = drawEdgeLabel(e, lay.routes[e.id], labelBoxes);
        gLabels.appendChild(lab);
        labelEls[e.id] = lab;
      });
    }
    graph.nodes.forEach(function (n) {
      var p = lay.pos[n.id];
      if (!p) return;
      var el = drawNode(n, p);
      gNodes.appendChild(el);
      nodeEls[n.id] = el;
    });
    current = { graph: graph, layout: lay, nodeEls: nodeEls, edgeEls: edgeEls, labelEls: labelEls };
    if (anchor && lay.pos[anchor.id]) {
      var p1 = lay.pos[anchor.id];
      view.x = anchor.sx - p1.x * view.k;
      view.y = anchor.sy - p1.y * view.k;
      applyView();
    } else if (opts.fit !== false) {
      R.fit();
    } else applyView();
  };

  function drawNode(n, p) {
    var g = s('g', { class: 'g-node t-' + n.type, 'data-id': n.id, 'data-type': n.type, transform: 'translate(' + (p.x - n.w / 2) + ',' + (p.y - n.h / 2) + ')' });
    var w = n.w, h = n.h, x = n.obj, st = n.status;
    var dash = st && st.basis === 'static' ? '5 3' : null;
    g.appendChild(s('rect', { class: 'sel-ring', x: -5, y: -5, width: w + 10, height: h + 10, rx: 12 }));
    if (n.type === 'entity') {
      var es = ENTITY_STYLE[x.kind] || ENTITY_STYLE.unknown;
      if (n.group) {
        g.appendChild(s('rect', { class: 'shape', x: 6, y: -6, width: w, height: h, rx: 10, fill: es.fill, stroke: es.stroke, opacity: 0.55 }));
        g.appendChild(s('rect', { class: 'shape', x: 3, y: -3, width: w, height: h, rx: 10, fill: es.fill, stroke: es.stroke, opacity: 0.8 }));
      }
      g.appendChild(s('rect', { class: 'shape', x: 0, y: 0, width: w, height: h, rx: 10, fill: es.fill, stroke: es.stroke, 'stroke-dasharray': es.dash || null }));
      g.appendChild(s('circle', { cx: 19, cy: h / 2, r: 11, fill: es.stroke }));
      g.appendChild(s('text', { x: 19, y: h / 2 + 4, 'text-anchor': 'middle', fill: '#fff', 'font-size': 11, 'font-weight': 700, text: es.icon }));
      if (x.in_scope === false) {
        var tg = s('g', { transform: 'translate(' + (w - 82) + ',-9)' });
        tg.appendChild(s('rect', { width: 78, height: 15, rx: 3, fill: '#fff', stroke: '#8b95a5', 'stroke-dasharray': '3 2' }));
        tg.appendChild(s('text', { class: 'tag', x: 39, y: 11, 'text-anchor': 'middle', fill: '#5b6576', text: 'OUT OF SCOPE' }));
        g.appendChild(tg);
      }
    } else if (n.type === 'operation') {
      g.appendChild(s('rect', { class: 'shape', x: 0, y: 0, width: w, height: h, rx: 14, fill: '#fff8ea', stroke: '#d98a1a', 'stroke-dasharray': dash }));
      g.appendChild(s('circle', { cx: 19, cy: h / 2, r: 11, fill: '#d98a1a' }));
      g.appendChild(s('text', { x: 19, y: h / 2 + 4.5, 'text-anchor': 'middle', fill: '#fff', 'font-size': 13, 'font-weight': 700, text: OP_GLYPH[x.kind] || '•' }));
    } else if (n.type === 'position') {
      var pc = POSITION_COLOR[x.kind] || '#64748b';
      g.appendChild(s('rect', { class: 'shape', x: 0, y: 0, width: w, height: h, rx: 5, fill: '#ffffff', stroke: pc, 'stroke-dasharray': dash }));
      g.appendChild(s('rect', { x: 0.8, y: 0.8, width: 7, height: h - 1.6, rx: 4, fill: pc }));
      if (x.kind === 'debt') g.appendChild(s('text', { class: 'tag', x: w - 8, y: 12, 'text-anchor': 'end', fill: pc, text: 'OWED' }));
    } else if (n.type === 'asset') {
      var share = x.kind === 'share' || x.kind === 'lp_token';
      var col = share ? '#7c3aed' : (x.kind === 'debt_token' ? '#d6334a' : '#64748b');
      var d = 'M10,0 H' + (w - 6) + ' Q' + w + ',0 ' + w + ',6 V' + (h - 6) + ' Q' + w + ',' + h + ' ' + (w - 6) + ',' + h + ' H10 L0,' + (h / 2) + ' Z';
      g.appendChild(s('path', { class: 'shape', d: d, fill: share ? '#f6f1ff' : '#f6f8fb', stroke: col, 'stroke-dasharray': dash }));
    }
    var ty = n.subText ? h / 2 - 3 : h / 2 + 4;
    g.appendChild(s('text', { class: 'label', x: n.padL, y: ty, text: n.labelText }));
    if (n.subText) g.appendChild(s('text', { class: 'sub', x: n.padL, y: h / 2 + 12, text: n.subText }));
    if (st && st.verification) {
      var vc = VER_COLOR[st.verification] || '#64748b';
      var badge = s('g', { class: 'ver-badge', transform: 'translate(' + (w - 1) + ',1)' });
      badge.appendChild(s('circle', { r: 7.5, fill: '#fff', stroke: vc, 'stroke-width': 1.5 }));
      badge.appendChild(s('text', { y: 3.6, 'text-anchor': 'middle', 'font-size': 10, 'font-weight': 700, fill: vc, text: AL.VERIFICATION_GLYPH[st.verification] || '' }));
      g.appendChild(badge);
    }
    if (n.groupTag) {
      var gl = AL.app && AL.app.model ? AL.app.model.label(n.groupTag) : n.groupTag;
      var room = (x.in_scope === false ? w - 90 : w) - 4;
      var gt = s('text', { class: 'group-tag', x: 2, y: -6, 'font-size': 10, fill: '#7c4ddb', 'data-group': n.groupTag, style: 'cursor:pointer', text: U.fitText('▾ ' + gl, '10px ' + FONT, Math.max(40, room)) });
      gt.addEventListener('click', function (ev) { ev.stopPropagation(); handlers.onGroupTag(n.groupTag); });
      gt.appendChild(s('title', { text: 'Part of group "' + gl + '" — click to collapse the group' }));
      g.appendChild(gt);
    }
    n.handles.forEach(function (hd, i) {
      var hg = s('g', { class: 'g-handle', 'data-action': hd.action, transform: 'translate(' + (w / 2 + i * 28 - (n.handles.length - 1) * 14) + ',' + h + ')' });
      var tw = Math.max(18, U.textWidth(hd.text, '700 10px ' + FONT) + 10);
      hg.appendChild(s('rect', { x: -tw / 2, y: -8, width: tw, height: 16, rx: 8, fill: '#fff', stroke: '#2f6fed', 'stroke-width': 1.5, class: 'hbg' }));
      hg.appendChild(s('text', { y: 3.6, 'text-anchor': 'middle', text: hd.text }));
      hg.appendChild(s('title', { text: hd.title }));
      hg.addEventListener('click', function (ev) { ev.stopPropagation(); handlers.onHandle(hd); });
      g.appendChild(hg);
    });
    g.addEventListener('click', function (ev) { ev.stopPropagation(); handlers.onSelectNode(n.id); });
    g.addEventListener('mouseenter', function (ev) { showTip(ev, n.label, (n.sub || '') + (n.synthetic ? '' : '\n' + n.id)); hover({ node: n.id }); });
    g.addEventListener('mousemove', moveTip);
    g.addEventListener('mouseleave', function () { hideTip(); hover(null); });
    return g;
  }

  function pathD(pts) {
    var d = 'M' + pts[0].x.toFixed(1) + ',' + pts[0].y.toFixed(1);
    var tb = orientation === 'TB';
    for (var i = 0; i + 1 < pts.length; i++) {
      var a = pts[i], b = pts[i + 1];
      if (tb) {
        var dy = (b.y - a.y) / 2;
        if (Math.abs(dy) < 24) dy = dy < 0 ? -24 : 24;
        d += ' C' + a.x.toFixed(1) + ',' + (a.y + dy).toFixed(1) + ' ' + b.x.toFixed(1) + ',' + (b.y - dy).toFixed(1) + ' ' + b.x.toFixed(1) + ',' + b.y.toFixed(1);
      } else {
        var dx = (b.x - a.x) / 2;
        if (Math.abs(dx) < 30) dx = dx < 0 ? -30 : 30;
        d += ' C' + (a.x + dx).toFixed(1) + ',' + a.y.toFixed(1) + ' ' + (b.x - dx).toFixed(1) + ',' + b.y.toFixed(1) + ' ' + b.x.toFixed(1) + ',' + b.y.toFixed(1);
      }
    }
    return d;
  }

  function edgeColor(e) {
    if (e.kind === 'flow' && e.sub === 'mint') return MINT_COLOR;
    if (e.kind === 'flow' && e.sub === 'burn') return BURN_COLOR;
    return (AL.graph.EDGE_KINDS[e.kind] || {}).color || '#64748b';
  }
  function edgeMarker(e) {
    if (e.kind === 'flow' && e.sub === 'mint') return 'url(#m-mint)';
    if (e.kind === 'flow' && e.sub === 'burn') return 'url(#m-burn)';
    var mk = (AL.graph.EDGE_KINDS[e.kind] || {}).marker;
    return mk && mk !== 'none' ? 'url(#m-' + e.kind + ')' : null;
  }

  function drawEdge(e, pts) {
    var g = s('g', { class: 'g-edge k-' + e.kind, 'data-id': e.id, 'data-kind': e.kind, 'data-source': e.source, 'data-target': e.target });
    var d = pathD(pts);
    var dash = e.status && e.status.basis === 'static' ? '6 4' : null;
    if (e.kind === 'binding' || e.kind === 'calls' || e.kind === 'initiates' || e.kind === 'contains') dash = '2 4';
    if (e.kind === 'denominated') dash = '1 3';
    g.appendChild(s('path', { class: 'hit', d: d }));
    g.appendChild(s('path', { class: 'line', d: d, stroke: edgeColor(e), 'stroke-dasharray': dash, 'marker-end': !e.dirs || e.dirs.fwd ? edgeMarker(e) : null, 'marker-start': e.dirs && e.dirs.back ? edgeMarker(e) : null }));
    g.addEventListener('click', function (ev) { ev.stopPropagation(); handlers.onSelectEdge(e.id); });
    g.addEventListener('mouseenter', function (ev) {
      showTip(ev, (AL.graph.EDGE_KINDS[e.kind] || {}).label + (e.label ? ': ' + e.label : ''), U.plural(e.refs.length, 'underlying item') + (e.status ? ' · ' + (AL.BASIS_LABEL[e.status.basis] || e.status.basis) : ''));
      hover({ edge: e });
    });
    g.addEventListener('mousemove', moveTip);
    g.addEventListener('mouseleave', function () { hideTip(); hover(null); });
    return g;
  }

  function midpoint(pts) {
    // bezier through every route point with horizontal tangents: the middle point / middle segment midpoint
    var k = pts.length - 1;
    if (k % 2 === 0) return pts[k / 2];
    var i = (k - 1) / 2;
    return { x: (pts[i].x + pts[i + 1].x) / 2, y: (pts[i].y + pts[i + 1].y) / 2 };
  }

  function drawEdgeLabel(e, pts, boxes) {
    var text = U.fitText(e.label, EDGE_FONT, 150);
    var tw = U.textWidth(text, EDGE_FONT) + 10;
    var m = midpoint(pts);
    var base = { x: m.x - tw / 2, y: m.y - 8, w: tw, h: 16 };
    function free(b) { return !boxes.some(function (o) { return !(b.x + b.w < o.x || o.x + o.w < b.x || b.y + b.h < o.y || o.y + o.h < b.y); }); }
    var box = base;
    // candidate offsets around the edge midpoint: vertical first, then sideways
    var cands = [[0, 0]];
    for (var r = 1; r <= 5; r++) cands.push([0, 17 * r], [0, -17 * r], [tw * 0.6 * r, 0], [-tw * 0.6 * r, 0]);
    for (var i = 0; i < cands.length; i++) {
      var b = { x: base.x + cands[i][0], y: base.y + cands[i][1], w: base.w, h: base.h };
      if (free(b)) { box = b; break; }
    }
    boxes.push(box);
    var g = s('g', { class: 'g-elabel', 'data-id': e.id, transform: 'translate(' + box.x.toFixed(1) + ',' + box.y.toFixed(1) + ')' });
    g.appendChild(s('rect', { width: box.w, height: box.h, rx: 4 }));
    var col = edgeColor(e);
    g.appendChild(s('text', { x: 5, y: 11.5, fill: col === '#cbd5e1' ? '#64748b' : null, text: text }));
    g.appendChild(s('title', { text: e.label }));
    g.addEventListener('click', function (ev) { ev.stopPropagation(); handlers.onSelectEdge(e.id); });
    return g;
  }

  // -------------------------------------------------------------------------
  // highlight / selection
  // -------------------------------------------------------------------------
  /** h: {selNode, selEdge, nodes:Set-like object, edges:object, matches:object} — null sets mean no dimming */
  R.highlight = function (h) {
    if (!current) return;
    var dimming = !!(h && h.nodes);
    Object.keys(current.nodeEls).forEach(function (id) {
      var el = current.nodeEls[id];
      el.classList.toggle('selected', !!(h && h.selNode === id));
      el.classList.toggle('dim', dimming && !h.nodes[id]);
      el.classList.toggle('match', !!(h && h.matches && h.matches[id]));
    });
    Object.keys(current.edgeEls).forEach(function (id) {
      var el = current.edgeEls[id], lab = current.labelEls[id];
      var sel = !!(h && h.selEdge === id), hl = dimming && !!h.edges[id];
      el.classList.toggle('selected', sel);
      el.classList.toggle('hl', hl);
      el.classList.toggle('dim', dimming && !h.edges[id] && !sel);
      if (lab) {
        lab.classList.toggle('show', sel || hl);
        lab.classList.toggle('selected', sel);
        lab.classList.toggle('dim', dimming && !h.edges[id] && !sel);
      }
    });
  };

  R.current = function () { return current; };

  function hover(target) { if (handlers.onHover) handlers.onHover(target); }

  /** Emphasise graph items without changing the selection highlight (used when hovering source lines). */
  R.flash = function (nodeIds, edgeIds) {
    if (!current) return;
    var nset = Object.create(null), eset = Object.create(null);
    (nodeIds || []).forEach(function (id) { nset[id] = true; });
    (edgeIds || []).forEach(function (id) { eset[id] = true; });
    Object.keys(current.nodeEls).forEach(function (id) { current.nodeEls[id].classList.toggle('flash', !!nset[id]); });
    Object.keys(current.edgeEls).forEach(function (id) {
      current.edgeEls[id].classList.toggle('flash', !!eset[id]);
      if (current.labelEls[id]) current.labelEls[id].classList.toggle('flash', !!eset[id]);
    });
  };

  // -------------------------------------------------------------------------
  // pan / zoom
  // -------------------------------------------------------------------------
  function applyView() {
    viewport.setAttribute('transform', 'translate(' + view.x.toFixed(1) + ',' + view.y.toFixed(1) + ') scale(' + view.k.toFixed(4) + ')');
  }

  /** Fit the graph. By default keep text readable (min scale) and show the start of the story;
   *  {all:true} fits everything regardless of scale. */
  R.fit = function (o) {
    if (!current) return;
    o = o || {};
    var rect = svg.getBoundingClientRect();
    var bb = bounds();
    if (!bb) return;
    var pad = 40;
    var k = Math.min((rect.width - 2 * pad) / Math.max(bb.w, 1), (rect.height - 2 * pad) / Math.max(bb.h, 1), 1.25);
    var minK = o.all ? 0.08 : 0.62;
    if (k >= minK) {
      view.k = k;
      view.x = (rect.width - bb.w * k) / 2 - bb.x * k;
      view.y = (rect.height - bb.h * k) / 2 - bb.y * k;
    } else {
      view.k = minK;
      var tb = orientation === 'TB';
      // align the beginning of the layout (left or top) and centre the other axis when it fits
      view.x = (tb && bb.w * minK < rect.width - 2 * pad) ? (rect.width - bb.w * minK) / 2 - bb.x * minK : pad - bb.x * minK;
      view.y = (!tb && bb.h * minK < rect.height - 2 * pad) ? (rect.height - bb.h * minK) / 2 - bb.y * minK : pad - bb.y * minK;
    }
    applyView();
  };

  R.orientation = function () { return orientation; };

  function bounds() {
    var lay = current.layout, g = current.graph;
    var x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    g.nodes.forEach(function (n) {
      var p = lay.pos[n.id];
      if (!p) return;
      x0 = Math.min(x0, p.x - n.w / 2); x1 = Math.max(x1, p.x + n.w / 2);
      y0 = Math.min(y0, p.y - n.h / 2 - 12); y1 = Math.max(y1, p.y + n.h / 2 + 12);
    });
    if (x0 === Infinity) return null;
    return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  }

  R.zoomBy = function (f, cx, cy) {
    var rect = svg.getBoundingClientRect();
    if (cx === undefined) { cx = rect.width / 2; cy = rect.height / 2; }
    var k2 = Math.max(0.08, Math.min(3.5, view.k * f));
    view.x = cx - (cx - view.x) * (k2 / view.k);
    view.y = cy - (cy - view.y) * (k2 / view.k);
    view.k = k2;
    applyView();
  };

  R.centerOn = function (id) {
    if (!current || !current.layout.pos[id]) return;
    var rect = svg.getBoundingClientRect();
    var p = current.layout.pos[id];
    var sx = p.x * view.k + view.x, sy = p.y * view.k + view.y;
    if (sx > 60 && sx < rect.width - 60 && sy > 40 && sy < rect.height - 40) return; // already visible
    view.x = rect.width / 2 - p.x * view.k;
    view.y = rect.height / 2 - p.y * view.k;
    applyView();
  };

  R.getView = function () { return { x: view.x, y: view.y, k: view.k }; };

  function installPanZoom() {
    var dragging = null;
    svg.addEventListener('wheel', function (ev) {
      ev.preventDefault();
      var rect = svg.getBoundingClientRect();
      var f = Math.exp(-ev.deltaY * (ev.ctrlKey ? 0.01 : 0.0018));
      R.zoomBy(f, ev.clientX - rect.left, ev.clientY - rect.top);
    }, { passive: false });
    svg.addEventListener('mousedown', function (ev) {
      if (ev.button !== 0) return;
      if (ev.target.closest && ev.target.closest('.g-node, .g-edge, .g-elabel')) return;
      dragging = { x: ev.clientX, y: ev.clientY, vx: view.x, vy: view.y, moved: false };
      svg.classList.add('panning');
    });
    window.addEventListener('mousemove', function (ev) {
      if (!dragging) return;
      var dx = ev.clientX - dragging.x, dy = ev.clientY - dragging.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) dragging.moved = true;
      view.x = dragging.vx + dx;
      view.y = dragging.vy + dy;
      applyView();
    });
    window.addEventListener('mouseup', function () {
      if (dragging) {
        svg.classList.remove('panning');
        var moved = dragging.moved;
        dragging = null;
        if (moved) { svg.__suppressClick = true; setTimeout(function () { svg.__suppressClick = false; }, 0); }
      }
    });
    svg.addEventListener('click', function (ev) {
      if (svg.__suppressClick) return;
      if (ev.target === svg || (ev.target.closest && !ev.target.closest('.g-node, .g-edge, .g-elabel'))) handlers.onBackground();
    });
  }

  // tooltip
  var tip = null;
  function showTip(ev, title, sub) {
    tip = tip || document.getElementById('tooltip');
    if (!tip) return;
    tip.innerHTML = '<div>' + U.esc(title) + '</div>' + (sub ? '<div class="tt-sub">' + U.esc(sub).replace(/\n/g, '<br>') + '</div>' : '');
    tip.hidden = false;
    moveTip(ev);
  }
  function moveTip(ev) {
    if (!tip || tip.hidden) return;
    var wrap = tip.parentElement.getBoundingClientRect();
    var x = ev.clientX - wrap.left + 14, y = ev.clientY - wrap.top + 14;
    if (x + 300 > wrap.width) x = ev.clientX - wrap.left - 300;
    tip.style.left = x + 'px';
    tip.style.top = y + 'px';
  }
  function hideTip() { if (tip) tip.hidden = true; }
  R.hideTip = hideTip;

  R.styles = { ENTITY_STYLE: ENTITY_STYLE, POSITION_COLOR: POSITION_COLOR, OP_GLYPH: OP_GLYPH, VER_COLOR: VER_COLOR, MINT_COLOR: MINT_COLOR, BURN_COLOR: BURN_COLOR, edgeColor: edgeColor };
})();
