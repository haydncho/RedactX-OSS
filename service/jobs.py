"""任务存储与后台执行。

- 任务元数据存 SQLite；文件存本地数据目录，每个任务一个子目录。
- 原文件名（常含患者姓名）只存在任务元数据里，供“最近任务”在各设备上显示；只返回给提交它的 Key，不写日志，任务到期或删除时随元数据一并删除。
- 上传的原文件处理完即删除；原件预览图（缩小版，供对比查看）与结果一起保留到期；选择“保留原件以便复核”的任务另存打码前的页面，复核完成或到期时删除。
- 后台线程串行处理任务（16 GB 内存的本机上避免同时加载多份模型），到期自动删除。
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from redactx.config import settings
from redactx.ingest import InputError
from redactx.pipeline import Options, run

log = logging.getLogger("redactx.jobs")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  created REAL NOT NULL,
  updated REAL NOT NULL,
  expires REAL NOT NULL,
  input_ext TEXT,
  pages INTEGER,
  progress REAL DEFAULT 0,
  message TEXT,
  error_code TEXT,
  error TEXT,
  options TEXT,
  summary TEXT,
  owner TEXT,
  name TEXT
)
"""


def clean_name(name: str | None) -> str | None:
    """上传时的原文件名：只取最后一段（去掉客户端路径），去掉控制字符，最长 200 字。"""
    if not name:
        return None
    base = re.split(r"[\\/]", name)[-1]
    base = "".join(ch for ch in base if ch.isprintable()).strip()
    return base[:200] or None


# 任务 ID：job_ 加 20 位小写字母数字（生成时用 uuid4 的十六进制）
JOB_ID = re.compile(r"job_[0-9a-z]{20}")

