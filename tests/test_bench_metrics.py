"""评估打分逻辑的单元测试。数据全部为虚构。"""

from bench import metrics


def _truth(*items):
    return {"items": [{"page": 1, "form": "print", "text": "", **it} for it in items]}


def _report(*boxes, type_="PERSON"):
    return {"items": [{"page": 1, "type": type_, "source": "rule", "box": list(b)} for b in boxes]}


def _status(scored):
    return [(it["type"], it["status"]) for it in scored["items"]]


def test_full_partial_miss():
    truth = _truth(
        {"type": "PERSON", "role": "redact", "box": [0.1, 0.1, 0.2, 0.12]},
        {"type": "PHONE", "role": "redact", "box": [0.3, 0.1, 0.5, 0.12]},
        {"type": "ORG", "role": "redact", "box": [0.1, 0.5, 0.4, 0.52]},
    )
    report = _report((0.09, 0.095, 0.21, 0.125), (0.3, 0.1, 0.4, 0.12))  # 姓名遮全，电话只遮一半，机构漏遮
    assert _status(metrics.score_doc(truth, report)) == [("PERSON", "full"), ("PHONE", "partial"), ("ORG", "miss")]


def test_union_of_boxes_counts():
    # 两个框各遮一半，合起来算遮全
    truth = _truth({"type": "ADDRESS", "role": "redact", "box": [0.1, 0.1, 0.5, 0.12]})
    report = _report((0.1, 0.1, 0.3, 0.12), (0.3, 0.1, 0.5, 0.12))
    assert _status(metrics.score_doc(truth, report)) == [("ADDRESS", "full")]


def test_keep_items_and_extras():
    truth = _truth(
        {"type": "DATE", "role": "keep", "box": [0.1, 0.3, 0.3, 0.32]},
        {"type": "DIAGNOSIS", "role": "keep", "box": [0.1, 0.4, 0.3, 0.42]},
        {"type": "PERSON", "role": "redact", "box": [0.6, 0.1, 0.62, 0.12]},
    )
    # 日期被遮（误遮）；短姓名的遮盖框按字高外扩，不算多遮；页面空白处的框算多遮
    report = _report((0.1, 0.3, 0.3, 0.32), (0.595, 0.095, 0.625, 0.125), (0.8, 0.8, 0.9, 0.85))
    scored = metrics.score_doc(truth, report)
    assert _status(scored) == [("DATE", "over"), ("DIAGNOSIS", "kept"), ("PERSON", "full")]
    assert len(scored["extras"]) == 2  # 遮日期的框与空白处的框
    assert {tuple(e["box"]) for e in scored["extras"]} == {(0.1, 0.3, 0.3, 0.32), (0.8, 0.8, 0.9, 0.85)}


def test_summarize_recall():
    scored = [
        {"type": "PERSON", "role": "redact", "status": "full"},
        {"type": "PERSON", "role": "redact", "status": "miss"},
        {"type": "DATE", "role": "keep", "status": "over"},
    ]
    s = metrics.summarize(scored)
    assert s["PERSON"]["recall"] == 0.5 and s["PERSON"]["miss"] == 1
    assert s["DATE"]["recall"] is None and s["DATE"]["over"] == 1
