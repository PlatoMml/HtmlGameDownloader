#!/usr/bin/env bash
# HtmlGameDownloader launcher (Linux / macOS)
#
# Kept ASCII-only for the same reason as run.bat: avoid codepage/encoding
# surprises on systems whose locale is not UTF-8. Chinese messaging is
# emitted by the Python side.

set -e
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "[ERROR] $PY not found. Please install Python 3.9+ first."
    exit 1
fi

if ! "$PY" -c "import PySide6" >/dev/null 2>&1; then
    echo "[setup] Installing dependencies, please wait..."
    "$PY" -m pip install -r requirements.txt
fi

exec "$PY" -m hgd "$@"
