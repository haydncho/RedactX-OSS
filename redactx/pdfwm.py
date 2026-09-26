"""PDF 结构层面的水印去除：在渲染之前直接删掉水印对象，不碰像素。

医院系统与 Acrobat 等工具添加的水印常以三种结构存在：
- 水印注释：/Annots 里 /Subtype /Watermark 的注释
- 标记内容：内容流里 /Artifact <</Subtype /Watermark ...>> BDC ... EMC 包住的一段
- 可选内容（图层）：名称含“水印 / watermark”的 OCG，以 /OC 标记内容或 XObject 的 /OC 挂在页面上
删掉它们之后再按原流程渲染、识别、打码；其余内容原样保留。
"""

from __future__ import annotations

import logging
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
    out, stack, changed = [], [], False
    for operands, op in ops:
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
