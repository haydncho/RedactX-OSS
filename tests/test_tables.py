import cv2
import numpy as np

from redactx import vision
from redactx.detect import anchors
from redactx.schemas import Char, Line, PageData


def _ln(text, x, y, cw=30, h=30, score=1.0):
    return Line([Char(ch, (x + k * cw, y, x + (k + 1) * cw - 2, y + h), score) for k, ch in enumerate(text)], "ocr")


def test_signature_column_rows_come_from_other_columns():
    lines = [_ln("日期", 100, 400), _ln("时间", 300, 400), _ln("病情观察及护理措施", 500, 400), _ln("护士签名", 1200, 400)]
    for r in range(4):
        y = 500 + r * 100
        lines.append(_ln("08:00", 300, y))
        lines.append(_ln("患者夜间睡眠尚可，无特殊不适。" if r % 2 else "体温正常。", 500, y))
        # 签名格里有时一个字都认不出来：不放任何识别结果
    lines.append(_ln("护士长签名：", 800, 1000))  # 表格下方的落款，不是表格行
    lines.append(_ln("第7页", 1400, 2200))
    page = PageData(0, 1654, 2339, lines)
    fields = anchors.table_signatures(page)
    assert len(fields) == 4
    for k, (typ, label, rect, _) in enumerate(fields):
        assert typ == "SIGNATURE" and label == "护士签名"
        assert 480 < rect[1] + 20 < 500 + k * 100 + 30  # 与该行对齐
        note_end = 500 + (15 if k % 2 else 5) * 30
        assert rect[0] >= note_end  # 不越过同一行里的记录内容


def test_rule_lines_allow_slight_slant():
    ink = np.zeros((200, 600), np.uint8)
    cv2.line(ink, (10, 100), (590, 120), 1, 2)  # 约 2° 的表格线
    cv2.ellipse(ink, (300, 60), (20, 25), 0, 0, 360, 1, 2)  # 笔画
    lines = vision._rule_lines(ink, 60, 60)
    assert lines[110, 300] or lines[109, 300] or lines[111, 300]
    assert lines[35:85, 280:320].sum() == 0
