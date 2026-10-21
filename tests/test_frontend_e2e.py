"""End-to-end acceptance tests for the FluxScope viewer (offline HTML).

Run:  .venv/bin/python -m pytest tests/test_frontend_e2e.py -q

The viewer is rebuilt once per session (frontend/build.py) and opened from
file:// in headless Chrome (Playwright, channel="chrome"). Every http(s)
request is aborted and recorded; each test asserts that none was attempted
and that the page raised no JavaScript errors.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "fluxscope.html"
TEMPLATE = ROOT / "fluxscope" / "static" / "app.html"
FIX = ROOT / "tests" / "fixtures"
SHOTS = ROOT / "dist" / "screenshots"
EXAMPLES = sorted((ROOT / "examples").glob("*.example.json"))
BEEFY = FIX / "beefy-erc4626.result.json"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _viewer_built(built):
    """The viewer is rebuilt once per session (shared `built` / `browser` fixtures in conftest.py)."""
    return built


class Page:
    """Thin wrapper adding offline enforcement and helpers."""

    def __init__(self, browser, url: str):
        self.ctx = browser.new_context(viewport={"width": 1600, "height": 950}, accept_downloads=True)
        self.page = self.ctx.new_page()
        self.errors: list[str] = []
        self.network: list[str] = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        self.page.on("request", self._on_request)
        self.page.route(re.compile(r"^(https?|wss?)://"), self._abort)
        self.open(url)

    def _on_request(self, req):
        if not req.url.startswith(("file:", "data:", "blob:", "about:")):
            self.network.append(req.url)

    def _abort(self, route):
        self.network.append(route.request.url)
        route.abort()

    def open(self, url: str):
        self.page.goto(url)
        self.page.wait_for_selector("html[data-fluxscope-ready]", state="attached", timeout=15000)

    # helpers ---------------------------------------------------------------
    def js(self, expr: str, arg=None):
        return self.page.evaluate(expr, arg)

    def node_ids(self) -> list[str]:
        return self.js("() => Array.from(document.querySelectorAll('#graph .g-node')).map(n => n.dataset.id)")

    def edge_kinds(self) -> list[str]:
        return self.js("() => Array.from(document.querySelectorAll('#graph .g-edge')).map(n => n.dataset.kind)")

    def view(self, v: str):
        self.page.click(f"#view-switch button[data-view='{v}']")
        self.page.wait_for_timeout(80)

    def case(self, title_part: str):
        self.page.click(f"#case-list .case-item:has-text('{title_part}')")
        self.page.wait_for_timeout(80)

    def click_node(self, node_id: str):
        self.page.locator(f"#graph .g-node[data-id='{node_id}']").dispatch_event("click")
        self.page.wait_for_timeout(60)

    def handle(self, node_id: str, action: str):
        self.page.locator(f"#graph .g-node[data-id='{node_id}'] .g-handle[data-action='{action}']").dispatch_event("click")
        self.page.wait_for_timeout(60)

    def details(self) -> str:
        return self.page.inner_text("#details")

    def shot(self, name: str):
        self.page.screenshot(path=str(SHOTS / f"{name}.png"))

    def assert_clean(self):
        assert not self.errors, f"JavaScript errors: {self.errors}"
        assert not self.network, f"network requests attempted: {self.network}"

    def close(self):
        self.ctx.close()


@pytest.fixture
def app(browser):
    p = Page(browser, DIST.as_uri())
    yield p
    try:
        p.assert_clean()
    finally:
        p.close()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# rendering and offline behaviour
# ---------------------------------------------------------------------------


def test_build_is_self_contained():
    for f in (DIST, TEMPLATE):
        html = f.read_text()
        assert not re.search(r"""(?:src|href)\s*=\s*["'](?:https?:)?//""", html), f"{f} references external resources"
        assert "<link " not in html
    tpl = TEMPLATE.read_text()
    # contract with fluxscope/report.py: these exact elements are replaced by regex
    assert re.search(r'<script type="application/json" id="fluxscope-data">(.*?)</script>', tpl, re.S).group(1) == '{"mode":"app","cases":[]}'
    assert re.search(r'<script type="application/json" id="fluxscope-config">(.*?)</script>', tpl, re.S).group(1) == '{"api":null}'


