"""数据模型。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Game:
    """一条游戏记录（下载库 / 收藏夹共用一张表）。"""

    id: Optional[int] = None
    name: str = ""
    source_url: str = ""
    local_path: str = ""           # 游戏文件夹或主文件路径
    entry_file: str = ""           # 相对 local_path 的入口文件，如 index.htm
    engine: str = ""               # flash / unity / html / unknown
    category: str = "未分类"        # 收藏夹分类
    favorite: int = 0              # 0/1
    cover: str = ""                # 封面图相对路径（可选）
    size: int = 0                  # 字节
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_played: float = 0.0
    play_count: int = 0
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_row(cls, row) -> "Game":
        cols = [
            "id", "name", "source_url", "local_path", "entry_file", "engine",
            "category", "favorite", "cover", "size", "created_at", "updated_at",
            "last_played", "play_count", "note",
        ]
        return cls(**{c: row[c] for c in cols if c in row.keys()})


@dataclass
class IdentifyResult:
    """网址识别结果。"""

    ok: bool
    url: str = ""
    name: str = ""            # 自动识别的游戏名（可手动改）
    engine: str = "unknown"   # flash / unity / html
    entry_url: str = ""       # 真正的游戏入口地址（可能是内层 iframe/index.htm）
    page_url: str = ""        # 用户给的页面
    width: int = 0
    height: int = 0
    message: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class DownloadProgress:
    """下载进度回调载荷。"""

    phase: str = ""        # analyse / mirror / lazy / done
    total: int = 0
    done: int = 0
    bytes: int = 0
    current: str = ""
    ok: bool = True
    message: str = ""

    @property
    def percent(self) -> float:
        if self.total <= 0:
            return 0.0
        return min(100.0, self.done * 100.0 / self.total)
