"""全局路径与配置。

设计原则：
- 便携优先：所有数据落在项目目录下的 data/ 内，方便整目录搬走。
- 敏感信息（代理密码等）不落盘；只存本地路径引用。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "HtmlGameDownloader"

# ---------------------------------------------------------------- 路径


def _app_root() -> Path:
    """项目根目录。

    开发模式：本文件的上两级（hgd/config.py -> 根）
    冻结模式（PyInstaller）：可执行文件所在目录
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


ROOT = _app_root()
ASSETS_DIR = ROOT / "assets"
RUFFLE_DIR = ASSETS_DIR / "ruffle"
SKILL_DIR = ROOT / "skill"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "library.db"
CONFIG_PATH = DATA_DIR / "config.json"

# 内置下载的默认落盘目录（用户可改）
DEFAULT_DOWNLOAD_DIR = DATA_DIR / "games"


def ensure_dirs() -> None:
    for d in (DATA_DIR, DEFAULT_DOWNLOAD_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- 设置

_DEFAULTS = {
    "download_dir": str(DEFAULT_DOWNLOAD_DIR),
    "proxy": "",                 # 例如 http://127.0.0.1:10808
    "use_env_proxy": True,       # 是否同时继承环境变量代理
    "mcp_enabled": False,
    "mcp_port": 8765,
    "mcp_host": "127.0.0.1",
    "default_volume": 30,        # 需求：默认音量 30%
    "muted": False,
    "max_depth": 3,              # 递归镜像深度
    "max_file_size": 512 * 1024 * 1024,   # 单文件上限 512MB
    "timeout": 30,
    "retries": 3,
    "concurrency": 6,
    "lazy_fill": True,           # 运行时懒补漏（继承参考仓库最有价值的设计）
    "last_scan_dir": "",
}


class Config:
    """简单的 JSON 配置，带默认值回退。"""

    def __init__(self) -> None:
        self._data: dict = {}
        self.load()

    def load(self) -> None:
        data = dict(_DEFAULTS)
        try:
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    # 只接受已知键，避免旧配置污染
                    data.update({k: v for k, v in loaded.items() if k in _DEFAULTS})
        except Exception:
            # 配置损坏不该让程序起不来，退回默认值
            pass
        self._data = data

    def save(self) -> None:
        ensure_dirs()
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def get(self, key: str, default=None):
        return self._data.get(key, _DEFAULTS.get(key, default))

    def set(self, key: str, value) -> None:
        self._data[key] = value

    def __getitem__(self, key: str):
        return self.get(key)

    def __setitem__(self, key: str, value) -> None:
        self.set(key, value)

    def as_dict(self) -> dict:
        return dict(self._data)


config = Config()

# ---------------------------------------------------------------- 常量

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 游戏主文件候选后缀（用于"目录内识别游戏"与入口推断）
ENGINE_EXT = {
    ".swf": "flash",
    ".unityweb": "unity",
    ".html": "html",
    ".htm": "html",
    ".xhtml": "html",
}

UNITY_MARKERS = ("unityInstance", "createUnityInstance", ".unityweb", "Build/", "StreamingAssets")
FLASH_MARKERS = (".swf", "application/x-shockwave-flash", "ruffle")
