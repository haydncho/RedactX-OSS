import json

from redactx.detect import templates
from redactx.schemas import Char, Line, PageData


def _line(text, x, y, cw=30, h=30):
    return Line([Char(ch, (x + k * cw, y, x + (k + 1) * cw - 2, y + h)) for k, ch in enumerate(text)], "ocr")


ROWS = [("姓名：张三", 100, 200), ("性别：男", 700, 200), ("身份证号：110105198001011234", 100, 300), ("电话：13912345678", 100, 400), ("住院号：ZY2025123456", 700, 400)]


def _page(scale=1.0, dx=0, dy=0, broken=()):
    lines = []
    for text, x, y in ROWS:
        ln = _line(text, int(x * scale + dx), int(y * scale + dy), cw=int(30 * scale), h=int(30 * scale))
        if text.split("：")[0] in broken:
            for c in ln.chars[: text.index("：")]:
                c.ch = "□"
        lines.append(ln)
    return PageData(0, 1654, 2339, lines)


def test_template_recovers_field_with_unreadable_label(monkeypatch, tmp_path):
    (tmp_path / "t.json").write_text(json.dumps(templates.build(_page(), "测试表"), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(templates.settings, "templates_dir", tmp_path)
    templates._load_all.cache_clear()
    # 另一次扫描：整体略放大、平移，身份证号的字段名认不出
    page = _page(1.05, 20, 35, broken=("身份证号",))
    hits, fields = templates.apply(page, {"PERSON", "ID_CARD", "PHONE", "MEDICAL_ID"}, [])
    assert [(h.type, h.value) for h in hits] == [("ID_CARD", "110105198001011234")]
    assert any(f[1] == "身份证号" for f in fields)
    templates._load_all.cache_clear()


def test_no_match_when_layout_differs(monkeypatch, tmp_path):
    (tmp_path / "t.json").write_text(json.dumps(templates.build(_page(), "测试表"), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(templates.settings, "templates_dir", tmp_path)
    templates._load_all.cache_clear()
    # 同样的字段名，但位置完全不同：拟合偏差大，不套用模板
    lines = [_line(t, x, y) for (t, _, _), (x, y) in zip(ROWS, [(900, 1500), (100, 900), (300, 100), (1200, 300), (100, 2000)])]
    for c in lines[2].chars[:4]:
        c.ch = "□"
    assert templates.apply(PageData(0, 1654, 2339, lines), {"PERSON", "ID_CARD", "PHONE", "MEDICAL_ID"}, []) == ([], [])
    templates._load_all.cache_clear()
