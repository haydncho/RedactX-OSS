"""运行配置，全部来自环境变量，便于本机与容器共用一套代码。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

_FONT_CANDIDATES = [
    # 容器内：apt 安装的 Noto Sans CJK
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
    # macOS 本机
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
]


def _find_font() -> str | None:
    env = os.environ.get("REDACTX_FONT")
    if env and Path(env).exists():
        return env
    for p in _FONT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("REDACTX_DATA", "data")).resolve())
    render_dpi: int = int(os.environ.get("REDACTX_DPI", "200"))
    retention_hours: float = float(os.environ.get("REDACTX_RETENTION_HOURS", "24"))
    workers: int = int(os.environ.get("REDACTX_WORKERS", "1"))
    # 单个任务内第二遍（打码、自检、保存）同时处理的页数；每多一页内存峰值约多 0.5 GB
    page_workers: int = int(os.environ.get("REDACTX_PAGE_WORKERS", "2"))
    api_key: str | None = os.environ.get("REDACTX_API_KEY") or None
    max_upload_mb: int = int(os.environ.get("REDACTX_MAX_UPLOAD_MB", "30"))
    max_pages: int = int(os.environ.get("REDACTX_MAX_PAGES", "500"))
    sync_max_pages: int = 10
    sync_max_mb: int = 20
    font_path: str | None = field(default_factory=_find_font)
    # 正文人名识别模型目录；不存在时该功能关闭
    ner_dir: Path = field(default_factory=lambda: Path(os.environ.get("REDACTX_NER_DIR", Path(__file__).resolve().parent.parent / "models" / "ner")))
    ner_threads: int = int(os.environ.get("REDACTX_NER_THREADS", "4"))
    # 常用表单模板目录（字段锚定的降级方案）；没有模板时不起作用
    # 签名、印章检测模型目录（可选）；不存在时不启用
    detector_dir: Path = field(default_factory=lambda: Path(os.environ.get("REDACTX_DETECTOR_DIR", Path(__file__).resolve().parent.parent / "models" / "detector")))
    # 导出标注（打码前的原始页面 + 复核后的框）：含真实病案内容，默认关闭，只在标注环境里打开
    allow_export: bool = os.environ.get("REDACTX_ALLOW_EXPORT", "") == "1"
    templates_dir: Path = field(default_factory=lambda: Path(os.environ.get("REDACTX_TEMPLATES", Path(__file__).resolve().parent.parent / "templates")))

    def __post_init__(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
