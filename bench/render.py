"""把表单版面渲染成 PDF 页面：系统导出（文字层）或扫描件（整页图片），同时产出标准答案框。

标准答案框一律是最终页面上的归一化坐标 [x0, y0, x1, y1]，左上角为原点，与脱敏报告的 box 字段一致。
"""

from __future__ import annotations

import ctypes
import io
import math
import random
from dataclasses import dataclass

import cv2
import numpy as np
import pypdfium2 as pdfium
import pypdfium2.raw as raw
from PIL import Image, ImageDraw

from . import fonts
from .forms import El, Page, measure, seal_rgba

_ASC = 0.88  # 文字对象基线相对字号的位置：让 El.y 大致落在字的顶部


@dataclass
class Truth:
    type: str
    role: str  # redact | keep
    form: str  # print | hand | image | seal
    box: list[float]
    text: str = ""

    def json(self, page: int) -> dict:
        return {"page": page, "type": self.type, "role": self.role, "form": self.form, "box": [round(v, 5) for v in self.box], "text": self.text}


def page_chars(pages: list[Page]) -> str:
    return "".join(sorted({ch for p in pages for e in p.els for ch in e.text} | set(USE_MARK)))


class Writer:
    """用 PDFium 写 PDF：文字对象、图片对象、线条。字体只嵌入用到的字。"""

    def __init__(self, chars: str):
        self.pdf = pdfium.PdfDocument.new()
        self._keep = []  # 字体数据必须在文档存续期间保持有效
        self.font = self._load(*fonts.print_font(), chars)
        self.bold = self._load(*fonts.bold_font(), chars)

    def _load(self, path: str, index: int, chars: str):
        data = fonts.subset_bytes(path, index, chars)
        buf = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
        self._keep.append(buf)
        font = raw.FPDFText_LoadFont(self.pdf.raw, buf, len(data), raw.FPDF_FONT_TRUETYPE, 1)
        if not font:
            raise fonts.FontMissing(f"PDFium 无法加载字体 {path}")
        return font

    # ---------- 元素 ----------
    def _text(self, page, W, H, x, y, s, size, bold) -> tuple[float, float, float, float]:
        obj = raw.FPDFPageObj_CreateTextObj(self.pdf.raw, self.bold if bold else self.font, ctypes.c_float(size))
        ws = ctypes.create_string_buffer((s + "\0").encode("utf-16-le"))
        raw.FPDFText_SetText(obj, ctypes.cast(ws, ctypes.POINTER(raw.FPDF_WCHAR)))
        raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, x, H - y - size * _ASC)
        raw.FPDFPage_InsertObject(page.raw, obj)
        l, b, r, t = (ctypes.c_float() for _ in range(4))
        raw.FPDFPageObj_GetBounds(obj, l, b, r, t)
        return l.value, H - t.value, r.value, H - b.value

    def _image(self, page, H, x, y, w, h, im: Image.Image, jpeg: bool = False):
        obj = pdfium.PdfImage.new(self.pdf)
        if jpeg:
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=72)
            buf.seek(0)
            obj.load_jpeg(buf, inline=True)
        else:
            obj.set_bitmap(pdfium.PdfBitmap.from_pil(im.convert("RGB")))
        obj.set_matrix(pdfium.PdfMatrix().scale(w, h).translate(x, H - y - h))
        page.insert_obj(obj)

    def _line(self, page, H, e: El):
        path = raw.FPDFPageObj_CreateNewPath(ctypes.c_float(e.x), ctypes.c_float(H - e.y))
        raw.FPDFPath_LineTo(path, ctypes.c_float(e.w), ctypes.c_float(H - e.h))
        raw.FPDFPageObj_SetStrokeColor(path, 0, 0, 0, 255)
        raw.FPDFPageObj_SetStrokeWidth(path, ctypes.c_float(e.size))
        raw.FPDFPath_SetDrawMode(path, raw.FPDF_FILLMODE_NONE, 1)
        raw.FPDFPage_InsertObject(page.raw, path)

    # ---------- 页面 ----------
    def _watermark(self, page, W, H, text: str, size: float, angle: float) -> list[Truth]:
        """文字层水印：旋转的浅灰半透明文字对象，平铺在页面上。"""
        out = []
        c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        for k, (fx, fy) in enumerate(WATERMARK_GRID):
            wt, wtype = _wm_text(k, text)
            tw = measure(wt, size, True)
            obj = raw.FPDFPageObj_CreateTextObj(self.pdf.raw, self.bold, ctypes.c_float(size))
            ws = ctypes.create_string_buffer((wt + "\0").encode("utf-16-le"))
            raw.FPDFText_SetText(obj, ctypes.cast(ws, ctypes.POINTER(raw.FPDF_WCHAR)))
            raw.FPDFPageObj_SetFillColor(obj, 150, 150, 150, 90)
            # 以水印中心为基准旋转
            cx, cy = W * fx, H * (1 - fy)
            x0, y0 = cx - (tw / 2 * c - size / 3 * s), cy - (tw / 2 * s + size / 3 * c)
            raw.FPDFPageObj_Transform(obj, c, s, -s, c, x0, y0)
            raw.FPDFPage_InsertObject(page.raw, obj)
            l, b, r, t = (ctypes.c_float() for _ in range(4))
            raw.FPDFPageObj_GetBounds(obj, l, b, r, t)
            out.append(Truth(wtype, "redact", "watermark", _norm((l.value, H - t.value, r.value, H - b.value), W, H), wt))
        return out

    def text_page(self, p: Page, rng: random.Random, scan_base: bool = False, watermark: bool = False) -> list[Truth]:
        """系统导出页。scan_base=True 时只画印刷部分（手写与印章留给扫描渲染）；watermark=True 时加文字层院名水印。"""
        page = self.pdf.new_page(p.w, p.h)
        W, H = p.w, p.h
        out: list[Truth] = []
        for e in p.els:
            if e.kind == "line":
                self._line(page, H, e)
            elif e.kind == "text" or (e.kind == "hand" and not e.esign and not scan_base):
                box = self._text(page, W, H, e.x, e.y, e.text, e.size, e.bold)
                if e.truth and e.text.strip():
                    out.append(Truth(e.truth[0], e.truth[1], "print", _norm(box, W, H), e.text))
            elif e.kind == "hand" and e.esign and not scan_base:
                im, _ = hand_image(e.text, 96, rng, signature=True)
                h = e.size * 1.7
                w = h * im.width / im.height
                y = e.y + e.size * 0.5 - h / 2
                self._image(page, H, e.x, y, w, h, im)
                out.append(Truth("SIGNATURE", "redact", "image", _norm((e.x, y, e.x + w, y + h), W, H), e.text))
            elif e.kind == "image":
                self._image(page, H, e.x, e.y, e.w, e.h, e.image)
                if e.truth:
                    # 答案框只框图片里的实际内容（二维码四周的静区不算）
                    fx0, fy0, fx1, fy1 = _content_box(e.image)
                    box = (e.x + fx0 * e.w, e.y + fy0 * e.h, e.x + fx1 * e.w, e.y + fy1 * e.h)
                    out.append(Truth(e.truth[0], e.truth[1], "image", _norm(box, W, H)))
            elif e.kind == "seal" and not scan_base:
                s = seal_rgba(e.text, 360)
                bg = Image.new("RGB", s.size, "white")
                bg.paste(s, mask=s.split()[3])
                self._image(page, H, e.x, e.y, e.w, e.h, bg)
                out.append(Truth("SEAL", "redact", "seal", _norm((e.x, e.y, e.x + e.w, e.y + e.h), W, H), e.text))
        if watermark:
            head = next((e for e in p.els if e.kind == "text" and e.bold and e.truth and e.truth[0] == "ORG"), None)
            if head:
                out += self._watermark(page, W, H, head.text, 26, rng.uniform(25, 35))
        page.gen_content()
        return out

    def image_page(self, im: Image.Image, w_pt: float, h_pt: float, ocr_layer: Page | None = None) -> None:
        """整页图片。ocr_layer 给出时，另加一层不可见的打印文字（可搜索 PDF：只有打印字，没有手写字）。"""
        page = self.pdf.new_page(w_pt, h_pt)
        self._image(page, h_pt, 0, 0, w_pt, h_pt, im, jpeg=True)
        for e in (ocr_layer.els if ocr_layer else []):
            if e.kind == "text" and e.text.strip():
                obj = raw.FPDFPageObj_CreateTextObj(self.pdf.raw, self.bold if e.bold else self.font, ctypes.c_float(e.size))
                ws = ctypes.create_string_buffer((e.text + "\0").encode("utf-16-le"))
                raw.FPDFText_SetText(obj, ctypes.cast(ws, ctypes.POINTER(raw.FPDF_WCHAR)))
                raw.FPDFTextObj_SetTextRenderMode(obj, raw.FPDF_TEXTRENDERMODE_INVISIBLE)
                raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, e.x, h_pt - e.y - e.size * _ASC)
                raw.FPDFPage_InsertObject(page.raw, obj)
        page.gen_content()

    def save(self, path) -> None:
        self.pdf.save(str(path))


