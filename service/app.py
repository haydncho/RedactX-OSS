"""锐消 RedactX 服务：REST 接口与 Web 页。

启动：uvicorn service.app:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from pathlib import Path

from fastapi import Body, Depends, FastAPI, File, Form, Header, HTTPException, Path as PathParam, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from redactx import __version__, review
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

TAGS = [
    {"name": "系统", "description": "服务状态"},
    {"name": "目录", "description": "可选的实体类型、打码样式与场景预设"},
    {"name": "任务", "description": "异步脱敏：提交后轮询状态，完成后下载结果、报告与预览"},
    {"name": "复核", "description": "人工复核：加框、删框、改框后重新打码并重建输出"},
    {"name": "同步", "description": "小文件直接返回脱敏结果"},
]
# 不用 Swagger/ReDoc：它们会从外部 CDN 加载脚本，违反本地运行、不联网的要求。接口文档页见 /docs（web/api.html），机器可读描述见 /openapi.json
app = FastAPI(title="锐消 RedactX", version=__version__, description="病案等文档的本地脱敏服务。全部在本机处理，不调用外部模型。",
              openapi_tags=TAGS, docs_url=None, redoc_url=None)
store = JobStore(settings.data_dir)


def error(status: int, code: str, message: str):
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


def auth(x_api_key: str | None = Header(default=None, description="服务端设置了 REDACTX_API_KEY 时必填")):
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
    v = d.get("verify", "auto")
    o.verify = "auto" if v == "auto" else bool(v)
    o.keep_source = bool(d.get("keep_source", False))
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


@app.get("/v1/health", tags=["系统"], summary="服务状态", description="返回版本号、是否找到打码标签字体、默认渲染分辨率。不需要 API Key。")
def health():
    return {"status": "ok", "version": __version__, "font": bool(settings.font_path), "dpi": settings.render_dpi}


@app.get("/v1/catalog", tags=["目录"], summary="完整目录", description="实体分组、实体类型、打码样式与场景预设。Web 页据此生成设置面板。", dependencies=[Depends(auth)])
def catalog():
    return {"groups": ENTITY_GROUPS, "entities": ENTITIES, "styles": STYLES, "presets": PRESETS}


@app.get("/v1/entities", tags=["目录"], summary="实体类型", description="可遮盖的实体类型：code 用于 options.entities，default 为病案审核场景的默认勾选。", dependencies=[Depends(auth)])
def entities():
    return ENTITIES


@app.get("/v1/styles", tags=["目录"], summary="打码样式", description="七种打码样式。所有样式都先擦除原像素再绘制外观。", dependencies=[Depends(auth)])
def styles():
    return STYLES


@app.post("/v1/jobs", status_code=202, tags=["任务"], summary="提交异步任务",
          description="上传文件，立即返回 job_id，后台排队处理。用 GET /v1/jobs/{job_id} 轮询，status 为 succeeded 后下载结果。上传的原文件处理完立即删除；原件预览图（GET /v1/jobs/{job_id}/preview/{page}?v=before，宽度不超过 1400 像素）与结果一起保留到期；options.keep_source 为 true 时另存打码前的页面供复核删框、改框，复核完成或到期时删除。",
          dependencies=[Depends(auth)])
async def create_job(
    file: UploadFile = File(..., description="PDF、图片或 Word/WPS/Excel/PPT/Markdown/TXT 等文档，按文件头识别真实类型"),
    options: str | None = Form(None, description="脱敏选项，JSON 字符串；不传时用病案审核默认值。字段见接口文档页"),
    password: str | None = Form(None, description="加密 PDF 的打开密码"),
    retention_hours: float | None = Form(None, description="结果保留时长（小时），0.1–168，默认 24"),
):
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


@app.get("/v1/jobs", tags=["任务"], summary="最近任务", description="按提交时间倒序。不含原文件名。", dependencies=[Depends(auth)])
def list_jobs(limit: int = Query(30, description="返回条数，1–100")):
    return store.list(min(max(limit, 1), 100))


@app.get("/v1/jobs/{job_id}", tags=["任务"], summary="任务状态",
         description="status 依次为 queued、running、succeeded 或 failed；progress 为 0–1；完成后 summary 含各类型遮盖数量。", dependencies=[Depends(auth)])
def get_job(job_id: str = PathParam(..., description="提交任务时返回的 job_id")):
    job = store.get(job_id)
    if not job:
        error(404, "NOT_FOUND", "任务不存在或已过期删除")
    return job


@app.get("/v1/jobs/{job_id}/result", tags=["任务"], summary="下载脱敏文件",
         description="PDF 或多页输入输出栅格化重建的 PDF（已清除元数据）；单张图片输出同格式图片。任务未完成返回 409。", dependencies=[Depends(auth)])
def get_result(job_id: str = PathParam(..., description="任务 ID")):
    job = store.get(job_id)
    if not job:
        error(404, "NOT_FOUND", "任务不存在或已过期删除")
    if job["status"] != "succeeded":
        error(409, "NOT_READY", "任务尚未完成")
    out = store.dir(job_id) / "out" / job["summary"]["output"]
    suffix = out.suffix
    return FileResponse(out, filename=f"redacted-{job_id}{suffix}", headers={"Cache-Control": "no-store"})


@app.get("/v1/jobs/{job_id}/report", tags=["任务"], summary="打码报告",
         description="每处遮盖的页码、类型、来源、样式与归一化坐标 box=[x0,y0,x1,y1]（左上角为原点）。不含任何原文。", dependencies=[Depends(auth)])
def get_report(job_id: str = PathParam(..., description="任务 ID")):
    p = store.dir(job_id) / "out" / "report.json"
    if not p.exists():
        error(404, "NOT_FOUND", "报告不存在")
    return JSONResponse(json.loads(p.read_text(encoding="utf-8")), headers={"Cache-Control": "no-store"})


@app.get("/v1/jobs/{job_id}/preview/{page}", tags=["任务"], summary="页面预览图", description="JPEG，宽度不超过 1400 像素。v=before 为原件缩小版，含未脱敏内容，与结果一起保留到期或随任务删除。", dependencies=[Depends(auth)])
def get_preview(job_id: str = PathParam(..., description="任务 ID"), page: int = PathParam(..., description="页码，从 1 开始"),
                v: str = Query("after", description="after 脱敏后，before 原件")):
    if v not in ("before", "after"):
        error(400, "INVALID", "v 只能是 before 或 after")
    p = store.dir(job_id) / "out" / "preview" / f"{v}-{page}.jpg"
    if not p.exists():
        error(404, "NOT_FOUND", "预览不存在")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


def _review_job(job_id: str) -> dict:
    job = store.get(job_id)
    if not job:
        error(404, "NOT_FOUND", "任务不存在或已过期删除")
    if job["status"] != "succeeded":
        error(409, "NOT_READY", "任务尚未完成")
    if not (store.dir(job_id) / "out" / "layout.json").exists():
        error(409, "NOT_EDITABLE", "该任务由旧版本处理，不支持复核")
    return job


@app.put("/v1/jobs/{job_id}/review", tags=["复核"], summary="提交复核后的遮盖框",
         description="body 为 {\"items\": [...]}：本任务全部遮盖框（格式同打码报告的 items；新框只需 page、type、box，style 可省略）。"
                     "服务按改动重新打码受影响的页并重建输出，返回新的报告。提交时 options.keep_source 为 true 的任务可以加框、删框、改框；"
                     "其余任务只能加框，删框或改框返回 409 NOT_EDITABLE。",
         dependencies=[Depends(auth)])
async def put_review(job_id: str = PathParam(..., description="任务 ID"), body: dict = Body(..., description='{"items": [...]}')):
    _review_job(job_id)
    try:
        return await run_in_threadpool(store.review, job_id, review.apply, body.get("items"))
    except review.ReviewError as e:
        error(409 if e.code == "NOT_EDITABLE" else 400, e.code, str(e))


@app.post("/v1/jobs/{job_id}/review/finish", tags=["复核"], summary="复核完成",
          description="立即删除为复核保留的打码前页面。之后只能再加框。", dependencies=[Depends(auth)])
async def finish_review(job_id: str = PathParam(..., description="任务 ID")):
    _review_job(job_id)
    rep = await run_in_threadpool(store.review, job_id, review.finish)
    return rep["review"]


@app.delete("/v1/jobs/{job_id}", tags=["任务"], summary="删除结果", description="立即删除脱敏文件、报告与预览，不等保留时长到期。处理中的任务返回 409。", dependencies=[Depends(auth)])
def delete_job(job_id: str = PathParam(..., description="任务 ID")):
    job = store.get(job_id)
    if job and job["status"] in ("queued", "running"):
        error(409, "BUSY", "任务正在处理，完成后再删除")
    if not store.delete(job_id):
        error(404, "NOT_FOUND", "任务不存在或已删除")
    return {"deleted": True}


@app.post("/v1/redact", tags=["同步"], summary="同步脱敏",
          description="限 10 页、20 MB 以内，直接返回脱敏后的文件；响应头 X-Redact-Counts 为各类型遮盖数量（JSON）。病案等大文件请用 /v1/jobs。",
          dependencies=[Depends(auth)])
async def redact_sync(
    file: UploadFile = File(..., description="待脱敏文件"),
    options: str | None = Form(None, description="脱敏选项，JSON 字符串，同 /v1/jobs"),
    password: str | None = Form(None, description="加密 PDF 的打开密码"),
):
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


@app.get("/docs", include_in_schema=False)
def api_docs():
    return FileResponse(WEB_DIR / "api.html", headers={"Cache-Control": "no-cache"})


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})
