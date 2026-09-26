"""结构化实体规则：正则加校验位。日期一律不遮，规则上把日期排除在外。"""

from __future__ import annotations

import re
from datetime import date

_ID_W = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
_ID_C = "10X98765432"


def id_card_ok(s: str) -> bool:
    s = s.upper()
    if not re.fullmatch(r"\d{17}[\dX]", s):
        return False
    try:
        date(int(s[6:10]), int(s[10:12]), int(s[12:14]))
    except ValueError:
        return False
    if not 1900 <= int(s[6:10]) <= 2100:
        return False
    return _ID_C[sum(int(a) * b for a, b in zip(s[:17], _ID_W)) % 11] == s[17]


def luhn_ok(s: str) -> bool:
    total, alt = 0, False
    for d in reversed(s):
        n = int(d)
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        total += n
        alt = not alt
    return total % 10 == 0


_USCC_CHARS = "0123456789ABCDEFGHJKLMNPQRTUWXY"
_USCC_W = [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28]


def uscc_ok(s: str) -> bool:
    s = s.upper()
    if len(s) != 18 or any(c not in _USCC_CHARS for c in s):
        return False
    total = sum(_USCC_CHARS.index(c) * w for c, w in zip(s[:17], _USCC_W))
    return _USCC_CHARS[(31 - total % 31) % 31] == s[17]


RE_ID = re.compile(r"(?<![0-9A-Za-z])\d{17}[\dXx](?![0-9A-Za-z])")
RE_MOBILE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d[- ]?\d{4}[- ]?\d{4}(?!\d)")
RE_LANDLINE = re.compile(r"(?<![\d-])0\d{2,3}[-－—]\d{7,8}(?!\d)")
# 低质量扫描里连字符常被 OCR 认成点、空格或下划线（“电话：0522.3964869”）：前面紧挨着电话字样时才算，免得把金额、小数当成电话
RE_LANDLINE_LOOSE = re.compile(r"(?:电话|联系|手机|座机|传真|[Tt][Ee][Ll])[^\d]{0,4}(0\d{2,3}[.·_ ]\d{7,8})(?![\d.])")
RE_BANK = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
RE_USCC = re.compile(r"(?<![0-9A-Z])[0-9A-HJ-NP-RTUWXY]{2}\d{6}[0-9A-HJ-NP-RTUWXY]{10}(?![0-9A-Z])")
RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
RE_PLATE = re.compile(r"[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼][A-HJ-NP-Z][A-HJ-NP-Z0-9]{4}[A-HJ-NP-Z0-9挂学警港澳]{1,2}")
# 日期形态，用于排除
RE_DATE = re.compile(r"(?:19|20)\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}|(?:19|20)\d{6}(?:\d{4,6})?")

HOSPITAL_SUFFIX = r"(?:医院|卫生院|卫生服务中心|卫生服务站|保健院|医疗中心|医学中心|诊所|门诊部|急救中心|疾病预防控制中心|体检中心|血站|医科大学|医学院)"
RE_HOSPITAL = re.compile(r"([一-龥]{2,20}?" + HOSPITAL_SUFFIX + r")(?:[一-龥]{0,6}(?:院区|分院))?")
_HOSPITAL_BAD_PREFIX = ("转入", "转出", "转往", "送往", "前往", "送至", "转至", "就诊于", "住院于", "其他", "上级", "本院", "该院", "我院", "贵院", "外院", "医嘱", "转院", "拟接收", "接收", "当地", "各级", "定点", "基层", "社区卫生", "乡镇", "医疗机构", "同级", "专科", "综合", "到", "至", "在", "去", "于", "往", "来", "及", "或", "和", "等")
ORG_SUFFIX = r"(?:有限责任公司|股份有限公司|有限公司|集团|银行|保险公司|事务所|委员会|管理局|人民政府)"
RE_ORG = re.compile(r"[一-龥（）()]{2,30}?" + ORG_SUFFIX)


