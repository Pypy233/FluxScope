import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BEEFY = ROOT / "corpus" / "beefy-erc4626"
FIXTURE = ROOT / "corpus" / "fixtures" / "receiver-vault"


def analyze(path: Path, config=None):
    from fluxscope.analyze import analyze_project

    return analyze_project(path, config).to_json_dict()


def mutate(src: Path, dst: Path, file: str, old: str, new: str) -> Path:
    """Copy a project and replace exactly one occurrence of ``old`` in ``file``."""

    if not dst.exists():
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("out", "cache"))
    p = dst / file
    text = p.read_text()
    assert text.count(old) == 1, f"mutation anchor not unique in {file}: {old!r}"
    p.write_text(text.replace(old, new))
    return dst


@pytest.fixture(scope="session")
def beefy():
    return analyze(BEEFY)


@pytest.fixture(scope="session")
def fixture_result():
    return analyze(FIXTURE)


def by_id(items):
    return {x["id"]: x for x in items}


# ---------------------------------------------------------------------------
# viewer (headless Chrome): one build and one Playwright instance per session
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def built():
    subprocess.run([sys.executable, str(ROOT / "frontend" / "build.py"), "--check"], check=True, cwd=ROOT)
    (ROOT / "dist" / "screenshots").mkdir(parents=True, exist_ok=True)
    return ROOT / "dist" / "fluxscope.html"


@pytest.fixture(scope="session")
def browser(built):
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"Chrome not available for Playwright: {exc}")
        yield b
        b.close()
