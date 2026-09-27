import numpy as np

from redactx.detect import detector


def test_no_model_no_detections(monkeypatch, tmp_path):
    monkeypatch.setattr(detector, "_model", None)
    monkeypatch.setattr(detector.settings, "detector_dir", tmp_path)
    assert not detector.available()
    assert detector.detect(np.full((100, 80, 3), 255, np.uint8)) == []
    monkeypatch.setattr(detector, "_model", None)


def test_load_coco_keeps_only_detector_classes(tmp_path):
    import json

    from train.detector import load_coco

    coco = {"images": [{"id": 1, "file_name": "a.jpg", "width": 200, "height": 100}, {"id": 2, "file_name": "b.jpg", "width": 200, "height": 100}],
            "annotations": [{"image_id": 1, "category_id": 3, "bbox": [20, 10, 40, 20]}, {"image_id": 1, "category_id": 1, "bbox": [0, 0, 10, 10]}],
            "categories": [{"id": 1, "name": "PERSON"}, {"id": 3, "name": "SIGNATURE"}]}
    (tmp_path / "annotations.json").write_text(json.dumps(coco))
    data = load_coco([tmp_path])
    assert data[0][1] == [(0, [0.2, 0.2, 0.2, 0.2])]  # 只留签名，框换成归一化的中心点与宽高
    assert data[1][1] == []  # 没有框的页面作为负样本保留
