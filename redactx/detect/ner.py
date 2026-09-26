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
BATCH = 32  # 一次送入模型的窗口数
MIN_SCORE = 0.85  # 人名各字概率的均值下限
SEED_SCORE = 0.97  # 达到此值的人名作为全文追踪的种子
PROSE_MIN = 12  # 送入模型的句段至少这么多字，且含下列标点之一
PROSE_MARKS = "，。；、（"
# 人名后紧跟称谓或常用动词时，模型有时把它的首字并进人名（“白主任”认成“白主”）
AFTER_NAME = ("主任", "代为", "护士", "医师", "医生", "大夫", "教授", "表示", "要求")
# 人名后面紧跟药名后半截时，其实是药名的前半截（“林可霉素”认成“林可”）
DRUG_TAILS = ("霉素", "沙星", "西林", "他汀", "洛尔", "地平", "普利", "沙坦", "替丁", "拉唑", "咪唑", "硝唑", "韦林", "卡因", "松片", "片", "胶囊", "注射液", "颗粒", "口服液")
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


def _page_segments(page: PageData) -> list[tuple[str, list[tuple[int, int]]]]:
    """本页的叙述性句段：折行的句子拼回整句（人名可能被拆到两行）。表格、字段行等短句段跳过
    （字段里的人名由字段锚定负责），以节省时间。返回 [(文字, 每个字对应的 (行, 字序号))]。"""
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
    out = []
    for seg in segs:
        text = "".join(ch for ch, _ in seg)
        if len(text) >= PROSE_MIN and any(p in text for p in PROSE_MARKS):
            out.append((text, [w for _, w in seg]))
    return out


def _page_text(page: PageData) -> tuple[str, list[tuple[int, int] | None]]:
    """各叙述句段以句号相连成一段（只用于检查），分隔符对应 None。"""
    text, where = "", []
    for t, w in _page_segments(page):
        if text:
            text += "。"
            where.append(None)
        text += t
        where += w
    return text, where


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
    return find_names_batch([text])[0]


def find_names_batch(texts: list[str]) -> list[list[tuple[int, int, float]]]:
    """多段文字一起送入模型（各段互不影响：前一句 OCR 错得厉害时不会拖累后一句），分别返回人名。"""
    m = _load()
    if m is None or not any(texts):
        return [[] for _ in texts]
    sess, tok, labels = m
    b_per, i_per = labels.index("B-PER"), labels.index("I-PER")
    jobs = [(ti, a) for ti, t in enumerate(texts) if t for a in range(0, max(len(t) - OVERLAP, 1), WINDOW - OVERLAP)]
    encs = [tok.encode(texts[ti][a : a + WINDOW]) for ti, a in jobs]
    probs = [np.zeros((len(t), len(labels)), np.float32) for t in texts]
    weight = [np.zeros(len(t), np.float32) for t in texts]
    for b0 in range(0, len(encs), BATCH):
        chunk = encs[b0 : b0 + BATCH]
        n = max(len(e.ids) for e in chunk)
        ids = np.zeros((len(chunk), n), np.int64)
        mask = np.zeros_like(ids)
        for r, e in enumerate(chunk):
            ids[r, : len(e.ids)] = e.ids
            mask[r, : len(e.ids)] = 1
        logits = sess.run(None, {"input_ids": ids, "attention_mask": mask, "token_type_ids": np.zeros_like(ids)})[0]
        ex = np.exp(logits - logits.max(-1, keepdims=True))
        p = ex / ex.sum(-1, keepdims=True)
        for r, e in enumerate(chunk):
            ti, a = jobs[b0 + r]
            seg = min(WINDOW, len(texts[ti]) - a)
            for t, (o0, o1) in enumerate(e.offsets):
                if o1 <= o0:
                    continue  # [CLS]、[SEP]
                w = 1.0 + min(o0, seg - o0)  # 越靠近窗口中间越可信
                for k in range(a + o0, a + o1):
                    probs[ti][k] += w * p[r, t]
                    weight[ti][k] += w
    out = []
    for text, pr, wt in zip(texts, probs, weight):
        pr = pr / np.maximum(wt, 1e-6)[:, None]
        per = pr[:, b_per] + pr[:, i_per]
        found, k = [], 0
        while k < len(text):
            if pr[k].argmax() in (b_per, i_per):
                j = k + 1
                while j < len(text) and pr[j].argmax() == i_per:
                    j += 1
                found.append((k, j, float(per[k:j].mean())))
                k = j
            else:
                k += 1
        out.append(found)
    return out


def page_names(page: PageData, enabled: set[str] | None = None) -> list[Hit]:
    """本页正文里的人名。与医护称谓相邻的记为医护人员姓名，其余记为患者与联系人姓名。"""
    enabled = enabled if enabled is not None else {"PERSON", "STAFF"}
    if not ({"PERSON", "STAFF"} & enabled) or not available():
        return []
    segs = _page_segments(page)
    hits = []
    for (text, where), found in zip(segs, find_names_batch([t for t, _ in segs])):
        hits += _to_hits(page, text, where, found, enabled)
    return hits


def _to_hits(page: PageData, text: str, where: list[tuple[int, int]], found, enabled: set[str]) -> list[Hit]:
    hits = []
    for a, b, s in found:
        while b - a > 1 and any(text[b - 1 :].startswith(w) for w in AFTER_NAME):
            b -= 1
        v = text[a:b]
        if s < MIN_SCORE or not _is_name(v) or any(text[b:].startswith(t) for t in DRUG_TAILS):
            continue
        ctx = text[max(0, a - 4) : a] + "|" + text[b : b + 4]
        typ = "STAFF" if any(c in ctx for c in STAFF_CUES) else "PERSON"
        if typ not in enabled:
            typ = "PERSON" if "PERSON" in enabled else "STAFF"
        # 跨行的人名拆成每行一个命中
        spots = [where[k] for k in range(a, b)]
        for li in dict.fromkeys(li for li, _ in spots):
            ks = [k for l2, k in spots if l2 == li]
            hits.append(Hit(typ, "ner", page.index, li, min(ks), max(ks) + 1, v, seed=s >= SEED_SCORE))
    return hits
