"""住院全套材料（30 多页）：病历、医嘱、护理、检验检查、手术麻醉，以及费用明细、医保结算清单、收费票据。

全部为虚构数据。明细、结算清单、票据与病案首页的金额彼此一致。示例数据见 bench/samples.py。
"""

from __future__ import annotations

import random
import re
from datetime import date, timedelta
from pathlib import Path

from . import render
from .fakes import Case, fake_name, fmt_date, make_case
from .forms import Page, consent, daily_note, endoscopy, front_page, logo, measure, nursing_record, qrcode, registration

R = "redact"
P, STF, SIG = ("PERSON", R), ("STAFF", R), ("SIGNATURE", R)
MID, ORG, PH, ADDR, IDC = ("MEDICAL_ID", R), ("ORG", R), ("PHONE", R), ("ADDRESS", R), ("ID_CARD", R)
D, DX, LAB, FEE = ("DATE", "keep"), ("DIAGNOSIS", "keep"), ("LAB_RESULT", "keep"), ("FEE", "keep")


def dshort(d: date) -> str:
    return f"{d.year}-{d.month:02d}-{d.day:02d}"


# ---------- 病例设定 ----------

SCENARIOS = {
    "chole": dict(dx=("胆囊结石伴慢性胆囊炎", "K80.100"), op=("腹腔镜胆囊切除术", "51.2301"), anes="全身麻醉",
                  complaint="反复右上腹痛2年，加重3天", organ="胆囊", op_fee=("330100010", "腹腔镜胆囊切除术", 2860.0),
                  path=("（胆囊）慢性胆囊炎，胆囊结石。", "送检胆囊一枚，大小8.5×3.2×2.8cm，壁厚0.3cm，腔内见结石3枚。"),
                  us="胆囊大小约8.3×3.1cm，壁毛糙增厚约0.4cm，腔内可见数个强回声团，最大约1.2cm，后伴声影，随体位移动。",
                  us_dx="胆囊结石，胆囊炎声像图", ct="胆囊不大，壁稍厚，腔内见多发高密度结节影。肝内外胆管未见扩张。双肺纹理清晰，未见实变影。",
                  ct_dx="胆囊多发结石，胆囊炎；双肺未见明显异常"),
    "appe": dict(dx=("急性化脓性阑尾炎", "K35.800"), op=("腹腔镜阑尾切除术", "47.0100"), anes="全身麻醉",
                 complaint="转移性右下腹痛1天", organ="阑尾", op_fee=("330100007", "腹腔镜阑尾切除术", 2240.0),
                 path=("（阑尾）急性化脓性阑尾炎，伴阑尾周围炎。", "送检阑尾一条，长6.5cm，直径1.0cm，浆膜面充血，附脓苔。"),
                 us="右下腹可探及一管状低回声结构，直径约1.1cm，壁增厚，加压不可压缩，周围见少量液性暗区。",
                 us_dx="右下腹阑尾区异常声像，考虑急性阑尾炎", ct="阑尾增粗，直径约1.1cm，周围脂肪间隙模糊，可见条索影。双肺纹理清晰。",
                 ct_dx="急性阑尾炎并周围炎；双肺未见明显异常"),
}


def setup(seed: int, scen: str) -> tuple[Case, dict]:
    c = make_case(seed)
    s = SCENARIOS[scen]
    rng = random.Random(f"long:{seed}")
    c.dept = "普通外科"
    c.diagnosis = s["dx"]
    c.discharge = c.admit + timedelta(days=rng.randint(7, 9))
    used = {c.patient, c.contact, c.relative_in_prose, *c.staff.values()}
    for role in ["麻醉医师", "手术助手", "器械护士", "巡回护士", "影像医师", "超声医师", "病理医师", "药师", "收费员", "医保经办人",
                 "审核护士", "护士长"]:
        c.staff[role] = fake_name(rng, used)
    x = dict(s)
    x["rng"] = rng
    x["op_day"] = c.admit + timedelta(days=2)
    x["ins_no"] = f"{rng.choice(['33', '44', '32'])}{rng.randint(10**12, 10**13 - 1)}"  # 医保编号
    x["org_code"] = f"H{rng.randint(10**10, 10**11 - 1)}"  # 定点医疗机构代码
    x["credit"] = f"12{rng.randint(10**15, 10**16 - 1)}{rng.choice('ABCDEFGHJKLMNPQRTUWXY')}"  # 统一社会信用代码
    x["bill_code"] = f"{rng.randint(33000000, 33999999)}"
    x["bill_no"] = f"{rng.randint(10**9, 10**10 - 1)}"
    x["check"] = f"{rng.randint(100000, 999999)}"
    x["list_no"] = f"JS{c.discharge.strftime('%Y%m%d')}{rng.randint(10**6, 10**7 - 1)}"
    x["outpatient"] = f"MZ{rng.randint(10**8, 10**9 - 1)}"
    x["path_no"] = f"P{c.admit.year}-{rng.randint(10000, 99999)}"
    x["img_no"] = f"CT{rng.randint(10**7, 10**8 - 1)}"
    x["us_no"] = f"US{rng.randint(10**7, 10**8 - 1)}"
    x["rx_no"] = f"{rng.randint(10**9, 10**10 - 1)}"
    x["cert_no"] = f"ZM{c.discharge.year}{rng.randint(10000, 99999)}"
    x["receipt"] = f"YJ{rng.randint(10**8, 10**9 - 1)}"
    x["deposit"] = rng.choice([5000, 8000, 10000])
    return c, x


# ---------- 版面小工具 ----------

def header(p: Page, c: Case, title: str, lg) -> None:
    p.image(40, 28, 40, 40, lg, truth=("LOGO", R))
    p.text(88, 36, c.hospital, 16, bold=True, truth=ORG)
    p.text((p.w - measure(title, 17, True)) / 2, 76, title, 17, bold=True)


def footer(p: Page, c: Case) -> None:
    y = p.h - 40
    p.line(40, y - 6, p.w - 40, y - 6, 0.4)
    x = p.text(40, y, c.hospital, 8, truth=ORG) + 10
    x = p.text(x, y, "地址：", 8)
    x = p.text(x, y, c.hospital_addr, 8, truth=ADDR) + 10
    x = p.text(x, y, "电话：", 8)
    p.text(x, y, c.hospital_tel, 8, truth=PH)
    p.text(p.w - 70, y, "第 0 页", 8)  # 最后统一改页码


def patient_bar(p: Page, c: Case, y: float = 112, bed: bool = True) -> float:
    p.field(40, y, "姓名", c.patient, truth=P, sep="：", gap=1)
    p.field(150, y, "性别", c.sex, truth=("SEX", "keep"), sep="：", gap=1)
    p.field(215, y, "年龄", f"{c.age}岁", truth=("AGE", "keep"), sep="：", gap=1)
    p.field(290, y, "科别", c.dept, sep="：", gap=1)
    if bed:
        p.field(390, y, "床号", c.bed, truth=MID, sep="：", gap=1)
    p.field(450, y, "住院号", c.inpatient_no, 9, truth=MID, sep="：", gap=1)
    p.line(40, y + 18, p.w - 40, y + 18)
    return y + 34


def para(p: Page, y: float, segs, size=10.5, indent=True) -> float:
    return p.flow(52, y, p.w - 104, ([("　　", None)] if indent else []) + segs, size=size) + 4


def heading(p: Page, y: float, s: str) -> float:
    p.text(52, y, s, 11, bold=True)
    return y + 20


def table(p: Page, x0: float, y0: float, widths, headers, rows, rh=20.0, size=9.0, hsize=9.0) -> float:
    """表格：rows 里每格是 str 或 (str, truth) 或 ("hand", str, truth, esign)。"""
    xs = [x0]
    for w in widths:
        xs.append(xs[-1] + w)
    n = len(rows) + 1
    for k in range(n + 1):
        p.line(xs[0], y0 + k * rh, xs[-1], y0 + k * rh, 0.8 if k in (0, 1, n) else 0.4)
    for x in xs:
        p.line(x, y0, x, y0 + n * rh, 0.4)
    ty = (rh - hsize) / 2 - 1
    for x, h in zip(xs, headers):
        p.text(x + 3, y0 + ty, h, hsize, bold=True)
    for r, row in enumerate(rows):
        y = y0 + (r + 1) * rh + (rh - size) / 2 - 1
        for k, cell in enumerate(row):
            if cell is None or cell == "":
                continue
            if isinstance(cell, tuple) and cell[0] == "hand":
                _, s, truth, esign = cell
                p.hand(xs[k] + 3, y, s, size, truth=truth, esign=esign)
                continue
            s, truth = (cell, None) if isinstance(cell, str) else cell
            # 数字右对齐
            if re.fullmatch(r"[-\d.,%]+", s):
                p.text(xs[k + 1] - 3 - measure(s, size), y, s, size, truth=truth)
            else:
                p.text(xs[k] + 3, y, s, size, truth=truth)
    return y0 + n * rh


def sign_row(p: Page, y: float, items, esign=True) -> float:
    """一行若干个“标签：手写签名”。items = [(x, 标签, 姓名)]。"""
    for x, label, name in items:
        p.field(x, y, label, name, truth=SIG, hand=True, esign=esign, sep="：", gap=4)
    return y + 36


# ---------- 病历 ----------

def admission_1(c, x, lg) -> Page:
    p = Page("入院记录")
    header(p, c, "入 院 记 录", lg)
    y = patient_bar(p, c)
    rows = [
        [("姓名", c.patient, P), ("出生地", c.address[:3], ADDR)],
        [("性别", c.sex, ("SEX", "keep")), ("职业", "职员", None)],
        [("年龄", f"{c.age}岁", ("AGE", "keep")), ("婚姻", "已婚", None)],
        [("民族", "汉族", None), ("入院日期", fmt_date(c.admit) + " 09:20", D)],
        [("身份证号", c.id_card, IDC), ("记录日期", fmt_date(c.admit) + " 11:05", D)],
        [("住址", c.address, ADDR)],
        [("病史陈述者", "患者本人及家属，可靠", None)],
    ]
    for row in rows:
        for k, (label, v, t) in enumerate(row):
            p.field(52 + k * 250, y, label, v, 10, truth=t, sep="：", gap=1)
        y += 20
    y += 6
    y = heading(p, y, "主诉")
    y = para(p, y, [(x["complaint"] + "。", None)])
    y = heading(p, y, "现病史")
    y = para(p, y, [(f"患者{x['complaint'][:-2]}前无明显诱因出现腹痛，呈持续性隐痛，阵发性加剧，伴恶心，无呕吐，无畏寒发热，"
                     "无皮肤巩膜黄染。曾于", None), (c.hospital[:3] + "社区卫生服务中心", ORG),
                    ("就诊，予抗感染、解痉治疗后稍缓解。3天前症状再发加重，遂来我院门诊，门诊以“", None), (c.diagnosis[0], DX),
                    ("”收住入院。起病以来，精神、睡眠一般，食欲欠佳，大小便正常，体重无明显变化。", None)])
    y = heading(p, y, "既往史")
    y = para(p, y, [("有", None), ("2型糖尿病", DX), ("病史5年，口服二甲双胍0.5g tid，血糖控制尚可。否认高血压、冠心病史，"
                     "否认肝炎、结核等传染病史，否认手术、外伤史，否认输血史。对青霉素过敏，表现为皮疹。预防接种史随当地。", None)])
    y = heading(p, y, "个人史")
    y = para(p, y, [("生于", None), (c.address[:3], ADDR), ("，久居本地，无疫区接触史。无吸烟、饮酒嗜好。", None)])
    y = heading(p, y, "婚育史")
    y = para(p, y, [("已婚，配偶", None), (c.contact, ("PERSON_PROSE", R)), ("体健，育有1女，女儿", None),
                    (c.relative_in_prose, ("PERSON_PROSE", R)), ("体健。", None)])
    y = heading(p, y, "家族史")
    para(p, y, [("父亲有“2型糖尿病”史，母亲体健。否认家族性遗传病史。", None)])
    footer(p, c)
    return p