def test_no_case_specific_code():
    """The renderer must not branch on example names or ids."""
    js = "\n".join(p.read_text() for p in sorted((ROOT / "frontend" / "src" / "js").glob("*.js")))
    ids = set()
    for ex in EXAMPLES:
        d = load_json(ex)
        ids.add(d["meta"]["id"])
        for coll in ("assets", "entities", "operations", "positions", "relations", "exits"):
            ids.update(x["id"] for x in d.get(coll, []))
    for i in ids:
        assert f"'{i}'" not in js and f'"{i}"' not in js, f"viewer code mentions example id {i}"
    for word in ("Nested vault", "ExampleVault", "Token U", "Beefy"):
        assert word not in js


def test_all_examples_render_every_view_offline(app):
    titles = app.js("() => FluxScope.app.cases.map(c => c.data.meta.title)")
    assert len(titles) == len(EXAMPLES) >= 3
    for i, title in enumerate(titles):
        app.page.click(f"#case-list .case-item[data-case='{i}']")
        assert app.page.inner_text("#scope-banner").startswith("EXAMPLE DATA")
        for v in ("flows", "holdings", "dependencies", "exits", "all"):
            app.view(v)
            assert app.js("() => document.querySelector('#view-switch button.active').dataset.view") == v
            assert len(app.node_ids()) > 0, f"{title}: empty {v} view"
            app.shot(f"e2e-case{i}-{v}")


def test_key_interaction_share_holders_claims_exits_evidence(app):
    data = load_json(ROOT / "examples" / "nested-vault.example.json")
    app.case("Example A")
    app.view("holdings")
    app.click_node("pos:user-vU")
    d = app.details()
    assert "vU held by user" in d
    holders = app.page.inner_text("[data-testid=share-holders]")
    assert "User (any account)" in holders
    claims = app.page.inner_text("[data-testid=share-claims]")
    # direct and transitive backing, each with its holder
    for label in ("Idle U in vault", "sU held by vault", "Idle U in strategy", "lU held by strategy", "U supplied to market L"):
        assert label in claims, label
    assert "ExampleVault" in claims and "ExampleStrategy" in claims and "Lending market L" in claims
    deps = app.page.inner_text("[data-testid=share-deps]")
    assert "ExampleStrategy" in deps and "Lending market L" in deps and "outside analysis scope" in deps
    exits = app.page.inner_text("[data-testid=share-exits]")
    assert "Redeem vU for Token U" in exits and "4 steps" in exits
    assert "not additional funds" in d
    app.shot("e2e-key-share-summary")
    # evidence chip → highlighted source lines that match the evidence record
    ev = {e["id"]: e for e in data["evidence"]}
    chip = app.page.locator("[data-testid=share-claims] .ev-chip").first
    ev_id = chip.get_attribute("data-ev")
    chip.click()
    loc = ev[ev_id]["source"]
    rows = app.js("() => Array.from(document.querySelectorAll('#drawer-body .code-view tr.hl')).map(r => +r.dataset.line)")
    assert rows == list(range(loc["start_line"], loc["end_line"] + 1))
    assert app.js("() => document.querySelector('#drawer-body .code-view').dataset.file") == loc["file"]
    app.page.wait_for_timeout(100)
    visible = app.js("""() => {
      const v = document.querySelector('#drawer-body .code-view'), r = v.querySelector('tr.hl');
      const a = v.getBoundingClientRect(), b = r.getBoundingClientRect();
      return b.top >= a.top && b.bottom <= a.bottom;
    }""")
    assert visible, "highlighted source line is scrolled into view"
    app.shot("e2e-evidence-source")


