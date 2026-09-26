"""规则与锚定的单元测试。测试数据全部为虚构值。"""

from redactx.detect import rules
from redactx.detect.anchors import anchor
from redactx.schemas import Char, Line, PageData


def _id_with_check(body17: str) -> str:
    w = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    return body17 + "10X98765432"[sum(int(a) * b for a, b in zip(body17, w)) % 11]


def test_id_card_checksum():
    good = _id_with_check("11010519491231002")
    assert rules.id_card_ok(good)
    assert not rules.id_card_ok(good[:-1] + ("0" if good[-1] != "0" else "1"))
    assert not rules.id_card_ok("110105194913310021")  # 非法日期


def test_luhn():
    assert rules.luhn_ok("4111111111111111")
    assert not rules.luhn_ok("4111111111111112")


def test_uscc():
    assert rules.uscc_ok("91350100M000100Y43")
    assert not rules.uscc_ok("91350100M000100Y44")


def test_find_phone_and_id():
    idn = _id_with_check("11010519491231002")
    hits = rules.find(f"电话13800138000身份证{idn}")
    types = {t for t, _, _ in hits}
    assert {"PHONE", "ID_CARD"} <= types


def test_dates_are_not_redacted():
    # 日期一律不遮：规则不应把日期识别成任何实体
    for text in ["2025-08-02", "2025年08月02日", "20250802", "2025/8/2 12:03:39", "入院时间2025年08月02日09时18分"]:
        assert rules.find(text) == [], text


def test_hospital_name():
    hits = rules.find("转入上级医院治疗，本院为某某市第一人民医院")
    names = [t for t, a, b in hits]
    assert names.count("ORG") == 1


def _line(text: str, x0: float = 0, w: float = 10, h: float = 10, gaps: dict | None = None) -> Line:
    chars, x = [], x0
    for i, ch in enumerate(text):
        x += (gaps or {}).get(i, 0)
        chars.append(Char(ch, (x, 0, x + w, h)))
        x += w
    return Line(chars, "text")


def test_anchor_name_and_stop():
    page = PageData(0, 1000, 1000, lines=[_line("姓名：张小明性别：女", gaps={6: 20})])
    hits, _ = anchor(page, {"PERSON"})
    assert [(h.type, page.lines[0].text[h.start:h.end]) for h in hits] == [("PERSON", "张小明")]


def test_anchor_staff_roles():
    page = PageData(0, 1000, 1000, lines=[_line("主治医师王一住院医师李二", gaps={4: 12, 6: 30, 10: 12})])
    hits, _ = anchor(page, {"STAFF"})
    vals = sorted(page.lines[0].text[h.start:h.end] for h in hits)
    assert vals == ["李二", "王一"]


def test_anchor_skips_prose():
    # 正文里的“患者……”不是字段
    page = PageData(0, 1000, 1000, lines=[_line("患者无腹痛，大便正常")])
    hits, _ = anchor(page, {"PERSON"})
    assert hits == []