def admission_2(c, x, lg) -> Page:
    p = Page("入院记录（续）")
    header(p, c, "入院记录（续）", lg)
    y = patient_bar(p, c)
    y = heading(p, y, "体格检查")
    y = para(p, y, [("T 37.2℃，P 86次/分，R 19次/分，BP 128/78mmHg，身高168cm，体重67kg。神志清楚，发育正常，营养中等，自主体位，"
                     "查体合作。全身皮肤黏膜无黄染，浅表淋巴结未触及肿大。头颅无畸形，巩膜无黄染，双侧瞳孔等大等圆，对光反射灵敏。"
                     "颈软，气管居中，甲状腺无肿大。胸廓对称，双肺呼吸音清，未闻及干湿性啰音。心率86次/分，律齐，各瓣膜听诊区未闻及杂音。", None)])
    y = heading(p, y, "专科情况")
    y = para(p, y, [(f"腹平坦，未见胃肠型及蠕动波，{'右上腹' if x['organ'] == '胆囊' else '右下腹'}压痛，"
                     f"{'Murphy征阳性' if x['organ'] == '胆囊' else '麦氏点压痛及反跳痛阳性'}，无肌紧张，肝脾肋下未触及，"
                     "移动性浊音阴性，肠鸣音4次/分。", None)])
    y = heading(p, y, "辅助检查")
    y = para(p, y, [(dshort(c.admit - timedelta(days=1)), D), (" 我院门诊超声：", None), (x["us_dx"], DX),
                    ("。血常规：白细胞计数", None), ("11.2×10^9/L", LAB), ("，中性粒细胞百分比", None), ("82.5%", LAB), ("。", None)])
    y = heading(p, y, "初步诊断")
    y = para(p, y, [("1. ", None), (c.diagnosis[0], DX), ("　　", None)], indent=False)
    y = para(p, y, [("2. ", None), ("2型糖尿病", DX)], indent=False)
    y += 20
    y = sign_row(p, y, [(300, "住院医师", c.staff["住院医师"])])
    y = sign_row(p, y, [(300, "主治医师", c.staff["主治医师"])])
    p.field(300, y, "日期", fmt_date(c.admit), truth=D, sep="：", gap=1)
    footer(p, c)
    return p


def first_note(c, x, lg) -> Page:
    p = Page("首次病程记录")
    header(p, c, "首次病程记录", lg)
    y = patient_bar(p, c)
    y = para(p, y, [(fmt_date(c.admit), D), (" 11:40", None)], indent=False)
    y = heading(p, y, "病例特点")
    y = para(p, y, [("1. 患者", None), (c.patient, P), (f"，{c.sex}，{c.age}岁，因“{x['complaint']}”入院。", None)], indent=False)
    y = para(p, y, [("2. 既往有2型糖尿病病史5年，青霉素过敏史。", None)], indent=False)
    y = para(p, y, [("3. 查体：", None), (x["organ"] + "区压痛阳性，余腹无压痛。", None)], indent=False)
    y = para(p, y, [("4. 辅助检查：门诊超声提示", None), (x["us_dx"], DX), ("。", None)], indent=False)
    y = heading(p, y, "拟诊讨论")
    y = para(p, y, [("根据患者病史、体征及辅助检查，诊断", None), (c.diagnosis[0], DX),
                    ("明确。需与消化性溃疡穿孔、右侧输尿管结石、急性胰腺炎等鉴别，可完善腹部CT、尿常规、血淀粉酶等检查以排除。", None)])
    y = heading(p, y, "诊疗计划")
    y = para(p, y, [("1. 普外科护理常规，二级护理，糖尿病饮食；2. 完善血常规、生化、凝血功能、心电图、胸部CT等术前检查；"
                     "3. 监测血糖，请内分泌科会诊调整降糖方案；4. 抗感染、解痉对症治疗；5. 择期行", None), (x["op"][0], None),
                    ("。已向患者及其配偶", None), (c.contact, ("PERSON_PROSE", R)), ("交代病情，联系电话", None),
                    (c.contact_phone, PH), ("。", None)])
    y += 16
    sign_row(p, y, [(330, "医师签名", c.staff["住院医师"])])
    footer(p, c)
    return p


def senior_round(c, x, lg) -> Page:
    p = Page("上级医师查房记录")
    header(p, c, "主任医师查房记录", lg)
    y = patient_bar(p, c)
    d1 = c.admit + timedelta(days=1)
    y = para(p, y, [(fmt_date(d1), D), (" 09:00 ", None), (c.staff["主任医师"], STF), ("主任医师查房记录", None)], indent=False)
    y = para(p, y, [("患者诉腹痛较前减轻，无发热，饮食睡眠可。查体：生命体征平稳，心肺未见异常，腹软，", None),
                    (x["organ"] + "区轻压痛。", None), (c.staff["主任医师"], STF),
                    ("主任医师查看患者后指出：患者诊断明确，具备手术指征，无明显手术禁忌。术前需将空腹血糖控制在", None),
                    ("8.0mmol/L", LAB), ("以下，请内分泌科", None), (c.staff["审核护士"], ("PERSON_PROSE", R)),
                    ("医师会诊协助调整胰岛素用量。拟于", None), (fmt_date(x["op_day"]), D), ("行", None), (x["op"][0], None),
                    ("，术前完善麻醉评估。", None)])
    y += 10
    y = para(p, y, [(fmt_date(d1), D), (" 15:30 内分泌科会诊意见", None)], indent=False)
    y = para(p, y, [("停用二甲双胍，改门冬胰岛素三餐前4U、甘精胰岛素睡前10U皮下注射，监测七点血糖，根据血糖调整剂量。", None)])
    y += 16
    y = sign_row(p, y, [(52, "记录医师", c.staff["住院医师"]), (300, "审签医师", c.staff["主任医师"])])
    footer(p, c)
    return p


def preop(c, x, lg) -> Page:
    p = Page("术前小结与术前讨论")
    header(p, c, "术前小结与术前讨论记录", lg)
    y = patient_bar(p, c)
    rows = [("讨论日期", fmt_date(x["op_day"] - timedelta(days=1)), D), ("讨论地点", "普通外科示教室", None),
            ("主持人", c.staff["科主任"], STF), ("参加人员", f"{c.staff['主任医师']}、{c.staff['主治医师']}、{c.staff['住院医师']}、{c.staff['麻醉医师']}", STF)]
    for label, v, t in rows:
        p.field(52, y, label, v, truth=t, sep="：", gap=1)
        y += 20
    y += 6
    y = heading(p, y, "术前诊断")
    y = para(p, y, [(c.diagnosis[0], DX), ("；", None), ("2型糖尿病", DX)])
    y = heading(p, y, "手术指征")
    y = para(p, y, [("症状反复发作，保守治疗效果欠佳，影像学检查提示病变明确，有手术指征。", None)])
    y = heading(p, y, "拟施手术及麻醉")
    y = para(p, y, [(x["op"][0], None), ("；", None), (x["anes"], None), ("。", None)])
    y = heading(p, y, "讨论意见")
    y = para(p, y, [(c.staff["主治医师"], STF), ("主治医师：患者术前检查无明显手术禁忌，血糖已控制在目标范围。", None)])
    y = para(p, y, [(c.staff["麻醉医师"], STF), ("麻醉医师：ASA分级Ⅱ级，可耐受全麻，术中注意血糖监测。", None)])
    y = para(p, y, [(c.staff["科主任"], STF), ("主任总结：同意手术方案，术中如腹腔粘连严重或出血难以控制，及时中转开腹。"
                     "术后注意观察腹腔引流、体温及血糖变化。", None)])
    y = heading(p, y, "术前准备")
    y = para(p, y, [("术前禁食8小时、禁饮2小时，术前30分钟预防性使用抗菌药物（青霉素过敏，选用克林霉素），备皮，已签署手术及麻醉知情同意书。", None)])
    y += 10
    sign_row(p, y, [(52, "记录者", c.staff["住院医师"]), (300, "主持人", c.staff["科主任"])])
    footer(p, c)
    return p


def anes_consent(c, x, lg) -> Page:
    p = Page("麻醉知情同意书")
    header(p, c, "麻醉知情同意书", lg)
    y = patient_bar(p, c)
    y = para(p, y, [("拟行手术：", None), (x["op"][0], None), ("　拟行麻醉：", None), (x["anes"], None)], indent=False)
    y = para(p, y, [("麻醉医师已告知麻醉的必要性及可能发生的风险，包括但不限于：麻醉药物过敏或中毒；呼吸抑制、喉痉挛、支气管痉挛；"
                     "气管插管引起牙齿损伤、咽喉疼痛、声音嘶哑；反流误吸引起吸入性肺炎；心律失常、心肌缺血、心搏骤停；"
                     "术后恶心呕吐、苏醒延迟；糖尿病患者围术期血糖波动；其他难以预料的意外情况。", None)])
    y = para(p, y, [("麻醉医师将以高度的责任心实施麻醉，严密监测，尽力预防和处理上述情况。患者及家属如同意实施麻醉，请签字。", None)])
    y += 12
    x0 = p.text(52, y, "患者签名：")
    p.hand(x0 + 6, y, c.patient, truth=SIG, esign=True)
    p.text(300, y, "签名日期：")
    p.hand(300 + measure("签名日期：", 10.5) + 4, y, dshort(x["op_day"] - timedelta(days=1)).replace("-", "."), truth=D)
    y += 40
    x0 = p.text(52, y, "被委托人签名：")
    p.hand(x0 + 6, y, c.contact, truth=SIG, esign=True)
    p.field(300, y, "与患者关系", "配偶", sep="：", gap=1)
    y += 40
    p.field(52, y, "联系电话", c.contact_phone, truth=PH, sep="：", gap=1)
    y += 40
    sign_row(p, y, [(52, "麻醉医师签名", c.staff["麻醉医师"])])
    footer(p, c)
    return p


