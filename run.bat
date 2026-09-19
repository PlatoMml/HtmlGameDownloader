@echo off
chcp 65001 >nul
title 网页游戏下载器
cd /d "%~dp0"

echo ============================================
echo   网页游戏下载器  HTML / Flash / Unity
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 python，请先安装 Python 3.9+ 并加入 PATH
    pause
    exit /b 1
)

python -c "import PySide6" >nul 2>nul
if errorlevel 1 (
    echo [提示] 首次运行需要安装依赖，正在安装...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败
        pause
        exit /b 1
    )
)

echo 正在启动...
python -m hgd %*
if errorlevel 1 (
    echo.
    echo [错误] 程序异常退出
    pause
)
