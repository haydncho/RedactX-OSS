"""锐消 RedactX 服务：REST 接口与 Web 页。

启动：uvicorn service.app:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import hmac
import json
import logging
import time
import uuid
import shutil
import tempfile
from pathlib import Path

from fastapi import Body, Depends, FastAPI, File, Form, Header, HTTPException, Path as PathParam, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask
from fastapi.staticfiles import StaticFiles

from redactx import __version__, review
from redactx.catalog import ENTITIES, ENTITY_BY_CODE, ENTITY_GROUPS, PRESETS, STYLE_CODES, STYLES
from redactx.config import settings
from redactx.convert import OFFICE_KINDS
from redactx.ingest import InputError, count_pages, sniff
from redactx.pipeline import Options, run

from .jobs import JobStore
from .keys import ADMIN, KeyStore, Principal

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
keys = KeyStore(store.db_path)
if settings.allow_export and not settings.api_key:
    log.warning("已设置 REDACTX_ALLOW_EXPORT=1 但没有设置 REDACTX_API_KEY：导出接口不可用。导出含真实病案内容，须同时设置 API Key")


def error(status: int, code: str, message: str):
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


# Key 连续输错的来源暂时拒绝：10 分钟内错 20 次。经 Cloudflare 隧道访问时按 CF-Connecting-IP 计
_FAIL_WINDOW, _FAIL_MAX = 600.0, 20
_fails: dict[str, list[float]] = {}


def _client_ip(request: Request) -> str:
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")


def auth(request: Request, x_api_key: str | None = Header(default=None, description="服务端设置了 REDACTX_API_KEY 或已生成用户 Key 时必填")) -> Principal:
    """返回调用者。管理员 Key 看全部任务；用户 Key 只看自己提交的任务。
    既没设管理员 Key、也没有生成过用户 Key 时是本机模式，不需要 Key。"""
    if not settings.api_key and not keys.active_count():
        return ADMIN
    ip = _client_ip(request)
    now = time.time()
    recent = [t for t in _fails.get(ip, []) if now - t < _FAIL_WINDOW]
    if len(recent) >= _FAIL_MAX:
        error(429, "TOO_MANY_ATTEMPTS", "API Key 错误次数过多，请 10 分钟后再试")
    key = x_api_key or ""
    if settings.api_key and hmac.compare_digest(key.encode(), settings.api_key.encode()):
        return ADMIN
    who = keys.verify(key)
    if who:
        return who
    recent.append(now)
    _fails[ip] = recent
    if len(_fails) > 10000:  # 防止记录无限增长
        _fails.clear()
    error(401, "BAD_API_KEY", "API Key 无效")


def owned(job_id: str, who: Principal) -> dict:
    """取任务并检查归属：不是自己的任务一律按不存在处理（不透露任务是否存在）。"""
    job = store.get(job_id)
    if not job or not (who.admin or job.get("owner") == who.id):
        error(404, "NOT_FOUND", "任务不存在或已过期删除")
    return job


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
    """边读边写到临时文件并计数，超限即停；再按文件头识别类型。任何失败都删除临时文件。"""
    tmp = Path(tempfile.mkstemp(dir=settings.data_dir, suffix=".upload")[1])
    ok = False
    try:
        size = 0
        limit = settings.max_upload_mb * 1024 * 1024
        with tmp.open("wb") as fh:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    error(413, "TOO_LARGE", f"文件超过 {settings.max_upload_mb} MB")
                fh.write(chunk)
        try:
            kind = await run_in_threadpool(sniff, tmp, Path(file.filename or "").suffix)
        except InputError as e:
            error(400, e.code, str(e))
        typed = tmp.with_suffix("." + EXT[kind])
        tmp.rename(typed)
        ok = True
        return typed, EXT[kind]
    finally:
        if not ok:
            tmp.unlink(missing_ok=True)


async def check_pages(tmp: Path, password: str | None) -> int:
    """快速校验与计页（放到线程池，解析大文件时不阻塞其他请求）。失败时删除临时文件。"""
    try:
        _, pages = await run_in_threadpool(count_pages, tmp, password, settings.max_pages)
        return pages
    except InputError as e:
        tmp.unlink(missing_ok=True)
        error(400, e.code, str(e))
    except Exception as e:  # noqa: BLE001  损坏或恶意构造的文件：解析库抛出的各种异常
        tmp.unlink(missing_ok=True)
        log.info("upload rejected: %s", type(e).__name__)
        error(400, "INVALID_FILE", "文件损坏或无法解析")


CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; font-src 'self' data:; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


@app.middleware("http")
async def security_headers(request, call_next):
    """所有响应：禁止被嵌入其他网页、禁止猜测类型、不带来源；页面加 CSP；接口响应默认不缓存（含病案信息）。"""
    resp = await call_next(request)
    h = resp.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "no-referrer")
    if h.get("content-type", "").startswith("text/html"):
        h.setdefault("Content-Security-Policy", CSP)
    if request.url.path.startswith("/v1/"):
        h.setdefault("Cache-Control", "no-store")
    return resp


@app.middleware("http")
async def reject_oversized(request, call_next):
    """上传接口在读请求体之前按 Content-Length 拒绝超限请求，免得先把整个文件写到磁盘；
    没有 Content-Length（分块上传）时由 save_upload 边读边数。留 1 MB 给表单其他字段。"""
    if request.method == "POST" and request.url.path in ("/v1/jobs", "/v1/redact"):
        try:
            n = int(request.headers.get("content-length", "0"))
        except ValueError:
            n = 0
        if n > (settings.max_upload_mb + 1) * 1024 * 1024:
            return JSONResponse(status_code=413, content={"error": {"code": "TOO_LARGE", "message": f"文件超过 {settings.max_upload_mb} MB"}})
    return await call_next(request)


@app.exception_handler(HTTPException)
async def http_error(_, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "ERROR", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": detail})


@app.get("/v1/health", tags=["系统"], summary="服务状态", description="返回版本号、是否找到打码标签字体、默认渲染分辨率、单个文件上传上限（max_upload_mb）。不需要 API Key。")
def health():
    return {"status": "ok", "version": __version__, "font": bool(settings.font_path), "dpi": settings.render_dpi,
            "export": settings.allow_export and bool(settings.api_key), "max_upload_mb": settings.max_upload_mb}


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
          )
async def create_job(
    who: Principal = Depends(auth),
    file: UploadFile = File(..., description="PDF、图片或 Word/WPS/Excel/PPT/Markdown/TXT 等文档，按文件头识别真实类型"),
    options: str | None = Form(None, description="脱敏选项，JSON 字符串；不传时用病案审核默认值。字段见接口文档页"),
    password: str | None = Form(None, description="加密 PDF 的打开密码"),
    retention_hours: float | None = Form(None, description="结果保留时长（小时），0.1–168，默认 24"),
):
    opts = parse_options(options, password)
    tmp, ext = await save_upload(file)
    pages = await check_pages(tmp, opts.password)
    hours = settings.retention_hours if retention_hours is None else max(0.1, min(float(retention_hours), 24 * 7))
    job_id = store.create(tmp, ext, opts, hours, pages or None, owner=who.id, name=file.filename)
    return {"job_id": job_id, "pages": pages or None, "status": "queued"}


@app.get("/v1/jobs", tags=["任务"], summary="最近任务", description="按提交时间倒序，只列当前 API Key 提交的任务（管理员 Key 列出全部）。name 为上传时的原文件名（旧任务可能为空），随任务到期或删除一并删除。")
def list_jobs(limit: int = Query(30, description="返回条数，1–100"), who: Principal = Depends(auth)):
    return store.list(min(max(limit, 1), 100), owner=None if who.admin else who.id)


@app.get("/v1/jobs/{job_id}", tags=["任务"], summary="任务状态",
         description="status 依次为 queued、running、succeeded 或 failed；progress 为 0–1；完成后 summary 含各类型遮盖数量。")
def get_job(job_id: str = PathParam(..., description="提交任务时返回的 job_id"), who: Principal = Depends(auth)):
    job = owned(job_id, who)
    job.pop("owner", None)
    return job


@app.get("/v1/jobs/{job_id}/result", tags=["任务"], summary="下载脱敏文件",
         description="PDF 或多页输入输出栅格化重建的 PDF（已清除元数据）；单张图片输出同格式图片。任务未完成返回 409。")
def get_result(job_id: str = PathParam(..., description="任务 ID"), who: Principal = Depends(auth)):
    job = owned(job_id, who)
    if job["status"] != "succeeded":
        error(409, "NOT_READY", "任务尚未完成")
    out = store.dir(job_id) / "out" / job["summary"]["output"]
    suffix = out.suffix
    return FileResponse(out, filename=f"redacted-{job_id}{suffix}", headers={"Cache-Control": "no-store"})


@app.get("/v1/jobs/{job_id}/report", tags=["任务"], summary="打码报告",
         description="每处遮盖的页码、类型、来源、样式与归一化坐标 box=[x0,y0,x1,y1]（左上角为原点）。不含任何原文。")
def get_report(job_id: str = PathParam(..., description="任务 ID"), who: Principal = Depends(auth)):
    owned(job_id, who)
    p = store.dir(job_id) / "out" / "report.json"
    if not p.exists():
        error(404, "NOT_FOUND", "报告不存在")
    return JSONResponse(json.loads(p.read_text(encoding="utf-8")), headers={"Cache-Control": "no-store"})


@app.get("/v1/jobs/{job_id}/preview/{page}", tags=["任务"], summary="页面预览图", description="JPEG，宽度不超过 1400 像素。v=before 为原件缩小版，含未脱敏内容，与结果一起保留到期或随任务删除。")
def get_preview(job_id: str = PathParam(..., description="任务 ID"), page: int = PathParam(..., description="页码，从 1 开始"),
                v: str = Query("after", description="after 脱敏后，before 原件"), who: Principal = Depends(auth)):
    owned(job_id, who)
    if v not in ("before", "after"):
        error(400, "INVALID", "v 只能是 before 或 after")
    p = store.dir(job_id) / "out" / "preview" / f"{v}-{page}.jpg"
    if not p.exists():
        error(404, "NOT_FOUND", "预览不存在")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


def _review_job(job_id: str, who: Principal) -> dict:
    job = owned(job_id, who)
    if job["status"] != "succeeded":
        error(409, "NOT_READY", "任务尚未完成")
    if not (store.dir(job_id) / "out" / "layout.json").exists():
        error(409, "NOT_EDITABLE", "该任务由旧版本处理，不支持复核")
    return job


@app.put("/v1/jobs/{job_id}/review", tags=["复核"], summary="提交复核后的遮盖框",
         description="body 为 {\"items\": [...]}：本任务全部遮盖框（格式同打码报告的 items；新框只需 page、type、box，style 可省略）。"
                     "服务按改动重新打码受影响的页并重建输出，返回新的报告。提交时 options.keep_source 为 true 的任务可以加框、删框、改框；"
                     "其余任务只能加框，删框或改框返回 409 NOT_EDITABLE。",
         )
async def put_review(job_id: str = PathParam(..., description="任务 ID"), body: dict = Body(..., description='{"items": [...]}'),
                     who: Principal = Depends(auth)):
    _review_job(job_id, who)
    try:
        return await run_in_threadpool(store.review, job_id, review.apply, body.get("items"))
    except review.ReviewError as e:
        error(409 if e.code == "NOT_EDITABLE" else 400, e.code, str(e))


@app.get("/v1/jobs/{job_id}/export", tags=["复核"], summary="导出标注",
         description="打码前的原始页面与复核后的全部框（COCO 格式），供训练检测模型。含真实病案内容：服务端设置 REDACTX_ALLOW_EXPORT=1 才可用，"
                     "且只有保留原件、尚未完成复核的任务可以导出；只有管理员 Key 能导出。")
async def export_annotations(job_id: str = PathParam(..., description="任务 ID"), who: Principal = Depends(auth)):
    if not settings.allow_export:
        error(403, "EXPORT_DISABLED", "未开启标注导出（服务端设置 REDACTX_ALLOW_EXPORT=1 才可用）")
    if not settings.api_key:
        error(403, "EXPORT_NEEDS_KEY", "导出含真实病案内容：开启导出时须同时设置 REDACTX_API_KEY")
    if not who.admin:
        error(403, "EXPORT_ADMIN_ONLY", "导出含真实病案内容，只有管理员 Key 可以导出")
    _review_job(job_id, who)
    out = store.dir(job_id) / "out"
    now = time.time()
    for old in out.glob("export-*.zip"):  # 下载中断时留下的导出包
        if now - old.stat().st_mtime > 600:
            old.unlink(missing_ok=True)
    dest = out / f"export-{uuid.uuid4().hex}.zip"  # 每次单独的文件：同时导出互不覆盖
    try:
        counts = await run_in_threadpool(review.export, out, dest)
    except review.ReviewError as e:
        dest.unlink(missing_ok=True)
        error(409, e.code, str(e))
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    _audit({"event": "export", "job_id": job_id, **counts})
    return FileResponse(dest, media_type="application/zip", filename=f"annotations-{job_id}.zip", headers={"Cache-Control": "no-store"},
                        background=BackgroundTask(dest.unlink, missing_ok=True))


def _audit(entry: dict) -> None:
    """导出审计：只记任务编号、页数、框数与时间，不记任何内容。"""
    entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **entry}
    with open(settings.data_dir / "export-audit.log", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    log.info("annotations exported: %s", entry)


@app.post("/v1/jobs/{job_id}/review/finish", tags=["复核"], summary="复核完成",
          description="立即删除为复核保留的打码前页面。之后只能再加框。")
async def finish_review(job_id: str = PathParam(..., description="任务 ID"), who: Principal = Depends(auth)):
    _review_job(job_id, who)
    rep = await run_in_threadpool(store.review, job_id, review.finish)
    return rep["review"]


@app.delete("/v1/jobs/{job_id}", tags=["任务"], summary="删除结果", description="立即删除脱敏文件、报告与预览，不等保留时长到期。处理中的任务返回 409。")
def delete_job(job_id: str = PathParam(..., description="任务 ID"), who: Principal = Depends(auth)):
    job = owned(job_id, who)
    if job["status"] in ("queued", "running"):
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
            error(413, "TOO_LARGE", f"超过同步接口限制（{settings.sync_max_mb} MB），请改用 /v1/jobs")
        pages = await check_pages(tmp, opts.password)
        if pages > settings.sync_max_pages:
            error(413, "TOO_LARGE", f"超过同步接口限制（{settings.sync_max_pages} 页），请改用 /v1/jobs")
        src = work / f"source.{ext}"
        shutil.move(str(tmp), src)
        report = await run_in_threadpool(run, src, work / "out", opts)
        data = (work / "out" / report["output"]).read_bytes()
    except InputError as e:
        error(400, e.code, str(e))
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001  异常信息里可能带有文件内容片段，日志只记类型
        log.error("sync redact crashed: %s", type(e).__name__)
        error(500, "INTERNAL", "处理失败，请稍后重试或联系管理员")
    finally:
        tmp.unlink(missing_ok=True)
        shutil.rmtree(work, ignore_errors=True)
    from fastapi.responses import Response

    media = "application/pdf" if report["output"].endswith(".pdf") else ("image/jpeg" if report["output"].endswith(".jpg") else "image/png")
    return Response(data, media_type=media, headers={"X-Redact-Counts": json.dumps(report["counts"]), "Cache-Control": "no-store"})


class _RevalidatedStatic(StaticFiles):
    """页面脚本与样式每次使用前向服务端确认（ETag 未变时返回 304，不重新下载），更新后浏览器不会继续用旧文件。"""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/static", _RevalidatedStatic(directory=WEB_DIR), name="static")


@app.get("/docs", include_in_schema=False)
def api_docs():
    return FileResponse(WEB_DIR / "api.html", headers={"Cache-Control": "no-cache"})


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})