def op_record(c, x, lg) -> Page:
    p = Page("手术记录")
    header(p, c, "手 术 记 录", lg)
    y = patient_bar(p, c)
    rows = [
        [("手术日期", fmt_date(x["op_day"]), D), ("手术时间", "09:10 - 10:25", D)],
        [("术前诊断", c.diagnosis[0], DX), ("术后诊断", c.diagnosis[0], DX)],
        [("手术名称", x["op"][0], None), ("手术编码", x["op"][1], DX)],
        [("手术者", c.staff["主任医师"], STF), ("助手", f"{c.staff['主治医师']}、{c.staff['手术助手']}", STF)],
        [("麻醉方式", x["anes"], None), ("麻醉医师", c.staff["麻醉医师"], STF)],
        [("器械护士", c.staff["器械护士"], STF), ("巡回护士", c.staff["巡回护士"], STF)],
    ]
    for row in rows:
        for k, (label, v, t) in enumerate(row):
            p.field(52 + k * 250, y, label, v, 10, truth=t, sep="：", gap=1)
        y += 20
    y += 6
    y = heading(p, y, "手术经过")
    steps = [
        "患者取仰卧位，全麻成功后常规消毒铺巾。于脐下缘做10mm弧形切口，建立气腹，压力维持12mmHg，置入腹腔镜探查。",
        f"见{x['organ']}充血水肿明显，与周围大网膜轻度粘连，腹腔内少量淡黄色渗液，肝脏、胃、小肠及结肠未见明显异常。",
        "分别于剑突下及右锁骨中线肋缘下置入5mm、10mm套管，钝锐性分离粘连，解剖出相应管道及血管，确认无误后以可吸收夹夹闭并离断。",
        f"完整切除{x['organ']}，装入标本袋经脐部切口取出，送病理检查。仔细检查创面无活动性出血及渗漏，冲洗腹腔，于创面旁放置引流管一根。",
        "清点器械纱布无误，解除气腹，逐层缝合切口。手术顺利，术中出血约20ml，未输血，术毕患者安返病房。",
    ]
    for s in steps:
        y = para(p, y, [(s, None)])
    y += 8
    p.field(52, y, "术中冰冻", "未送", sep="：", gap=1)
    p.field(300, y, "标本去向", "送病理科", sep="：", gap=1)
    y += 36
    sign_row(p, y, [(52, "手术者签名", c.staff["主任医师"]), (300, "记录者签名", c.staff["主治医师"])])
    footer(p, c)
    return p


def anes_record(c, x, lg) -> Page:
    p = Page("麻醉记录单")
    header(p, c, "麻 醉 记 录 单", lg)
    y = patient_bar(p, c)
    for k, (label, v, t) in enumerate([("手术日期", fmt_date(x["op_day"]), D), ("ASA分级", "Ⅱ级", None), ("体重", "67kg", None)]):
        p.field(52 + k * 170, y, label, v, 10, truth=t, sep="：", gap=1)
    y += 20
    p.field(52, y, "麻醉方式", x["anes"] + "（气管插管）", 10, sep="：", gap=1)
    p.field(300, y, "手术名称", x["op"][0], 10, sep="：", gap=1)
    y += 26
    # 生命体征表（每 15 分钟一列）
    times = ["08:45", "09:00", "09:15", "09:30", "09:45", "10:00", "10:15", "10:30", "10:45"]
    rng = x["rng"]
    rows = []
    for item, lo, hi in [("收缩压 mmHg", 108, 132), ("舒张压 mmHg", 62, 82), ("心率 次/分", 64, 88), ("SpO2 %", 97, 100),
                         ("呼气末CO2 mmHg", 33, 40), ("体温 ℃", 36, 37), ("血糖 mmol/L", 6, 9)]:
        vals = [f"{rng.uniform(lo, hi):.1f}" if "℃" in item or "血糖" in item else str(rng.randint(lo, hi)) for _ in times]
        rows.append([item] + [(v, LAB) for v in vals])
    y = table(p, 40, y, [92] + [47] * 9, ["项目"] + times, rows, rh=20, size=8.5, hsize=8.5)
    y += 14
    y = heading(p, y, "麻醉用药")
    drugs = [("丙泊酚注射液", "120mg", "08:52"), ("舒芬太尼注射液", "20μg", "08:52"), ("罗库溴铵注射液", "40mg", "08:53"),
             ("瑞芬太尼（泵注）", "0.1μg/kg/min", "09:00"), ("七氟烷（吸入）", "1.5%-2%", "09:00"), ("昂丹司琼注射液", "8mg", "10:20"),
             ("舒更葡糖钠注射液", "200mg", "10:28")]
    y = table(p, 40, y, [200, 150, 165], ["药名", "剂量", "时间"], [[a, b, (t, D)] for a, b, t in drugs], rh=19, size=9)
    y += 14
    p.field(52, y, "输液量", "晶体液 800ml", sep="：", gap=1)
    p.field(220, y, "出血量", "20ml", sep="：", gap=1)
    p.field(380, y, "尿量", "150ml", sep="：", gap=1)
    y += 22
    p.field(52, y, "麻醉总结", "麻醉平稳，术毕10:35拔除气管导管，Steward评分6分，送回病房。", 10, sep="：", gap=1)
    y += 36
    sign_row(p, y, [(52, "麻醉医师", c.staff["麻醉医师"]), (300, "巡回护士", c.staff["巡回护士"])])
    footer(p, c)
    return p


def safety_check(c, x, lg) -> Page:
    p = Page("手术安全核查表")
    header(p, c, "手术安全核查表", lg)
    y = patient_bar(p, c)
    p.field(52, y, "手术日期", fmt_date(x["op_day"]), truth=D, sep="：", gap=1)
    p.field(250, y, "手术名称", x["op"][0], sep="：", gap=1)
    y += 26
    items = [
        ("麻醉实施前", ["患者身份核对：姓名、性别、年龄、病案号 ☑", "手术方式确认 ☑　手术部位与标识正确 ☑", "知情同意书签署 ☑　麻醉安全检查完成 ☑",
                   "皮肤是否完整 ☑　术野皮肤准备正确 ☑", "静脉通道建立完成 ☑　患者过敏史：青霉素", "术前备血：否"]),
        ("手术开始前", ["患者身份再次核对 ☑", "手术方式、部位再次确认 ☑", "手术、麻醉风险预警：手术医师 ☑　麻醉医师 ☑　手术护士 ☑",
                   "手术物品灭菌合格 ☑　仪器设备正常 ☑", "术前术中特殊用药情况：克林霉素0.6g静滴"]),
        ("患者离室前", ["实际手术方式确认 ☑　手术用药、输血核查 ☑", "手术器械、敷料清点正确 ☑", "手术标本确认 ☑　皮肤完整 ☑",
                   "各种管路：引流管 ☑　尿管 ☐", "患者去向：恢复室 ☐　病房 ☑　ICU ☐"]),
    ]
    widths = [(p.w - 80) / 3] * 3
    xs = [40, 40 + widths[0], 40 + 2 * widths[0], p.w - 40]
    top = y
    for k, (title, lines) in enumerate(items):
        p.text(xs[k] + 6, top + 6, title, 10, bold=True)
        yy = top + 30
        for s in lines:
            yy = p.flow(xs[k] + 6, yy, widths[k] - 12, [(s, None)], size=8.5, leading=1.7) + 4
    bottom = top + 300
    for yy in (top, top + 22, bottom, bottom + 44):
        p.line(xs[0], yy, xs[-1], yy, 0.6)
    for xx in xs:
        p.line(xx, top, xx, bottom + 44, 0.6)
    for k, (role, name) in enumerate([("手术医师", c.staff["主任医师"]), ("麻醉医师", c.staff["麻醉医师"]), ("巡回护士", c.staff["巡回护士"])]):
        p.field(xs[k] + 6, bottom + 16, role, name, 9.5, truth=SIG, hand=True, esign=True, sep="：", gap=3)
    footer(p, c)
    return p


def orders(c, x, lg, long_term: bool) -> Page:
    p = Page("长期医嘱单" if long_term else "临时医嘱单")
    header(p, c, "长 期 医 嘱 单" if long_term else "临 时 医 嘱 单", lg)
    y = patient_bar(p, c)
    a, op = c.admit, x["op_day"]
    doc, nurse = c.staff["住院医师"], c.staff["责任护士"]
    if long_term:
        rows_src = [
            (a, "08:30", "普通外科护理常规"), (a, "08:30", "二级护理"), (a, "08:30", "糖尿病饮食"),
            (a, "08:35", "测指尖血糖 qid"), (a, "08:40", "注射用头孢呋辛钠 1.5g ivgtt q12h（皮试阴性）"),
            (a, "08:40", "0.9%氯化钠注射液 100ml ivgtt q12h"), (a, "08:45", "消旋山莨菪碱注射液 10mg im prn"),
            (a + timedelta(days=1), "15:40", "门冬胰岛素注射液 4U ih tid（三餐前）"), (a + timedelta(days=1), "15:40", "甘精胰岛素注射液 10U ih qn"),
            (op, "11:00", "术后医嘱：普通外科术后护理常规"), (op, "11:00", "一级护理"), (op, "11:00", "禁食、禁饮"),
            (op, "11:00", "心电监护、吸氧 3L/min"), (op, "11:05", "注射用奥美拉唑钠 40mg ivgtt qd"),
            (op, "11:05", "复方氨基酸注射液 250ml ivgtt qd"), (op, "11:10", "腹腔引流管护理"),
            (op + timedelta(days=1), "08:30", "停禁食，改流质饮食"), (op + timedelta(days=2), "08:30", "改二级护理"),
        ]
    else:
        rows_src = [
            (a, "08:50", "血常规＋CRP"), (a, "08:50", "肝功能、肾功能、电解质、血糖"), (a, "08:50", "凝血功能四项"),
            (a, "08:50", "尿常规"), (a, "08:55", "十二导联心电图"), (a, "09:00", "胸部CT平扫＋上腹部CT平扫"),
            (a, "09:00", "肝胆胰脾彩超"), (a + timedelta(days=1), "10:00", "电子胃镜检查"),
            (a + timedelta(days=1), "16:00", "明日在全麻下行" + x["op"][0]), (a + timedelta(days=1), "16:05", "术前禁食禁饮"),
            (a + timedelta(days=1), "16:05", "术区备皮"), (op, "08:20", "克林霉素磷酸酯注射液 0.6g ivgtt st"),
            (op, "11:20", "血糖监测 st"), (op, "14:00", "盐酸曲马多注射液 100mg im st"),
            (op + timedelta(days=1), "07:00", "复查血常规、电解质"), (op + timedelta(days=3), "09:00", "拔除腹腔引流管"),
            (c.discharge, "09:00", "今日出院"),
        ]
    rows = []
    for d0, tm, s in rows_src:
        stop = ("hand", dshort(d0 + timedelta(days=3))[5:], D, False) if long_term and "护理" not in s else ""
        rows.append([(dshort(d0)[5:], D), (tm, D), s, ("hand", doc, SIG, True), ("hand", nurse, SIG, True)] +
                    ([stop] if long_term else [("hand", tm, D, False)]))
    heads = ["日期", "时间", "医嘱内容", "医师签名", "护士签名", "停止日期" if long_term else "执行时间"]
    table(p, 40, y, [44, 40, 229, 72, 72, 58], heads, rows, rh=int(min(28, (p.h - 80 - y) / (len(rows) + 1))), size=8.5)
    footer(p, c)
    return p