def _content_box(im: Image.Image) -> tuple[float, float, float, float]:
    """图片中非白色内容的外框，按图片宽高归一化。"""
    a = np.asarray(im.convert("L")) < 235
    ys, xs = np.nonzero(a)
    if len(xs) == 0:
        return 0.0, 0.0, 1.0, 1.0
    h, w = a.shape
    return xs.min() / w, ys.min() / h, (xs.max() + 1) / w, (ys.max() + 1) / h


def _norm(box, W, H) -> list[float]:
    x0, y0, x1, y1 = box
    return [max(x0, 0) / W, max(y0, 0) / H, min(x1, W) / W, min(y1, H) / H]


# ---------- 手写 ----------

def hand_image(text: str, px: int, rng: random.Random, signature: bool = False, ink=(28, 36, 92)) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """用手写风格字体逐字绘制，加随机倾斜、大小和基线抖动。返回 (白底 RGB 图, 墨迹外框)。"""
    rgba = hand_rgba(text, px, rng, signature, ink)
    bg = Image.new("RGB", rgba.size, "white")
    bg.paste(rgba, mask=rgba.split()[3])
    return bg, rgba.getbbox() or (0, 0, rgba.width, rgba.height)


def watermark_rgba(text: str, px: int, angle: float, color=(150, 150, 150, 110)) -> Image.Image:
    """斜向浅灰文字水印。"""
    path, idx = fonts.bold_font()
    f = fonts.pil_font(path, idx, px)
    w = int(f.getlength(text)) + px
    tile = Image.new("RGBA", (w, int(px * 1.6)), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text((tile.width / 2, tile.height / 2), text, font=f, fill=color, anchor="mm")
    return tile.rotate(angle, resample=Image.BICUBIC, expand=True)


def hand_rgba(text: str, px: int, rng: random.Random, signature: bool = False, ink=(28, 36, 92), tail: float = 1.0) -> Image.Image:
    path, idx = rng.choice(fonts.hand_fonts()[:3] if signature else fonts.hand_fonts())
    f = fonts.pil_font(path, idx, px)
    step = 0.72 if signature else 0.95
    canvas = Image.new("RGBA", (int(px * (len(text) * step + 1.0 + 0.4 * tail)), int(px * 1.8)), (0, 0, 0, 0))
    x = px * 0.3
    for ch in text:
        tile = Image.new("RGBA", (int(px * 1.5), int(px * 1.5)), (0, 0, 0, 0))
        ImageDraw.Draw(tile).text((tile.width / 2, tile.height / 2), ch, font=f, fill=ink + (255,), anchor="mm")
        s = rng.uniform(0.88, 1.15) * (1.1 if signature else 1.0)
        tile = tile.resize((int(tile.width * s), int(tile.height * s)), Image.BICUBIC)
        tile = tile.rotate(rng.uniform(-7, 7) + (-10 if signature else 0), resample=Image.BICUBIC)
        dy = rng.uniform(-0.08, 0.08) * px
        canvas.alpha_composite(tile, (int(x - (tile.width - px) / 2 - px * 0.25), max(0, int(px * 0.15 + dy - (tile.height - px * 1.5) / 2))))
        x += px * step * rng.uniform(0.9, 1.1)
    if signature:
        # 签名常见的收笔拖尾
        d = ImageDraw.Draw(canvas)
        y = canvas.height * rng.uniform(0.62, 0.75)
        d.line([(x - px * 0.4, y), (x + px * 0.4 * tail, y - px * 0.25 * tail)], fill=ink + (230,), width=max(2, px // 18))
        canvas = canvas.transform(canvas.size, Image.AFFINE, (1, 0.25, -canvas.height * 0.12, 0, 1, 0), resample=Image.BICUBIC)
    bb = canvas.getbbox()
    return canvas.crop(bb) if bb else canvas


# ---------- 扫描件 ----------

HARD = {"faint_seal", "watermark", "big_sig"}
WATERMARK_GRID = [(0.3, 0.3), (0.72, 0.42), (0.3, 0.6), (0.72, 0.75)]  # 水印中心（页面比例）
USE_MARK = "仅供医保审核使用"  # 与院名交替出现的非机构水印：同样应整体去除


def _wm_text(k: int, hospital: str) -> tuple[str, str]:
    """第 k 个水印的文字与标准答案类型：院名与用途水印交替。"""
    return (hospital, "ORG") if k % 2 == 0 else (USE_MARK, "WATERMARK")


def scan_page(p: Page, chars: str, dpi: int, paper: str, rng: random.Random, rotate: bool = False,
              hard: frozenset[str] = frozenset(), skew: bool = True) -> tuple[Image.Image, list[Truth], tuple[float, float]]:
    """扫描件：印刷底图 + 手写 + 盖章 + 纸张与扫描退化。返回 (整页图, 标准答案, 页面尺寸 pt)。

    hard 可选的难点：faint_seal 压在院名上的淡粉色低饱和印章；watermark 斜向院名水印；
    big_sig 大号连笔签名（超出签名栏、带拖尾）。
    """
    w = Writer(chars)
    truths = w.text_page(p, rng, scan_base=True)
    scale = dpi / 72
    im = w.pdf[0].render(scale=scale).to_pil().convert("RGB")
    W, H = im.size
    # 印刷文字：换算到像素
    items = [(t, [t.box[0] * W, t.box[1] * H, t.box[2] * W, t.box[3] * H]) for t in truths]
    for e in p.els:
        if e.kind == "hand":
            big = e.esign and "big_sig" in hard
            px = int(e.size * scale * (1.35 if not e.esign else (2.6 if big else 1.6)))
            rgba = hand_rgba(e.text, px, rng, signature=e.esign, tail=2.5 if big else 1.0)
            x = int(e.x * scale + rng.uniform(0, 0.4) * px)
            # 大号签名常常压过签名栏的上沿
            y = int((e.y + e.size * 0.5) * scale - rgba.height * (0.62 if big else 0.5) + rng.uniform(-0.12, 0.12) * px)
            im.paste(rgba, (x, y), rgba)
            if e.truth:
                items.append((Truth(e.truth[0], e.truth[1], "hand", [], e.text), [x, y, x + rgba.width, y + rgba.height]))
        elif e.kind == "seal":
            size = int(e.w * scale)
            s = seal_rgba(e.text, size).rotate(rng.uniform(-18, 18), resample=Image.BICUBIC, expand=False)
            x, y = int(e.x * scale + rng.uniform(-8, 8)), int(e.y * scale + rng.uniform(-8, 8))
            _multiply(im, s, x, y, alpha=rng.uniform(0.7, 0.92))
            bb = s.getbbox() or (0, 0, size, size)
            items.append((Truth("SEAL", "redact", "seal", [], e.text), [x + bb[0], y + bb[1], x + bb[2], y + bb[3]]))
    if "faint_seal" in hard:
        # 淡粉色、饱和度很低的圆章，压在页眉院名的末尾
        head = next((e for e in p.els if e.kind == "text" and e.bold and e.truth and e.truth[0] == "ORG"), None)
        if head:
            size = int(1.3 * 72 / 2.54 * scale * 3)  # 直径约 3.9 cm
            s = seal_rgba(head.text, size, color=(236, 168, 184, 200)).rotate(rng.uniform(-25, 25), resample=Image.BICUBIC)
            cx = (head.x + measure(head.text, head.size, True)) * scale
            cy = (head.y + head.size * 0.5) * scale
            x, y = int(cx - size * 0.55), int(cy - size * 0.5)
            _multiply(im, s, x, y, alpha=rng.uniform(0.55, 0.7))
            bb = s.getbbox() or (0, 0, size, size)
            items.append((Truth("SEAL", "redact", "faint", [], head.text), [x + bb[0], y + bb[1], x + bb[2], y + bb[3]]))
    if "watermark" in hard:
        # 平铺的斜向院名水印，压在正文上
        head = next((e for e in p.els if e.kind == "text" and e.bold and e.truth and e.truth[0] == "ORG"), None)
        if head:
            ang = rng.uniform(25, 35)
            for k, (fx, fy) in enumerate(WATERMARK_GRID):
                wt, wtype = _wm_text(k, head.text)
                wm = watermark_rgba(wt, int(26 * scale), angle=ang)
                bb = wm.getbbox() or (0, 0, wm.width, wm.height)
                x, y = int(W * fx - wm.width / 2), int(H * fy - wm.height / 2)
                _multiply(im, wm, x, y, alpha=1.0)
                items.append((Truth(wtype, "redact", "watermark", [], wt), [x + bb[0], y + bb[1], x + bb[2], y + bb[3]]))
    arr, M = _degrade(np.asarray(im), paper, rng, skew)
    boxes = [_affine_box(b, M) for _, b in items]
    w_pt, h_pt = p.w, p.h
    if rotate:
        # 横放扫描：内容逆时针转 90°，页面变为横向
        Hh, Ww = arr.shape[:2]
        arr = np.ascontiguousarray(np.rot90(arr, 1))
        boxes = [[y0, Ww - x1, y1, Ww - x0] for x0, y0, x1, y1 in boxes]
        W, H = arr.shape[1], arr.shape[0]
        w_pt, h_pt = h_pt, w_pt
    else:
        H, W = arr.shape[:2]
    out = []
    for (t, _), b in zip(items, boxes):
        t.box = _norm(b, W, H)
        out.append(t)
    return Image.fromarray(arr), out, (w_pt, h_pt)


def _multiply(im: Image.Image, rgba: Image.Image, x: int, y: int, alpha: float) -> None:
    base = np.asarray(im).astype(np.float32)
    s = np.asarray(rgba).astype(np.float32)
    h, w = s.shape[:2]
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + w, base.shape[1]), min(y + h, base.shape[0])
    if x1 <= x0 or y1 <= y0:
        return
    sub = s[y0 - y : y1 - y, x0 - x : x1 - x]
    a = sub[..., 3:4] / 255 * alpha
    region = base[y0:y1, x0:x1]
    base[y0:y1, x0:x1] = region * (1 - a) + region * (sub[..., :3] / 255) * a
    im.paste(Image.fromarray(np.clip(base, 0, 255).astype(np.uint8)))


def _degrade(arr: np.ndarray, paper: str, rng: random.Random, skew: bool = True) -> tuple[np.ndarray, np.ndarray]:
    nrng = np.random.default_rng(rng.randint(0, 2**31))
    img = arr.astype(np.float32)
    h, w = img.shape[:2]
    if paper == "kraft":
        # 牛皮纸：整体偏黄褐，低频明暗起伏，加纤维噪点；墨迹略褪色
        low = cv2.resize(nrng.normal(0, 1, (8, 6)).astype(np.float32), (w, h), interpolation=cv2.INTER_CUBIC)
        tint = np.array([222, 200, 164], np.float32) + low[..., None] * 7
        img = 30 + img / 255 * (tint - 30)
        img += nrng.normal(0, 5, (h, w, 1)).astype(np.float32)
    else:
        img = 8 + img * (244 / 255)
        img += nrng.normal(0, 3.5, (h, w, 1)).astype(np.float32)
    img = cv2.GaussianBlur(np.clip(img, 0, 255), (0, 0), 0.7)
    ang = rng.uniform(-0.6, 0.6) if skew else 0.0
    M = cv2.getRotationMatrix2D((w / 2, h / 2), ang, 1.0)
    border = tuple(float(v) for v in np.median(img.reshape(-1, 3)[:: 97], axis=0))
    img = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=border)
    return np.clip(img, 0, 255).astype(np.uint8), M


def _affine_box(b, M) -> list[float]:
    x0, y0, x1, y1 = b
    pts = np.array([[x0, y0, 1], [x1, y0, 1], [x0, y1, 1], [x1, y1, 1]], np.float32) @ M.T
    return [float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())]



