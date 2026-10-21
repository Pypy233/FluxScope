"""Code view of the viewer: source on the left, the graph of the code being read on the right.

Runs against the real Beefy result (tests/fixtures/beefy-erc4626.result.json) rendered as an
offline report, in headless Chrome with every network request blocked.
"""

from __future__ import annotations

import json

import pytest

from .test_frontend_e2e import FIX, Page

BEEFY = FIX / "beefy-erc4626.result.json"
DEPOSIT = "op:vault.deposit(uint256)"
WITHDRAW = "op:vault.withdraw(uint256)"

pytestmark = pytest.mark.skipif(not BEEFY.exists(), reason="real analysis result not generated yet")


@pytest.fixture
def report(browser, tmp_path):
    from fluxscope.report import write_report

    data = json.loads(BEEFY.read_text())
    out = write_report(data, tmp_path / "beefy.html")
    p = Page(browser, out.as_uri())
    yield p, data
    try:
        p.assert_clean()
    finally:
        p.close()


def by_id(items):
    return {x["id"]: x for x in items}


def locations(data, ev_ids):
    ev = by_id(data["evidence"])
    out = []
    for i in ev_ids:
        s = ev.get(i, {}).get("source")
        if s:
            out.append((s["file"], s["start_line"], s.get("end_line") or s["start_line"]))
    return out


def definition(data, op):
    return max(locations(data, op["evidence"]), key=lambda l: l[2] - l[1])


def open_fn(p, op_id):
    p.page.click(f"[data-testid=code-outline] .co-item[data-op='{op_id}']")
    p.page.wait_for_timeout(120)


def shown_file(p):
    return p.js("() => document.querySelector('#code-body .src-view').dataset.file")


def test_report_opens_in_code_view_with_a_small_system_map(report):
    p, data = report
    assert p.js("() => FluxScope.app.state.view") == "code"
    assert p.page.is_visible("[data-testid=code-pane]") and not p.page.is_visible("#sidebar")
    nodes = p.node_ids()
    contracts = {e["id"] for e in data["entities"] if e["kind"] == "contract"}
    assert contracts <= set(nodes) and "map:users" in nodes
    assert len(nodes) <= 8
    # every account and role is one node; its members are listed in the details
    p.click_node("map:users")
    assert "members" in p.details().lower()
    # the first contract's file is open
    vault = next(e for e in data["entities"] if e["kind"] == "contract")
    assert shown_file(p) == vault["contract"]["file"]
    p.shot("code-map")
    p.view("flows")
    assert len(p.node_ids()) > 2 * len(nodes)


def test_opening_a_function_shows_only_its_graph_and_annotated_source(report):
    p, data = report
    op = by_id(data["operations"])[DEPOSIT]
    open_fn(p, DEPOSIT)
    nodes = p.node_ids()
    assert DEPOSIT in nodes and "map:users" not in nodes
    assert len(nodes) <= 12
    assert p.js("() => FluxScope.app.graph.roots") == [DEPOSIT]
    f, start, end = definition(data, op)
    assert shown_file(p) == f
    fn_rows = p.js("() => Array.from(document.querySelectorAll('#code-body tr.fn:not(.lens)')).map(r => +r.dataset.line)")
    assert set(range(start, end + 1)) <= set(fn_rows)
    # every own flow is annotated above one of its lines in this file
    for flow in op["inputs"] + op["outputs"]:
        lines = [a for (ff, a, b) in locations(data, flow["evidence"]) if ff == f and b - a < 6]
        pills = p.js("(id) => Array.from(document.querySelectorAll(`#code-body tr.lens .ann[data-k=flow][data-id='${id}']`)).map(a => +a.closest('tr').dataset.line)", flow["id"])
        assert pills and set(pills) <= set(lines), (flow["id"], pills, lines)
    head = p.page.inner_text(f"#code-body tr.lens[data-line='{start}'] .ann[data-k=op]")
    assert op["kind"].upper() in head and "Any account" in head
    p.shot("code-deposit")


