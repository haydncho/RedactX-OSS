"""正文人名评估集（纯文本）：只在病程、谈话、护理记录的叙述里出现的人名，外加大量含姓氏字的医学用语作干扰。

    python -m bench.prose [--docs 200] [--seed 7]

不渲染页面：段落按随机宽度折成“行”，交给识别引擎（与流水线相同的行结构），按字统计：
- 遮全率：人名的每个字都被命中；
- 误遮：命中了不属于任何人名的字（按连续片段计数，并列出最常见的片段）。
只有姓氏的称呼（“李主任”里的“李”）不计入遮全率，也不算误遮。
"""

from __future__ import annotations

import argparse
import random
from collections import Counter

from redactx.schemas import Char, Line, PageData

from .fakes import GIVEN, SURNAMES

REL = ["女儿", "儿子", "妻子", "丈夫", "母亲", "父亲", "女婿", "儿媳", "孙女", "外甥"]
COMPLAINTS = ["反复胸闷气促2年，加重3天", "多饮、多尿伴体重下降3月", "右下腹痛12小时", "发热、咳嗽1周", "头晕伴视物模糊2天", "腰痛伴左下肢放射痛1月"]
TITLES = ["主任医师", "副主任医师", "主治医师", "住院医师", "医师", "主任"]

# 句式：{P} 患者，{F} 家属，{D} 医师，{N} 护士，{S} 只有姓氏的称呼，{rel} 亲属关系
NAME_SENTENCES = [
    "患者{P}，{sex}，{age}岁，因“{complaint}”入院。",
    "由家属{F}（患者之{rel}）陪同就诊，{F}表示理解病情并同意治疗方案。",
    "今日{D}{title}查房，同意目前诊断，指示继续目前治疗。",
    "向患者家属{F}交代病情及手术风险，家属表示理解并签字。",
    "患者之{rel}{F}代为签署知情同意书。",
    "护士{N}遵医嘱予静脉滴注，患者无不适。",
    "会诊意见：{D}医师建议完善胃镜检查。",
    "与患者{rel}{F}谈话，告知病情及预后。",
    "{F}诉患者昨夜睡眠差，食欲欠佳。",
    "患者由{F}、{F2}两人陪同入院。",
    "电话联系患者{rel}{F}，嘱其明日来院。",
    "{P}今晨诉头痛，测血压150/95mmHg，已通知值班医师。",
    "经{D}、{D2}两位医师共同讨论，决定择期手术。",
    "{N}护士长组织护理查房，强调防跌倒措施。",
    "{S}主任查房后指示：加强抗感染治疗。",
    "患者{P}家属{F}要求转上级医院，已告知转院风险。",
    "术者{D}，助手{D2}，麻醉医师{D3}，手术顺利。",
    "今日由{D}医师为患者行腰椎穿刺术，过程顺利。",
    "已将检查结果告知患者本人及其{rel}{F}。",
    "{S}医生建议复查胸部CT。",
]
# 不含人名、但含常见姓氏字的句子
PLAIN_SENTENCES = [
    "痰培养示金黄色葡萄球菌，药敏提示对万古霉素敏感。",
    "既往高血压病史10年，规律服用苯磺酸氨氯地平片。",
    "查体：巩膜无黄染，双肺呼吸音清，未闻及干湿性啰音。",
    "予常规护理，告知患者注意事项，余无特殊。",
    "患者信息已核对无误，腕带佩戴正确。",
    "谈话记录由家属签字确认，一式两份。",
    "复查白细胞计数正常，C反应蛋白较前下降。",
    "调整降糖方案后，空腹血糖控制可。",
    "予马来酸依那普利片10mg口服，每日一次。",
    "右前臂石膏固定，嘱抬高患肢，定期复查。",
    "何时出院视病情而定，向家属解释清楚。",
    "既往史：否认肝炎、结核病史，否认输血史。",
    "予甘露醇脱水降颅压，监测电解质。",
    "夏季注意防暑，戴口罩，避免去人群密集场所。",
    "患者曾于2019年行胆囊切除术，恢复良好。",
    "疼痛程度评分3分，睡眠尚可。",
    "龙胆泻肝汤加减，水煎服，每日一剂。",
    "双侧瞳孔等大等圆，直径约3mm，对光反射灵敏。",
    "于今日上午转入重症监护室继续治疗。",
    "向患者及家属交代病情，家属表示知情同意。",
    "予林可霉素抗感染，注意观察有无皮疹。",
    "血常规：血红蛋白98g/L，血小板计数正常。",
    "毛发分布正常，皮肤黏膜无出血点。",
    "任何不适随时就诊，门诊随访。",
    "予付费后办理出院手续。",
    "严格卧床休息，严重时及时通知医师。",
    "患者诉周身乏力，无发热、寒战。",
    "方可下床活动，逐步增加活动量。",
    "孔径约0.5cm，边缘整齐，无渗出。",
    "予温水擦浴物理降温，体温降至37.2℃。",
]