# ---------- MRC 分层压缩 ----------

def write_mrc_pdf(pages: list[tuple[Image.Image, float, float]], path, bg_dpi_ratio: float = 0.5) -> None:
    """按复印机 MRC 方式写 PDF：低分辨率彩色底图（JPEG）+ 全分辨率 1 位文字蒙版（ImageMask，涂黑色）。

    文字与手写都进蒙版并变成纯黑；印章等彩色、浅色内容只留在低分辨率底图里。
    """
    import zlib

    import pikepdf

    pdf = pikepdf.new()
    for im, w_pt, h_pt in pages:
        arr = np.asarray(im.convert("RGB"))
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        ink = gray < 110
        # 底图：抹掉文字后缩小，文字处用周边颜色填充
        bg = cv2.inpaint(arr, cv2.dilate(ink.astype(np.uint8), np.ones((3, 3), np.uint8)), 3, cv2.INPAINT_TELEA)
        bg = cv2.resize(bg, None, fx=bg_dpi_ratio, fy=bg_dpi_ratio, interpolation=cv2.INTER_AREA)
        buf = io.BytesIO()
        Image.fromarray(bg).save(buf, "JPEG", quality=55)
        bg_img = pikepdf.Stream(pdf, buf.getvalue())
        bg_img.Type, bg_img.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
        bg_img.Width, bg_img.Height = bg.shape[1], bg.shape[0]
        bg_img.ColorSpace, bg_img.BitsPerComponent, bg_img.Filter = pikepdf.Name.DeviceRGB, 8, pikepdf.Name.DCTDecode
        # 蒙版：ImageMask 的 0 表示涂色，所以墨迹处为 0
        bits = np.packbits(~ink, axis=1)
        mask = pikepdf.Stream(pdf, zlib.compress(bits.tobytes()))
        mask.Type, mask.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
        mask.Width, mask.Height = ink.shape[1], ink.shape[0]
        mask.ImageMask, mask.BitsPerComponent, mask.Filter = True, 1, pikepdf.Name.FlateDecode
        content = f"q {w_pt:.2f} 0 0 {h_pt:.2f} 0 0 cm /Bg Do Q q 0 g {w_pt:.2f} 0 0 {h_pt:.2f} 0 0 cm /Fg Do Q".encode()
        page = pikepdf.Dictionary(
            Type=pikepdf.Name.Page,
            MediaBox=[0, 0, w_pt, h_pt],
            Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Bg=bg_img, Fg=mask)),
            Contents=pikepdf.Stream(pdf, content),
        )
        pdf.pages.append(pikepdf.Page(page))
    pdf.save(str(path))
