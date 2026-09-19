"""游戏打包：把已下载的游戏导出成可分发的 7z 压缩包。

## 两个使用场景

1. **玩家带走**：拷到别的电脑上玩，解压后双击即可
2. **站长部署**：上传到网站服务器，作为网页游戏在线提供

## 为什么包内要带启动脚本

游戏里的资源引用是相对路径，直接双击 `index.html` 用 `file://` 打开时，
浏览器的同源策略（CORS）会拦截 Unity 的 `.wasm` / `.data` 等资源加载 ——
Chrome/Edge 对此限制很严，Firefox 稍宽，行为不一致。

因此包里附带 `启动游戏.bat` / `start-game.sh`：起一个本地 HTTP 服务再打开浏览器，
让游戏运行在 `http://127.0.0.1:port/` 下，兼容所有浏览器。

站长部署则不需要这个脚本 —— 直接把包内容放进网站目录即可，
因为游戏在网站里本来就是通过 HTTP 提供的。

## 压缩工具

优先使用系统已安装的 7-Zip，找不到则用仓库内置的 `assets/7z/7zr.exe`
（standalone 版，不依赖 DLL）。7zr 只支持 7z 格式，对本站用途足够。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from ..config import ROOT, ASSETS_DIR, config
from ..models import Game

# 内置 7zr
BUNDLED_7ZR = ASSETS_DIR / "7z" / "7zr.exe"

# 系统常见 7-Zip 安装位置（Windows）
_SYSTEM_7Z_CANDIDATES = [
    r"G:\traetool\7-Zip\7z.exe",
    r"C:\Program Files\7-Zip\7z.exe",
    r"C:\Program Files (x86)\7-Zip\7z.exe",
]

ProgressCB = Callable[[str, int], None]     # (阶段说明, 百分比) -> None


# ---------------------------------------------------------------- 压缩工具

def find_7z() -> Optional[str]:
    """定位可用的 7z 可执行文件。

    顺序：内置 7zr -> 环境变量 HGD_7Z -> 系统常见路径 -> PATH 里的 7z
    """
    if BUNDLED_7ZR.exists():
        return str(BUNDLED_7ZR)

    env = os.environ.get("HGD_7Z", "").strip()
    if env and os.path.exists(env):
        return env

    for cand in _SYSTEM_7Z_CANDIDATES:
        if os.path.exists(cand):
            return cand

    for name in ("7z", "7za", "7zr"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _run(cmd: List[str], cwd: Optional[str] = None,
         timeout: int = 3600) -> Tuple[int, str]:
    """执行命令并合并输出。"""
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        out = (proc.stdout or b"").decode("utf-8", "ignore") + \
              (proc.stderr or b"").decode("utf-8", "ignore")
        return proc.returncode, out
    except subprocess.TimeoutExpired:
        return -1, "压缩超时"
    except Exception as e:
        return -2, f"执行失败：{e}"


# ---------------------------------------------------------------- 包内辅助文件

_README_TXT = """{title}
{underline}

这是一个网页游戏（{engine_name}），已打包为离线版本。


== 怎么玩 ==

Windows:
    双击「启动游戏.bat」

macOS / Linux:
    终端里执行  ./start-game.sh

也可以直接双击 index.html，但部分浏览器（Chrome/Edge）会因为
安全策略拒绝加载游戏资源。上面的启动脚本用本地服务打开，兼容性最好。


== 部署到网站 ==

本包内容可直接作为网站目录使用：
把全部文件上传到服务器上的任意目录，
访问 index.html 即可在线游玩，无需额外配置。

（因为游戏本身就是通过 HTTP 提供的，所以不需要启动脚本，
  那个脚本只是为本地离线游玩准备的。）


== 说明 ==

- 游戏来源：{source}
- 引擎类型：{engine_name}
- 打包时间：{when}
- 文件数量：{files}

本包由「网页游戏下载器」生成。
游戏版权归原作者所有，请勿用于商业用途。
"""

_START_BAT = """@echo off
rem HtmlGameDownloader - local launcher
rem Kept ASCII-only: cmd.exe parses .bat with the OEM codepage.
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 goto :no_python

echo Starting local server, your browser will open shortly...
python "%~dp0_serve.py"
pause
exit /b 0

:no_python
echo.
echo [!] Python not found.
echo.
echo     You can still play by opening index.html directly,
echo     but Chrome/Edge may refuse to load the game assets.
echo.
echo     To fix: install Python 3.9+ from https://www.python.org/downloads/
echo             and make sure "Add python.exe to PATH" is checked.
echo.
pause
exit /b 1
"""

_SERVE_PY = '''"""本地启动服务：起一个 HTTP 服务并打开浏览器。

之所以需要它：浏览器对 file:// 协议下的资源加载有 CORS 限制，
直接用 file:// 打开 index.html 时 Chrome/Edge 可能拒绝加载游戏资源。
通过 http://127.0.0.1 提供则可以兼容所有浏览器。
"""
import http.server
import os
import socket
import socketserver
import sys
import threading
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def log_message(self, *a):
        pass


def main():
    port = free_port()
    with socketserver.TCPServer(("127.0.0.1", port), Handler) as httpd:
        url = "http://127.0.0.1:%d/index.html" % port
        print("Serving at:", url)
        print("Press Ctrl+C to stop.")
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\\nStopped.")


if __name__ == "__main__":
    main()
'''

_START_SH = """#!/usr/bin/env bash
# HtmlGameDownloader - local launcher
set -e
cd "$(dirname "$0")"

PY=python3
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "[!] python3 not found."
    echo "    You can still open index.html directly, but Chrome/Edge"
    echo "    may refuse to load the game assets due to security policy."
    echo "    Install Python 3.9+ to use this launcher."
    exit 1
fi

