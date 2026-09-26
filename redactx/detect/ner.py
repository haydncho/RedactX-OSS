"""正文人名识别（NER）：只在叙述里出现、字段锚定与全文追踪都覆盖不到的人名。

模型为 shibing624/bert4ner-base-chinese（Apache-2.0，人民日报与 CNER 语料训练），导出为 ONNX 并做 int8 量化，
运行时只用 onnxruntime 与 tokenizers，不联网。模型目录由 REDACTX_NER_DIR 指定（默认仓库根目录下的 models/ner），
内含 model.onnx、vocab.txt 与 config.json；目录不存在时本模块不产生任何命中。
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import numpy as np

from ..config import settings
from ..schemas import Hit, PageData
from .anchors import NOT_NAME
from .lexicon import COMPOUND_SURNAMES, STOP_WORDS, SURNAMES

log = logging.getLogger("redactx.ner")

WINDOW = 200  # 每段送入模型的字数；相邻两段重叠 OVERLAP 字，重叠处取靠近段中间的那一段的结果
OVERLAP = 40
MIN_SCORE = 0.85  # 人名各字概率的均值下限
SEED_SCORE = 0.97  # 达到此值的人名作为全文追踪的种子
PROSE_MIN = 12  # 送入模型的句段至少这么多字，且含下列标点之一
PROSE_MARKS = "，。；、（"
# 人名后紧跟称谓或常用动词时，模型有时把它的首字并进人名（“白主任”认成“白主”）
AFTER_NAME = ("主任", "代为", "护士", "医师", "医生", "大夫", "教授", "表示", "要求")
STAFF_CUES = ("医师", "医生", "主任", "护士", "护师", "教授", "大夫", "术者", "助手", "麻醉", "查房", "会诊")

_lock = threading.Lock()
_model = None  # (session, tokenizer, labels)；None 表示未加载，False 表示没有模型


def _load():
    global _model
    with _lock:
        if _model is None:
            d = Path(settings.ner_dir)
            if not (d / "model.onnx").exists():
                log.info("未找到 NER 模型（%s），正文人名识别关闭", d)
                _model = False
            else:
                import onnxruntime as ort
                from tokenizers import BertWordPieceTokenizer

                opts = ort.SessionOptions()
                opts.intra_op_num_threads = settings.ner_threads
                sess = ort.InferenceSession(str(d / "model.onnx"), opts, providers=["CPUExecutionProvider"])
                tok = BertWordPieceTokenizer(str(d / "vocab.txt"), lowercase=True)
                cfg = json.loads((d / "config.json").read_text(encoding="utf-8"))
                labels = [cfg["id2label"][str(i)] for i in range(len(cfg["id2label"]))]
                _model = (sess, tok, labels)
        return _model or None


def available() -> bool:
    return _load() is not None


def _page_text(page: PageData) -> tuple[str, list[tuple[int, int] | None]]:
    """取出本页的叙述性文字拼成一段：折行的句子直接相连（人名可能被拆到两行），不同句段之间插入句号。
    表格、字段行等短句段不送入模型（字段里的人名由字段锚定负责），以节省时间。
    返回文字与每个字对应的 (行, 字序号)，插入的分隔符对应 None。"""
    lines = [(li, ln) for li, ln in enumerate(page.lines) if ln.chars]
    widths = sorted(ln.box[2] - ln.box[0] for _, ln in lines)
    full = widths[int(len(widths) * 0.8)] if widths else 0
    segs, cur, prev = [], [], None
    for li, ln in lines:
        wrapped = prev is not None and prev.box[2] - prev.box[0] >= 0.85 * full and prev.text[-1] not in "。；！？"
        if cur and not wrapped:
            segs.append(cur)
            cur = []
        cur += [(c.ch, (li, k)) for k, c in enumerate(ln.chars)]
        prev = ln
    if cur:
        segs.append(cur)
    text, where = [], []
    for seg in segs:
        s = "".join(ch for ch, _ in seg)
        if len(s) < PROSE_MIN or not any(p in s for p in PROSE_MARKS):
            continue
        if text:
            text.append("。")
            where.append(None)
        for ch, w in seg:
            text.append(ch)
            where.append(w)
    return "".join(text), where


def _surname_start(v: str) -> bool:
    return v[:2] in COMPOUND_SURNAMES or v[0] in SURNAMES


def _is_name(v: str) -> bool:
    core = v.replace("·", "")
    if not 2 <= len(core) <= 4 or not all("一" <= ch <= "龥" or ch == "·" for ch in v):
        return False
    if not _surname_start(v) or v in STOP_WORDS or any(v.startswith(w) for w in NOT_NAME if len(w) >= 2):
        return False
    return True


def find_names(text: str) -> list[tuple[int, int, float]]:
    """在一段文字里找人名，返回 (起, 止, 分数)。"""
    m = _load()
    if m is None or not text:
        return []
    sess, tok, labels = m
    b_per, i_per = labels.index("B-PER"), labels.index("I-PER")
    probs = np.zeros((len(text), len(labels)), np.float32)
    weight = np.zeros(len(text), np.float32)
    starts = list(range(0, max(len(text) - OVERLAP, 1), WINDOW - OVERLAP))
    encs = [tok.encode(text[a : a + WINDOW]) for a in starts]
    n = max(len(e.ids) for e in encs)
    ids = np.zeros((len(encs), n), np.int64)
    mask = np.zeros_like(ids)
    for r, e in enumerate(encs):
        ids[r, : len(e.ids)] = e.ids
        mask[r, : len(e.ids)] = 1
    logits = sess.run(None, {"input_ids": ids, "attention_mask": mask, "token_type_ids": np.zeros_like(ids)})[0]
    ex = np.exp(logits - logits.max(-1, keepdims=True))
    p = ex / ex.sum(-1, keepdims=True)
    for r, (a, e) in enumerate(zip(starts, encs)):
        seg = min(WINDOW, len(text) - a)
        for t, (o0, o1) in enumerate(e.offsets):
            if o1 <= o0:
                continue  # [CLS]、[SEP]
            # 越靠近本段中间越可信
            w = 1.0 + min(o0, seg - o0)
            for k in range(a + o0, a + o1):
                probs[k] += w * p[r, t]
                weight[k] += w
    probs /= np.maximum(weight, 1e-6)[:, None]
    out, k = [], 0
    per = probs[:, b_per] + probs[:, i_per]
    while k < len(text):
        if probs[k].argmax() in (b_per, i_per):
            j = k + 1
            while j < len(text) and probs[j].argmax() == i_per:
                j += 1
            out.append((k, j, float(per[k:j].mean())))
            k = j
        else:
            k += 1
    return out


def page_names(page: PageData, enabled: set[str] | None = None) -> list[Hit]:
    """本页正文里的人名。与医护称谓相邻的记为医护人员姓名，其余记为患者与联系人姓名。"""
    enabled = enabled if enabled is not None else {"PERSON", "STAFF"}
    if not ({"PERSON", "STAFF"} & enabled) or not available():
        return []
    text, where = _page_text(page)
    hits = []
    for a, b, s in find_names(text):
        while b - a > 1 and any(text[b - 1 :].startswith(w) for w in AFTER_NAME):
            b -= 1
        v = text[a:b]
        if s < MIN_SCORE or not _is_name(v):
            continue
        ctx = text[max(0, a - 4) : a] + "|" + text[b : b + 4]
        typ = "STAFF" if any(c in ctx for c in STAFF_CUES) else "PERSON"
        if typ not in enabled:
            typ = "PERSON" if "PERSON" in enabled else "STAFF"
        # 跨行的人名拆成每行一个命中
        spots = [where[k] for k in range(a, b) if where[k] is not None]
        for li in dict.fromkeys(li for li, _ in spots):
            ks = [k for l2, k in spots if l2 == li]
            hits.append(Hit(typ, "ner", page.index, li, min(ks), max(ks) + 1, v, seed=s >= SEED_SCORE))
    return hits
