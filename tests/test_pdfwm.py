"""PDF 结构层面的水印去除。用 pikepdf 构造只含色块的小 PDF（不需要字体），渲染后检查像素。"""

import numpy as np
import pikepdf
import pypdfium2 as pdfium

from redactx import pdfwm

# 正文：左下角黑块；水印：页面中部灰块
BODY = b"0 g 20 20 80 40 re f\n"
WM_BOX = b"0.7 g 150 150 200 200 re f\n"


def _pdf(tmp_path, content: bytes, setup=None):
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(400, 400))
    page = pdf.pages[0]
    page.obj.Resources = pikepdf.Dictionary()
    if setup:
        setup(pdf, page)
    page.obj.Contents = pdf.make_stream(content)
    path = tmp_path / "in.pdf"
    pdf.save(path)
    return path


def _pixels(path):
    img = pdfium.PdfDocument(str(path))[0].render(scale=1).to_numpy()
    wm = img[400 - 250, 250, :3]  # 水印块中心（PDF 坐标 y 向上）
    body = img[400 - 40, 60, :3]  # 正文黑块
    return int(wm.mean()), int(body.mean())


def _check(tmp_path, src, expect_removed=1):
    wm, body = _pixels(src)
    assert wm < 230 and body < 30  # 清理前：水印可见
    dst = tmp_path / "out.pdf"
    assert pdfwm.strip_watermarks(src, dst) == expect_removed
    wm, body = _pixels(dst)
    assert wm > 245, "水印未去除"
    assert body < 30, "正文被误删"


def test_artifact_watermark(tmp_path):
    src = _pdf(tmp_path, BODY + b"/Artifact <</Type /Pagination /Subtype /Watermark>> BDC\n" + WM_BOX + b"EMC\n")
    _check(tmp_path, src)


def test_watermark_annotation(tmp_path):
    def setup(pdf, page):
        ap = pdf.make_stream(WM_BOX, Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form, BBox=[0, 0, 400, 400])
        annot = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Watermark,
                                                     Rect=[0, 0, 400, 400], AP=pikepdf.Dictionary(N=ap)))
        page.obj.Annots = pikepdf.Array([annot])

    _check(tmp_path, _pdf(tmp_path, BODY, setup))


def _ocg_setup(name):
    def setup(pdf, page):
        ocg = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.OCG, Name=pikepdf.String(name)))
        pdf.Root.OCProperties = pikepdf.Dictionary(OCGs=[ocg], D=pikepdf.Dictionary(ON=[ocg]))
        page.obj.Resources.Properties = pikepdf.Dictionary(oc1=ocg)
        form = pdf.make_stream(WM_BOX, Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form, BBox=[0, 0, 400, 400], OC=ocg)
        page.obj.Resources.XObject = pikepdf.Dictionary(Fm1=form)

    return setup


def test_watermark_layer_marked_content(tmp_path):
    src = _pdf(tmp_path, BODY + b"/OC /oc1 BDC\n" + WM_BOX + b"EMC\n", _ocg_setup("水印"))
    _check(tmp_path, src)


def test_watermark_layer_xobject(tmp_path):
    src = _pdf(tmp_path, BODY + b"q /Fm1 Do Q\n", _ocg_setup("Watermark"))
    _check(tmp_path, src)


def test_ordinary_layer_and_plain_pdf_untouched(tmp_path):
    # 普通图层里的内容不动
    src = _pdf(tmp_path, BODY + b"/OC /oc1 BDC\n" + WM_BOX + b"EMC\n", _ocg_setup("Layer 1"))
    assert pdfwm.strip_watermarks(src, tmp_path / "out.pdf") == 0
    assert not (tmp_path / "out.pdf").exists()
    # 普通灰块（没有水印标记）不动
    src = _pdf(tmp_path, BODY + WM_BOX)
    assert pdfwm.strip_watermarks(src, tmp_path / "out2.pdf") == 0
    assert np.array(_pixels(src)).min() < 230


def test_slanted_faint_text_watermark(tmp_path):
    # 没有任何标记、只是斜着写的半透明浅色文字（压在照片上时按像素擦不掉）
    def setup(pdf, page):
        font = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica))
        page.obj.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font),
                                                ExtGState=pikepdf.Dictionary(G1=pikepdf.Dictionary(ca=0.35)))

    wm = b"q 0.6 g /G1 gs BT 0.8660 0.5 -0.5 0.8660 170 190 Tm /F1 60 Tf (MMMM) Tj ET Q\n"
    src = _pdf(tmp_path, BODY + b"BT 1 0 0 1 20 300 Tm /F1 12 Tf (body) Tj ET\n" + wm, setup)
    dst = tmp_path / "out.pdf"
    assert pdfwm.strip_watermarks(src, dst) == 1  # 横排的正文文字不删
    with pikepdf.open(dst) as out:
        content = out.pages[0].Contents.read_bytes()
    assert b"body" in content and b"MMMM" not in content
    # 报告里的水印区域取自删除前后文字层里斜向文字的差别
    from redactx.ingest import slanted_text

    assert [t for t, _ in slanted_text(src, 72).get(0, [])] == ["MMMM"]
    assert slanted_text(dst, 72) == {}