def _name(rng: random.Random, used: set[str]) -> str:
    while True:
        n = rng.choice(SURNAMES) + "".join(rng.choice(GIVEN) for _ in range(rng.choice([1, 2, 2])))
        if n not in used:
            used.add(n)
            return n


def make_doc(rng: random.Random) -> tuple[str, list[tuple[int, int, str]]]:
    """一段叙述：约一半句子含人名。返回 (文本, [(起, 止, 类别)])，类别为 name / optional。"""
    used: set[str] = set()
    ctx = {"P": _name(rng, used), "F": _name(rng, used), "F2": _name(rng, used), "D": _name(rng, used), "D2": _name(rng, used),
           "D3": _name(rng, used), "N": _name(rng, used)}
    sents = rng.sample(NAME_SENTENCES, 5) + rng.sample(PLAIN_SENTENCES, 5)
    rng.shuffle(sents)
    text, spans = "", []
    for s in sents:
        vals = {**ctx, "S": rng.choice(SURNAMES), "rel": rng.choice(REL), "sex": rng.choice("男女"), "age": str(rng.randint(18, 90)),
                "complaint": rng.choice(COMPLAINTS), "title": rng.choice(TITLES)}
        i = 0
        while i < len(s):
            if s[i] == "{":
                j = s.index("}", i)
                key = s[i + 1 : j]
                v = vals[key]
                if key in ctx or key == "S":
                    spans.append((len(text), len(text) + len(v), "optional" if key == "S" else "name"))
                text += v
                i = j + 1
            else:
                text += s[i]
                i += 1
    return text, spans


def to_page(text: str, rng: random.Random) -> tuple[PageData, list[int]]:
    """按随机宽度折行（人名可能被拆到两行），返回页面与每行在全文中的起点。"""
    width = rng.randint(16, 38)
    lines, starts = [], []
    for y, a in enumerate(range(0, len(text), width)):
        chunk = text[a : a + width]
        starts.append(a)
        lines.append(Line([Char(ch, (40 + k * 20, 40 + y * 30, 58 + k * 20, 58 + y * 30)) for k, ch in enumerate(chunk)], "text"))
    return PageData(index=0, width=1000, height=40 + 30 * len(lines) + 40, lines=lines, text_source="text"), starts


def baseline(page: PageData) -> list[tuple[int, int, int]]:
    """识别引擎（规则、字段锚定、NER、全文追踪），只取人名类命中。返回 (行, 起, 止)。"""
    from redactx.detect import engine

    enabled = {"PERSON", "STAFF"}
    hits, _ = engine.page_hits(page, enabled, [])
    seeds = {v: t for v, t in engine.collect_seeds(hits, []).items() if t in enabled}
    hits += engine.propagate(page, seeds, hits)
    return [(h.line, h.start, h.end) for h in hits if h.type in enabled]


def score(docs, predict) -> dict:
    total = covered = 0
    fp_runs: Counter = Counter()
    chars = 0
    for text, spans, page, starts in docs:
        chars += len(text)
        hit = set()
        for li, a, b in predict(page):
            hit.update(range(starts[li] + a, starts[li] + b))
        allowed = set()
        for a, b, kind in spans:
            allowed.update(range(a, b))
            if kind == "name":
                total += 1
                covered += all(k in hit for k in range(a, b))
        run = ""
        for k in range(len(text) + 1):
            if k < len(text) and k in hit and k not in allowed and text[k] != "\n":
                run += text[k]
            elif run:
                fp_runs[run] += 1
                run = ""
    n_fp = sum(fp_runs.values())
    return {"names": total, "recall": covered / max(total, 1), "fp": n_fp, "fp_per_1k": 1000 * n_fp / max(chars, 1), "fp_top": fp_runs.most_common(12)}


def main() -> None:
    ap = argparse.ArgumentParser(description="正文人名评估（纯文本、合成数据）")
    ap.add_argument("--docs", type=int, default=200)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    from redactx.detect import ner

    rng = random.Random(a.seed)
    docs = []
    for _ in range(a.docs):
        text, spans = make_doc(rng)
        page, starts = to_page(text, rng)
        docs.append((text, spans, page, starts))
    r = score(docs, baseline)
    label = "识别（含 NER）" if ner.available() else "识别（未找到 NER 模型）"
    print(f"{label}：人名 {r['names']}，遮全率 {r['recall']:.1%}，误遮片段 {r['fp']}（每千字 {r['fp_per_1k']:.2f}）")
    if r["fp_top"]:
        print("  常见误遮：" + "，".join(f"{t}×{n}" for t, n in r["fp_top"]))


if __name__ == "__main__":
    main()
