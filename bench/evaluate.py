"""在合成评估集上跑脱敏流水线并打分。

    python -m bench.evaluate --data bench/out [--verify] [--keep-output]

输出：终端汇总表，以及 <data>/results/summary.md 与 results.json（含每一处漏遮与误遮）。
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import time
from pathlib import Path

import pypdfium2 as pdfium

from . import metrics

NAMES = {
    "PERSON": "患者与联系人姓名", "PERSON_PROSE": "正文人名（仅正文出现）", "STAFF": "医护人员姓名", "ORG": "机构名称",
    "ADDRESS": "地址", "ID_CARD": "身份证号", "PHONE": "电话", "MEDICAL_ID": "医疗标识号", "SIGNATURE": "签名",
    "SEAL": "印章", "LOGO": "Logo", "QRCODE": "二维码", "DATE": "日期", "DIAGNOSIS": "诊断与编码", "LAB_RESULT": "检验结果",
    "SEX": "性别", "AGE": "年龄", "FEE": "费用", "PHOTO": "临床照片",
}
FORMS = {"print": "印刷", "hand": "手写", "image": "图片", "seal": "印章"}


def _aspects(pdf: Path) -> dict[int, float]:
    doc = pdfium.PdfDocument(str(pdf))
    try:
        return {i + 1: w / h for i, (w, h) in enumerate(doc[i].get_size() for i in range(len(doc)))}
    finally:
        doc.close()


def _pct(v) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def _table(title: str, agg: dict, label=lambda k: k, extra_cols=None) -> list[str]:
    head = "| " + title + " | 应遮 | 遮全率 | 部分 | 漏遮 | 应留 | 误遮 |" + (" " + " | ".join(extra_cols[0]) + " |" if extra_cols else "")
    sep = "|" + "---|" * (7 + (len(extra_cols[0]) if extra_cols else 0))
    rows = [head, sep]
    for k, a in agg.items():
        extra = (" " + " | ".join(str(v) for v in extra_cols[1](k)) + " |") if extra_cols else ""
        rows.append(f"| {label(k)} | {a['redact']} | {_pct(a['recall'])} | {a['partial']} | {a['miss']} | {a['keep']} | {a['over']} |{extra}")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="在合成评估集上跑脱敏流水线并打分")
    ap.add_argument("--data", default="bench/out", type=Path)
    ap.add_argument("--only", default="", help="只跑名称包含该字符串的文档")
    ap.add_argument("--verify", action="store_true", help="开启出厂自检")
    ap.add_argument("--mode", default="strict", choices=["strict", "balanced"])
    ap.add_argument("--keep-output", action="store_true", help="保留脱敏输出与报告，便于用 bench.show 查看")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    from redactx.pipeline import Options, run

    docs = sorted((args.data / "docs").glob("*.pdf"))
    docs = [d for d in docs if args.only in d.stem]
    if not docs:
        raise SystemExit(f"{args.data}/docs 下没有文档，先运行 python -m bench.make")
    res_dir = args.data / "results"
    res_dir.mkdir(exist_ok=True)
    per_doc, all_items, all_extras = [], [], []
    for pdf in docs:
        truth = json.loads((args.data / "truth" / f"{pdf.stem}.json").read_text(encoding="utf-8"))
        work = res_dir / "work" / pdf.stem
        shutil.rmtree(work, ignore_errors=True)
        t0 = time.time()
        report = run(pdf, work, Options(verify=args.verify, mode=args.mode))
        elapsed = time.time() - t0
        scored = metrics.score_doc(truth, report, _aspects(pdf))
        for it in scored["items"]:
            it["doc"], it["variant"] = pdf.stem, truth["variant"]
        for it in scored["extras"]:
            it["doc"], it["variant"] = pdf.stem, truth["variant"]
        all_items += scored["items"]
        all_extras += scored["extras"]
        s = metrics.summarize(scored["items"], key=lambda it: "all")["all"]
        per_doc.append({"doc": pdf.stem, "variant": truth["variant"], "pages": report["pages"], "elapsed": round(elapsed, 1),
                        "sec_per_page": round(elapsed / report["pages"], 2), "extras": len(scored["extras"]), **s})
        print(f"{pdf.stem:14s} 遮全率 {_pct(s['recall']):>6s}  漏遮 {s['miss']:3d}  部分 {s['partial']:3d}  误遮 {s['over']:2d}  "
              f"多遮 {len(scored['extras']):3d}  {elapsed / report['pages']:.1f} 秒/页", flush=True)
        if args.keep_output:
            (work / "report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        else:
            shutil.rmtree(work, ignore_errors=True)
    if not args.keep_output:
        shutil.rmtree(res_dir / "work", ignore_errors=True)

    by_variant = metrics.summarize(all_items, key=lambda it: it["variant"])
    by_type = metrics.summarize(all_items)
    by_type_form = metrics.summarize([it for it in all_items if it["role"] == "redact"], key=lambda it: (it["type"], it["form"]))

    def variant_extra(v):
        ds = [d for d in per_doc if d["variant"] == v]
        pages = sum(d["pages"] for d in ds)
        return [sum(d["extras"] for d in ds), f"{sum(d['elapsed'] for d in ds) / max(pages, 1):.1f}"]

    lines = ["# 合成评估集结果", "", f"文档 {len(per_doc)} 份，共 {sum(d['pages'] for d in per_doc)} 页；"
             f"自检{'开启' if args.verify else '关闭'}；模式 {args.mode}。", ""]
    lines += ["## 按形态", ""] + _table("形态", by_variant, extra_cols=(["多遮", "秒/页"], variant_extra)) + [""]
    lines += ["## 按实体类型", ""] + _table("类型", dict(sorted(by_type.items(), key=lambda kv: (kv[1]["redact"] == 0, kv[0]))),
                                           label=lambda k: NAMES.get(k, k)) + [""]
    lines += ["## 应遮项：类型 × 书写形式", ""] + _table("类型 / 形式", dict(sorted(by_type_form.items())),
                                                       label=lambda k: f"{NAMES.get(k[0], k[0])} / {FORMS.get(k[1], k[1])}") + [""]
    misses = [it for it in all_items if it["status"] in ("miss", "partial", "over")]
    lines += ["## 漏遮、部分遮盖与误遮明细", "", "| 文档 | 页 | 类型 | 形式 | 状态 | 覆盖 | 内容（虚构） |", "|---|---|---|---|---|---|---|"]
    for it in misses:
        lines.append(f"| {it['doc']} | {it['page']} | {NAMES.get(it['type'], it['type'])} | {FORMS.get(it['form'], it['form'])} | "
                     f"{ {'miss': '漏遮', 'partial': '部分', 'over': '误遮'}[it['status']]} | {it['coverage']:.2f} | {it['text']} |")
    md = "\n".join(lines) + "\n"
    (res_dir / "summary.md").write_text(md, encoding="utf-8")
    (res_dir / "results.json").write_text(json.dumps({"docs": per_doc, "items": all_items, "extras": all_extras}, ensure_ascii=False, indent=1), encoding="utf-8")
    print()
    print("\n".join(lines[: lines.index("## 漏遮、部分遮盖与误遮明细")]))
    print(f"明细见 {res_dir / 'summary.md'}")


if __name__ == "__main__":
    main()
