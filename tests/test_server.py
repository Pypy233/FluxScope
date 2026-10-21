"""Web API: list projects, run a real analysis job in the worker, fetch results."""

import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from fluxscope.server.app import create_app
from fluxscope.validate import validate_data

from .conftest import FIXTURE, ROOT

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    app = create_app(ROOT / "corpus", tmp_path_factory.mktemp("jobs"))
    with TestClient(app) as c:
        yield c
    app.state.jobs.shutdown()


def wait(client, jid, timeout=180):
    t = time.time()
    while time.time() - t < timeout:
        j = client.get(f"/api/jobs/{jid}").json()
        if j["status"] in ("succeeded", "failed"):
            return j
        time.sleep(0.5)
    raise AssertionError("job did not finish")


def test_projects(client):
    ids = {p["id"] for p in client.get("/api/projects").json()}
    assert "beefy-erc4626" in ids and "fixtures/receiver-vault" in ids


def test_job_lifecycle(client):
    r = client.post("/api/jobs", json={"project": "beefy-erc4626"})
    assert r.status_code == 202
    jid = r.json()["id"]
    j = wait(client, jid)
    assert j["status"] == "succeeded", j
    stages = " ".join(j["log"])
    for s in ("snapshot", "compile", "facts", "semantics", "check", "assemble"):
        assert f"[{s}]" in stages
    res = client.get(f"/api/jobs/{jid}/result").json()
    _, errs = validate_data(res)
    assert errs == []
    assert res["scope"]["project"]["commit"].startswith("e33f868")


def test_bad_requests(client):
    assert client.post("/api/jobs", json={"project": "../../etc"}).status_code == 400
    assert client.post("/api/jobs", json={"project": "beefy-erc4626", "llm": "magic"}).status_code == 400
    assert client.get("/api/jobs/nope").status_code == 404


def test_upload_zip(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p in FIXTURE.rglob("*"):
            if p.is_file() and "out" not in p.parts and "cache" not in p.parts:
                z.write(p, f"receiver-vault/{p.relative_to(FIXTURE)}")
    r = client.post("/api/jobs/upload", files={"file": ("rv.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 202, r.text
    j = wait(client, r.json()["id"])
    assert j["status"] == "succeeded", j


def test_failed_job_reports_error(client, tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("p/fluxscope.json", '{"name": "broken", "instances": [{"id": "x", "contract": "Missing"}]}')
        z.writestr("p/foundry.toml", '[profile.default]\nsrc = "src"\n')
        z.writestr("p/src/A.sol", "pragma solidity ^0.8.0; contract A {}")
    r = client.post("/api/jobs/upload", files={"file": ("bad.zip", buf.getvalue(), "application/zip")})
    j = wait(client, r.json()["id"])
    assert j["status"] == "failed" and "Missing" in j["error"]