def test_source_lines_and_graph_items_highlight_each_other(report):
    p, data = report
    op = by_id(data["operations"])[DEPOSIT]
    open_fn(p, DEPOSIT)
    f = shown_file(p)
    mint = next(x for x in op["outputs"] if not x.get("from"))
    line = next(a for (ff, a, b) in locations(data, mint["evidence"]) if ff == f and b - a < 6)
    edge = p.js("(id) => FluxScope.app.graph.edges.find(e => e.refs.some(r => r.type === 'flow' && r.id === id)).id", mint["id"])
    # hovering the source line emphasises the graph edge it supports
    p.page.hover(f"#code-body tr[data-line='{line}']:not(.lens) td.cd")
    assert edge in p.js("() => Array.from(document.querySelectorAll('#graph .g-edge.flash')).map(e => e.dataset.id)")
    # hovering the graph edge highlights its source line
    p.page.locator(f"#graph .g-edge[data-id='{edge}'] path.hit").dispatch_event("mouseenter")
    assert str(line) in p.js("() => Array.from(document.querySelectorAll('#code-body tr.xhl')).map(r => r.dataset.line)")
    p.page.locator(f"#graph .g-edge[data-id='{edge}'] path.hit").dispatch_event("mouseleave")
    assert not p.js("() => document.querySelectorAll('#code-body tr.xhl').length")
    # clicking the annotation selects the edge and shows the flow in the details
    p.page.click(f"#code-body .ann[data-k=flow][data-id='{mint['id']}']")
    sel = p.js("() => FluxScope.app.state.selection")
    assert sel and sel["edge"]["id"] == edge
    assert "mooToken" in p.details()


def test_pinned_functions_build_the_graph_up(report):
    p, _ = report
    open_fn(p, DEPOSIT)
    p.page.click("#code-tools [data-pin]")
    open_fn(p, WITHDRAW)
    assert p.js("() => FluxScope.app.graph.roots") == [DEPOSIT, WITHDRAW]
    assert {DEPOSIT, WITHDRAW} <= set(p.node_ids())
    assert "pinned" in p.page.get_attribute(f".co-item[data-op='{DEPOSIT}']", "class")
    p.shot("code-pinned")
    p.page.click(f"#code-tools [data-unpin='{DEPOSIT}']")
    p.page.wait_for_timeout(80)
    assert p.js("() => FluxScope.app.graph.roots") == [WITHDRAW]
    p.page.click("[data-testid=code-map]")
    assert p.js("() => FluxScope.app.graph.map") is True
    p.page.click("[data-testid=code-fn]")
    assert p.js("() => FluxScope.app.graph.roots") == [WITHDRAW]


def test_nested_calls_open_level_by_level(report):
    p, data = report
    ops = by_id(data["operations"])
    open_fn(p, DEPOSIT)

    def flows_under(i):
        kids = [o for o in data["operations"] if o.get("parent") == i]
        return len(ops[i]["inputs"]) + len(ops[i]["outputs"]) + sum(flows_under(k["id"]) for k in kids)

    calls = [o for o in data["operations"] if o.get("parent") == DEPOSIT and flows_under(o["id"])]
    deep = next(o for o in calls if any(k.get("parent") == o["id"] and flows_under(k["id"]) for k in data["operations"]))
    grandchildren = [k["id"] for k in data["operations"] if k.get("parent") == deep["id"] and flows_under(k["id"])]
    nodes = set(p.node_ids())
    assert {c["id"] for c in calls} <= nodes and not set(grandchildren) & nodes
    # the CALL mark at the call site selects the call in the graph
    p.page.click(f"#code-body .ann[data-k=call][data-id='{deep['id']}']")
    assert p.js("() => FluxScope.app.state.selection.id") == deep["id"]
    # its handle opens the next level
    p.handle(deep["id"], "expand-op")
    assert set(grandchildren) <= set(p.node_ids())
    # the callee's own source opens from the details
    p.page.click("#details [data-action=open-def]")
    f, start, _ = definition(data, deep)
    assert shown_file(p) == f
    assert p.js("() => !document.getElementById('btn-code-back').disabled")


def test_code_tab_without_embedded_sources(browser, built):
    p = Page(browser, built.as_uri())
    p.page.set_input_files("#file-input", str(FIX / "unseen-topology.json"))
    p.page.wait_for_timeout(200)
    assert p.js("() => FluxScope.app.state.view") != "code"
    p.view("code")
    assert p.js("() => FluxScope.app.graph.map") is True
    assert "embeds no source files" in p.page.inner_text("#code-body")
    first = p.js("() => document.querySelector('[data-testid=code-outline] .co-item').dataset.op")
    open_fn(p, first)
    assert first in p.node_ids()
    p.assert_clean()
    p.close()
