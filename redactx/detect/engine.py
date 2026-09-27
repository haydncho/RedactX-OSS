"""识别编排：单页识别（规则、锚定、自定义词）与文档内实体传播。"""

from __future__ import annotations

import re
from collections import Counter

from rapidfuzz.distance import Hamming

from ..schemas import Hit, PageData
from . import ner, rules, templates
from .anchors import NOT_NAME, anchor, date_signatures, round_names, table_signatures
from .lexicon import STOP_WORDS, surname_start

SEED_TYPES = {"PERSON", "STAFF", "ORG", "ADDRESS", "ID_CARD", "PHONE", "MEDICAL_ID", "BANK_CARD", "USCC", "CUSTOM"}
_CJK = re.compile(r"^[一-龥·•]+$")


def text_hits(page: PageData, enabled: set[str], custom: list[str]) -> list[Hit]:
    """号码规则与自定义词的命中（出厂自检只用这两类）。"""
    hits: list[Hit] = []
    for li, line in enumerate(page.lines):
        text = line.text
        for t, a, b in rules.find(text):
            if t in enabled:
                hits.append(Hit(t, "rule", page.index, li, a, b, text[a:b]))
        if "CUSTOM" in enabled:
            for w in custom:
                for m in re.finditer(re.escape(w), text):
                    hits.append(Hit("CUSTOM", "custom", page.index, li, m.start(), m.end(), w))
    return hits


def page_hits(page: PageData, enabled: set[str], custom: list[str]):
    """单页识别，返回 (文字命中, 待验墨迹的填写区)。"""
    hits = text_hits(page, enabled, custom)
    ahits, fields = anchor(page, enabled)
    hits.extend(ahits)
    if "STAFF" in enabled:
        hits.extend(h for h in round_names(page) if not any(o.line == h.line and o.start < h.end and h.start < o.end for o in hits))
    # 表单模板：补出 OCR 没认出字段名的字段
    thits, tfields = templates.apply(page, enabled, hits)
    hits.extend(thits)
    fields.extend(tfields)
    if {"PERSON", "STAFF"} & enabled:
        # 叙述里的人名：与已有命中重叠的不再重复
        for h in ner.page_names(page, enabled):
            if not any(o.line == h.line and o.start < h.end and h.start < o.end for o in hits):
                hits.append(h)
    if "SIGNATURE" in enabled:
        fields.extend(date_signatures(page))
        fields.extend(table_signatures(page))
    return hits, fields


def collect_seeds(all_hits: list[Hit], custom: list[str], pages: list[PageData] | None = None) -> dict[str, str]:
    """从高置信度命中里取种子：值 -> 类型。种子只在本任务内存中存在。
    pages 给出时，用来数姓名单独成行（签名栏、执行人栏）的次数。"""
    seeds: dict[str, str] = {}
    for h in all_hits:
        v = h.value
        if h.type not in SEED_TYPES or not v or not h.seed:
            continue
        if h.type in ("PERSON", "STAFF"):
            if not _CJK.match(v) or len(v) < 2 or v in STOP_WORDS:
                continue
            if h.source not in ("anchor", "custom"):
                continue
            # 以常见姓氏字开头的病历用语（康复治疗、高热、信息）也不作种子
            if any(v.startswith(w) for w in NOT_NAME if len(w) >= 2):
                continue
        elif h.type == "ORG":
            if len(v) < 4:
                continue
        elif h.type == "ADDRESS":
            if len(v) < 8:  # 只追踪完整的地址，不追踪“某某市某某区”这类短地名
                continue
        elif len(v) < 5:
            continue
        # 同一个值被识别为不同类型时，人名类优先
        if v not in seeds or h.type in ("PERSON", "STAFF"):
            seeds[v] = h.type
    # 正文人名模型认出的姓名单次不作种子（证据弱）；同一姓名在文档里至少两处独立认出、首字是常见姓氏时，
    # 用它补上模型漏认的其余位置（医嘱单上多行重复的执行护士姓名常只认出一部分）
    ner_lines: dict[str, set] = {}
    for h in all_hits:
        v = h.value
        if h.source == "ner" and h.type in ("PERSON", "STAFF") and v and _CJK.match(v) and 2 <= len(v) <= 4 and surname_start(v):
            ner_lines.setdefault(v, set()).add((h.page, h.line))
    # 模型只认出一次、但在文档里至少两处单独成行的姓名（签名栏里的“林某”）也算
    alone = Counter(ln.text for pd in pages or [] for ln in pd.lines if 2 <= len(ln.text) <= 4)
    for v, where in ner_lines.items():
        if (len(where) >= 2 or alone[v] >= 2) and v not in seeds and v not in STOP_WORDS and not any(v.startswith(w) for w in NOT_NAME if len(w) >= 2):
            seeds[v] = next(h.type for h in all_hits if h.source == "ner" and h.value == v)
    for w in custom:
        seeds.setdefault(w, "CUSTOM")
    return seeds


def propagate(page: PageData, seeds: dict[str, str], existing: list[Hit]) -> list[Hit]:
    """用种子在本页所有行里做精确匹配，3 字以上的姓名允许 1 个错字（OCR 误识）。"""
    out: list[Hit] = []
    covered: dict[int, list[tuple[int, int]]] = {}
    for h in existing:
        covered.setdefault(h.line, []).append((h.start, h.end))

    def free(li: int, a: int, b: int) -> bool:
        return all(b <= x or a >= y for x, y in covered.get(li, []))

    for li, line in enumerate(page.lines):
        text = line.text
        for v, t in seeds.items():
            n = len(v)
            if n > len(text):
                continue
            start = 0
            while True:
                i = text.find(v, start)
                if i < 0:
                    break
                if free(li, i, i + n):
                    out.append(Hit(t, "propagate", page.index, li, i, i + n, v))
                    covered.setdefault(li, []).append((i, i + n))
                start = i + 1
            if t in ("PERSON", "STAFF") and n >= 3:
                for i in range(0, len(text) - n + 1):
                    win = text[i : i + n]
                    if win != v and _CJK.match(win) and Hamming.distance(win, v) == 1 and win[0] == v[0] and free(li, i, i + n):
                        out.append(Hit(t, "propagate", page.index, li, i, i + n, v))
                        covered.setdefault(li, []).append((i, i + n))
            elif t == "ORG" and n >= 6:
                # 医院名称：允许 OCR 错 1–2 个字（水印、印章附近常见）
                for i in range(0, len(text) - n + 1):
                    win = text[i : i + n]
                    if win != v and win[-1] == v[-1] and Hamming.distance(win, v) <= max(1, n // 6) and free(li, i, i + n):
                        out.append(Hit(t, "propagate", page.index, li, i, i + n, v))
                        covered.setdefault(li, []).append((i, i + n))
    return out
