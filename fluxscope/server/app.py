"""FastAPI application: projects, analysis jobs and the viewer.

``python -m fluxscope serve`` starts it. Analyses run in a worker process; the
viewer served at ``/`` is the same single-file app as the offline report, with
its API slot pointing at ``/api``.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from .. import __version__
from ..model import json_schema
from .jobs import JobManager

ROOT = Path(__file__).resolve().parent.parent.parent


class JobRequest(BaseModel):
    project: str
    llm: str = "none"
    llm_model: Optional[str] = None


def _examples():
    ex = []
    for p in sorted((ROOT / "examples").glob("*.json")):
        try:
            ex.append(json.loads(p.read_text()))
        except json.JSONDecodeError:
            continue
    return ex


def create_app(projects_dir: Path, data_dir: Path, workers: int = 1, llm_cache: Optional[Path] = None) -> FastAPI:
    jobs = JobManager(data_dir, projects_dir, workers=workers, llm_cache=llm_cache)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        jobs.shutdown()

    app = FastAPI(title="FluxScope", version=__version__, lifespan=lifespan)
    app.state.jobs = jobs

    @app.get("/", response_class=HTMLResponse)
    def index():
        from ..report import render_html

        try:
            return render_html(_examples(), mode="app", api="/api")
        except FileNotFoundError as exc:
            raise HTTPException(503, str(exc))

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/api/schema")
    def schema():
        return json_schema()

    @app.get("/api/projects")
    def projects():
        return jobs.projects()

    @app.get("/api/jobs")
    def list_jobs():
        return jobs.list()

    @app.post("/api/jobs", status_code=202)
    def create_job(req: JobRequest):
        if req.llm not in ("none", "openai", "anthropic", "replay"):
            raise HTTPException(400, f"unknown llm provider '{req.llm}'")
        try:
            return jobs.submit(req.project, req.llm, req.llm_model)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.post("/api/jobs/upload", status_code=202)
    async def upload_job(file: UploadFile = File(...), llm: str = Form("none")):
        payload = await file.read()
        try:
            return jobs.submit_upload(file.filename or "upload.zip", payload, llm)
        except (ValueError, OSError) as exc:
            raise HTTPException(400, str(exc))
        except Exception as exc:  # zipfile.BadZipFile etc.
            raise HTTPException(400, f"could not read archive: {exc}")

    @app.get("/api/jobs/{jid}")
    def get_job(jid: str):
        try:
            return jobs.get(jid)
        except KeyError:
            raise HTTPException(404, "unknown job")

    @app.get("/api/jobs/{jid}/result")
    def get_result(jid: str):
        try:
            return FileResponse(jobs.file(jid, "result.json"), media_type="application/json")
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, "no result for this job")

    @app.get("/api/jobs/{jid}/report", response_class=HTMLResponse)
    def get_report(jid: str):
        try:
            return FileResponse(jobs.file(jid, "report.html"), media_type="text/html",
                                filename=f"fluxscope-report-{jid}.html")
        except (KeyError, FileNotFoundError):
            raise HTTPException(404, "no report for this job")

    return app


def run(host: str, port: int, projects_dir: Path, data_dir: Path) -> None:  # pragma: no cover
    import uvicorn

    cache = os.environ.get("FLUXSCOPE_LLM_CACHE")
    app = create_app(projects_dir, data_dir, llm_cache=Path(cache) if cache else None)
    print(f"FluxScope {__version__}: http://{host}:{port}/  (projects: {projects_dir})")
    uvicorn.run(app, host=host, port=port)