def test_search_acts_on_current_data(app):
    app.case("Example A")
    app.page.fill("#search-input", "market L")
    app.page.wait_for_timeout(250)
    items = app.page.locator("[data-testid=search-item]")
    assert items.count() >= 1
    texts = [items.nth(i).inner_text() for i in range(items.count())]
    assert any("Lending market L" in t for t in texts)
    assert not any("Pair YZ" in t for t in texts)
    app.page.locator("[data-testid=search-item]:has-text('Lending market L')").first.click()
    assert app.page.inner_text("[data-testid=details-title]") == "Lending market L"
    # switching case changes the search space
    app.case("Example B")
    app.page.fill("#search-input", "Pair YZ")
    app.page.wait_for_timeout(250)
    assert "Pair YZ" in app.page.inner_text("#search-results")
    app.page.fill("#search-input", "market L")
    app.page.wait_for_timeout(250)
    assert "No matches" in app.page.inner_text("#search-results")


def test_filters_apply_to_current_graph(app):
    app.case("Example A")
    app.view("holdings")
    app.page.click("#sec-filters h3")
    assert "claim" in app.edge_kinds()
    app.page.uncheck("[data-testid=filter-edge-claim]")
    assert "claim" not in app.edge_kinds()
    app.page.click("[data-testid=filter-reset]")
    assert "claim" in app.edge_kinds()
    # basis filter: hide static candidates → only observed / example items remain
    app.view("all")
    before = set(app.node_ids())
    assert "op:redeem" in before
    app.page.uncheck("[data-testid=filter-basis-static]")
    after = set(app.node_ids())
    assert "op:redeem" not in after and "op:deposit" in after
    app.page.click("[data-testid=filter-reset]")


def test_static_and_observed_are_visually_distinct(app):
    app.case("Example A")
    app.view("flows")
    dashes = app.js("""() => {
      const out = {};
      document.querySelectorAll('#graph .g-node[data-type=operation]').forEach(n => {
        out[n.dataset.id] = n.querySelector('.shape').getAttribute('stroke-dasharray');
      });
      return out;
    }""")
    assert dashes["op:deposit"] is None  # observed → solid
    assert dashes["op:redeem"] == "5 3"  # static candidate → dashed
    app.click_node("op:redeem")
    assert "Static candidate" in app.details()
    app.click_node("op:deposit")
    assert "Observed" in app.details()


def test_expand_collapse_operations_groups_and_dependency_levels(app):
    app.case("Example A")
    app.view("flows")
    ids = app.node_ids()
    assert "op:deposit" in ids and "op:deposit.pull" not in ids
    app.handle("op:deposit", "expand-op")
    ids = app.node_ids()
    assert "op:deposit.pull" in ids and "op:deposit.mint" in ids
    assert "contains" in app.edge_kinds()
    app.handle("op:deposit", "collapse-op")
    assert "op:deposit.pull" not in app.node_ids()
    # entity group
    app.page.click("#sec-groups h3")
    app.page.check("[data-testid='group-ent:sys']")
    ids = app.node_ids()
    assert "ent:sys" in ids and "ent:vault" not in ids and "ent:strategy" not in ids
    app.handle("ent:sys", "expand-group")
    ids = app.node_ids()
    assert "ent:sys" not in ids and "ent:vault" in ids
    # dependency depth in holdings view
    app.view("holdings")
    ids = app.node_ids()
    assert "pos:strategy-lU" in ids and "pos:market-U" not in ids
    app.handle("pos:strategy-lU", "expand-dep")
    assert "pos:market-U" in app.node_ids()
    app.page.click("#btn-collapse-all")
    ids = app.node_ids()
    assert "pos:strategy-U" not in ids and "pos:user-vU" in ids
    app.page.click("#btn-expand-all")
    assert "pos:market-U" in app.node_ids()


