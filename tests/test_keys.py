"""按 API Key 隔离任务：用户 Key 只能看到、操作自己的任务；管理员看全部；Key 只存哈希、不重复；错误次数限制。"""

import io
import sqlite3

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

app_mod = pytest.importorskip("service.app")
ADMIN_KEY = "admin-secret-for-tests"


@pytest.fixture
def env(monkeypatch, tmp_path):
    from service.jobs import JobStore
    from service.keys import KeyStore

    monkeypatch.setattr(app_mod.settings, "data_dir", tmp_path)
    monkeypatch.setattr(app_mod.settings, "api_key", ADMIN_KEY)
    store = JobStore(tmp_path)
    monkeypatch.setattr(store.pool, "submit", lambda *a, **k: None)  # 只建任务，不真正处理
    monkeypatch.setattr(app_mod, "store", store)
    keys = KeyStore(store.db_path)
    monkeypatch.setattr(app_mod, "keys", keys)
    app_mod._fails.clear()
    return TestClient(app_mod.app), store, keys, tmp_path


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (200, 100), "white").save(buf, "PNG")
    return buf.getvalue()


def _submit(c, key):
    r = c.post("/v1/jobs", files={"file": ("a.png", _png(), "image/png")}, headers={"X-API-Key": key})
    assert r.status_code == 202, r.text
    return r.json()["job_id"]


def test_jobs_isolated_between_keys(env):
    c, _, keys, _ = env
    _, ka = keys.create("甲")
    _, kb = keys.create("乙")
    ja, jb = _submit(c, ka), _submit(c, kb)
    H = lambda k: {"X-API-Key": k}  # noqa: E731
    assert [j["id"] for j in c.get("/v1/jobs", headers=H(ka)).json()] == [ja]
    assert [j["id"] for j in c.get("/v1/jobs", headers=H(kb)).json()] == [jb]
    # 别人的任务：状态、报告、预览、结果、复核、删除一律 404
    for method, path in [("get", f"/v1/jobs/{jb}"), ("get", f"/v1/jobs/{jb}/report"), ("get", f"/v1/jobs/{jb}/preview/1?v=before"),
                         ("get", f"/v1/jobs/{jb}/result"), ("put", f"/v1/jobs/{jb}/review"), ("post", f"/v1/jobs/{jb}/review/finish"),
                         ("delete", f"/v1/jobs/{jb}")]:
        kw = {"json": {"items": []}} if method == "put" else {}
        r = getattr(c, method)(path, headers=H(ka), **kw)
        assert r.status_code == 404, (method, path, r.status_code)
    assert c.get(f"/v1/jobs/{ja}", headers=H(ka)).status_code == 200
    assert "owner" not in c.get(f"/v1/jobs/{ja}", headers=H(ka)).json()
    # 管理员看全部
    assert {j["id"] for j in c.get("/v1/jobs", headers=H(ADMIN_KEY)).json()} == {ja, jb}
    assert c.get(f"/v1/jobs/{jb}", headers=H(ADMIN_KEY)).status_code == 200


def test_bad_and_revoked_keys(env):
    c, _, keys, _ = env
    kid, key = keys.create("丙")
    assert c.get("/v1/jobs", headers={"X-API-Key": "rx_nope"}).status_code == 401
    assert c.get("/v1/jobs").status_code == 401
    assert c.get("/v1/jobs", headers={"X-API-Key": key}).status_code == 200
    assert keys.revoke(kid)
    assert c.get("/v1/jobs", headers={"X-API-Key": key}).status_code == 401


def test_keys_stored_hashed_and_unique(env):
    _, store, keys, _ = env
    made = [keys.create(f"k{i}")[1] for i in range(50)]
    assert len(set(made)) == 50 and all(k.startswith("rx_") for k in made)
    with sqlite3.connect(store.db_path) as db:
        dump = "\n".join(str(r) for r in db.execute("SELECT * FROM api_keys"))
    assert not any(k in dump for k in made)  # 只存哈希
    # 与已有哈希冲突时会重新生成，而不是报错或覆盖
    import service.keys as km

    seq = iter([made[0][3:], "fresh-token-value-000000000000"])
    orig = km.secrets.token_urlsafe
    km.secrets.token_urlsafe = lambda n: next(seq)
    try:
        _, k2 = keys.create("撞车")
    finally:
        km.secrets.token_urlsafe = orig
    assert k2 == "rx_fresh-token-value-000000000000"


