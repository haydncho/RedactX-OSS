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


def test_role_word_plus_doctor_with_colon():
    # “谈话医生：”：词表里没有“谈话医生”，“医生”前面紧挨着“话”，带冒号时仍是医护标签
    page = PageData(0, 1000, 1000, lines=[_ocr_line("谈话医生：")])
    _, fields = anchor(page, {"STAFF"})
    assert [f[1] for f in fields] == ["医生"]
    # 正文里的“医生”（无冒号）不受影响
    assert anchor(PageData(0, 1000, 1000, lines=[_ocr_line("由谈话医生告知病情")]), {"STAFF"}) == ([], [])


def test_form_words_are_not_names():
    # “执行时间”“评估者”里的“执行”“评估”、“谈话”等表单用词不是姓名（否则当成种子会在全文误遮）
    for text in ["上级医师 执行时间", "医师签名 评估", "护士 谈话", "医师 告知"]:
        ln = _line(text, gaps={text.index(" "): 60})
        hits, _ = anchor(PageData(0, 1000, 1000, lines=[ln]), {"STAFF", "SIGNATURE"})
        assert hits == [], text
    # 5 个字的取值不是姓名，复姓与带“·”的除外
    assert anchor(PageData(0, 1000, 1000, lines=[_line("医师：查看患者后")]), {"STAFF"})[0] == []
    assert anchor(PageData(0, 1000, 1000, lines=[_line("医师：如期完成手")]), {"STAFF"})[0] == []
    for name in ["欧阳娜娜子", "阿依·古丽"]:
        ln = _line("医师：" + name)
        assert [ln.text[h.start:h.end] for h in anchor(PageData(0, 1000, 1000, lines=[ln]), {"STAFF"})[0]] == [name], name


def test_hospital_name_not_swallowing_sentence():
    orgs = lambda t: [t[a:b] for k, a, b in rules.find(t) if k == "ORG"]
    assert orgs("患者在我院住院期间如需转往上级医院") == []
    assert orgs("本人同意在澄岚市第一人民医院接受手术治疗") == ["澄岚市第一人民医院"]
    assert orgs("住院告知：患者须知澄岚市中医院规定") == ["澄岚市中医院"]
    assert orgs("经治医生告知患者于澄岚市中医院就诊") == ["澄岚市中医院"]
    # 名称里本身带“和”“向”“平”的不能截
    assert orgs("北京协和医院") == ["北京协和医院"]
    assert orgs("转入向阳医院治疗") == ["向阳医院"]


def test_common_words_and_weak_names_do_not_seed():
    from redactx.detect.engine import collect_seeds

    # “患者信息”：“信”虽是常见姓氏，“信息”是常用词，不是姓名
    assert anchor(PageData(0, 1000, 1000, lines=[_ocr_line("患者信息")]), {"PERSON"})[0] == []
    # 通用词后无冒号认出的姓名只遮本处，不作全文追踪的种子；带冒号的照常作种子
    weak, _ = anchor(PageData(0, 1000, 1000, lines=[_ocr_line("医生郑杰雪")]), {"STAFF"})
    strong, _ = anchor(PageData(0, 1000, 1000, lines=[_ocr_line("医生：郑杰雪")]), {"STAFF"})
    assert [h.value for h in weak] == ["郑杰雪"] and collect_seeds(weak, []) == {}
    assert collect_seeds(strong, []) == {"郑杰雪": "STAFF"}


def test_company_name_not_swallowing_sentence():
    orgs = lambda t: [t[a:b] for k, a, b in rules.find(t) if k == "ORG"]
    assert orgs("患者同意由澄岚晨星科技有限公司支付费用") == ["澄岚晨星科技有限公司"]


def test_weak_sources_seed_only_with_surname():
    from redactx.detect.engine import collect_seeds

    seeds = lambda text: collect_seeds(anchor(PageData(0, 1000, 1000, lines=[_line(text, gaps={text.index("：") + 1: 4} if "：" in text else None)]), {"PERSON", "STAFF"})[0], [])
    # 明确标签后：姓不在常见姓氏表里（罕见姓或首字被认错）也作种子
    assert seeds("姓名：乜翀") == {"乜翀": "PERSON"}
    assert seeds("科主任：乜翀") == {"乜翀": "STAFF"}
    # 通用词后：首字须是常见姓氏
    assert seeds("医师：乜翀") == {}
    assert seeds("医师：王一") == {"王一": "STAFF"}
    # 以病历用语开头的一律不作种子
    from redactx.schemas import Hit
    assert collect_seeds([Hit("STAFF", "anchor", 0, 0, 0, 4, "康复治疗")], []) == {}


def test_landline_with_ocr_dot_needs_phone_cue():
    from redactx.detect import rules

    phones = lambda t: [t[a:b] for ty, a, b in rules.find(t) if ty == "PHONE"]  # noqa: E731
    assert phones("地址：某路30号电话：0522.3964869") == ["0522.3964869"]
    assert phones("金额 010.12345678元") == []
    assert phones("编码0571 88886666") == []


def test_label_chain_field_starts_after_colon():
    # “受委托人/亲属（或监护人）：”是一个标签：填写区从冒号之后开始，不被紧挨着的“亲属”截断
    page = PageData(0, 1000, 1000, lines=[_line("受委托人/亲属（或监护人）：")])
    _, fields = anchor(page, {"PERSON"})
    assert len(fields) == 1 and fields[0][2] is not None
    assert fields[0][2][0] >= page.lines[0].chars[-1].box[2]


