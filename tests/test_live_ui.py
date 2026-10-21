"""Full stack: the served viewer runs a real analysis through the API and shows it."""

import socket
import threading
import time

import pytest

from .conftest import ROOT

pytestmark = [pytest.mark.slow, pytest.mark.e2e]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    uvicorn = pytest.importorskip("uvicorn")
    from fluxscope.report import TEMPLATE
    from fluxscope.server.app import create_app

    if not TEMPLATE.exists():
        pytest.skip("viewer template not built")
    app = create_app(ROOT / "corpus", tmp_path_factory.mktemp("jobs"))
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(timeout=10)
    app.state.jobs.shutdown()


def _drive(server, out):
    """Runs in its own thread: the sync Playwright API needs a thread without an
    active asyncio loop (other browser tests may hold one in the main thread)."""

    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1500, "height": 950})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(server + "/")
            page.wait_for_selector('[data-testid="analyze-project"] option[value="beefy-erc4626"]', state="attached",
                                   timeout=15000)
            page.select_option('[data-testid="analyze-project"]', "beefy-erc4626")
            page.click('[data-testid="analyze-run"]')
            page.wait_for_function("document.body.innerText.toUpperCase().includes('STATIC ANALYSIS')", timeout=120000)
            page.wait_for_timeout(1000)
            out["text"] = page.inner_text("body")
            page.screenshot(path=str(ROOT / "build" / "live-ui.png"))
            browser.close()
            out["errors"] = errors
    except Exception as exc:  # noqa: BLE001 - reported by the test
        out["exception"] = exc


def test_analyze_from_ui(server):
    pytest.importorskip("playwright.sync_api")
    (ROOT / "build").mkdir(exist_ok=True)
    out = {}
    t = threading.Thread(target=_drive, args=(server, out))
    t.start()
    t.join(timeout=300)
    if "exception" in out:
        raise out["exception"]
    assert "STATIC ANALYSIS" in out["text"].upper()
    assert "succeeded" in out["text"]
    assert not out["errors"], out["errors"]
