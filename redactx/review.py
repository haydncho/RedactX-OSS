"""复核：按人工确认后的遮盖框重新打码、重建输出。

任务输出目录里保留着打码后的页面（pages/）与重建所需的版面信息（layout.json）。
提交时选择“保留原件以便复核”的任务另有打码前的页面（orig/）：
- 有 orig/：可以加框、删框、改框，改动的页从打码前的页面重新打码；
- 没有 orig/：只能加框，新框直接打在已脱敏的页面上（原像素已不可恢复）。
复核完成（finish）或任务到期时删除 orig/。
"""

from __future__ import annotations

import json
import shutil
import time
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image

from . import ocr, redact
from .catalog import ENTITY_BY_CODE, STYLE_CODES
from .pipeline import _preview, _rebuild


class ReviewError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _key(it: dict) -> tuple:
    return it["page"], it["type"], tuple(round(v, 4) for v in it["box"]), it.get("style")


def _clean(raw, pages: int, default_style) -> list[dict]:
    if not isinstance(raw, list) or len(raw) > 50 * pages + 1000:
        raise ReviewError("INVALID_ITEMS", "items 应为数组，且数量合理")
    out = []
    for it in raw:
        try:
            page, typ, box = int(it["page"]), str(it["type"]), [float(v) for v in it["box"]]
        except (KeyError, TypeError, ValueError):
            raise ReviewError("INVALID_ITEMS", "每个框须有 page、type 与 box") from None
        if not 1 <= page <= pages or typ not in ENTITY_BY_CODE or len(box) != 4:
            raise ReviewError("INVALID_ITEMS", f"第 {page} 页的框类型或页码无效")
        x0, y0, x1, y1 = (min(max(v, 0.0), 1.0) for v in box)
        if x1 - x0 < 0.002 or y1 - y0 < 0.002:
            raise ReviewError("INVALID_ITEMS", f"第 {page} 页有过小的框")
        style = it.get("style") or default_style(typ)
        if style not in STYLE_CODES and style != "watermark":
            raise ReviewError("INVALID_ITEMS", f"不支持的样式：{style}")
        # 来源与代号会写进报告、画到图上：限制长度
        out.append({"page": page, "type": typ, "source": str(it.get("source") or "manual")[:32], "style": style,
                    "alias": it.get("alias")[:64] if isinstance(it.get("alias"), str) else None,
                    "box": [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]})
    return out


def _draw(img: np.ndarray, items: list[dict], rotation: int, label_text: str, dpi: int) -> np.ndarray:
    """在转正后的画面上打码（标签文字方向与自动打码一致），再转回原方向。"""
    oh, ow = img.shape[:2]
    work = ocr._rotate(img, rotation).copy()
    rh, rw = work.shape[:2]
    back = (360 - rotation) % 360
    rng = np.random.default_rng()
    for it in items:
        if it["style"] == "watermark":  # 水印已在打码前的页面上擦除
            continue
        x0, y0, x1, y1 = it["box"]
        rect = ocr._rect_unrotate((x0 * ow, y0 * oh, x1 * ow, y1 * oh), back, rw, rh) if rotation else (x0 * ow, y0 * oh, x1 * ow, y1 * oh)
        label = ENTITY_BY_CODE[it["type"]]["label"]
        text = it["alias"] if (label_text == "alias" or it["style"] == "replace") and it["alias"] else label
        redact.apply(work, rect, it["style"], text, rng, int(dpi * 0.13))
    return ocr._rotate(work, back)


