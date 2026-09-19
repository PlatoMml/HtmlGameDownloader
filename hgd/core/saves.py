"""游戏存档（浏览器存储 profile）管理。

## 为什么需要这个模块

网页游戏的存档主要存在浏览器的持久化存储里：
    - localStorage      —— 大多数 HTML5 游戏
    - IndexedDB         —— Unity WebGL（IDBFS 文件系统）、体量大的游戏
    - SharedObject      —— Flash 游戏（Ruffle 用 IndexedDB/localStorage 模拟）
    - Cookie / Cache    —— 少数游戏

QtWebEngine 默认 profile 是 **off-the-record**（无痕模式），
用它打开游戏，存档在窗口关闭时全部丢失 —— 这不是"重玩"问题，
而是"根本存不住"问题。

## 约束（均为实测得出）

1. **必须用命名 profile**：`QWebEngineProfile("name")` 才带持久存储；
   `QWebEngineProfile.defaultProfile()` 是 off-the-record，存不住。

2. **存储位置按游戏隔离**：每个游戏一份存储，避免不同游戏的同名
   localStorage 键互相覆盖（例如两个游戏都用 "level" 会串档）。

3. **端口必须按游戏固定**：浏览器存储按源（scheme://host:port）隔离。
   端口一变，同一个游戏就成了"另一个源"，读不到旧存档。

## 重玩为什么要用"代际"而不是直接删目录

游戏运行时，QtWebEngine 通过 leveldb 持有存储目录的文件句柄，
且 `profile.deleteLater()` 只是延迟释放。实测直接删除会出现：
本地文件删不干净、游戏重新载入失败（新 profile 读到半删状态）。

因此重玩采用**代际切换**：
    saves/<key>/gen1/   <- 旧存档（待清理）
    saves/<key>/gen2/   <- 切换后的活跃代际（全新空目录）
重玩只需把活跃代际 +1，游戏立刻从全新存档开始，
旧目录在句柄自然释放后（下次启动或后续操作时）再真正删除。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import time
from pathlib import Path
from typing import Optional, Tuple

from ..config import SAVES_DIR, DATA_DIR, ensure_dirs

# 端口分配范围（避开常用端口与系统保留段）
PORT_RANGE_START = 17800
PORT_RANGE_END = 18899

_PORTS_FILE = DATA_DIR / "save_ports.json"


# ---------------------------------------------------------------- 标识

def game_key(game) -> str:
    """给游戏算一个稳定的存储标识。

    优先用游戏在库里的 id（稳定、短、不与路径耦合），
    未入库的游戏退化为路径哈希。
    """
    gid = getattr(game, "id", None)
    if gid:
        return f"id{gid}"
    p = (getattr(game, "local_path", "") or "").strip()
    if not p:
        return "unknown"
    h = hashlib.sha1(os.path.normcase(os.path.abspath(p)).encode("utf-8")).hexdigest()
    return "p" + h[:12]


def base_name(game) -> str:
    """该游戏存档的基名（也是 profile 名前缀）。"""
    return "hgd_" + game_key(game)


def game_root(game) -> Path:
    """该游戏存档的根目录（内含各代际子目录）。"""
    return SAVES_DIR / base_name(game)


# ---------------------------------------------------------------- 代际

def _state_file(game) -> Path:
    return game_root(game) / "state.json"


def _read_state(game) -> dict:
    """读取代际状态。返回 {"gen": N, "pending": [目录名...]}"""
    f = _state_file(game)
    try:
        if f.exists():
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                gen = int(data.get("gen", 1) or 1)
                pend = [str(x) for x in (data.get("pending") or [])]
                return {"gen": max(1, gen), "pending": pend}
    except Exception:
        pass
    return {"gen": 1, "pending": []}


def _write_state(game, state: dict) -> None:
    try:
        root = game_root(game)
        root.mkdir(parents=True, exist_ok=True)
        with open(_state_file(game), "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False)
    except Exception:
        pass


def current_gen(game) -> int:
    return _read_state(game)["gen"]


def save_dir(game, gen: Optional[int] = None) -> Path:
    """该游戏当前（或指定代际）的存档目录。"""
    g = current_gen(game) if gen is None else int(gen)
    return game_root(game) / f"gen{g}"


def profile_name(game, gen: Optional[int] = None) -> str:
    """QtWebEngine profile 名称。

    带代际后缀：同一进程内重建 profile 时不会与旧对象共用存储，
    避免 Qt 内部的 profile 名称缓存带来干扰。
    """
    g = current_gen(game) if gen is None else int(gen)
    return f"{base_name(game)}_g{g}"


# ---------------------------------------------------------------- 查询

def save_exists(game) -> bool:
    """当前代际是否有存档数据。"""
    d = save_dir(game)
    if not d.exists():
        return False
    for root, _dirs, files in os.walk(d):
        if os.path.basename(root) == "_cache":
            continue
        for f in files:
            try:
                if os.path.getsize(os.path.join(root, f)) > 0:
                    return True
            except OSError:
                pass
    return False


def _dir_size(d: Path) -> int:
    total = 0
    if not d.exists():
        return 0
    for root, _dirs, files in os.walk(d):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def save_size(game) -> int:
    """当前代际存档占用（含缓存）。"""
    return _dir_size(save_dir(game))


def total_save_size() -> int:
    return _dir_size(SAVES_DIR)


def all_save_size() -> int:
    """所有代际（含待清理）总占用 —— 用于设置页展示真实磁盘占用。"""
    return _dir_size(SAVES_DIR)


# ---------------------------------------------------------------- 清理

def purge_pending(game) -> int:
    """尝试删除待清理的旧代际目录。返回成功删除的个数。

    句柄未释放时删除会失败，此时保留记录，下次再试。
    这个函数是"尽力而为"的，不会影响游戏运行。
    """
    state = _read_state(game)
    pending = state.get("pending") or []
    if not pending:
        return 0

    root = game_root(game)
    still: list = []
    removed = 0
    for name in pending:
        p = root / name
        if not p.exists():
            removed += 1
            continue
        try:
            shutil.rmtree(p)
            removed += 1
        except Exception:
            # 句柄还占着，先清空内容再留待下轮
            try:
                for sub in os.listdir(p):
                    sp = p / sub
                    try:
                        if sp.is_dir():
                            shutil.rmtree(sp, ignore_errors=True)
                        else:
                            sp.unlink(missing_ok=True)
                    except Exception:
                        pass
                shutil.rmtree(p)
                removed += 1
                continue
            except Exception:
                pass
            still.append(name)

    if removed:
        state["pending"] = still
        _write_state(game, state)
    return removed


def purge_all_pending() -> int:
    """清理所有游戏的待删代际。返回删除的目录数。"""
    if not SAVES_DIR.exists():
        return 0
    total = 0
    for d in list(SAVES_DIR.iterdir()):
        if not d.is_dir():
            continue
        state_file = d / "state.json"
        if not state_file.exists():
            continue
        try:
            with open(state_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        root = d

        class _G:
            local_path = ""
            id = None

        # 直接按目录名定位（不依赖 game 对象）
        pending = data.get("pending") or []
        still = []
        for name in pending:
            p = root / name
            if not p.exists():
                total += 1
                continue
            try:
                shutil.rmtree(p)
                total += 1
            except Exception:
                still.append(name)
        if still != pending:
            data["pending"] = still
            try:
                with open(state_file, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False)
            except Exception:
                pass
    return total


def clear_save(game) -> Tuple[bool, str]:
    """重玩：切换到全新代际，实现"从新存档开始"。

    不直接删除正在使用的目录（leveldb 句柄仍被占用会失败），
    而是把活跃代际 +1，让游戏立即用上一个全新的空目录。
    旧目录登记为待清理，稍后真正删除。

    返回 (是否成功, 说明)。
    """
    try:
        state = _read_state(game)
        old_gen = state["gen"]
        new_gen = old_gen + 1

        old_dir_name = f"gen{old_gen}"
        pending = list(state.get("pending") or [])
        if old_dir_name not in pending:
            pending.append(old_dir_name)

        state["gen"] = new_gen
        state["pending"] = pending
        _write_state(game, state)

        # 立即尝试清理旧代际（句柄没释放也没关系，会留待下次）
        purge_pending(game)

        # 建立新代际目录，确保路径存在
        save_dir(game).mkdir(parents=True, exist_ok=True)
        return True, "已切换到新存档"
    except Exception as e:
        return False, f"重置存档失败：{e}"


def clear_all_saves() -> Tuple[int, int]:
    """清空所有游戏的存档。返回 (成功数, 失败数)。"""
    if not SAVES_DIR.exists():
        return 0, 0
    ok = fail = 0
    for d in list(SAVES_DIR.iterdir()):
        if not d.is_dir():
            continue
        try:
            shutil.rmtree(d)
            ok += 1
        except Exception:
            # 目录被占用时清内容 + 推进代际
            try:
                for sub in os.listdir(d):
                    sp = d / sub
                    try:
                        if sp.is_dir():
                            shutil.rmtree(sp, ignore_errors=True)
                        else:
                            sp.unlink(missing_ok=True)
                    except Exception:
                        pass
                ok += 1
            except Exception:
                fail += 1
    return ok, fail


# ---------------------------------------------------------------- 端口

def _load_ports() -> dict:
    try:
        if _PORTS_FILE.exists():
            with open(_PORTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {str(k): int(v) for k, v in data.items()}
    except Exception:
        pass
    return {}


def _save_ports(mapping: dict) -> None:
    try:
        ensure_dirs()
        with open(_PORTS_FILE, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def port_for(game, preferred: int = 0) -> int:
    """给游戏分配一个稳定端口并持久化。

    稳定性是关键：同一个游戏每次都必须拿到同一个端口，
    否则浏览器会认为换了源，读不到旧存档。
    """
    key = base_name(game)
    mapping = _load_ports()

    if preferred and _port_free(preferred):
        mapping[key] = int(preferred)
        _save_ports(mapping)
        return int(preferred)

    old = mapping.get(key)
    if old and _port_free(old):
        return old

    used = set(mapping.values())
    for port in range(PORT_RANGE_START, PORT_RANGE_END):
        if port in used or not _port_free(port):
            continue
        mapping[key] = port
        _save_ports(mapping)
        return port

    # 范围内都不可用：退化为临时端口（本次可玩，但存档无法续接）
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def release_port(game) -> None:
    mapping = _load_ports()
    if mapping.pop(base_name(game), None) is not None:
        _save_ports(mapping)
