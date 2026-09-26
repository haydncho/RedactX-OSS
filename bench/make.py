"""生成合成病案评估集。

    python -m bench.make --out bench/out --cases 3

每份病案 4 页（病案首页、检验报告单、病程记录、内镜报告），扫描类形态另加第 5 页手写日常病程
（手写日期与诊断、各种写法的医师签名）与第 6 页手写知情同意书（标签与手写内容隔得较远、句末“签名”）。按以下形态各出一份：
  text     系统导出：全部带文字层，签名为电子签名小图，Logo 跨页重复
  textwm   系统导出，加文字层院名水印（旋转的浅灰半透明文字对象，平铺）
  scan     白纸扫描 200 DPI：手写姓名与签名、红章压字、轻微歪斜
  kraft    牛皮纸扫描 300 DPI
  rotated  横放扫描：页面横向，内容旋转 90°
  mixed    混合：文字层页与扫描页交替
  hard     白纸扫描加难点：淡粉色低饱和印章压院名、平铺斜向院名水印（压在正文上）、大号连笔签名
  mrc      300 DPI 扫描按复印机 MRC 分层压缩：1 位文字蒙版 + 150 DPI 彩色底图
  scanocr  可搜索 PDF：扫描图像加不可见的打印文字层（手写不在文字层里）
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

# 名称 -> 每页的渲染方式：("text", 水印) 或 ("scan", dpi, 纸张, 是否横放, 难点)。
# 第 5、6 页（手写病程、手写知情同意书）只有扫描类形态才有：模式列表比页面少时，多出的页不生成。
_ALL_HARD = frozenset({"faint_seal", "watermark", "big_sig"})
_T, _TW = ("text", False), ("text", True)
_S200 = ("scan", 200, "white", False, frozenset())
VARIANTS = {
    "text": [_T] * 4,
    "textwm": [_TW] * 4,
    "scan": [_S200] * 6,
    "kraft": [("scan", 300, "kraft", False, frozenset())] * 6,
    "rotated": [("scan", 200, "white", True, frozenset())] * 6,
    "mixed": [_T, _S200, _T, _S200],
    "hard": [("scan", 200, "white", False, _ALL_HARD)] * 6,
    "mrc": [("scan", 300, "white", False, frozenset())] * 6,
    "scanocr": [("scanocr", 200, "white", False, frozenset())] * 6,
}
# 这些形态按复印机 MRC 分层压缩写出
MRC_VARIANTS = {"mrc"}


def build(case_seed: int, variant: str, out_dir: Path) -> Path:
    case = make_case(case_seed)
    pages = case_pages(case)
    chars = render.page_chars(pages)
    rng = random.Random(case_seed * 7919 + len(variant))
    w = render.Writer(chars)
    truth, scans = [], []
    for i, (p, mode) in enumerate(zip(pages, VARIANTS[variant])):
        if mode[0] == "text":
            items = w.text_page(p, rng, watermark=mode[1])
        else:
            kind, dpi, paper, rot, hard = mode
            ocr = kind == "scanocr"  # 可搜索 PDF：扫描图像加不可见的打印文字层，图文对齐，不歪斜
            im, items, (w_pt, h_pt) = render.scan_page(p, chars, dpi, paper, rng, rotate=rot, hard=hard, skew=not ocr)
            if variant in MRC_VARIANTS:
                scans.append((im, w_pt, h_pt))
            else:
                w.image_page(im, w_pt, h_pt, ocr_layer=p if ocr else None)
        truth += [t.json(i + 1) for t in items]
    name = f"{variant}-{case_seed:03d}"
    (out_dir / "docs").mkdir(parents=True, exist_ok=True)
    (out_dir / "truth").mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / "docs" / f"{name}.pdf"
    if variant in MRC_VARIANTS:
        render.write_mrc_pdf(scans, pdf_path)
    else:
        w.save(pdf_path)
    meta = {"name": name, "variant": variant, "case_seed": case_seed, "pages": len(VARIANTS[variant]), "items": truth}
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
