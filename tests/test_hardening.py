"""服务端加固：安全响应头、上传失败清理、自定义词不落库、像素上限、临时文件清扫。"""

import io
import json
import os
import time

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

app_mod = pytest.importorskip("service.app")


@pytest.fixture
def client(monkeypatch, tmp_path):
    from service.jobs import JobStore

    monkeypatch.setattr(app_mod.settings, "data_dir", tmp_path)
    monkeypatch.setattr(app_mod.settings, "api_key", None)
    store = JobStore(tmp_path)
    monkeypatch.setattr(app_mod, "store", store)
    return TestClient(app_mod.app), store, tmp_path


def test_security_headers(client):
    c, _, _ = client
    page = c.get("/")
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    api = c.get("/v1/health")
    assert api.headers["cache-control"] == "no-store"
    assert api.headers["referrer-policy"] == "no-referrer"


def test_corrupt_upload_leaves_no_temp_file(client):
    c, _, tmp = client
    bad = b"BM" + b"\x00" * 64  # 文件头像 BMP，内容无法解析
    r = c.post("/v1/jobs", files={"file": ("a.bmp", bad, "image/bmp")})
    assert r.status_code == 400
    assert not [p for p in tmp.iterdir() if p.is_file() and p.name != "jobs.sqlite3"]


def test_custom_words_not_stored(client, monkeypatch):
    c, store, _ = client
    monkeypatch.setattr(store.pool, "submit", lambda *a, **k: None)  # 不真正处理
    buf = io.BytesIO()
    Image.new("RGB", (200, 100), "white").save(buf, "PNG")
    opts = {"custom_words": ["张三丰", "李四光"]}
    r = c.post("/v1/jobs", files={"file": ("a.png", buf.getvalue(), "image/png")}, data={"options": json.dumps(opts)})
    job_id = r.json()["job_id"]
    body = c.get(f"/v1/jobs/{job_id}").text
    assert "张三丰" not in body and "李四光" not in body
    with store._conn() as db:
        row = db.execute("SELECT options FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
    assert "张三丰" not in row and json.loads(row)["custom_words"] == 2


def test_oversized_image_rejected(client):
    c, _, _ = client
    from redactx import ingest

    side = int((ingest.MAX_PAGE_PIXELS * 1.2) ** 0.5)
    buf = io.BytesIO()
    Image.new("1", (side, side)).save(buf, "PNG")  # 1 位图，文件很小、像素很多
    r = c.post("/v1/jobs", files={"file": ("big.png", buf.getvalue(), "image/png")})
    assert r.status_code == 400 and r.json()["error"]["code"] in ("TOO_LARGE", "INVALID_FILE")


def test_sweep_stale_temp(tmp_path):
    from service.jobs import JobStore

    old = tmp_path / "tmpabc.upload"
    old.write_bytes(b"x")
    os.utime(old, (time.time() - 7200,) * 2)
    fresh = tmp_path / "tmpnew.upload"
    fresh.write_bytes(b"x")
    keep = tmp_path / "notes.txt"
    keep.write_bytes(b"x")
    store = JobStore(tmp_path)  # 启动时清掉所有残留
    assert not old.exists() and not fresh.exists() and keep.exists()
    fresh.write_bytes(b"x")
    store._sweep_temp(3600)  # 运行中只清一小时以前的
    assert fresh.exists()
