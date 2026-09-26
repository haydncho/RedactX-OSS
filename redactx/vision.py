"""图像类识别：红色印章、二维码与条码、填写区墨迹、页内图片对象归类、扫描页跨页重复图形（Logo）。"""

from __future__ import annotations

import cv2
import numpy as np

from dataclasses import dataclass

from .schemas import ImageObject, Line, Rect


def red_seals(img: np.ndarray, dpi: int) -> list[Rect]:
    """红章：HSV 颜色分割。排除内镜、病理等本身偏红的照片。"""
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
    h, s, v = cv2.split(hsv)
    red = (((h <= 10) | (h >= 155)) & (s >= 25) & (v >= 90)).astype(np.uint8) * 255
    k = max(3, int(dpi / 40))
    closed = cv2.morphologyEx(red, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k * 3, k * 3)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(closed)
    min_side = dpi * 0.5 / 2.54  # 0.5 cm
    max_side = dpi * 6 / 2.54  # 6 cm
    out = []
    for i in range(1, n):
        x, y, w, hh, area = stats[i]
        if not (min_side <= w <= max_side and min_side <= hh <= max_side):
            continue
        if not (0.2 <= w / hh <= 5.0):
            continue
        box_red = red[y : y + hh, x : x + w]
        density = box_red.mean() / 255
        if not (0.04 <= density <= 0.65):
            continue
        # 非红色部分应以浅色纸面为主，照片会有大量暗部
        non_red = v[y : y + hh, x : x + w][box_red == 0]
        if non_red.size and (non_red > 120).mean() < 0.6:
            continue
        pad = int(0.06 * max(w, hh))
        out.append((x - pad, y - pad, x + w + pad, y + hh + pad))
    return out


def _plausible_qr(p: np.ndarray, dpi: int) -> bool:
    """单码检测偶尔把表格线的交角当成二维码：要求近似正方形，边长 0.6–6 cm。"""
    w, h = np.ptp(p[:, 0]), np.ptp(p[:, 1])
    lo, hi = dpi * 0.6 / 2.54, dpi * 6 / 2.54
    return lo <= w <= hi and lo <= h <= hi and 0.75 <= w / max(h, 1) <= 1.33


def codes(img: np.ndarray, dpi: int = 200) -> list[Rect]:
    """二维码与一维条码。"""
    out: list[Rect] = []
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    try:
        det = cv2.QRCodeDetector()
        ok, pts = det.detectMulti(gray)
        if not ok or pts is None:
            # 多码检测偶尔整页失败（底色偏深时），单码检测仍能找到
            ok, pts = det.detect(gray)
            pts = pts.reshape(1, 4, 2) if ok and pts is not None and _plausible_qr(pts.reshape(4, 2), dpi) else None
        if ok and pts is not None:
            for p in pts:
                xs, ys = p[:, 0], p[:, 1]
                out.append((xs.min() - 6, ys.min() - 6, xs.max() + 6, ys.max() + 6))
    except cv2.error:
        pass
    try:
        det = cv2.barcode.BarcodeDetector()
        ok, pts = det.detect(gray)[:2] if hasattr(det, "detect") else (False, None)
        if ok and pts is not None:
            for p in pts:
                xs, ys = p[:, 0], p[:, 1]
                if xs.max() - xs.min() > 20:
                    out.append((xs.min() - 6, ys.min() - 6, xs.max() + 6, ys.max() + 6))
    except (cv2.error, AttributeError):
        pass
    return out


def _ink_mask(img: np.ndarray, rect: Rect):
    """填写区内的笔迹掩码：去掉横线、竖线（下划线和表格线），只留下有一定高度的笔画。"""
    hgt, wid = img.shape[:2]
    x0, y0, x1, y1 = (int(round(v)) for v in rect)
    x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, wid), min(y1, hgt)
    if x1 - x0 < 6 or y1 - y0 < 6:
        return None, (x0, y0)
    crop = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_RGB2GRAY)
    thr = min(150, int(np.percentile(crop, 60)) - 50)
    ink = (crop < thr).astype(np.uint8)
    rh = y1 - y0
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(12, int(rh * 0.9)), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(12, int(rh * 0.7))))
    lines = cv2.morphologyEx(ink, cv2.MORPH_OPEN, hk) | cv2.morphologyEx(ink, cv2.MORPH_OPEN, vk)
    lines = cv2.dilate(lines, np.ones((3, 3), np.uint8))
    ink = ink & (1 - lines)
    # 只保留高度达到区域高度 18% 以上的连通笔画，过滤噪点、虚线和下划线残段
    n, lab, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    keep = np.zeros_like(ink)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_HEIGHT] >= 0.18 * rh and stats[i, cv2.CC_STAT_AREA] >= 12:
            keep[lab == i] = 1
    return keep, (x0, y0)