def test_new_key_never_equals_admin_key(env):
    _, _, keys, _ = env
    import service.keys as km

    seq = iter(["same", "other"])
    orig = km.secrets.token_urlsafe
    km.secrets.token_urlsafe = lambda n: next(seq)
    try:
        _, k = keys.create("x", admin_key="rx_same")
    finally:
        km.secrets.token_urlsafe = orig
    assert k == "rx_other"


def test_export_admin_only(env, monkeypatch):
    c, _, keys, _ = env
    monkeypatch.setattr(app_mod.settings, "allow_export", True)
    _, key = keys.create("丁")
    job = _submit(c, key)
    r = c.get(f"/v1/jobs/{job}/export", headers={"X-API-Key": key})
    assert r.status_code == 403 and r.json()["error"]["code"] == "EXPORT_ADMIN_ONLY"


def test_too_many_failures_rate_limited(env):
    c, _, keys, _ = env
    _, key = keys.create("戊")
    for _ in range(app_mod._FAIL_MAX):
        assert c.get("/v1/jobs", headers={"X-API-Key": "wrong"}).status_code == 401
    assert c.get("/v1/jobs", headers={"X-API-Key": "wrong"}).status_code == 429
    assert c.get("/v1/jobs", headers={"X-API-Key": key}).status_code == 429  # 同一来源暂时全部拒绝


def test_admin_cli(env, capsys):
    from service import admin

    _, _, keys, tmp = env
    assert admin.main(["keys", "create", "演示"]) == 0
    out = capsys.readouterr().out
    key = next(w for w in out.split() if w.startswith("rx_"))
    assert keys.verify(key)
    admin.main(["keys", "list"])
    assert "演示" in capsys.readouterr().out and key not in capsys.readouterr().out
    assert admin.main(["jobs", "clear"]) == 1  # 没有 --yes 不删
    assert admin.main(["keys", "clear", "--yes"]) == 0
    assert keys.verify(key) is None


def test_local_mode_without_any_key(env, monkeypatch):
    c, _, _, _ = env
    monkeypatch.setattr(app_mod.settings, "api_key", None)
    assert c.get("/v1/jobs").status_code == 200  # 没设管理员 Key、也没生成用户 Key：本机模式


def test_file_name_kept_per_key_and_dropped_with_job(env):
    # 原文件名随任务存在服务端，各设备都能在“最近任务”里看到；只给提交它的 Key，删除任务时一并删除
    c, store, keys, _ = env
    _, ka = keys.create("甲")
    _, kb = keys.create("乙")
    r = c.post("/v1/jobs", files={"file": ("C:\\病案\\张三-住院.png", _png(), "image/png")}, headers={"X-API-Key": ka})
    ja = r.json()["job_id"]
    H = lambda k: {"X-API-Key": k}  # noqa: E731
    assert [j["name"] for j in c.get("/v1/jobs", headers=H(ka)).json()] == ["张三-住院.png"]  # 去掉客户端路径
    assert c.get(f"/v1/jobs/{ja}", headers=H(ka)).json()["name"] == "张三-住院.png"
    assert c.get("/v1/jobs", headers=H(kb)).json() == []
    store.delete(ja)  # 到期清理与删除接口都走这里（排队中的任务接口不让删）
    with store._conn() as db:
        assert db.execute("SELECT COUNT(*) FROM jobs WHERE name IS NOT NULL").fetchone()[0] == 0


def test_clean_name():
    from service.jobs import clean_name

    assert clean_name("/tmp/a/b.pdf") == "b.pdf"
    assert clean_name("x\u0000\u200by.pdf") == "xy.pdf"
    assert clean_name("") is None and clean_name(None) is None
    assert len(clean_name("长" * 500)) == 200


def test_old_db_gets_name_column(tmp_path):
    from service.jobs import JobStore

    with sqlite3.connect(tmp_path / "jobs.sqlite3") as db:  # 没有 name 列的旧库
        db.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, "
                   "expires REAL NOT NULL, input_ext TEXT, pages INTEGER, progress REAL DEFAULT 0, message TEXT, error_code TEXT, "
                   "error TEXT, options TEXT, summary TEXT, owner TEXT)")
    store = JobStore(tmp_path)
    with store._conn() as db:
        assert "name" in {r[1] for r in db.execute("PRAGMA table_info(jobs)")}
