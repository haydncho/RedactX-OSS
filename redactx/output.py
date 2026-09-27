"""任务输出：页面文件、预览图与最终文件的重建。自动打码与复核重打码共用。

页面文件：out/pages/00001.jpg（打码后，按渲染 DPI）；out/orig/00001.jpg（为复核保留的打码前页面）；
预览图：out/preview/before-1.jpg、after-1.jpg（宽度不超过 1400 像素）。
"""

from __future__ import annotations

from pathlib import Path

import cv2
import img2pdf
import numpy as np
import pikepdf
from PIL import Image

PAGE_QUALITY = 88  # 打码后页面的 JPEG 质量
ORIG_QUALITY = 95  # 为复核保留的打码前页面
PREVIEW_QUALITY = 82
PREVIEW_MAX_W = 1400


def page_file(d: Path, page: int) -> Path:
    """页面文件路径，page 从 1 开始。"""
    return d / f"{page:05d}.jpg"


def save_page(img: np.ndarray, d: Path, page: int, dpi: int) -> None:
    Image.fromarray(img).save(page_file(d, page), "JPEG", quality=PAGE_QUALITY, dpi=(dpi, dpi))


def save_orig(img: np.ndarray, d: Path, page: int) -> None:
    d.mkdir(exist_ok=True)
    Image.fromarray(img).save(page_file(d, page), "JPEG", quality=ORIG_QUALITY)


def preview(img: np.ndarray, path: Path, max_w: int = PREVIEW_MAX_W) -> None:
    h, w = img.shape[:2]
    if w > max_w:
        img = cv2.resize(img, (max_w, int(h * max_w / w)), interpolation=cv2.INTER_AREA)
    Image.fromarray(img).save(path, "JPEG", quality=PREVIEW_QUALITY)


def rebuild(kind: str, sizes, pages_dir: Path, out_dir: Path, n: int) -> Path:
    """由打码后的页面重建输出：PDF 或多页输入按原页面尺寸输出 PDF（清除元数据）；单张图片输出同格式图片。"""
    files = [str(page_file(pages_dir, i + 1)) for i in range(n)]
    if kind == "pdf" or n > 1:
        out = out_dir / "redacted.pdf"
        # 按原页面尺寸（磅）输出
        with open(out, "wb") as fh:
            fh.write(img2pdf.convert(files, layout_fun=_layout_from_sizes(sizes)))
        _sanitize(out)
        return out
    ext = {"jpeg": "jpg", "png": "png", "bmp": "png", "webp": "png", "tiff": "png"}[kind]
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