def test_related_path_highlight(app):
    app.case("Example A")
    app.view("holdings")
    app.page.click("#btn-expand-all")
    app.click_node("pos:vault-sU")
    dim = set(app.js("() => Array.from(document.querySelectorAll('#graph .g-node.dim')).map(n => n.dataset.id)"))
    lit = set(app.node_ids()) - dim
    # downstream: what sU claims; upstream: the vU position that claims sU and its holders
    assert {"pos:vault-sU", "pos:strategy-U", "pos:strategy-lU", "pos:market-U", "pos:user-vU", "ent:user"} <= lit
    assert "pos:vault-U" in dim
    app.page.select_option("#related-mode", "down")
    dim = set(app.js("() => Array.from(document.querySelectorAll('#graph .g-node.dim')).map(n => n.dataset.id)"))
    assert "pos:user-vU" in dim and "pos:market-U" not in dim
    app.page.select_option("#related-mode", "off")
    assert app.js("() => document.querySelectorAll('#graph .g-node.dim').length") == 0


def test_exit_path_view_and_conditions(app):
    app.case("Example A")
    app.view("holdings")
    app.click_node("pos:user-vU")
    app.page.locator("[data-testid=share-exits] [data-action=show-exit]").first.click()
    assert app.js("() => FluxScope.app.state.view") == "exits"
    lit = set(app.node_ids()) - set(app.js("() => Array.from(document.querySelectorAll('#graph .g-node.dim')).map(n => n.dataset.id)"))
    assert {"pos:user-vU", "op:redeem", "op:redeem.swithdraw", "op:redeem.mwithdraw", "op:redeem.pay"} <= lit
    d = app.details()
    assert "steps" in d.lower() and "conditional step" in d
    assert "idle U in vault < assets" in d
    assert "Caller holds at least `shares` vU" in d
    assert app.page.locator("#details .step").count() == 4
    app.shot("e2e-exit-path")


def test_borrowed_asset_and_debt_are_separate(app):
    app.case("Example C")
    app.view("holdings")
    app.click_node("pos:c:user-D")
    assert app.page.locator("[data-testid=borrowed-note]").count() == 1
    app.click_node("pos:c:user-debt")
    assert app.page.locator("[data-testid=debt-note]").count() == 1
    app.click_node("ent:c:user")
    groups = app.js("() => Array.from(document.querySelectorAll('#details [data-hold-kind]')).map(g => g.dataset.holdKind)")
    assert "custody" in groups and "debt" in groups and "share" in groups
    d = app.details()
    assert "never summed" in d
    app.shot("e2e-lending-borrower")


def test_flow_amounts_carry_their_asset(app):
    app.case("Example B")
    app.view("flows")
    app.click_node("op:zap")
    rows = app.page.locator("#details .flow-row")
    assert rows.count() >= 1
    units = app.js("() => Array.from(document.querySelectorAll('#details .flow-row .amount .unit')).map(u => u.textContent)")
    assert units and all(units)
    app.handle("op:zap", "expand-op")
    app.click_node("op:zap.addliq")
    d = app.details()
    assert "49.1 Y" in d and "24.3 Z" in d and "34.5 LP-YZ" in d
    app.shot("e2e-swap-multi-asset")


def test_missing_evidence_is_explicit(app):
    app.case("Example B")
    app.page.evaluate("() => FluxScope.app.select('rel:b:dep-xyreserve', {reveal: true})")
    assert app.page.locator("#details [data-testid=evidence-missing]").count() >= 1
    assert "No evidence attached" in app.details()


def test_task_applies_view_and_focus(app):
    app.case("Example B")
    app.page.click("[data-task='task:b:branches']")
    assert app.js("() => FluxScope.app.state.view") == "flows"
    ids = app.node_ids()
    assert {"op:zap.swapY", "op:zap.swapZ", "op:zap.addliq"} <= set(ids)
    app.page.click("[data-task='task:b:branches'] >> text=Show expected answer")
    assert "merge in pair YZ" in app.page.inner_text("[data-task='task:b:branches']")
    app.shot("e2e-task-branches")


