"""合成评估集转 COCO：训练签名、印章检测模型用。格式与复核台“导出标注”相同（类别按实体类型名对应）。

    python -m bench.to_coco --data bench/train --out bench/train/coco [--dpi 200]

页面按流水线同样的方式渲染（200 DPI），标准答案的归一化框换算成像素。只收扫描类页面（文字层页面里的签名是电子签名小图，另由图片对象处理）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from redactx.catalog import ENTITY_BY_CODE
from redactx.ingest import iter_pages, open_doc


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", type=Path, default=Path("bench/train"))
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--dpi", type=int, default=200)
    a = ap.parse_args()
    out = a.out or a.data / "coco"
    (out / "images").mkdir(parents=True, exist_ok=True)
    cat_id = {c: k + 1 for k, c in enumerate(sorted(ENTITY_BY_CODE))}
    images, anns = [], []
    for pdf in sorted((a.data / "docs").glob("*.pdf")):
        truth = json.loads((a.data / "truth" / f"{pdf.stem}.json").read_text(encoding="utf-8"))
        doc = open_doc(pdf, None, 500)
        for img, pd in iter_pages(pdf, doc.kind, a.dpi, None):
            if pd.text_source == "text":
                continue
            p = pd.index + 1
            items = [it for it in truth["items"] if it["page"] == p and it["role"] == "redact" and it["type"] in ("SIGNATURE", "SEAL")]
            h, w = img.shape[:2]
            name = f"images/{pdf.stem}-p{p}.jpg"
            from PIL import Image

            Image.fromarray(img).save(out / name, "JPEG", quality=90)
            iid = len(images) + 1
            images.append({"id": iid, "file_name": name, "width": w, "height": h})
            for it in items:
                x0, y0, x1, y1 = it["box"]
                bw, bh = (x1 - x0) * w, (y1 - y0) * h
                anns.append({"id": len(anns) + 1, "image_id": iid, "category_id": cat_id[it["type"]], "bbox": [round(x0 * w, 1), round(y0 * h, 1), round(bw, 1), round(bh, 1)],
                             "area": round(bw * bh, 1), "iscrowd": 0})
    coco = {"images": images, "annotations": anns, "categories": [{"id": cat_id[c], "name": c, "zh": ENTITY_BY_CODE[c]["name"]} for c in sorted(ENTITY_BY_CODE)]}
    (out / "annotations.json").write_text(json.dumps(coco, ensure_ascii=False), encoding="utf-8")
    print(f"{out}：{len(images)} 页，{len(anns)} 个框")


if __name__ == "__main__":
    main()
