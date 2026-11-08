import hashlib, json, argparse
from pathlib import Path
import numpy as np
import torch
import onnxruntime as ort
from rfdetr import RFDETRBase


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    root = args.source
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    if (
        hashlib.sha256((root / "checkpoint.pth").read_bytes()).hexdigest()
        != "3a831ae093be64a01a4d9c635dc6071ad7c43c3e95c3da0b33a17351df1e7909"
    ):
        raise RuntimeError("UPSTREAM_CHECKPOINT_INTEGRITY_FAILURE")
    with torch.serialization.safe_globals([argparse.Namespace]):
        ck = torch.load(root / "checkpoint.pth", map_location="cpu", weights_only=True)
    torch.set_num_threads(4)
    # The checkpoint has four focal-loss logits (no separate background logit).
    torch.save({"model": ck["model"]}, root / "tensors.pth")
    model = RFDETRBase(
        pretrain_weights=str(root / "tensors.pth"), num_classes=3, device="cpu"
    ).model.model
    model.load_state_dict(ck["model"], strict=True)
    model.eval()
    model.export()
    x = torch.randn(1, 3, 560, 560)
    with torch.no_grad():
        expected = [t.numpy() for t in model(x)]
    torch.onnx.export(
        model,
        (x,),
        str(out / "model.onnx"),
        input_names=["pixel_values"],
        output_names=["boxes", "logits"],
        opset_version=17,
        dynamo=False,
    )
    session = ort.InferenceSession(
        str(out / "model.onnx"), providers=["CPUExecutionProvider"]
    )
    actual = session.run(None, {"pixel_values": x.numpy()})
    for a, b in zip(expected, actual):
        np.testing.assert_allclose(a, b, rtol=0.005, atol=0.005)
    config = {
        "classes": ["stamp", "signature", "checkbox_checked", "checkbox_unchecked"],
        "size": 560,
        "mean": [0.485, 0.456, 0.406],
        "std": [0.229, 0.224, 0.225],
        "source": "bluecopa/rf-detr-stamp-signature-detector",
        "revision": "c59fd4f451b254501700a56c7769f1a3d788c753",
        "license": "Apache-2.0",
        "exporter": "rfdetr/1.2.1",
        "input": "RGB resize 560x560, ImageNet normalization",
        "torchOnnxParity": "passed",
    }
    (out / "config.json").write_text(json.dumps(config, indent=2))
    manifest = {
        "sha256": {
            n: hashlib.sha256((out / n).read_bytes()).hexdigest()
            for n in ["model.onnx", "config.json"]
        },
        "upstreamSha256": "3a831ae093be64a01a4d9c635dc6071ad7c43c3e95c3da0b33a17351df1e7909",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print("EXPORT_AND_PARITY_OK", manifest, flush=True)


if __name__ == "__main__":
    main()