def temperature(c, x, lg) -> Page:
    p = Page("体温单")
    header(p, c, "体 温 单", lg)
    y = patient_bar(p, c)
    days = [c.admit + timedelta(days=k) for k in range(7)]
    rng = x["rng"]
    x0, colw = 110, (p.w - 40 - 110) / 7
    p.text(46, y, "日期", 9, bold=True)
    for k, d0 in enumerate(days):
        p.text(x0 + k * colw + 8, y, dshort(d0)[5:], 9, truth=D)
    y += 16
    p.text(46, y, "住院日数", 9, bold=True)
    for k in range(7):
        p.text(x0 + k * colw + 20, y, str(k + 1), 9)
    y += 16
    p.text(46, y, "术后日数", 9, bold=True)
    for k, d0 in enumerate(days):
        dd = (d0 - x["op_day"]).days
        if dd >= 0:
            p.text(x0 + k * colw + 18, y, "手术" if dd == 0 else str(dd), 9)
    y += 18
    # 体温曲线网格：35-41℃
    top, h = y, 300
    for k in range(13):
        yy = top + k * h / 12
        p.line(x0, yy, p.w - 40, yy, 0.6 if k % 2 == 0 else 0.25)
        if k % 2 == 0:
            p.text(62, yy - 4, f"{41 - k // 2}℃", 8)
    for k in range(8):
        p.line(x0 + k * colw, top, x0 + k * colw, top + h, 0.6)
    temps = [37.2, 37.0, 37.8, 37.4, 36.9, 36.8, 36.6]
    pts = []
    for k, t in enumerate(temps):
        t = t + rng.uniform(-0.1, 0.1)
        px = x0 + k * colw + colw / 2
        py = top + (41 - t) / 6 * h
        pts.append((px, py))
        p.text(px - 3, py - 4, "×", 8)
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        p.line(ax + 1, ay, bx + 1, by, 0.9)
    y = top + h + 12
    for label, vals in [("脉搏 次/分", [86, 80, 92, 84, 78, 76, 74]), ("呼吸 次/分", [19, 18, 20, 19, 18, 18, 18]),
                        ("血压 mmHg", ["128/78", "124/76", "132/80", "126/78", "122/74", "120/76", "118/72"]),
                        ("空腹血糖", ["9.6", "8.4", "7.8", "7.2", "6.9", "6.5", "6.3"]), ("入量 ml", ["", "", "2150", "1800", "1500", "", ""]),
                        ("引流量 ml", ["", "", "60", "35", "10", "", ""]), ("大便次数", ["1", "1", "0", "0", "1", "1", "1"]),
                        ("体重 kg", ["67", "", "", "", "", "", "66"])]:
        p.text(46, y, label, 8.5, bold=True)
        for k, v in enumerate(vals):
            if v != "":
                p.text(x0 + k * colw + 10, y, str(v), 8.5, truth=LAB)
        p.line(40, y + 13, p.w - 40, y + 13, 0.3)
        y += 18
    footer(p, c)
    return p


LAB_PANELS = {
    "血常规": [("白细胞计数", "WBC", "10^9/L", 3.5, 9.5), ("中性粒细胞百分比", "NEUT%", "%", 40, 75), ("淋巴细胞百分比", "LYMPH%", "%", 20, 50),
            ("红细胞计数", "RBC", "10^12/L", 4.3, 5.8), ("血红蛋白", "HGB", "g/L", 130, 175), ("红细胞压积", "HCT", "%", 40, 50),
            ("平均红细胞体积", "MCV", "fL", 82, 100), ("血小板计数", "PLT", "10^9/L", 125, 350), ("C反应蛋白", "CRP", "mg/L", 0, 10)],
    "生化全套": [("谷丙转氨酶", "ALT", "U/L", 9, 50), ("谷草转氨酶", "AST", "U/L", 15, 40), ("总胆红素", "TBIL", "μmol/L", 0, 23),
             ("直接胆红素", "DBIL", "μmol/L", 0, 8), ("白蛋白", "ALB", "g/L", 40, 55), ("尿素", "UREA", "mmol/L", 3.1, 8.0),
             ("肌酐", "CREA", "μmol/L", 57, 111), ("葡萄糖", "GLU", "mmol/L", 3.9, 6.1), ("糖化血红蛋白", "HbA1c", "%", 4.0, 6.0),
             ("钾", "K", "mmol/L", 3.5, 5.3), ("钠", "Na", "mmol/L", 137, 147), ("氯", "Cl", "mmol/L", 99, 110), ("淀粉酶", "AMY", "U/L", 35, 135)],
    "凝血功能及尿常规": [("凝血酶原时间", "PT", "s", 9.8, 12.1), ("国际标准化比值", "INR", "", 0.8, 1.2), ("活化部分凝血活酶时间", "APTT", "s", 25, 31.3),
                 ("纤维蛋白原", "FIB", "g/L", 2.0, 4.0), ("D-二聚体", "D-D", "mg/L", 0, 0.55), ("尿比重", "SG", "", 1.003, 1.030),
                 ("尿酸碱度", "pH", "", 4.5, 8.0), ("尿葡萄糖", "GLU", "", 0, 0), ("尿蛋白", "PRO", "", 0, 0), ("尿白细胞", "LEU", "/μL", 0, 25)],
}


def lab(c, x, lg, panel: str, d0: date) -> Page:
    rng = x["rng"]
    p = Page("检验报告单")
    header(p, c, f"检验报告单（{panel}）", lg)
    lab_no = f"{rng.randint(10**7, 10**8 - 1)}"
    p.image(p.w - 100, 24, 60, 60, qrcode(f"RX-LAB-{lab_no}"), truth=("QRCODE", R))
    y = 112
    p.field(40, y, "姓名", c.patient, truth=P, sep="：", gap=1)
    p.field(150, y, "性别", c.sex, truth=("SEX", "keep"), sep="：", gap=1)
    p.field(215, y, "年龄", f"{c.age}岁", truth=("AGE", "keep"), sep="：", gap=1)
    p.field(300, y, "病案号", c.case_no, truth=MID, sep="：", gap=1)
    p.field(430, y, "床号", c.bed, truth=MID, sep="：", gap=1)
    y += 20
    p.field(40, y, "科室", c.dept, sep="：", gap=1)
    p.field(150, y, "标本号", lab_no, truth=MID, sep="：", gap=1)
    p.field(300, y, "标本类型", "尿液/血浆" if "尿" in panel else "血清", sep="：", gap=1)
    p.field(430, y, "送检医师", c.staff["住院医师"], 9.5, truth=STF, sep="：", gap=1)
    y += 26
    rows = []
    for name, code, unit, lo, hi in LAB_PANELS[panel]:
        if hi == 0:
            val, flag, ref = rng.choice(["阴性", "阴性", "弱阳性"]), "", "阴性"
        else:
            v = rng.uniform(lo * 0.85, hi * 1.25)
            nd = 3 if hi < 2 else 1
            val = f"{v:.{nd}f}"
            flag = "↑" if v > hi else ("↓" if v < lo else "")
            ref = f"{lo}-{hi}"
        rows.append([name, code, (val, LAB), flag, unit, ref])
    y = table(p, 40, y, [150, 70, 80, 30, 80, 105], ["项目", "代号", "结果", "", "单位", "参考范围"], rows, rh=22, size=9.5, hsize=10)
    y += 20
    p.field(46, y, "采集时间", f"{fmt_date(d0)} 06:30", truth=D, sep="：", gap=1)
    p.field(300, y, "报告时间", f"{fmt_date(d0)} 10:12", truth=D, sep="：", gap=1)
    y += 34
    p.field(46, y, "检验者", c.staff["检验者"], truth=SIG, hand=True, esign=True, sep="：", gap=4)
    p.field(300, y, "审核者", c.staff["审核者"], truth=SIG, hand=True, esign=True, sep="：", gap=4)
    p.seal(470, y + 30, 40, c.hospital + "检验专用章")
    p.text(46, y + 64, "本报告仅对所检标本负责。如有疑问请于报告发出后7日内与检验科联系。", 9)
    footer(p, c)
    return p


def imaging(c, x, lg, kind: str) -> Page:
    ct = kind == "CT"
    p = Page("影像检查报告")
    header(p, c, "CT 检查报告单" if ct else "超声检查报告单", lg)
    y = patient_bar(p, c)
    p.field(52, y, "检查号", x["img_no"] if ct else x["us_no"], truth=MID, sep="：", gap=1)
    p.field(250, y, "检查日期", fmt_date(c.admit), truth=D, sep="：", gap=1)
    y += 20
    p.field(52, y, "检查部位", "胸部＋上腹部平扫" if ct else "肝胆胰脾、阑尾区", sep="：", gap=1)
    p.field(250, y, "设备", "64排螺旋CT" if ct else "彩色多普勒超声诊断仪", sep="：", gap=1)
    y += 28
    if not ct:
        p.image(60, y, 220, 165, us_image(x["rng"]), truth=("PHOTO", "keep"))
        p.image(315, y, 220, 165, us_image(x["rng"]), truth=("PHOTO", "keep"))
        y += 185
    y = heading(p, y, "检查所见")
    y = para(p, y, [(x["ct"] if ct else x["us"], None)])
    if ct:
        y = para(p, y, [("肝脏形态、大小正常，肝实质密度均匀，未见异常密度影。脾脏不大，胰腺形态、密度未见异常。双肾未见异常。"
                         "腹膜后未见肿大淋巴结。纵隔居中，未见肿大淋巴结。心影不大。", None)])
    else:
        y = para(p, y, [("肝脏形态大小正常，包膜光整，实质回声均匀。胰腺显示部分未见异常。脾脏大小正常。", None)])
    y = heading(p, y, "诊断意见")
    y = para(p, y, [(x["ct_dx"] if ct else x["us_dx"], DX), ("。", None)])
    y += 26
    key = "影像医师" if ct else "超声医师"
    sign_row(p, y, [(52, "报告医师", c.staff[key]), (300, "审核医师", c.staff["主任医师"] if not ct else c.staff["审核者"])])
    p.text(52, y + 44, "本报告仅供临床参考，不作为诊断证明。", 9)
    footer(p, c)
    return p


def us_image(rng):
    """超声图样：扇形区域加斑点噪声与一个低回声区。"""
    import cv2
    import numpy as np
    from PIL import Image

    nrng = np.random.default_rng(rng.randint(0, 2**31))
    h, w = 300, 400
    yy, xx = np.mgrid[0:h, 0:w]
    fan = ((xx - w / 2) ** 2 + (yy + 40) ** 2 < (h + 20) ** 2) & (np.abs(xx - w / 2) < (yy + 40) * 0.9)
    speck = cv2.GaussianBlur(nrng.random((h, w)).astype(np.float32), (0, 0), 1.6)
    img = np.where(fan, 40 + 150 * speck, 8)
    cy, cx = rng.randint(120, 200), rng.randint(140, 260)
    img[((xx - cx) / 60) ** 2 + ((yy - cy) / 30) ** 2 < 1] *= 0.35
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).convert("RGB")


