"""OCR：PP-OCRv6（RapidOCR / ONNX Runtime），输出单字坐标，并处理整页旋转 90° 的扫描件。"""

from __future__ import annotations

import logging
import threading

import cv2
import numpy as np

from .schemas import Char, Line

log = logging.getLogger("redactx.ocr")

_engine = None
_lock = threading.Lock()


def engine():
    global _engine
    with _lock:
        if _engine is None:
            from rapidocr import RapidOCR

            logging.getLogger("RapidOCR").setLevel(logging.WARNING)
            _engine = RapidOCR(params={"Global.log_level": "warning"}) if _accepts_params() else RapidOCR()
        return _engine


def _accepts_params() -> bool:
    try:
        from rapidocr import RapidOCR  # noqa: F401
        import inspect

        return "params" in inspect.signature(RapidOCR.__init__).parameters
    except Exception:
        return False


def _rotate(img: np.ndarray, k: int) -> np.ndarray:
    """顺时针旋转 k*90 度。"""
    return {0: img, 90: cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE), 180: cv2.rotate(img, cv2.ROTATE_180), 270: cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)}[k]


def _unrotate_pt(x: float, y: float, k: int, w: int, h: int) -> tuple[float, float]:
    """把旋转后图像上的点换算回原图坐标。w、h 为原图尺寸。"""
    if k == 0:
        return x, y
    if k == 90:  # 原图 (x0,y0) -> 旋转后 (h-1-y0, x0)
        return y, h - 1 - x
    if k == 180:
        return w - 1 - x, h - 1 - y
    if k == 270:  # 原图 (x0,y0) -> 旋转后 (y0, w-1-x0)
        return w - 1 - y, x
    raise ValueError(k)


def _quad_rect(quad, k: int, w: int, h: int) -> tuple[float, float, float, float]:
    pts = [_unrotate_pt(float(px), float(py), k, w, h) for px, py in quad]
    xs, ys = zip(*pts)
    return min(xs), min(ys), max(xs), max(ys)


def _rect_unrotate(rect, k: int, w: int, h: int) -> tuple[float, float, float, float]:
    """旋转画面上的矩形换算回原图坐标。w、h 为原图尺寸。"""
    x0, y0, x1, y1 = rect
    return _quad_rect([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], k, w, h)


def detect_orientation(img: np.ndarray) -> int:
    """整页方向：竖长文字框占多数时，判定页面被旋转了 90° 或 270°，再用识别置信度二选一。"""
    eng = engine()
    small = cv2.resize(img, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    res = eng(small, use_det=True, use_cls=False, use_rec=False)
    boxes = getattr(res, "boxes", None)
    if boxes is None or len(boxes) < 5:
        return 0
    tall = wide = 0
    for b in boxes:
        b = np.array(b)
        wbox = np.linalg.norm(b[1] - b[0])
        hbox = np.linalg.norm(b[3] - b[0])
        if hbox > wbox * 1.5:
            tall += 1
        elif wbox > hbox * 1.5:
            wide += 1
    # 竖长框明显多于横长框，说明整页被旋转了 90° 或 270°
    if tall < 8 or tall < wide * 2:
        return 0
    best, best_score = 90, -1.0
    for k in (90, 270):
        # 不开方向分类：它会把倒置的文字行翻正，两个方向的置信度就拉不开差距，文字少的页会选反
        r = eng(_rotate(small, k), use_det=True, use_cls=False, use_rec=True)
        scores = list(getattr(r, "scores", None) or [])
        s = float(np.mean(scores)) * len(scores) if scores else 0.0
        if s > best_score:
            best, best_score = k, s
    return best


def ocr_page(img: np.ndarray, rotation: int = 0, min_score: float = 0.3) -> list[Line]:
    h, w = img.shape[:2]
    work = _rotate(img, rotation)
    res = engine()(work, use_det=True, use_cls=True, use_rec=True, return_word_box=True)
    txts = getattr(res, "txts", None) or ()
    boxes = getattr(res, "boxes", None)
    words = getattr(res, "word_results", None) or ()
    scores = getattr(res, "scores", None) or ()
    lines: list[Line] = []
    for idx, txt in enumerate(txts):
        if not txt or (scores and scores[idx] < min_score):
            continue
        chars: list[Char] = []
        wr = words[idx] if idx < len(words) else None
        if wr:
            for token, sc, quad in wr:
                if not token:
                    continue
                x0, y0, x1, y1 = _quad_rect(quad, rotation, w, h)
                # 英文与数字可能按词返回，按字符均分
                n = len(token)
                for j, ch in enumerate(token):
                    if ch.isspace():
                        continue
                    if rotation in (0, 180) or n == 1:
                        if rotation == 180:
                            cx0 = x1 - (x1 - x0) * (j + 1) / n
                            cx1 = x1 - (x1 - x0) * j / n
                        else:
                            cx0 = x0 + (x1 - x0) * j / n
                            cx1 = x0 + (x1 - x0) * (j + 1) / n
                        chars.append(Char(ch=ch, box=(cx0, y0, cx1, y1), score=float(sc)))
                    else:
                        # 旋转 90/270 时文字在原图上是竖排的
                        if rotation == 90:
                            cy0 = y1 - (y1 - y0) * (j + 1) / n
                            cy1 = y1 - (y1 - y0) * j / n
                        else:
                            cy0 = y0 + (y1 - y0) * j / n
                            cy1 = y0 + (y1 - y0) * (j + 1) / n
                        chars.append(Char(ch=ch, box=(x0, cy0, x1, cy1), score=float(sc)))
        elif boxes is not None:
            x0, y0, x1, y1 = _quad_rect(boxes[idx], rotation, w, h)
            n = max(len(txt), 1)
            for j, ch in enumerate(txt):
                if ch.isspace():
                    continue
                chars.append(Char(ch=ch, box=(x0 + (x1 - x0) * j / n, y0, x0 + (x1 - x0) * (j + 1) / n, y1), score=float(scores[idx]) if scores else 1.0))
        if chars:
            angle = 0.0
            if boxes is not None and idx < len(boxes):
                q = np.asarray(boxes[idx], dtype=float)
                angle = float(np.degrees(np.arctan2(q[1][1] - q[0][1], q[1][0] - q[0][0])))
            lines.append(Line(chars=chars, source="ocr", angle=angle))
    return lines


def region_ocr(img: np.ndarray, rect) -> list[Line]:
    """只对页面中的某个区域做 OCR（用于混合页里的扫描图片），坐标换算回整页。"""
    x0, y0, x1, y1 = (int(max(v, 0)) for v in rect)
    crop = img[y0:y1, x0:x1]
    if crop.size == 0 or min(crop.shape[:2]) < 32:
        return []
    lines = ocr_page(crop)
    for ln in lines:
        for c in ln.chars:
            bx0, by0, bx1, by1 = c.box
            c.box = (bx0 + x0, by0 + y0, bx1 + x0, by1 + y0)
    return lines
