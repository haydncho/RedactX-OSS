"""Visual detection must preserve class, page coordinates and evidence freshness."""

import json

import numpy as np
import pytest

from redactx import visual
from redactx.detect import rfdet


def test_disabled_does_not_load_model(monkeypatch):
    monkeypatch.setattr(visual.settings, "visual_enabled", False)
    monkeypatch.setattr(rfdet, "version", lambda: pytest.fail("disabled model loaded"))
    assert visual.health() == {"enabled": False, "ready": False, "version": None}
    assert visual.regions(np.zeros((100, 100, 3), np.uint8), 0, {"SEAL"}) == []


def test_regions_pad_and_honor_entity_selection(monkeypatch):
    monkeypatch.setattr(visual.settings, "visual_enabled", True)
    monkeypatch.setattr(rfdet, "detect", lambda *a, **kw: [
        ("SEAL", (10, 20, 50, 60), .9), ("SIGNATURE", (60, 70, 95, 90), .8)])
    regions = visual.regions(np.zeros((100, 100, 3), np.uint8), 2, {"SEAL"})
    assert len(regions) == 1
    assert regions[0].type == "SEAL" and regions[0].page == 2
    assert regions[0].rect == (6, 16, 54, 64)


def test_missing_enabled_model_fails_explicitly(monkeypatch, tmp_path):
    monkeypatch.setattr(visual.settings, "visual_enabled", True)
    monkeypatch.setattr(visual.settings, "visual_model_dir", tmp_path)
    monkeypatch.setattr(rfdet, "_model", None)
    assert visual.health()["ready"] is False
    with pytest.raises(rfdet.VisualModelError):
        visual.regions(np.zeros((100, 100, 3), np.uint8), 0, {"SIGNATURE"})


def test_manifest_cannot_substitute_another_model(monkeypatch, tmp_path):
    import hashlib

    model = b"not the approved model"
    (tmp_path / "model.onnx").write_bytes(model)
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "manifest.json").write_text(json.dumps({"sha256": {
        "model.onnx": hashlib.sha256(model).hexdigest(),
        "config.json": hashlib.sha256(b"{}").hexdigest()}}))
    monkeypatch.setattr(visual.settings, "visual_model_dir", tmp_path)
    monkeypatch.setattr(rfdet, "_model", None)
    with pytest.raises(rfdet.VisualModelError, match="INTEGRITY"):
        rfdet.version()


def test_check_output_records_residuals(monkeypatch, tmp_path):
    from PIL import Image

    monkeypatch.setattr(visual.settings, "visual_enabled", True)
    monkeypatch.setattr(rfdet, "version", lambda: "rf-detr/test")
    monkeypatch.setattr(rfdet, "detect", lambda *a, **kw: [("SIGNATURE", (10, 20, 50, 60), .4)])
    path = tmp_path / "result.png"
    Image.new("RGB", (100, 100), "white").save(path)
    evidence = visual.check_output(path, {"SIGNATURE"})
    assert evidence["status"] == "review" and evidence["pages"] == 1
    assert evidence["findings"][0]["box"] == [.1, .2, .5, .6]
    assert evidence["findings"][0]["page"] == 1
    assert evidence["model_version"] == "rf-detr/test"


def test_no_selected_visual_entities_is_not_a_pass(monkeypatch, tmp_path):
    monkeypatch.setattr(visual.settings, "visual_enabled", True)
    assert visual.check_output(tmp_path / "absent.pdf", {"PERSON"})["status"] == "not_applicable"


def test_final_page_count_must_match_source(monkeypatch, tmp_path):
    from PIL import Image

    monkeypatch.setattr(visual.settings, "visual_enabled", True)
    monkeypatch.setattr(rfdet, "version", lambda: "test")
    monkeypatch.setattr(rfdet, "detect", lambda *a, **kw: [])
    src = tmp_path / "result.png"
    Image.new("RGB", (100, 100), "white").save(src)
    with pytest.raises(rfdet.VisualModelError, match="PAGE_COUNT_MISMATCH"):
        visual.check_output(src, {"SEAL"}, expected_pages=2)


def test_visual_signature_over_date_requires_review_instead_of_redaction():
    from redactx.schemas import Char, Line, PageData, Region

    line = Line([Char(c, (10 + i * 8, 20, 18 + i * 8, 35)) for i, c in enumerate("2025.10.04")], "ocr")
    pd = PageData(0, 200, 300, lines=[line])
    signature = Region("SIGNATURE", "visual-model", 0, (5, 15, 100, 40))
    accepted, concerns = visual.protect_dates([signature], pd)
    assert accepted == [] and concerns == [signature]


def test_default_tiff_resolution_does_not_create_giant_pdf(tmp_path):
    from PIL import Image
    from redactx.ingest import open_doc

    src = tmp_path / "frames.tiff"
    frames = [Image.new("RGB", (1200, 900), "white") for _ in range(2)]
    frames[0].save(src, save_all=True, append_images=frames[1:])
    doc = open_doc(src, None, 10)
    assert doc.page_count == 2
    assert all(w < 2000 and h < 2000 for w, h in doc.page_sizes_pt)


