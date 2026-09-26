"""生成合成病案评估集。

    python -m bench.make --out bench/out --cases 3

每份病案 4 页（病案首页、检验报告单、病程记录、内镜报告），按以下形态各出一份：
  text     系统导出：全部带文字层，签名为电子签名小图，Logo 跨页重复
  scan     白纸扫描 200 DPI：手写姓名与签名、红章压字、轻微歪斜
  kraft    牛皮纸扫描 300 DPI
  rotated  横放扫描：页面横向，内容旋转 90°
  mixed    混合：文字层页与扫描页交替
输出 docs/<名称>.pdf 与 truth/<名称>.json（标准答案，坐标为归一化页面坐标）。
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from . import render
from .fakes import make_case
from .forms import case_pages

# 名称 -> 每页的渲染方式：("text",) 或 ("scan", dpi, 纸张, 是否横放)
VARIANTS = {
    "text": [("text",)] * 4,
    "scan": [("scan", 200, "white", False)] * 4,
    "kraft": [("scan", 300, "kraft", False)] * 4,
    "rotated": [("scan", 200, "white", True)] * 4,
    "mixed": [("text",), ("scan", 200, "white", False), ("text",), ("scan", 200, "white", False)],
}


def build(case_seed: int, variant: str, out_dir: Path) -> Path:
    case = make_case(case_seed)
    pages = case_pages(case)
    chars = render.page_chars(pages)
    rng = random.Random(case_seed * 7919 + len(variant))
    w = render.Writer(chars)
    truth = []
    for i, (p, mode) in enumerate(zip(pages, VARIANTS[variant])):
        if mode[0] == "text":
            items = w.text_page(p, rng)
        else:
            _, dpi, paper, rot = mode
            im, items, (w_pt, h_pt) = render.scan_page(p, chars, dpi, paper, rng, rotate=rot)
            w.image_page(im, w_pt, h_pt)
        truth += [t.json(i + 1) for t in items]
    name = f"{variant}-{case_seed:03d}"
    (out_dir / "docs").mkdir(parents=True, exist_ok=True)
    (out_dir / "truth").mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / "docs" / f"{name}.pdf"
    w.save(pdf_path)
    meta = {"name": name, "variant": variant, "case_seed": case_seed, "pages": len(pages), "items": truth}
    (out_dir / "truth" / f"{name}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return pdf_path


def main() -> None:
    ap = argparse.ArgumentParser(description="生成合成病案评估集（全部为虚构数据）")
    ap.add_argument("--out", default="bench/out", type=Path)
    ap.add_argument("--cases", default=3, type=int, help="每种形态生成几份病案")
    ap.add_argument("--seed", default=1, type=int)
    ap.add_argument("--variants", default=",".join(VARIANTS), help="逗号分隔：" + ",".join(VARIANTS))
    args = ap.parse_args()
    for v in args.variants.split(","):
        if v not in VARIANTS:
            raise SystemExit(f"未知形态：{v}")
        for k in range(args.cases):
            path = build(args.seed + k, v, args.out)
            print(f"{path}  {path.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
