"""字段锚定：先找印刷标签，再定位它对应的填写内容或填写区。

- 标签后能识别出合格取值：产生文字命中（Hit）。
- 取值为空或无法识别（手写、签名）：按标签位置推算填写区，有墨迹就整块遮盖（Region）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..schemas import Hit, Line, PageData, Rect
from .lexicon import BELOW_TYPES, COMPOUND_SURNAMES, GENERIC, LABELS, MAX_LEN, SIGNATURE_LABELS, STOP_WORDS, SURNAMES, TEMPLATE_CHARS

_LABEL_MAX = max(len(k) for k in LABELS)
_STOP_MAX = max(len(k) for k in STOP_WORDS)
SEPS = set("：:;；")
SIGN_SUFFIX = {"签名", "签字", "签章"}
ROLE_TAILS = {"医生", "医师", "护士"}
SOFT_SEPS = set("：:()（）[]【】 _＿-—")
PUNCT_PRE = set("：:，,；;。.、()（）[]【】 |｜/")
NOT_NAME = set(
    """本人 无 不详 同上 拒绝 签名 签字 家属 患者 病人 医师 医生 护士 已签 未签 见上 空 未知 自己 其他 其它 同意 不同意 确认 日期 时间
    科室 主任 主治 住院 年龄 性别 男 女 岁 电话 地址 手机 查房 记录 病程 首次 日常 会诊 抢救 讨论 小结 交班 接班 复查 建议 病情
    高热 高烧 高血 黄疸 白细 查体 常规 严重 全身 平稳 安静 明显 康复 于今 于入 包块 方案 时间 目前
    信息 须知 情况 意见 声明 义务 权利 资料 隐私 费用 协议 安全 管理 服务 保护 规定 制度 要求 注意 事项
    谈话 告知 知情 签署 委托 授权 执行 评估 核对 交接 说明 麻醉 护理 手术 治疗 检查 审核 复核 操作 采样 录入
    查看 给予 予以 考虑 继续""".split()
)
RE_CJK_NAME = re.compile(r"[一-龥·•]{2,5}")
# 少数民族姓名（“阿依古丽·买买提”）与英文姓名（“Li Wei”）
RE_DOT_NAME = re.compile(r"[一-龥]{1,8}(?:[·•][一-龥]{1,8}){1,3}")
RE_LATIN_NAME = re.compile(r"[A-Z][a-z]+(?: ?[A-Z][a-z]+){1,2}")  # 文字层常丢掉空格：“LiWei”
# 军官证、士兵证等“军字第××××号”
RE_MIL_ID = re.compile(r"[军士兵警文]{1,2}字?第?\d{6,9}号?")
RE_ALNUM = re.compile(r"[0-9A-Za-z\-]+")

# 找不到可识别取值时，填写区的宽度（以字高为单位）。签名栏一直延伸到本行下一个字段，签名之后的笔迹都遮盖
FIELD_WIDTH = {"PERSON": 6, "STAFF": 6, "ORG": 16, "ADDRESS": 20, "ID_CARD": 14, "PHONE": 10, "MEDICAL_ID": 8}
SIGN_WIDTH = 24
# 标签与取值之间超过这个距离（字高）时，取值须是独立的一小段才当作姓名：可搜索 PDF 的文字层里没有手写字，
# “患者 （手写姓名） 已阅读……”在文字层里是“患者……已阅读……”，不能把后面的一句话当成姓名
FAR_VALUE = 2.5
# 超过这个距离（字高）的取值不再属于这个标签，即使是独立的短词：多半是同一行另一个字段的内容，
# 若当成姓名，会作为种子在全文里被大量误追踪（实测一个常用词被追踪了 157 次）
FAR_NAME_MAX = 10


@dataclass
class LabelHit:
    line: int
    start: int
    end: int
    label: str
    type: str
    weak: bool = False  # 通用词后无冒号、凭“像手写姓名”才认作标签：取值只遮本处，不作种子


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
            kind = LABELS[lab]
            # “上级医师签名”“主治医师签字”：角色标签后紧跟签名字样，合并成一个签名标签（否则角色标签的填写区在“签名”前就截止了）
            suffix = _match_at(text, j, SIGN_SUFFIX, 2) if lab not in SIGNATURE_LABELS else None
            if suffix:
                j += len(suffix)
                lab = text[i:j]
            pre_ok = i == 0 or text[i - 1] in PUNCT_PRE or text[i - 1].isdigit() or _gap(line, i - 1, i) > 0.4
            # “谈话医师签名：”“谈话医生：”“经治医师：”：签名或医护标签后面带冒号时，
            # 前面紧挨着别的字（词表里没有的角色词）也算
            if not pre_ok and ("签" in lab or lab in ROLE_TAILS) and j < len(text) and text[j] in SEPS:
                pre_ok = True
            # 通用词在行尾时没有冒号或间隔可作凭据（多为正文折行，如“……（患者”），只有独占一行才算标签；
            # 签名类标签除外：“……自愿接受治疗。签名 （手写）”里的“签名”正在句末
            at_end = j == len(text) and (lab not in GENERIC or i == 0 or lab in SIGNATURE_LABELS)
            post_ok = at_end or (j < len(text) and (text[j] in SOFT_SEPS or _gap(line, j - 1, j) > 0.4))
            # 表单标签位于行首时，手写内容常紧贴标签，被 OCR 识别到同一行
            if not post_ok and i == 0 and lab not in GENERIC and line.source == "ocr":
                post_ok = True
            # “患者”“医生”等通用词后面紧跟手写姓名、没有冒号时也算标签
            weak = False
            if not post_ok and pre_ok and kind in ("PERSON", "STAFF") and _handwritten_name_after(line, i, j):
                post_ok = weak = True
            if pre_ok and post_ok:
                out.append(LabelHit(li, i, j, lab, kind, weak))
                i = j
            else:
                i += 1
    return out


def _name_run(text: str, j: int) -> int:
    """从 j 开始的连续汉字，遇到标点、非敏感字段名或行尾为止，返回结束位置。"""
    k = j
    while k < len(text) and "一" <= text[k] <= "龥" and k - j < 5:
        if k > j and _match_at(text, k, STOP_WORDS, _STOP_MAX):
            break
        k += 1
    return k


def _handwritten_name_after(line: Line, i: int, j: int) -> bool:
    """通用词后面紧跟的 2–4 个汉字像手写姓名：首字是常见姓氏（或独占行尾且识别置信度明显偏低），并且满足其一——
    姓名正好在行尾（整行就是“医生 + 姓名”）；字比标签高出 15% 以上或识别置信度偏低（手写特征）。
    正文里“患者病情好转，……”之类不会触发。"""
    if line.source != "ocr":
        return False
    text = line.text
    k = _name_run(text, j)
    name = text[j:k]
    if not 2 <= len(name) <= 4 or name in NOT_NAME or name in STOP_WORDS or any(name.startswith(w) for w in NOT_NAME if len(w) >= 2):
        return False
    if name[0] not in SURNAMES and name[:2] not in COMPOUND_SURNAMES:
        # 手写姓名的首字常被 OCR 认错（“彭曦涛”认成“壹细羲涛”）：姓名独占行尾且识别置信度明显偏低时，不要求姓氏。
        # 这类取值证据弱，只遮本处，不作全文追踪的种子
        chars = line.chars[j:k]
        return k == len(text) and sum(c.score for c in chars) / len(chars) < 0.8
    if k < len(text) and text[k] not in PUNCT_PRE and not _match_at(text, k, STOP_WORDS, _STOP_MAX) and _gap(line, k - 1, k) <= 0.4:
        return False
    if k == len(text):
        return True
    lab_h = sorted(c.box[3] - c.box[1] for c in line.chars[i:j])[(j - i) // 2]
    name_chars = line.chars[j:k]
    name_h = sorted(c.box[3] - c.box[1] for c in name_chars)[len(name_chars) // 2]
    mean_score = sum(c.score for c in name_chars) / len(name_chars)
    return name_h >= 1.15 * lab_h or mean_score < 0.85


# 手写日期与紧挨着的签名常被 OCR 识别成一行，“日”的数字还会被签名吞掉（如“2025.10双宇娜”），所以日可缺省
RE_DATE_LOOSE = re.compile(r"(?:19|20)\d{2}\s*[.\-/年]\s*\d{1,2}(?:\s*[.\-/月]\s*(?:\d{1,2}日?)?)?(?:\s*\d{1,2}[:：]\d{2})?")
# 日期后面常见的非姓名词：不当作签名
_AFTER_DATE_WORDS = set("入院 出院 手术 查房 记录 复查 复诊 首次 日常 抢救 会诊 转科 交班 接班 死亡 讨论 小结 病程 术后 术前 上午 下午 晚上 夜间 凌晨".split())


DATE_SIGN = "日期"  # date_signatures 产生的填写区标签
DATE_SIGN_CUT = "日期缺日"  # 日的数字被 OCR 并进了签名：打码前先跳过这两位数字


def date_signatures(page: PageData) -> list[tuple[str, str, Rect, Rect | None]]:
    """病程记录常见“日期 + 医师签名”而签名前没有标签。日期后面只跟着 1–4 个像姓名的汉字（OCR 对连笔签名往往只认出一两个字）
    或日期就在行尾时，把日期右侧推算为签名填写区，交给墨迹检查。右侧有成句的正文则不算。"""
    fields: list[tuple[str, str, Rect, Rect | None]] = []
    for li, line in enumerate(page.lines):
        if line.source != "ocr":
            continue
        text = line.text
        for m in RE_DATE_LOOSE.finditer(text):
            j = m.end()
            k = _name_run(text, j)
            tail = text[j:k]
            if k != len(text) or len(tail) > 4 or any(tail.startswith(w) for w in _AFTER_DATE_WORDS) \
                    or any(tail.startswith(w) for w in NOT_NAME if len(w) >= 2) or tail in STOP_WORDS:
                continue
            d = line.chars[m.start():j]
            h = max(sorted(c.box[3] - c.box[1] for c in d)[len(d) // 2], 1)
            x1 = d[-1].box[2]
            cy = (min(c.box[1] for c in d) + max(c.box[3] for c in d)) / 2
            rect = (x1 + 0.2 * h, cy - 1.1 * h, max(x1 + 8 * h, line.box[2] + 0.5 * h), cy + 1.1 * h)
            if any(o is not line and _overlaps(o.box, rect) and len(o.text) >= 5 for o in page.lines):
                continue
            # 年、月、日三段齐全才算完整日期；只到月（“2025.10”“2025.07.”）说明日的数字被签名吞掉了
            cut = not re.search(r"\d{4}\s*[.\-/年]\s*\d{1,2}\s*[.\-/月]\s*\d{1,2}日?$", text[m.start():j])
            fields.append(("SIGNATURE", DATE_SIGN_CUT if cut else DATE_SIGN, rect, None))
    return fields


def _is_label_line(text: str) -> bool:
    """整行只是一个字段名（可带冒号），或以“字段名：”开头。“患者夜间睡眠尚可”这样以通用词开头的正文不算。"""
    m = _match_at(text, 0, LABELS, _LABEL_MAX) or _match_at(text, 0, STOP_WORDS, _STOP_MAX)
    if not m:
        return False
    rest = text[len(m) :]
    return rest.strip("：: ") in ("", *SIGN_SUFFIX) or rest[:1] in SEPS or any(rest.startswith(s) and rest[len(s) : len(s) + 1] in SEPS for s in SIGN_SUFFIX)


def table_signatures(page: PageData) -> list[tuple[str, str, Rect, Rect | None]]:
    """表格里的签名列：“护士签名”“执行者签名”等作为表头单元格（同一行还有“日期”“时间”等别的表头）时，
    表格每一行在这一列的格子都是签名填写区，交给墨迹检查。行的位置取自其他列（时间、记录内容）——
    手写签名常常连一个字都认不出来，不能靠本列的识别结果；行距突然拉大处（表格下方的落款、页脚）表格结束。"""
    fields: list[tuple[str, str, Rect, Rect | None]] = []
    for lh in find_labels(page):
        if "签" not in lh.label:
            continue
        line = page.lines[lh.line]
        if len(line.text.strip("：: ")) != lh.end - lh.start:
            continue  # 表头单元格里只有这个标签
        x0, y0, x1, y1 = line.box
        h = max(y1 - y0, 1)
        cy = (y0 + y1) / 2
        heads = [ln for ln in page.lines if ln is not line and ln.chars and abs((ln.box[1] + ln.box[3]) / 2 - cy) < 0.6 * h and len(ln.text) <= 12]
        if not heads:
            continue
        left = [b.box[2] for b in heads if b.box[2] <= x0]
        right = [b.box[0] for b in heads if b.box[0] >= x1]
        col_l = (max(left) + x0) / 2 if left else x0 - 2 * h
        col_r = min(right) - 0.3 * h if right else min(page.width, x1 + 4 * (x1 - x0))
        table_l = min(b.box[0] for b in heads + [line]) - h
        cx = lambda ln: (ln.box[0] + ln.box[2]) / 2  # noqa: E731
        # 其他列里的表格内容；以字段名开头的（表格下方的“护士长签名：”之类）不算
        others = sorted((ln for ln in page.lines if ln.chars and ln.box[1] >= y1 - 0.2 * h and (table_l <= cx(ln) < col_l or col_r < cx(ln))
                         and not _is_label_line(ln.text)),
                        key=lambda ln: ln.box[1])
        rows: list[list[Line]] = []
        for ln in others:
            if rows and ln.box[1] < max(o.box[3] for o in rows[-1]) - 0.3 * h:
                rows[-1].append(ln)
            else:
                rows.append([ln])
        tops = [min(o.box[1] for o in r) for r in rows]
        pitch = sorted(b - a for a, b in zip(tops, tops[1:]))[len(tops) // 2 - 1] if len(tops) >= 3 else 3 * h
        prev = y1
        for r, top in zip(rows, tops):
            if top - prev > max(1.8 * pitch, 3 * h):
                break  # 行距突然拉大：表格结束
            bot = max(o.box[3] for o in r)
            # 左边不越过同一行里其他列的文字（记录内容可能写得很长）
            l = max([col_l] + [o.box[2] + 0.3 * h for o in r if o.box[2] <= x0 + 0.5 * h])
            fields.append(("SIGNATURE", lh.label, (l, top - 0.35 * h, col_r, bot + 0.35 * h), None))
            prev = top
    return fields


def _overlaps(a: Rect, b: Rect) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _valid(kind: str, value: str) -> bool:
    if not value:
        return False
    if kind in ("PERSON", "STAFF"):
        # “主治医师查房记录”里的“查房记录”不是姓名：以病历常用词开头的取值一律不算
        if RE_DOT_NAME.fullmatch(value) or RE_LATIN_NAME.fullmatch(value):
            return True
        if not RE_CJK_NAME.fullmatch(value) or value in STOP_WORDS or any(value.startswith(w) for w in NOT_NAME):
            return False
        # 表单用词的前半截（“执行时间”里的“执行”、“评估者”的“评估”）不是姓名：它们一旦当成种子会在全文误遮
        if any(len(w) > len(value) and w.startswith(value) for w in STOP_WORDS):
            return False
        # 中文姓名多为 2–4 字；5 字的只有复姓或带“·”的姓名才算，其余多是一句话的开头
        if len(value.replace("·", "").replace("•", "")) >= 5 and value[:2] not in COMPOUND_SURNAMES and "·" not in value and "•" not in value:
            return False
        return True
    if kind in ("ID_CARD",):
        if RE_MIL_ID.fullmatch(value):
            return True
        return len(RE_ALNUM.sub("", value)) <= 2 and sum(c.isalnum() for c in value) >= 6
    if kind == "PHONE":
        # 电话号码；或微信号、QQ 号等联系方式（字母开头的账号）
        return sum(c.isdigit() for c in value) >= 7 or bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{4,29}", value))
    if kind == "BANK_CARD":
        return sum(c.isdigit() for c in value) >= 12
    if kind == "PLATE":
        return len(value) >= 6 and sum(c.isalnum() for c in value) >= 5
    if kind == "MEDICAL_ID":
        return any(c.isalnum() for c in value) and len(RE_ALNUM.sub("", value)) <= 1 and value not in ("-", "无")
    if kind in ("ORG", "ADDRESS"):
        real = [c for c in value if c not in TEMPLATE_CHARS]
        if kind == "ADDRESS" and not any(c in value for c in "省市县区镇乡村路街道号巷弄室栋幢楼@"):
            return False
        return len(value) >= 4 and len(real) >= 3
    return True


def _surname_start(value: str) -> bool:
    return bool(value) and (value[0] in SURNAMES or value[:2] in COMPOUND_SURNAMES)


def _name_like(line: Line, k: int, e: int) -> bool:
    """离标签较远的取值像姓名：独立的一小段，或首字是常见姓氏。两者都不满足的（如“已阅读并理解……”）是正文。"""
    return _standalone(line, k, e) or _surname_start(line.text[k:e])


def _standalone(line: Line, k: int, e: int) -> bool:
    """[k, e) 是独立的一小段（2–4 个字，后面是行尾、标点、字段名或明显空白），而不是一句话的开头。
    离标签较远的取值只有这样才当作姓名：姓名常与标签隔开；“已阅读并理解上述内容”之类是正文。
    不用姓氏表判断：医生的姓不在常见姓氏表里时会漏掉整份文档的全文追踪。"""
    text = line.text
    if not 2 <= e - k <= 4:
        return False
    return e == len(text) or text[e] in PUNCT_PRE or _gap(line, e - 1, e) > 1.0 or bool(_match_at(text, e, STOP_WORDS, _STOP_MAX))


def _looks_printed(text: str, chars) -> bool:
    """成句的打印文字：至少 4 个字且识别置信度高（手写签名常被识别成一两个低置信度的字）。"""
    return len(text) >= 4 and sum(c.score for c in chars) / max(len(chars), 1) >= 0.9


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
    if kind in ("PERSON", "STAFF") and k < len(text) and text[k].isascii() and text[k].isalpha():
        # 英文姓名：连续的字母与单个空格
        while end < len(text) and end - k < 30 and (text[end].isascii() and text[end].isalpha() or text[end] == " " and end + 1 < len(text) and text[end + 1].isalpha()):
            end += 1
        return k, end
    if kind in ("PERSON", "STAFF") and any(c in "·•" for c in text[k : k + 9]):
        limit = 15  # 少数民族姓名较长
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
        if kind in ("PHONE", "ID_CARD", "MEDICAL_ID", "BANK_CARD", "PLATE") and not (ch.isalnum() or ch in "-－— _·•"):
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
            # 成句的打印文字也是填写区的边界：单独成行的，或同一行里隔开一大段空白后出现的（可搜索 PDF 的文字层会把整行连成一行）
            elif (i == 0 or _gap(ln, i - 1, i) > 1.5) and _looks_printed(t[i:], ln.chars[i:]):
                xs.append(ln.chars[i].box[0])
    return sorted(xs)


def anchor(page: PageData, enabled: set[str]) -> tuple[list[Hit], list[tuple[str, str, Rect]]]:
    """返回 (文字命中, 待验墨迹的填写区 [(类型, 标签, 右侧区域, 下方区域)])。"""
    hits: list[Hit] = []
    fields: list[tuple[str, str, Rect | None, Rect | None]] = []
    labels = find_labels(page)
    for lh in labels:
        kind = lh.type
        is_sig = "签" in lh.label
        if kind not in enabled and not (is_sig and "SIGNATURE" in enabled):
            continue
        line = page.lines[lh.line]
        lab_chars = line.chars[lh.start : lh.end]
        h = max(sorted(c.box[3] - c.box[1] for c in lab_chars)[len(lab_chars) // 2], 1)
        lab_x1 = lab_chars[-1].box[2]
        k, e = _take_value(line, lh.end, kind)
        value = line.text[k:e]
        if _starts_with_label(line.text, k):
            value = ""
        limit = None  # 填写区右边界：远处成句的打印文字
        dist = (line.chars[k].box[0] - lab_x1) / h if value else 0.0
        if value and kind in ("PERSON", "STAFF") and dist > FAR_VALUE and (dist > FAR_NAME_MAX or not _name_like(line, k, e)):
            if _looks_printed(line.text[k:], line.chars[k:]):
                limit = line.chars[k].box[0] - 0.3 * h
            value = ""
        # 标签后面紧跟“查房”“记录”等病历常用词：这是正文（如“上级医师查房，同意……”），不是表单字段
        if any(value.startswith(w) for w in NOT_NAME if len(w) >= 2):
            continue
        if _valid(kind, value) and kind in enabled:
            # 通用词（医师、患者、签名……）后的取值证据弱：首字须是常见姓氏才作全文追踪的种子；
            # 明确标签（姓名、主治医师、科主任……）后的照常作种子（手写姓名首字常被 OCR 认错，不能要求姓氏）
            seed = not lh.weak and (lh.label not in GENERIC or _surname_start(value))
            hits.append(Hit(kind, "anchor", page.index, lh.line, k, e, value, seed=seed))
            if is_sig:  # 签名之后到本行下一个字段的笔迹全部遮盖（收笔拖尾、认不出的字）；不用标签下方的备选区
                fields += [(f[0], f[1], f[2], None) for f in _field_rects(page, line, lh, kind, is_sig, h, limit) if f[2]]
            continue
        nb = _right_neighbor(page, line, lab_x1, lh.line) if limit is None else None
        if nb is not None:
            nl = page.lines[nb[0]]
            k2, e2 = _take_value(nl, 0, kind)
            v2 = "" if _starts_with_label(nl.text, k2) else nl.text[k2:e2]
            dist2 = (nl.box[0] - lab_x1) / h
            if dist2 > FAR_VALUE and kind in ("PERSON", "STAFF") and (dist2 > FAR_NAME_MAX or not _name_like(nl, k2, e2)):
                # 远处的邻行不像姓名：若是成句的打印文字，它就是填写区的右边界
                if _looks_printed(nl.text, nl.chars):
                    limit = nl.box[0] - 0.3 * h
                v2 = ""
            if _valid(kind, v2) and kind in enabled:
                # 取自标签旁边另一行的取值证据弱：首字须是常见姓氏才作种子（B 样例第 50 页的表单用语曾被追踪 65 次）
                hits.append(Hit(kind, "anchor", page.index, nb[0], k2, e2, v2, seed=not lh.weak and _surname_start(v2)))
                if is_sig:
                    fields += [(f[0], f[1], f[2], None) for f in _field_rects(page, line, lh, kind, is_sig, h, limit) if f[2]]
                continue
        fields += _field_rects(page, line, lh, kind, is_sig, h, limit)
    return hits, fields


def _field_rects(page: PageData, line: Line, lh: LabelHit, kind: str, is_sig: bool, h: float, limit: float | None):
    """推算填写区：标签右侧到同一行下一个标签、非敏感字段或成句打印文字之间；签名与人员栏另给标签正下方的备选区域。"""
    lab_chars = line.chars[lh.start : lh.end]
    lx0, lx1 = lab_chars[0].box[0], lab_chars[-1].box[2]
    ly0, ly1 = min(c.box[1] for c in lab_chars), max(c.box[3] for c in lab_chars)
    cy = (ly0 + ly1) / 2
    right = lx1 + (SIGN_WIDTH if is_sig else FIELD_WIDTH.get(kind, 8)) * h
    if limit is not None:
        right = min(right, limit)
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
    return [(ftype, lh.label, right_rect, below_rect)] if right_rect or below_rect else []
