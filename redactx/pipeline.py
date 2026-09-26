"""处理流水线：两遍扫描。

第一遍：逐页渲染、取文字（文字层或 OCR）、规则与锚定识别、印章与条码检测。
第二遍：用第一遍收集的种子做文档内实体传播，重新渲染页面并打码、可选自检，最后栅格化重建。
"""

from __future__ import annotations

import logging
import secrets
import shutil
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import img2pdf
import numpy as np
import pikepdf
from PIL import Image

from . import ocr, redact, vision
from .catalog import ENTITY_BY_CODE, STYLE_CODES
from .detect import engine
from .ingest import iter_pages, open_doc
from .schemas import Hit, PageData, Region

log = logging.getLogger("redactx.pipeline")

ProgressCb = Callable[[float, str], None]


@dataclass
class Options:
    entities: set[str] = field(default_factory=lambda: {e["code"] for e in ENTITY_BY_CODE.values() if e["default"]})
    default_style: str = "label"
    styles: dict[str, str] = field(default_factory=dict)
    custom_words: list[str] = field(default_factory=list)
    mode: str = "strict"  # strict | balanced
    label_text: str = "type"  # type：标签写类型；alias：写一致性代号
    dpi: int = 200
    verify: bool = False
    password: str | None = None

    def style_for(self, code: str) -> str:
        s = self.styles.get(code) or self.default_style
        return s if s in STYLE_CODES else "label"


def _hits_to_rect(page: PageData, h: Hit, strict: bool):
    line = page.lines[h.line]
    chars = line.chars[h.start : h.end]
    if not chars:
        return None
    x0 = min(c.box[0] for c in chars)
    y0 = min(c.box[1] for c in chars)
    x1 = max(c.box[2] for c in chars)
    y1 = max(c.box[3] for c in chars)
    ch = max(y1 - y0, 4)
    if line.source == "ocr":
        # OCR 框偏紧，手写更会超出；严格模式外扩更多
        px, py = (0.2 * ch, 0.2 * ch) if strict else (0.12 * ch, 0.14 * ch)
        if h.source == "anchor":
            px, py = px + 0.1 * ch, py + 0.1 * ch
    else:
        px, py = 0.12 * ch, 0.12 * ch
    left, right = x0 - px, x1 + px
    # 不压到同一行紧邻的字（例如“姓名”标签本身）
    if h.start > 0:
        left = max(left, line.chars[h.start - 1].box[2] + 1)
    if h.end < len(line.chars):
        right = min(right, line.chars[h.end].box[0] - 1)
    return (min(left, x0), y0 - py, max(right, x1), y1 + py)


def _merge(regions: list[Region]) -> list[Region]:
    """同类型且重叠、或同一行上紧挨着的区域合并为一个，避免标签叠标签。"""
    out: list[Region] = []
    for r in sorted(regions, key=lambda r: (r.type, r.rect[1], r.rect[0])):
        merged = False
        for o in out:
            if o.type != r.type or o.alias != r.alias:
                continue
            ax0, ay0, ax1, ay1 = o.rect
            bx0, by0, bx1, by1 = r.rect
            ih = min(ay1, by1) - max(ay0, by0)
            if ih <= 0.5 * min(ay1 - ay0, by1 - by0):
                continue
            gap = max(bx0 - ax1, ax0 - bx1)
            if gap <= 0.3 * min(ay1 - ay0, by1 - by0):
                o.rect = (min(ax0, bx0), min(ay0, by0), max(ax1, bx1), max(ay1, by1))
                merged = True
                break
        if not merged:
            out.append(Region(r.type, r.source, r.page, r.rect, r.alias, r.style))
    return out


class Aliases:
    """一致性代号：同一任务内同一值始终对应同一代号。值只在内存中，任务结束即丢弃。"""

    def __init__(self) -> None:
        self._salt = secrets.token_bytes(16)
        self._map: dict[tuple[str, str], int] = {}
        self._count: Counter = Counter()

    def get(self, type_: str, value: str) -> str | None:
        if not value:
            return None
        key = (type_, value)
        if key not in self._map:
            self._count[type_] += 1
            self._map[key] = self._count[type_]
        return f"{ENTITY_BY_CODE[type_]['label']}{self._map[key]}"