def test_checkbox_option_is_not_a_field():
    page = PageData(0, 1000, 1000, lines=[_line("主管医师☑主管护士☑")])
    hits, fields = anchor(page, {"STAFF"})
    assert hits == [] and fields == []


def test_new_labels():
    page = PageData(0, 1000, 1000, lines=[_line("医保编号：123456789012345"), _line("卡号：6222000011112222333"),
                                          _line("主诊医师姓名：王小明"), _line("联系人地址：某某市某某区某某路1号")])
    hits, _ = anchor(page, {"MEDICAL_ID", "BANK_CARD", "STAFF", "ADDRESS"})
    assert {(h.type, h.value) for h in hits} == {("MEDICAL_ID", "123456789012345"), ("BANK_CARD", "6222000011112222333"),
                                                 ("STAFF", "王小明"), ("ADDRESS", "某某市某某区某某路1号")}


def test_code_after_role_is_not_a_name():
    # “责任护士代码：N123”里的“代码”不是姓名（否则会作为种子把全文的“疾病代码”都遮掉）
    page = PageData(0, 1000, 1000, lines=[_line("责任护士代码：N8678381085")])
    hits, _ = anchor(page, {"STAFF", "MEDICAL_ID"})
    assert all(h.type != "STAFF" for h in hits)


def test_bank_word_before_card_is_not_org():
    assert not [t for t, _, _ in rules.find("原路退回银行卡") if t == "ORG"]


def test_repeated_ner_names_become_seeds():
    from redactx.detect.engine import collect_seeds
    from redactx.schemas import Hit

    hits = [Hit("PERSON", "ner", 0, 1, 0, 3, "黄小萍"), Hit("PERSON", "ner", 0, 2, 0, 3, "黄小萍"), Hit("PERSON", "ner", 0, 3, 0, 3, "何其多")]
    seeds = collect_seeds(hits, [])
    assert seeds.get("黄小萍") == "PERSON"  # 两处独立认出
    assert "何其多" not in seeds  # 只认出一次


def test_name_before_role_in_ward_round_title():
    from redactx.detect.anchors import round_names

    page = PageData(0, 1000, 1000, lines=[_line("王志强主治医师查房记录"), _line("2025年09月02日09:00赵立新副主任医师查房记录"),
                                          _line("主任医师查房记录"), _line("无上级医师查房记录")])
    assert [(h.line, h.value) for h in round_names(page)] == [(0, "王志强"), (1, "赵立新")]


def test_label_inside_longer_plain_word_and_prefixed_role():
    page = PageData(0, 1000, 1000, lines=[_line("床位费315.00"), _line("医保机构经办人：王小明"), _line("定点医疗机构代码：H12345678901")])
    hits, fields = anchor(page, {"MEDICAL_ID", "STAFF", "USCC"})
    assert {(h.type, h.value) for h in hits} == {("STAFF", "王小明"), ("USCC", "H12345678901")}
    assert all(f[1] != "床位" for f in fields)


def test_number_value_stops_at_cjk_and_address_after_generic_label_seeds():
    page = PageData(0, 1000, 1000, lines=[_line("电话：13912345678某某省某某市"), _line("地址：某某省某某市某某镇长青街8号")])
    hits, _ = anchor(page, {"PHONE", "ADDRESS"})
    assert [(h.type, h.value, h.seed) for h in hits] == [("PHONE", "13912345678", True), ("ADDRESS", "某某省某某市某某镇长青街8号", True)]


def test_sign_note_in_brackets_is_part_of_label():
    for text in ("直系亲属/近亲属/委托代理人(摁手印)：", "见证人（签字）："):
        page = PageData(0, 1000, 1000, lines=[_line(text)])
        hits, fields = anchor(page, {"PERSON", "SIGNATURE"})
        assert hits == []  # 括号里的“摁手印”“签字”不是姓名
        assert len(fields) == 1 and fields[0][2][0] >= page.lines[0].chars[-1].box[2]  # 填写区从冒号之后开始


def test_relation_words_in_prose_are_not_fields():
    page = PageData(0, 1000, 1000, lines=[_ocr_line("如果患者无法签署，可由直系亲属/近亲属/委托代理人(代理律师等)签署。")])
    hits, fields = anchor(page, {"PERSON", "SIGNATURE"})
    assert hits == [] and fields == []


def test_quality_control_row():
    # OCR 把“质控护士”读成“质检护士”：“质检”不是质控医师的姓名，后面的“陈晓梅”才是护士姓名
    text = "病案质量：甲乙丙质控医师质检护士陈晓梅质检日期2023年10月29日"
    ln = _line(text, gaps={text.index(w): 30 for w in ("质控医师", "质检护士", "陈晓梅", "质检日期")})
    ln.source = "ocr"
    page = PageData(0, 1000, 1000, lines=[ln])
    hits, _ = anchor(page, {"STAFF", "SIGNATURE"})
    assert [(h.type, h.value) for h in hits] == [("STAFF", "陈晓梅")]


def test_ner_name_standing_alone_twice_seeds():
    from redactx.detect.engine import collect_seeds
    from redactx.schemas import Hit

    pages = [PageData(0, 1000, 1000, lines=[_line("林小慧"), _line("体温正常"), _line("林小慧")])]
    seeds = collect_seeds([Hit("PERSON", "ner", 0, 9, 0, 3, "林小慧")], [], pages)
    assert seeds.get("林小慧") == "PERSON"
