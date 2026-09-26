"""常用表单模板：字段锚定的降级方案。

OCR 没认出某个字段名（污渍、印章、折痕压住）时，该字段的内容无从锚定。模板记录一张表单上各字段名的相对位置；
识别时若本页认出的字段名与某个模板大半对得上，就用这些认出的字段名拟合出缩放与平移，推算没认出的字段名在哪，
再把其右侧的内容按类型遮盖：已识别的印刷文字按该类型校验，手写部分交给墨迹检查。

生成模板（用本单位的空白或样例表单，只记录字段名与位置，不记录填写内容）：

    python -m redactx.detect.templates build 空白病案首页.pdf --name 住院病案首页 [--page 1]

模板存为 JSON，目录由 REDACTX_TEMPLATES 指定（默认仓库根目录下的 templates/）。
"""

from __future__ import annotations

import argparse
import json
import logging
from functools import lru_cache
from pathlib import Path

import numpy as np

from ..config import settings
from ..schemas import Hit, PageData, Rect
from .anchors import FIELD_WIDTH, SIGN_WIDTH, _valid, find_labels

log = logging.getLogger("redactx.templates")

MIN_MATCHED = 3  # 至少认出这么多个模板里的字段名
MIN_RATIO = 0.4  # 且占模板字段名的比例不低于此值
MAX_RESID = 0.8  # 拟合后认出的字段名与模板位置的中位偏差（以字高计）上限


@lru_cache(maxsize=1)
def _load_all(root: str) -> tuple[dict, ...]:
    out = []
    for p in sorted(Path(root).glob("*.json")):
        try:
            t = json.loads(p.read_text(encoding="utf-8"))
            if t.get("labels"):
                out.append(t)
        except (OSError, ValueError):
            log.warning("模板无法读取：%s", p.name)
    return tuple(out)


def templates() -> tuple[dict, ...]:
    return _load_all(str(settings.templates_dir))


def build(page: PageData, name: str) -> dict:
    """从一页表单生成模板：每个字段名的文字、类型与归一化位置。"""
    labels = []
    for lh in find_labels(page):
        cs = page.lines[lh.line].chars[lh.start : lh.end]
        box = (min(c.box[0] for c in cs), min(c.box[1] for c in cs), max(c.box[2] for c in cs), max(c.box[3] for c in cs))
        labels.append({"text": lh.label, "type": lh.type, "box": [round(box[0] / page.width, 5), round(box[1] / page.height, 5),
                                                                   round(box[2] / page.width, 5), round(box[3] / page.height, 5)]})
    return {"name": name, "labels": labels}


def _center(b) -> tuple[float, float]:
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def _fit(pairs: list[tuple[tuple[float, float], tuple[float, float]]]):
    """按轴分别拟合 x' = a·x + c、y' = b·y + d（扫描缩放与平移）。"""
    src = np.array([p[0] for p in pairs], np.float64)
    dst = np.array([p[1] for p in pairs], np.float64)
    coef = []
    for k in range(2):
        A = np.stack([src[:, k], np.ones(len(src))], 1)
        sol, *_ = np.linalg.lstsq(A, dst[:, k], rcond=None)
        coef.append(sol)
    pred = np.stack([src[:, 0] * coef[0][0] + coef[0][1], src[:, 1] * coef[1][0] + coef[1][1]], 1)
    resid = np.linalg.norm(pred - dst, axis=1)
    return coef, float(np.median(resid))


