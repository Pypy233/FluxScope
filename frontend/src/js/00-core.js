/* FluxScope viewer — namespace and shared utilities.
 * Plain browser JavaScript; files are concatenated in name order by frontend/build.py. */
(function () {
  'use strict';
  var AL = window.FluxScope = window.FluxScope || {};

  // Capture the untouched document before any script mutates the DOM. The
  // offline report export clones this and swaps in the current case data.
  AL.pristine = document.documentElement.cloneNode(true);
  AL.VERSION = '0.1.0';
  AL.SUPPORTED_SCHEMA = 'fluxscope/analysis';
  AL.SUPPORTED_VERSION = /^0\.1\.\d+$/;

  var U = AL.util = {};

  U.esc = function (s) {
    if (s === null || s === undefined) return '';
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };

  /** Create an HTML element. attrs: {class, text, html, on:{evt:fn}, ...attributes} */
  U.h = function (tag, attrs, children) {
    var el = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) return;
        if (k === 'class') el.className = v;
        else if (k === 'text') el.textContent = v;
        else if (k === 'html') el.innerHTML = v;
        else if (k === 'on') Object.keys(v).forEach(function (e) { el.addEventListener(e, v[e]); });
        else if (k === 'dataset') Object.keys(v).forEach(function (d) { el.dataset[d] = v[d]; });
        else el.setAttribute(k, v === true ? '' : v);
      });
    }
    U.append(el, children);
    return el;
  };

  U.append = function (el, children) {
    if (children === null || children === undefined) return el;
    if (!Array.isArray(children)) children = [children];
    children.forEach(function (c) {
      if (c === null || c === undefined || c === false) return;
      if (Array.isArray(c)) U.append(el, c);
      else if (typeof c === 'string' || typeof c === 'number') el.appendChild(document.createTextNode(String(c)));
      else el.appendChild(c);
    });
    return el;
  };

  var SVGNS = 'http://www.w3.org/2000/svg';
  U.s = function (tag, attrs, children) {
    var el = document.createElementNS(SVGNS, tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) return;
        if (k === 'text') el.textContent = v;
        else if (k === 'class') el.setAttribute('class', v);
        else el.setAttribute(k, v);
      });
    }
    if (children) (Array.isArray(children) ? children : [children]).forEach(function (c) { if (c) el.appendChild(c); });
    return el;
  };

  U.$ = function (sel, root) { return (root || document).querySelector(sel); };
  U.$$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  U.clear = function (el) { while (el && el.firstChild) el.removeChild(el.firstChild); return el; };

  U.debounce = function (fn, ms) {
    var t = null;
    return function () {
      var a = arguments, self = this;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(self, a); }, ms);
    };
  };

  U.truncate = function (s, n) {
    s = String(s === undefined || s === null ? '' : s);
    return s.length > n ? s.slice(0, Math.max(1, n - 1)) + '…' : s;
  };

  U.uniq = function (arr) {
    var seen = Object.create(null), out = [];
    arr.forEach(function (x) { if (x !== null && x !== undefined && !seen[x]) { seen[x] = true; out.push(x); } });
    return out;
  };

  U.humanize = function (s) { return String(s || '').replace(/_/g, ' '); };

  var canvas = null;
  U.textWidth = function (text, font) {
    if (!canvas) canvas = document.createElement('canvas');
    var ctx = canvas.getContext('2d');
    ctx.font = font;
    return ctx.measureText(String(text)).width;
  };

  /** Fit text into maxWidth px for the given font, adding an ellipsis. */
  U.fitText = function (text, font, maxWidth) {
    text = String(text || '');
    if (U.textWidth(text, font) <= maxWidth) return text;
    var lo = 0, hi = text.length;
    while (lo < hi) {
      var mid = Math.ceil((lo + hi) / 2);
      if (U.textWidth(text.slice(0, mid) + '…', font) <= maxWidth) lo = mid; else hi = mid - 1;
    }
    return text.slice(0, Math.max(1, lo)) + '…';
  };

  U.shortHash = function (h) {
    h = String(h || '');
    return h.length > 18 ? h.slice(0, 10) + '…' + h.slice(-6) : h;
  };

  U.download = function (filename, content, mime) {
    var blob = new Blob([content], { type: mime || 'application/octet-stream' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(url); a.remove(); }, 1500);
  };

  U.safeFileName = function (s) {
    return String(s || 'fluxscope').replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 80) || 'fluxscope';
  };

  /** JSON for embedding inside <script type="application/json">. */
  U.embedJSON = function (obj) {
    return JSON.stringify(obj).replace(/</g, '\\u003c')
      .split(String.fromCharCode(0x2028)).join('\\u2028')
      .split(String.fromCharCode(0x2029)).join('\\u2029');
  };

  U.readJSONScript = function (id) {
    var el = document.getElementById(id);
    if (!el) return null;
    var txt = (el.textContent || '').trim();
    if (!txt || txt.indexOf('/*@') === 0) return null;
    try { return JSON.parse(txt); } catch (e) { console.error('FluxScope: cannot parse #' + id, e); return null; }
  };

  U.deepClone = function (o) { return JSON.parse(JSON.stringify(o)); };

  U.plural = function (n, word, pluralWord) { return n + ' ' + (n === 1 ? word : (pluralWord || word + 's')); };

  // Shared vocabulary -------------------------------------------------------
  AL.TYPE_LETTER = { entity: 'E', operation: 'O', position: 'P', asset: 'A', relation: 'R', exit: 'X', evidence: '§', unresolved: '!' };
  AL.TYPE_LABEL = { entity: 'Entity', operation: 'Operation', position: 'Position', asset: 'Asset', relation: 'Relation', exit: 'Exit path', evidence: 'Evidence', unresolved: 'Unresolved' };

  AL.BASIS_LABEL = { example: 'Example', static: 'Static candidate', observed: 'Observed', config: 'Configuration' };
  AL.VERIFICATION_LABEL = {
    supported: 'Supported', partially_supported: 'Partially supported', pending: 'Pending confirmation',
    contradicted: 'Contradicted', unresolved: 'Unresolved'
  };
  AL.VERIFICATION_GLYPH = { supported: '✓', partially_supported: '◐', pending: '?', contradicted: '✗', unresolved: '!' };
  AL.SUPPORT_LABEL = { rule: 'rule', adapter: 'adapter', llm: 'model-proposed', config: 'configuration', manual: 'manual', derived: 'derived' };
  AL.SCOPE_LABEL = {
    example: 'EXAMPLE DATA',
    static_analysis: 'STATIC ANALYSIS',
    execution_observation: 'OBSERVED EXECUTION',
    mixed: 'MIXED SCOPE'
  };
  AL.SCOPE_EXPLAIN = {
    example: 'Synthetic data for demonstration — not derived from a real project.',
    static_analysis: 'Candidate behaviour supported by code; not an observed execution.',
    execution_observation: 'Derived from concrete executions (transactions, logs, traces).',
    mixed: 'Combines static candidates and observed executions; see each item’s status.'
  };
})();
