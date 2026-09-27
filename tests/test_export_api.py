import json
import time

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

app_mod = pytest.importorskip("service.app")
from tests.test_review import _job  # noqa: E402


@pytest.fixture
def client(monkeypatch, tmp_path):
    from service.jobs import JobStore

    store = JobStore(tmp_path)
    monkeypatch.setattr(app_mod, "store", store)
    monkeypatch.setattr(app_mod.settings, "data_dir", tmp_path)
    job_id = "job_test0000000000000000"
    d = store.dir(job_id)
    out, _ = _job(d, editable=True)  # 在任务目录下建 out/
    now = time.time()
    with store._conn() as c:
        c.execute("INSERT INTO jobs (id,status,created,updated,expires,pages,progress,message) VALUES (?,?,?,?,?,?,?,?)",
                  (job_id, "succeeded", now, now, now + 3600, 1, 1.0, "完成"))
    return TestClient(app_mod.app), job_id, out, tmp_path


def test_export_disabled_by_default(client, monkeypatch):
    c, job_id, _, _ = client
    monkeypatch.setattr(app_mod.settings, "allow_export", False)
    assert c.get(f"/v1/jobs/{job_id}/export").json()["error"]["code"] == "EXPORT_DISABLED"


def test_export_requires_api_key(client, monkeypatch):
    c, job_id, _, _ = client
    monkeypatch.setattr(app_mod.settings, "allow_export", True)
    monkeypatch.setattr(app_mod.settings, "api_key", None)
    r = c.get(f"/v1/jobs/{job_id}/export")
    assert r.status_code == 403 and r.json()["error"]["code"] == "EXPORT_NEEDS_KEY"
    assert c.get("/v1/health").json()["export"] is False


def test_export_writes_audit_and_cleans_up(client, monkeypatch):
    c, job_id, out, root = client
    monkeypatch.setattr(app_mod.settings, "allow_export", True)
    monkeypatch.setattr(app_mod.settings, "api_key", "k")
    assert c.get(f"/v1/jobs/{job_id}/export").status_code == 401  # 没带 Key
    r = c.get(f"/v1/jobs/{job_id}/export", headers={"X-API-Key": "k"})
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert not list(out.glob("export-*.zip"))  # 发送后删除
    entry = json.loads((root / "export-audit.log").read_text(encoding="utf-8").strip().splitlines()[-1])
    assert entry["job_id"] == job_id and entry["pages"] == 1 and entry["boxes"] == 1


def test_upload_rejected_by_content_length_before_reading(client, monkeypatch):
    c, _, _, tmp = client
    monkeypatch.setattr(app_mod.settings, "api_key", None)
    monkeypatch.setattr(app_mod.settings, "max_upload_mb", 1)
    big = b"%PDF-1.4\n" + b"0" * (3 * 1024 * 1024)
    r = c.post("/v1/jobs", files={"file": ("a.pdf", big, "application/pdf")})
    assert r.status_code == 413 and r.json()["error"]["code"] == "TOO_LARGE"
    assert not list(tmp.glob("*.upload"))  # 没有先落盘


def test_health_reports_upload_limit(client):
    c, _, _, _ = client
    assert c.get("/v1/health").json()["max_upload_mb"] == app_mod.settings.max_upload_mb


@pytest.mark.parametrize("bad", ["..", "%2E%2E", "job_../../x", "job_TEST"])
def test_delete_rejects_path_traversal(client, monkeypatch, bad):
    c, job_id, _, tmp = client
    monkeypatch.setattr(app_mod.settings, "api_key", None)
    sentinel = tmp / "sentinel.txt"
    sentinel.write_text("keep")
    r = c.delete(f"/v1/jobs/{bad}")
    assert r.status_code == 404
    assert sentinel.exists() and (tmp / "jobs" / job_id).exists()
