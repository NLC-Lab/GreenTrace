"""SQLite persistence: appending a block is O(1), unlike rewriting a JSON chain."""
import json
import sqlite3
from pathlib import Path
from .models import Block


class ChainStorage:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        legacy = self.path.with_suffix(".json")
        migrate = not self.path.exists() and legacy.exists()
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS blocks (height INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
        if migrate:
            blocks = json.loads(legacy.read_text(encoding="utf-8"))
            with sqlite3.connect(self.path) as db:
                db.executemany(
                    "INSERT INTO blocks(height, payload) VALUES (?, ?)",
                    ((block["height"], json.dumps(block, ensure_ascii=False)) for block in blocks),
                )

    def load(self) -> list[Block]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT payload FROM blocks ORDER BY height").fetchall()
        return [Block.model_validate(json.loads(row[0])) for row in rows]

    def save(self, chain: list[Block]) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM blocks")
            db.executemany("INSERT INTO blocks(height, payload) VALUES (?, ?)",
                           ((block.height, json.dumps(block.model_dump(), ensure_ascii=False))
                            for block in chain))

    def append(self, block: Block) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO blocks(height, payload) VALUES (?, ?)",
                       (block.height, json.dumps(block.model_dump(), ensure_ascii=False)))
