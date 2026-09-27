"""后台管理脚本：生成、列出、吊销 API Key，清空任务。在服务所在机器（或容器内）运行，直接读写数据目录里的库。

    python -m service.admin keys create 名称      生成用户 Key（明文只显示这一次；保证不与库里已有的、管理员 Key 重复）
    python -m service.admin keys list             列出 Key（只显示前缀）
    python -m service.admin keys revoke key_xxx   吊销，立即失效；--purge 同时删除它提交的任务
    python -m service.admin keys clear --yes      删除全部用户 Key
    python -m service.admin jobs clear --yes      删除全部任务（结果、报告、预览）

容器部署时用 deploy/keys.sh，参数相同。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time

from redactx.config import settings

from .keys import KeyStore


def _fmt(t: float | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "—"


def _job_ids(db, owner: str | None = None) -> list[str]:
    with sqlite3.connect(db) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='jobs'").fetchone():
            return []
        q = c.execute("SELECT id FROM jobs") if owner is None else c.execute("SELECT id FROM jobs WHERE owner=?", (owner,))
        return [r[0] for r in q]


def _delete_jobs(ids: list[str]) -> int:
    # 与服务使用同一套删除逻辑（校验 ID 格式、删库记录与目录）；不启动后台线程
    import shutil

    from .jobs import JOB_ID

    db = settings.data_dir / "jobs.sqlite3"
    n = 0
    with sqlite3.connect(db) as c:
        for job_id in ids:
            if not JOB_ID.fullmatch(job_id):
                continue
            n += c.execute("DELETE FROM jobs WHERE id=?", (job_id,)).rowcount
            shutil.rmtree(settings.data_dir / "jobs" / job_id, ignore_errors=True)
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m service.admin", description="锐消 RedactX 后台管理")
    sub = ap.add_subparsers(dest="what", required=True)
    k = sub.add_parser("keys", help="API Key").add_subparsers(dest="cmd", required=True)
    c = k.add_parser("create", help="生成用户 Key")
    c.add_argument("name", help="用途或使用人，如“演示-张医生”")
    k.add_parser("list", help="列出 Key")
    r = k.add_parser("revoke", help="吊销 Key")
    r.add_argument("id", help="Key 的 id（见 list）")
    r.add_argument("--purge", action="store_true", help="同时删除它提交的任务")
    kc = k.add_parser("clear", help="删除全部用户 Key")
    kc.add_argument("--yes", action="store_true", help="确认删除")
    j = sub.add_parser("jobs", help="任务").add_subparsers(dest="cmd", required=True)
    jc = j.add_parser("clear", help="删除全部任务")
    jc.add_argument("--yes", action="store_true", help="确认删除")
    a = ap.parse_args(argv)

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = settings.data_dir / "jobs.sqlite3"
    store = KeyStore(db)
    if a.what == "keys" and a.cmd == "create":
        kid, key = store.create(a.name, settings.api_key)
        print(f"已生成：{kid}（{a.name}）\n\n    {key}\n\n这是唯一一次显示完整 Key，请立即交给使用人；库里只存哈希，丢失后只能吊销再生成。")
    elif a.what == "keys" and a.cmd == "list":
        rows = store.list()
        if not rows:
            print("还没有生成用户 Key")
        for row in rows:
            state = f"已吊销 {_fmt(row['revoked'])}" if row["revoked"] else "有效"
            n = len(_job_ids(db, row["id"]))
            print(f"{row['id']}  {row['prefix']}…  {row['name']}  创建 {_fmt(row['created'])}  最近使用 {_fmt(row['last_used'])}  任务 {n}  {state}")
    elif a.what == "keys" and a.cmd == "revoke":
        if not store.revoke(a.id):
            print(f"没有找到有效的 Key：{a.id}", file=sys.stderr)
            return 1
        msg = f"已吊销 {a.id}"
        if a.purge:
            msg += f"，删除任务 {_delete_jobs(_job_ids(db, a.id))} 个"
        print(msg)
    elif a.cmd == "clear":
        if not a.yes:
            print("会永久删除，加 --yes 确认", file=sys.stderr)
            return 1
        if a.what == "keys":
            print(f"已删除用户 Key {store.clear()} 个")
        else:
            import shutil

            from .jobs import JOB_ID

            n = _delete_jobs(_job_ids(db))
            for d in (settings.data_dir / "jobs").glob("job_*"):  # 库里已没有记录的残留目录
                if JOB_ID.fullmatch(d.name):
                    shutil.rmtree(d, ignore_errors=True)
            print(f"已删除任务 {n} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
