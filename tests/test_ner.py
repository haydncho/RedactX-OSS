from redactx.detect import ner
from redactx.schemas import Char, Line, PageData


def _page(rows, width=20):
    lines = []
    for y, text in enumerate(rows):
        lines.append(Line([Char(ch, (k * 10, y * 20, k * 10 + 9, y * 20 + 12)) for k, ch in enumerate(text)], "text"))
    return PageData(index=0, width=400, height=400, lines=lines, text_source="text")


def test_is_name():
    assert ner._is_name("李建国") and ner._is_name("欧阳晓")
    assert not ner._is_name("李") and not ner._is_name("秀英") and not ner._is_name("王Ab")
    assert not ner._is_name("李建国华英")


def test_page_text_joins_wrapped_prose_and_skips_short_rows():
    rows = ["姓名：王五", "由家属张秀英（患者之女）陪同就诊，张秀", "英表示理解病情并同意治疗方案。"]
    text, where = ner._page_text(_page(rows))
    assert "姓名" not in text  # 短的字段行不送入模型
    assert "张秀英表示" in text  # 折行处直接相连
    k = text.index("英表示")
    assert where[k] == (2, 0)


def test_page_names_trims_title_and_splits_lines(monkeypatch):
    rows = ["由家属张秀英（患者之女）陪同就诊，张秀", "英表示理解病情。今日白主任查房，同意诊断。"]
    page = _page(rows)

    def fake(texts):
        out = []
        for t in texts:
            spans = []
            if "张秀英表" in t:
                spans.append((t.index("张秀英表"), t.index("张秀英表") + 3, 0.99))
            if "白主任" in t:
                spans.append((t.index("白主任"), t.index("白主任") + 2, 0.99))
            out.append(spans)
        return out

    monkeypatch.setattr(ner, "available", lambda: True)
    monkeypatch.setattr(ner, "find_names_batch", fake)
    hits = ner.page_names(page)
    assert [(h.line, h.start, h.end) for h in hits] == [(0, 17, 19), (1, 0, 1)]  # 跨行拆开；“白主”去掉“主”后只剩姓氏，丢弃
    assert all(h.type == "PERSON" and h.source == "ner" for h in hits)


def test_no_model_no_hits(monkeypatch, tmp_path):
    monkeypatch.setattr(ner, "_model", None)
    monkeypatch.setattr(ner.settings, "ner_dir", tmp_path)
    assert ner.page_names(_page(["由家属张秀英（患者之女）陪同就诊。"])) == []
    monkeypatch.setattr(ner, "_model", None)


def test_drug_name_prefix_is_not_a_person(monkeypatch):
    page = _page(["予林可霉素抗感染，注意观察有无皮疹。"])
    monkeypatch.setattr(ner, "available", lambda: True)
    monkeypatch.setattr(ner, "find_names_batch", lambda texts: [[(t.index("林可"), t.index("林可") + 2, 0.95)] for t in texts])
    assert ner.page_names(page) == []
