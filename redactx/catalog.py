"""实体类型与打码样式目录，供识别引擎与 Web 页共用。"""

from __future__ import annotations

ENTITY_GROUPS = [
    {"key": "people", "name": "人员"},
    {"key": "org", "name": "机构"},
    {"key": "numbers", "name": "证件与号码"},
    {"key": "medical", "name": "医疗标识"},
    {"key": "handwriting", "name": "手写与印章"},
    {"key": "images", "name": "图像"},
]

# default_style 为病案审核场景的推荐样式
ENTITIES = [
    {"code": "PERSON", "name": "患者与联系人姓名", "label": "姓名", "group": "people", "default": True, "default_style": "label"},
    {"code": "STAFF", "name": "医护人员姓名", "label": "医护", "group": "people", "default": True, "default_style": "label"},
    {"code": "ORG", "name": "医院及机构名称", "label": "机构", "group": "org", "default": True, "default_style": "label"},
    {"code": "ADDRESS", "name": "地址", "label": "地址", "group": "org", "default": True, "default_style": "label"},
    {"code": "ID_CARD", "name": "身份证号", "label": "身份证号", "group": "numbers", "default": True, "default_style": "label"},
    {"code": "PHONE", "name": "手机与座机", "label": "电话", "group": "numbers", "default": True, "default_style": "label"},
    {"code": "BANK_CARD", "name": "银行卡号", "label": "银行卡号", "group": "numbers", "default": True, "default_style": "label"},
    {"code": "USCC", "name": "统一社会信用代码", "label": "机构代码", "group": "numbers", "default": True, "default_style": "label"},
    {"code": "EMAIL", "name": "电子邮箱", "label": "邮箱", "group": "numbers", "default": True, "default_style": "label"},
    {"code": "PLATE", "name": "车牌号", "label": "车牌", "group": "numbers", "default": False, "default_style": "label"},
    {"code": "MEDICAL_ID", "name": "病案号、住院号等医疗标识号", "label": "医疗编号", "group": "medical", "default": True, "default_style": "label"},
    {"code": "HANDWRITTEN_FIELD", "name": "手写填写区（字段锚定）", "label": "手写", "group": "handwriting", "default": True, "default_style": "background"},
    {"code": "SIGNATURE", "name": "签名（手写与电子签名）", "label": "签名", "group": "handwriting", "default": True, "default_style": "hatch"},
    {"code": "SEAL", "name": "印章", "label": "印章", "group": "handwriting", "default": True, "default_style": "background"},
    {"code": "LOGO", "name": "医院 Logo 等标识图片", "label": "标识", "group": "images", "default": True, "default_style": "background"},
    {"code": "QRCODE", "name": "二维码与条码", "label": "条码", "group": "images", "default": True, "default_style": "mosaic"},
    {"code": "CUSTOM", "name": "自定义词", "label": "已删除", "group": "people", "default": True, "default_style": "label"},
]

ENTITY_BY_CODE = {e["code"]: e for e in ENTITIES}

STYLES = [
    {"code": "label", "name": "浅色标签", "desc": "擦除后标注被删内容的类型"},
    {"code": "background", "name": "背景色擦除", "desc": "取周边底色填充，像原本就是空白"},
    {"code": "replace", "name": "代号替换", "desc": "擦除后写入一致性代号，如“姓名1”"},
    {"code": "mosaic", "name": "安全马赛克", "desc": "先用噪声覆盖原字，再生成马赛克外观"},
    {"code": "hatch", "name": "斜线花纹", "desc": "保密文件式阴影线，黑白打印清楚"},
    {"code": "inpaint", "name": "图像修复", "desc": "抹去文字，尽量保留表格线和底纹"},
    {"code": "black", "name": "黑色块", "desc": "传统纯黑遮盖"},
]
STYLE_CODES = {s["code"] for s in STYLES}

PRESETS = [
    {"code": "audit", "name": "病案审核", "desc": "标签样式，看得出删了什么", "default_style": "label", "overrides": {}},
    {"code": "public", "name": "对外公开", "desc": "背景色擦除，不暴露被删内容的类型", "default_style": "background", "overrides": {"QRCODE": "background"}},
    {"code": "print", "name": "打印归档", "desc": "斜线花纹，黑白打印清楚", "default_style": "hatch", "overrides": {}},
    {"code": "classic", "name": "传统黑块", "desc": "全部纯黑遮盖", "default_style": "black", "overrides": {}},
]
