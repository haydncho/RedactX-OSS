"""PDF 结构层面的水印去除：在渲染之前直接删掉水印对象，不碰像素。

医院系统与 Acrobat 等工具添加的水印常以三种结构存在：
- 水印注释：/Annots 里 /Subtype /Watermark 的注释
- 标记内容：内容流里 /Artifact <</Subtype /Watermark ...>> BDC ... EMC 包住的一段
- 可选内容（图层）：名称含“水印 / watermark”的 OCG，以 /OC 标记内容或 XObject 的 /OC 挂在页面上
- 没有任何标记的斜向浅色文字：文字矩阵倾斜（不是横排、竖排），并且半透明或用浅色填充的 BT…ET 文字块。
  压在照片上的这种水印按像素擦不掉（底色不是纸色），只能在结构层面删
删掉它们之后再按原流程渲染、识别、打码；其余内容原样保留。
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import pikepdf

log = logging.getLogger("redactx.pdfwm")

WM_KEYWORDS = ("watermark", "水印")


def _is_wm_name(value) -> bool:
    try:
        s = str(value).lower()
    except Exception:
        return False
    return any(k in s for k in WM_KEYWORDS)


def _wm_ocgs(pdf: pikepdf.Pdf) -> set[tuple[int, int]]:
    """名称含水印字样的可选内容组（OCG）。"""
    out: set[tuple[int, int]] = set()
    props = pdf.Root.get("/OCProperties")
    if props is None:
        return out
    for ocg in props.get("/OCGs", []):
        try:
            if _is_wm_name(ocg.get("/Name", "")):
                out.add(ocg.objgen)
        except Exception:
            continue
    return out


def _refers_wm_ocg(obj, wm_ocgs: set[tuple[int, int]]) -> bool:
    """obj 是水印 OCG，或是只由水印 OCG 构成的可选内容成员字典（OCMD）。"""
    if obj is None:
        return False
    try:
        if obj.is_indirect and obj.objgen in wm_ocgs:
            return True
        if obj.get("/Type") == "/OCMD":
            ocgs = obj.get("/OCGs")
            members = list(ocgs) if isinstance(ocgs, pikepdf.Array) else [ocgs]
            return bool(members) and all(m is not None and m.is_indirect and m.objgen in wm_ocgs for m in members)
    except Exception:
        return False
    return False


def _section_is_wm(op: str, operands, props_res, wm_ocgs) -> bool:
    """BDC / BMC 开始的标记内容段是否属于水印。"""
    if op == "BMC":
        return bool(operands) and _is_wm_name(operands[0])
    if op != "BDC" or len(operands) < 2:
        return False
    tag, props = operands[0], operands[1]
    if isinstance(props, pikepdf.Name):  # 引用页面资源 /Properties 里的字典
        props = props_res.get(props) if props_res is not None else None
    if props is None:
        return False
    if tag == "/Artifact":
        try:
            return props.get("/Subtype") == "/Watermark"
        except Exception:
            return False
    if tag == "/OC":
        return _refers_wm_ocg(props, wm_ocgs)
    return False


SLANT_MIN = 10  # 文字矩阵倾斜超过这个角度（度，且离竖排也超过）才可能是水印


def _num(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _slanted_tm(operands) -> bool:
    if len(operands) < 2:
        return False
    deg = math.degrees(math.atan2(_num(operands[1]), _num(operands[0]))) % 90
    return SLANT_MIN < deg < 90 - SLANT_MIN


def _alpha(res, name) -> float:
    try:
        gs = res.ExtGState.get(name)
        return _num(gs.get("/ca", 1), 1.0) if gs is not None else 1.0
    except Exception:
        return 1.0


def _faint_fill(op: str, operands) -> bool | None:
    """填充色是否浅（灰度 ≥ 0.5）；不是填充色设置时返回 None。"""
    vals = [_num(v) for v in operands]
    if op == "g" and vals:
        return vals[0] >= 0.5
    if op == "rg" and len(vals) == 3:
        return sum(vals) / 3 >= 0.5
    if op == "k" and len(vals) == 4:
        return max(vals) <= 0.5
    return None


TEXT_SHOW = {"Tj", "TJ", "'", '"'}


def _slanted_text_ops(ops, res) -> tuple[set[int], int]:
    """斜向且半透明（或浅色填充）的 BT…ET 文字块里显示文字的操作的下标，以及这样的文字块数。
    只删显示文字的操作：块里设置的颜色、字体等状态照旧保留，不影响后面的内容。"""
    drop: set[int] = set()
    blocks = 0
    alpha, faint = 1.0, False  # 当前的填充透明度、填充色是否浅；随 q/Q 入栈出栈
    saved: list[tuple[float, bool]] = []
    shows: list[int] | None = None  # 当前文字块里显示文字的操作
    slanted = False
    for idx, (operands, op) in enumerate(ops):
        name = str(op)
        if name == "q":
            saved.append((alpha, faint))
        elif name == "Q" and saved:
            alpha, faint = saved.pop()
        elif name == "gs" and operands and res is not None:
            alpha = _alpha(res, operands[0])
        elif _faint_fill(name, operands) is not None:
            faint = _faint_fill(name, operands)
        elif name == "BT":
            shows, slanted = [], False
        elif shows is not None:
            if name == "Tm" and _slanted_tm(operands):
                slanted = True
            elif name in TEXT_SHOW:
                shows.append(idx)
            elif name == "ET":
                if slanted and shows and (alpha < 0.9 or faint):
                    drop.update(shows)
                    blocks += 1
                shows = None
    return drop, blocks


def _strip_page(pdf: pikepdf.Pdf, page: pikepdf.Page, wm_ocgs) -> int:
    removed = 0
    # 1. 水印注释
    annots = page.obj.get("/Annots")
    if annots is not None:
        keep = [a for a in annots if a.get("/Subtype") != "/Watermark"]
        if len(keep) != len(annots):
            removed += len(annots) - len(keep)
            if keep:
                page.obj.Annots = pikepdf.Array(keep)
            else:
                del page.obj["/Annots"]
    # 2. 内容流里的水印段，以及挂在水印图层上的 XObject
    res = page.obj.get("/Resources")
    props_res = res.get("/Properties") if res is not None else None
    wm_xobjects = set()
    if res is not None and res.get("/XObject") is not None:
        for name, xo in res.XObject.items():
            try:
                if _refers_wm_ocg(xo.get("/OC"), wm_ocgs):
                    wm_xobjects.add(name)
            except Exception:
                continue
    try:
        ops = pikepdf.parse_content_stream(page)
    except Exception as e:  # 内容流损坏：不动它
        log.warning("内容流无法解析，跳过水印对象去除：%s", type(e).__name__)
        return removed
    drop, blocks = _slanted_text_ops(ops, res)
    removed += blocks
    out, stack, changed = [], [], bool(drop)
    for idx, (operands, op) in enumerate(ops):
        if idx in drop:
            continue
        name = str(op)
        inside = bool(stack) and stack[-1]
        if name in ("BDC", "BMC"):
            wm = inside or _section_is_wm(name, operands, props_res, wm_ocgs)
            if wm and not inside:
                removed += 1
            stack.append(wm)
            if wm:
                changed = True
                continue
        elif name == "EMC":
            was = stack.pop() if stack else False
            if was:
                continue
        elif inside:
            continue
        elif name == "Do" and operands and str(operands[0]) in wm_xobjects:
            removed += 1
            changed = True
            continue
        out.append((operands, op))
    if changed:
        page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(out))
    return removed


def strip_watermarks(src: Path, dst: Path, password: str | None = None) -> int:
    """删除 src 里的水印对象，有删除时写出到 dst 并返回删除的数量；没有水印对象时返回 0，不写文件。"""
    with pikepdf.open(src, password=password or "") as pdf:
        wm_ocgs = _wm_ocgs(pdf)
        n = sum(_strip_page(pdf, page, wm_ocgs) for page in pdf.pages)
        if not n:
            return 0
        # 水印图层在默认视图里也关掉（不在内容流里的其余引用）
        props = pdf.Root.get("/OCProperties")
        if wm_ocgs and props is not None and props.get("/D") is not None:
            off = [o for o in props.get("/OCGs", []) if o.is_indirect and o.objgen in wm_ocgs]
            props.D.OFF = pikepdf.Array(list(props.D.get("/OFF", [])) + off)
        pdf.save(dst)
    return n