echo "Starting local server, your browser will open shortly..."
exec "$PY" "_serve.py"
"""

_ENGINE_NAMES = {
    "unity": "Unity WebGL",
    "flash": "Flash (Ruffle)",
    "html": "HTML5",
    "unknown": "网页游戏",
}


# ---------------------------------------------------------------- 打包

def package_game(
    game: Game,
    out_dir: Optional[str] = None,
    level: int = 5,
    progress: Optional[ProgressCB] = None,
    keep_staging: bool = False,
) -> Tuple[bool, str, Optional[str]]:
    """把游戏打包成 7z。

    返回 (是否成功, 说明, 压缩包路径)。

    level: 0=仅存储 1=最快 5=默认 7=较高 9=最高
    """
    def emit(msg: str, pct: int = 0):
        if progress:
            progress(msg, pct)

    src = Path(game.local_path or "")
    if not src.exists():
        return False, f"游戏目录不存在：{src}", None
    if not src.is_dir():
        return False, "该游戏是单文件形式，暂不支持打包", None

    seven = find_7z()
    if not seven:
        return False, ("找不到 7z 压缩工具。\n"
                       "请把 7zr.exe 放到 assets/7z/ 目录，"
                       "或安装 7-Zip，或设置环境变量 HGD_7Z 指向 7z.exe"), None

    out_dir = out_dir or str(Path(config.get("download_dir")).parent / "packages")
    os.makedirs(out_dir, exist_ok=True)

    safe_name = _safe_pkg_name(game.name)
    if _is_flash(game):
        safe_name += "_with_ruffle"
    archive = os.path.join(out_dir, f"{safe_name}_{time.strftime('%Y%m%d')}.7z")

    emit("准备打包内容…", 3)

    # 1) 在临时目录组装包结构（补 README 与启动脚本，排除内部文件）
    staging = os.path.join(out_dir, f".staging_{os.getpid()}")
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging, exist_ok=True)

    try:
        copied, skipped = _assemble(src, Path(staging), game)
        if copied == 0:
            return False, "没有可打包的文件", None

        emit(f"已准备 {copied} 个文件，开始压缩…", 12)

        # 2) 调 7z 压缩
        cmd = [seven, "a", "-t7z", f"-mx={level}", "-y", "-bso0", "-bsp0",
               archive, "."]
        rc, out = _run(cmd, cwd=staging, timeout=7200)
        if rc != 0 or not os.path.exists(archive):
            return False, f"压缩失败（code={rc}）：\n{out[-600:]}", None

        size = os.path.getsize(archive)
        emit(f"压缩完成：{size / 1024 / 1024:.1f} MB", 100)
        return True, (f"打包完成\n\n"
                      f"压缩包：{archive}\n"
                      f"大小：{size / 1024 / 1024:.1f} MB（原始 {_dir_size(src) / 1024 / 1024:.1f} MB）\n"
                      f"文件数：{copied}"), archive
    finally:
        if not keep_staging:
            shutil.rmtree(staging, ignore_errors=True)


def _is_flash(game: Game) -> bool:
    return (game.engine or "").lower() == "flash"


# 包内不携带的内部文件
_EXCLUDE_FILES = {".hgd_urlmap.json", "game_meta.json", ".part"}
_EXCLUDE_DIRS = {"_cache"}


def _assemble(src: Path, dst: Path, game: Game) -> Tuple[int, int]:
    """把游戏目录复制到暂存目录，并补上包内说明与启动脚本。"""
    copied = 0
    skipped = 0

    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in _EXCLUDE_DIRS]
        rel_root = os.path.relpath(root, src)
        target_root = dst if rel_root == "." else dst / rel_root
        target_root.mkdir(parents=True, exist_ok=True)

        for f in files:
            if f in _EXCLUDE_FILES or f.endswith(".part"):
                skipped += 1
                continue
            try:
                shutil.copy2(os.path.join(root, f), target_root / f)
                copied += 1
            except OSError:
                skipped += 1

    # 入口页必须存在，否则包没法用
    entry = game.entry_file or "index.html"
    if not (dst / entry).exists():
        for cand in ("index.html", "index.htm"):
            if (dst / cand).exists():
                entry = cand
                break

    now = time.strftime("%Y-%m-%d %H:%M")
    readme = _README_TXT.format(
        title=game.name or "网页游戏",
        underline="=" * max(4, len(game.name or "网页游戏") * 2),
        engine_name=_ENGINE_NAMES.get((game.engine or "").lower(), "网页游戏"),
        source=game.source_url or "（本地导入）",
        when=now,
        files=copied,
    )
    with open(dst / "使用说明.txt", "w", encoding="utf-8", newline="\r\n") as f:
        f.write(readme)

    # 本地启动脚本（普通浏览器对 file:// 有 CORS 限制，HTTP 方式最稳）
    with open(dst / "_serve.py", "w", encoding="utf-8", newline="\n") as f:
        f.write(_SERVE_PY)
    with open(dst / "启动游戏.bat", "w", encoding="ascii", newline="\r\n") as f:
        f.write(_START_BAT)
    with open(dst / "start-game.sh", "w", encoding="utf-8", newline="\n") as f:
        f.write(_START_SH)
    try:
        os.chmod(dst / "start-game.sh", 0o755)
    except OSError:
        pass

    return copied + 4, skipped


def _safe_pkg_name(name: str) -> str:
    bad = '<>:"/\\|?*\x00-\x1f'
    out = "".join("_" if c in bad else c for c in (name or "game")).strip(" .")
    return (out or "game")[:80]


def _dir_size(p: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def estimate_size(game: Game) -> int:
    """估算原始大小（未压缩），用于打包前提示。"""
    p = Path(game.local_path or "")
    return _dir_size(p) if p.exists() else 0
