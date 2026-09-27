"""文件校验、逐页渲染、文字层提取、页内图片对象登记。"""

from __future__ import annotations

import hashlib
import io
import math
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image, ImageSequence

from .schemas import Char, ImageObject, Line, PageData


# PDFium 不是线程安全的：所有调用都必须串行
PDFIUM_LOCK = threading.RLock()
# 单页像素上限：A3 在 600 DPI 下约 7000 万像素。超过的多半是恶意构造的超大页面或解压炸弹，直接拒绝
MAX_PAGE_PIXELS = 80_000_000
Image.MAX_IMAGE_PIXELS = MAX_PAGE_PIXELS


def _check_pixels(w: float, h: float, i: int) -> None:
    if w * h > MAX_PAGE_PIXELS:
        raise InputError("TOO_LARGE", f"第 {i + 1} 页尺寸过大（约 {w * h / 1e6:.0f} 百万像素，上限 {MAX_PAGE_PIXELS // 1_000_000} 百万），请降低分辨率或拆分后重试")


class InputError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


MAGIC = [
    (b"%PDF-", "pdf"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"II*\x00", "tiff"),
    (b"MM\x00*", "tiff"),
    (b"BM", "bmp"),
]


OLE_EXTS = {"doc", "wps", "xls", "et", "ppt", "dps"}
ZIP_DIRS = {"word/": "docx", "xl/": "xlsx", "ppt/": "pptx"}
ODF_TYPES = {"application/vnd.oasis.opendocument.text": "odt", "application/vnd.oasis.opendocument.spreadsheet": "ods", "application/vnd.oasis.opendocument.presentation": "odp"}


def sniff(path: Path, ext_hint: str = "") -> str:
    """按文件头判断真实类型，不信任扩展名。扩展名只用于区分同一容器格式下的变体（如 doc 与 wps）。"""
    ext_hint = ext_hint.lower().lstrip(".")
    with path.open("rb") as fh:  # 只读文件头，不把整个文件读进内存
        head = fh.read(16)
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    for magic, kind in MAGIC:
        if head.startswith(magic):
            return kind
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):  # OLE 复合文档：doc、wps、xls、ppt 等
        return ext_hint if ext_hint in OLE_EXTS else "doc"
    if head.startswith(b"PK\x03\x04"):
        import zipfile

        try:
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                if "mimetype" in names:
                    with z.open("mimetype") as m:  # 限长读取，防压缩炸弹
                        mt = m.read(128).decode("ascii", "ignore").strip()
                    if mt in ODF_TYPES:
                        return ODF_TYPES[mt]
                for prefix, kind in ZIP_DIRS.items():
                    if any(n.startswith(prefix) for n in names):
                        # 新版 WPS 也会以 OOXML 结构保存 .wps/.et/.dps
                        return {"docx": "docx", "xlsx": "xlsx", "pptx": "pptx"}[kind]
        except zipfile.BadZipFile:
            pass
        raise InputError("INVALID_FILE", "压缩包格式的文件无法识别为 Office 文档")
    if head.startswith(b"{\\rtf"):
        return "rtf"
    if ext_hint in ("md", "markdown", "txt", "text"):
        with path.open("rb") as fh:
            sample = fh.read(65536)
        if b"\x00" not in sample:
            for enc in ("utf-8", "gb18030"):
                try:
                    sample.decode(enc)
                    return "md" if ext_hint in ("md", "markdown") else "txt"
                except UnicodeDecodeError as e:
                    if e.start > len(sample) - 4:  # 截断在多字节字符中间
                        return "md" if ext_hint in ("md", "markdown") else "txt"
    raise InputError("INVALID_FILE", "文件类型不受支持。支持 PDF、图片、Word、WPS、RTF、ODT、Excel、PPT、Markdown 与纯文本")


@dataclass
class SourceDoc:
    kind: str
    page_count: int
    page_sizes_pt: list[tuple[float, float]]  # PDF 页面尺寸（磅）；图片为 None 时按像素


def open_doc(path: Path, password: str | None, max_pages: int) -> SourceDoc:
    kind = sniff(path)
    if kind == "pdf":
        with PDFIUM_LOCK:
            try:
                pdf = pdfium.PdfDocument(str(path), password=password)
            except pdfium.PdfiumError as e:
                if "password" in str(e).lower():
                    raise InputError("ENCRYPTED_PDF", "PDF 已加密，请提供密码") from e
                raise InputError("INVALID_FILE", "PDF 文件损坏或无法解析") from e
            n = len(pdf)
            sizes = [pdf[i].get_size() for i in range(n)]
            pdf.close()
    else:
        with Image.open(path) as im:
            n = getattr(im, "n_frames", 1)
            sizes = []
            for fr in ImageSequence.Iterator(im):
                dpi = fr.info.get("dpi", (200, 200))[0] or 200
                sizes.append((fr.width * 72 / dpi, fr.height * 72 / dpi))
    if n == 0:
        raise InputError("INVALID_FILE", "文件没有可处理的页面")
    if n > max_pages:
        raise InputError("TOO_LARGE", f"页数 {n} 超过上限 {max_pages}")
    return SourceDoc(kind=kind, page_count=n, page_sizes_pt=sizes)


