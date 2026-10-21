"""File-backed analysis jobs executed in a separate worker process.

Layout per job: ``<data>/<job id>/job.json`` (status, stage, log),
``result.json``, ``report.html`` and, for uploads, ``upload/``.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import traceback
import uuid
import zipfile
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..project import CONFIG_NAME, load_config


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, path)


def run_job(job_dir: str, project: str, llm: str, llm_model: Optional[str], llm_cache: Optional[str]) -> None:
    """Worker entry point (runs in a child process)."""

    jd = Path(job_dir)
    jf = jd / "job.json"

    def update(**kw):
        data = json.loads(jf.read_text())
        log = kw.pop("log", None)
        data.update(kw)
        if log:
            data.setdefault("log", []).append(f"{_now()} {log}")
        _write(jf, data)

    update(status="running", started_at=_now(), log="analysis started")
    try:
        from ..analyze import AnalysisOptions, analyze_project
        from ..report import write_report

        def progress(stage: str, msg: str) -> None:
            update(stage=stage, log=f"[{stage}] {msg}")

        opts = AnalysisOptions(llm=llm, llm_model=llm_model, llm_cache=Path(llm_cache) if llm_cache else None,
                               progress=progress, workdir=jd / "work")
        result = analyze_project(Path(project), options=opts)
        data = result.to_json_dict()
        (jd / "result.json").write_text(json.dumps(data, ensure_ascii=False))
        try:
            write_report(data, jd / "report.html")
        except FileNotFoundError as exc:  # viewer not built
            update(log=f"report skipped: {exc}")
        counts = {k: len(v) for k, v in data.items() if isinstance(v, list)}
        update(status="succeeded", stage="done", finished_at=_now(), counts=counts, log="analysis finished")
    except Exception as exc:  # noqa: BLE001 - report every failure to the client
        update(status="failed", finished_at=_now(), error=f"{type(exc).__name__}: {exc}",
               log="analysis failed:\n" + traceback.format_exc(limit=8))
    finally:
        shutil.rmtree(jd / "work", ignore_errors=True)


class JobManager:
    def __init__(self, data_dir: Path, projects_dir: Path, workers: int = 1, llm_cache: Optional[Path] = None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.projects_dir = Path(projects_dir).resolve()
        self.llm_cache = llm_cache
        self.pool = ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn"))

    # ------------------------------------------------------------ projects
    def projects(self) -> List[Dict[str, str]]:
        out = []
        for cfg in sorted(self.projects_dir.rglob(CONFIG_NAME)):
            if any(part in ("out", "cache", "node_modules", "lib") for part in cfg.parts):
                continue
            root = cfg.parent
            try:
                c = load_config(root)
            except Exception as exc:  # noqa: BLE001
                out.append({"id": root.relative_to(self.projects_dir).as_posix(), "name": root.name,
                            "path": str(root), "description": f"invalid configuration: {exc}"})
                continue
            out.append({"id": root.relative_to(self.projects_dir).as_posix(), "name": c.name, "path": str(root),
                        "description": c.description or "", "commit": c.commit or ""})
        return out

    def resolve_project(self, ref: str) -> Path:
        cand = (self.projects_dir / ref).resolve()
        if os.environ.get("FLUXSCOPE_ALLOW_ANY_PATH") == "1" and Path(ref).is_absolute():
            cand = Path(ref).resolve()
        elif not str(cand).startswith(str(self.projects_dir)):
            raise ValueError("project must be inside the configured projects directory")
        if not (cand / CONFIG_NAME).exists():
            raise ValueError(f"no {CONFIG_NAME} in {ref}")
        return cand

    # ---------------------------------------------------------------- jobs
    def _new(self, project_label: str, llm: str) -> Path:
        jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        jd = self.data_dir / jid
        jd.mkdir(parents=True)
        _write(jd / "job.json", {"id": jid, "status": "queued", "stage": None, "project": project_label, "llm": llm,
                                 "created_at": _now(), "log": [f"{_now()} queued"]})
        return jd

    def submit(self, project_ref: str, llm: str = "none", llm_model: Optional[str] = None) -> Dict[str, Any]:
        path = self.resolve_project(project_ref)
        jd = self._new(project_ref, llm)
        self.pool.submit(run_job, str(jd), str(path), llm, llm_model, str(self.llm_cache) if self.llm_cache else None)
        return self.get(jd.name)

    def submit_upload(self, filename: str, payload: bytes, llm: str = "none", llm_model: Optional[str] = None) -> Dict[str, Any]:
        jd = self._new(filename, llm)
        up = jd / "upload"
        up.mkdir()
        zpath = jd / "upload.zip"
        zpath.write_bytes(payload)
        with zipfile.ZipFile(zpath) as z:
            for name in z.namelist():
                target = (up / name).resolve()
                if not str(target).startswith(str(up.resolve())):
                    raise ValueError(f"unsafe path in archive: {name}")
            z.extractall(up)
        roots = [p.parent for p in up.rglob(CONFIG_NAME)]
        if not roots:
            raise ValueError(f"archive contains no {CONFIG_NAME}")
        root = min(roots, key=lambda p: len(p.parts))
        self.pool.submit(run_job, str(jd), str(root), llm, llm_model, str(self.llm_cache) if self.llm_cache else None)
        return self.get(jd.name)

    def _dir(self, jid: str) -> Path:
        jd = (self.data_dir / jid).resolve()
        if not str(jd).startswith(str(self.data_dir.resolve())) or not (jd / "job.json").exists():
            raise KeyError(jid)
        return jd

    def get(self, jid: str) -> Dict[str, Any]:
        jd = self._dir(jid)
        data = json.loads((jd / "job.json").read_text())
        data["has_result"] = (jd / "result.json").exists()
        data["has_report"] = (jd / "report.html").exists()
        return data

    def list(self) -> List[Dict[str, Any]]:
        out = []
        for jd in sorted(self.data_dir.iterdir(), reverse=True):
            if (jd / "job.json").exists():
                d = json.loads((jd / "job.json").read_text())
                d.pop("log", None)
                out.append(d)
        return out[:50]

    def file(self, jid: str, name: str) -> Path:
        p = self._dir(jid) / name
        if not p.exists():
            raise FileNotFoundError(name)
        return p

    def shutdown(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)
