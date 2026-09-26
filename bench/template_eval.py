"""表单模板降级方案的评估：模拟 OCR 认不出一部分字段名（污渍、印章、折痕），比较有无模板时字段内容的遮全率。

    python -m bench.template_eval [--data bench/out] [--drop 0.3] [--trials 20]

模板现场用文字层病案首页（text-001 第 1 页）生成到临时目录；在扫描件病案首页（scan-00x 第 1 页）上 OCR 一次，
每轮随机把一部分字段名的字换成认不出的符号（内容保留），统计印刷的应遮内容被命中覆盖的比例。只用合成数据。
"""

from __future__ import annotations

import argparse
import json
import random
import tempfile
from copy import deepcopy
from pathlib import Path

from redactx import ocr, pipeline
from redactx.config import settings
from redactx.detect import anchors, engine, templates
from redactx.ingest import iter_pages
from redactx.schemas import PageData

ENABLED = {"PERSON", "STAFF", "ID_CARD", "PHONE", "ADDRESS", "MEDICAL_ID", "ORG"}


def _page(pdf: Path) -> PageData:
    for img, pd in iter_pages(pdf, "pdf", 200, None):
        if pd.text_source != "text":
            pd.rotation = ocr.detect_orientation(img)
        work = ocr._rotate(img, pd.rotation).copy()
        pipeline._page_text(work, pd, set())
        return pd
    raise SystemExit(f"{pdf} 没有页面")


def _covered(pd: PageData, hits, truth_box) -> bool:
    x0, y0, x1, y1 = truth_box[0] * pd.width, truth_box[1] * pd.height, truth_box[2] * pd.width, truth_box[3] * pd.height
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    for h in hits:
        r = pipeline._hits_to_rect(pd, h, True)
        if r and r[0] <= cx <= r[2] and r[1] <= cy <= r[3]:
            return True
    return False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", type=Path, default=Path("bench/out"))
    ap.add_argument("--drop", type=float, default=0.3, help="认不出的字段名比例")
    ap.add_argument("--trials", type=int, default=20)
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="tpl-"))
    settings.templates_dir = tmp
    t = templates.build(_page(a.data / "docs" / "text-001.pdf"), "住院病案首页")
    (tmp / "front.json").write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")
    templates._load_all.cache_clear()
    rng = random.Random(7)
    stats = {"off": [0, 0], "on": [0, 0]}
    for pdf in sorted((a.data / "docs").glob("scan-*.pdf")):
        truth = json.loads((a.data / "truth" / f"{pdf.stem}.json").read_text(encoding="utf-8"))
        items = [it for it in truth["items"] if it["page"] == 1 and it["role"] == "redact" and it["form"] == "print" and it["type"] in ENABLED]
        base = _page(pdf)
        for _ in range(a.trials):
            pd = deepcopy(base)
            for lh in anchors.find_labels(pd):
                if rng.random() < a.drop:
                    for c in pd.lines[lh.line].chars[lh.start : lh.end]:
                        c.ch = "□"
            for mode in ("off", "on"):
                settings.templates_dir = tmp if mode == "on" else tmp / "none"
                templates._load_all.cache_clear()
                hits, _ = engine.page_hits(pd, ENABLED, [])
                stats[mode][0] += sum(_covered(pd, hits, it["box"]) for it in items)
                stats[mode][1] += len(items)
    for mode, label in (("off", "无模板"), ("on", "有模板")):
        c, n = stats[mode]
        print(f"{label}：印刷字段内容 {n} 处，遮全 {c / max(n, 1):.1%}")


if __name__ == "__main__":
    main()
