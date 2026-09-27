"""文字层分行、填写区里姓名行的判断。"""

from redactx.ingest import _group_lines
from redactx.pipeline import _names_only
from redactx.schemas import Char


def test_clipped_overflow_text_not_interleaved():
    # “三级手术”超出格子，被裁掉的“手术”压在右边一格的“王小红”上：两串字不能按 x 交错
    chars = [Char("三", (0, 0, 10, 10)), Char("级", (10, 0, 20, 10)), Char("手", (20, 0, 30, 10)), Char("术", (32, 0, 42, 10)),
             Char("王", (25, 0, 35, 10)), Char("小", (35, 0, 45, 10)), Char("红", (45, 0, 55, 10))]
    assert [ln.text for ln in _group_lines(chars)] == ["三级手术王小红"]


def test_plain_line_order_unchanged():
    chars = [Char("乙", (20, 0, 30, 10)), Char("甲", (0, 0, 10, 10))]  # 书写顺序与位置相反的独立字仍按 x 排
    assert [ln.text for ln in _group_lines(chars)] == ["甲乙"]


def test_names_only():
    assert _names_only("张明/李晓红") and _names_only("王芳")
    assert not _names_only("静脉输液通畅") and not _names_only("体温正常")
