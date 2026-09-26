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
    # 红色优势：R 明显高于 G、B。牛皮纸上的蓝黑手写墨迹边缘呈暗红褐色（R 只高 10–20，且 G > B），不能算作印章；
    # 淡粉色印章的红色优势也不大（约 30），但偏品红（B ≥ G），据此区分
    rgb = img.astype(np.int16)
    dominance = rgb[..., 0] - np.maximum(rgb[..., 1], rgb[..., 2])
    reddish = (dominance >= 35) | ((dominance >= 22) & (rgb[..., 2] >= rgb[..., 1]))
    red = (((h <= 10) | (h >= 155)) & (s >= 25) & (v >= 90) & reddish).astype(np.uint8) * 255
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


def _ink_mask(img: np.ndarray, rect: Rect, exclude: list[Rect] | None = None):
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
    # 已识别的打印字不算填写笔迹（如签名栏右侧“与患者关系”的“与”）
    for ex0, ey0, ex1, ey1 in exclude or ():
        a, b = int(max(ex0 - x0 - 1, 0)), int(max(ey0 - y0 - 1, 0))
        c, d = int(min(ex1 - x0 + 1, x1 - x0)), int(min(ey1 - y0 + 1, y1 - y0))
        if c > a and d > b:
            ink[b:d, a:c] = 0
    # 只保留高度达到区域高度 18% 以上的连通笔画，过滤噪点、虚线和下划线残段
    n, lab, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    keep = np.zeros_like(ink)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_HEIGHT] >= 0.18 * rh and stats[i, cv2.CC_STAT_AREA] >= 12:
            keep[lab == i] = 1
    return keep, (x0, y0)


def has_ink(img: np.ndarray, rect: Rect, min_ratio: float = 0.01, exclude: list[Rect] | None = None) -> bool:
    mask, _ = _ink_mask(img, rect, exclude)
    return mask is not None and mask.mean() >= min_ratio


def ink_bounds(img: np.ndarray, rect: Rect, pad: float, exclude: list[Rect] | None = None) -> Rect:
    """把填写区收紧到实际笔迹范围，避免大面积遮盖空白。"""
    mask, (x0, y0) = _ink_mask(img, rect, exclude)
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


# ---------- 水印：浅色层单独识别，只擦水印颜色的像素，不遮下面的正文 ----------

SLANT_MIN = 15  # 倾斜超过这个角度（度）的浅色文字行视为水印


@dataclass
class Watermark:
    quad: np.ndarray  # 4×2，文字行四边形（页面像素坐标）
    rect: Rect
    text: str  # 只在任务内存中使用，不写入报告与日志
    org: bool = True  # 内容是机构名称


def _paper(gray: np.ndarray) -> float:
    return float(np.percentile(gray, 90))


