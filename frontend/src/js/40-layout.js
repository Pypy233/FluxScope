/* FluxScope viewer — layered (Sugiyama-style) layout, left→right or top→down.
 * cycle breaking (DFS) → longest-path layering with source pull → dummy nodes →
 * barycenter crossing reduction → isotonic-regression vertical placement →
 * port distribution. Disconnected components are stacked vertically. */
(function () {
  'use strict';
  var AL = window.FluxScope;

  /**
   * nodes: [{id, w, h, prio}], edges: [{id, source, target}]
   * opt.dir: 'LR' (default) or 'TB'. The core works left→right; for 'TB' node
   * sizes are swapped before and coordinates swapped after.
   * Returns {pos:{id:{x,y}}, routes:{edgeId:[{x,y}]}, width, height, dir}.
   */
  function layout(nodes, edges, opt) {
    opt = Object.assign({ dir: 'LR', layerGap: 150, nodeGap: 26, compGap: 70, dummyH: 10, iterations: 24 }, opt || {});
    var tb = opt.dir === 'TB';
    var N = Object.create(null);
    nodes.forEach(function (n, i) {
      N[n.id] = { id: n.id, w: tb ? n.h : n.w, h: tb ? n.w : n.h, rw: n.w, rh: n.h, idx: i, prio: n.prio || 0 };
    });
    var E = edges.filter(function (e) { return N[e.source] && N[e.target] && e.source !== e.target; });

    // connected components (undirected)
    var parent = Object.create(null);
    function find(x) { while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; } return x; }
    nodes.forEach(function (n) { parent[n.id] = n.id; });
    E.forEach(function (e) { var a = find(e.source), b = find(e.target); if (a !== b) parent[a] = b; });
    var comps = Object.create(null), compOrder = [];
    nodes.forEach(function (n) {
      var r = find(n.id);
      if (!comps[r]) { comps[r] = { nodes: [], edges: [] }; compOrder.push(r); }
      comps[r].nodes.push(N[n.id]);
    });
    E.forEach(function (e) { comps[find(e.source)].edges.push(e); });
    var list = compOrder.map(function (k) { return comps[k]; });
    var multi = list.filter(function (c) { return c.nodes.length > 1; });
    var singles = list.filter(function (c) { return c.nodes.length === 1; }).map(function (c) { return c.nodes[0]; });
    multi.sort(function (a, b) { return b.nodes.length - a.nodes.length || a.nodes[0].idx - b.nodes[0].idx; });

    var pos = Object.create(null), routes = Object.create(null);
    var y0 = 0, maxW = 0;
    multi.forEach(function (c) {
      var r = layoutComponent(c.nodes, c.edges, opt);
      Object.keys(r.pos).forEach(function (id) { pos[id] = { x: r.pos[id].x, y: r.pos[id].y + y0 - r.minY }; });
      Object.keys(r.routes).forEach(function (id) {
        routes[id] = r.routes[id].map(function (p) { return { x: p.x, y: p.y + y0 - r.minY }; });
      });
      y0 += (r.maxY - r.minY) + opt.compGap;
      maxW = Math.max(maxW, r.width);
    });
    if (multi.length) y0 -= opt.compGap;
    var W = maxW, H = y0;
    if (tb) {
      Object.keys(pos).forEach(function (id) { var p = pos[id]; pos[id] = { x: p.y, y: p.x }; });
      Object.keys(routes).forEach(function (id) { routes[id] = routes[id].map(function (p) { return { x: p.y, y: p.x }; }); });
      W = y0; H = maxW;
    }
    // isolated nodes: rows below the connected part
    if (singles.length) {
      var rowW = Math.max(W, 900), x = 0, rowH = 0, y = H + (multi.length ? opt.compGap : 0);
      singles.forEach(function (n) {
        if (x > 0 && x + n.rw > rowW) { x = 0; y += rowH + opt.nodeGap; rowH = 0; }
        pos[n.id] = { x: x + n.rw / 2, y: y + n.rh / 2 };
        x += n.rw + 36;
        rowH = Math.max(rowH, n.rh);
        W = Math.max(W, x);
      });
      H = y + rowH;
    }
    return { pos: pos, routes: routes, width: W, height: H, dir: tb ? 'TB' : 'LR' };
  }

  function layoutComponent(nodes, edges, opt) {
    var ids = nodes.map(function (n) { return n.id; });
    var N = Object.create(null);
    nodes.forEach(function (n) { N[n.id] = n; });
    var out = Object.create(null), inn = Object.create(null);
    ids.forEach(function (id) { out[id] = []; inn[id] = []; });
    edges.forEach(function (e) { out[e.source].push(e); inn[e.target].push(e); });

    // 1. cycle breaking: DFS, reverse back edges
    var reversed = Object.create(null), state = Object.create(null);
    // DFS roots: preferred origins first (e.g. user accounts), then sources, then input order
    var startOrder = ids.slice().sort(function (a, b) {
      return (N[a].prio - N[b].prio) || (inn[a].length - inn[b].length) || (N[a].idx - N[b].idx);
    });
    function dfs(v) {
      state[v] = 1;
      out[v].forEach(function (e) {
        var t = e.target;
        if (state[t] === 1) reversed[e.id] = true;
        else if (!state[t]) dfs(t);
      });
      state[v] = 2;
    }
    startOrder.forEach(function (v) { if (!state[v]) dfs(v); });
    var dag = edges.map(function (e) {
      return reversed[e.id] ? { id: e.id, s: e.target, t: e.source, rev: true } : { id: e.id, s: e.source, t: e.target, rev: false };
    });
    var dOut = Object.create(null), dIn = Object.create(null);
    ids.forEach(function (id) { dOut[id] = []; dIn[id] = []; });
    dag.forEach(function (e) { dOut[e.s].push(e); dIn[e.t].push(e); });

    // 2. layering: longest path, then pull sources right
    var layer = Object.create(null), indeg = Object.create(null), q = [];
    ids.forEach(function (id) { indeg[id] = dIn[id].length; if (!indeg[id]) { layer[id] = 0; q.push(id); } });
    for (var qi = 0; qi < q.length; qi++) {
      var u = q[qi];
      dOut[u].forEach(function (e) {
        layer[e.t] = Math.max(layer[e.t] || 0, layer[u] + 1);
        if (--indeg[e.t] === 0) q.push(e.t);
      });
    }
    ids.forEach(function (id) { if (layer[id] === undefined) layer[id] = 0; });
    for (var pass = 0; pass < 2; pass++) {
      q.slice().reverse().forEach(function (id) {
        if (dIn[id].length || !dOut[id].length) return;
        var m = Infinity;
        dOut[id].forEach(function (e) { m = Math.min(m, layer[e.t]); });
        if (m !== Infinity && m - 1 > layer[id]) layer[id] = m - 1;
      });
    }
    var L = 0;
    ids.forEach(function (id) { L = Math.max(L, layer[id]); });
    L += 1;

    // 3. dummy nodes
    var V = Object.create(null); // all vertices incl. dummies
    ids.forEach(function (id) { V[id] = { id: id, w: N[id].w, h: N[id].h, layer: layer[id], dummy: false, up: [], down: [] }; });
    var chains = Object.create(null), dcount = 0;
    dag.forEach(function (e) {
      var chain = [e.s];
      for (var l = layer[e.s] + 1; l < layer[e.t]; l++) {
        var did = '\u0000d' + (dcount++);
        V[did] = { id: did, w: 0, h: opt.dummyH, layer: l, dummy: true, up: [], down: [] };
        chain.push(did);
      }
      chain.push(e.t);
      for (var i = 0; i + 1 < chain.length; i++) {
        V[chain[i]].down.push(chain[i + 1]);
        V[chain[i + 1]].up.push(chain[i]);
      }
      chains[e.id] = { chain: chain, rev: e.rev };
    });

    // 4. ordering
    var layers = [];
    for (var l = 0; l < L; l++) layers.push([]);
    // initial order: DFS from sources following down links
    var seen = Object.create(null);
    function place(v) {
      if (seen[v]) return;
      seen[v] = true;
      layers[V[v].layer].push(v);
      V[v].down.forEach(place);
    }
    startOrder.filter(function (id) { return !dIn[id].length; }).forEach(place);
    Object.keys(V).forEach(place);
    var posIdx = Object.create(null);
    function index() { layers.forEach(function (ly) { ly.forEach(function (v, i) { posIdx[v] = i; }); }); }
    index();
    function crossings() {
      var c = 0;
      for (var l = 0; l + 1 < layers.length; l++) {
        var segs = [];
        layers[l].forEach(function (v) { V[v].down.forEach(function (w) { segs.push([posIdx[v], posIdx[w]]); }); });
        for (var i = 0; i < segs.length; i++) for (var j = i + 1; j < segs.length; j++) {
          if ((segs[i][0] - segs[j][0]) * (segs[i][1] - segs[j][1]) < 0) c++;
        }
      }
      return c;
    }
    function sweep(down) {
      var range = [];
      if (down) for (var l = 1; l < layers.length; l++) range.push(l);
      else for (var l2 = layers.length - 2; l2 >= 0; l2--) range.push(l2);
      range.forEach(function (l) {
        var bc = Object.create(null);
        layers[l].forEach(function (v) {
          var nb = down ? V[v].up : V[v].down;
          if (!nb.length) { bc[v] = posIdx[v]; return; }
          var s = 0;
          nb.forEach(function (w) { s += posIdx[w]; });
          bc[v] = s / nb.length;
        });
        layers[l].sort(function (a, b) { return (bc[a] - bc[b]) || (posIdx[a] - posIdx[b]); });
        layers[l].forEach(function (v, i) { posIdx[v] = i; });
      });
    }
    var best = layers.map(function (ly) { return ly.slice(); }), bestC = crossings();
    for (var it = 0; it < opt.iterations && bestC > 0; it++) {
      sweep(it % 2 === 0);
      var c = crossings();
      if (c < bestC) { bestC = c; best = layers.map(function (ly) { return ly.slice(); }); }
    }
    layers = best;
    index();

    // 5. coordinates
    var layerX = [], x = 0;
    layers.forEach(function (ly, l) {
      var w = 0;
      ly.forEach(function (v) { w = Math.max(w, V[v].w); });
      if (!w) w = 20;
      layerX[l] = x + w / 2;
      x += w + opt.layerGap;
    });
    var Y = Object.create(null);
    function sep(a, b) {
      var g = (V[a].dummy || V[b].dummy) ? opt.nodeGap * 0.45 : opt.nodeGap;
      return (V[a].h + V[b].h) / 2 + g;
    }
    layers.forEach(function (ly) {
      var y = 0;
      ly.forEach(function (v, i) { if (i) y += sep(ly[i - 1], v); Y[v] = y; });
      var mid = y / 2;
      ly.forEach(function (v) { Y[v] -= mid; });
    });
    // isotonic regression placement: minimise squared distance to desired positions keeping order + gaps
    function placeLayer(ly, desired, weight) {
      var n = ly.length;
      if (!n) return;
      var off = [0];
      for (var i = 1; i < n; i++) off[i] = off[i - 1] + sep(ly[i - 1], ly[i]);
      var blocks = [];
      for (var k = 0; k < n; k++) {
        var t = desired[k] - off[k];
        blocks.push({ sum: t * weight[k], w: weight[k], n: 1, start: k });
        while (blocks.length > 1) {
          var b = blocks[blocks.length - 1], a = blocks[blocks.length - 2];
          if (a.sum / a.w <= b.sum / b.w) break;
          a.sum += b.sum; a.w += b.w; a.n += b.n;
          blocks.pop();
        }
      }
      blocks.forEach(function (b) {
        var z = b.sum / b.w;
        for (var j = b.start; j < b.start + b.n; j++) Y[ly[j]] = z + off[j];
      });
    }
    for (var r = 0; r < 12; r++) {
      var down = r % 2 === 0;
      var order = [];
      if (down) for (var l3 = 0; l3 < layers.length; l3++) order.push(l3);
      else for (var l4 = layers.length - 1; l4 >= 0; l4--) order.push(l4);
      order.forEach(function (l) {
        var ly = layers[l];
        var desired = [], weight = [];
        ly.forEach(function (v) {
          var nb = V[v].up.concat(V[v].down);
          if (!nb.length) { desired.push(Y[v]); weight.push(0.2); return; }
          var s = 0;
          nb.forEach(function (w) { s += Y[w]; });
          desired.push(s / nb.length);
          weight.push(V[v].dummy ? 2 : 1);
        });
        placeLayer(ly, desired, weight);
      });
    }

    var pos = Object.create(null), minY = Infinity, maxY = -Infinity;
    Object.keys(V).forEach(function (v) {
      var p = { x: layerX[V[v].layer], y: Y[v] };
      if (!V[v].dummy) {
        pos[v] = p;
        minY = Math.min(minY, p.y - V[v].h / 2);
        maxY = Math.max(maxY, p.y + V[v].h / 2);
      }
      V[v].p = p;
    });

    // 6. routes with distributed ports
    var routes = Object.create(null);
    var portsOut = Object.create(null), portsIn = Object.create(null);
    Object.keys(chains).forEach(function (eid) {
      var ch = chains[eid].chain;
      var a = ch[0], b = ch[ch.length - 1];
      (portsOut[a] || (portsOut[a] = [])).push({ eid: eid, ny: V[ch[1]].p.y });
      (portsIn[b] || (portsIn[b] = [])).push({ eid: eid, ny: V[ch[ch.length - 2]].p.y });
    });
    var startY = Object.create(null), endY = Object.create(null);
    function distribute(map, store) {
      Object.keys(map).forEach(function (v) {
        var list = map[v].sort(function (p, q2) { return p.ny - q2.ny; });
        var h = V[v].h * 0.6, n = list.length;
        list.forEach(function (p, i) { store[p.eid] = V[v].p.y + (n > 1 ? -h / 2 + h * i / (n - 1) : 0); });
      });
    }
    distribute(portsOut, startY);
    distribute(portsIn, endY);
    Object.keys(chains).forEach(function (eid) {
      var ch = chains[eid].chain;
      var pts = ch.map(function (v, i) {
        var vv = V[v];
        if (i === 0) return { x: vv.p.x + vv.w / 2, y: startY[eid] };
        if (i === ch.length - 1) return { x: vv.p.x - vv.w / 2, y: endY[eid] };
        return { x: vv.p.x, y: vv.p.y };
      });
      if (chains[eid].rev) pts.reverse();
      routes[eid] = pts;
    });
    return { pos: pos, routes: routes, minY: minY === Infinity ? 0 : minY, maxY: maxY === -Infinity ? 0 : maxY, width: x - opt.layerGap, crossings: bestC };
  }

  AL.layout = layout;
})();
