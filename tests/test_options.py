import pytest

app = pytest.importorskip("service.app")


@pytest.mark.parametrize(("raw", "want"), [(None, "auto"), ("{}", "auto"), ('{"verify": "auto"}', "auto"), ('{"verify": true}', True), ('{"verify": false}', False)])
def test_verify_modes(raw, want):
    assert app.parse_options(raw, None).verify == want


def test_style_resolution_order():
    from redactx.catalog import resolve_style
    from redactx.pipeline import Options

    # 不给任何样式：用各实体的推荐样式（签名为斜线、二维码为马赛克），不是一律标签
    o = Options()
    assert o.style_for("SIGNATURE") == "hatch" and o.style_for("QRCODE") == "mosaic" and o.style_for("PERSON") == "label"
    # 全局默认优先于推荐样式，单独指定的优先于全局默认
    o = Options(default_style="black", styles={"PERSON": "mosaic"})
    assert o.style_for("SIGNATURE") == "black" and o.style_for("PERSON") == "mosaic"
    # 复核重打码与自动打码同一规则
    assert resolve_style("SIGNATURE", {}, None) == Options().style_for("SIGNATURE")
    assert resolve_style("PERSON", {"PERSON": "nope"}) == "label"  # 不支持的样式退回标签
