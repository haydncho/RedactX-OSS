"""微调正文人名识别模型（在 shibing624/bert4ner-base-chinese 基础上继续训练），导出 ONNX 供 redactx.detect.ner 使用。

    .venv/bin/python -m train.ner corpus --out train/data/ner-synth.jsonl --n 4000        # 生成合成语料（句式与 bench.prose 不重叠）
    .venv/bin/python -m train.ner fit --data train/data/ner-synth.jsonl [--data 真实标注.jsonl] --out train/runs/ner1
    .venv/bin/python -m train.ner export train/runs/ner1 --to train/runs/ner1/onnx
    REDACTX_NER_DIR=train/runs/ner1/onnx .venv/bin/python -m bench.prose                  # 与现有模型比较后再决定是否替换 models/ner

语料为 JSONL，每行 {"text": 句子, "entities": [[起, 止, "PER"|"ORG"|"LOC"|"TIME"], ...]}。标签集与原模型一致，只是继续训练。
只在合成语料上微调时收益有限（合成句式本身就是评估用的）；真实标注到位后再训练、评估、替换。
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

BASE = "shibing624/bert4ner-base-chinese"
BASE_REVISION = "5d660ed2aa9da482bf2d99c6bc8cf2ce66758f6a"

# 训练用句式：与 bench/prose.py 的评估句式不重叠。{P} 患者 {F} 家属 {D} 医护 {H} 医院
TEMPLATES = [
    "患者{P}于今日上午在{F}陪同下来院复诊。",
    "{D}护士为患者更换敷料，伤口愈合良好。",
    "经{D}医师评估后，患者可下地行走。",
    "患者丈夫{F}签署手术同意书并留下联系方式。",
    "今晨{D}副主任医师带领查房，调整用药。",
    "患者{P}主诉夜间咳嗽加重，予止咳对症处理。",
    "家属{F}询问出院后注意事项，已详细告知。",
    "{H}{D}主任远程会诊，建议继续观察。",
    "患者由{H}转入我科，{F}随行。",
    "护理组长{D}检查床单位，要求保持整洁。",
    "{F}（患者之子）要求查看化验结果。",
    "患者{P}拒绝夜间输液，{D}医生已做沟通。",
    "麻醉科{D}术前访视，患者无特殊不适。",
    "康复治疗师{D}指导患者进行床边训练。",
    "患者母亲{F}反映患儿进食差。",
]
PLAIN = [
    "予头孢呋辛钠静脉滴注，每日两次。", "心电监护示窦性心律，心率78次/分。", "嘱低盐低脂饮食，适量活动。", "复查肝功能较前好转。",
    "术后第三天，引流液减少。", "双下肢无水肿，足背动脉搏动可。", "血压控制在130/80mmHg左右。", "余治疗同前，继续观察病情变化。",
]


def corpus(out: Path, n: int, seed: int) -> None:
    from bench.fakes import CITIES, GIVEN, HOSP_KINDS, SURNAMES

    rng = random.Random(seed)

    def name():
        return rng.choice(SURNAMES) + "".join(rng.choice(GIVEN) for _ in range(rng.choice([1, 2, 2])))

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for _ in range(n):
            t = rng.choice(TEMPLATES) if rng.random() < 0.75 else rng.choice(PLAIN)
            vals = {"P": name(), "F": name(), "D": name(), "H": rng.choice(CITIES) + rng.choice(HOSP_KINDS)}
            text, ents, i = "", [], 0
            while i < len(t):
                if t[i] == "{":
                    j = t.index("}", i)
                    k = t[i + 1 : j]
                    ents.append([len(text), len(text) + len(vals[k]), "ORG" if k == "H" else "PER"])
                    text += vals[k]
                    i = j + 1
                else:
                    text += t[i]
                    i += 1
            f.write(json.dumps({"text": text, "entities": ents}, ensure_ascii=False) + "\n")
    print(f"已写入 {out}：{n} 句")


def fit(a) -> None:
    import torch
    from tokenizers import BertWordPieceTokenizer
    from transformers import BertForTokenClassification

    rows = [json.loads(line) for p in a.data for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    random.Random(0).shuffle(rows)
    model = BertForTokenClassification.from_pretrained(BASE, revision=BASE_REVISION)
    label2id = model.config.label2id
    from huggingface_hub import hf_hub_download

    tok = BertWordPieceTokenizer(hf_hub_download(BASE, "vocab.txt", revision=BASE_REVISION), lowercase=True)
    dev = torch.device(a.device)
    model.to(dev).train()
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr)

    def encode(row):
        e = tok.encode(row["text"])
        tags = ["O"] * len(row["text"])
        for s, t, lab in row["entities"]:
            for k in range(s, t):
                tags[k] = ("B-" if k == s else "I-") + lab
        labels = [-100 if o1 <= o0 else label2id.get(tags[o0], label2id["O"]) for o0, o1 in e.offsets]
        return e.ids, labels

    data = [encode(r) for r in rows]
    t0 = time.time()
    for ep in range(a.epochs):
        random.shuffle(data)
        for k in range(0, len(data), a.batch):
            chunk = data[k : k + a.batch]
            n = max(len(ids) for ids, _ in chunk)
            ids = torch.tensor([x + [0] * (n - len(x)) for x, _ in chunk], device=dev)
            mask = torch.tensor([[1] * len(x) + [0] * (n - len(x)) for x, _ in chunk], device=dev)
            lab = torch.tensor([y + [-100] * (n - len(y)) for _, y in chunk], device=dev)
            loss = model(input_ids=ids, attention_mask=mask, labels=lab).loss
            opt.zero_grad()
            loss.backward()
            opt.step()
            if (k // a.batch) % 50 == 0:
                print(f"第 {ep + 1} 轮 {k}/{len(data)}  损失 {loss.item():.4f}  {time.time() - t0:.0f} 秒", flush=True)
    a.out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(a.out / "hf")
    (a.out / "train.json").write_text(json.dumps({"base": BASE, "revision": BASE_REVISION, "sentences": len(rows), "epochs": a.epochs,
                                                   "sources": [str(p) for p in a.data]}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已保存到 {a.out}")


def export(run: Path, to: Path) -> None:
    import shutil

    import torch
    from huggingface_hub import hf_hub_download
    from onnxruntime.quantization import QuantType, quantize_dynamic
    from transformers import BertForTokenClassification

    model = BertForTokenClassification.from_pretrained(run / "hf").eval()
    to.mkdir(parents=True, exist_ok=True)
    ids = torch.ones(1, 16, dtype=torch.long)
    fp32 = to / "model.fp32.onnx"
    names = ["input_ids", "attention_mask", "token_type_ids"]
    torch.onnx.export(model, (ids, torch.ones_like(ids), torch.zeros_like(ids)), str(fp32), input_names=names, output_names=["logits"],
                      dynamic_axes={k: {0: "batch", 1: "tokens"} for k in [*names, "logits"]}, opset_version=17, dynamo=False)
    quantize_dynamic(str(fp32), str(to / "model.onnx"), weight_type=QuantType.QInt8)
    fp32.unlink()
    shutil.copy(run / "hf" / "config.json", to / "config.json")
    shutil.copy(hf_hub_download(BASE, "vocab.txt", revision=BASE_REVISION), to / "vocab.txt")
    print(f"已导出到 {to}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("corpus")
    c.add_argument("--out", type=Path, default=Path("train/data/ner-synth.jsonl"))
    c.add_argument("--n", type=int, default=4000)
    c.add_argument("--seed", type=int, default=11)
    f = sub.add_parser("fit")
    f.add_argument("--data", type=Path, action="append", required=True)
    f.add_argument("--out", type=Path, default=Path("train/runs/ner"))
    f.add_argument("--epochs", type=int, default=1)
    f.add_argument("--batch", type=int, default=16)
    f.add_argument("--lr", type=float, default=2e-5)
    f.add_argument("--device", default="mps")
    e = sub.add_parser("export")
    e.add_argument("run", type=Path)
    e.add_argument("--to", type=Path, required=True)
    a = ap.parse_args()
    if a.cmd == "corpus":
        corpus(a.out, a.n, a.seed)
    elif a.cmd == "fit":
        fit(a)
    else:
        export(a.run, a.to)


if __name__ == "__main__":
    main()