def count_pages(path: Path, password: str | None, max_pages: int) -> tuple[str, int]:
    """接收上传时快速校验与计页。用 pikepdf，不占用 PDFium，避免与后台渲染冲突。"""
    import pikepdf

    from .convert import OFFICE_KINDS

    kind = sniff(path, path.suffix)
    if kind in OFFICE_KINDS:
        return kind, 0  # 页数在转换成 PDF 之后才知道
    if kind != "pdf":
        with Image.open(path) as im:
            n = getattr(im, "n_frames", 1)
            if n > max_pages:
                raise InputError("TOO_LARGE", f"页数 {n} 超过上限 {max_pages}")
            for i, fr in enumerate(ImageSequence.Iterator(im)):
                _check_pixels(fr.width, fr.height, i)
    else:
        try:
            with pikepdf.open(path, password=password or "") as pdf:
                n = len(pdf.pages)
        except pikepdf.PasswordError as e:
            raise InputError("ENCRYPTED_PDF", "PDF 已加密，请提供密码") from e
        except Exception as e:  # noqa: BLE001
            raise InputError("INVALID_FILE", "PDF 文件损坏或无法解析") from e
    if n == 0:
        raise InputError("INVALID_FILE", "文件没有可处理的页面")
    if n > max_pages:
        raise InputError("TOO_LARGE", f"页数 {n} 超过上限 {max_pages}")
    return kind, n


def _group_lines(chars: list[Char]) -> list[Line]:
    """把文字层字符按基线聚成行，行内按 x 排序。"""
    chars = sorted(chars, key=lambda c: ((c.box[1] + c.box[3]) / 2, c.box[0]))
    lines: list[list[Char]] = []
    for c in chars:
        cy = (c.box[1] + c.box[3]) / 2
        h = max(c.box[3] - c.box[1], 1)
        if lines:
            last = lines[-1]
            ly = sum((x.box[1] + x.box[3]) / 2 for x in last) / len(last)
            lh = max(sorted(x.box[3] - x.box[1] for x in last)[len(last) // 2], 1)
            if abs(cy - ly) < 0.5 * max(h, lh):
                last.append(c)
                continue
        lines.append([c])
    out = []
    for ln in lines:
        ln.sort(key=lambda c: c.box[0])
        out.append(Line(chars=ln, source="text"))
    return out


def _text_layer(page: pdfium.PdfPage, scale: float, height_pt: float) -> list[Char]:
    tp = page.get_textpage()
    chars = []
    n = tp.count_chars()
    for i in range(n):
        ch = tp.get_text_range(i, 1)
        if not ch or ch.isspace() or ch in "\r\n￾￿":
            continue
        left, bottom, right, top = tp.get_charbox(i, loose=False)
        lleft, lbottom, lright, ltop = tp.get_charbox(i, loose=True)
        if top - bottom < 0.3 * (ltop - lbottom):
            # 下划线、连字符等细笔画的紧致框很扁（甚至为 0）、还可能在基线以下：纵向改用字号框，
            # 否则会被单独分成一行，“wxid_abc”丢掉“_”，遮盖框也盖不到基线以下
            bottom, top = lbottom, ltop
            if right - left <= 0:
                left, right = lleft, lright
        if right - left <= 0 or top - bottom <= 0:
            continue
        # 斜向文字（多为院名水印）不并入正文行：按基线分行会把它们拆散、混进正文。水印另由图像识别消除
        deg = math.degrees(pdfium_c.FPDFText_GetCharAngle(tp.raw, i)) % 90
        if 8 < deg < 82:
            continue
        chars.append(Char(ch=ch, box=(left * scale, (height_pt - top) * scale, right * scale, (height_pt - bottom) * scale)))
    tp.close()
    return chars


def _image_objects(page: pdfium.PdfPage, scale: float, height_pt: float) -> list[ImageObject]:
    out = []
    for obj in page.get_objects(filter=(pdfium_c.FPDF_PAGEOBJ_IMAGE,), max_depth=4):
        try:
            left, bottom, right, top = obj.get_bounds()
            data = bytes(obj.get_data(decode_simple=False))
            px = obj.get_px_size()
        except Exception:
            continue
        out.append(
            ImageObject(
                rect=(left * scale, (height_pt - top) * scale, right * scale, (height_pt - bottom) * scale),
                px_size=px,
                digest=hashlib.sha1(data).hexdigest()[:16],
            )
        )
    return out


def iter_pages(path: Path, kind: str, dpi: int, password: str | None = None) -> Iterator[tuple[np.ndarray, PageData]]:
    """逐页产出 (RGB 图像, 页面数据)。PDF 一律整页合成渲染，MRC 分层扫描件也不例外。"""
    if kind == "pdf":
        with PDFIUM_LOCK:
            pdf = pdfium.PdfDocument(str(path), password=password)
            n = len(pdf)
        scale = dpi / 72
        try:
            for i in range(n):
                # 只在调用 PDFium 期间持锁，OCR 等耗时步骤在锁外进行
                with PDFIUM_LOCK:
                    page = pdf[i]
                    w_pt, h_pt = page.get_size()
                    _check_pixels(w_pt * scale, h_pt * scale, i)
                    img = page.render(scale=scale, rotation=0, rev_byteorder=True).to_numpy()
                    if img.ndim == 2:
                        img = np.stack([img] * 3, axis=-1)
                    img = np.ascontiguousarray(img[:, :, :3])
                    pd = PageData(index=i, width=img.shape[1], height=img.shape[0])
                    if page.get_rotation() == 0:
                        chars = _text_layer(page, scale, h_pt)
                        if len(chars) >= 20:
                            pd.lines = _group_lines(chars)
                            pd.text_source = "text"
                    pd.images = _image_objects(page, scale, h_pt) if page.get_rotation() == 0 else []
                    page.close()
                yield img, pd
        finally:
            with PDFIUM_LOCK:
                pdf.close()
    else:
        with Image.open(path) as im:
            for i, fr in enumerate(ImageSequence.Iterator(im)):
                rgb = np.array(fr.convert("RGB"))
                yield rgb, PageData(index=i, width=rgb.shape[1], height=rgb.shape[0])


def image_bytes_ok(data: bytes) -> bool:
    try:
        Image.open(io.BytesIO(data)).verify()
        return True
    except Exception:
        return False