def test_merging_rule_and_model_regions_keeps_both_sources():
    from redactx.pipeline import _merge
    from redactx.schemas import Region

    merged = _merge([Region("SEAL", "color", 0, (0, 0, 20, 20)),
                     Region("SEAL", "visual-model", 0, (2, 2, 22, 22))])
    assert len(merged) == 1
    assert set(merged[0].sources) == {"color", "visual-model"}


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_invalid_model_outputs_are_rejected(monkeypatch, bad):
    class Name:
        def __init__(self, name):
            self.name = name

    class Session:
        def get_inputs(self):
            return [Name("pixel_values")]

        def get_outputs(self):
            return [Name("logits"), Name("boxes")]

        def run(self, *args):
            return [np.zeros((1, 1, 4)), np.array([[[bad, .5, .2, .2]]])]

    monkeypatch.setattr(rfdet, "_model", (Session(), {"mean": [0, 0, 0], "std": [1, 1, 1]}, "test"))
    with pytest.raises(rfdet.VisualModelError, match="OUTPUT_INVALID"):
        rfdet.detect(np.zeros((100, 100, 3), np.uint8))


def test_class_names_output_order_and_checkbox_exclusion(monkeypatch):
    class Name:
        def __init__(self, name):
            self.name = name

    class Session:
        def get_inputs(self):
            return [Name("pixel_values")]

        def get_outputs(self):
            return [Name("logits"), Name("boxes")]

        def run(self, *args):
            return [np.array([[[8, -20, -20, -20], [-20, 8, -20, -20], [-20, -20, 8, -20]]]),
                    np.array([[[.5, .5, .2, .2], [.8, .8, .1, .1], [.2, .2, .1, .1]]])]

    monkeypatch.setattr(rfdet, "_model", (Session(), {"mean": [0, 0, 0], "std": [1, 1, 1]}, "test"))
    hits = rfdet.detect(np.zeros((100, 100, 3), np.uint8))
    assert [h[0] for h in hits] == ["SEAL", "SIGNATURE"]


def test_tiled_detections_return_page_coordinates(monkeypatch):
    monkeypatch.setattr(rfdet, "_infer", lambda *a: [("SIGNATURE", (10, 20, 50, 60), .9)])
    hits = rfdet.detect(np.zeros((1200, 1000, 3), np.uint8))
    assert len(hits) == 4  # full-page/top-left duplicate removed
    assert any(hit[1] == (360, 440, 400, 480) for hit in hits)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_pipeline_rotated_coordinates_and_final_artifact(monkeypatch, tmp_path, rotation):
    from PIL import Image
    from redactx import pipeline

    monkeypatch.setattr(visual.settings, "visual_enabled", True)
    monkeypatch.setattr(visual.settings, "page_workers", 1)
    monkeypatch.setattr(rfdet, "version", lambda: "test")
    monkeypatch.setattr(pipeline.ocr, "detect_orientation", lambda img: rotation)
    monkeypatch.setattr(pipeline, "page_text", lambda *a: None)
    monkeypatch.setattr(rfdet, "detect", lambda image, min_score=.35:
                        [("SIGNATURE", (image.shape[1] * .2, image.shape[0] * .3,
                                         image.shape[1] * .4, image.shape[0] * .5), .9)] if min_score == .35 else [])
    src = tmp_path / "source.png"
    Image.new("RGB", (200, 300), "white").save(src)
    report = pipeline.run(src, tmp_path / "out", pipeline.Options(entities={"SIGNATURE"}, default_style="black", verify=False))
    assert report["visual_verification"]["status"] == "passed"
    assert report["visual_verification"]["pages"] == 1
    item = next(it for it in report["items"] if it["source"] == "visual-model")
    img = np.array(Image.open(tmp_path / "out" / report["output"]).convert("RGB"))
    x0, y0, x1, y1 = item["box"]
    assert img[int((y0 + y1) / 2 * 300), int((x0 + x1) / 2 * 200)].max() < 30


def test_native_text_pdf_also_gets_visual_detection(monkeypatch, tmp_path):
    import pikepdf
    from redactx import pipeline

    monkeypatch.setattr(visual.settings, "visual_enabled", True)
    monkeypatch.setattr(visual.settings, "page_workers", 1)
    monkeypatch.setattr(rfdet, "version", lambda: "test")
    monkeypatch.setattr(rfdet, "detect", lambda img, min_score=.35:
                        [("SEAL", (20, 20, 60, 60), .9)] if min_score == .35 else [])
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(200, 300))
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica))))
    page.Contents = pdf.make_stream(b"BT /F1 10 Tf 10 250 Td (A native PDF with enough text to extract directly) Tj ET")
    src = tmp_path / "native.pdf"
    pdf.save(src)
    pdf.close()
    report = pipeline.run(src, tmp_path / "out", pipeline.Options(entities={"SEAL"}, verify=False))
    assert report["page_meta"][0]["text_source"] == "text"
    assert any(it["source"] == "visual-model" for it in report["items"])
