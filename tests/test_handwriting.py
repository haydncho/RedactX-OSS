from redactx import pipeline
from redactx.detect import anchors
from redactx.schemas import Char, Line, PageData


def _line(text, scores=None, h=40):
    scores = scores or [1.0] * len(text)
    return Line([Char(ch, (k * 45, 100, k * 45 + 40, 100 + h), s) for k, (ch, s) in enumerate(zip(text, scores))], "ocr")


def test_signature_misread_as_month_is_not_a_date():
    # 连笔签名“覃诗明”被认成“覃月”：没有数字，不是日期，填写区照常保留
    page = PageData(0, 1000, 400, [_line("覃月")])
    assert not pipeline._has_printed_digits(page, (0, 90, 200, 150))
    page = PageData(0, 1000, 400, [_line("2025年")])
    assert pipeline._has_printed_digits(page, (0, 90, 400, 150))


def test_low_confidence_handwritten_name_without_surname():
    # “患者”后的手写“彭曦涛”被认成“壹细羲涛”：首字不是姓氏，但独占行尾且置信度低
    assert anchors._handwritten_name_after(_line("患者壹细羲涛", [1, 1, .41, .77, .98, 1]), 0, 2)
    # 置信度高的正文、或后面还有标点的，不算
    assert not anchors._handwritten_name_after(_line("患者壹细羲涛"), 0, 2)
    assert not anchors._handwritten_name_after(_line("患者无不适，", [1, 1, .5, .5, .5, 1]), 0, 2)
