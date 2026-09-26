"""打码样式引擎。核心原则：先擦除、后装饰。

擦除一步把遮盖区域内的原像素全部覆盖；装饰函数只拿得到擦除后的图像，
因此任何样式都不会留下可还原的原始信息。
"""

from __future__ import annotations

from functools import lru_cache

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .config import settings
from .schemas import Rect

LABEL_BG = (236, 235, 252)
LABEL_FG = (60, 52, 137)
HATCH_BG = (238, 238, 236)
HATCH_FG = (120, 124, 130)

# 样式 -> 擦除方式
ERASE_MODE = {"label": "background", "background": "background", "replace": "background", "hatch": "background", "mosaic": "noise", "inpaint": "inpaint", "black": "black"}


def _clip(rect: Rect, w: int, h: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = rect
    return max(int(np.floor(x0)), 0), max(int(np.floor(y0)), 0), min(int(np.ceil(x1)), w), min(int(np.ceil(y1)), h)


def _ring_color(img: np.ndarray, box: tuple[int, int, int, int], width: int = 5) -> np.ndarray:
    """遮盖区外圈像素的中位色；只取偏亮的像素，避免被相邻文字带暗。"""
    h, w = img.shape[:2]
    x0, y0, x1, y1 = box
    X0, Y0, X1, Y1 = max(x0 - width, 0), max(y0 - width, 0), min(x1 + width, w), min(y1 + width, h)
    outer = img[Y0:Y1, X0:X1].reshape(-1, 3).astype(np.int32)
    mask = np.ones((Y1 - Y0, X1 - X0), bool)
    mask[y0 - Y0 : y1 - Y0, x0 - X0 : x1 - X0] = False
    ring = outer[mask.reshape(-1)]
    if ring.size == 0:
        return np.array([255, 255, 255], np.uint8)
    lum = ring.sum(axis=1)
    bright = ring[lum >= np.percentile(lum, 40)]
    return np.median(bright if len(bright) else ring, axis=0).astype(np.uint8)


def _ink_color(img: np.ndarray, box) -> tuple[int, int, int]:
    x0, y0, x1, y1 = box
    crop = img[y0:y1, x0:x1].reshape(-1, 3).astype(np.int32)
    if crop.size == 0:
        return (40, 40, 40)
    lum = crop.sum(axis=1)
    dark = crop[lum <= np.percentile(lum, 15)]
    c = np.median(dark, axis=0) if len(dark) else np.array([40, 40, 40])
    return tuple(int(min(v, 90)) for v in c)


@lru_cache(maxsize=32)
def _font(size: int):
    if settings.font_path:
        try:
            return ImageFont.truetype(settings.font_path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _fit_text(draw: ImageDraw.ImageDraw, text: str, max_w: int, max_h: int):
    size = max(int(max_h * 0.72), 6)
    while size >= 6:
        f = _font(size)
        l, t, r, b = draw.textbbox((0, 0), text, font=f)
        if r - l <= max_w and b - t <= max_h:
            return f, (r - l, b - t, l, t)
        size -= 1
    return None, None


def erase(img: np.ndarray, box, mode: str, rng: np.random.Generator) -> None:
    x0, y0, x1, y1 = box
    if mode == "black":
        img[y0:y1, x0:x1] = 0
    elif mode == "noise":
        base = _ring_color(img, box).astype(np.int16)
        noise = rng.integers(-60, 60, size=(y1 - y0, x1 - x0, 3))
        img[y0:y1, x0:x1] = np.clip(base + noise, 0, 255).astype(np.uint8)
    elif mode == "inpaint":
        h, w = img.shape[:2]
        pad = max(8, (y1 - y0) // 2)
        X0, Y0, X1, Y1 = max(x0 - pad, 0), max(y0 - pad, 0), min(x1 + pad, w), min(y1 + pad, h)
        crop = img[Y0:Y1, X0:X1].copy()
        # 先按背景色覆盖原像素，再修复纹理：修复只参考遮盖区外的像素
        crop[y0 - Y0 : y1 - Y0, x0 - X0 : x1 - X0] = _ring_color(img, box)
        mask = np.zeros(crop.shape[:2], np.uint8)
        mask[y0 - Y0 : y1 - Y0, x0 - X0 : x1 - X0] = 255
        fixed = cv2.inpaint(cv2.cvtColor(crop, cv2.COLOR_RGB2BGR), mask, 5, cv2.INPAINT_TELEA)
        img[Y0:Y1, X0:X1] = cv2.cvtColor(fixed, cv2.COLOR_BGR2RGB)
    else:
        img[y0:y1, x0:x1] = _ring_color(img, box)


def _overlay_label(img, box, text, max_font):
    """浅色胶囊标签：字号不超过正文大小，胶囊居中放在擦除后的区域里。"""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if h < 10 or w < 14:
        return
    pil = Image.fromarray(img[y0:y1, x0:x1])
    d = ImageDraw.Draw(pil)
    size = min(int(h * 0.62), max_font)
    while size >= 8:
        f = _font(size)
        l, t, r, b = d.textbbox((0, 0), text, font=f)
        tw, th = r - l, b - t
        pill_h = min(h - 2, int(size * 1.5))
        pill_w = tw + int(size * 1.0)
        if pill_w <= w - 2:
            break
        size -= 1
    if size < 8:
        # 区域太窄放不下文字，只画一个无字胶囊
        pill_h, pill_w, f = min(h - 2, int(h * 0.7)), w - 2, None
    px0 = (w - pill_w) // 2
    py0 = (h - pill_h) // 2
    d.rounded_rectangle((px0, py0, px0 + pill_w, py0 + pill_h), radius=pill_h // 2, fill=LABEL_BG)
    if f:
        d.text((px0 + (pill_w - tw) / 2 - l, py0 + (pill_h - th) / 2 - t), text, font=f, fill=LABEL_FG)
    img[y0:y1, x0:x1] = np.asarray(pil)


def _overlay_replace(img, box, text, color):
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if h < 8:
        return
    pil = Image.fromarray(img[y0:y1, x0:x1])
    d = ImageDraw.Draw(pil)
    f, m = _fit_text(d, text, w - 2, min(int(h * 0.85), int(h * 0.85)))
    if f:
        tw, th, l, t = m
        d.text((1 - l, (h - th) / 2 - t), text, font=f, fill=color)
    img[y0:y1, x0:x1] = np.asarray(pil)


def _overlay_mosaic(img, box, rng):
    x0, y0, x1, y1 = box
    h = y1 - y0
    block = max(6, h // 2)
    region = img[y0:y1, x0:x1]
    for by in range(0, region.shape[0], block):
        for bx in range(0, region.shape[1], block):
            cell = region[by : by + block, bx : bx + block]
            tone = cell.reshape(-1, 3).mean(axis=0) + rng.integers(-45, 45)
            cell[:] = np.clip(tone, 0, 255).astype(np.uint8)


def _overlay_hatch(img, box):
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    region = img[y0:y1, x0:x1]
    region[:] = HATCH_BG
    step = max(5, h // 4)
    yy, xx = np.mgrid[0:h, 0:w]
    stripes = ((xx + yy) % step) < max(1, step // 4)
    region[stripes] = HATCH_FG
    cv2.rectangle(region, (0, 0), (w - 1, h - 1), HATCH_FG, 1)


def apply(img: np.ndarray, rect: Rect, style: str, text: str, rng: np.random.Generator, max_font: int = 28) -> None:
    h, w = img.shape[:2]
    box = _clip(rect, w, h)
    if box[2] - box[0] < 2 or box[3] - box[1] < 2:
        return
    ink = _ink_color(img, box) if style == "replace" else None
    # 第一步：擦除，原像素到此不可恢复
    erase(img, box, ERASE_MODE.get(style, "background"), rng)
    # 第二步：装饰，只读取擦除后的图像
    if style == "label":
        _overlay_label(img, box, text, max_font)
    elif style == "replace":
        _overlay_replace(img, box, text, ink)
    elif style == "mosaic":
        _overlay_mosaic(img, box, rng)
    elif style == "hatch":
        _overlay_hatch(img, box)
