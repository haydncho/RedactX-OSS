"""API Key：按 Key 隔离任务。

- 管理员 Key：环境变量 REDACTX_API_KEY，能看到、操作全部任务。
- 用户 Key：由后台脚本（python -m service.admin keys create 名称）生成，只能看到、操作自己提交的任务。
  库里只存 SHA-256 哈希与前 8 位（便于辨认），明文只在生成时显示一次；生成时保证与库里已有的、与管理员 Key 都不重复。
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  prefix TEXT NOT NULL,
  key_hash TEXT NOT NULL UNIQUE,
  created REAL NOT NULL,
  last_used REAL,
  revoked REAL
)
"""
KEY_PREFIX = "rx_"


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


@dataclass(frozen=True)
class Principal:
    """调用者：id 用于任务归属（管理员与未启用鉴权的本机模式为 admin）。"""

    id: str
    admin: bool
    name: str = ""


ADMIN = Principal("admin", True, "管理员")


class KeyStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        with self._conn() as c:
            c.execute(_SCHEMA)

    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        c.row_factory = sqlite3.Row
        return c

    def create(self, name: str, admin_key: str | None = None) -> tuple[str, str]:
        """生成新 Key，返回 (id, 明文 Key)。明文不入库。"""
        name = (name or "").strip()[:64] or "未命名"
        reserved = key_hash(admin_key) if admin_key else None
        for _ in range(10):
            key = KEY_PREFIX + secrets.token_urlsafe(24)  # 192 位随机
            h = key_hash(key)
            if h == reserved:
                continue
            kid = "key_" + secrets.token_hex(6)
            try:
                with self._conn() as c:
                    c.execute("INSERT INTO api_keys (id,name,prefix,key_hash,created) VALUES (?,?,?,?,?)",
                              (kid, name, key[: len(KEY_PREFIX) + 5], h, time.time()))
                return kid, key
            except sqlite3.IntegrityError:  # 哈希或 id 与已有记录重复（概率可忽略），重新生成
                continue
        raise RuntimeError("无法生成不重复的 Key")

    def verify(self, key: str) -> Principal | None:
        if not key or not key.startswith(KEY_PREFIX):
            return None
        with self._conn() as c:
            row = c.execute("SELECT id,name,last_used FROM api_keys WHERE key_hash=? AND revoked IS NULL", (key_hash(key),)).fetchone()
            if not row:
                return None
            now = time.time()
            if not row["last_used"] or now - row["last_used"] > 60:  # 最近使用时间每分钟最多写一次
                c.execute("UPDATE api_keys SET last_used=? WHERE id=?", (now, row["id"]))
        return Principal(row["id"], False, row["name"])

    def active_count(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM api_keys WHERE revoked IS NULL").fetchone()[0]

    def list(self) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT id,name,prefix,created,last_used,revoked FROM api_keys ORDER BY created")]

    def revoke(self, kid: str) -> bool:
        """按 id 吊销（id 见 list）。吊销后立即失效；哈希保留，新生成的 Key 不会与它重复。"""
        with self._conn() as c:
            n = c.execute("UPDATE api_keys SET revoked=? WHERE id=? AND revoked IS NULL", (time.time(), kid)).rowcount
        return n > 0

    def clear(self) -> int:
        with self._conn() as c:
            return c.execute("DELETE FROM api_keys").rowcount