def has_ink(img: np.ndarray, rect: Rect, min_ratio: float = 0.01) -> bool:
    mask, _ = _ink_mask(img, rect)
    return mask is not None and mask.mean() >= min_ratio


def ink_bounds(img: np.ndarray, rect: Rect, pad: float) -> Rect:
    """把填写区收紧到实际笔迹范围，避免大面积遮盖空白。"""
    mask, (x0, y0) = _ink_mask(img, rect)
    if mask is None:
        return rect
    ys, xs = np.nonzero(mask)
    if len(xs) < 5:
        return rect
    return (x0 + xs.min() - pad, y0 + ys.min() - pad, x0 + xs.max() + pad, y0 + ys.max() + pad)


def classify_image(obj: ImageObject, page_w: int, page_h: int, repeated: bool, dpi: int) -> str | None:
    """页内图片对象：跨页重复或形似签名的小图遮盖，内镜、病理等临床照片保留。"""
    x0, y0, x1, y1 = obj.rect
    w, h = x1 - x0, y1 - y0
    if w < dpi * 0.2 / 2.54 or h < dpi * 0.2 / 2.54:  # 细线、装饰条
        return None
    area_ratio = (w * h) / float(page_w * page_h)
    if area_ratio > 0.3:
        return None
    small_sig = h < dpi * 1.4 / 2.54 and w / max(h, 1) >= 1.8 and area_ratio < 0.02
    if small_sig:
        return "SIGNATURE"
    if repeated:
        return "LOGO"
    return None


# ---------- 扫描页上的 Logo：页眉页脚里跨页重复出现的图形 ----------

_THUMB = 48
_BANDS = (0.2, 0.12)  # 只在页面顶部 20%、底部 12% 里找


@dataclass
class Graphic:
    page: int
    rect: Rect
    size: tuple[int, int]  # 所在页面图像的宽、高
    thumb: np.ndarray  # 归一化灰度缩略图，用于跨页比对


def graphic_candidates(img: np.ndarray, lines: list[Line], page: int, dpi: int) -> list[Graphic]:
    """页眉页脚里不属于文字的图形块（0.6–5 cm、形状不过分狭长），留作跨页比对。"""
    hgt, wid = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    text = np.zeros((hgt, wid), bool)
    for ln in lines:
        x0, y0, x1, y1 = (int(round(v)) for v in ln.box)
        text[max(y0, 0) : max(y1, 0), max(x0, 0) : max(x1, 0)] = True
    lo, hi = dpi * 0.6 / 2.54, dpi * 5 / 2.54
    out: list[Graphic] = []
    for y0, y1 in ((0, int(hgt * _BANDS[0])), (int(hgt * (1 - _BANDS[1])), hgt)):
        band = gray[y0:y1]
        _, ink = cv2.threshold(band, 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        ink = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        n, _, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            if not (lo <= w <= hi and lo <= h <= hi and 0.4 <= w / h <= 2.5):
                continue
            if not 0.05 <= area / (w * h) <= 0.85:
                continue
            if text[y0 + y : y0 + y + h, x : x + w].mean() >= 0.4:  # 大半落在识别出的文字行里
                continue
            crop = cv2.resize(band[y : y + h, x : x + w], (_THUMB, _THUMB), interpolation=cv2.INTER_AREA).astype(np.float32)
            crop = (crop - crop.mean()) / (crop.std() + 1e-6)
            pad = max(2.0, 0.06 * max(w, h))  # 二值化会丢掉抗锯齿的淡边，外扩一圈
            out.append(Graphic(page, (x - pad, y0 + y - pad, x + w + pad, y0 + y + h + pad), (wid, hgt), crop))
    return out


def repeated_graphics(cands: list[Graphic], min_corr: float = 0.6, max_shift: float = 0.04) -> list[Graphic]:
    """在另一页相近位置出现、大小相近、外观相似的图形，判为跨页重复（Logo 等）。"""

    def norm(g: Graphic):
        w, h = g.size
        x0, y0, x1, y1 = g.rect
        return (x0 + x1) / 2 / w, (y0 + y1) / 2 / h, (x1 - x0) / w, (y1 - y0) / h

    keys = [norm(g) for g in cands]
    hits: set[int] = set()
    for a in range(len(cands)):
        for b in range(a + 1, len(cands)):
            if cands[a].page == cands[b].page:
                continue
            (ax, ay, aw, ah), (bx, by, bw, bh) = keys[a], keys[b]
            if abs(ax - bx) > max_shift or abs(ay - by) > max_shift:
                continue
            if not (0.8 <= aw / bw <= 1.25 and 0.8 <= ah / bh <= 1.25):
                continue
            if float((cands[a].thumb * cands[b].thumb).mean()) >= min_corr:
                hits.update((a, b))
    return [cands[i] for i in sorted(hits)]
