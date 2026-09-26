"""评估打分：把脱敏报告里的遮盖区域与标准答案逐页比对。

- 应遮项：被遮盖区域覆盖的面积比例 ≥ FULL 记为“遮全”，≥ PARTIAL 记为“部分”，否则“漏遮”。
- 应留项：被覆盖比例 > OVER 记为“误遮”（日期、诊断、检验结果等被遮掉）。
- 多余遮盖：报告里的区域与任何应遮项（略微放宽后）重叠不足 EXTRA，计为“多遮”。
覆盖率在每页的栅格上计算，多个遮盖框叠加时按并集算。
水印消除（报告里 source 为 watermark）只擦除水印像素、不遮下面的内容：它只用于判断水印是否被消除，不计入误遮与多遮。
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np

FULL = 0.8
PARTIAL = 0.3
OVER = 0.15
EXTRA = 0.2
MARGIN = 0.006  # 判断“多遮”时应遮项向外放宽的距离（页面比例），容纳遮盖框的正常外扩
GRID = 1000  # 页面长边的栅格数


def _grid(aspect: float) -> tuple[int, int]:
    """aspect = 宽/高，返回 (宽, 高) 栅格数。"""
    return (GRID, max(1, round(GRID / aspect))) if aspect >= 1 else (max(1, round(GRID * aspect)), GRID)


def _cells(box, gw: int, gh: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    return (int(np.floor(max(x0, 0) * gw)), int(np.floor(max(y0, 0) * gh)), int(np.ceil(min(x1, 1) * gw)), int(np.ceil(min(y1, 1) * gh)))


def _mask(boxes, gw: int, gh: int) -> np.ndarray:
    m = np.zeros((gh, gw), bool)
    for b in boxes:
        a, c, e, f = _cells(b, gw, gh)
        m[c:f, a:e] = True
    return m


def _grow(box, m: float):
    x0, y0, x1, y1 = box
    return (x0 - m, y0 - m, x1 + m, y1 + m)


def coverage(box, mask: np.ndarray) -> float:
    gh, gw = mask.shape
    a, c, e, f = _cells(box, gw, gh)
    if e <= a or f <= c:
        return 0.0
    return float(mask[c:f, a:e].mean())


def score_doc(truth: dict, report: dict, aspects: dict[int, float] | None = None) -> dict:
    """truth：make 生成的标准答案；report：pipeline.run 的返回值。aspects：页码 -> 宽/高。"""
    aspects = aspects or {}
    by_page_pred: dict[int, list] = defaultdict(list)
    for it in report.get("items", []):
        by_page_pred[it["page"]].append(it)
    by_page_truth: dict[int, list] = defaultdict(list)
    for it in truth["items"]:
        by_page_truth[it["page"]].append(it)

    results, extras = [], []
    for page in sorted(set(by_page_truth) | set(by_page_pred)):
        gw, gh = _grid(aspects.get(page, 1 / 1.414))
        pred = by_page_pred.get(page, [])
        pmask = _mask([p["box"] for p in pred], gw, gh)
        cover = _mask([p["box"] for p in pred if p.get("source") != "watermark"], gw, gh)  # 真正盖住内容的区域
        tmask = _mask([_grow(t["box"], MARGIN) for t in by_page_truth.get(page, []) if t["role"] == "redact"], gw, gh)
        for t in by_page_truth.get(page, []):
            cov = coverage(t["box"], pmask if t["role"] == "redact" else cover)
            if t["role"] == "redact":
                status = "full" if cov >= FULL else ("partial" if cov >= PARTIAL else "miss")
            else:
                status = "over" if cov > OVER else "kept"
            results.append({**t, "coverage": round(cov, 3), "status": status})
        for p in pred:
            if p.get("source") != "watermark" and coverage(p["box"], tmask) < EXTRA:
                extras.append({"page": page, "type": p["type"], "source": p.get("source"), "box": p["box"]})
    return {"items": results, "extras": extras}


def summarize(scored: list[dict], key=lambda it: it["type"]) -> dict:
    """按 key 汇总：应遮项的遮全率、漏遮数；应留项的误遮数。"""
    agg: dict[str, dict] = defaultdict(lambda: {"redact": 0, "full": 0, "partial": 0, "miss": 0, "keep": 0, "over": 0})
    for it in scored:
        a = agg[key(it)]
        if it["role"] == "redact":
            a["redact"] += 1
            a[it["status"]] += 1
        else:
            a["keep"] += 1
            a["over"] += it["status"] == "over"
    for a in agg.values():
        a["recall"] = round(a["full"] / a["redact"], 3) if a["redact"] else None
    return dict(agg)
