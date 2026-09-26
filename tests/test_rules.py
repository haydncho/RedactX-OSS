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


def test_anchor_skips_generic_label_at_line_end():
    # 正文折行恰好停在“（患者”，不应推算出填写区
    page = PageData(0, 1000, 1000, lines=[_line("由家属陪同就诊（患者")])
    hits, fields = anchor(page, {"PERSON"})
    assert hits == [] and fields == []


def test_anchor_label_alone_on_line():
    # 表格里单独一格的“签名”，填写内容在下方
    page = PageData(0, 1000, 1000, lines=[_line("签名")])
    _, fields = anchor(page, {"STAFF", "SIGNATURE"})
    assert [f[0] for f in fields] == ["SIGNATURE"]


def _ocr_line(text: str) -> Line:
    ln = _line(text)
    ln.source = "ocr"
    return ln


def test_date_then_signature():
    from redactx.detect.anchors import DATE_SIGN, DATE_SIGN_CUT, date_signatures

    # OCR 把手写日期和签名识别成一行：连笔签名常只认出一两个字，日的数字还可能被签名吞掉
    for text, label in [("2025.10双宇娜", DATE_SIGN_CUT), ("2025.07.清网", DATE_SIGN_CUT), ("2025.10.04娜", DATE_SIGN),
                        ("2025年10月03日王一", DATE_SIGN), ("2025.10.04", DATE_SIGN)]:
        fields = date_signatures(PageData(0, 1000, 1000, lines=[_ocr_line(text)]))
        assert [(f[0], f[1]) for f in fields] == [("SIGNATURE", label)], text
        date_end = _ocr_line(text).chars[len(text.rstrip("双宇娜清网王一娜")) - 1].box[2]
        assert fields[0][2][0] > date_end - 1  # 填写区从日期之后开始
    # 日期后面是正文，不是签名
    for text in ["2025.10.04上级医师查房，同意目前诊疗方案。", "2025年10月01日入院", "2025.10.03主治医师查房记录"]:
        assert date_signatures(PageData(0, 1000, 1000, lines=[_ocr_line(text)])) == [], text


def test_handwritten_name_after_generic_label():
    # “患者”后面紧跟的姓名字更大（手写）：算标签；正文里同样大小的“患者病情平稳”不算
    ln = _ocr_line("患者高强兰")
    for c in ln.chars[2:]:
        c.box = (c.box[0], -3, c.box[2], 13)
    hits, _ = anchor(PageData(0, 1000, 1000, lines=[ln]), {"PERSON"})
    assert [ln.text[h.start:h.end] for h in hits] == ["高强兰"]
    hits, _ = anchor(PageData(0, 1000, 1000, lines=[_ocr_line("患者病情平稳，医生建议明日出院。")]), {"PERSON", "STAFF"})
    assert hits == []


def test_label_then_name_at_line_end():
    # OCR 常给整行统一字高：“医生郑杰雪”整行就是标签加姓名，算字段
    hits, _ = anchor(PageData(0, 1000, 1000, lines=[_ocr_line("医生郑杰雪")]), {"STAFF"})
    assert [(h.type, "医生郑杰雪"[h.start:h.end]) for h in hits] == [("STAFF", "郑杰雪")]
    # 以姓氏字开头的病历常用词不算
    for text in ["患者高热", "患者平稳", "医生查体"]:
        assert anchor(PageData(0, 1000, 1000, lines=[_ocr_line(text)]), {"PERSON", "STAFF"})[0] == [], text


def test_role_plus_sign_labels():
    # “上级医师签名：”合并成一个签名标签；“谈话医师签名：”前面是词表外的角色词也算
    for text, label in [("上级医师签名：", "上级医师签名"), ("谈话医师签名：", "医师签名")]:
        page = PageData(0, 1000, 1000, lines=[_ocr_line(text)])
        _, fields = anchor(page, {"STAFF", "SIGNATURE"})
        assert [(f[0], f[1]) for f in fields] == [("SIGNATURE", label)], text


def test_sign_label_at_sentence_end():
    page = PageData(0, 1000, 1000, lines=[_ocr_line("我已了解上述风险，自愿接受治疗。签名")])
    _, fields = anchor(page, {"STAFF", "SIGNATURE"})
    assert [f[0] for f in fields] == ["SIGNATURE"]


def test_far_printed_text_is_not_the_name():
    # 可搜索 PDF 的文字层里没有手写字：“患者 ……（空白）…… 已阅读并理解上述内容”，后面的打印字不是姓名
    ln = _line("患者已阅读并理解上述内容。", gaps={2: 120})
    hits, fields = anchor(PageData(0, 1000, 1000, lines=[ln]), {"PERSON"})
    assert hits == []
    assert len(fields) == 1 and fields[0][2][2] < ln.chars[2].box[0]  # 填写区止于打印文字之前


def test_far_short_name_is_kept():
    # 签名栏与姓名隔得远、姓不在常见姓氏表里：独立的两个字仍是姓名（否则整份文档的全文追踪都会丢）
    ln = _line("上级医师 乜翀", gaps={5: 60})
    hits, _ = anchor(PageData(0, 1000, 1000, lines=[ln]), {"STAFF"})
    assert [ln.text[h.start:h.end] for h in hits] == ["乜翀"]


def test_very_far_word_is_not_the_name():
    # 离签名栏十几个字高的独立短词属于别的字段，不能当成姓名种子
    ln = _line("麻醉医师签名 护理", gaps={7: 150})
    hits, _ = anchor(PageData(0, 1000, 1000, lines=[ln]), {"STAFF", "SIGNATURE"})
    assert hits == []