class JobStore:
    def __init__(self, root: Path):
        self.root = root
        self.db_path = root / "jobs.sqlite3"
        self._lock = threading.Lock()
        self._review_lock = threading.Lock()
        with self._conn() as c:
            c.execute(_SCHEMA)
            # 旧库没有 owner 列：补上（旧任务无归属，只有管理员可见）
            cols = {r[1] for r in c.execute("PRAGMA table_info(jobs)")}
            if "owner" not in cols:
                c.execute("ALTER TABLE jobs ADD COLUMN owner TEXT")
            # 旧库没有 name 列：补上（旧任务没有文件名，网页上显示本浏览器记下的名字或任务编号）
            if "name" not in cols:
                c.execute("ALTER TABLE jobs ADD COLUMN name TEXT")
            # 服务重启时，未完成的任务标记为失败
            c.execute("UPDATE jobs SET status='failed', error_code='INTERRUPTED', error='服务重启，任务中断，请重新提交' WHERE status IN ('queued','running')")
        self.pool = ThreadPoolExecutor(max_workers=max(1, settings.workers), thread_name_prefix="redact")
        self._sweep_temp(0)  # 上次运行中断留下的上传临时文件
        threading.Thread(target=self._janitor, daemon=True, name="janitor").start()

    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        c.row_factory = sqlite3.Row
        return c

    def dir(self, job_id: str) -> Path:
        # job_id 来自 URL：只接受本服务生成的格式，杜绝“..”等路径穿越
        if not JOB_ID.fullmatch(job_id or ""):
            raise ValueError("非法的任务 ID")
        return self.root / "jobs" / job_id

    def create(self, src_bytes_path: Path, ext: str, opts: Options, retention_hours: float, pages: int | None = None,
               owner: str | None = None, name: str | None = None) -> str:
        job_id = "job_" + uuid.uuid4().hex[:20]
        d = self.dir(job_id)
        (d / "in").mkdir(parents=True)
        dst = d / "in" / f"source.{ext}"
        shutil.move(str(src_bytes_path), dst)
        now = time.time()
        # 数据库只存可公开的选项：不存密码；自定义词常是患者姓名，只记数量
        saved = {k: (sorted(v) if isinstance(v, set) else v) for k, v in asdict(opts).items() if k != "password"}
        saved["custom_words"] = len(opts.custom_words or [])
        opt_json = json.dumps(saved, ensure_ascii=False)
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO jobs (id,status,created,updated,expires,input_ext,pages,progress,message,options,owner,name) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, "queued", now, now, now + retention_hours * 3600, ext, pages, 0, "排队中", opt_json, owner, clean_name(name)),
            )
        self.pool.submit(self._execute, job_id, dst, opts)
        return job_id

    def _update(self, job_id: str, **fields) -> None:
        fields["updated"] = time.time()
        cols = ",".join(f"{k}=?" for k in fields)
        with self._lock, self._conn() as c:
            c.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))

    def _execute(self, job_id: str, src: Path, opts: Options) -> None:
        if not self.get(job_id):
            return  # 排队期间已被删除
        self._update(job_id, status="running", message="开始处理")
        last = [0.0]

        def progress(p: float, msg: str) -> None:
            now = time.time()
            if now - last[0] > 0.4 or p >= 0.99:
                last[0] = now
                self._update(job_id, progress=round(p, 3), message=msg)

        try:
            report = run(src, self.dir(job_id) / "out", opts, progress)
            (self.dir(job_id) / "out" / "report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            summary = {k: report[k] for k in ("pages", "counts", "elapsed_sec", "output", "verification")}
            self._update(job_id, status="succeeded", progress=1.0, message="完成", pages=report["pages"], summary=json.dumps(summary, ensure_ascii=False))
            log.info("job %s succeeded pages=%s elapsed=%s", job_id, report["pages"], report["elapsed_sec"])
        except InputError as e:
            self._update(job_id, status="failed", error_code=e.code, error=str(e), message="失败")
            log.info("job %s failed %s", job_id, e.code)
        except Exception as e:  # noqa: BLE001
            # 异常信息里可能带有文件内容片段，日志只记类型与堆栈位置
            log.error("job %s crashed: %s\n%s", job_id, type(e).__name__, "".join(traceback.format_tb(e.__traceback__)[-3:]))
            self._update(job_id, status="failed", error_code="INTERNAL", error="处理失败，请稍后重试或联系管理员", message="失败")
        finally:
            # 原件处理完即删除；保留脱敏后的页面，复核加框时在其上重打码。
            # 转换出的 PDF（含原文文字层）与去水印中间件在失败时也要删掉
            d = self.dir(job_id)
            shutil.rmtree(d / "in", ignore_errors=True)
            shutil.rmtree(d / "out" / "convert", ignore_errors=True)
            (d / "out" / "wm-clean.pdf").unlink(missing_ok=True)

    def review(self, job_id: str, fn, *args) -> dict:
        """复核改动串行执行，并同步更新任务摘要里的遮盖数量。"""
        with self._review_lock:
            report = fn(self.dir(job_id) / "out", *args)
        job = self.get(job_id)
        summary = {**(job["summary"] or {}), "counts": report["counts"]}
        self._update(job_id, summary=json.dumps(summary, ensure_ascii=False))
        return report

    def get(self, job_id: str) -> dict | None:
        if not JOB_ID.fullmatch(job_id or ""):
            return None
        with self._conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["summary"] = json.loads(d["summary"]) if d["summary"] else None
        d["options"] = json.loads(d["options"]) if d["options"] else None
        return d

    def list(self, limit: int = 30, owner: str | None = None) -> list[dict]:
        """owner 为 None 时列出全部（管理员），否则只列该 Key 提交的任务。"""
        cols = "id,status,created,pages,progress,message,summary,input_ext,name"
        with self._conn() as c:
            if owner is None:
                rows = c.execute(f"SELECT {cols} FROM jobs ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
            else:
                rows = c.execute(f"SELECT {cols} FROM jobs WHERE owner=? ORDER BY created DESC LIMIT ?", (owner, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["summary"] = json.loads(d["summary"]) if d["summary"] else None
            out.append(d)
        return out

    def delete(self, job_id: str) -> bool:
        if not JOB_ID.fullmatch(job_id or ""):
            return False
        with self._lock, self._conn() as c:
            n = c.execute("DELETE FROM jobs WHERE id=?", (job_id,)).rowcount
        shutil.rmtree(self.dir(job_id), ignore_errors=True)
        return n > 0

    def ids(self, owner: str | None = None) -> list[str]:
        with self._conn() as c:
            q = c.execute("SELECT id FROM jobs") if owner is None else c.execute("SELECT id FROM jobs WHERE owner=?", (owner,))
            return [r[0] for r in q]

    def _janitor(self) -> None:
        while True:
            try:
                now = time.time()
                with self._conn() as c:
                    ids = [r[0] for r in c.execute("SELECT id FROM jobs WHERE expires < ? AND status NOT IN ('queued','running')", (now,))]
                for job_id in ids:
                    self.delete(job_id)
                    log.info("job %s expired and deleted", job_id)
                self._sweep_temp(3600)
            except Exception:  # noqa: BLE001
                log.exception("janitor error")
            time.sleep(300)

    def _sweep_temp(self, max_age: float) -> None:
        """删除数据目录根下残留的上传临时文件与同步接口工作目录（请求中断、进程被杀时留下的）。"""
        now = time.time()
        for p in self.root.iterdir():
            if not (p.name.endswith(".upload") or p.name.startswith(("tmp", "sync-"))):
                continue
            try:
                if now - p.stat().st_mtime < max_age:
                    continue
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
                log.info("removed stale temp %s", p.name)
            except OSError:
                pass
