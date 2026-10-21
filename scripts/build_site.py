"""Write dist/index.html, the landing page of the published website (GitHub Pages serves dist/).

    python scripts/build_site.py

Lists the reports in dist/reports/ (rules only and with the recorded LLM proposals) with the
numbers from each result, and links the example gallery. Self-contained: no external resources.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
REAL = ["kaia-yield", "beefy-erc4626", "metamorpho"]
FIXTURES = ["oz-erc4626", "receiver-vault"]

ROLE = {
    "beefy-erc4626": "Development project of the first milestone: vault, strategy and an external ERC-4626 vault.",
    "kaia-yield": "Development project for ledgers, debts and registries: two-step withdrawals, strategy debt, "
                  "a Yearn V2 vault and an ERC-4626 vault with registered strategies.",
    "metamorpho": "Held-out project: analysed without analyzer changes. Morpho Blue itself is outside the scope "
                  "(interface only), which the system map shows as a boundary.",
    "oz-erc4626": "Unmodified OpenZeppelin ERC-4626 (generality check).",
    "receiver-vault": "Synthetic fixture whose mint / burn helpers are not called _mint / _burn.",
}


def card(name: str, primary: bool) -> str:
    js = DIST / "reports" / f"{name}.json"
    if not js.exists():
        return ""
    r = json.loads(js.read_text())
    meta, scope = r["meta"], r.get("scope", {})
    proj = scope.get("project", {})
    roots = sum(1 for o in r["operations"] if not o.get("parent"))
    stats = (f"{len([e for e in r['entities'] if e['kind'] == 'contract'])} contracts · {roots} entry points · "
             f"{len(r['positions'])} positions · {len(r['relations'])} relations · {len(r['sources'])} source files")
    links = [f'<a class="btn primary" href="reports/{name}.html">Open report</a>']
    if (DIST / "reports" / f"{name}.llm.html").exists():
        links.append(f'<a class="btn" href="reports/{name}.llm.html">With LLM proposals</a>')
    links.append(f'<a class="btn ghost" href="reports/{name}.json">JSON</a>')
    commit = proj.get("commit")
    return f"""
    <article class="card{' primary' if primary else ''}">
      <h3>{html.escape(meta.get('title') or name)}</h3>
      <p class="role">{html.escape(ROLE.get(name, ''))}</p>
      <p class="desc">{html.escape(meta.get('description') or '')}</p>
      <p class="stats">{html.escape(stats)}{' · commit <code>' + html.escape(commit[:10]) + '</code>' if commit else ''}</p>
      <div class="links">{''.join(links)}</div>
    </article>"""


def main() -> None:
    real = "".join(card(n, True) for n in REAL)
    fixtures = "".join(card(n, False) for n in FIXTURES)
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FluxScope</title>
<style>
:root {{ --bg:#f4f5f8; --panel:#fff; --text:#1b2232; --muted:#5f6b80; --border:#e1e4ea; --accent:#2f6fed; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }}
main {{ max-width:1040px; margin:0 auto; padding:36px 20px 60px; }}
h1 {{ font-size:30px; margin:0 0 4px; }}
h2 {{ font-size:18px; margin:34px 0 12px; }}
h3 {{ font-size:17px; margin:0 0 6px; }}
.lead {{ color:var(--muted); font-size:16px; max-width:820px; }}
.how {{ background:var(--panel); border:1px solid var(--border); border-radius:10px; padding:14px 18px; }}
.how li {{ margin:4px 0; }}
.grid {{ display:grid; gap:14px; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); }}
.card {{ background:var(--panel); border:1px solid var(--border); border-radius:10px; padding:16px 18px; display:flex; flex-direction:column; }}
.card.primary {{ border-top:3px solid var(--accent); }}
.role {{ font-weight:600; margin:0 0 6px; font-size:14px; }}
.desc {{ color:var(--muted); font-size:13.5px; margin:0 0 8px; flex:1; }}
.stats {{ font-size:12.5px; color:var(--muted); margin:0 0 12px; }}
.links {{ display:flex; gap:8px; flex-wrap:wrap; }}
.btn {{ display:inline-block; padding:6px 12px; border-radius:6px; border:1px solid #c9ceda; color:var(--text); text-decoration:none; font-size:13.5px; }}
.btn.primary {{ background:var(--accent); border-color:var(--accent); color:#fff; }}
.btn.ghost {{ border-color:transparent; color:var(--muted); }}
.btn:hover {{ filter:brightness(.95); }}
code {{ font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:12.5px; }}
footer {{ margin-top:36px; color:var(--muted); font-size:13px; }}
</style>
</head>
<body>
<main>
  <h1>FluxScope</h1>
  <p class="lead">Recovers how assets and positions move through a DeFi system (asset flows, holdings,
  claims, dependencies and exit paths) from Solidity code with static analysis, rules and an optional
  language model, and shows the result next to the source, with the evidence behind every statement.</p>

  <div class="how">
    <b>How to read a report</b> (works offline in the browser; nothing is installed or sent anywhere):
    <ul>
      <li>It opens in the <b>Code</b> view: the source on the left, a <i>system map</i> of contracts and users on the right.</li>
      <li>Pick a function in the list: the graph shows only that function (caller, assets moved, calls; <code>+</code> opens the next level),
          and the source is annotated with what the analysis read from each line.</li>
      <li>Hover a marked line to find it in the graph, or a graph item to find its lines; <i>pin</i> functions to build the picture up
          (in the Kaia report: <code>redeem</code>, pin, then <code>claim</code> — the two-step withdrawal).</li>
      <li>The tabs <b>Flows · Holdings · Dependencies · Exits · All</b> show the whole system; <i>Holdings</i> answers who holds a share,
          what it claims and how to exit.</li>
    </ul>
  </div>

  <h2>Real projects (pinned commits)</h2>
  <div class="grid">{real}</div>

  <h2>Fixtures and examples</h2>
  <div class="grid">{fixtures}
    <article class="card">
      <h3>Synthetic examples</h3>
      <p class="desc">Viewer gallery with three hand-written cases (nested vault, swap + LP, lending), clearly marked as example data.
      JSON results can be imported into it.</p>
      <div class="links"><a class="btn primary" href="fluxscope.html">Open gallery</a></div>
    </article>
  </div>

  <footer>Static analysis results are candidate behaviour supported by code, not observed executions. Third-party Solidity shown in
  the reports keeps its own licence (SPDX header of each file).</footer>
</main>
</body>
</html>
"""
    (DIST / "index.html").write_text(page)
    print(f"wrote {DIST / 'index.html'}")


if __name__ == "__main__":
    main()
