"""Pinned RF-DETR ONNX adapter, adapted from drg-oras's local visual detector.

See docs/visual-model.md for provenance, license and deployment. No runtime downloads.
The old RT-DETR adapter in detector.py deliberately remains a separate contract.
"""

from __future__ import annotations

import hashlib
import json
import threading

import cv2
import numpy as np

from ..config import settings
from ..schemas import Rect

MODEL_SHA = "5855eb13aa9614051e176479a23daa8db552c9810094f26e8ce4ba26cac9d6d9"
CONFIG_SHA = "f66e7017ec854750db525ccaea6db96d740158b4ae93f4d8c86d65a677c0758a"
_load_lock = threading.Lock()
_infer_lock = threading.Lock()  # bound peak memory across page/job worker threads
_model = None
_CLASSES = ("SEAL", "SIGNATURE")


class VisualModelError(RuntimeError):
    """Enabled visual detection cannot produce trustworthy evidence."""


def _load():
    global _model
    with _load_lock:
        if _model is None:
            from ..integrity import sha256

            root = settings.visual_model_dir
            try:
                manifest = json.loads((root / "manifest.json").read_text())
                for name, digest in (("model.onnx", MODEL_SHA), ("config.json", CONFIG_SHA)):
                    if manifest["sha256"][name] != digest or sha256(root / name) != digest:
                        raise VisualModelError("VISUAL_MODEL_INTEGRITY_FAILURE")
                cfg = json.loads((root / "config.json").read_text())
                import onnxruntime as ort

                opts = ort.SessionOptions()
                opts.intra_op_num_threads = max(1, settings.visual_threads)
                opts.inter_op_num_threads = 1
                session = ort.InferenceSession(str(root / "model.onnx"), opts, providers=["CPUExecutionProvider"])
                if len(session.get_inputs()) != 1 or session.get_inputs()[0].shape != [1, 3, 560, 560]:
                    raise VisualModelError("VISUAL_MODEL_INPUT_INVALID")
                if {o.name for o in session.get_outputs()} != {"boxes", "logits"}:
                    raise VisualModelError("VISUAL_MODEL_OUTPUT_INVALID")
                bundle = hashlib.sha256(f"{MODEL_SHA}:{CONFIG_SHA}".encode()).hexdigest()[:16]
                _model = session, cfg, f"rf-detr-bluecopa/{bundle}"
            except VisualModelError:
                raise
            except Exception as exc:
                raise VisualModelError("VISUAL_MODEL_UNAVAILABLE") from exc
        return _model


def version() -> str:
    return _load()[2]


def overlap(a: Rect, b: Rect) -> float:
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union > 0 else 0


def _infer(image: np.ndarray, threshold: float) -> list[tuple[str, Rect, float]]:
    session, cfg, _ = _load()
    h, w = image.shape[:2]
    x = cv2.resize(image, (560, 560), interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255
    x = (x - np.array(cfg["mean"], np.float32)) / np.array(cfg["std"], np.float32)
    outputs = session.run(None, {session.get_inputs()[0].name: x.transpose(2, 0, 1)[None]})
    values = dict(zip((o.name for o in session.get_outputs()), outputs))
    boxes, logits = values["boxes"][0], values["logits"][0]
    if (boxes.ndim != 2 or boxes.shape[1] != 4 or logits.ndim != 2
            or logits.shape != (len(boxes), 4) or not np.isfinite(boxes).all() or not np.isfinite(logits).all()):
        raise VisualModelError("VISUAL_MODEL_OUTPUT_INVALID")
    scores = 1 / (1 + np.exp(-np.clip(logits, -60, 60)))
    found = []
    for q, c in enumerate(scores.argmax(axis=1)):
        if c >= len(_CLASSES) or scores[q, c] < threshold:
            continue
        cx, cy, bw, bh = boxes[q]
        rect = (max(0, float((cx - bw / 2) * w)), max(0, float((cy - bh / 2) * h)),
                min(w, float((cx + bw / 2) * w)), min(h, float((cy + bh / 2) * h)))
        if rect[2] > rect[0] and rect[3] > rect[1]:
            found.append((_CLASSES[c], rect, float(scores[q, c])))
    return found


def detect(image: np.ndarray, min_score: float = 0.35) -> list[tuple[str, Rect, float]]:
    if image.ndim != 3 or image.shape[2] != 3 or min(image.shape[:2]) == 0:
        raise VisualModelError("INVALID_RGB_IMAGE")
    h, w = image.shape[:2]
    crops = [(0, 0, w, h)]
    if max(h, w) > 1000:
        cw, ch = int(w * .65), int(h * .65)
        crops += [(x, y, x + cw, y + ch) for y in (0, h - ch) for x in (0, w - cw)]
    hits = []
    try:
        with _infer_lock:
            for x0, y0, x1, y1 in crops:
                for kind, box, score in _infer(image[y0:y1, x0:x1], min_score):
                    hits.append((kind, (box[0] + x0, box[1] + y0, box[2] + x0, box[3] + y0), score))
    except VisualModelError:
        raise
    except Exception as exc:
        raise VisualModelError("VISUAL_INFERENCE_FAILED") from exc
    from ..vision import double_ring_seal

    # Resolve only positive stamp geometry; do not guess from box aspect or colour alone.
    stamps = [box for _, box, _ in hits if double_ring_seal(image, box)]
    resolved = []
    for kind, box, score in hits:
        for seal in stamps:
            area = (box[2]-box[0]) * (box[3]-box[1])
            intersection = max(0, min(box[2], seal[2])-max(box[0], seal[0])) * max(0, min(box[3], seal[3])-max(box[1], seal[1]))
            # A tile may report just an arc of the same stamp as a signature.
            cx = (box[0]+box[2]-seal[0]-seal[2]) / max(seal[2]-seal[0], 1)
            cy = (box[1]+box[3]-seal[1]-seal[3]) / max(seal[3]-seal[1], 1)
            rim_fragment = intersection / area > .85 and cx*cx + cy*cy > .55
            if overlap(box, seal) > .8 or rim_fragment:
                kind = "SEAL"
                break
        resolved.append((kind, box, score))
    out = []
    for hit in sorted(resolved, key=lambda x: -x[2]):
        if not any(overlap(hit[1], old[1]) > (.45 if hit[0] == old[0] else .8) for old in out):
            out.append(hit)
    if len(out) > 100:
        raise VisualModelError("VISUAL_DETECTION_LIMIT_REQUIRES_REVIEW")
    return out
