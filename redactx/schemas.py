"""中间数据契约。坐标一律为渲染后页面图像的像素坐标（左上角为原点）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Rect = tuple[float, float, float, float]  # x0, y0, x1, y1


@dataclass
class Char:
    ch: str
    box: Rect
    score: float = 1.0


@dataclass
class Line:
    chars: list[Char]
    source: Literal["text", "ocr"]
    angle: float = 0.0  # 文字行的倾斜角（度，顺时针为正），斜向水印据此识别

    @property
    def text(self) -> str:
        return "".join(c.ch for c in self.chars)

    @property
    def box(self) -> Rect:
        xs0, ys0, xs1, ys1 = zip(*(c.box for c in self.chars))
        return min(xs0), min(ys0), max(xs1), max(ys1)

    @property
    def height(self) -> float:
        hs = sorted(c.box[3] - c.box[1] for c in self.chars)
        return hs[len(hs) // 2] if hs else 0.0


@dataclass
class ImageObject:
    rect: Rect
    px_size: tuple[int, int]
    digest: str


@dataclass
class PageData:
    index: int
    width: int
    height: int
    lines: list[Line] = field(default_factory=list)
    images: list[ImageObject] = field(default_factory=list)
    text_source: str = "ocr"
    rotation: int = 0  # 为识别而做的旋转角度（顺时针），坐标已换算回原图


@dataclass
class Hit:
    """文字类命中：某一行上的字符区间。"""

    type: str
    source: str
    page: int
    line: int
    start: int
    end: int
    value: str = ""  # 只在任务内存中使用，绝不写入报告和日志


@dataclass
class Region:
    """最终要遮盖的区域。"""

    type: str
    source: str
    page: int
    rect: Rect
    alias: str | None = None
    style: str | None = None
