"""把标准答案（以及可选的脱敏报告）画到页面上，便于人工检查。

    python -m bench.show bench/out/docs/scan-001.pdf --page 1 --out page1.png [--report report.json]

绿框：必须保留；红框：必须遮盖；蓝框：脱敏报告里的遮盖区域。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pypdfium2 as pdfium
from PIL import ImageDraw


def draw(pdf: Path, truth: dict, page: int, report: dict | None, scale: float = 1.5):
    doc = pdfium.PdfDocument(str(pdf))
    im = doc[page - 1].render(scale=scale).to_pil().convert("RGB")
    W, H = im.size
    d = ImageDraw.Draw(im)
    for it in truth["items"]:
        if it["page"] != page:
            continue
        x0, y0, x1, y1 = it["box"]
        d.rectangle((x0 * W, y0 * H, x1 * W, y1 * H), outline=(220, 30, 30) if it["role"] == "redact" else (20, 160, 60), width=2)
    for it in (report or {}).get("items", []):
        if it["page"] != page:
            continue
        x0, y0, x1, y1 = it["box"]
        d.rectangle((x0 * W, y0 * H, x1 * W, y1 * H), outline=(30, 90, 230), width=1)
    return im


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--truth", type=Path, help="默认取 ../truth/<同名>.json")
    ap.add_argument("--report", type=Path)
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    truth_path = a.truth or a.pdf.parent.parent / "truth" / (a.pdf.stem + ".json")
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    report = json.loads(a.report.read_text(encoding="utf-8")) if a.report else None
    draw(a.pdf, truth, a.page, report).save(a.out)


if __name__ == "__main__":
    main()