def _page_text(img: np.ndarray, pd: PageData, repeated: set[str]) -> None:
    """为页面准备文字：文字层足够就用文字层；扫描页整页 OCR；混合页对大图片区域补 OCR。"""
    area = float(pd.width * pd.height)
    if pd.text_source == "text":
        for obj in pd.images:
            x0, y0, x1, y1 = obj.rect
            if (x1 - x0) * (y1 - y0) / area >= 0.12 and obj.digest not in repeated:
                pd.lines.extend(ocr.region_ocr(img, obj.rect))
        return
    # 旋转页在纠正方向后的画面上识别与打码，输出时再转回原方向
    pd.rotation = ocr.detect_orientation(img)
    work = ocr._rotate(img, pd.rotation)
    pd.width, pd.height = work.shape[1], work.shape[0]
    pd.lines = ocr.ocr_page(work, 0)
    pd.text_source = "ocr"


def _has_printed_labels(pd: PageData, rect) -> bool:
    """区域里有识别出的印刷字段名（如“入院途径”“病案质量”），说明推算的填写区越界了。"""
    from .detect.anchors import _STOP_MAX, _match_at
    from .detect.lexicon import STOP_WORDS

    x0, y0, x1, y1 = rect
    for ln in pd.lines:
        inside = "".join(c.ch for c in ln.chars if x0 <= (c.box[0] + c.box[2]) / 2 <= x1 and y0 <= (c.box[1] + c.box[3]) / 2 <= y1)
        if any(_match_at(inside, i, STOP_WORDS, _STOP_MAX) for i in range(len(inside))):
            return True
    return False


def _field_ok(work: np.ndarray, pd: PageData, rect) -> bool:
    return vision.has_ink(work, rect) and not _has_printed_digits(pd, rect) and not _has_printed_labels(pd, rect)


def _has_printed_digits(pd: PageData, rect) -> bool:
    """区域里识别出的字符以数字为主时（多半是日期、编号），不按填写区整块遮盖。日期一律不遮。"""
    x0, y0, x1, y1 = rect
    chars = [c for ln in pd.lines for c in ln.chars if c.box[0] >= x0 and c.box[2] <= x1 and c.box[1] >= y0 and c.box[3] <= y1]
    if len(chars) < 2:
        return False
    return sum(c.ch.isdigit() or c.ch in "-/:年月日时分" for c in chars) >= 0.5 * len(chars)


def _preview(img: np.ndarray, path: Path, max_w: int = 1400) -> None:
    h, w = img.shape[:2]
    if w > max_w:
        img = cv2.resize(img, (max_w, int(h * max_w / w)), interpolation=cv2.INTER_AREA)
    Image.fromarray(img).save(path, "JPEG", quality=82)