# ---------------------------------------------------------------------------
# import / export
# ---------------------------------------------------------------------------


def test_import_unseen_topology_updates_whole_page(app):
    before = app.js("() => FluxScope.app.cases.length")
    app.page.set_input_files("#file-input", str(FIX / "unseen-topology.json"))
    app.page.wait_for_timeout(200)
    assert app.js("() => FluxScope.app.cases.length") == before + 1
    assert "Router fan-out" in app.page.inner_text("#case-list .case-item.active")
    assert app.page.inner_text("#scope-banner").startswith("OBSERVED EXECUTION")
    # graph uses the new ids
    app.view("holdings")
    assert {"p-a", "p-1", "p-2"} <= set(app.node_ids())
    app.view("flows")
    assert {"n-alice", "o-dep", "o-reb"} <= set(app.node_ids())
    # tasks, unresolved, search, filters reflect the new data
    assert "Which strategies back Alice" in app.page.inner_text("#task-list")
    assert "No withdraw entrypoint" in app.page.inner_text("#unresolved-list")
    app.page.fill("#search-input", "Venue")
    app.page.wait_for_timeout(250)
    assert "Venue E" in app.page.inner_text("#search-results")
    app.page.click("#sec-filters h3")
    assert "Static candidate" in app.page.inner_text("#filter-panel") and "Observed" in app.page.inner_text("#filter-panel")
    # snippet fallback: file not embedded
    app.page.evaluate("() => FluxScope.app.select('o-reb', {reveal: true})")
    app.page.locator("#details .ev-chip[data-ev='x2']").first.click()
    body = app.page.inner_text("#drawer-body")
    assert "not embedded" in body and "s1.divest(amt)" in body
    # an item without evidence says so
    app.page.evaluate("() => FluxScope.app.select('p-1', {reveal: true})")
    assert "No evidence attached" in app.details()
    app.shot("e2e-import-unseen")


@pytest.mark.parametrize("name, expected", [
    ("invalid-malformed.json", "not valid JSON"),
    ("invalid-schema.json", 'missing required property "title"'),
    ("invalid-dangling.json", 'unknown entity "e-missing"'),
    ("invalid-version.json", "unsupported schema version"),
])
def test_invalid_files_give_readable_errors(app, name, expected):
    before = app.js("() => FluxScope.app.cases.length")
    active = app.js("() => FluxScope.app.caseIndex")
    app.page.set_input_files("#file-input", str(FIX / name))
    app.page.wait_for_selector("[data-testid=modal]")
    text = app.page.inner_text("[data-testid=modal]")
    assert expected in text
    assert "Nothing was changed" in text
    if name == "invalid-schema.json":
        assert "must be one of" in text and "scope.kind" in text
    if name == "invalid-dangling.json":
        assert 'unknown evidence "ev-missing"' in text and "amount is denominated in" in text
    app.page.keyboard.press("Escape")
    assert app.js("() => FluxScope.app.cases.length") == before
    assert app.js("() => FluxScope.app.caseIndex") == active


def test_paste_import(app):
    app.page.click("#btn-paste")
    app.page.fill("[data-testid=paste-text]", (FIX / "unseen-topology.json").read_text())
    app.page.click("[data-testid=paste-import]")
    app.page.wait_for_timeout(200)
    assert "Router fan-out" in app.page.inner_text("#case-list .case-item.active")


def test_export_json_roundtrip(app, tmp_path):
    app.case("Example B")
    with app.page.expect_download() as dl:
        app.page.click("#btn-export-json")
    path = tmp_path / "out.json"
    dl.value.save_as(path)
    assert load_json(path) == load_json(ROOT / "examples" / "swap-lp.example.json")


