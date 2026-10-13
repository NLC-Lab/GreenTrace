"""SQLite persistence: appending a block is O(1), unlike rewriting a JSON chain."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from .models import Block

# 竞争写入时最长等待 30 秒，避免跨进程/多线程立即报 "database is locked"。
_BUSY_TIMEOUT_MS = 30_000


class ChainStorage:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        legacy = self.path.with_suffix(".json")
        migrate = not self.path.exists() and legacy.exists()
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS blocks (height INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
        if migrate:
            blocks = json.loads(legacy.read_text(encoding="utf-8"))
            with self._db() as db:
                db.executemany(
                    "INSERT INTO blocks(height, payload) VALUES (?, ?)",
                    ((block["height"], json.dumps(block, ensure_ascii=False)) for block in blocks),
                )

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            # WAL 让读写并发；busy_timeout 让竞争写入等待而非立即失败。
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            yield db
            db.commit()
        finally:
            db.close()

    def load(self) -> list[Block]:
        with self._db() as db:
            rows = db.execute("SELECT payload FROM blocks ORDER BY height").fetchall()
        return [Block.model_validate(json.loads(row[0])) for row in rows]

    def save(self, chain: list[Block]) -> None:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM blocks")
            db.executemany("INSERT INTO blocks(height, payload) VALUES (?, ?)",
                           ((block.height, json.dumps(block.model_dump(), ensure_ascii=False))
                            for block in chain))

    def append(self, block: Block) -> None:
        with self._db() as db:
            db.execute("INSERT INTO blocks(height, payload) VALUES (?, ?)",
                       (block.height, json.dumps(block.model_dump(), ensure_ascii=False)))