def light_layer(img: np.ndarray) -> np.ndarray:
    """比纸色深、比正文浅，且不紧挨深色笔画的像素：水印、淡色印章多在这一层。"""
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    paper = _paper(gray)
    dark = cv2.dilate((gray < paper - 110).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    light = ((gray < paper - 22) & ~dark).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(light, connectivity=8)
    keep = np.zeros(n, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= 12  # 去掉零散噪点
    return keep[lab]


def watermarks(img: np.ndarray, names: list[str], is_org, all_slanted: bool = False) -> list[Watermark]:
    """在浅色层上单独做 OCR 找水印。院名水印（is_org(text) 或与 names 里已识别的机构名相近）不论方向都算；
    all_slanted 时，倾斜的浅色文字行（至少 4 个字）不论内容都算水印。"""
    from rapidfuzz import fuzz

    from . import ocr

    light = light_layer(img)
    if light.mean() < 0.001:
        return []
    layer = np.where(light, 70, 255).astype(np.uint8)
    res = ocr.engine()(cv2.cvtColor(layer, cv2.COLOR_GRAY2RGB), use_det=True, use_cls=True, use_rec=True)
    txts = getattr(res, "txts", None) or ()
    boxes = getattr(res, "boxes", None)
    scores = getattr(res, "scores", None) or ()
    out: list[Watermark] = []
    for i, t in enumerate(txts):
        t = (t or "").replace(" ", "")
        if len(t) < 4 or (scores and scores[i] < 0.5) or boxes is None:
            continue
        q = np.asarray(boxes[i], dtype=np.float32)
        org = is_org(t) or any(len(n) >= 4 and fuzz.partial_ratio(n, t) >= 80 for n in names)
        a = abs(float(np.degrees(np.arctan2(q[1][1] - q[0][1], q[1][0] - q[0][0])))) % 180
        slanted = SLANT_MIN <= a <= 90 - SLANT_MIN or 90 + SLANT_MIN <= a <= 180 - SLANT_MIN
        if not (org or (all_slanted and slanted)):
            continue
        out.append(Watermark(q, (float(q[:, 0].min()), float(q[:, 1].min()), float(q[:, 0].max()), float(q[:, 1].max())), t, org))
    return out


def erase_watermark(img: np.ndarray, quad: np.ndarray) -> None:
    """在水印四边形内，只把颜色落在“纸色—水印色”连线附近的像素抹成背景；深色正文、照片、印章不动。"""
    h = float(np.linalg.norm(quad[3] - quad[0]))
    pad = max(3, int(0.25 * h))
    x0, y0 = (int(max(v - pad, 0)) for v in quad.min(axis=0))
    x1, y1 = int(min(quad[:, 0].max() + pad, img.shape[1])), int(min(quad[:, 1].max() + pad, img.shape[0]))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return
    crop = img[y0:y1, x0:x1]
    poly = np.zeros(crop.shape[:2], np.uint8)
    cv2.fillPoly(poly, [np.round(quad - [x0, y0]).astype(np.int32)], 1)
    poly = cv2.dilate(poly, np.ones((2 * pad + 1, 2 * pad + 1), np.uint8)) > 0
    px = crop.astype(np.float32)
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY).astype(np.float32)
    paper_g = _paper(gray[poly]) if poly.any() else 255.0
    paper = np.median(px[poly & (gray >= paper_g - 6)], axis=0) if (poly & (gray >= paper_g - 6)).any() else np.array([255, 255, 255], np.float32)
    wm_px = poly & (gray < paper_g - 22) & (gray > paper_g - 110)
    if wm_px.sum() < 20:
        return
    wm = np.median(px[wm_px], axis=0)
    d = paper - wm
    dd = float(d @ d)
    if dd < 100:
        return
    diff = paper - px  # 每个像素相对纸色的偏移
    t = (diff @ d) / dd  # 在“纸色 → 水印色”方向上的位置
    resid = np.linalg.norm(diff - t[..., None] * d, axis=-1)
    # 灰色水印与黑字边缘的抗锯齿像素颜色相同，靠颜色分不开：深色笔画及其周边 2 像素一律不动
    near_dark = cv2.dilate((gray < paper_g - 110).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    sel = poly & (t > 0.12) & (t < 1.6) & (resid < 24) & ~near_dark
    if sel.any():
        # 直接填纸色；图像修复会把旁边的黑字“涂”进来，反而把正文抹糊
        crop[sel] = np.clip(paper, 0, 255).astype(np.uint8)


def grow_strokes(img: np.ndarray, rect: Rect, avoid: list[Rect]) -> Rect:
    """大号手写签名常超出推算的区域：把与区域相连的笔画整体纳入。
    表格线先去掉；与已识别文字（avoid，如旁边的日期、标签）重叠的笔画不纳入。"""
    x0, y0, x1, y1 = rect
    h = max(y1 - y0, 8.0)
    H, W = img.shape[:2]
    wx0, wy0 = int(max(x0 - 2 * h, 0)), int(max(y0 - h, 0))
    wx1, wy1 = int(min(x1 + 3.5 * h, W)), int(min(y1 + h, H))
    if wx1 - wx0 < 8 or wy1 - wy0 < 8:
        return rect
    crop = img[wy0:wy1, wx0:wx1]
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    rgb = crop.astype(np.int16)
    not_red = (rgb[..., 0] - np.maximum(rgb[..., 1], rgb[..., 2])) < 35  # 印章的红色笔画不算签名
    ink = ((gray < _paper(gray) - 90) & not_red).astype(np.uint8)
    k = max(15, int(1.5 * h))
    lines = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, 1)))
    lines |= cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, k)))
    ink &= 1 - cv2.dilate(lines, np.ones((3, 3), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    rx0, ry0, rx1, ry1 = int(x0) - wx0, int(y0) - wy0, int(x1) - wx0, int(y1) - wy0
    inside = set(np.unique(lab[max(ry0, 0) : max(ry1, 0), max(rx0, 0) : max(rx1, 0)]).tolist()) - {0}
    out = [x0, y0, x1, y1]

    def box(i):
        cx, cy, cw, chh, _ = stats[i]
        return (cx + wx0, cy + wy0, cx + cw + wx0, cy + chh + wy0)

    def is_text(b):  # 这一笔主要落在别的已识别文字的字框里，或盖住了某个字框的大部分（日期、标签）
        return any(_inter(b, a) > 0.5 * max((b[2] - b[0]) * (b[3] - b[1]), 1) or _inter(b, a) > 0.3 * max((a[2] - a[0]) * (a[3] - a[1]), 1)
                   for a in avoid)

    for i in inside:
        b = box(i)
        if stats[i][4] < 12 or (b[0] >= x0 and b[1] >= y0 and b[2] <= x1 and b[3] <= y1) or is_text(b):
            continue
        out = [min(out[0], b[0] - 2), min(out[1], b[1] - 2), max(out[2], b[2] + 2), max(out[3], b[3] + 2)]
    # 签名右侧不相连的零散笔画（收笔拖尾、分开写的字）：中心落在原区域中间 60% 高度内、间隔不超过 2.5 个字高的逐段纳入
    rest = sorted((i for i in range(1, n) if i not in inside and stats[i][4] >= 12), key=lambda i: stats[i][0])
    band0, band1 = y0 + 0.2 * (y1 - y0), y1 - 0.2 * (y1 - y0)
    # 到同一行右边下一个已识别文字（如“报告日期”）为止，不越过
    stop = min((a[0] for a in avoid if a[0] >= x1 and band0 <= (a[1] + a[3]) / 2 <= band1), default=float("inf"))
    for i in rest:
        b = box(i)
        cyb = (b[1] + b[3]) / 2
        if b[0] < out[2] - 2 or b[0] - out[2] > 2.5 * h or b[2] > stop or not (band0 <= cyb <= band1) or is_text(b):
            continue
        out = [min(out[0], b[0] - 2), min(out[1], b[1] - 2), max(out[2], b[2] + 2), max(out[3], b[3] + 2)]
    return tuple(float(v) for v in out)


def _inter(a: Rect, b: Rect) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def skip_leading_cluster(img: np.ndarray, rect: Rect, h: float) -> Rect:
    """区域最左边的一簇笔画（被 OCR 并进签名的日期数字）之后若有明显空白，把左边界移到空白之后。"""
    x0, y0, x1, y1 = (int(round(v)) for v in rect)
    x0, y0 = max(x0, 0), max(y0, 0)
    crop = img[y0:y1, x0:x1]
    if crop.size == 0:
        return rect
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    cols = (gray < _paper(gray) - 90).any(axis=0)
    ink = np.flatnonzero(cols)
    if len(ink) == 0:
        return rect
    need = max(3, int(0.25 * h))
    run = 0
    for x in range(ink[0], min(len(cols), ink[0] + int(2.0 * h))):
        run = run + 1 if not cols[x] else 0
        if run >= need:
            nx = x0 + x - 1
            return (float(nx), rect[1], rect[2], rect[3]) if nx < x1 - h else rect
    return rect
