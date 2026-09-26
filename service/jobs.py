"""任务存储与后台执行。

- 任务元数据存 SQLite；文件存本地数据目录，每个任务一个子目录。
- 不保存原文件名（常含患者姓名），只保存扩展名与页数。
- 原件处理完即删除；选择“保留原件以便复核”的任务另存打码前的页面，复核完成或到期时删除。
- 后台线程串行处理任务（16 GB 内存的本机上避免同时加载多份模型），到期自动删除。
"""

from __future__ import annotations

import json
import logging
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
  summary TEXT
)
"""


class JobStore:
    def __init__(self, root: Path):
        self.root = root
        self.db_path = root / "jobs.sqlite3"
        self._lock = threading.Lock()
        self._review_lock = threading.Lock()
        with self._conn() as c:
            c.execute(_SCHEMA)
            # 服务重启时，未完成的任务标记为失败
            c.execute("UPDATE jobs SET status='failed', error_code='INTERRUPTED', error='服务重启，任务中断，请重新提交' WHERE status IN ('queued','running')")
        self.pool = ThreadPoolExecutor(max_workers=max(1, settings.workers), thread_name_prefix="redact")
        threading.Thread(target=self._janitor, daemon=True, name="janitor").start()

    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        c.row_factory = sqlite3.Row
        return c

    def dir(self, job_id: str) -> Path:
        return self.root / "jobs" / job_id

    def create(self, src_bytes_path: Path, ext: str, opts: Options, retention_hours: float, pages: int | None = None) -> str:
        job_id = "job_" + uuid.uuid4().hex[:20]
        d = self.dir(job_id)
        (d / "in").mkdir(parents=True)
        dst = d / "in" / f"source.{ext}"
        shutil.move(str(src_bytes_path), dst)
        now = time.time()
        opt_json = json.dumps({k: (sorted(v) if isinstance(v, set) else v) for k, v in asdict(opts).items() if k != "password"}, ensure_ascii=False)
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO jobs (id,status,created,updated,expires,input_ext,pages,progress,message,options) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (job_id, "queued", now, now, now + retention_hours * 3600, ext, pages, 0, "排队中", opt_json),
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
            # 原件处理完即删除；保留脱敏后的页面，复核加框时在其上重打码
            shutil.rmtree(self.dir(job_id) / "in", ignore_errors=True)

    def review(self, job_id: str, fn, *args) -> dict:
        """复核改动串行执行，并同步更新任务摘要里的遮盖数量。"""
        with self._review_lock:
            report = fn(self.dir(job_id) / "out", *args)
        job = self.get(job_id)
        summary = {**(job["summary"] or {}), "counts": report["counts"]}
        self._update(job_id, summary=json.dumps(summary, ensure_ascii=False))
        return report

    def get(self, job_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["summary"] = json.loads(d["summary"]) if d["summary"] else None
        d["options"] = json.loads(d["options"]) if d["options"] else None
        return d

    def list(self, limit: int = 30) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT id,status,created,pages,progress,message,summary,input_ext FROM jobs ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["summary"] = json.loads(d["summary"]) if d["summary"] else None
            out.append(d)
        return out

    def delete(self, job_id: str) -> bool:
        with self._lock, self._conn() as c:
            n = c.execute("DELETE FROM jobs WHERE id=?", (job_id,)).rowcount
        shutil.rmtree(self.dir(job_id), ignore_errors=True)
        return n > 0

    def _janitor(self) -> None:
        while True:
            try:
                now = time.time()
                with self._conn() as c:
                    ids = [r[0] for r in c.execute("SELECT id FROM jobs WHERE expires < ? AND status NOT IN ('queued','running')", (now,))]
                for job_id in ids:
                    self.delete(job_id)
                    log.info("job %s expired and deleted", job_id)
            except Exception:  # noqa: BLE001
                log.exception("janitor error")
            time.sleep(300)
