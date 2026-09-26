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


def _seal(img, cx, cy, color):
    cv2.circle(img, (cx, cy), 120, color, 10)
    cv2.circle(img, (cx, cy), 30, color, -1)


def test_red_seal_detected_on_kraft_but_not_dark_handwriting():
    kraft = np.full((1200, 1000, 3), (224, 202, 166), np.uint8)
    _seal(kraft, 300, 300, (186, 25, 29))  # 红章压在牛皮纸上
    # 蓝黑手写墨迹与牛皮纸混合后的暗红褐色笔画
    for k in range(6):
        cv2.putText(kraft, "ABCD", (120, 700 + k * 60), cv2.FONT_HERSHEY_SIMPLEX, 2, (91, 76, 69), 5)
    found = vision.red_seals(kraft, DPI)
    assert len(found) == 1
    x0, y0, x1, y1 = found[0]
    assert x0 < 300 < x1 and y0 < 300 < y1


def test_faint_pink_seal_detected():
    img = np.full((800, 800, 3), 245, np.uint8)
    _seal(img, 400, 400, (236, 168, 184))
    assert len(vision.red_seals(img, DPI)) == 1


def test_grow_strokes_takes_signature_tail_but_not_neighbours():
    img = np.full((600, 900, 3), 245, np.uint8)
    cv2.putText(img, "2025", (100, 150), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (20, 20, 20), 3)  # 上一行的日期
    cv2.line(img, (200, 260), (560, 200), (30, 30, 90), 4)  # 签名的长拖尾，伸出区域
    cv2.circle(img, (620, 240), 60, (212, 32, 44), 6)  # 旁边的红章
    rect = (200.0, 200.0, 320.0, 280.0)
    date_box = (95.0, 115.0, 220.0, 160.0)
    x0, y0, x1, y1 = vision.grow_strokes(img, rect, [date_box])
    assert x1 >= 555  # 拖尾整体纳入
    assert x1 < 600  # 不吞红章
    assert y0 > 160  # 不吞上一行日期


def test_faded_name_stamp_detected():
    # 褪色的淡红名章：红色优势约 25、偏橙，但很亮
    img = np.full((600, 600, 3), 245, np.uint8)
    cv2.rectangle(img, (200, 200), (300, 290), (222, 197, 190), 6)
    cv2.putText(img, "AB", (215, 265), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (222, 197, 190), 5)
    assert len(vision.red_seals(img, DPI)) == 1
