"""SQLite 存储层：游戏库 + 收藏夹（同一张表，favorite/category 区分）。"""
from __future__ import annotations

import os
import sqlite3
import time
from typing import List, Optional

from ..config import DB_PATH, ensure_dirs
from ..models import Game


_SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL DEFAULT '',
    source_url   TEXT NOT NULL DEFAULT '',
    local_path   TEXT NOT NULL DEFAULT '',
    entry_file   TEXT NOT NULL DEFAULT '',
    engine       TEXT NOT NULL DEFAULT '',
    category     TEXT NOT NULL DEFAULT '未分类',
    favorite     INTEGER NOT NULL DEFAULT 0,
    cover        TEXT NOT NULL DEFAULT '',
    size         INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL DEFAULT 0,
    updated_at   REAL NOT NULL DEFAULT 0,
    last_played  REAL NOT NULL DEFAULT 0,
    play_count   INTEGER NOT NULL DEFAULT 0,
    note         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_games_fav ON games(favorite);
CREATE INDEX IF NOT EXISTS idx_games_cat ON games(category);
CREATE UNIQUE INDEX IF NOT EXISTS idx_games_path ON games(local_path);
"""


def connect() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(_SCHEMA)


def add_game(g: Game) -> int:
    """新增记录；同路径冲突时更新而非报错。"""
    init_db()
    now = time.time()
    g.created_at = g.created_at or now
    g.updated_at = now
    with connect() as conn:
        cur = conn.execute("SELECT id FROM games WHERE local_path=?", (g.local_path,))
        row = cur.fetchone()
        if row:
            gid = row["id"]
            conn.execute(
                """UPDATE games SET name=?, source_url=?, entry_file=?, engine=?,
                   category=?, favorite=?, cover=?, size=?, updated_at=? WHERE id=?""",
                (g.name, g.source_url, g.entry_file, g.engine, g.category,
                 g.favorite, g.cover, g.size, now, gid),
            )
            return gid
        cur = conn.execute(
            """INSERT INTO games
               (name, source_url, local_path, entry_file, engine, category, favorite,
                cover, size, created_at, updated_at, last_played, play_count, note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (g.name, g.source_url, g.local_path, g.entry_file, g.engine, g.category,
             g.favorite, g.cover, g.size, g.created_at, g.updated_at,
             g.last_played, g.play_count, g.note),
        )
        conn.commit()
        return int(cur.lastrowid)


def update_game(gid: int, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [gid]
    with connect() as conn:
        conn.execute(f"UPDATE games SET {cols} WHERE id=?", vals)
        conn.commit()


def get_game(gid: int) -> Optional[Game]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM games WHERE id=?", (gid,)).fetchone()
    return Game.from_row(row) if row else None


def delete_game(gid: int, also_files: bool = False) -> None:
    import shutil
    g = get_game(gid)
    with connect() as conn:
        conn.execute("DELETE FROM games WHERE id=?", (gid,))
        conn.commit()
    if also_files and g and g.local_path and os.path.isdir(g.local_path):
        shutil.rmtree(g.local_path, ignore_errors=True)


def list_games(favorite_only: bool = False, keyword: str = "",
               category: str = "") -> List[Game]:
    init_db()
    sql = "SELECT * FROM games WHERE 1=1"
    args: list = []
    if favorite_only:
        sql += " AND favorite=1"
    if category:
        sql += " AND category=?"
        args.append(category)
    if keyword:
        sql += " AND (name LIKE ? OR source_url LIKE ?)"
        args += [f"%{keyword}%", f"%{keyword}%"]
    sql += " ORDER BY favorite DESC, updated_at DESC"
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [Game.from_row(r) for r in rows]


def categories() -> List[str]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT category FROM games WHERE category<>'' ORDER BY category"
        ).fetchall()
    return [r["category"] for r in rows]


def rename_category(old: str, new: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE games SET category=? WHERE category=?", (new, old))
        conn.commit()


def touch_played(gid: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE games SET last_played=?, play_count=play_count+1 WHERE id=?",
            (time.time(), gid),
        )
        conn.commit()
