"""图像类识别的单元测试：扫描页跨页重复图形（Logo）与二维码几何校验。图形全部为程序绘制。"""

import cv2
import numpy as np

from redactx import vision

DPI = 200


def _page(draw=None, shift=(0, 0)) -> np.ndarray:
    img = np.full((2339, 1654, 3), 245, np.uint8)
    if draw:
        draw(img, shift)
    return img


def _emblem(img, shift):
    cx, cy = 180 + shift[0], 150 + shift[1]
    cv2.circle(img, (cx, cy), 70, (20, 70, 150), 12)
    cv2.rectangle(img, (cx - 12, cy - 45), (cx + 12, cy + 30), (20, 70, 150), -1)
    cv2.rectangle(img, (cx - 45, cy - 20), (cx + 45, cy + 4), (20, 70, 150), -1)


def _triangle(img, shift):
    pts = np.array([[110, 220], [250, 220], [180, 80]], np.int32) + np.array(shift)
    cv2.fillPoly(img, [pts], (30, 30, 30))


def _cands(pages):
    return [g for i, p in enumerate(pages) for g in vision.graphic_candidates(p, [], i, DPI)]


def test_repeated_emblem_across_pages():
    # 扫描件每页位置略有偏移，仍应判为跨页重复
    shifts = [(0, 0), (6, -4)]
    reps = vision.repeated_graphics(_cands([_page(_emblem, shifts[0]), _page(_emblem, shifts[1]), _page()]))
    assert sorted({g.page for g in reps}) == [0, 1]
    # 圆环半径 70、线宽 12，外沿约 76：每页至少有一个框盖住整个徽标（内部十字另成一块，打码时合并）
    for page, (dx, dy) in enumerate(shifts):
        assert any(
            g.rect[0] <= 180 + dx - 76 and g.rect[1] <= 150 + dy - 76 and g.rect[2] >= 180 + dx + 76 and g.rect[3] >= 150 + dy + 76
            for g in reps
            if g.page == page
        )


def test_different_or_single_graphics_not_repeated():
    assert vision.repeated_graphics(_cands([_page(_emblem), _page(_triangle)])) == []
    assert vision.repeated_graphics(_cands([_page(_emblem), _page()])) == []


def test_graphic_inside_text_line_ignored():
    from redactx.schemas import Char, Line

    text = [Line([Char("院", (100, 70, 260, 230))], "ocr")]
    cands = [g for i in range(2) for g in vision.graphic_candidates(_page(_emblem), text, i, DPI)]
    assert vision.repeated_graphics(cands) == []


def test_qr_plausibility():
    square = np.array([[0, 0], [120, 0], [120, 120], [0, 120]], np.float32)
    assert vision._plausible_qr(square, DPI)
    assert not vision._plausible_qr(square * 6, DPI)  # 大半页的“二维码”多是表格线
    assert not vision._plausible_qr(np.array([[0, 0], [300, 0], [300, 90], [0, 90]], np.float32), DPI)
