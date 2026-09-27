"""模型文件完整性：导出、训练脚本在模型目录写入 SHA256SUMS，加载前逐个核对。

模型决定哪些内容会被遮盖：文件被替换或损坏时宁可报错，也不能悄悄少遮。
没有 SHA256SUMS 的旧目录照常加载，只记一条警告。
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

log = logging.getLogger("redactx.integrity")
SUMS = "SHA256SUMS"


class ModelTampered(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_sums(d: Path, names: list[str]) -> None:
    """在模型目录写入 SHA256SUMS（格式同 sha256sum 命令，可用 sha256sum -c 核对）。"""
    lines = [f"{sha256(d / n)}  {n}" for n in sorted(names)]
    (d / SUMS).write_text("\n".join(lines) + "\n", encoding="utf-8")


def verify_dir(d: Path) -> None:
    """核对模型目录；不一致时抛 ModelTampered。"""
    sums = d / SUMS
    if not sums.exists():
        log.warning("模型目录 %s 没有 %s，无法核对模型文件是否被改动；重新导出模型可生成", d, SUMS)
        return
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, name = line.split(None, 1)
        name = name.strip().lstrip("*")
        f = d / name
        if Path(name).name != name or not f.is_file() or sha256(f) != digest:
            raise ModelTampered(f"模型文件与 {SUMS} 不符：{d.name}/{name}。请重新导出或训练模型")