def pathology(c, x, lg) -> Page:
    p = Page("病理诊断报告")
    header(p, c, "病理诊断报告书", lg)
    y = patient_bar(p, c)
    p.field(52, y, "病理号", x["path_no"], truth=MID, sep="：", gap=1)
    p.field(250, y, "送检日期", fmt_date(x["op_day"]), truth=D, sep="：", gap=1)
    y += 20
    p.field(52, y, "送检医师", c.staff["主任医师"], truth=STF, sep="：", gap=1)
    p.field(250, y, "送检材料", x["organ"], sep="：", gap=1)
    y += 20
    p.field(52, y, "临床诊断", c.diagnosis[0], truth=DX, sep="：", gap=1)
    y += 28
    y = heading(p, y, "肉眼所见")
    y = para(p, y, [(x["path"][1], None)])
    y = heading(p, y, "镜下所见")
    y = para(p, y, [("黏膜上皮部分脱落，固有层及肌层见大量中性粒细胞、淋巴细胞及浆细胞浸润，局部可见小脓肿形成，浆膜层充血水肿。", None)])
    y = heading(p, y, "病理诊断")
    y = para(p, y, [(x["path"][0], DX)])
    y += 30
    sign_row(p, y, [(52, "诊断医师", c.staff["病理医师"]), (300, "复核医师", c.staff["审核者"])])
    p.field(52, y + 40, "报告日期", fmt_date(x["op_day"] + timedelta(days=3)), truth=D, sep="：", gap=1)
    p.seal(470, y + 60, 38, c.hospital + "病理科")
    footer(p, c)
    return p


def critical_notice(c, x, lg) -> Page:
    p = Page("病重通知书")
    header(p, c, "病重（病危）通知书", lg)
    y = patient_bar(p, c)
    y = para(p, y, [("患者家属", None), (c.contact, P), ("：", None)], indent=False)
    y = para(p, y, [("您的家属", None), (c.patient, P), ("因", None), (c.diagnosis[0], DX), ("、", None), ("2型糖尿病", DX),
                    ("于", None), (fmt_date(c.admit), D), ("入院。目前患者术后血糖波动较大，存在感染加重、酮症酸中毒等风险，"
                     "病情较重，特此告知。我们将积极救治，但仍可能出现病情恶化甚至危及生命的情况，请您理解并配合治疗。", None)])
    y += 10
    y = para(p, y, [("通知时间：", None), (fmt_date(x["op_day"]) + " 16:20", D)], indent=False)
    y += 20
    y = sign_row(p, y, [(52, "主管医师", c.staff["主治医师"])])
    y += 10
    x0 = p.text(52, y, "家属已知晓，签名：")
    p.hand(x0 + 8, y, c.contact, truth=SIG, esign=True)
    p.field(330, y, "关系", "配偶", sep="：", gap=1)
    y += 30
    p.field(52, y, "联系电话", c.contact_phone, truth=PH, sep="：", gap=1)
    y += 60
    p.text(52, y, "（本通知一式两份，一份交家属，一份存病历）", 9)
    footer(p, c)
    return p


def discharge(c, x, lg) -> Page:
    p = Page("出院记录")
    header(p, c, "出 院 记 录", lg)
    y = patient_bar(p, c)
    for k, (label, v, t) in enumerate([("入院日期", fmt_date(c.admit), D), ("出院日期", fmt_date(c.discharge), D)]):
        p.field(52 + k * 250, y, label, v, truth=t, sep="：", gap=1)
    y += 20
    p.field(52, y, "住院天数", f"{(c.discharge - c.admit).days}天", sep="：", gap=1)
    y += 26
    y = heading(p, y, "入院情况")
    y = para(p, y, [(f"因“{x['complaint']}”入院，查体：{x['organ']}区压痛阳性。门诊超声提示", None), (x["us_dx"], DX), ("。", None)])
    y = heading(p, y, "诊疗经过")
    y = para(p, y, [("入院后完善相关检查，积极控制血糖，于", None), (fmt_date(x["op_day"]), D), ("在全麻下行", None), (x["op"][0], None),
                    ("，手术顺利，术后予抗感染、抑酸、补液、胰岛素控制血糖等治疗。术后病理：", None), (x["path"][0], DX)])
    y = heading(p, y, "出院诊断")
    y = para(p, y, [("1. ", None), (c.diagnosis[0], DX), ("（", None), (c.diagnosis[1], DX), ("）；2. ", None), ("2型糖尿病", DX),
                    ("（", None), ("E11.900", DX), ("）", None)], indent=False)
    y = heading(p, y, "出院情况")
    y = para(p, y, [("患者无腹痛、发热，饮食睡眠好，切口愈合良好（甲级愈合），空腹血糖", None), ("6.3mmol/L", LAB), ("。", None)])
    y = heading(p, y, "出院医嘱")
    y = para(p, y, [("1. 低脂糖尿病饮食，注意休息，1月内避免剧烈运动；2. 出院带药见处方；3. 监测血糖，内分泌科门诊随诊；"
                     "4. 术后2周普外科门诊复查，如有腹痛、发热、切口渗液等不适及时就诊。随访电话", None), (c.hospital_tel, PH), ("。", None)])
    y += 16
    sign_row(p, y, [(52, "住院医师", c.staff["住院医师"]), (300, "主治医师", c.staff["主治医师"])])
    footer(p, c)
    return p


def certificate(c, x, lg) -> Page:
    p = Page("诊断证明书")
    header(p, c, "诊 断 证 明 书", lg)
    y = 116
    p.field(400, y, "编号", x["cert_no"], truth=MID, sep="：", gap=1)
    y += 30
    rows = [[("姓名", c.patient, P), ("性别", c.sex, ("SEX", "keep")), ("年龄", f"{c.age}岁", ("AGE", "keep"))],
            [("身份证号", c.id_card, IDC), ("住院号", c.inpatient_no, MID)],
            [("工作单位", c.employer, ORG)]]
    for row in rows:
        xx = 52
        for label, v, t in row:
            xx = p.field(xx, y, label, v, truth=t, sep="：", gap=1) + 30
        y += 26
    y += 10
    y = heading(p, y, "诊断")
    y = para(p, y, [("1. ", None), (c.diagnosis[0], DX), ("；2. ", None), ("2型糖尿病", DX)], indent=False)
    y = heading(p, y, "建议")
    y = para(p, y, [("患者于", None), (fmt_date(c.admit), D), ("至", None), (fmt_date(c.discharge), D), ("住院治疗，行", None),
                    (x["op"][0], None), ("，建议出院后休息二周（", None), (fmt_date(c.discharge + timedelta(days=1)), D), ("至", None),
                    (fmt_date(c.discharge + timedelta(days=14)), D), ("）。", None)])
    y += 40
    sign_row(p, y, [(52, "医师签名", c.staff["主治医师"])])
    p.field(300, y, "开具日期", fmt_date(c.discharge), truth=D, sep="：", gap=1)
    p.seal(440, y + 70, 44, c.hospital + "医务科")
    p.text(52, y + 140, "注：本证明涂改无效，须加盖医院医务科公章。", 9)
    footer(p, c)
    return p


def prescription(c, x, lg) -> Page:
    p = Page("处方笺")
    header(p, c, "处 方 笺（出院带药）", lg)
    y = 112
    p.field(40, y, "处方编号", x["rx_no"], truth=MID, sep="：", gap=1)
    p.field(300, y, "费别", "职工医保", sep="：", gap=1)
    y += 20
    p.field(40, y, "姓名", c.patient, truth=P, sep="：", gap=1)
    p.field(160, y, "性别", c.sex, truth=("SEX", "keep"), sep="：", gap=1)
    p.field(230, y, "年龄", f"{c.age}岁", truth=("AGE", "keep"), sep="：", gap=1)
    p.field(320, y, "门诊/住院号", c.inpatient_no, truth=MID, sep="：", gap=1)
    y += 20
    p.field(40, y, "临床诊断", f"{c.diagnosis[0]}；2型糖尿病", truth=DX, sep="：", gap=1)
    p.field(380, y, "开具日期", dshort(c.discharge), truth=D, sep="：", gap=1)
    y += 20
    p.field(40, y, "住址/电话", c.phone, truth=PH, sep="：", gap=1)
    p.line(40, y + 18, p.w - 40, y + 18)
    y += 32
    p.text(52, y, "Rp.", 16, bold=True)
    y += 30
    drugs = [("门冬胰岛素注射液", "3ml:300U×1支", "4U 三餐前皮下注射"), ("甘精胰岛素注射液", "3ml:300U×1支", "10U 睡前皮下注射"),
             ("熊去氧胆酸片", "250mg×24片", "250mg 口服 bid"), ("奥美拉唑肠溶胶囊", "20mg×14粒", "20mg 口服 qd 早餐前"),
             ("一次性使用胰岛素注射针头", "0.23mm×4mm×28支", "配合注射使用")]
    for k, (n, spec, use) in enumerate(drugs, 1):
        p.text(70, y, f"{k}. {n}", 10.5)
        p.text(300, y, spec, 10.5)
        y += 18
        p.text(90, y, "用法：" + use, 9.5)
        y += 26
    p.text(52, y, "（以下空白）", 9.5)
    y += 50
    p.line(40, y - 12, p.w - 40, y - 12)
    p.field(40, y, "药品金额", "286.40元", truth=FEE, sep="：", gap=1)
    p.field(300, y, "医师", c.staff["住院医师"], truth=SIG, hand=True, esign=True, sep="：", gap=4)
    y += 36
    p.field(40, y, "审核药师", c.staff["药师"], truth=SIG, hand=True, esign=True, sep="：", gap=4)
    p.field(300, y, "调配/核对发药", c.staff["药师"], truth=SIG, hand=True, esign=True, sep="：", gap=4)
    footer(p, c)
    return p


# ---------- 费用 ----------

