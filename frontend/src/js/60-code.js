/* FluxScope viewer — evidence drawer: source viewer with line highlighting,
 * execution evidence, configuration / standard / model evidence. */
(function () {
  'use strict';
  var AL = window.FluxScope;
  var U = AL.util;
  var h = U.h;

  var KEYWORDS = ('pragma solidity import from as contract interface library abstract is function modifier event error struct enum mapping ' +
    'returns return if else for while do break continue emit revert require assert new delete public private internal external view pure payable ' +
    'virtual override constant immutable memory storage calldata indexed using unchecked try catch constructor fallback receive type assembly let ' +
    'true false this super msg tx block selfdestruct anonymous').split(' ');
  var TYPES = /^(u?int\d*|bytes\d*|bool|address|string|byte|fixed|ufixed)$/;
  var KW = Object.create(null);
  KEYWORDS.forEach(function (k) { KW[k] = true; });

  /** Tokenise source into lines of HTML, keeping block-comment state across lines. */
  function highlight(text, lang) {
    var lines = String(text).replace(/\r\n?/g, '\n').split('\n');
    var inBlock = false;
    return lines.map(function (line) {
      var out = '', i = 0;
      while (i < line.length) {
        if (inBlock) {
          var end = line.indexOf('*/', i);
          if (end < 0) { out += span('c', line.slice(i)); i = line.length; }
          else { out += span('c', line.slice(i, end + 2)); i = end + 2; inBlock = false; }
          continue;
        }
        var ch = line[i];
        if (ch === '/' && line[i + 1] === '/') { out += span('c', line.slice(i)); break; }
        if (ch === '/' && line[i + 1] === '*') { inBlock = true; continue; }
        if (ch === '"' || ch === "'") {
          var j = i + 1;
          while (j < line.length && line[j] !== ch) { if (line[j] === '\\') j++; j++; }
          out += span('s', line.slice(i, j + 1));
          i = j + 1;
          continue;
        }
        if (/[0-9]/.test(ch)) {
          var m = /^(0x[0-9a-fA-F_]+|[0-9][0-9_]*(\.[0-9_]+)?(e[0-9]+)?)/.exec(line.slice(i));
          if (m) { out += span('n', m[0]); i += m[0].length; continue; }
        }
        if (/[A-Za-z_$]/.test(ch)) {
          var w = /^[A-Za-z_$][A-Za-z0-9_$]*/.exec(line.slice(i))[0];
          if (lang !== 'text' && KW[w]) out += span('k', w);
          else if (lang !== 'text' && TYPES.test(w)) out += span('t', w);
          else out += U.esc(w);
          i += w.length;
          continue;
        }
        out += U.esc(ch);
        i++;
      }
      return out;
    });
  }
  function span(cls, s) { return '<span class="tok-' + cls + '">' + U.esc(s) + '</span>'; }

  var C = AL.code = {};
  var body, sub, picker, drawer, toggleBtn;
  var cache = Object.create(null);
  var currentEv = null;

  C.init = function () {
    body = document.getElementById('drawer-body');
    sub = document.getElementById('drawer-sub');
    picker = document.getElementById('source-picker');
    drawer = document.getElementById('drawer');
    toggleBtn = document.getElementById('btn-drawer-toggle');
    toggleBtn.addEventListener('click', function () { C.setOpen(drawer.classList.contains('closed')); });
    picker.addEventListener('change', function () {
      if (picker.value) C.showFile(picker.value);
    });
    // resize
    var handle = document.getElementById('drawer-handle');
    var drag = null;
    handle.addEventListener('mousedown', function (ev) { drag = { y: ev.clientY, h: drawer.getBoundingClientRect().height }; ev.preventDefault(); });
    window.addEventListener('mousemove', function (ev) {
      if (!drag) return;
      var nh = Math.max(120, Math.min(window.innerHeight * 0.75, drag.h - (ev.clientY - drag.y)));
      document.documentElement.style.setProperty('--drawer-h', nh + 'px');
      if (drawer.classList.contains('closed')) C.setOpen(true);
    });
    window.addEventListener('mouseup', function () { drag = null; });
  };

  C.setOpen = function (open) {
    drawer.classList.toggle('closed', !open);
    toggleBtn.textContent = open ? 'Hide' : 'Show';
  };

  C.reset = function (M) {
    cache = Object.create(null);
    currentEv = null;
    U.clear(picker);
    var files = Object.keys(M.sources).sort();
    picker.appendChild(h('option', { value: '', text: files.length ? 'Open source file… (' + files.length + ')' : 'No embedded source files' }));
    files.forEach(function (f) { picker.appendChild(h('option', { value: f, text: f })); });
    picker.disabled = !files.length;
    sub.textContent = '';
    U.clear(body).appendChild(h('div', { class: 'drawer-empty', text: 'Click an evidence chip (e.g. a source location or transaction) in the details panel to inspect it here.' }));
  };

  C.currentEvidence = function () { return currentEv; };

  function codeTable(path, content, lang, hlStart, hlEnd, firstLine) {
    var key = path + '|' + (firstLine || 1);
    var lines = cache[key] || (cache[key] = highlight(content, lang));
    var rows = [];
    var base = firstLine || 1;
    for (var i = 0; i < lines.length; i++) {
      var ln = base + i;
      var hl = hlStart && ln >= hlStart && ln <= hlEnd;
      var cls = hl ? 'hl' + (ln === hlStart ? ' hl-first' : '') + (ln === hlEnd ? ' hl-last' : '') : '';
      rows.push('<tr class="' + cls + '" data-line="' + ln + '"><td class="ln">' + ln + '</td><td>' + (lines[i] || ' ') + '</td></tr>');
    }
    var wrap = h('div', { class: 'code-view', 'data-file': path });
    wrap.innerHTML = '<table>' + rows.join('') + '</table>';
    return wrap;
  }

  function scrollToLine(view, line) {
    var row = view.querySelector('tr[data-line="' + line + '"]');
    if (!row) return;
    var top = row.offsetTop - Math.max(30, view.clientHeight * 0.25);
    view.scrollTop = Math.max(0, top);
  }

  function citedBy(M, evId) {
    var users = M.evUsers[evId] || [];
    if (!users.length) return h('div', { class: 'ev-cited', text: 'Not cited by any item.' });
    var box = h('div', { class: 'ev-cited' }, ['Cited by: ']);
    users.slice(0, 12).forEach(function (id) { box.appendChild(AL.details.refChip(M, id)); });
    if (users.length > 12) box.appendChild(document.createTextNode(' +' + (users.length - 12) + ' more'));
    return box;
  }

  C.showFile = function (path) {
    var M = AL.app.model;
    var src = M.sources[path];
    if (!src) return;
    C.setOpen(true);
    currentEv = null;
    sub.textContent = path + (src.synthetic ? ' · synthetic' : '');
    U.clear(body);
    if (src.synthetic) body.appendChild(h('div', { class: 'note-box warn', text: 'Synthetic example source — not from a real project.' }));
    body.appendChild(codeTable(path, src.content, src.language === 'solidity' ? 'solidity' : 'text'));
  };

  C.show = function (evId) {
    var M = AL.app.model;
    var ev = M.get(evId);
    C.setOpen(true);
    currentEv = evId;
    picker.value = '';
    U.clear(body);
    if (!ev || M.type(evId) !== 'evidence') {
      sub.textContent = evId;
      body.appendChild(h('div', { class: 'note-box bad', text: 'Evidence "' + evId + '" is referenced but not present in this result.' }));
      return;
    }
    sub.textContent = ev.title + (ev.synthetic ? ' · synthetic' : '');
    var head = h('div', { class: 'ev-head' }, [
      h('div', {}, [h('span', { class: 'badge', text: ev.kind }), ev.synthetic ? h('span', { class: 'badge basis-example', text: 'synthetic', style: 'margin-left:5px' }) : null]),
      h('div', { class: 'ev-title', text: ev.title }),
      ev.description ? h('div', { class: 'ev-desc', text: ev.description }) : null,
      citedBy(M, evId)
    ]);
    body.appendChild(head);

    if (ev.source) {
      var loc = ev.source;
      var where = loc.file + ':' + loc.start_line + (loc.end_line && loc.end_line !== loc.start_line ? '–' + loc.end_line : '') +
        (loc.contract ? '  ·  ' + loc.contract + (loc.function ? '.' + loc.function : '') : '');
      var file = M.sources[loc.file];
      if (file) {
        body.appendChild(h('div', { class: 'note-box info', html: '<b>' + U.esc(where) + '</b> — highlighted below' + (file.synthetic ? ' (synthetic example file)' : '') }));
        var view = codeTable(loc.file, file.content, file.language === 'solidity' ? 'solidity' : 'text', loc.start_line, loc.end_line || loc.start_line);
        body.appendChild(view);
        scrollToLine(view, loc.start_line);  // reading offsetTop forces layout, so this works synchronously
        requestAnimationFrame(function () { scrollToLine(view, loc.start_line); });
        if (loc.snippet && !snippetMatches(file.content, loc)) {
          body.insertBefore(h('div', { class: 'note-box warn', text: 'The recorded snippet differs from the embedded file at these lines; the file may have changed since analysis.' }), view);
        }
      } else if (loc.snippet) {
        body.appendChild(h('div', { class: 'note-box warn', html: '<b>' + U.esc(where) + '</b> — the file is not embedded in this result; showing the recorded snippet.' }));
        var sv = codeTable(loc.file + '#snippet', loc.snippet, 'solidity', loc.start_line, loc.end_line || loc.start_line, loc.start_line);
        body.appendChild(sv);
      } else {
        body.appendChild(h('div', { class: 'note-box bad', text: where + ' — neither the file nor a snippet is available.' }));
      }
    }
    if (ev.execution) {
      var x = ev.execution;
      var rows = [];
      function row(k, v, mono) { if (v !== undefined && v !== null && v !== '') rows.push(h('tr', {}, [h('td', { text: k }), h('td', { class: mono ? 'mono' : '', text: String(v) })])); }
      row('Chain', x.chain_id);
      row('Transaction', x.tx_hash, true);
      row('Block', x.block);
      row('Log index', x.log_index);
      row('Call path', x.call_path, true);
      var ex = h('div', { class: 'exec-view' }, [h('table', {}, rows)]);
      if (x.data) ex.appendChild(h('div', { style: 'margin-top:8px' }, [h('div', { class: 'muted small', text: 'Decoded data' }), h('pre', { text: JSON.stringify(x.data, null, 2) })]));
      body.appendChild(ex);
    }
    if (ev.llm) {
      var l = ev.llm;
      body.appendChild(h('div', { class: 'note-box warn', text: 'Model output — a proposal, not a verified fact. Check the cited program facts.' }));
      var lt = h('div', { class: 'exec-view' }, [h('table', {}, Object.keys(l).map(function (k) {
        return h('tr', {}, [h('td', { text: k }), h('td', { class: 'mono', text: typeof l[k] === 'object' ? JSON.stringify(l[k]) : String(l[k]) })]);
      }))]);
      body.appendChild(lt);
    }
    if (ev.reference) {
      body.appendChild(h('div', { class: 'exec-view' }, [h('span', { class: 'muted', text: 'Reference: ' }), h('span', { class: 'mono', text: ev.reference })]));
    }
    if (!ev.source && !ev.execution && !ev.llm && !ev.reference && !ev.description) {
      body.appendChild(h('div', { class: 'note-box warn', text: 'This evidence item carries no location, execution reference or description.' }));
    }
  };

  function snippetMatches(content, loc) {
    var lines = content.split('\n').slice(loc.start_line - 1, (loc.end_line || loc.start_line));
    var a = lines.join('\n').replace(/\s+/g, ' ').trim();
    var b = String(loc.snippet).replace(/\s+/g, ' ').trim();
    return !b || a === b || a.indexOf(b) >= 0 || b.indexOf(a) >= 0;
  }

  C.highlight = highlight;
})();