# 医院名前面的句子成分：出现在“医院”之前时，在它之后截断。只用几乎不会出现在院名里的词，
# 不用“和”“向”“平”等（北京协和医院、向阳医院、和平医院）
_NAME_BOUNDARY_WORDS = ("患者", "本人", "家属", "同意", "告知", "须知", "住院", "入院", "出院", "就诊", "转诊", "转往", "转至", "转入", "转出",
                        "期间", "治疗", "手术", "我院", "本院", "该院", "贵院", "如需", "需要", "以下", "前往", "送往", "签署", "协议", "规定", "所有")
_NAME_BOUNDARY_CHARS = set("在到于往至经由从的是请对")


def _name_start(name: str, suffix: str = HOSPITAL_SUFFIX) -> int:
    """名称在 name 中的起点：机构后缀之前最后一个句子成分之后。"""
    m = re.search(suffix, name)
    head = name[: m.start()] if m else name
    cut = 0
    for w in _NAME_BOUNDARY_WORDS:
        i = head.rfind(w)
        if i >= 0:
            cut = max(cut, i + len(w))
    for i, ch in enumerate(head):
        if ch in _NAME_BOUNDARY_CHARS:
            cut = max(cut, i + 1)
    return cut


def _spans(regex: re.Pattern, text: str):
    for m in regex.finditer(text):
        yield m.start(), m.end(), m.group(0)


def find(text: str) -> list[tuple[str, int, int]]:
    """在一行紧凑文本（已去空白）上找结构化实体，返回 (类型, 起, 止)。"""
    hits: list[tuple[str, int, int]] = []
    taken: list[tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= x or a >= y for x, y in taken)

    def add(t: str, a: int, b: int) -> None:
        hits.append((t, a, b))
        taken.append((a, b))

    for a, b, s in _spans(RE_ID, text):
        if id_card_ok(s):
            add("ID_CARD", a, b)
    for a, b, s in _spans(RE_USCC, text):
        if free(a, b) and uscc_ok(s):
            add("USCC", a, b)
    for a, b, s in _spans(RE_MOBILE, text):
        if free(a, b):
            add("PHONE", a, b)
    for a, b, s in _spans(RE_LANDLINE, text):
        if free(a, b):
            add("PHONE", a, b)
    for m in RE_LANDLINE_LOOSE.finditer(text):
        a, b = m.span(1)
        if free(a, b):
            add("PHONE", a, b)
    for a, b, s in _spans(RE_BANK, text):
        if free(a, b) and luhn_ok(s) and not RE_DATE.fullmatch(s):
            add("BANK_CARD", a, b)
    for a, b, s in _spans(RE_EMAIL, text):
        if free(a, b):
            add("EMAIL", a, b)
    for a, b, s in _spans(RE_PLATE, text):
        if free(a, b):
            add("PLATE", a, b)
    for m in RE_HOSPITAL.finditer(text):
        name = m.group(0)
        a = m.start()
        # 正则会从一串连续汉字的开头匹配：“患者在我院住院期间……某某医院”要截到最后一个虚词、动词之后，只留名称本身
        cut = _name_start(name)
        name, a = name[cut:], a + cut
        # 去掉“转入”“至”等动词前缀带来的误判
        while name and name.startswith(_HOSPITAL_BAD_PREFIX):
            cut = next(len(p) for p in _HOSPITAL_BAD_PREFIX if name.startswith(p))
            name, a = name[cut:], a + cut
        core = re.sub(HOSPITAL_SUFFIX + ".*$", "", name)
        if len(core) >= 2 and free(a, a + len(name)):
            add("ORG", a, a + len(name))
    for a, b, s in _spans(RE_ORG, text):
        cut = _name_start(s, ORG_SUFFIX)
        a, s = a + cut, s[cut:]
        if free(a, b) and len(s) >= 5:
            add("ORG", a, b)
    return hits
