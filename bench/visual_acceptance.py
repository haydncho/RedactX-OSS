"""Reproducible, local baseline/RF-DETR comparison using existing synthetic truth.

python -m bench.visual_acceptance --model local/visual-acceptance/models
Each run uses a fresh child process with its own data and output directories.
No request is sent to any running service.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

import pikepdf

from . import metrics


def child(source: Path, out: Path) -> None:
    from redactx.pipeline import Options, run

    started = time.monotonic()
    report = run(source, out, Options(keep_source=True))
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_mb = rss / (1024 ** 2 if sys.platform == "darwin" else 1024)
    print(json.dumps({"elapsed_sec": round(time.monotonic() - started, 2), "peak_rss_mb": round(rss_mb, 1)}))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", type=Path)
    ap.add_argument("--data", type=Path, default=Path("bench/out"))
    ap.add_argument("--out", type=Path, default=Path("local/visual-acceptance/comparison"))
    ap.add_argument("--child", nargs=2, type=Path)
    args = ap.parse_args()
    if args.child:
        child(*args.child)
        return
    if not args.model:
        ap.error("--model is required")
    args.out.mkdir(parents=True, exist_ok=True)
    selections = {"text": [1, 3], "scan": [1, 5], "rotated": [1, 5], "mixed": [1, 2],
                  "hard": [1, 5], "harsh": [1, 7], "photo": [1, 7]}
    rows = []
    for variant, selected in selections.items():
        truth = json.loads((args.data / "truth" / f"{variant}-001.json").read_text())
        source = args.out / f"{variant}.pdf"
        aspects = {}
        with pikepdf.open(args.data / "docs" / f"{variant}-001.pdf") as original:
            pdf = pikepdf.Pdf.new()
            for i, page in enumerate(selected):
                pdf.pages.append(original.pages[page - 1])
                box = list(pdf.pages[-1].MediaBox)
                aspects[i + 1] = float(box[2] - box[0]) / float(box[3] - box[1])
            pdf.save(source)
        mapping = {p: i + 1 for i, p in enumerate(selected)}
        truth["items"] = [{**item, "page": mapping[item["page"]]} for item in truth["items"] if item["page"] in mapping]
        (args.out / f"{variant}-truth.json").write_text(json.dumps(truth, ensure_ascii=False))
        for mode in ("baseline", "visual"):
            out = args.out / variant / mode
            env = {**os.environ, "REDACTX_VISUAL_ENABLED": "1" if mode == "visual" else "0",
                   "REDACTX_VISUAL_MODEL_DIR": str(args.model.resolve()), "REDACTX_PAGE_WORKERS": "1",
                   "REDACTX_DATA": str((args.out / "data" / variant / mode).resolve())}
            result = subprocess.run([sys.executable, "-m", "bench.visual_acceptance", "--child", str(source), str(out)],
                                    env=env, capture_output=True, text=True, timeout=300)
            if result.returncode:
                raise RuntimeError(f"{variant}/{mode}: {result.stderr[-1500:]}")
            perf = json.loads(result.stdout.strip().splitlines()[-1])
            report = json.loads((out / "report.json").read_text())
            scored = metrics.score_doc(truth, report, aspects)
            row = {"variant": variant, "mode": mode, "pages": len(selected), **perf,
                   "summary": metrics.summarize(scored["items"], key=lambda x: "all")["all"],
                   "by_type": metrics.summarize(scored["items"]), "extras": scored["extras"],
                   "visual_verification": report["visual_verification"],
                   "model_regions": sum(it["source"] == "visual-model" for it in report["items"])}
            rows.append(row)
            (args.out / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))
            print(f"{variant}/{mode}: {perf} full={row['summary']['full']}/{row['summary']['redact']} "
                  f"over={row['summary']['over']} extras={len(row['extras'])} "
                  f"visual={report['visual_verification']['status']}", flush=True)


if __name__ == "__main__":
    main()
