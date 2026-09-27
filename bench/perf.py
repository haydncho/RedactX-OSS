"""性能基准：生成约 100 页的合成文档，测总耗时、每页耗时、首页耗时（含模型加载）与内存峰值。

    python -m bench.perf --out bench/perf [--pages 100]

每份文档在独立进程中处理，内存峰值互不影响。测量前检查系统负载：负载高时结果偏慢，会给出提示。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pikepdf

from . import make

KINDS = {"text": "系统导出（文字层）", "scan": "白纸扫描 200 DPI（含手写页）"}


def build_long(kind: str, pages: int, out: Path) -> Path:
    """用多份合成病案拼出约 pages 页的文档。"""
    parts = out / f"parts-{kind}"
    parts.mkdir(parents=True, exist_ok=True)
    merged = pikepdf.new()
    k = 0
    while len(merged.pages) < pages:
        k += 1
        pdf = pikepdf.open(make.build(1000 + k, kind, parts))
        merged.pages.extend(pdf.pages)
    path = out / f"perf-{kind}-{len(merged.pages)}p.pdf"
    merged.save(path)
    return path


def _child(pdf: str, work: str) -> None:
    """子进程：处理一份文档，输出耗时与内存峰值。"""
    import resource
    import shutil

    from redactx.pipeline import Options, run

    t0 = time.time()
    first = {}

    def progress(p, msg):
        if "识别第 1/" in msg and "first" not in first:
            first["first"] = time.time() - t0

    rep = run(Path(pdf), Path(work), Options(), progress)
    total = time.time() - t0
    shutil.rmtree(work, ignore_errors=True)
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_mb = peak / 1024 / 1024 if sys.platform == "darwin" else peak / 1024  # macOS 单位为字节，Linux 为 KB
    print(json.dumps({"pages": rep["pages"], "total_sec": round(total, 1), "sec_per_page": round(total / rep["pages"], 2),
                      "first_page_sec": round(first.get("first", 0), 1), "peak_mb": round(peak_mb)}))


def main() -> None:
    ap = argparse.ArgumentParser(description="性能基准（合成数据）")
    ap.add_argument("--out", default="bench/perf", type=Path)
    ap.add_argument("--pages", default=100, type=int)
    ap.add_argument("--child", nargs=2, help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.child:
        _child(*a.child)
        return
    load = os.getloadavg()[0]
    cpus = os.cpu_count() or 1
    a.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for kind, label in KINDS.items():
        pdf = build_long(kind, a.pages, a.out)
        r = subprocess.run([sys.executable, "-m", "bench.perf", "--child", str(pdf), str(a.out / f"work-{kind}")],
                           capture_output=True, text=True, check=True)
        res = json.loads(r.stdout.strip().splitlines()[-1])
        rows.append((label, res))
        print(f"{label}: {res}", flush=True)
    lines = ["| 文档 | 页数 | 总耗时 | 每页 | 首页（含模型加载） | 内存峰值 |", "|---|---|---|---|---|---|"]
    for label, r in rows:
        lines.append(f"| {label} | {r['pages']} | {r['total_sec']} 秒 | {r['sec_per_page']} 秒 | {r['first_page_sec']} 秒 | {r['peak_mb']} MB |")
    note = f"测量时系统 1 分钟负载 {load:.1f}（{cpus} 核）" + ("；负载偏高，结果可能偏慢" if load > cpus * 0.5 else "")
    md = "\n".join(lines) + "\n\n" + note + "\n"
    (a.out / "perf.md").write_text(md, encoding="utf-8")
    print("\n" + md)


if __name__ == "__main__":
    main()
