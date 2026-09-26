"""各类证件号、联系方式与特殊写法姓名的识别（第 8 页患者信息登记表涉及的写法）。"""

from redactx import vision
from redactx.detect import anchors, ner, rules
from redactx.schemas import Char, Line, PageData


def _found(text, typ):
    return [text[a:b] for t, a, b in rules.find(text) if t == typ]


def test_rules_formats():
    assert _found("车牌号：京E·31979", "PLATE") == ["京E·31979"]
    assert _found("单位电话：0539-8591248转7774", "PHONE") == ["0539-8591248转7774"]
    assert _found("患者持护照（P99768653）办理入院", "ID_CARD") == ["P99768653"]
    assert _found("检验编码E12345678", "ID_CARD") == []  # 没有证件字样不算
    assert _found("银行卡6222 0212 3456 7890 128缴纳", "BANK_CARD") == ["6222 0212 3456 7890 128"]
    assert _found("编号6222 0212 3456 7890 127", "BANK_CARD") == []  # 不满足 Luhn 校验


def test_value_checks():
    assert anchors._valid("PERSON", "阿依古丽·买买提")
    assert anchors._valid("PERSON", "Li Wei") and anchors._valid("PERSON", "LiWei")
    assert anchors._valid("ID_CARD", "军字第3768545号")
    assert anchors._valid("PHONE", "wxid_cne0bahbaq")
    assert not anchors._valid("PHONE", "无")
    assert anchors._valid("BANK_CARD", "6222 0212 3456 7890 128")


def _line(text, x=0, y=100):
    return Line([Char(ch, (x + k * 30, y, x + k * 30 + 28, y + 30)) for k, ch in enumerate(text)], "text")


def test_long_names_are_taken_whole():
    line = _line("姓名：阿依古丽·买买提")
    assert line.text[slice(*anchors._take_value(line, 3, "PERSON"))] == "阿依古丽·买买提"
    line = _line("英文姓名：Li Wei")
    assert line.text[slice(*anchors._take_value(line, 5, "PERSON"))] == "Li Wei"


def test_ner_roles():
    t = "经张三丰、李四光两位医师共同讨论，由家属王五陪同。"
    spans = [(t.index(n), t.index(n) + len(n)) for n in ("张三丰", "李四光", "王五")]
    assert ner._roles(t, spans) == ["STAFF", "STAFF", "PERSON"]
    t = "患者赵六家属钱七要求转院。"
    spans = [(t.index(n), t.index(n) + 2) for n in ("赵六", "钱七")]
    assert ner._roles(t, spans) == ["PERSON", "PERSON"]


def test_logo_beside_hospital_name():
    name = (200, 60, 520, 100)
    assert vision.beside_name((100, 40, 180, 120), [name])  # 院名左边
    assert not vision.beside_name((100, 400, 180, 480), [name])  # 不同高度
    assert not vision.beside_name((900, 40, 980, 120), [name])  # 离得太远