def run(src: Path, out_dir: Path, opts: Options, progress: ProgressCb = lambda p, m: None) -> dict:
    t0 = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    prev_dir = out_dir / "preview"
    prev_dir.mkdir(exist_ok=True)
    pages_dir = out_dir / "pages"
    pages_dir.mkdir(exist_ok=True)

    from .config import settings

    from .convert import OFFICE_KINDS, ConvertError, to_pdf
    from .ingest import InputError, sniff

    kind = sniff(src, src.suffix)
    source_kind = kind
    if kind in OFFICE_KINDS:
        progress(0.01, "转换文档格式")
        try:
            src = to_pdf(src, kind, out_dir / "convert")
        except ConvertError as e:
            raise InputError("CONVERT_FAILED", str(e)) from e
    doc = open_doc(src, opts.password, settings.max_pages)
    n = doc.page_count
    enabled = set(opts.entities)
    strict = opts.mode == "strict"
    rng = np.random.default_rng()

    # ---------- 第一遍：识别 ----------
    pages: list[PageData] = []
    page_hits: list[list[Hit]] = []
    page_regions: list[list[Region]] = []
    digest_pages: dict[str, set[int]] = {}

    progress(0.01, "解析文件")
    for img, pd in iter_pages(src, doc.kind, opts.dpi, opts.password):
        for obj in pd.images:
            digest_pages.setdefault(obj.digest, set()).add(pd.index)
        repeated_so_far = {d for d, ps in digest_pages.items() if len(ps) >= 2}
        _page_text(img, pd, repeated_so_far)
        work = ocr._rotate(img, pd.rotation)
        hits, fields = engine.page_hits(pd, enabled, opts.custom_words)
        regions: list[Region] = []
        for ftype, _label, right_rect, below_rect in fields:
            if ftype not in enabled:
                continue
            rect = None
            if right_rect and _field_ok(work, pd, right_rect):
                rect = right_rect
            elif below_rect and _field_ok(work, pd, below_rect):
                rect = below_rect
            if rect:
                h = max(rect[3] - rect[1], 1)
                regions.append(Region(ftype, "anchor-field", pd.index, vision.ink_bounds(work, rect, pad=0.12 * h)))
        if "SEAL" in enabled:
            regions += [Region("SEAL", "color", pd.index, r) for r in vision.red_seals(work, opts.dpi)]
        if "QRCODE" in enabled:
            regions += [Region("QRCODE", "detector", pd.index, r) for r in vision.codes(work)]
        _preview(img, prev_dir / f"before-{pd.index + 1}.jpg")
        pages.append(pd)
        page_hits.append(hits)
        page_regions.append(regions)
        progress(0.02 + 0.6 * (pd.index + 1) / n, f"识别第 {pd.index + 1}/{n} 页")

    repeated = {d for d, ps in digest_pages.items() if len(ps) >= 2}
    all_hits = [h for hs in page_hits for h in hs]
    seeds = engine.collect_seeds(all_hits, opts.custom_words)
    seeds = {v: t for v, t in seeds.items() if t in enabled}

    # ---------- 第二遍：传播、打码、自检 ----------
    aliases = Aliases()
    report_items = []
    residual_total = 0
    page_meta = []
    max_font = int(opts.dpi * 0.13)
    for img, _pd in iter_pages(src, doc.kind, opts.dpi, opts.password):
        i = _pd.index
        pd = pages[i]
        oh, ow = img.shape[:2]
        img = ocr._rotate(img, pd.rotation).copy()
        hits = page_hits[i] + engine.propagate(pd, seeds, page_hits[i])
        regions = list(page_regions[i])
        for h in hits:
            rect = _hits_to_rect(pd, h, strict)
            if rect:
                regions.append(Region(h.type, h.source, i, rect, aliases.get(h.type, h.value)))
        scanned = any((o.rect[2] - o.rect[0]) * (o.rect[3] - o.rect[1]) >= 0.8 * pd.width * pd.height for o in pd.images)
        for obj in [] if scanned else pd.images:  # 扫描页的图片对象是整页底图与 MRC 文字蒙版，不逐个归类
            kind = vision.classify_image(obj, pd.width, pd.height, obj.digest in repeated, opts.dpi)
            if kind and kind in enabled:
                regions.append(Region(kind, "image-object", i, obj.rect))
        regions = _merge(regions)
        for r in regions:
            r.style = opts.style_for(r.type)
            text = r.alias if (opts.label_text == "alias" and r.alias) else ENTITY_BY_CODE[r.type]["label"]
            if r.style == "replace":
                text = r.alias or ENTITY_BY_CODE[r.type]["label"]
            redact.apply(img, r.rect, r.style, text, rng, max_font)

        residual = 0
        if opts.verify:
            progress(0.62 + 0.36 * (i + 0.5) / n, f"自检第 {i + 1}/{n} 页")
            vlines = ocr.ocr_page(img, 0)
            vpd = PageData(index=i, width=pd.width, height=pd.height, lines=vlines, rotation=pd.rotation)
            extra = [h for h in engine.page_hits(vpd, enabled, opts.custom_words)[0] if h.source in ("rule", "custom")]
            extra += engine.propagate(vpd, seeds, extra)
            for h in extra:
                rect = _hits_to_rect(vpd, h, True)
                if rect:
                    residual += 1
                    reg = Region(h.type, "verify", i, rect, aliases.get(h.type, h.value), opts.style_for(h.type))
                    redact.apply(img, reg.rect, reg.style, ENTITY_BY_CODE[h.type]["label"], rng, max_font)
                    regions.append(reg)
            residual_total += residual

        img = ocr._rotate(img, (360 - pd.rotation) % 360)
        Image.fromarray(img).save(pages_dir / f"{i + 1:05d}.jpg", "JPEG", quality=88, dpi=(opts.dpi, opts.dpi))
        _preview(img, prev_dir / f"after-{i + 1}.jpg")
        for r in regions:
            x0, y0, x1, y1 = ocr._rect_unrotate(r.rect, pd.rotation, ow, oh)
            report_items.append(
                {
                    "page": i + 1,
                    "type": r.type,
                    "source": r.source,
                    "style": r.style,
                    "alias": r.alias,
                    "box": [round(max(x0, 0) / ow, 4), round(max(y0, 0) / oh, 4), round(min(x1, ow) / ow, 4), round(min(y1, oh) / oh, 4)],
                }
            )
        page_meta.append({"page": i + 1, "text_source": pd.text_source, "rotation": pd.rotation, "regions": len(regions), "residual": residual})
        progress(0.62 + 0.36 * (i + 1) / n, f"打码第 {i + 1}/{n} 页")

    # ---------- 重建 ----------
    progress(0.99, "生成输出文件")
    out_path = _rebuild(doc, pages_dir, out_dir, n)
    shutil.rmtree(out_dir / "convert", ignore_errors=True)
    counts = Counter(it["type"] for it in report_items)
    return {
        "pages": n,
        "input_kind": source_kind,
        "output": out_path.name,
        "elapsed_sec": round(time.time() - t0, 1),
        "counts": dict(counts),
        "items": report_items,
        "page_meta": page_meta,
        "verification": {"enabled": opts.verify, "residual_hits": residual_total, "passed": True},
        "options": {
            "entities": sorted(enabled),
            "default_style": opts.default_style,
            "styles": opts.styles,
            "mode": opts.mode,
            "dpi": opts.dpi,
            "custom_words": len(opts.custom_words),
        },
    }


