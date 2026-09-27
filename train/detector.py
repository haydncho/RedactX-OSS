"""训练签名、印章检测模型（RT-DETR，Apache-2.0），导出 ONNX 供 redactx.detect.detector 使用。

    uv pip install --python .venv/bin/python -e ".[train]"      # torch、transformers、scipy
    .venv/bin/python -m train.detector --coco bench/train/coco [--coco 标注导出目录 ...] --out train/runs/det1
    .venv/bin/python -m train.detector --export train/runs/det1 --to models/detector

训练数据为 COCO 格式：合成数据用 bench.to_coco 生成；真实标注用复核台“导出标注”的压缩包解压后的目录。
类别按实体类型名对应（SIGNATURE、SEAL），其余类别忽略。预处理与推理完全一致：拉伸到 SIZE×SIZE、除以 255，不做归一化。
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

import cv2
import numpy as np

BASE = "PekingU/rtdetr_r18vd"
BASE_REVISION = "ac77a11ff0"  # Apache-2.0，COCO 预训练
CLASSES = ["SIGNATURE", "SEAL"]
SIZE = 960


def preprocess(img_rgb: np.ndarray, size: int = SIZE) -> np.ndarray:
    from redactx.detect.detector import preprocess as infer_preprocess  # 与推理共用同一份预处理

    return infer_preprocess(img_rgb, size)


def load_coco(dirs: list[Path]) -> list[tuple[Path, list[tuple[int, list[float]]]]]:
    """[(图片路径, [(类别序号, [cx, cy, w, h] 归一化)])]；只收 CLASSES 里的类别，没有框的页面也保留（负样本）。"""
    out = []
    for d in dirs:
        coco = json.loads((d / "annotations.json").read_text(encoding="utf-8"))
        names = {c["id"]: c["name"] for c in coco["categories"]}
        by_img: dict[int, list] = {}
        for a in coco["annotations"]:
            name = names.get(a["category_id"])
            if name in CLASSES:
                by_img.setdefault(a["image_id"], []).append((CLASSES.index(name), a["bbox"]))
        for im in coco["images"]:
            w, h = im["width"], im["height"]
            boxes = [(c, [(x + bw / 2) / w, (y + bh / 2) / h, bw / w, bh / h]) for c, (x, y, bw, bh) in by_img.get(im["id"], [])]
            out.append((d / im["file_name"], boxes))
    return out


def train(a) -> None:
    import torch
    from transformers import RTDetrForObjectDetection

    data = load_coco(a.coco)
    random.Random(0).shuffle(data)
    n_val = max(1, len(data) // 20)
    val, tr = data[:n_val], data[n_val:]
    print(f"训练 {len(tr)} 页，验证 {len(val)} 页，框 {sum(len(b) for _, b in tr)} 个", flush=True)
    dev = torch.device(a.device)
    model = RTDetrForObjectDetection.from_pretrained(BASE, revision=BASE_REVISION, num_labels=len(CLASSES), id2label=dict(enumerate(CLASSES)),
                                                     label2id={c: k for k, c in enumerate(CLASSES)}, ignore_mismatched_sizes=True).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-4)
    steps = a.epochs * ((len(tr) + a.batch - 1) // a.batch)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=steps, pct_start=0.1)

    def batch_of(items, aug):
        xs, labels = [], []
        for path, boxes in items:
            img = cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB)
            if aug:  # 轻度亮度、对比度扰动
                img = np.clip(img.astype(np.float32) * random.uniform(0.85, 1.1) + random.uniform(-15, 15), 0, 255).astype(np.uint8)
            xs.append(preprocess(img, a.size))
            labels.append({"class_labels": torch.tensor([c for c, _ in boxes], dtype=torch.long, device=dev),
                           "boxes": torch.tensor([b for _, b in boxes], dtype=torch.float32, device=dev).reshape(-1, 4)})
        return torch.tensor(np.stack(xs), device=dev), labels

    a.out.mkdir(parents=True, exist_ok=True)
    t0, step = time.time(), 0
    for ep in range(a.epochs):
        model.train()
        random.shuffle(tr)
        for k in range(0, len(tr), a.batch):
            x, labels = batch_of(tr[k : k + a.batch], True)
            loss = model(pixel_values=x, labels=labels).loss
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 0.1)
            opt.step()
            sched.step()
            step += 1
            if step % 20 == 0:
                print(f"第 {ep + 1} 轮 {step}/{steps} 步  损失 {loss.item():.3f}  {time.time() - t0:.0f} 秒", flush=True)
        model.eval()
        with torch.no_grad():
            vl = [model(pixel_values=x, labels=l).loss.item() for x, l in (batch_of(val[k : k + a.batch], False) for k in range(0, len(val), a.batch))]
        print(f"第 {ep + 1} 轮结束，验证损失 {np.mean(vl):.3f}", flush=True)
        model.save_pretrained(a.out / "hf")
    (a.out / "train.json").write_text(json.dumps({"base": BASE, "revision": BASE_REVISION, "classes": CLASSES, "size": a.size, "epochs": a.epochs,
                                                   "pages": len(tr), "sources": [str(d) for d in a.coco]}, ensure_ascii=False, indent=1), encoding="utf-8")


def export(run: Path, to: Path) -> None:
    import torch
    from transformers import RTDetrForObjectDetection

    meta = json.loads((run / "train.json").read_text(encoding="utf-8"))
    model = RTDetrForObjectDetection.from_pretrained(run / "hf").eval()

    class Wrap(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, pixel_values):
            o = self.m(pixel_values=pixel_values)
            return o.logits, o.pred_boxes

    to.mkdir(parents=True, exist_ok=True)
    x = torch.zeros(1, 3, meta["size"], meta["size"])
    torch.onnx.export(Wrap(model), (x,), str(to / "model.onnx"), input_names=["pixel_values"], output_names=["logits", "boxes"],
                      opset_version=17, dynamo=False)
    (to / "config.json").write_text(json.dumps({"classes": meta["classes"], "size": meta["size"], "base": meta["base"], "revision": meta["revision"],
                                                "trained_on": meta["sources"], "pages": meta["pages"]}, ensure_ascii=False, indent=1), encoding="utf-8")
    (to / "NOTICE").write_text(f"签名、印章检测模型：由 {meta['base']}（Apache-2.0，版本 {meta['revision']}）在本项目数据上微调后导出为 ONNX。\n", encoding="utf-8")
    from redactx.integrity import write_sums

    write_sums(to, ["model.onnx", "config.json"])  # 服务加载前核对
    print(f"已导出到 {to}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--coco", type=Path, action="append", default=[])
    ap.add_argument("--out", type=Path, default=Path("train/runs/det"))
    ap.add_argument("--epochs", type=int, default=8)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--size", type=int, default=SIZE)
    ap.add_argument("--device", default="mps")
    ap.add_argument("--export", type=Path, help="导出这次训练的结果为 ONNX")
    ap.add_argument("--to", type=Path, default=Path("models/detector"))
    a = ap.parse_args()
    if a.export:
        export(a.export, a.to)
    else:
        train(a)


if __name__ == "__main__":
    main()
