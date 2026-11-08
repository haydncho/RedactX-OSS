"""Visual redaction regions and evidence from the actual saved output artifact."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .config import settings
from .detect import rfdet
from .ingest import iter_pages, sniff
from .schemas import PageData, Region

TYPES = {"SEAL", "SIGNATURE"}


def health() -> dict:
    if not settings.visual_enabled:
        return {"enabled": False, "ready": False, "version": None}
    try:
        return {"enabled": True, "ready": True, "version": rfdet.version()}
    except rfdet.VisualModelError:
        return {"enabled": True, "ready": False, "version": None}


def regions(image: np.ndarray, page: int, enabled: set[str]) -> list[Region]:
    if not settings.visual_enabled or not TYPES & enabled:
        return []
    h, w = image.shape[:2]
    out = []
    for kind, box, _ in rfdet.detect(image):
        if kind not in enabled:
            continue
        dx, dy = max(4, (box[2] - box[0]) * .08), max(4, (box[3] - box[1]) * .08)
        rect = (max(0, box[0] - dx), max(0, box[1] - dy), min(w, box[2] + dx), min(h, box[3] + dy))
        out.append(Region(kind, "visual-model", page, rect))
    return out


def protect_dates(candidates: list[Region], page: PageData) -> tuple[list[Region], list[Region]]:
    """A model prediction overlapping a recognized date needs human review."""
    from .detect.anchors import RE_DATE_LOOSE

    date_boxes = []
    for line in page.lines:
        for match in RE_DATE_LOOSE.finditer(line.text):
            date_boxes.extend(c.box for c in line.chars[match.start():match.end()])
    accepted, concerns = [], []
    for region in candidates:
        x0, y0, x1, y1 = region.rect
        overlaps = any(min(x1, b[2]) > max(x0, b[0]) and min(y1, b[3]) > max(y0, b[1]) for b in date_boxes)
        (concerns if overlaps else accepted).append(region)
    return accepted, concerns


def check_output(path: Path, enabled: set[str], expected_pages: int | None = None) -> dict:
    if not settings.visual_enabled:
        return {"status": "disabled", "pages": 0, "findings": [], "model_version": None}
    selected = TYPES & enabled
    if not selected:
        return {"status": "not_applicable", "pages": 0, "findings": [], "model_version": None}
    version = rfdet.version()  # fail explicitly; absence is never an empty successful check
    findings, pages = [], 0
    for image, pd in iter_pages(path, sniff(path), 150):
        pages += 1
        h, w = image.shape[:2]
        for kind, box, score in rfdet.detect(image, min_score=.25):
            if kind in selected:
                findings.append({"page": pd.index + 1, "type": kind, "score": round(score, 4),
                                 "box": [round(box[0] / w, 4), round(box[1] / h, 4), round(box[2] / w, 4), round(box[3] / h, 4)]})
    if pages == 0 or pages > settings.max_pages or (expected_pages is not None and pages != expected_pages):
        raise rfdet.VisualModelError("VISUAL_PAGE_COUNT_MISMATCH")
    return {"status": "review" if findings else "passed", "pages": pages, "findings": findings,
            "model_version": version, "threshold": .25, "entities": sorted(selected)}