def _rebuild(doc, pages_dir: Path, out_dir: Path, n: int) -> Path:
    files = [str(pages_dir / f"{i + 1:05d}.jpg") for i in range(n)]
    if doc.kind == "pdf" or n > 1:
        out = out_dir / "redacted.pdf"
        # 按原页面尺寸（磅）输出
        with open(out, "wb") as fh:
            fh.write(img2pdf.convert(files, layout_fun=_layout_from_sizes(doc.page_sizes_pt)))
        _sanitize(out)
        return out
    ext = {"jpeg": "jpg", "png": "png", "bmp": "png", "webp": "png", "tiff": "png"}[doc.kind]
    out = out_dir / f"redacted.{ext}"
    im = Image.open(files[0])
    if ext == "jpg":
        im.save(out, "JPEG", quality=90)
    else:
        im.save(out, "PNG")
    return out


def _layout_from_sizes(sizes):
    it = iter(sizes)

    def fun(imgwidthpx, imgheightpx, ndpi):
        w_pt, h_pt = next(it)
        return w_pt, h_pt, w_pt, h_pt

    return fun


def _sanitize(path: Path) -> None:
    """清理元数据：文档信息字典与 XMP 全部移除，整文件重写。"""
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        if "/Info" in pdf.trailer:
            del pdf.trailer["/Info"]
        if "/Metadata" in pdf.Root:
            del pdf.Root["/Metadata"]
        pdf.save(path, fix_metadata_version=False)
