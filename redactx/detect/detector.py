"""签名、印章检测模型（可选）：RT-DETR 微调后导出的 ONNX，由 train/detector.py 训练。

模型目录由 REDACTX_DETECTOR_DIR 指定（默认仓库根目录下的 models/detector），内含 model.onnx 与 config.json；
目录不存在时本模块不产生任何结果。默认不启用：须在合成与真实标注数据上确认不增加误遮后再放入模型目录。
预处理与训练一致：拉伸到 size×size、除以 255。
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import cv2
import numpy as np

from ..config import settings
from ..schemas import Rect

log = logging.getLogger("redactx.detector")
MIN_SCORE = 0.6

_lock = threading.Lock()
_model = None  # (session, classes, size)；False 表示没有模型


def _load():
    global _model
    with _lock:
        if _model is None:
            d = Path(settings.detector_dir)
            if not (d / "model.onnx").exists():
                _model = False
            else:
                import onnxruntime as ort

                cfg = json.loads((d / "config.json").read_text(encoding="utf-8"))
                opts = ort.SessionOptions()
                opts.intra_op_num_threads = settings.ner_threads
                _model = (ort.InferenceSession(str(d / "model.onnx"), opts, providers=["CPUExecutionProvider"]), cfg["classes"], cfg["size"])
                log.info("签名、印章检测模型已加载：%s", d)
        return _model or None


def available() -> bool:
    return _load() is not None


def detect(img_rgb: np.ndarray, min_score: float = MIN_SCORE) -> list[tuple[str, Rect, float]]:
    """返回 [(类型, 像素框, 分数)]。"""
    m = _load()
    if m is None:
        return []
    sess, classes, size = m
    h, w = img_rgb.shape[:2]
    x = cv2.resize(img_rgb, (size, size), interpolation=cv2.INTER_AREA).astype(np.float32).transpose(2, 0, 1)[None] / 255.0
    logits, boxes = sess.run(None, {"pixel_values": x})
    prob = 1 / (1 + np.exp(-logits[0]))
    out = []
    for q in range(prob.shape[0]):
        c = int(prob[q].argmax())
        s = float(prob[q, c])
        if s < min_score:
            continue
        cx, cy, bw, bh = boxes[0, q]
        out.append((classes[c], ((cx - bw / 2) * w, (cy - bh / 2) * h, (cx + bw / 2) * w, (cy + bh / 2) * h), s))
    return out