def test_export_html_report_reopens_offline(app, browser, tmp_path):
    app.page.set_input_files("#file-input", str(FIX / "unseen-topology.json"))
    app.page.wait_for_timeout(150)
    app.case("Example A")
    with app.page.expect_download() as dl:
        app.page.click("#btn-export-html")
    report = tmp_path / "report.html"
    dl.value.save_as(report)
    html = report.read_text()
    assert '"mode":"report"' in html and "example-nested-vault" in html
    assert "fixture-router-fanout" not in html  # only the current case is exported

    r = Page(browser, report.as_uri())
    try:
        assert r.page.is_visible("#report-banner")
        assert "Offline report" in r.page.inner_text("#report-banner")
        assert r.js("() => FluxScope.app.cases.length") == 1
        assert "Example A" in r.page.inner_text("#case-list")
        r.view("holdings")
        r.click_node("pos:user-vU")
        assert "Redeem vU for Token U" in r.page.inner_text("[data-testid=share-exits]")
        r.page.locator("#details .ev-chip").first.click()
        assert r.page.locator("#drawer-body .code-view tr.hl").count() >= 1
        r.view("exits")
        assert "op:redeem" in r.node_ids()
        r.shot("e2e-report-reopened")
        # the exported report can itself export again
        with r.page.expect_download() as dl2:
            r.page.click("#btn-export-html")
        again = tmp_path / "again.html"
        dl2.value.save_as(again)
        assert "example-nested-vault" in again.read_text()
        r.assert_clean()
    finally:
        r.close()


def test_import_exported_report_html(app, tmp_path):
    html = app.js("() => FluxScope.app.buildReportHTML()")
    p = tmp_path / "x.report.html"
    p.write_text(html)
    before = app.js("() => FluxScope.app.cases.length")
    app.page.set_input_files("#file-input", str(p))
    app.page.wait_for_timeout(150)
    assert app.js("() => FluxScope.app.cases.length") == before + 1


def test_empty_template_shows_import_hint(browser):
    r = Page(browser, TEMPLATE.as_uri())
    try:
        assert r.js("() => FluxScope.app.cases.length") == 0
        assert "No analysis result loaded" in r.page.inner_text("#graph-empty")
        r.page.set_input_files("#file-input", str(FIX / "unseen-topology.json"))
        r.page.wait_for_timeout(150)
        assert len(r.node_ids()) > 0
        r.assert_clean()
    finally:
        r.close()


def test_template_filled_like_backend_report(browser, tmp_path):
    """Mimic fluxscope/report.py: regex-replace the data slot of the template."""
    data = load_json(FIX / "unseen-topology.json")
    payload = json.dumps({"mode": "report", "cases": [data], "default": data["meta"]["id"]}).replace("<", "\\u003c")
    tpl = TEMPLATE.read_text()
    out = re.sub(r'(<script type="application/json" id="fluxscope-data">)(.*?)(</script>)',
                 lambda m: m.group(1) + payload + m.group(3), tpl, count=1, flags=re.S)
    f = tmp_path / "filled.html"
    f.write_text(out)
    r = Page(browser, f.as_uri())
    try:
        assert r.js("() => FluxScope.app.cases.length") == 1
        assert r.page.inner_text("#scope-banner").startswith("OBSERVED EXECUTION")
        r.assert_clean()
    finally:
        r.close()


# ---------------------------------------------------------------------------
# analyze panel
# ---------------------------------------------------------------------------


def test_analyze_panel_without_backend_does_not_fake_progress(app):
    t = app.page.inner_text("[data-testid=analyze-off]")
    assert "No analysis backend connected" in t
    assert app.page.locator("[data-testid=analyze-run]").count() == 0


