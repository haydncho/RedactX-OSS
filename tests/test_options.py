import pytest

app = pytest.importorskip("service.app")


@pytest.mark.parametrize(("raw", "want"), [(None, "auto"), ("{}", "auto"), ('{"verify": "auto"}', "auto"), ('{"verify": true}', True), ('{"verify": false}', False)])
def test_verify_modes(raw, want):
    assert app.parse_options(raw, None).verify == want
