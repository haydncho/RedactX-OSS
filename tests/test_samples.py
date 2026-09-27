"""示例数据：住院全套材料的费用明细、结算清单与支付拆分金额一致（不渲染页面，不依赖字体）。"""

import pytest

pytest.importorskip("fontTools")  # bench 的依赖（.[bench]），CI 只装 .[dev] 时跳过
from bench import longdoc  # noqa: E402


@pytest.mark.parametrize("seed,scen", [(301, "chole"), (302, "appe")])
def test_fee_totals_consistent(seed, scen):
    c, x = longdoc.setup(seed, scen)
    items = longdoc.fee_items(c, x)
    s = longdoc.settle(items, x)
    assert len(items) > 60
    assert abs(sum(it["amt"] for it in items) - s["total"]) < 0.01
    assert abs(sum(e["total"] for e in s["by"].values()) - s["total"]) < 0.01
    assert abs(s["a"] + s["b"] + s["c"] - s["total"]) < 0.01  # 甲类 + 乙类 + 自费 = 总额
    assert abs(s["fund"] + s["personal"] - s["total"]) < 0.01  # 统筹支付 + 个人负担 = 总额
    assert abs(s["acct"] + s["cash"] - s["personal"]) < 0.01  # 个人账户 + 现金 = 个人负担
    assert c.discharge > x["op_day"] > c.admit


def test_rmb_uppercase():
    assert longdoc.to_rmb(12302.68) == "壹万贰仟叁佰零贰元陆角捌分"
    assert longdoc.to_rmb(5000) == "伍仟元整"
