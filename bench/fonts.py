"""合成数据用的字体：印刷体（嵌入 PDF 时只保留用到的字）与手写体（只用于栅格化）。"""

from __future__ import annotations

import glob
import io
import logging
import os
from functools import lru_cache

from fontTools import subset
from fontTools.ttLib import TTCollection, TTFont
from PIL import ImageFont

logging.getLogger("fontTools.subset").setLevel(logging.ERROR)

# (路径, TTC 内的字体名)。只能用 TrueType 轮廓的字体：PDFium 按 TrueType 加载。
_PRINT_CANDIDATES = [
    ("/System/Library/Fonts/Supplemental/Songti.ttc", "Songti SC Regular"),
    ("/System/Library/Fonts/STHeiti Light.ttc", "Heiti SC Light"),
]
_BOLD_CANDIDATES = [
    ("/System/Library/Fonts/STHeiti Medium.ttc", "Heiti SC Medium"),
    ("/System/Library/Fonts/Supplemental/Songti.ttc", "Songti SC Bold"),
]
# macOS 按需下载的手写风格字体
_HAND_PATTERNS = ["Hannotate.ttc", "Hanzipen.ttc", "Xingkai.ttc", "WawaSC-Regular.otf"]
_ASSET_DIRS = ["/System/Library/AssetsV2/com_apple_MobileAsset_Font*/*/AssetData", "/Library/Fonts", os.path.expanduser("~/Library/Fonts")]


class FontMissing(RuntimeError):
    pass


def _ttc_index(path: str, name: str) -> int:
    if not path.lower().endswith(".ttc"):
        return 0
    coll = TTCollection(path, lazy=True)
    for i, f in enumerate(coll.fonts):
        if f["name"].getDebugName(4) == name:
            return i
    return 0


def _pick(env: str, candidates) -> tuple[str, int]:
    p = os.environ.get(env)
    if p and os.path.exists(p):
        return p, 0
    for path, name in candidates:
        if os.path.exists(path):
            return path, _ttc_index(path, name)
    raise FontMissing(f"找不到中文印刷字体，请用环境变量 {env} 指定一个 TrueType 字体文件")


@lru_cache
def print_font() -> tuple[str, int]:
    return _pick("REDACTX_BENCH_FONT", _PRINT_CANDIDATES)


@lru_cache
def bold_font() -> tuple[str, int]:
    try:
        return _pick("REDACTX_BENCH_BOLD_FONT", _BOLD_CANDIDATES)
    except FontMissing:
        return print_font()


@lru_cache
def hand_fonts() -> list[tuple[str, int]]:
    """手写体列表；本机没有时退回印刷体，由调用方加抖动模拟手写。"""
    out = []
    for pat in _HAND_PATTERNS:
        for d in _ASSET_DIRS:
            hits = sorted(glob.glob(os.path.join(d, pat)))
            if hits:
                out.append((hits[0], 0))
                break
    return out or [print_font()]


@lru_cache(maxsize=8)
def subset_bytes(path: str, index: int, text: str) -> bytes:
    """只保留 text 里用到的字，嵌入 PDF 后文件不至于几十 MB。"""
    font = TTCollection(path).fonts[index] if path.lower().endswith(".ttc") else TTFont(path)
    opts = subset.Options()
    opts.layout_features = []
    opts.name_IDs = ["*"]
    opts.notdef_outline = True
    sub = subset.Subsetter(opts)
    sub.populate(text=text + " 0123456789")
    sub.subset(font)
    buf = io.BytesIO()
    font.save(buf)
    return buf.getvalue()


@lru_cache(maxsize=64)
def pil_font(path: str, index: int, size_px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size_px, index=index)
