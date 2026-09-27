"""生成一组示例文档，用来试用、演示或联调。全部为虚构数据：姓名、机构、号码随机生成，不对应任何真实人员与机构。

    python -m bench.samples                 # 写到 samples/（已加入 .gitignore）
    python -m bench.samples --out 目录 --quick --truth

默认生成：
  住院全套材料-系统导出.pdf   38 页：病历、医嘱、护理、检验检查、手术麻醉、费用明细、医保结算清单、收费票据（文字层，部分页带院名水印）
  住院全套材料-扫描件.pdf     37 页：同类材料的扫描件（白纸、牛皮纸、低质量扫描、手机拍照、手写签名、印章）
  病案-系统导出.pdf 等        各种形态的 4–8 页病案：系统导出、带水印、白纸扫描、手机拍照、难点扫描、患者信息登记表
  出院小结.md、病程记录.txt   Markdown 与纯文本（服务会先转成 PDF 再脱敏）
--quick 跳过两份住院全套材料（最耗时）；--truth 另存每份的标准答案（应遮、应留的位置与类型）。
需要 bench 的依赖：uv pip install -e ".[bench]"
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
from datetime import timedelta
from pathlib import Path

from . import longdoc, make
from .fakes import fmt_date, make_case

# (文件名, bench.make 的形态, 病案种子)
SHORT = [
    ("病案-系统导出", "text", 11),
    ("病案-系统导出带水印", "textwm", 12),
    ("病案-白纸扫描", "scan", 13),
    ("病案-手机拍照", "photo", 14),
    ("病案-难点扫描", "hard", 15),
    ("患者信息登记表", "idtext", 16),
]
LONG = [
    ("住院全套材料-系统导出", 301, "chole", "text"),
    ("住院全套材料-扫描件", 302, "appe", "scan"),
]


def markdown_sample(seed: int) -> str:
    c = make_case(seed)
    return f"""# {c.hospital} 出院小结

| 项目 | 内容 |
|---|---|
| 姓名 | {c.patient} |
| 性别 / 年龄 | {c.sex} / {c.age}岁 |
| 身份证号 | {c.id_card} |
| 住院号 | {c.inpatient_no} |
| 联系电话 | {c.phone} |
| 入院日期 | {fmt_date(c.admit)} |
| 出院日期 | {fmt_date(c.discharge)} |

## 出院诊断

{c.diagnosis[0]}（{c.diagnosis[1]}）

## 诊疗经过

患者因病情于{fmt_date(c.admit)}入住{c.dept}，由{c.staff["主治医师"]}主治医师负责诊治。住院期间家属{c.contact}（电话 {c.contact_phone}）陪护。
经治疗后病情好转，于{fmt_date(c.discharge)}出院。

## 出院医嘱

1. 注意休息，定期复查；
2. 如有不适，请拨打 {c.hospital_tel} 或到门诊就诊。

主治医师：{c.staff["主治医师"]}　　住院医师：{c.staff["住院医师"]}
"""


def text_sample(seed: int) -> str:
    c = make_case(seed)
    d1, d2 = c.admit + timedelta(days=1), c.admit + timedelta(days=2)
    return (f"{c.hospital}　病程记录\n\n"
            f"姓名：{c.patient}　性别：{c.sex}　年龄：{c.age}岁　住院号：{c.inpatient_no}　床号：{c.bed}\n\n"
            f"{fmt_date(c.admit)}　首次病程记录\n"
            f"患者{c.patient}因“{c.diagnosis[0]}”入院，现住址{c.address}，联系电话{c.phone}。\n"
            f"入院后完善相关检查，{c.staff['主治医师']}主治医师查房后制定诊疗方案。\n\n"
            f"{fmt_date(d1)}　主任医师查房\n"
            f"{c.staff['主任医师']}主任医师查房：患者病情平稳，继续当前治疗。家属{c.relative_in_prose}表示理解。\n\n"
            f"{fmt_date(d2)}　日常病程\n"
            f"患者一般情况可，饮食睡眠好。　记录医师：{c.staff['住院医师']}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="生成示例文档（全部为虚构数据）")
    ap.add_argument("--out", default="samples", type=Path, help="输出目录，默认 samples/")
    ap.add_argument("--quick", action="store_true", help="跳过两份 30 多页的住院全套材料")
    ap.add_argument("--truth", action="store_true", help="另存标准答案 JSON（<文件名>.truth.json）")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    made: list[tuple[str, int]] = []

    def truth(name: str, items: list[dict], pages: int) -> None:
        if a.truth:
            (a.out / f"{name}.truth.json").write_text(json.dumps({"pages": pages, "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")

    if not a.quick:
        for name, seed, scen, mode in LONG:
            n, items = longdoc.build(a.out / f"{name}.pdf", seed, scen, mode)
            truth(name, items, n)
            made.append((f"{name}.pdf", n))
            print(f"{name}.pdf  {n} 页", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        for name, variant, seed in SHORT:
            pdf = make.build(seed, variant, Path(tmp))
            shutil.move(pdf, a.out / f"{name}.pdf")
            meta = json.loads((Path(tmp) / "truth" / f"{pdf.stem}.json").read_text(encoding="utf-8"))
            truth(name, meta["items"], meta["pages"])
            made.append((f"{name}.pdf", meta["pages"]))
            print(f"{name}.pdf  {meta['pages']} 页", flush=True)
    (a.out / "出院小结.md").write_text(markdown_sample(21), encoding="utf-8")
    (a.out / "病程记录.txt").write_text(text_sample(22), encoding="utf-8")
    made += [("出院小结.md", 0), ("病程记录.txt", 0)]
    print(f"\n已生成 {len(made)} 份示例文档到 {a.out}/，用时 {time.time() - t0:.0f} 秒。全部为虚构数据，可放心上传试用。")


if __name__ == "__main__":
    main()