def apply(page: PageData, enabled: set[str], existing: list[Hit]) -> tuple[list[Hit], list[tuple[str, str, Rect, Rect | None]]]:
    """用匹配上的模板补出没认出字段名的字段。返回 (文字命中, 待验墨迹的填写区)。"""
    tpls = templates()
    if not tpls or not page.lines:
        return [], []
    found = find_labels(page)
    seen: dict[str, list[tuple[float, float, float]]] = {}
    for lh in found:
        cs = page.lines[lh.line].chars[lh.start : lh.end]
        b = (min(c.box[0] for c in cs), min(c.box[1] for c in cs), max(c.box[2] for c in cs), max(c.box[3] for c in cs))
        cx, cy = _center(b)
        seen.setdefault(lh.label, []).append((cx, cy, b[3] - b[1]))
    best = None
    for t in tpls:
        pairs, used = [], set()
        for k, lab in enumerate(t["labels"]):
            for cx, cy, h in seen.get(lab["text"], []):
                if (lab["text"], cx, cy) in used:
                    continue
                tx, ty = _center(lab["box"])
                pairs.append(((tx * page.width, ty * page.height), (cx, cy)))
                used.add((lab["text"], cx, cy))
                break
        if len(pairs) < max(MIN_MATCHED, MIN_RATIO * len(t["labels"])):
            continue
        coef, resid = _fit(pairs)
        hs = [h for v in seen.values() for _, _, h in v]
        if resid > MAX_RESID * (float(np.median(hs)) if hs else 20):
            continue
        if best is None or len(pairs) > best[2]:
            best = (t, coef, len(pairs))
    if best is None:
        return [], []
    t, coef, _ = best

    def tr(b):
        x0, y0, x1, y1 = b[0] * page.width, b[1] * page.height, b[2] * page.width, b[3] * page.height
        return (x0 * coef[0][0] + coef[0][1], y0 * coef[1][0] + coef[1][1], x1 * coef[0][0] + coef[0][1], y1 * coef[1][0] + coef[1][1])

    placed = [tr(lab["box"]) for lab in t["labels"]]
    got = {(lab, round(cx), round(cy)) for lab, v in seen.items() for cx, cy, _ in v}
    hits: list[Hit] = []
    fields: list[tuple[str, str, Rect, Rect | None]] = []
    for lab, box in zip(t["labels"], placed):
        kind = lab["type"]
        # 这个字段名已经认出来了（位置对得上）：照常由字段锚定处理
        if any(g[0] == lab["text"] and abs(g[1] - _center(box)[0]) < 2 * (box[3] - box[1]) and abs(g[2] - _center(box)[1]) < box[3] - box[1] for g in got):
            continue
        is_sig = "签" in lab["text"]
        if kind not in enabled and not (is_sig and "SIGNATURE" in enabled):
            continue
        h = max(box[3] - box[1], 4)
        right = box[2] + (SIGN_WIDTH if is_sig else FIELD_WIDTH.get(kind, 8)) * h
        # 到同一行的下一个模板字段名为止
        stop = float("inf")
        for ob in placed:
            if ob is not box and ob[0] > box[2] and abs(_center(ob)[1] - _center(box)[1]) < 0.6 * h:
                stop = min(stop, ob[0] - 0.25 * h)
        right = min(right, stop)
        rect = (box[2] + 0.15 * h, box[1] - 0.3 * h, right, box[3] + 0.3 * h)
        if rect[2] - rect[0] < 1.2 * h:
            continue
        # 已识别的印刷内容：按类型校验后作为文字命中
        for li, ln in enumerate(page.lines):
            ks = [k for k, c in enumerate(ln.chars) if rect[0] <= (c.box[0] + c.box[2]) / 2 <= rect[2] and rect[1] <= (c.box[1] + c.box[3]) / 2 <= rect[3]]
            if not ks:
                continue
            a, b = ks[0], ks[-1] + 1
            # 取值可能比推算的填写区长（18 位证件号）：沿本行连续的字向右延伸，到下一个字段名为止
            while b < len(ln.chars) and ln.chars[b].box[0] - ln.chars[b - 1].box[2] <= 1.0 * h and (ln.chars[b].box[0] + ln.chars[b].box[2]) / 2 < stop:
                b += 1
            value = ln.text[a:b].strip("：: ")
            if kind in enabled and _valid(kind, value) and not any(o.line == li and o.start < b and a < o.end for o in existing):
                hits.append(Hit(kind, "template", page.index, li, a, b, value, seed=False))
        fields.append(("SIGNATURE" if is_sig else "HANDWRITTEN_FIELD", lab["text"], rect, None))
    return hits, fields


def main() -> None:
    ap = argparse.ArgumentParser(description="从空白或样例表单生成模板（只记录字段名与位置）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("file", type=Path)
    b.add_argument("--name", required=True)
    b.add_argument("--page", type=int, default=1)
    b.add_argument("--out", type=Path, default=None, help="默认写入模板目录")
    a = ap.parse_args()
    from .. import ocr, pipeline
    from ..ingest import iter_pages, open_doc

    doc = open_doc(a.file, None, settings.max_pages)
    for img, pd in iter_pages(a.file, doc.kind, 200, None):
        if pd.index != a.page - 1:
            continue
        if pd.text_source != "text":
            pd.rotation = ocr.detect_orientation(img)
        work = ocr._rotate(img, pd.rotation).copy()
        pipeline._page_text(work, pd, set())
        t = build(pd, a.name)
        out = a.out or Path(settings.templates_dir) / f"{a.name}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已写入 {out}：{len(t['labels'])} 个字段名")
        return
    raise SystemExit(f"没有第 {a.page} 页")


if __name__ == "__main__":
    main()