def test_analyze_panel_talks_to_api(browser, tmp_path):
    """Exercise the job UI against a scripted API (no real backend, no network)."""
    tpl = TEMPLATE.read_text()
    html = tpl.replace('<script type="application/json" id="fluxscope-config">{"api":null}</script>',
                       '<script type="application/json" id="fluxscope-config">{"api":"http://fluxscope.test/api"}</script>')
    f = tmp_path / "served.html"
    f.write_text(html)
    result = (ROOT / "examples" / "lending.example.json").read_text()
    calls = {"status": 0}

    ctx = browser.new_context(viewport={"width": 1600, "height": 950})
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    def handler(route):
        url, method = route.request.url, route.request.method
        if url.endswith("/api/projects"):
            return route.fulfill(json=[{"id": "demo", "name": "Demo project", "path": "/x"}])
        if url.endswith("/api/jobs") and method == "POST":
            body = json.loads(route.request.post_data)
            assert body["project"] == "demo" and body["llm"] == "none"
            return route.fulfill(json={"id": "job1", "status": "queued"})
        if url.endswith("/api/jobs/job1"):
            calls["status"] += 1
            if calls["status"] < 2:
                return route.fulfill(json={"id": "job1", "status": "running", "stage": "static facts", "log": ["compiling"]})
            return route.fulfill(json={"id": "job1", "status": "succeeded", "stage": "done", "log": ["compiling", "done"]})
        if url.endswith("/api/jobs/job1/result"):
            return route.fulfill(body=result, content_type="application/json")
        return route.abort()

    page.route("http://fluxscope.test/**", handler)
    page.goto(f.as_uri())
    page.wait_for_selector("html[data-fluxscope-ready]", state="attached")
    page.wait_for_selector("[data-testid=analyze-project] option[value=demo]", state="attached")
    page.click("[data-testid=analyze-run]")
    page.wait_for_function("() => FluxScope.app.cases.length === 1", timeout=10000)
    assert "Collateralised borrowing" in page.inner_text("#case-list")
    assert "succeeded" in page.inner_text("[data-testid=job-box]")
    assert calls["status"] >= 2
    assert not errors
    ctx.close()


# ---------------------------------------------------------------------------
# real backend output (if present)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not BEEFY.exists(), reason="real analysis result not generated yet")
def test_real_backend_result_is_readable(app):
    data = load_json(BEEFY)
    app.page.set_input_files("#file-input", str(BEEFY))
    app.page.wait_for_timeout(300)
    assert app.js("() => FluxScope.app.cases[FluxScope.app.caseIndex].data.meta.id") == data["meta"]["id"]
    assert app.page.inner_text("#scope-banner").startswith("STATIC ANALYSIS")
    view = app.js("() => FluxScope.app.state.view")
    assert view == "code"  # embedded sources: source next to the system map
    # nested operations start collapsed
    nested = [o["id"] for o in data["operations"] if o.get("parent")]
    assert not set(nested) & set(app.node_ids())
    app.shot("e2e-real-default")
    # a share position: holders, backing and exits are shown with evidence
    share_assets = {a["id"] for a in data["assets"] if a["kind"] in ("share", "lp_token")}
    share_pos = next(p for p in data["positions"] if p["asset"] in share_assets)
    app.view("holdings")
    app.page.evaluate("(id) => FluxScope.app.select(id, {reveal: true})", share_pos["id"])
    assert app.page.locator("[data-testid=share-summary], [data-section=share-summary]").count() == 1
    assert app.page.locator("#details .ev-chip").count() > 0
    app.page.locator("#details .ev-chip").first.click()
    assert app.page.locator("#drawer-body .code-view tr.hl").count() >= 1
    app.shot("e2e-real-share")
    # dense flows: focusing one root operation reduces the graph
    app.view("flows")
    full = len(app.edge_kinds())
    root = next(o for o in data["operations"] if not o.get("parent") and o["kind"] == "deposit")
    app.page.evaluate("(id) => FluxScope.app.onlyOperation(id)", root["id"])
    assert len(app.edge_kinds()) < full
    assert root["id"] in app.node_ids()
    app.shot("e2e-real-flows-focused")
    for v in ("dependencies", "exits", "all"):
        app.view(v)
        assert len(app.node_ids()) > 0
