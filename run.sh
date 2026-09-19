#!/usr/bin/env bash
# 网页游戏下载器 —— Linux / macOS 启动脚本
set -e
cd "$(dirname "$0")"

echo "============================================"
echo "  网页游戏下载器  HTML / Flash / Unity"
echo "============================================"

PY=${PYTHON:-python3}
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "[错误] 未找到 $PY，请先安装 Python 3.9+"
    exit 1
fi

if ! "$PY" -c "import PySide6" >/dev/null 2>&1; then
    echo "[提示] 首次运行需要安装依赖..."
    "$PY" -m pip install -r requirements.txt
fi

exec "$PY" -m hgd "$@"
