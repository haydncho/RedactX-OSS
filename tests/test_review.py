import json

import numpy as np
import pytest
from PIL import Image

from redactx import review


def _job(tmp_path, editable):
    out = tmp_path / "out"
    for d in ("pages", "preview", "orig"):
        (out / d).mkdir(parents=True)
    page = np.full((300, 200, 3), 255, np.uint8)
    page[40:60, 20:80] = 0  # 已打码的框
    Image.fromarray(page).save(out / "pages" / "00001.jpg", quality=95)
    orig = np.full((300, 200, 3), 255, np.uint8)
    orig[40:60, 20:80] = 120  # 打码前的内容
    if editable:
        Image.fromarray(orig).save(out / "orig" / "00001.jpg", quality=95)
    else:
        (out / "orig").rmdir()
    item = {"page": 1, "type": "PERSON", "source": "anchor", "style": "black", "alias": None, "box": [0.1, 0.1333, 0.4, 0.2]}
    report = {"pages": 1, "output": "redacted.pdf", "items": [item], "counts": {"PERSON": 1}, "page_meta": [{"page": 1, "rotation": 0}],
              "options": {"styles": {"PERSON": "black"}, "default_style": "label"}}
    (out / "report.json").write_text(json.dumps(report))
    (out / "layout.json").write_text(json.dumps({"kind": "pdf", "sizes": [[72, 108]], "dpi": 200, "label_text": "type"}))
    return out, item


def _page(out):
    return np.array(Image.open(out / "pages" / "00001.jpg").convert("L")).astype(int)


def test_add_box_without_source(tmp_path):
    out, item = _job(tmp_path, editable=False)
    new = {"page": 1, "type": "PHONE", "box": [0.5, 0.5, 0.9, 0.6], "style": "black"}
    rep = review.apply(out, [item, new])
    assert rep["counts"] == {"PERSON": 1, "PHONE": 1} and rep["items"][1]["source"] == "manual"
    assert _page(out)[165, 140] < 60  # 新框已打码
    assert _page(out)[50, 50] < 60  # 原有的框不变
    assert (out / "redacted.pdf").exists() and (out / "preview" / "after-1.jpg").exists()


def test_remove_needs_source(tmp_path):
    out, _ = _job(tmp_path, editable=False)
    with pytest.raises(review.ReviewError) as e:
        review.apply(out, [])
    assert e.value.code == "NOT_EDITABLE"


def test_remove_box_restores_from_source(tmp_path):
    out, _ = _job(tmp_path, editable=True)
    rep = review.apply(out, [])
    assert rep["counts"] == {} and rep["review"]["edits"] == 1
    assert 100 < _page(out)[50, 50] < 140  # 恢复成打码前的内容
    assert review.finish(out)["review"]["editable"] is False
    assert not (out / "orig").exists()


def test_invalid_items(tmp_path):
    out, item = _job(tmp_path, editable=True)
    for bad in ([{"page": 2, "type": "PERSON", "box": [0, 0, 1, 1]}], [{"page": 1, "type": "NOPE", "box": [0, 0, 1, 1]}],
                [{"page": 1, "type": "PERSON", "box": [0.5, 0.5, 0.5, 0.6]}], "x"):
        with pytest.raises(review.ReviewError):
            review.apply(out, bad)


def test_export_coco(tmp_path):
    import zipfile

    out, item = _job(tmp_path, editable=True)
    dest = review.export(out, tmp_path / "e.zip")
    with zipfile.ZipFile(dest) as z:
        names = z.namelist()
        coco = json.loads(z.read("annotations.json"))
    assert "images/page-0001.jpg" in names
    ann = coco["annotations"][0]
    assert ann["bbox"] == [20.0, 40.0, 60.0, 20.0]  # box [0.1, 0.1333, 0.4, 0.2] × 200×300
    assert any(c["name"] == "PERSON" and c["id"] == ann["category_id"] for c in coco["categories"])


def test_export_needs_source(tmp_path):
    out, _ = _job(tmp_path, editable=False)
    with pytest.raises(review.ReviewError):
        review.export(out, tmp_path / "e.zip")