def fee_items(c: Case, x) -> list[dict]:
    """逐日费用明细。cat 为结算清单的收费项目类别，ins 为医保类别（甲/乙/丙）。"""
    a, op, dc = c.admit, x["op_day"], c.discharge
    rng = x["rng"]
    items = []

    def add(d0, code, name, spec, unit, price, qty, cat, ins):
        items.append(dict(date=d0, code=code, name=name, spec=spec, unit=unit, price=price, qty=qty, amt=round(price * qty, 2),
                          cat=cat, ins=ins))

    ndays = (dc - a).days
    for k in range(ndays):
        d0 = a + timedelta(days=k)
        add(d0, "110900001", "普通病房床位费（三人间）", "", "日", 45.0, 1, "床位费", "甲")
        add(d0, "110200002", "住院诊查费", "", "日", 25.0, 1, "诊察费", "甲")
        nurse = ("121400001", "一级护理", 36.0) if op <= d0 < op + timedelta(days=2) else ("121300001", "二级护理", 24.0)
        add(d0, nurse[0], nurse[1], "", "日", nurse[2], 1, "护理费", "甲")
        add(d0, "250305006", "指尖血糖测定", "", "次", 8.0, 4, "化验费", "甲")
        if d0 < dc - timedelta(days=1):
            add(d0, "120400006", "静脉输液（第一组）", "", "组", 12.0, 1, "治疗费", "甲")
            add(d0, "120400007", "静脉输液（加组）", "", "组", 3.0, rng.randint(1, 3), "治疗费", "甲")
            add(d0, "XJ01DCC", "注射用头孢呋辛钠", "1.5g/支", "支", 18.62, 2, "西药费", "乙")
            add(d0, "XB05BBA", "0.9%氯化钠注射液", "100ml/袋", "袋", 3.1, 3, "西药费", "甲")
            add(d0, "HCL0012", "一次性使用输液器（带针）", "0.7mm", "套", 4.5, 2, "卫生材料费", "乙")
        if d0 > a:
            add(d0, "XA10ABC", "门冬胰岛素注射液", "3ml:300U", "支", 65.8, 1 if k % 3 == 1 else 0, "西药费", "乙")
        add(d0, "XA10AEC", "甘精胰岛素注射液", "3ml:300U", "支", 168.0, 1 if k in (1, 5) else 0, "西药费", "乙")
    add(a, "250101015", "血细胞分析（五分类）", "", "项", 25.0, 1, "化验费", "甲")
    add(a, "250102035", "C反应蛋白测定", "", "项", 30.0, 1, "化验费", "甲")
    add(a, "250301001", "肝功能组合", "", "组", 88.0, 1, "化验费", "甲")
    add(a, "250302001", "肾功能组合", "", "组", 45.0, 1, "化验费", "甲")
    add(a, "250304001", "电解质四项", "", "组", 28.0, 1, "化验费", "甲")
    add(a, "250307003", "糖化血红蛋白测定", "", "项", 60.0, 1, "化验费", "乙")
    add(a, "250203001", "凝血功能四项", "", "组", 72.0, 1, "化验费", "甲")
    add(a, "250101002", "尿液分析（干化学＋有形成分）", "", "次", 20.0, 1, "化验费", "甲")
    add(a, "250403001", "乙肝两对半＋丙肝＋HIV＋梅毒筛查", "", "组", 148.0, 1, "化验费", "乙")
    add(a, "310701001", "常规心电图检查（十二导）", "", "次", 20.0, 1, "检查费", "甲")
    add(a, "210300001", "X线计算机体层（CT）平扫（胸部）", "", "部位", 180.0, 1, "检查费", "甲")
    add(a, "210300001", "X线计算机体层（CT）平扫（上腹部）", "", "部位", 180.0, 1, "检查费", "甲")
    add(a, "220302001", "彩色多普勒超声（肝胆胰脾）", "", "部位", 120.0, 1, "检查费", "甲")
    add(a + timedelta(days=1), "310300001", "电子胃镜检查", "", "次", 260.0, 1, "检查费", "乙")
    add(a + timedelta(days=1), "310300012", "无痛胃镜麻醉监护", "", "次", 180.0, 1, "治疗费", "丙")
    add(op, x["op_fee"][0], x["op_fee"][1], "", "次", x["op_fee"][2], 1, "手术费", "甲")
    add(op, "330100018", "腹腔镜使用费", "", "次", 800.0, 1, "手术费", "乙")
    add(op, "330100001", "全身麻醉（气管插管）", "", "2小时", 680.0, 1, "手术费", "甲")
    add(op, "330100015", "术中心电血压血氧监测", "", "小时", 30.0, 2, "治疗费", "甲")
    add(op, "HCL2001", "一次性使用腹腔镜穿刺器", "10mm", "个", 580.0, 2, "卫生材料费", "乙")
    add(op, "HCL2002", "一次性使用腹腔镜穿刺器", "5mm", "个", 420.0, 1, "卫生材料费", "乙")
    add(op, "HCL2015", "可吸收结扎夹", "中号", "枚", 238.0, 3, "卫生材料费", "乙")
    add(op, "HCL2030", "一次性使用标本取物袋", "", "个", 180.0, 1, "卫生材料费", "丙")
    add(op, "HCL2044", "一次性使用引流管", "F20", "根", 46.0, 1, "卫生材料费", "乙")
    add(op, "HCL2050", "可吸收缝合线", "3-0", "根", 88.0, 2, "卫生材料费", "乙")
    add(op, "XN01AXA", "丙泊酚乳状注射液", "20ml:0.2g", "支", 32.5, 1, "西药费", "甲")
    add(op, "XN01AHS", "枸橼酸舒芬太尼注射液", "1ml:50μg", "支", 48.6, 1, "西药费", "甲")
    add(op, "XM03ACR", "罗库溴铵注射液", "5ml:50mg", "支", 58.9, 1, "西药费", "乙")
    add(op, "XN01ABS", "吸入用七氟烷", "250ml", "瓶", 368.0, 0.2, "西药费", "乙")
    add(op, "XV03ABS", "舒更葡糖钠注射液", "2ml:200mg", "支", 398.0, 1, "西药费", "丙")
    add(op, "XJ01FFC", "克林霉素磷酸酯注射液", "2ml:0.3g", "支", 9.8, 2, "西药费", "甲")
    add(op, "XA02BCO", "注射用奥美拉唑钠", "40mg", "支", 12.6, 3, "西药费", "乙")
    add(op, "XB05BAA", "复方氨基酸注射液（18AA）", "250ml", "瓶", 28.4, 3, "西药费", "乙")
    add(op, "XN02AXT", "盐酸曲马多注射液", "2ml:100mg", "支", 6.2, 1, "西药费", "甲")
    add(op, "ZC01XST", "血塞通注射液", "2ml:100mg", "支", 14.8, 4, "中成药费", "乙")
    add(op, "270300001", "病理组织标本检查与诊断", "", "例", 180.0, 1, "化验费", "甲")
    add(op, "120900001", "氧气吸入", "", "小时", 3.0, 18, "治疗费", "甲")
    add(op, "310603001", "心电监测", "", "小时", 6.0, 20, "治疗费", "甲")
    add(op + timedelta(days=1), "250101015", "血细胞分析（五分类）", "", "项", 25.0, 1, "化验费", "甲")
    add(op + timedelta(days=1), "250304001", "电解质四项", "", "组", 28.0, 1, "化验费", "甲")
    add(op + timedelta(days=1), "120100008", "中换药", "", "次", 22.0, 1, "治疗费", "甲")
    add(op + timedelta(days=3), "120100008", "中换药", "", "次", 22.0, 1, "治疗费", "甲")
    add(op + timedelta(days=3), "120200002", "拔除引流管", "", "次", 15.0, 1, "治疗费", "甲")
    add(op + timedelta(days=1), "ZY001", "中药饮片（大承气汤加减）", "付", "付", 26.5, 3, "中药饮片费", "乙")
    add(dc, "XA10ABC", "门冬胰岛素注射液（出院带药）", "3ml:300U", "支", 65.8, 1, "西药费", "乙")
    add(dc, "XA10AEC", "甘精胰岛素注射液（出院带药）", "3ml:300U", "支", 168.0, 1, "西药费", "乙")
    add(dc, "XA05AAU", "熊去氧胆酸片（出院带药）", "250mg×24片", "盒", 34.6, 1, "西药费", "乙")
    add(dc, "XA02BCO", "奥美拉唑肠溶胶囊（出院带药）", "20mg×14粒", "盒", 18.0, 1, "西药费", "甲")
    add(dc, "250305006", "病历复印及工本费", "", "次", 10.0, 1, "其他费", "丙")
    items = [it for it in items if it["qty"]]
    items.sort(key=lambda it: it["date"])
    return items


def fee_pages(c, x, lg, items) -> list[Page]:
    import math
    per = math.ceil(len(items) / math.ceil(len(items) / 30))  # 平均分页，避免最后一页只剩几行
    chunks = [items[k:k + per] for k in range(0, len(items), per)]
    pages = []
    running = 0.0
    for n, chunk in enumerate(chunks, 1):
        p = Page("住院费用明细清单")
        header(p, c, f"住院患者费用明细清单（{n}/{len(chunks)}）", lg)
        y = 108
        p.field(40, y, "姓名", c.patient, 9.5, truth=P, sep="：", gap=1)
        p.field(140, y, "住院号", c.inpatient_no, 9.5, truth=MID, sep="：", gap=1)
        p.field(290, y, "科室", c.dept, 9.5, sep="：", gap=1)
        p.field(390, y, "医保编号", x["ins_no"], 9.5, truth=MID, sep="：", gap=1)
        y += 16
        p.field(40, y, "入院", dshort(c.admit), 9.5, truth=D, sep="：", gap=1)
        p.field(140, y, "出院", dshort(c.discharge), 9.5, truth=D, sep="：", gap=1)
        p.field(290, y, "费别", "职工基本医疗保险", 9.5, sep="：", gap=1)
        y += 18
        rows = []
        for it in chunk:
            running += it["amt"]
            rows.append([(dshort(it["date"])[5:], D), it["code"], it["name"] if measure(it["name"], 8) < 150 else it["name"][:18] + "…",
                         it["spec"], it["unit"], (f"{it['price']:.2f}", FEE), (f"{it['qty']:g}", FEE), (f"{it['amt']:.2f}", FEE), it["ins"]])
        y = table(p, 40, y, [36, 62, 154, 66, 32, 50, 34, 56, 25], ["日期", "项目编码", "项目名称", "规格", "单位", "单价", "数量", "金额", "类别"],
                  rows, rh=19.2, size=8, hsize=8.5)
        y += 14
        page_sum = sum(it["amt"] for it in chunk)
        p.field(40, y, "本页小计", f"{page_sum:.2f}", 9.5, truth=FEE, sep="：", gap=1)
        p.field(200, y, "累计金额", f"{running:.2f}", 9.5, truth=FEE, sep="：", gap=1)
        p.field(380, y, "打印人", c.staff["收费员"], 9.5, truth=STF, sep="：", gap=1)
        y += 16
        p.text(40, y, "类别说明：甲＝医保甲类全额纳入，乙＝医保乙类先行自付10%，丙＝自费。", 8)
        footer(p, c)
        pages.append(p)
    return pages


CATS = ["床位费", "诊察费", "检查费", "化验费", "治疗费", "手术费", "护理费", "卫生材料费", "西药费", "中药饮片费", "中成药费", "其他费"]


def settle(items, x) -> dict:
    by = {k: dict(total=0.0, a=0.0, b=0.0, c=0.0) for k in CATS}
    for it in items:
        e = by[it["cat"]]
        e["total"] += it["amt"]
        e[{"甲": "a", "乙": "b", "丙": "c"}[it["ins"]]] += it["amt"]
    total = round(sum(e["total"] for e in by.values()), 2)
    a = sum(e["a"] for e in by.values())
    b = sum(e["b"] for e in by.values())
    cc = round(sum(e["c"] for e in by.values()), 2)
    first_pay = round(b * 0.10, 2)  # 乙类先行自付
    deductible = 800.0
    base = a + b - first_pay
    fund = round(max(0.0, base - deductible) * 0.85, 2)
    serious = 0.0
    civil = 0.0
    personal = round(total - fund - serious - civil, 2)
    acct = round(min(personal, 1500.0), 2)
    cash = round(personal - acct, 2)
    self_pay_in = round(base - fund, 2)  # 个人自付（起付线＋政策范围内自付）
    return dict(by=by, total=total, a=round(a, 2), b=round(b, 2), c=cc, first=first_pay, deductible=deductible, fund=fund,
                serious=serious, civil=civil, personal=personal, acct=acct, cash=cash, selfpay=self_pay_in, selfcost=cc)


