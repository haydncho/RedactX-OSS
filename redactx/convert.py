"""文档格式转换：Word、WPS、RTF、ODT、Excel、PPT、Markdown、纯文本 -> PDF。

全部在本机用 LibreOffice 无界面模式完成，不联网。转换后的 PDF 带文字层，
随后走与其他 PDF 相同的脱敏流程，输出统一为栅格化重建的 PDF。
"""

from __future__ import annotations

import html
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

# 需要先转换的格式（按真实类型，而不是扩展名）
OFFICE_KINDS = {"doc", "docx", "wps", "rtf", "odt", "xls", "xlsx", "et", "ods", "ppt", "pptx", "dps", "odp", "md", "txt"}

_SOFFICE_CANDIDATES = [
    os.environ.get("REDACTX_SOFFICE", ""),
    "/opt/homebrew/bin/soffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/bin/soffice",
    "/usr/bin/libreoffice",
]

_CSS = """
body { font-family: "PingFang SC", "Hiragino Sans GB", "Noto Sans CJK SC", sans-serif; font-size: 11pt; line-height: 1.7; color: #111; }
h1, h2, h3 { line-height: 1.35; }
table { border-collapse: collapse; }
td, th { border: 1px solid #888; padding: 3px 8px; }
pre, code { font-family: "Menlo", "Noto Sans Mono CJK SC", monospace; font-size: 10pt; white-space: pre-wrap; }
"""


class ConvertError(RuntimeError):
    pass


def soffice_path() -> str | None:
    for p in _SOFFICE_CANDIDATES:
        if p and Path(p).exists():
            return p
    return shutil.which("soffice")


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ConvertError("文本编码无法识别")


def _text_to_html(path: Path, kind: str, dst: Path) -> Path:
    text = _read_text(path)
    if kind == "md":
        import markdown

        body = markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists"])
    else:
        body = f"<pre>{html.escape(text)}</pre>"
    dst.write_text(f'<!doctype html><html><head><meta charset="utf-8"><style>{_CSS}</style></head><body>{body}</body></html>', encoding="utf-8")
    return dst


def to_pdf(src: Path, kind: str, work_dir: Path, timeout: int = 240) -> Path:
    """把文档转成 PDF，返回 PDF 路径。"""
    soffice = soffice_path()
    if not soffice:
        raise ConvertError("本机未安装 LibreOffice，无法处理 Word、WPS 等文档")
    work_dir.mkdir(parents=True, exist_ok=True)
    # 扩展名决定 LibreOffice 选用的导入过滤器，按真实类型重新命名
    ext = {"wps": "doc", "et": "xls", "dps": "ppt"}.get(kind, kind)
    if kind in ("md", "txt"):
        inp = _text_to_html(src, kind, work_dir / "source.html")
        convert_to = "pdf:writer_web_pdf_Export"
    else:
        inp = work_dir / f"source.{ext}"
        shutil.copyfile(src, inp)
        convert_to = "pdf"
    # 每次转换使用独立的配置目录，允许并发且互不干扰
    profile = Path(tempfile.gettempdir()) / f"redactx-lo-{uuid.uuid4().hex}"
    cmd = [
        soffice,
        f"-env:UserInstallation=file://{profile}",
        "--headless", "--norestore", "--nolockcheck", "--nodefault", "--nologo",
        "--convert-to", convert_to,
        "--outdir", str(work_dir),
        str(inp),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, env={**os.environ, "HOME": str(profile)})
    except subprocess.TimeoutExpired as e:
        raise ConvertError("文档转换超时") from e
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    out = work_dir / (inp.stem + ".pdf")
    if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        raise ConvertError("文档转换失败，文件可能已损坏或格式不受支持")
    # 转换用的副本不再需要
    inp.unlink(missing_ok=True)
    return out
