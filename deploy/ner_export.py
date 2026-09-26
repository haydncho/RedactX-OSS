"""导出正文人名识别模型：下载固定版本的 shibing624/bert4ner-base-chinese（Apache-2.0），导出 ONNX 并做 int8 量化。

    uv pip install --python .venv/bin/python -e ".[ner-export]"
    .venv/bin/python deploy/ner_export.py            # 写入 models/ner/

只在准备模型时联网一次；服务运行时只读 models/ner/ 下的文件（model.onnx、vocab.txt、config.json），不再联网。
容器镜像构建时把 models/ner/ 复制进镜像。
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

REPO = "shibing624/bert4ner-base-chinese"
REVISION = "5d660ed2aa9da482bf2d99c6bc8cf2ce66758f6a"  # 固定版本，换版本须重新跑 bench.prose 与 bench.evaluate
NOTICE = f"""正文人名识别模型
来源：https://huggingface.co/{REPO}（版本 {REVISION}）
许可证：Apache-2.0，作者 shibing624（https://github.com/shibing624/nerpy）
本目录的 model.onnx 由上述权重导出为 ONNX 并做 int8 动态量化，未做其他修改。
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "models" / "ner")
    a = ap.parse_args()

    import torch
    from huggingface_hub import snapshot_download
    from onnxruntime.quantization import QuantType, quantize_dynamic
    from transformers import BertForTokenClassification

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(snapshot_download(REPO, revision=REVISION, local_dir=Path(tmp) / "hf",
                                     allow_patterns=["config.json", "vocab.txt", "model.safetensors", "README.md"]))
        model = BertForTokenClassification.from_pretrained(src).eval()
        ids = torch.ones(1, 16, dtype=torch.long)
        fp32 = Path(tmp) / "model.fp32.onnx"
        names = ["input_ids", "attention_mask", "token_type_ids"]
        torch.onnx.export(model, (ids, torch.ones_like(ids), torch.zeros_like(ids)), str(fp32), input_names=names, output_names=["logits"],
                          dynamic_axes={k: {0: "batch", 1: "tokens"} for k in [*names, "logits"]}, opset_version=17, dynamo=False)
        a.out.mkdir(parents=True, exist_ok=True)
        quantize_dynamic(str(fp32), str(a.out / "model.onnx"), weight_type=QuantType.QInt8)
        for f in ("config.json", "vocab.txt"):
            shutil.copy(src / f, a.out / f)
    (a.out / "NOTICE").write_text(NOTICE, encoding="utf-8")
    print(f"已写入 {a.out}：" + "，".join(sorted(p.name for p in a.out.iterdir())))


if __name__ == "__main__":
    main()