def ins_list_1(c, x, lg, s) -> Page:
    p = Page("医疗保障基金结算清单")
    p.text((p.w - measure("医疗保障基金结算清单", 17, True)) / 2, 40, "医疗保障基金结算清单", 17, bold=True)
    y = 76
    p.field(40, y, "清单流水号", x["list_no"], 9, truth=MID, sep="：", gap=1)
    p.field(300, y, "定点医疗机构名称", c.hospital, 9, truth=ORG, sep="：", gap=1)
    y += 16
    p.field(40, y, "定点医疗机构代码", x["org_code"], 9, truth=MID, sep="：", gap=1)
    p.field(300, y, "医保结算等级", "三级", 9, sep="：", gap=1)
    y += 16
    p.field(40, y, "医保编号", x["ins_no"], 9, truth=MID, sep="：", gap=1)
    p.field(300, y, "病案号", c.case_no, 9, truth=MID, sep="：", gap=1)
    y += 16
    p.field(40, y, "申报时间", fmt_date(c.discharge + timedelta(days=1)), 9, truth=D, sep="：", gap=1)
    y += 22
    p.text(40, y, "一、基本信息", 10.5, bold=True)
    y += 18
    rows = [
        [("姓名", c.patient, P), ("性别", c.sex, ("SEX", "keep")), ("出生日期", dshort(c.birth), D), ("年龄", str(c.age), ("AGE", "keep"))],
        [("国籍", "中国", None), ("民族", "汉族", None), ("证件类型", "居民身份证", None), ("证件号码", c.id_card, IDC)],
        [("职业", "企业职员", None), ("现住址", c.address, ADDR)],
        [("工作单位及地址", c.employer, ORG), ("单位电话", c.hospital_tel[:5] + str(int(c.hospital_tel[5:]) + 1113), PH)],
        [("联系人姓名", c.contact, P), ("关系", "配偶", None), ("联系人地址", c.address, ADDR)],
        [("联系人电话", c.contact_phone, PH), ("医保类型", "职工基本医疗保险", None), ("参保地", c.address[:3], ADDR)],
        [("特殊人员类型", "无", None), ("新生儿入院类型", "—", None)],
    ]
    for row in rows:
        xx = 40
        for label, v, t in row:
            xx = p.field(xx, y, label, v, 8.5, truth=t, sep="：", gap=1) + 14
        p.line(40, y + 13, p.w - 40, y + 13, 0.3)
        y += 18
    y += 8
    p.text(40, y, "二、住院诊疗信息", 10.5, bold=True)
    y += 18
    rows = [
        [("住院医疗类型", "住院", None), ("入院途径", "门诊", None), ("治疗类别", "西医", None)],
        [("入院时间", fmt_date(c.admit) + " 09时", D), ("入院科别", c.dept, None), ("转科科别", "无", None)],
        [("出院时间", fmt_date(c.discharge) + " 10时", D), ("出院科别", c.dept, None), ("实际住院天数", str((c.discharge - c.admit).days), None)],
        [("门(急)诊诊断(西医)", c.diagnosis[0], DX), ("疾病代码", c.diagnosis[1], DX)],
    ]
    for row in rows:
        xx = 40
        for label, v, t in row:
            xx = p.field(xx, y, label, v, 8.5, truth=t, sep="：", gap=1) + 14
        p.line(40, y + 13, p.w - 40, y + 13, 0.3)
        y += 18
    y += 8
    dx_rows = [["主要诊断", (c.diagnosis[0], DX), (c.diagnosis[1], DX), "1"], ["其他诊断", ("2型糖尿病", DX), ("E11.900", DX), "1"],
               ["其他诊断", ("青霉素过敏个人史", DX), ("Z88.000", DX), "1"]]
    y = table(p, 40, y, [80, 230, 120, 85], ["诊断类别", "出院西医诊断", "疾病代码", "入院病情"], dx_rows, rh=18, size=8.5, hsize=8.5)
    y += 12
    op_rows = [[(x["op"][0], None), (x["op"][1], DX), (dshort(x["op_day"]), D), ("hand", c.staff["主任医师"], STF, False),
                x["anes"], ("hand", c.staff["麻醉医师"], STF, False)]]
    y = table(p, 40, y, [140, 70, 70, 80, 75, 80], ["手术及操作名称", "代码", "操作日期", "术者", "麻醉方式", "麻醉医师"], op_rows, rh=20,
              size=8.5, hsize=8.5)
    y += 12
    rows = [[("呼吸机使用时间", "0天0小时0分钟", None), ("颅脑损伤患者昏迷时间", "—", None)],
            [("重症监护病房类型", "无", None), ("输血品种", "无", None)],
            [("特级护理天数", "0", None), ("一级护理天数", "2", None), ("二级护理天数", str((c.discharge - c.admit).days - 2), None)],
            [("离院方式", "医嘱离院", None), ("是否有出院31天内再住院计划", "无", None)],
            [("主诊医师姓名", c.staff["主治医师"], STF), ("主诊医师代码", f"D{x['org_code'][1:7]}{x['rng'].randint(1000, 9999)}", MID)],
            [("责任护士姓名", c.staff["责任护士"], STF), ("责任护士代码", f"N{x['org_code'][1:7]}{x['rng'].randint(1000, 9999)}", MID)]]
    for row in rows:
        xx = 40
        for label, v, t in row:
            xx = p.field(xx, y, label, v, 8.5, truth=t, sep="：", gap=1) + 14
        p.line(40, y + 13, p.w - 40, y + 13, 0.3)
        y += 18
    p.text(p.w - 70, p.h - 40, "第 0 页", 8)
    return p


def ins_list_2(c, x, lg, s) -> Page:
    p = Page("医疗保障基金结算清单（续）")
    p.text((p.w - measure("医疗保障基金结算清单（续）", 15, True)) / 2, 40, "医疗保障基金结算清单（续）", 15, bold=True)
    y = 72
    p.field(40, y, "姓名", c.patient, 9, truth=P, sep="：", gap=1)
    p.field(160, y, "清单流水号", x["list_no"], 9, truth=MID, sep="：", gap=1)
    p.field(400, y, "医保编号", x["ins_no"], 9, truth=MID, sep="：", gap=1)
    y += 20
    p.text(40, y, "三、医疗收费信息", 10.5, bold=True)
    y += 16
    p.field(40, y, "业务流水号", x["receipt"].replace("YJ", "YW"), 8.5, truth=MID, sep="：", gap=1)
    p.field(250, y, "票据代码", x["bill_code"], 8.5, truth=MID, sep="：", gap=1)
    p.field(400, y, "票据号码", x["bill_no"], 8.5, truth=MID, sep="：", gap=1)
    y += 16
    p.field(40, y, "结算期间", f"{dshort(c.admit)} 至 {dshort(c.discharge)}", 8.5, truth=D, sep="：", gap=1)
    y += 18
    rows = []
    for k in CATS:
        e = s["by"][k]
        if e["total"] == 0:
            continue
        rows.append([k, (f"{e['total']:.2f}", FEE), (f"{e['a']:.2f}", FEE), (f"{e['b']:.2f}", FEE), (f"{e['c']:.2f}", FEE), "0.00"])
    rows.append([("金额合计", None), (f"{s['total']:.2f}", FEE), (f"{s['a']:.2f}", FEE), (f"{s['b']:.2f}", FEE), (f"{s['c']:.2f}", FEE), "0.00"])
    y = table(p, 40, y, [120, 85, 80, 80, 80, 70], ["项目名称", "金额", "甲类", "乙类", "自费", "其他"], rows, rh=18, size=8.5, hsize=8.5)
    y += 14
    fund_rows = [["医保统筹基金支付", (f"{s['fund']:.2f}", FEE), "个人自付", (f"{s['selfpay']:.2f}", FEE)],
                 ["职工大额医疗费用补助基金支付", (f"{s['serious']:.2f}", FEE), "个人自费", (f"{s['selfcost']:.2f}", FEE)],
                 ["公务员医疗补助资金支付", (f"{s['civil']:.2f}", FEE), "个人账户支付", (f"{s['acct']:.2f}", FEE)],
                 ["医疗救助支付", "0.00", "个人现金支付", (f"{s['cash']:.2f}", FEE)],
                 ["基金支付合计", (f"{s['fund'] + s['serious'] + s['civil']:.2f}", FEE), "个人负担合计", (f"{s['personal']:.2f}", FEE)]]
    y = table(p, 40, y, [160, 97.3, 160, 97.3], ["基金支付类型", "金额", "个人负担", "金额"], fund_rows, rh=19, size=8.5, hsize=8.5)
    y += 12
    p.text(40, y, f"说明：起付标准 {s['deductible']:.2f} 元；乙类先行自付 {s['first']:.2f} 元；统筹基金支付比例 85%。", 8.5)
    y += 18
    p.field(40, y, "医保支付方式", "按病种分值付费（DIP）", 8.5, sep="：", gap=1)
    p.field(300, y, "病种分值", f"{x['rng'].randint(800, 1400)}", 8.5, sep="：", gap=1)
    y += 26
    p.field(40, y, "定点医疗机构填报部门", "医保办", 9, sep="：", gap=1)
    p.field(300, y, "定点医疗机构填报人", c.staff["编码员"], 9, truth=SIG, hand=True, esign=True, sep="：", gap=4)
    y += 30
    p.field(40, y, "医保机构", c.address[:3] + "医疗保障事务中心", 9, truth=ORG, sep="：", gap=1)
    p.field(300, y, "医保机构经办人", c.staff["医保经办人"], 9, truth=SIG, hand=True, esign=True, sep="：", gap=4)
    y += 30
    p.field(40, y, "医保机构编码", f"S{x['org_code'][1:9]}", 9, truth=MID, sep="：", gap=1)
    p.seal(470, y + 30, 40, c.hospital + "医保办")
    p.text(p.w - 70, p.h - 40, "第 0 页", 8)
    return p


