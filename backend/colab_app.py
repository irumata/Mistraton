"""Colab-hosted website backend for Mistralethon / Sentinel-Loop.

One FastAPI app, one port, serving everything the website needs:

  /api/health, /api/db     status-card endpoints (/api/db checks the SQLite
                           labeling store on Colab — no Postgres needed here)
  /sentinel/*              the existing Sentinel-Loop watcher router
                           (health, watch, feedback, alerts)
  /sentinel/upload         multipart upload of an incident bundle into
                           <data_dir>/raw/<incident_id>/ + optional ingest
  /sentinel/archive/prepare   zip a source work dir (default repo work/) into
                           <data_dir>/archive/*.zip with a sha256 manifest
  /sentinel/archive           list archives in <data_dir>/archive/
  /sentinel/archive/download  download one archive zip (?name=...)
  /sentinel/archive/upload    upload an archive zip; verify + extract + ingest
  /sentinel/data           overview of the data dir (records, metrics,
                           artifacts, eval reports)
  /sentinel/eval           latest evaluation report for a strategy
  /sentinel/pipeline/<stage>   run a pipeline stage as a background job
  /sentinel/pipeline/jobs[/<id>]   poll job status / logs
  /sentinel/timestral/chat chat with the fine-tuned self-hosted Timestral
                           checkpoint (GPU on Colab)
  /                        the built React frontend (frontend/dist), SPA fallback

Environment (see sentinel_loop.config.Settings):
  SENTINEL_DATA_DIR, SENTINEL_ARTIFACTS_DIR, MISTRAL_API_KEY, ...
  SENTINEL_DIST_DIR       built frontend dir (default: <repo>/frontend/dist)
  SENTINEL_WEB_USERNAME   browser/CLI login (default safestral)
  SENTINEL_WEB_PASSWORD   required strong password (at least 32 characters)
  SENTINEL_PUBLIC_URL     canonical HTTPS origin for same-origin browser requests
  SENTINEL_TIMESTRAL_HF   fine-tuned checkpoint dir (default <artifacts>/timestral-hf)

Run from backend/:  python colab_app.py --host 0.0.0.0 --port 8000
In a notebook:      app = build_app(); uvicorn.run(app, host="0.0.0.0", port=8000)
                    (in a daemon thread so the cell returns)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from colab_security import ColabAuthMiddleware, web_auth_settings

from sentinel_loop.config import Settings, artifacts_dir_for, db_path_for, resolve_config
from sentinel_loop.serve.investigation import create_investigation_router, investigation_runs
from sentinel_loop.serve.voice import create_voice_router
from sentinel_loop.serve.watcher import create_sentinel_router

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent
REPO_DIR = BACKEND_DIR.parent
DEFAULT_DIST = REPO_DIR / "frontend" / "dist"

# Pipeline stages the website can trigger (CLI subcommands of sentinel_loop).
STAGES = (
    "prepare-archive",
    "apply-archive",
    "ingest",
    "extract-metrics",
    "label-windows",
    "build-dataset",
    "train-detection",
    "train-embedding-head",
    "train-zero-shot",
    "evaluate",
    "shadow-run",
    "finetune-timestral",
)
STAGES_NEEDING_STRATEGY = {"evaluate", "shadow-run"}

# In-memory job registry for pipeline runs.
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

# Lazily loaded fine-tuned Timestral text-generation pipeline.
_timestral_pipe = None
_timestral_lock = threading.Lock()

TIMESTRAL_SYSTEM = (
    "You are Timestral, a time-series security analyst. You receive metric windows "
    "as JSON and answer with a single JSON object: {\"window_state\": one of "
    "normal|precursor|incident|degraded, \"confidence\": 0..1, \"lead_time_minutes\": number, "
    "\"explanation\": short text}. Only you detect; never refuse the analysis."
)


def _settings() -> Settings:
    return Settings()


def _data_dir() -> Path:
    return Path(_settings().data_dir)


def _dist_dir() -> Optional[Path]:
    raw = os.environ.get("SENTINEL_DIST_DIR", "")
    path = Path(raw) if raw else DEFAULT_DIST
    return path if path.is_dir() else None


class PipelineRequest(BaseModel):
    strategy: str = "forecasting"
    args: list[str] = []  # extra CLI args, e.g. ["--epochs", "2"]


class ArchivePrepareRequest(BaseModel):
    src: Optional[str] = None  # source work dir; default: $SENTINEL_WORK_DIR or <repo>/work


class ChatRequest(BaseModel):
    messages: list[dict]  # [{role: system|user|assistant, content: str}]
    max_new_tokens: int = 256
    temperature: float = 0.0


def _run_stage_job(job_id: str, stage: str, extra_args: list[str]) -> None:
    cmd = [sys.executable, "-m", "sentinel_loop", stage, "--data-dir", str(_data_dir()), *extra_args]
    with _jobs_lock:
        _jobs[job_id]["cmd"] = " ".join(cmd)
    logger.info("pipeline job %s: %s", job_id, " ".join(cmd))
    try:
        proc = subprocess.run(cmd, cwd=BACKEND_DIR, capture_output=True, text=True)
        log = (proc.stdout or "") + (proc.stderr or "")
        with _jobs_lock:
            _jobs[job_id].update(
                status="done" if proc.returncode == 0 else "failed",
                returncode=proc.returncode,
                log=log[-20000:],
                finished=time.time(),
            )
    except Exception as exc:  # noqa: BLE001 - job must always terminate
        with _jobs_lock:
            _jobs[job_id].update(status="failed", log=f"{exc}", finished=time.time())


def _get_timestral_pipeline():
    global _timestral_pipe
    with _timestral_lock:
        if _timestral_pipe is not None:
            return _timestral_pipe
        hf_dir = os.environ.get("SENTINEL_TIMESTRAL_HF") or str(
            Path(artifacts_dir_for(_settings())) / "timestral-hf"
        )
        if not Path(hf_dir).is_dir():
            raise HTTPException(
                503,
                f"no fine-tuned Timestral at {hf_dir}; run pipeline stage "
                "'finetune-timestral' first (needs a GPU runtime)",
            )
        try:
            from transformers import pipeline as hf_pipeline
        except ImportError:
            raise HTTPException(503, "transformers not installed; pip install -r requirements-colab.txt") from None
        logger.info("loading fine-tuned Timestral from %s", hf_dir)
        _timestral_pipe = hf_pipeline("text-generation", model=hf_dir, device_map="auto")
        return _timestral_pipe


def build_app() -> FastAPI:
    auth = web_auth_settings()
    settings = _settings()
    runs = investigation_runs(settings.mistral_api_key)

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            runs.close()

    app = FastAPI(title="Mistralethon (Colab)", docs_url="/api/docs", redoc_url=None,
                  openapi_url="/api/openapi.json", lifespan=lifespan)

    # A whole-app ASGI guard also covers static files, future routes and WS.
    # Same-origin UI requests use the browser's authenticated HTTP session.
    app.add_middleware(ColabAuthMiddleware, **auth)

    # --- status card endpoints (compatible with the existing React app) ---
    @app.get("/api/health")
    def health():
        return {"status": "ok", "project": "Mistralethon"}

    @app.get("/api/db")
    def db_health():
        """On Colab there is no Postgres; check the SQLite labeling store instead."""
        db = Path(db_path_for(_settings()))
        if db.exists() or db.parent.is_dir():
            return {"status": "ok", "database": "sqlite", "project": "Mistralethon", "schema_version": 1}
        raise HTTPException(503, "Database unavailable")

    # --- existing Sentinel-Loop watcher (health/watch/feedback/alerts) ---
    app.include_router(create_sentinel_router(settings=settings), prefix="/sentinel", tags=["sentinel-loop"])
    app.include_router(create_investigation_router(runs), prefix="/sentinel", tags=["investigations"])
    app.include_router(create_voice_router(runs, settings.mistral_api_key), prefix="/sentinel", tags=["incident voice"])

    # --- upload incident bundles ---
    @app.post("/sentinel/upload")
    async def upload(
        incident_id: str = Form(...),
        files: list[UploadFile] = File(...),
        ingest: bool = Form(True),
    ):
        safe_id = "".join(c for c in incident_id if c.isalnum() or c in "-_.") or "incident"
        bundle = _data_dir() / "raw" / safe_id
        bundle.mkdir(parents=True, exist_ok=True)
        saved = []
        for f in files:
            rel = Path(f.filename or "file").name  # flatten; keep basename only
            if not rel:
                continue
            dest = bundle / rel
            dest.write_bytes(await f.read())
            saved.append(rel)
        result = {"incident_id": safe_id, "files": saved, "bundle": str(bundle), "ingested": False, "records": 0}
        if ingest:
            from sentinel_loop.ingest.loader import ingest_directory

            records = ingest_directory(_data_dir() / "raw", _data_dir())
            result["ingested"] = True
            result["records"] = len(records)
        return result

    # --- input data archives: prepare / list / download / upload+apply ---
    @app.post("/sentinel/archive/prepare")
    def archive_prepare(req: ArchivePrepareRequest | None = None):
        from sentinel_loop.archive import default_work_dir, make_archive

        src = Path(req.src) if req and req.src else default_work_dir()
        try:
            manifest = make_archive(src, data_dir=_data_dir())
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from None
        return manifest

    @app.get("/sentinel/archive")
    def archive_list():
        from sentinel_loop.archive import archive_dir_for, list_archives

        return {"archive_dir": str(archive_dir_for(_data_dir())), "archives": list_archives(_data_dir())}

    @app.get("/sentinel/archive/download")
    def archive_download(name: str = Query(...)):
        from sentinel_loop.archive import archive_dir_for

        safe = Path(name).name  # basename only
        path = archive_dir_for(_data_dir()) / safe
        if safe != name or not path.is_file():
            raise HTTPException(404, "archive not found")
        return FileResponse(path, media_type="application/zip", filename=safe)

    @app.post("/sentinel/archive/upload")
    async def archive_upload(file: UploadFile = File(...), apply: bool = Form(True)):
        from sentinel_loop.archive import apply_archive, archive_dir_for, read_manifest

        safe = Path(file.filename or "archive.zip").name
        if safe != (file.filename or "") or not safe.endswith(".zip"):
            raise HTTPException(400, "archive file name must be a plain .zip basename")
        adir = archive_dir_for(_data_dir())
        adir.mkdir(parents=True, exist_ok=True)
        dest = adir / safe
        dest.write_bytes(await file.read())
        try:
            if apply:
                return apply_archive(dest, _data_dir(), ingest=True)
            manifest = read_manifest(dest)
            return {"archive": safe, "validated": True, "ingested": False,
                    "file_count": manifest.get("file_count"), "source": manifest.get("source"),
                    "created_at": manifest.get("created_at")}
        except (ValueError, FileNotFoundError) as exc:
            dest.unlink(missing_ok=True)
            raise HTTPException(400, f"invalid sentinel input archive: {exc}") from None

    # --- data overview for the UI ---
    @app.get("/sentinel/data")
    def data_overview():
        data_dir = _data_dir()
        art = Path(artifacts_dir_for(_settings()))
        records = []
        rec_path = data_dir / "records.jsonl"
        if rec_path.exists():
            for line in rec_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    records.append({"incident_id": r.get("incident_id"), "start": r.get("timestamp_start"),
                                    "end": r.get("timestamp_end"), "metrics": len(r.get("metric_sources") or [])})
        metrics = []
        try:
            cfg, cfg_path = resolve_config(data_dir, None)
            metrics = [m.name for m in cfg.metrics]
        except Exception:  # noqa: BLE001 - overview must never 500
            metrics = []
        bundles = sorted(p.name for p in (data_dir / "raw").iterdir()) if (data_dir / "raw").is_dir() else []
        return {
            "data_dir": str(data_dir),
            "artifacts_dir": str(art),
            "records": records,
            "metrics": metrics,
            "raw_bundles": bundles,
            "artifacts": sorted(p.name for p in art.iterdir()) if art.is_dir() else [],
            "timestral_ready": (Path(os.environ.get("SENTINEL_TIMESTRAL_HF", "")) if os.environ.get("SENTINEL_TIMESTRAL_HF") else art / "timestral-hf").is_dir(),
        }

    # --- pipeline stages as background jobs ---
    @app.post("/sentinel/pipeline/{stage}")
    def run_stage(stage: str, req: PipelineRequest | None = None):
        if stage not in STAGES:
            raise HTTPException(404, f"unknown stage; choose from {list(STAGES)}")
        req = req or PipelineRequest()
        extra = list(req.args)
        if stage in STAGES_NEEDING_STRATEGY:
            extra += ["--strategy", req.strategy]
        job_id = uuid.uuid4().hex[:12]
        with _jobs_lock:
            _jobs[job_id] = {"job_id": job_id, "stage": stage, "status": "running",
                             "log": "", "started": time.time()}
        threading.Thread(target=_run_stage_job, args=(job_id, stage, extra), daemon=True).start()
        return {"job_id": job_id, "stage": stage, "status": "running"}

    @app.get("/sentinel/pipeline/jobs")
    def list_jobs():
        with _jobs_lock:
            return {"jobs": [dict(j) for j in _jobs.values()]}

    @app.get("/sentinel/pipeline/jobs/{job_id}")
    def job_status(job_id: str):
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job is None:
                raise HTTPException(404, "unknown job")
            return dict(job)

    # --- latest evaluation report ---
    @app.get("/sentinel/eval")
    def latest_eval(strategy: str = Query("forecasting")):
        art = Path(artifacts_dir_for(_settings()))
        path = art / f"eval_{strategy}.json"
        if not path.exists():
            raise HTTPException(404, f"no eval for strategy {strategy!r} yet; run 'evaluate' first")
        return json.loads(path.read_text(encoding="utf-8"))

    # --- chat with the fine-tuned self-hosted Timestral ---
    @app.post("/sentinel/timestral/chat")
    def timestral_chat(req: ChatRequest):
        pipe = _get_timestral_pipeline()
        messages = [{"role": "system", "content": TIMESTRAL_SYSTEM}, *req.messages]
        tokenizer = getattr(pipe, "tokenizer", None)
        if tokenizer is not None and getattr(tokenizer, "chat_template", None):
            prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        else:
            prompt = "\n".join(f"{m['role']}: {m['content']}" for m in messages) + "\nassistant:"
        out = pipe(prompt, max_new_tokens=req.max_new_tokens, do_sample=req.temperature > 0,
                   temperature=req.temperature or None, return_full_text=False)
        text = out[0]["generated_text"].strip()
        return {"reply": text, "model": os.environ.get("SENTINEL_TIMESTRAL_HF", "timestral-hf")}

    # --- built frontend + SPA fallback ---
    dist = _dist_dir()
    if dist is not None:
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
        index = dist / "index.html"

        def index_html() -> FileResponse:
            return FileResponse(index)

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            # real files in dist root (favicon, etc.) win; everything else -> index.html
            candidate = (dist / full_path).resolve()
            if (not candidate.is_relative_to(dist.resolve())
                    or any(part.startswith(".") for part in Path(full_path).parts)):
                raise HTTPException(404, "Not found")
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return index_html()

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mistralethon Colab website backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(build_app(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