def apply(out_dir: Path, raw_items) -> dict:
    """按提交的全部遮盖框更新任务输出，返回新的报告。"""
    report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    layout = json.loads((out_dir / "layout.json").read_text(encoding="utf-8"))
    styles = report.get("options", {}).get("styles", {})
    default = report.get("options", {}).get("default_style", "label")
    items = _clean(raw_items, report["pages"], lambda t: styles.get(t) or ENTITY_BY_CODE[t].get("default_style") or default)
    orig_dir = out_dir / "orig"
    editable = orig_dir.is_dir()

    old = Counter(_key(it) for it in report["items"])
    new = Counter(_key(it) for it in items)
    removed, added = old - new, new - old
    if removed and not editable:
        raise ReviewError("NOT_EDITABLE", "本任务未保留原件，只能加框，不能删框或改框")
    changed = sorted({k[0] for k in removed} | {k[0] for k in added})
    rotations = {m["page"]: m.get("rotation", 0) for m in report.get("page_meta", [])}

    for p in changed:
        page_items = [it for it in items if it["page"] == p]
        if editable:
            base, todo = orig_dir / f"{p:05d}.jpg", page_items
        else:
            left = Counter(added)
            todo = []
            for it in page_items:
                if left[_key(it)] > 0:
                    left[_key(it)] -= 1
                    todo.append(it)
            base = out_dir / "pages" / f"{p:05d}.jpg"
        img = np.array(Image.open(base).convert("RGB"))
        img = _draw(img, todo, rotations.get(p, 0), layout["label_text"], layout["dpi"])
        Image.fromarray(img).save(out_dir / "pages" / f"{p:05d}.jpg", "JPEG", quality=88, dpi=(layout["dpi"], layout["dpi"]))
        _preview(img, out_dir / "preview" / f"after-{p}.jpg")

    if changed:
        _rebuild(layout["kind"], layout["sizes"], out_dir / "pages", out_dir, report["pages"])
    report["items"] = items
    report["counts"] = dict(Counter(it["type"] for it in items))
    rv = report.setdefault("review", {})
    rv.update(editable=editable, edits=rv.get("edits", 0) + sum(removed.values()) + sum(added.values()), updated=time.time())
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return report


def export(out_dir: Path, dest: Path) -> dict:
    """导出标注：打码前的原始页面与复核后的全部框，COCO 格式（bbox 为像素 [x, y, 宽, 高]）。
    只有保留原件、尚未完成复核的任务可以导出。导出包含真实内容，调用方负责只在授权的标注环境里使用。"""
    import zipfile

    orig = out_dir / "orig"
    if not orig.is_dir():
        raise ReviewError("NOT_EDITABLE", "本任务未保留原件或已完成复核，没有可导出的原始页面")
    report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    cat_id = {c: k + 1 for k, c in enumerate(sorted(ENTITY_BY_CODE))}
    images, anns = [], []
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_STORED) as z:
        for p in range(1, report["pages"] + 1):
            src = orig / f"{p:05d}.jpg"
            if not src.exists():
                continue
            with Image.open(src) as im:
                w, h = im.size
            name = f"images/page-{p:04d}.jpg"
            z.write(src, name)
            images.append({"id": p, "file_name": name, "width": w, "height": h})
            for it in report["items"]:
                if it["page"] != p or it.get("style") == "watermark":
                    continue
                x0, y0, x1, y1 = it["box"]
                bw, bh = (x1 - x0) * w, (y1 - y0) * h
                anns.append({"id": len(anns) + 1, "image_id": p, "category_id": cat_id[it["type"]], "bbox": [round(x0 * w, 1), round(y0 * h, 1), round(bw, 1), round(bh, 1)],
                             "area": round(bw * bh, 1), "iscrowd": 0, "source": it.get("source")})
        coco = {"info": {"description": "RedactX 复核标注", "reviewed_edits": report.get("review", {}).get("edits", 0)},
                "images": images, "annotations": anns,
                "categories": [{"id": cat_id[c], "name": c, "zh": ENTITY_BY_CODE[c]["name"]} for c in sorted(ENTITY_BY_CODE)]}
        z.writestr("annotations.json", json.dumps(coco, ensure_ascii=False, indent=1))
    return {"pages": len(images), "boxes": len(anns)}


def finish(out_dir: Path) -> dict:
    """复核完成：删除打码前的页面，之后只能再加框。"""
    shutil.rmtree(out_dir / "orig", ignore_errors=True)
    for f in out_dir.glob("export-*.zip"):  # 下载中断时可能留下的导出包
        f.unlink(missing_ok=True)
    report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    report.setdefault("review", {}).update(editable=False, finished=True)
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return report
