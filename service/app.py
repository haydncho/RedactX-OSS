"""锐消 RedactX 服务：REST 接口与 Web 页。

启动：uvicorn service.app:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from redactx import __version__
from redactx.catalog import ENTITIES, ENTITY_BY_CODE, ENTITY_GROUPS, PRESETS, STYLE_CODES, STYLES
from redactx.config import settings
from redactx.convert import OFFICE_KINDS
from redactx.ingest import InputError, count_pages, sniff
from redactx.pipeline import Options, run

from .jobs import JobStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("redactx.api")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
EXT = {"pdf": "pdf", "jpeg": "jpg", "png": "png", "tiff": "tif", "bmp": "bmp", "webp": "webp", **{k: k for k in OFFICE_KINDS}}

# 关闭 Swagger/ReDoc 页面：它们会从外部 CDN 加载脚本，违反本地运行、不联网的要求。接口描述见 /openapi.json
app = FastAPI(title="锐消 RedactX", version=__version__, description="病案等文档的本地脱敏服务。全部在本机处理，不调用外部模型。", docs_url=None, redoc_url=None)
store = JobStore(settings.data_dir)


def error(status: int, code: str, message: str):
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


def auth(x_api_key: str | None = Header(default=None)):
    if settings.api_key and x_api_key != settings.api_key:
        error(401, "BAD_API_KEY", "API Key 无效")


def parse_options(raw: str | None, password: str | None) -> Options:
    o = Options(dpi=settings.render_dpi)
    if not raw:
        return o
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        error(400, "INVALID_OPTIONS", "options 不是合法的 JSON")
    if "entities" in d:
        bad = [e for e in d["entities"] if e not in ENTITY_BY_CODE]
        if bad:
            error(400, "INVALID_OPTIONS", f"不支持的实体类型：{', '.join(bad)}")
        o.entities = set(d["entities"])
    if "default_style" in d:
        if d["default_style"] not in STYLE_CODES:
            error(400, "INVALID_OPTIONS", "default_style 不受支持")
        o.default_style = d["default_style"]
    if "styles" in d:
        for k, v in d["styles"].items():
            if k not in ENTITY_BY_CODE or v not in STYLE_CODES:
                error(400, "INVALID_OPTIONS", f"styles 中的 {k}: {v} 不受支持")
        o.styles = dict(d["styles"])
    if "custom_words" in d:
        o.custom_words = [w.strip() for w in d["custom_words"] if isinstance(w, str) and len(w.strip()) >= 2][:500]
    if d.get("mode") in ("strict", "balanced"):
        o.mode = d["mode"]
    if d.get("label_text") in ("type", "alias"):
        o.label_text = d["label_text"]
    if d.get("dpi") in (150, 200, 300):
        o.dpi = d["dpi"]
    o.verify = bool(d.get("verify", False))
    o.password = password or None
    return o


async def save_upload(file: UploadFile) -> tuple[Path, str]:
    tmp = Path(tempfile.mkstemp(dir=settings.data_dir, suffix=".upload")[1])
    size = 0
    limit = settings.max_upload_mb * 1024 * 1024
    with tmp.open("wb") as fh:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                fh.close()
                tmp.unlink(missing_ok=True)
                error(413, "TOO_LARGE", f"文件超过 {settings.max_upload_mb} MB")
            fh.write(chunk)
    try:
        kind = sniff(tmp, Path(file.filename or "").suffix)
    except InputError as e:
        tmp.unlink(missing_ok=True)
        error(400, e.code, str(e))
    typed = tmp.with_suffix("." + EXT[kind])
    tmp.rename(typed)
    return typed, EXT[kind]


@app.exception_handler(HTTPException)
async def http_error(_, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "ERROR", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": detail})


@app.get("/v1/health")
def health():
    return {"status": "ok", "version": __version__, "font": bool(settings.font_path), "dpi": settings.render_dpi}


@app.get("/v1/catalog", dependencies=[Depends(auth)])
def catalog():
    return {"groups": ENTITY_GROUPS, "entities": ENTITIES, "styles": STYLES, "presets": PRESETS}


@app.get("/v1/entities", dependencies=[Depends(auth)])
def entities():
    return ENTITIES


@app.get("/v1/styles", dependencies=[Depends(auth)])
def styles():
    return STYLES


@app.post("/v1/jobs", status_code=202, dependencies=[Depends(auth)])
async def create_job(file: UploadFile = File(...), options: str | None = Form(None), password: str | None = Form(None), retention_hours: float | None = Form(None)):
    """提交异步脱敏任务。options 为 JSON 字符串，字段见 /docs。"""
    opts = parse_options(options, password)
    tmp, ext = await save_upload(file)
    try:
        _, pages = count_pages(tmp, opts.password, settings.max_pages)
    except InputError as e:
        tmp.unlink(missing_ok=True)
        error(400, e.code, str(e))
    hours = settings.retention_hours if retention_hours is None else max(0.1, min(float(retention_hours), 24 * 7))
    job_id = store.create(tmp, ext, opts, hours, pages or None)
    return {"job_id": job_id, "pages": pages or None, "status": "queued"}


@app.get("/v1/jobs", dependencies=[Depends(auth)])
def list_jobs(limit: int = 30):
    return store.list(min(max(limit, 1), 100))


@app.get("/v1/jobs/{job_id}", dependencies=[Depends(auth)])
def get_job(job_id: str):
    job = store.get(job_id)
    if not job:
        error(404, "NOT_FOUND", "任务不存在或已过期删除")
    return job


@app.get("/v1/jobs/{job_id}/result", dependencies=[Depends(auth)])
def get_result(job_id: str):
    job = store.get(job_id)
    if not job:
        error(404, "NOT_FOUND", "任务不存在或已过期删除")
    if job["status"] != "succeeded":
        error(409, "NOT_READY", "任务尚未完成")
    out = store.dir(job_id) / "out" / job["summary"]["output"]
    suffix = out.suffix
    return FileResponse(out, filename=f"redacted-{job_id}{suffix}", headers={"Cache-Control": "no-store"})


@app.get("/v1/jobs/{job_id}/report", dependencies=[Depends(auth)])
def get_report(job_id: str):
    p = store.dir(job_id) / "out" / "report.json"
    if not p.exists():
        error(404, "NOT_FOUND", "报告不存在")
    return JSONResponse(json.loads(p.read_text(encoding="utf-8")), headers={"Cache-Control": "no-store"})


@app.get("/v1/jobs/{job_id}/preview/{page}", dependencies=[Depends(auth)])
def get_preview(job_id: str, page: int, v: str = "after"):
    if v not in ("before", "after"):
        error(400, "INVALID", "v 只能是 before 或 after")
    p = store.dir(job_id) / "out" / "preview" / f"{v}-{page}.jpg"
    if not p.exists():
        error(404, "NOT_FOUND", "预览不存在")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.delete("/v1/jobs/{job_id}", dependencies=[Depends(auth)])
def delete_job(job_id: str):
    job = store.get(job_id)
    if job and job["status"] in ("queued", "running"):
        error(409, "BUSY", "任务正在处理，完成后再删除")
    if not store.delete(job_id):
        error(404, "NOT_FOUND", "任务不存在或已删除")
    return {"deleted": True}


@app.post("/v1/redact", dependencies=[Depends(auth)])
async def redact_sync(file: UploadFile = File(...), options: str | None = Form(None), password: str | None = Form(None)):
    """同步脱敏：限 10 页、20 MB 以内，直接返回脱敏后的文件。病案文件请用 /v1/jobs。"""
    opts = parse_options(options, password)
    tmp, ext = await save_upload(file)
    work = Path(tempfile.mkdtemp(dir=settings.data_dir, prefix="sync-"))
    try:
        if tmp.stat().st_size > settings.sync_max_mb * 1024 * 1024:
            error(413, "TOO_LARGE", "超过同步接口限制（20 MB），请改用 /v1/jobs")
        _, pages = count_pages(tmp, opts.password, settings.max_pages)
        if pages > settings.sync_max_pages:
            error(413, "TOO_LARGE", "超过同步接口限制（10 页），请改用 /v1/jobs")
        src = work / f"source.{ext}"
        shutil.move(str(tmp), src)
        report = await run_in_threadpool(run, src, work / "out", opts)
        data = (work / "out" / report["output"]).read_bytes()
    except InputError as e:
        error(400, e.code, str(e))
    finally:
        tmp.unlink(missing_ok=True)
        shutil.rmtree(work, ignore_errors=True)
    from fastapi.responses import Response

    media = "application/pdf" if report["output"].endswith(".pdf") else ("image/jpeg" if report["output"].endswith(".jpg") else "image/png")
    return Response(data, media_type=media, headers={"X-Redact-Counts": json.dumps(report["counts"]), "Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})
