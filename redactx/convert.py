"""文档格式转换：Word、WPS、RTF、ODT、Excel、PPT、Markdown、纯文本 -> PDF。

全部在本机用 LibreOffice 无界面模式完成，不联网。转换后的 PDF 带文字层，
随后走与其他 PDF 相同的脱敏流程，输出统一为栅格化重建的 PDF。
"""

from __future__ import annotations

import html
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from html.parser import HTMLParser
from pathlib import Path

log = logging.getLogger("redactx.convert")

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


# Markdown 允许内嵌原始 HTML。渲染后按白名单重建：只保留 Markdown 本身会生成的标签，
# 图片只接受 data: 内嵌（file://、http:// 会让 LibreOffice 读取本机文件或访问网络），script/style 连内容一起丢弃
_ALLOWED = {"p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "blockquote", "pre", "code", "em", "strong",
            "b", "i", "del", "sup", "sub", "table", "thead", "tbody", "tr", "th", "td", "a", "img"}
_VOID = {"br", "hr", "img"}
_DROP_CONTENT = {"script", "style", "iframe", "object", "embed", "svg", "math", "template", "noscript", "textarea", "select"}


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _DROP_CONTENT:
            self.skip += tag not in _VOID
            return
        if self.skip or tag not in _ALLOWED:
            return
        keep = []
        for k, v in attrs:
            v = v or ""
            if tag == "img" and k == "src" and v.startswith("data:image/"):
                keep.append((k, v))
            elif tag == "img" and k == "alt":
                keep.append((k, v))
            elif tag in ("td", "th") and k in ("align", "colspan", "rowspan"):
                keep.append((k, v))
        if tag == "img" and not any(k == "src" for k, _ in keep):
            alt = next((v for k, v in keep if k == "alt"), "")
            self.out.append(html.escape(f"[图片：{alt}]" if alt else "[图片]"))
            return
        self.out.append(f"<{tag}" + "".join(f' {k}="{html.escape(v)}"' for k, v in keep) + ">")

    def handle_endtag(self, tag):
        if tag in _DROP_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if not self.skip and tag in _ALLOWED and tag not in _VOID:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data, quote=False))


def _sanitize(fragment: str) -> str:
    s = _Sanitizer()
    s.feed(fragment)
    s.close()
    return "".join(s.out)


def _text_to_html(path: Path, kind: str, dst: Path) -> Path:
    text = _read_text(path)
    if kind == "md":
        import markdown

        body = _sanitize(markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists"]))
    else:
        body = f"<pre>{html.escape(text)}</pre>"
    dst.write_text(f'<!doctype html><html><head><meta charset="utf-8"><style>{_CSS}</style></head><body>{body}</body></html>', encoding="utf-8")
    return dst


# LibreOffice 解析的是不可信文档（可能引用本机文件或网络资源）。有条件时放进沙箱运行：
# 新的用户、网络、挂载命名空间（unshare -rnm，不需要 root）：网络命名空间里没有任何网卡，无法联网；
# 本次的工作目录先绑定到单独的挂载点，再用空的 tmpfs 盖住整个数据目录，转换进程看不到其他任务的文件与数据库
_SANDBOX_SH = '''set -e
w="$1"; m="$2"; hide="$3"; shift 3
mount --bind "$w" "$m"
if [ -d "$hide" ]; then mount -t tmpfs -o size=1m,mode=0755 tmpfs "$hide"; fi
cd "$m"
exec "$@"
'''
_sandbox_state: bool | None = None


def _sandbox_ok() -> bool:
    """本机能否创建沙箱（Linux 且允许非特权用户命名空间）。只探测一次。"""
    global _sandbox_state
    if _sandbox_state is None:
        ok = False
        if shutil.which("unshare"):
            try:
                ok = subprocess.run(["unshare", "-rnm", "sh", "-c", "mount -t tmpfs tmpfs /mnt 2>/dev/null || mount -t tmpfs tmpfs /tmp"],
                                    capture_output=True, timeout=10).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                ok = False
        if not ok:
            log.warning("无法为文档转换创建沙箱（需要 Linux 与非特权用户命名空间），LibreOffice 将直接运行")
        _sandbox_state = ok
    return _sandbox_state


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
    from .config import settings

    sandbox = _sandbox_ok()
    if not sandbox and settings.require_sandbox:
        raise ConvertError("本机无法为文档转换创建沙箱，已按配置（REDACTX_REQUIRE_SANDBOX）拒绝转换 Word 等文档；PDF 与图片不受影响")
    mount = Path(tempfile.mkdtemp(prefix="redactx-sbx-")) if sandbox else None
    base = mount if sandbox else work_dir
    cmd = [
        soffice,
        f"-env:UserInstallation=file://{profile}",
        "--headless", "--norestore", "--nolockcheck", "--nodefault", "--nologo",
        "--convert-to", convert_to,
        "--outdir", str(base),
        str(base / inp.name),
    ]
    if sandbox:
        cmd = ["unshare", "-rnm", "sh", "-c", _SANDBOX_SH, "sh", str(work_dir.resolve()), str(mount), str(Path(settings.data_dir).resolve()), *cmd]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, env={**os.environ, "HOME": str(profile)})
    except subprocess.TimeoutExpired as e:
        raise ConvertError("文档转换超时") from e
    finally:
        shutil.rmtree(profile, ignore_errors=True)
        if mount:
            shutil.rmtree(mount, ignore_errors=True)
    out = work_dir / (inp.stem + ".pdf")
    if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        raise ConvertError("文档转换失败，文件可能已损坏或格式不受支持")
    # 转换用的副本不再需要
    inp.unlink(missing_ok=True)
    return out
