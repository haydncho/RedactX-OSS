"""字段锚定：先找印刷标签，再定位它对应的填写内容或填写区。

- 标签后能识别出合格取值：产生文字命中（Hit）。
- 取值为空或无法识别（手写、签名）：按标签位置推算填写区，有墨迹就整块遮盖（Region）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..schemas import Hit, Line, PageData, Rect
from .lexicon import BELOW_TYPES, GENERIC, LABELS, MAX_LEN, SIGNATURE_LABELS, STOP_WORDS, TEMPLATE_CHARS

_LABEL_MAX = max(len(k) for k in LABELS)
_STOP_MAX = max(len(k) for k in STOP_WORDS)
SEPS = set("：:;；")
SOFT_SEPS = set("：:()（）[]【】 _＿-—")
PUNCT_PRE = set("：:，,；;。.、()（）[]【】 |｜/")
NOT_NAME = set(
    """本人 无 不详 同上 拒绝 签名 签字 家属 患者 病人 医师 医生 护士 已签 未签 见上 空 未知 自己 其他 其它 同意 不同意 确认 日期 时间
    科室 主任 主治 住院 年龄 性别 男 女 岁 电话 地址 手机""".split()
)
RE_CJK_NAME = re.compile(r"[一-龥·•]{2,5}")
RE_ALNUM = re.compile(r"[0-9A-Za-z\-]+")

# 找不到可识别取值时，填写区的宽度（以字高为单位）
FIELD_WIDTH = {"PERSON": 6, "STAFF": 6, "ORG": 16, "ADDRESS": 20, "ID_CARD": 14, "PHONE": 10, "MEDICAL_ID": 8}


@dataclass
class LabelHit:
    line: int
    start: int
    end: int
    label: str
    type: str


def _match_at(text: str, i: int, vocab, maxlen: int) -> str | None:
    for n in range(min(maxlen, len(text) - i), 1, -1):
        if text[i : i + n] in vocab:
            return text[i : i + n]
    return None


def _gap(line: Line, a: int, b: int) -> float:
    """第 a 个字右边到第 b 个字左边的距离，以行高为单位。"""
    h = max(line.height, 1)
    return (line.chars[b].box[0] - line.chars[a].box[2]) / h


def find_labels(page: PageData) -> list[LabelHit]:
    out: list[LabelHit] = []
    for li, line in enumerate(page.lines):
        text = line.text
        i = 0
        while i < len(text):
            lab = _match_at(text, i, LABELS, _LABEL_MAX)
            if not lab:
                i += 1
                continue
            j = i + len(lab)
            pre_ok = i == 0 or text[i - 1] in PUNCT_PRE or text[i - 1].isdigit() or _gap(line, i - 1, i) > 0.4
            post_ok = j == len(text) or text[j] in SOFT_SEPS or _gap(line, j - 1, j) > 0.4
            # 表单标签位于行首时，手写内容常紧贴标签，被 OCR 识别到同一行
            if not post_ok and i == 0 and lab not in GENERIC and line.source == "ocr":
                post_ok = True
            if pre_ok and post_ok:
                out.append(LabelHit(li, i, j, lab, LABELS[lab]))
                i = j
            else:
                i += 1
    return out


def _valid(kind: str, value: str) -> bool:
    if not value:
        return False
    if kind in ("PERSON", "STAFF"):
        return bool(RE_CJK_NAME.fullmatch(value)) and value not in NOT_NAME and value not in STOP_WORDS
    if kind in ("ID_CARD",):
        return len(RE_ALNUM.sub("", value)) <= 2 and sum(c.isalnum() for c in value) >= 6
    if kind == "PHONE":
        return sum(c.isdigit() for c in value) >= 7
    if kind == "MEDICAL_ID":
        return any(c.isalnum() for c in value) and len(RE_ALNUM.sub("", value)) <= 1 and value not in ("-", "无")
    if kind in ("ORG", "ADDRESS"):
        real = [c for c in value if c not in TEMPLATE_CHARS]
        if kind == "ADDRESS" and not any(c in value for c in "省市县区镇乡村路街道号巷弄室栋幢楼@"):
            return False
        return len(value) >= 4 and len(real) >= 3
    return True


def _starts_with_label(text: str, k: int) -> bool:
    """取值本身以标签或非敏感字段词开头（如“质控日期”“主治医师”），说明这里没有填写内容。"""
    return bool(_match_at(text, k, STOP_WORDS, _STOP_MAX))


def _take_value(line: Line, k: int, kind: str) -> tuple[int, int]:
    """从第 k 个字开始取值，返回 [k, end)。"""
    text = line.text
    while k < len(text) and text[k] in SOFT_SEPS:
        k += 1
    end = k
    limit = MAX_LEN.get(kind, 20)
    while end < len(text) and end - k < limit:
        if end > k and _gap(line, end - 1, end) > (2.5 if kind in ("ADDRESS", "ORG") else 1.6):
            break
        if end > k and _match_at(text, end, STOP_WORDS, _STOP_MAX):
            break
        ch = text[end]
        if ch in "，。；;,|｜":
            break
        if kind in ("PERSON", "STAFF") and not ("一" <= ch <= "龥" or ch in "·•"):
            break
        if kind in ("PHONE", "ID_CARD", "MEDICAL_ID") and not (ch.isalnum() or ch in "-－— "):
            break
        end += 1
    return k, end


def _right_neighbor(page: PageData, lab_line: Line, lab_end_x: float, skip: int) -> tuple[int, int] | None:
    """标签所在行右侧紧挨着的另一行（OCR 常把标签和手写内容切成两行）。"""
    h = max(lab_line.height, 1)
    y0, y1 = lab_line.box[1], lab_line.box[3]
    cy = (y0 + y1) / 2
    best = None
    for li, ln in enumerate(page.lines):
        if li == skip or not ln.chars:
            continue
        bx0, by0, bx1, by1 = ln.box
        if not (lab_end_x - 0.5 * h <= bx0 <= lab_end_x + 10 * h):
            continue
        ncy = (by0 + by1) / 2
        if not (y0 - 0.3 * h <= ncy <= y1 + 0.3 * h):
            continue
        if best is None or bx0 < page.lines[best].box[0]:
            best = li
    return (best, 0) if best is not None else None


def row_stops(page: PageData, cy: float, h: float) -> list[float]:
    """与标签同一行（纵向重叠）的所有标签、停止词的起点 x 坐标，从左到右。"""
    xs = []
    for ln in page.lines:
        if not ln.chars:
            continue
        by0, by1 = ln.box[1], ln.box[3]
        if not (by0 - 0.3 * h <= cy <= by1 + 0.3 * h):
            continue
        t = ln.text
        for i in range(len(t)):
            if _match_at(t, i, STOP_WORDS, _STOP_MAX) or t[i] in TEMPLATE_CHARS - {" "}:
                xs.append(ln.chars[i].box[0])
    return sorted(xs)


def anchor(page: PageData, enabled: set[str]) -> tuple[list[Hit], list[tuple[str, str, Rect]]]:
    """返回 (文字命中, 待验墨迹的填写区 [(类型, 标签, 右侧区域, 下方区域)])。"""
    hits: list[Hit] = []
    fields: list[tuple[str, str, Rect | None, Rect | None]] = []
    labels = find_labels(page)
    for lh in labels:
        kind = lh.type
        is_sig = lh.label in SIGNATURE_LABELS
        if kind not in enabled and not (is_sig and "SIGNATURE" in enabled):
            continue
        line = page.lines[lh.line]
        k, e = _take_value(line, lh.end, kind)
        value = line.text[k:e]
        if _starts_with_label(line.text, k):
            value = ""
        if _valid(kind, value) and kind in enabled:
            hits.append(Hit(kind, "anchor", page.index, lh.line, k, e, value))
            continue
        nb = _right_neighbor(page, line, line.chars[lh.end - 1].box[2], lh.line)
        if nb is not None:
            nl = page.lines[nb[0]]
            k2, e2 = _take_value(nl, 0, kind)
            v2 = "" if _starts_with_label(nl.text, k2) else nl.text[k2:e2]
            if _valid(kind, v2) and kind in enabled:
                hits.append(Hit(kind, "anchor", page.index, nb[0], k2, e2, v2))
                continue
        # 推算填写区：标签右侧到同一行下一个标签或非敏感字段之间
        lab_chars = line.chars[lh.start : lh.end]
        h = max(sorted(c.box[3] - c.box[1] for c in lab_chars)[len(lab_chars) // 2], 1)
        lx0, lx1 = lab_chars[0].box[0], lab_chars[-1].box[2]
        ly0, ly1 = min(c.box[1] for c in lab_chars), max(c.box[3] for c in lab_chars)
        cy = (ly0 + ly1) / 2
        right = lx1 + FIELD_WIDTH.get(kind, 8) * h
        for sx in row_stops(page, cy, h):
            if sx > lx1 + 0.4 * h:
                right = min(right, sx - 0.25 * h)
                break
        ftype = "SIGNATURE" if is_sig else "HANDWRITTEN_FIELD"
        right_rect = (lx1 + 0.15 * h, cy - 0.8 * h, right, cy + 0.8 * h) if right - lx1 > 1.2 * h else None
        below_rect = None
        if is_sig or kind in BELOW_TYPES:
            # 表格列头式的栏目：填写区在标签正下方（仅当右侧没有笔迹时采用）
            below_rect = (lx0 - 0.4 * h, ly1 + 0.15 * h, lx1 + 0.4 * h, ly1 + 2.3 * h)
        if right_rect or below_rect:
            fields.append((ftype, lh.label, right_rect, below_rect))
    return hits, fields
