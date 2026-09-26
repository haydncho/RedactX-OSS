"""病案表单版面。坐标单位为磅（pt），左上角为原点。

每个元素可带标准答案 truth=(实体类型, 角色)：角色 redact 表示必须遮盖，keep 表示必须保留（日期、诊断、检验结果等）。
手写元素（hand=True）在“系统导出”版本里按印刷字输出，在“扫描件”版本里用手写体画在底图上；
esign=True 的手写元素在系统导出版本里是电子签名小图。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import timedelta

import cv2
import numpy as np
from PIL import Image, ImageDraw

from . import fonts
from .fakes import LAB_ITEMS, Case, fmt_date

A4 = (595.3, 841.9)
Truth = tuple[str, str]


@dataclass
class El:
    kind: str  # text | hand | line | image | seal
    x: float
    y: float
    text: str = ""
    size: float = 10.5
    bold: bool = False
    w: float = 0.0
    h: float = 0.0
    truth: Truth | None = None
    esign: bool = False
    image: Image.Image | None = None


@dataclass
class Page:
    title: str
    w: float = A4[0]
    h: float = A4[1]
    els: list[El] = field(default_factory=list)

    # ---------- 基本元素 ----------
    def text(self, x, y, s, size=10.5, bold=False, truth=None) -> float:
        self.els.append(El("text", x, y, s, size, bold, truth=truth))
        return x + measure(s, size, bold)

    def hand(self, x, y, s, size=10.5, truth=None, esign=False) -> float:
        self.els.append(El("hand", x, y, s, size, truth=truth, esign=esign))
        # 手写体每个字（含数字）按固定字距排开，再加上扫描渲染时的随机右移
        return x + len(s) * size * 1.3 + size * 0.8

    def field(self, x, y, label, value, size=10.5, truth=None, hand=False, esign=False, sep="", gap=5.0) -> float:
        x = self.text(x, y, label + sep, size) + gap
        return (self.hand if hand else self.text)(x, y, value, size, truth=truth, **({"esign": esign} if hand else {}))

    def line(self, x0, y0, x1, y1, width=0.6):
        self.els.append(El("line", x0, y0, w=x1, h=y1, size=width))

    def image(self, x, y, w, h, img, truth=None):
        self.els.append(El("image", x, y, w=w, h=h, image=img, truth=truth))

    def seal(self, cx, cy, r, text, truth=("SEAL", "redact")):
        self.els.append(El("seal", cx - r, cy - r, text, w=2 * r, h=2 * r, truth=truth))

    def flow(self, x, y, width, segments, size=10.5, leading=1.9) -> float:
        """段落排版：segments 为 [(文字, truth)]，按字换行，实体跨行时拆成两段。"""
        cx = x
        for s, truth in segments:
            buf = ""
            for ch in s:
                cw = measure(ch, size)
                if cx + measure(buf, size) + cw > x + width:
                    if buf:
                        self.text(cx, y, buf, size, truth=truth)
                    y += size * leading
                    cx, buf = x, ""
                buf += ch
            if buf:
                cx = self.text(cx, y, buf, size, truth=truth)
        return y + size * leading


def measure(s: str, size: float, bold: bool = False) -> float:
    path, idx = fonts.bold_font() if bold else fonts.print_font()
    return fonts.pil_font(path, idx, int(size * 8)).getlength(s) / 8


# ---------- 图片素材 ----------

def logo(hospital: str) -> Image.Image:
    """医院 Logo：圆形徽标加两个字，蓝色。同一文档里每页字节完全相同（跨页重复 -> Logo）。"""
    s = 240
    im = Image.new("RGB", (s, s), "white")
    d = ImageDraw.Draw(im)
    blue = (20, 70, 150)
    d.ellipse((8, 8, s - 8, s - 8), outline=blue, width=14)
    d.rectangle((s / 2 - 16, 50, s / 2 + 16, s - 90), fill=blue)
    d.rectangle((60, s / 2 - 46, s - 60, s / 2 - 14), fill=blue)
    path, idx = fonts.bold_font()
    f = fonts.pil_font(path, idx, 44)
    t = hospital[:2]
    d.text((s / 2, s - 62), t, font=f, fill=blue, anchor="mm")
    return im


def qrcode(payload: str) -> Image.Image:
    enc = cv2.QRCodeEncoder.create()
    q = enc.encode(payload)
    q = cv2.resize(q, (q.shape[1] * 8, q.shape[0] * 8), interpolation=cv2.INTER_NEAREST)
    q = cv2.copyMakeBorder(q, 24, 24, 24, 24, cv2.BORDER_CONSTANT, value=255)
    return Image.fromarray(q).convert("RGB")


def photo(rng: random.Random, w=400, h=300) -> Image.Image:
    """内镜样照片：暗角加偏红的黏膜纹理。必须保留，不能被当成印章。"""
    nrng = np.random.default_rng(rng.randint(0, 2**31))
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.hypot((xx - w / 2) / (w / 2), (yy - h / 2) / (h / 2))
    base = np.clip(1.15 - r, 0, 1)
    tex = cv2.GaussianBlur(nrng.random((h, w)).astype(np.float32), (0, 0), 9)
    tex = (tex - tex.min()) / (np.ptp(tex) + 1e-6)
    img = np.stack([200 * base + 50 * tex, 80 * base + 60 * tex, 70 * base + 40 * tex], -1)
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))


def seal_rgba(text: str, px: int = 360, color=(212, 32, 44, 235)) -> Image.Image:
    """圆形印章：外圈、环形文字、中央五角星。默认红色。"""
    im = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    red = color
    c = px / 2
    d.ellipse((6, 6, px - 6, px - 6), outline=red, width=int(px * 0.035))
    # 五角星
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rr = px * (0.13 if i % 2 == 0 else 0.055)
        pts.append((c + rr * math.cos(ang), c + rr * math.sin(ang)))
    d.polygon(pts, fill=red)
    path, idx = fonts.bold_font()
    f = fonts.pil_font(path, idx, int(px * 0.12))
    n = len(text)
    span = min(240, 22 * n)
    for i, ch in enumerate(text):
        ang = math.radians(-90 - span / 2 + span * (i + 0.5) / n)
        glyph = Image.new("RGBA", (int(px * 0.16),) * 2, (0, 0, 0, 0))
        ImageDraw.Draw(glyph).text((glyph.width / 2, glyph.height / 2), ch, font=f, fill=red, anchor="mm")
        glyph = glyph.rotate(-math.degrees(ang) - 90, resample=Image.BICUBIC)
        gx = c + px * 0.34 * math.cos(ang) - glyph.width / 2
        gy = c + px * 0.34 * math.sin(ang) - glyph.height / 2
        im.alpha_composite(glyph, (int(gx), int(gy)))
    return im


# ---------- 表单 ----------

def _header(p: Page, c: Case, title: str, logo_img: Image.Image) -> None:
    p.image(40, 28, 40, 40, logo_img, truth=("LOGO", "redact"))
    p.text(88, 36, c.hospital, 16, bold=True, truth=("ORG", "redact"))
    p.text((p.w - measure(title, 17, True)) / 2, 76, title, 17, bold=True)


def _footer(p: Page, c: Case, no: int) -> None:
    y = p.h - 40
    p.line(40, y - 6, p.w - 40, y - 6, 0.4)
    x = p.text(40, y, c.hospital, 8, truth=("ORG", "redact")) + 10
    x = p.text(x, y, "地址：", 8)
    x = p.text(x, y, c.hospital_addr, 8, truth=("ADDRESS", "redact")) + 10
    x = p.text(x, y, "电话：", 8)
    p.text(x, y, c.hospital_tel, 8, truth=("PHONE", "redact"))
    p.text(p.w - 70, y, f"第 {no} 页", 8)


def front_page(c: Case, logo_img) -> Page:
    p = Page("病案首页")
    _header(p, c, "住院病案首页", logo_img)
    y = 110
    p.field(40, y, "医疗机构", c.hospital, 10, truth=("ORG", "redact"))
    p.field(400, y, "病案号", c.case_no, 10, truth=("MEDICAL_ID", "redact"), sep="：", gap=1)
    rows = [
        [("姓名", c.patient, ("PERSON", "redact"), True, 0), ("性别", c.sex, ("SEX", "keep"), False, 150),
         ("出生日期", fmt_date(c.birth), ("DATE", "keep"), False, 250), ("年龄", f"{c.age}岁", ("AGE", "keep"), False, 440)],
        [("身份证号", c.id_card, ("ID_CARD", "redact"), False, 0), ("职业", "职员", None, False, 300)],
        [("现住址", c.address, ("ADDRESS", "redact"), False, 0)],
        [("电话", c.phone, ("PHONE", "redact"), False, 0), ("工作单位", c.employer, ("ORG", "redact"), False, 200)],
        [("联系人姓名", c.contact, ("PERSON", "redact"), True, 0), ("关系", "配偶", None, False, 170),
         ("联系人电话", c.contact_phone, ("PHONE", "redact"), False, 280)],
        [("入院日期", fmt_date(c.admit), ("DATE", "keep"), False, 0), ("入院科别", c.dept, None, False, 200),
         ("住院号", c.inpatient_no, ("MEDICAL_ID", "redact"), False, 340)],
        [("出院日期", fmt_date(c.discharge), ("DATE", "keep"), False, 0), ("出院科别", c.dept, None, False, 200),
         ("实际住院", f"{(c.discharge - c.admit).days}天", None, False, 340)],
        [("出院诊断", c.diagnosis[0], ("DIAGNOSIS", "keep"), False, 0), ("疾病编码", c.diagnosis[1], ("DIAGNOSIS", "keep"), False, 330)],
        [("科主任", c.staff["科主任"], ("STAFF", "redact"), True, 0), ("主任医师", c.staff["主任医师"], ("STAFF", "redact"), True, 260)],
        [("主治医师", c.staff["主治医师"], ("STAFF", "redact"), True, 0), ("住院医师", c.staff["住院医师"], ("STAFF", "redact"), True, 260)],
        [("责任护士", c.staff["责任护士"], ("STAFF", "redact"), True, 0), ("编码员", c.staff["编码员"], ("STAFF", "redact"), False, 260)],
        [("住院费用（元）：总费用", c.fee_total, ("FEE", "keep"), False, 0)],
    ]
    y = 140
    p.line(40, y - 8, p.w - 40, y - 8)
    for row in rows:
        for label, value, truth, hand, dx in row:
            p.field(46 + dx, y, label, value, 10.5, truth=truth, hand=hand)
        y += 30
        p.line(40, y - 10, p.w - 40, y - 10)
    p.line(40, 132, 40, y - 10)
    p.line(p.w - 40, 132, p.w - 40, y - 10)
    _footer(p, c, 1)
    return p


def lab_report(c: Case, logo_img, rng: random.Random) -> Page:
    p = Page("检验报告单")
    _header(p, c, "检验报告单", logo_img)
    p.image(p.w - 100, 24, 60, 60, qrcode(f"RX-LAB-{c.lab_no}"), truth=("QRCODE", "redact"))
    y = 112
    p.field(40, y, "姓名", c.patient, truth=("PERSON", "redact"), sep="：", gap=1)
    p.field(170, y, "性别", c.sex, truth=("SEX", "keep"), sep="：", gap=1)
    p.field(260, y, "年龄", f"{c.age}岁", truth=("AGE", "keep"), sep="：", gap=1)
    p.field(380, y, "病案号", c.case_no, truth=("MEDICAL_ID", "redact"), sep="：", gap=1)
    y += 22
    p.field(40, y, "科室", c.dept, sep="：", gap=1)
    p.field(170, y, "床号", c.bed, truth=("MEDICAL_ID", "redact"), sep="：", gap=1)
    p.field(260, y, "标本号", c.lab_no, truth=("MEDICAL_ID", "redact"), sep="：", gap=1)
    p.field(420, y, "标本类型", "血清", sep="：", gap=1)
    y += 26
    p.line(40, y - 6, p.w - 40, y - 6, 1.0)
    cols = [46, 190, 270, 360, 450]
    for x, h in zip(cols, ["项目", "代号", "结果", "单位", "参考范围"]):
        p.text(x, y, h, 10.5, bold=True)
    y += 22
    p.line(40, y - 6, p.w - 40, y - 6, 0.5)
    for name, code, unit, lo, hi in LAB_ITEMS:
        val = round(rng.uniform(lo * 0.8, hi * 1.2), 1)
        flag = " ↑" if val > hi else (" ↓" if val < lo else "")
        p.text(cols[0], y, name)
        p.text(cols[1], y, code)
        p.text(cols[2], y, f"{val}{flag}", truth=("LAB_RESULT", "keep"))
        p.text(cols[3], y, unit)
        p.text(cols[4], y, f"{lo}-{hi}")
        y += 22
    p.line(40, y - 6, p.w - 40, y - 6, 1.0)
    y += 16
    p.field(46, y, "送检日期", fmt_date(c.admit), truth=("DATE", "keep"), sep="：", gap=1)
    p.field(300, y, "报告日期", fmt_date(c.admit), truth=("DATE", "keep"), sep="：", gap=1)
    y += 34
    p.field(46, y, "检验者", c.staff["检验者"], truth=("SIGNATURE", "redact"), hand=True, esign=True, sep="：", gap=4)
    p.field(300, y, "审核者", c.staff["审核者"], truth=("SIGNATURE", "redact"), hand=True, esign=True, sep="：", gap=4)
    p.seal(470, y + 30, 42, c.hospital + "检验专用章")
    p.text(46, y + 60, "本报告仅对所检标本负责。", 9)
    _footer(p, c, 2)
    return p


def progress_note(c: Case, logo_img) -> Page:
    p = Page("病程记录")
    _header(p, c, "病程记录", logo_img)
    y = 112
    p.field(40, y, "姓名", c.patient, truth=("PERSON", "redact"), sep="：", gap=1)
    p.field(170, y, "科别", c.dept, sep="：", gap=1)
    p.field(300, y, "床号", c.bed, truth=("MEDICAL_ID", "redact"), sep="：", gap=1)
    p.field(390, y, "住院号", c.inpatient_no, truth=("MEDICAL_ID", "redact"), sep="：", gap=1)
    p.line(40, y + 18, p.w - 40, y + 18)
    y += 36
    P, D = ("PERSON", "redact"), ("DATE", "keep")
    R = ("PERSON_PROSE", "redact")  # 只在正文出现的人名：规则与锚定都覆盖不到，需要 NER
    paras = [
        [(fmt_date(c.admit), D), (" 10:30 首次病程记录", None)],
        [("患者", None), (c.patient, P), (f"，{c.sex}，{c.age}岁，因“多饮、多尿伴体重下降3月”于", None), (fmt_date(c.admit), D),
         ("入院。由家属", None), (c.relative_in_prose, R), ("（患者之女）陪同就诊，", None), (c.relative_in_prose, R),
         ("表示理解病情并同意治疗方案，联系电话", None), (c.contact_phone, ("PHONE", "redact")),
         ("。既往体健，否认肝炎、结核等传染病史，否认药物过敏史。", None)],
        [("查体：T 36.5℃，P 78次/分，R 18次/分，BP 132/84mmHg。神清，精神可，双肺呼吸音清，未闻及干湿性啰音，心律齐。", None)],
        [("初步诊断：", None), (c.diagnosis[0], ("DIAGNOSIS", "keep")), ("（", None), (c.diagnosis[1], ("DIAGNOSIS", "keep")), ("）。", None)],
        [("诊疗计划：完善血常规、肝肾功能、糖化血红蛋白等检查，", None), (c.staff["主治医师"], ("STAFF", "redact")),
         ("主治医师查房后调整治疗方案，密切观察病情变化。", None)],
        [(fmt_date(c.discharge), D), (" 出院记录：患者病情好转，今日出院，嘱门诊随访，不适随诊。", None)],
    ]
    for segs in paras:
        y = p.flow(52, y, p.w - 104, [("　　", None)] + segs) + 4
    y += 20
    p.field(330, y, "医师签名", c.staff["住院医师"], truth=("SIGNATURE", "redact"), hand=True, esign=True, sep="：", gap=4)
    _footer(p, c, 3)
    return p


def endoscopy(c: Case, logo_img, rng: random.Random) -> Page:
    p = Page("内镜报告")
    _header(p, c, "电子胃镜检查报告", logo_img)
    y = 112
    p.field(40, y, "姓名", c.patient, truth=("PERSON", "redact"), sep="：", gap=1)
    p.field(170, y, "性别", c.sex, truth=("SEX", "keep"), sep="：", gap=1)
    p.field(260, y, "年龄", f"{c.age}岁", truth=("AGE", "keep"), sep="：", gap=1)
    p.field(380, y, "住院号", c.inpatient_no, truth=("MEDICAL_ID", "redact"), sep="：", gap=1)
    p.line(40, y + 18, p.w - 40, y + 18)
    y += 34
    p.image(60, y, 220, 165, photo(rng), truth=("PHOTO", "keep"))
    p.image(315, y, 220, 165, photo(rng), truth=("PHOTO", "keep"))
    y += 190
    y = p.flow(52, y, p.w - 104, [("检查所见：食管黏膜光滑，血管纹理清晰。贲门开闭好。胃底黏液湖清，胃体黏膜光滑，胃窦黏膜红白相间，以红为主，"
                                   "散在点状糜烂。幽门圆，开闭好。十二指肠球部及降部未见异常。", None)])
    y += 8
    p.text(52, y, "内镜诊断：")
    p.text(52 + measure("内镜诊断：", 10.5), y, "慢性非萎缩性胃炎伴糜烂", truth=("DIAGNOSIS", "keep"))
    y += 50
    p.field(52, y, "报告医师", c.staff["主任医师"], truth=("SIGNATURE", "redact"), hand=True, esign=True, sep="：", gap=4)
    p.field(330, y, "报告日期", fmt_date(c.admit), truth=("DATE", "keep"), sep="：", gap=1)
    _footer(p, c, 4)
    return p


def daily_note(c: Case, logo_img) -> Page:
    """日常病程记录（手写）：只用于扫描件。手写的日期、诊断必须保留；各种写法的医师签名必须遮盖。"""
    p = Page("日常病程记录")
    _header(p, c, "日常病程记录", logo_img)
    y = 112
    p.field(40, y, "姓名", c.patient, truth=("PERSON", "redact"), sep="：", gap=1)
    p.field(170, y, "科别", c.dept, sep="：", gap=1)
    p.field(300, y, "床号", c.bed, truth=("MEDICAL_ID", "redact"), sep="：", gap=1)
    p.field(390, y, "住院号", c.inpatient_no, truth=("MEDICAL_ID", "redact"), sep="：", gap=1)
    p.line(40, y + 18, p.w - 40, y + 18)
    D, K, S = ("DATE", "keep"), ("DIAGNOSIS", "keep"), ("SIGNATURE", "redact")
    d1, d2, d3 = (fmt_date(c.admit + timedelta(days=k)) for k in (1, 2, 3))
    y = 150
    # 1. 手写日期 + 查房记录；“医师：”不在签名标签词表里
    p.hand(52, y, d1.replace("年", ".").replace("月", ".").rstrip("日"), truth=D)
    p.hand(190, y, "主治医师查房记录")
    y += 30
    p.hand(52, y, "患者一般情况可，血糖控制平稳，继续当前治疗。")
    y += 30
    p.text(52, y, "诊断：")
    p.hand(52 + measure("诊断：", 10.5) + 4, y, c.diagnosis[0], truth=K)
    y += 30
    p.field(330, y, "医师", c.staff["主治医师"], truth=S, hand=True, esign=True, sep="：", gap=4)
    y += 44
    # 2. “医师签名：”后面先是打印日期，再签名
    p.hand(52, y, d2.replace("年", ".").replace("月", ".").rstrip("日"), truth=D)
    p.hand(190, y, "今日复查血常规未见明显异常。")
    y += 30
    x = p.text(300, y, "医师签名：")
    x = p.text(x + 4, y, d2, truth=D)
    p.hand(x + 10, y, c.staff["住院医师"], truth=S, esign=True)
    y += 44
    # 3. 空着的签名栏，正下方手写补充诊断
    p.text(52, y, "医师签名：")
    y += 26
    p.text(52, y, "补充诊断：")
    p.hand(52 + measure("补充诊断：", 10.5) + 4, y, "高脂血症", truth=K)
    y += 44
    # 4. 手写日期后面直接签名，没有标签
    p.hand(52, y, d3.replace("年", ".").replace("月", ".").rstrip("日"), truth=D)
    p.hand(190, y, "上级医师查房，同意目前诊疗方案。")
    y += 30
    x = p.hand(330, y, d3.replace("年", ".").replace("月", ".").rstrip("日"), truth=D)
    p.hand(x + 6, y, c.staff["科主任"], truth=S, esign=True)
    y += 44
    # 5. “上级医师签名：”
    p.field(300, y, "上级医师签名", c.staff["主任医师"], truth=S, hand=True, esign=True, sep="：", gap=4)
    y += 44
    # 6. “患者”“医生”后面不带冒号、紧跟手写姓名；下一行印刷正文里的“患者”“医生”不应触发
    p.field(52, y, "患者", c.patient, truth=("PERSON", "redact"), hand=True, gap=2)
    p.field(300, y, "医生", c.staff["住院医师"], truth=("STAFF", "redact"), hand=True, gap=2)
    y += 30
    p.text(52, y, "患者病情平稳，医生建议明日出院。")
    _footer(p, c, 5)
    return p


def consent(c: Case, logo_img) -> Page:
    """知情同意书（手写签署）：只用于扫描件。标签与手写内容隔得较远、后面还跟着打印文字；句末的“签名”后接手写签名。"""
    p = Page("知情同意书")
    _header(p, c, "手术知情同意书", logo_img)
    y = 112
    p.field(40, y, "姓名", c.patient, truth=("PERSON", "redact"), sep="：", gap=1)
    p.field(300, y, "住院号", c.inpatient_no, truth=("MEDICAL_ID", "redact"), sep="：", gap=1)
    p.line(40, y + 18, p.w - 40, y + 18)
    y += 36
    y = p.flow(52, y, p.w - 104, [("　　医师已向我详细说明手术的目的、方式、风险及可能出现的并发症，我已充分理解，同意接受手术治疗。"
                                   "手术中如出现意外情况，同意医师根据病情采取必要的处理措施。", None)]) + 16
    # 1. “患者”与手写姓名隔得较远，姓名后面还有打印文字
    p.text(52, y, "患者")
    p.hand(52 + 60, y, c.patient, truth=("PERSON", "redact"))
    p.text(300, y, "已阅读并理解上述内容。")
    y += 40
    # 2. 句末的“签名”，后接手写签名
    x = p.text(52, y, "我已了解上述风险，自愿接受治疗。签名")
    p.hand(x + 8, y, c.patient, truth=("SIGNATURE", "redact"), esign=True)
    y += 44
    # 3. “签名：”后面的签名写得很远
    p.text(52, y, "家属签名：")
    p.hand(52 + 150, y, c.contact, truth=("SIGNATURE", "redact"), esign=True)
    p.field(360, y, "与患者关系", "配偶", sep="：", gap=1)
    y += 44
    # 4. 医师签名与签名日期（手写日期应保留）
    p.field(52, y, "谈话医师签名", c.staff["主治医师"], truth=("SIGNATURE", "redact"), hand=True, esign=True, sep="：", gap=6)
    p.text(330, y, "签名日期：")
    p.hand(330 + measure("签名日期：", 10.5) + 4, y, fmt_date(c.admit).replace("年", ".").replace("月", ".").rstrip("日"), truth=("DATE", "keep"))
    _footer(p, c, 6)
    return p


def nursing_record(c: Case, logo_img) -> Page:
    """护理记录单（表格，手写）：只用于加难的扫描形态。表格格子里的护士签名、一格两人签名、
    手写护理记录里只出现一次的家属姓名、盖在护士长签名上的科室章。"""
    rng = random.Random("nurse:" + c.patient)  # 独立的随机源：不改变 c.rng，前 6 页的内容保持不变
    used = {c.patient, c.contact, c.relative_in_prose, *c.staff.values()}
    nurses = [_fake_name(rng, used) for _ in range(4)]
    family = _fake_name(rng, used)
    p = Page("护理记录单")
    _header(p, c, "一般护理记录单", logo_img)
    y = 112
    p.field(40, y, "姓名", c.patient, truth=("PERSON", "redact"), sep="：", gap=1)
    p.field(170, y, "科别", c.dept, sep="：", gap=1)
    p.field(300, y, "床号", c.bed, truth=("MEDICAL_ID", "redact"), sep="：", gap=1)
    p.field(390, y, "住院号", c.inpatient_no, truth=("MEDICAL_ID", "redact"), sep="：", gap=1)
    # 表格：日期 | 时间 | 病情观察及护理措施 | 护士签名
    cols = [40, 100, 180, 440, p.w - 40]
    top, rh = 140, 40
    rows = [
        ("08:00", "体温36.8℃，精神可，遵医嘱予胰岛素皮下注射。", [nurses[0]]),
        ("10:30", f"患者女儿{family}陪护，已告知防跌倒注意事项。", [nurses[0]]),
        ("14:00", "静脉输液通畅，穿刺处无红肿，双人核对用药。", [nurses[1], nurses[2]]),
        ("16:30", "测指尖血糖8.6mmol/L，已报告医师。", [nurses[1]]),
        ("20:00", "患者夜间睡眠尚可，无特殊不适。", [nurses[3]]),
    ]
    for k, head in enumerate(["日期", "时间", "病情观察及护理措施", "护士签名"]):
        p.text(cols[k] + 6, top + 12, head, 10, bold=True)
    for k in range(len(rows) + 2):
        p.line(cols[0], top + k * rh, cols[-1], top + k * rh, 0.5)
    for x in cols:
        p.line(x, top, x, top + (len(rows) + 1) * rh, 0.5)
    day = fmt_date(c.admit + timedelta(days=1)).replace("年", ".").replace("月", ".").rstrip("日")
    for r, (tm, note, signers) in enumerate(rows):
        y = top + (r + 1) * rh + 13
        if r == 0:
            p.hand(cols[0] + 4, y, day[5:], size=9, truth=("DATE", "keep"))
        p.hand(cols[1] + 4, y, tm, size=9, truth=("DATE", "keep"))
        if family in note:
            a = note.index(family)
            x = p.hand(cols[2] + 4, y, note[:a], size=8)
            x = p.hand(x - 6, y, family, size=8, truth=("PERSON_PROSE", "redact"))
            p.hand(x - 6, y, note[a + len(family):], size=8)
        else:
            p.hand(cols[2] + 4, y, note, size=8)
        # 双人核对：两位护士签在同一格，中间用“/”隔开
        p.hand(cols[3] + 4, y, "/".join(signers), size=9, truth=("SIGNATURE", "redact"), esign=True)
    y = top + (len(rows) + 1) * rh + 40
    # 护士长签名，上面压着科室护理单元的章
    x = p.field(300, y, "护士长签名", _fake_name(rng, used), truth=("SIGNATURE", "redact"), hand=True, esign=True, sep="：", gap=4)
    p.seal(x - 20, y + 4, 34, f"{c.dept}护理单元")
    _footer(p, c, 7)
    return p


def registration(c: Case, logo_img) -> Page:
    """患者信息登记表（第 8 页）：各类证件号与联系方式的多种写法，用于检验规则与字段锚定的覆盖面。
    少数民族姓名、复姓、英文姓名；护照、港澳通行证、军官证、医保卡号；带空格的银行卡号、带“转”分机的座机、
    +86 手机、邮箱、微信号、带“·”的车牌与新能源车牌。"""
    rng = random.Random("reg:" + c.patient)  # 独立的随机源：不改变其他页
    R = "redact"
    passport = rng.choice("EGP") + f"{rng.randint(10000000, 99999999)}"
    hk = "C" + f"{rng.randint(10000000, 99999999)}"
    mil = f"军字第{rng.randint(1000000, 9999999)}号"
    ins = f"{rng.randint(10, 99)}{rng.randint(100000000, 999999999)}"
    body = "6222" + "".join(str(rng.randint(0, 9)) for _ in range(14))  # 真实卡号满足 Luhn 校验
    digits = body + _luhn_digit(body)
    bank = " ".join(digits[k : k + 4] for k in range(0, 16, 4)) + " " + digits[16:]
    mobile = f"+86 13{rng.randint(0, 9)} {rng.randint(1000, 9999)} {rng.randint(1000, 9999)}"
    tel = f"0{rng.randint(510, 799)}-{rng.randint(1000000, 9999999)}转{rng.randint(100, 9999)}"
    email = rng.choice(["li.wei", "zhang_san88", "wang.fang2020"]) + "@" + rng.choice(["example.com", "mail.example.cn"])
    wechat = "wxid_" + "".join(rng.choice("abcdefghjkmnpqrstuvwxyz0123456789") for _ in range(10))
    plate = rng.choice("浙粤苏京沪") + rng.choice("ABCDE") + "·" + f"{rng.randint(10000, 99999)}"
    ev_plate = rng.choice("浙粤苏京沪") + rng.choice("ABCDE") + "D" + f"{rng.randint(10000, 99999)}"
    minority = rng.choice(["阿依古丽·买买提", "迪丽娜尔·阿不都", "木合塔尔·吐尔逊"])
    compound = rng.choice(["欧阳", "司马", "上官", "诸葛"]) + rng.choice(["晓明", "文静", "志远"])
    english = rng.choice(["Li Wei", "Zhang Min", "Wang Fang"])
    p = Page("患者信息登记表")
    _header(p, c, "患者信息登记表", logo_img)
    rows = [
        [("姓名", minority, ("PERSON", R), 0), ("英文姓名", english, ("PERSON", R), 300)],
        [("证件类型", "护照", None, 0), ("护照号码", passport, ("ID_CARD", R), 300)],
        [("港澳通行证号", hk, ("ID_CARD", R), 0), ("军官证号", mil, ("ID_CARD", R), 300)],
        [("医保卡号", ins, ("MEDICAL_ID", R), 0), ("银行卡号", bank, ("BANK_CARD", R), 260)],
        [("手机", mobile, ("PHONE", R), 0), ("单位电话", tel, ("PHONE", R), 260)],
        [("电子邮箱", email, ("EMAIL", R), 0), ("微信号", wechat, ("PHONE", R), 300)],
        [("车牌号", plate, ("PLATE", R), 0), ("备用车牌", ev_plate, ("PLATE", R), 300)],
        [("紧急联系人", compound, ("PERSON", R), 0), ("紧急联系人电话", c.contact_phone, ("PHONE", R), 260)],
        [("籍贯", "新疆喀什地区疏附县", ("ADDRESS", R), 0), ("民族", "维吾尔族", None, 300)],
    ]
    y = 118
    p.line(40, y - 8, p.w - 40, y - 8)
    for row in rows:
        for label, value, truth, dx in row:
            p.field(46 + dx, y, label, value, 10.5, truth=truth, sep="：", gap=2)
        y += 30
        p.line(40, y - 10, p.w - 40, y - 10)
    y += 16
    # 正文里的证件号与联系方式（没有字段标签）
    y = p.flow(52, y, p.w - 104, [("　　患者持护照（", None), (passport, ("ID_CARD", R)), ("）办理入院，费用由家属通过银行卡", None),
                                   (bank, ("BANK_CARD", R)), ("缴纳，如有疑问请发邮件至", None), (email, ("EMAIL", R)), ("或拨打", None),
                                   (tel, ("PHONE", R)), ("。", None)])
    _footer(p, c, 8)
    return p


def _luhn_digit(body: str) -> str:
    total = 0
    for k, ch in enumerate(reversed(body)):
        d = int(ch) * (2 if k % 2 == 0 else 1)
        total += d - 9 if d > 9 else d
    return str((10 - total % 10) % 10)


def _fake_name(rng, used: set[str]) -> str:
    from .fakes import GIVEN, SURNAMES

    while True:
        n = rng.choice(SURNAMES) + "".join(rng.choice(GIVEN) for _ in range(rng.choice([1, 2, 2])))
        if n not in used:
            used.add(n)
            return n


def case_pages(c: Case) -> list[Page]:
    """前 4 页各形态通用；第 5、6 页（手写病程、手写知情同意书）只有扫描类形态会用到，
    第 7 页（护理记录单）只有加难的扫描形态会用到（见 make.VARIANTS）。"""
    lg = logo(c.hospital)
    return [front_page(c, lg), lab_report(c, lg, c.rng), progress_note(c, lg), endoscopy(c, lg, c.rng), daily_note(c, lg), consent(c, lg),
            nursing_record(c, lg), registration(c, lg)]
