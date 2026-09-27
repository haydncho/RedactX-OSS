"""虚构数据：姓名、机构、号码全部随机生成，不对应任何真实人员与机构。"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

SURNAMES = list("王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤")
GIVEN = list("伟芳娜秀英敏静丽强磊军洋勇艳杰娟涛明超秀兰霞平刚桂英华建国志红玉兰梅文辉鑫宇浩然欣怡思远子涵梓萱雨桐佳琪嘉怡晨曦诗涵俊杰博文天佑瑞泽一诺若曦婉清书瑶晓峰海燕春梅秋菊冬雪")
# 虚构地名，刻意避开常见真实地名
CITIES = ["澄岚市", "望舒市", "青禾市", "云栖市", "枫泽市", "岱川市", "沐阳湾市", "汀州湖市"]
DISTRICTS = ["桐荫区", "翠微区", "沙洲区", "北苑区", "临溪区", "东篱区"]
ROADS = ["栖梧路", "听澜街", "揽月大道", "青萝巷", "南屏路", "望江东路"]
HOSP_KINDS = ["第一人民医院", "第二人民医院", "中心医院", "中医院", "妇幼保健院", "人民医院"]
COMPANIES = ["澄岚晨星科技有限公司", "青禾远帆物流有限公司", "云栖百川建材有限公司", "枫泽和泰贸易有限公司"]
AREA_CODES = ["110105", "310104", "440106", "330102", "510107", "420106", "320102", "370102"]

DIAGNOSES = [("2型糖尿病", "E11.900"), ("原发性高血压", "I10.x00"), ("社区获得性肺炎", "J18.900"), ("急性阑尾炎", "K35.900"),
             ("冠状动脉粥样硬化性心脏病", "I25.103"), ("胆囊结石伴慢性胆囊炎", "K80.100"), ("腰椎间盘突出", "M51.202")]
DEPTS = ["内分泌科", "心血管内科", "呼吸内科", "普通外科", "骨科", "消化内科"]
LAB_ITEMS = [("白细胞计数", "WBC", "10^9/L", 3.5, 9.5), ("红细胞计数", "RBC", "10^12/L", 4.3, 5.8), ("血红蛋白", "HGB", "g/L", 130, 175),
             ("血小板计数", "PLT", "10^9/L", 125, 350), ("空腹血糖", "GLU", "mmol/L", 3.9, 6.1), ("谷丙转氨酶", "ALT", "U/L", 9, 50),
             ("肌酐", "CREA", "μmol/L", 57, 111), ("总胆固醇", "TC", "mmol/L", 2.8, 5.2)]


def id_checksum(body17: str) -> str:
    w = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    return "10X98765432"[sum(int(a) * b for a, b in zip(body17, w)) % 11]


@dataclass
class Case:
    """一份虚构病案所需的全部取值。"""

    rng: random.Random
    patient: str = ""
    contact: str = ""
    relative_in_prose: str = ""  # 只在正文出现、从不出现在字段里的人名（检验 NER 缺口）
    sex: str = ""
    birth: date = date(1970, 1, 1)
    age: int = 0
    id_card: str = ""
    phone: str = ""
    contact_phone: str = ""
    address: str = ""
    employer: str = ""
    hospital: str = ""
    hospital_addr: str = ""
    hospital_tel: str = ""
    case_no: str = ""
    inpatient_no: str = ""
    lab_no: str = ""
    bed: str = ""
    dept: str = ""
    admit: date = date(2025, 1, 1)
    discharge: date = date(2025, 1, 1)
    diagnosis: tuple[str, str] = ("", "")
    staff: dict[str, str] = field(default_factory=dict)
    fee_total: str = ""


def fake_name(rng: random.Random, used: set[str]) -> str:
    """随机姓名，不与 used 里的重复（生成后加入 used）。"""
    while True:
        n = rng.choice(SURNAMES) + "".join(rng.choice(GIVEN) for _ in range(rng.choice([1, 2, 2])))
        if n not in used:
            used.add(n)
            return n


def make_case(seed: int) -> Case:
    rng = random.Random(seed)
    used: set[str] = set()
    c = Case(rng=rng)
    c.patient, c.contact, c.relative_in_prose = fake_name(rng, used), fake_name(rng, used), fake_name(rng, used)
    c.sex = rng.choice("男女")
    c.birth = date(rng.randint(1940, 2000), rng.randint(1, 12), rng.randint(1, 28))
    c.admit = date(2025, rng.randint(1, 10), rng.randint(1, 20))
    c.discharge = c.admit + timedelta(days=rng.randint(3, 14))
    c.age = c.admit.year - c.birth.year - ((c.admit.month, c.admit.day) < (c.birth.month, c.birth.day))
    body = rng.choice(AREA_CODES) + c.birth.strftime("%Y%m%d") + f"{rng.randint(0, 999):03d}"
    c.id_card = body + id_checksum(body)
    c.phone = f"1{rng.choice('35789')}{rng.randint(0, 9)}{rng.randint(0, 99999999):08d}"
    c.contact_phone = f"1{rng.choice('35789')}{rng.randint(0, 9)}{rng.randint(0, 99999999):08d}"
    city = rng.choice(CITIES)
    c.address = f"{city}{rng.choice(DISTRICTS)}{rng.choice(ROADS)}{rng.randint(1, 300)}号{rng.randint(1, 30)}栋{rng.randint(101, 2808)}室"
    c.employer = rng.choice(COMPANIES)
    c.hospital = city + rng.choice(HOSP_KINDS)
    c.hospital_addr = f"{city}{rng.choice(DISTRICTS)}{rng.choice(ROADS)}{rng.randint(1, 99)}号"
    c.hospital_tel = f"0{rng.randint(510, 799)}-{rng.randint(1000000, 9999999)}"
    c.case_no = f"{rng.randint(100000, 999999)}"
    c.inpatient_no = f"ZY{rng.randint(2025000000, 2025999999)}"
    c.lab_no = f"{rng.randint(10000000, 99999999)}"
    c.bed = f"{rng.randint(1, 60)}"
    c.dept = rng.choice(DEPTS)
    c.diagnosis = rng.choice(DIAGNOSES)
    for role in ["科主任", "主任医师", "主治医师", "住院医师", "责任护士", "编码员", "检验者", "审核者"]:
        c.staff[role] = fake_name(rng, used)
    c.fee_total = f"{rng.randint(3000, 60000)}.{rng.randint(0, 99):02d}"
    return c


def fmt_date(d: date) -> str:
    return f"{d.year}年{d.month:02d}月{d.day:02d}日"