def e_invoice(c, x, lg, s) -> Page:
    p = Page("医疗收费电子票据")
    t = "医疗收费电子票据（住院）"
    p.text((p.w - measure(t, 17, True)) / 2, 40, t, 17, bold=True)
    p.image(40, 30, 62, 62, qrcode(f"RX-BILL-{x['bill_code']}-{x['bill_no']}-{x['check']}"), truth=("QRCODE", R))
    y = 72
    p.field(150, y, "票据代码", x["bill_code"], 9, truth=MID, sep="：", gap=1)
    p.field(300, y, "票据号码", x["bill_no"], 9, truth=MID, sep="：", gap=1)
    p.field(450, y, "校验码", x["check"], 9, truth=MID, sep="：", gap=1)
    y += 18
    p.field(150, y, "交款人统一社会信用代码", c.id_card, 9, truth=IDC, sep="：", gap=1)
    p.field(450, y, "开票日期", dshort(c.discharge), 9, truth=D, sep="：", gap=1)
    y += 18
    p.field(40, y, "交款人", c.patient, 9, truth=P, sep="：", gap=1)
    p.field(150, y, "住院号", c.inpatient_no, 9, truth=MID, sep="：", gap=1)
    p.field(300, y, "住院科别", c.dept, 9, sep="：", gap=1)
    p.field(450, y, "住院天数", str((c.discharge - c.admit).days), 9, sep="：", gap=1)
    y += 18
    p.field(40, y, "住院时间", f"{dshort(c.admit)} 至 {dshort(c.discharge)}", 9, truth=D, sep="：", gap=1)
    p.field(300, y, "医保类型", "职工基本医疗保险", 9, sep="：", gap=1)
    p.field(450, y, "性别", c.sex, 9, truth=("SEX", "keep"), sep="：", gap=1)
    y += 20
    rows = []
    for k in CATS:
        e = s["by"][k]
        if e["total"]:
            rows.append([k, "1", (f"{e['total']:.2f}", FEE), ""])
    y = table(p, 40, y, [200, 80, 120, 115.3], ["项目名称", "数量/单位", "金额（元）", "备注"], rows, rh=19, size=9, hsize=9)
    y += 12
    cn = to_rmb(s["total"])
    p.field(40, y, "合计（大写）", cn, 10, truth=FEE, sep="：", gap=1)
    p.field(400, y, "（小写）", f"{s['total']:.2f}", 10, truth=FEE, gap=1)
    y += 22
    lines = [f"预缴金额：{x['deposit']:.2f}　补缴金额：{max(0.0, s['cash'] - x['deposit']):.2f}　退费金额：{max(0.0, x['deposit'] - s['cash']):.2f}",
             f"医保统筹基金支付：{s['fund']:.2f}　其他支付：{s['serious'] + s['civil']:.2f}　个人账户支付：{s['acct']:.2f}",
             f"个人现金支付：{s['cash']:.2f}　个人自付：{s['selfpay']:.2f}　个人自费：{s['selfcost']:.2f}"]
    for ln in lines:
        p.text(40, y, ln, 9, truth=FEE)
        y += 16
    y += 6
    p.field(40, y, "医保编号", x["ins_no"], 9, truth=MID, sep="：", gap=1)
    p.field(300, y, "医疗机构类型", "非营利性医疗机构", 9, sep="：", gap=1)
    y += 30
    p.field(40, y, "收款单位（章）", c.hospital, 9.5, truth=ORG, sep="：", gap=1)
    p.field(300, y, "复核人", c.staff["审核者"], 9.5, truth=STF, sep="：", gap=1)
    p.field(450, y, "收款人", c.staff["收费员"], 9.5, truth=STF, sep="：", gap=1)
    p.seal(p.w - 130, y + 70, 42, c.hospital + "财务专用章")
    p.seal(p.w - 64, 42, 24, "财政部监制")
    p.text(p.w - 70, p.h - 40, "第 0 页", 8)
    return p


def to_rmb(v: float) -> str:
    digits = "零壹贰叁肆伍陆柒捌玖"
    units = ["", "拾", "佰", "仟", "万", "拾", "佰", "仟"]
    yuan, fen = int(v), round(v * 100) % 100
    s = ""
    ds = str(yuan)
    zero = False
    for i, ch in enumerate(ds):
        pos = len(ds) - 1 - i
        if ch == "0":
            zero = True
            if pos == 4:
                s += "万"
            continue
        if zero:
            s += "零"
            zero = False
        s += digits[int(ch)] + units[pos]
    s += "元"
    j, f = fen // 10, fen % 10
    if j == 0 and f == 0:
        return s + "整"
    s += (digits[j] + "角") if j else "零"
    s += (digits[f] + "分") if f else ""
    return s


def deposit_and_settle(c, x, lg, s) -> Page:
    p = Page("预交金收据与出院结算单")
    header(p, c, "住院预交金收据", lg)
    y = 112
    p.field(40, y, "收据号", x["receipt"], truth=MID, sep="：", gap=1)
    p.field(300, y, "交款日期", fmt_date(c.admit), truth=D, sep="：", gap=1)
    y += 22
    p.field(40, y, "交款人", c.contact, truth=P, sep="：", gap=1)
    p.field(200, y, "患者姓名", c.patient, truth=P, sep="：", gap=1)
    p.field(380, y, "住院号", c.inpatient_no, truth=MID, sep="：", gap=1)
    y += 22
    p.field(40, y, "金额（大写）", to_rmb(x["deposit"]), truth=FEE, sep="：", gap=1)
    p.field(380, y, "（小写）", f"{x['deposit']:.2f}", truth=FEE, gap=1)
    y += 22
    card = "6217 " + " ".join(f"{x['rng'].randint(0, 9999):04d}" for _ in range(3)) + f" {x['rng'].randint(100, 999)}"
    p.field(40, y, "支付方式", "银行卡", sep="：", gap=1)
    p.field(200, y, "卡号", card, truth=("BANK_CARD", R), sep="：", gap=1)
    y += 30
    p.field(40, y, "收款员", c.staff["收费员"], truth=SIG, hand=True, esign=True, sep="：", gap=4)
    p.seal(470, y, 34, c.hospital + "住院收费专用章")
    y += 44
    p.line(40, y, p.w - 40, y, 1.2)
    y += 24
    t = "出 院 结 算 单"
    p.text((p.w - measure(t, 16, True)) / 2, y, t, 16, bold=True)
    y += 34
    rows = [[("姓名", c.patient, P), ("住院号", c.inpatient_no, MID), ("结算日期", fmt_date(c.discharge), D)],
            [("入院日期", fmt_date(c.admit), D), ("出院日期", fmt_date(c.discharge), D), ("住院天数", f"{(c.discharge - c.admit).days}天", None)],
            [("医保编号", x["ins_no"], MID), ("人员类别", "在职职工", None)]]
    for row in rows:
        for k, (label, v, tt) in enumerate(row):
            p.field(40 + k * 180, y, label, v, 9.5, truth=tt, sep="：", gap=1)
        y += 20
    y += 8
    back = x["deposit"] - s["cash"]
    rows = [["住院总费用", (f"{s['total']:.2f}", FEE), "预交金", (f"{x['deposit']:.2f}", FEE)],
            ["统筹基金支付", (f"{s['fund']:.2f}", FEE), "个人账户支付", (f"{s['acct']:.2f}", FEE)],
            ["起付标准", (f"{s['deductible']:.2f}", FEE), "乙类先行自付", (f"{s['first']:.2f}", FEE)],
            ["自费费用", (f"{s['selfcost']:.2f}", FEE), "个人现金支付", (f"{s['cash']:.2f}", FEE)],
            ["应退金额" if back >= 0 else "应补金额", (f"{abs(back):.2f}", FEE), "结算方式", "原路退回银行卡" if back >= 0 else "现金"]]
    y = table(p, 40, y, [140, 117, 140, 118.3], ["项目", "金额（元）", "项目", "金额（元）"], rows, rh=22, size=9.5, hsize=9.5)
    y += 30
    x0 = p.text(40, y, "患者（家属）签字：")
    p.hand(x0 + 6, y, c.contact, truth=SIG, esign=True)
    p.field(330, y, "结算员", c.staff["收费员"], truth=SIG, hand=True, esign=True, sep="：", gap=4)
    footer(p, c)
    return p


# ---------- 组装 ----------

def build_pages(seed: int, scen: str) -> tuple[list[Page], Case]:
    c, x = setup(seed, scen)
    items = fee_items(c, x)
    s = settle(items, x)
    c.fee_total = f"{s['total']:.2f}"
    lg = logo(c.hospital)
    a = c.admit
    pages = [
        front_page(c, lg), registration(c, lg), admission_1(c, x, lg), admission_2(c, x, lg), first_note(c, x, lg),
        daily_note(c, lg), senior_round(c, x, lg), preop(c, x, lg), consent(c, lg), anes_consent(c, x, lg),
        safety_check(c, x, lg), anes_record(c, x, lg), op_record(c, x, lg), critical_notice(c, x, lg),
        orders(c, x, lg, True), orders(c, x, lg, False), temperature(c, x, lg), nursing_record(c, lg),
        lab(c, x, lg, "血常规", a + timedelta(days=0)), lab(c, x, lg, "生化全套", a), lab(c, x, lg, "凝血功能及尿常规", a),
        lab(c, x, lg, "血常规", x["op_day"] + timedelta(days=1)),
        imaging(c, x, lg, "CT"), imaging(c, x, lg, "US"), endoscopy(c, lg, c.rng), pathology(c, x, lg),
        discharge(c, x, lg), certificate(c, x, lg), prescription(c, x, lg),
        *fee_pages(c, x, lg, items), ins_list_1(c, x, lg, s), ins_list_2(c, x, lg, s), e_invoice(c, x, lg, s),
        deposit_and_settle(c, x, lg, s),
    ]
    # 统一页码
    for n, p in enumerate(pages, 1):
        for e in p.els:
            if e.kind == "text" and re.fullmatch(r"第 \d+ 页", e.text):
                e.text = f"第 {n} 页"
    return pages, c


def write(pages, path: Path, mode: str, seed: int) -> list[dict]:
    chars = render.page_chars(pages) + "0123456789"
    rng = random.Random(seed)
    w = render.Writer(chars)
    truth = []
    for i, p in enumerate(pages):
        if mode == "text":
            items = w.text_page(p, rng, watermark=(i % 3 == 0))
        else:
            # 扫描件：以白纸 200 DPI 为主，夹杂牛皮纸、低质量扫描和手机拍照
            r = i % 9
            if r == 4:
                im, items, (wp, hp) = render.scan_page(p, chars, 200, "kraft", rng)
            elif r == 7:
                im, items, (wp, hp) = render.scan_page(p, chars, 150, "white", rng, hard=frozenset({"lowq"}))
            elif r == 8 and p.title not in ("住院费用明细清单",):
                im, items, (wp, hp) = render.scan_page(p, chars, 200, "white", rng, hard=frozenset({"photo"}))
            elif r == 2:
                im, items, (wp, hp) = render.scan_page(p, chars, 200, "white", rng, hard=frozenset({"faint_seal"}))
            else:
                im, items, (wp, hp) = render.scan_page(p, chars, 200, "white", rng)
            w.image_page(im, wp, hp)
        truth += [t.json(i + 1) for t in items]
    w.save(path)
    return truth


def build(path: Path, seed: int, scenario: str, mode: str) -> tuple[int, list[dict]]:
    """生成一份住院全套材料 PDF。scenario：chole 胆囊结石 / appe 阑尾炎；mode：text 系统导出 / scan 扫描件。
    返回 (页数, 标准答案)。"""
    pages, _ = build_pages(seed, scenario)
    return len(pages), write(pages, path, mode, seed)
