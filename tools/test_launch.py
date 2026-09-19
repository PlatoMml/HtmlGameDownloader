"""验证 run.bat 双击启动后主窗口真实可见。

只检查"进程存在"不够——窗口可能创建失败或不可见。
这里用 Win32 API 枚举顶层窗口，确认标题与可见性。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

user32 = ctypes.windll.user32

EXPECT_TITLE_PART = "网页游戏下载器"


def top_level_windows():
    """枚举所有可见的顶层窗口，返回 [(hwnd, title, rect)]。"""
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        out.append((hwnd, buf.value, (r.left, r.top, r.right - r.left, r.bottom - r.top)))
        return True

    user32.EnumWindows(cb, 0)
    return out


def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    print("[1] 通过 run.bat 启动（模拟双击）")
    proc = subprocess.Popen(
        ["cmd.exe", "/c", "run.bat"],
        cwd=root,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    found = None
    deadline = time.time() + 45
    while time.time() < deadline:
        time.sleep(1.5)
        if proc.poll() is not None:
            out = proc.stdout.read().decode("utf-8", "ignore") if proc.stdout else ""
            print(f"  [FAIL] run.bat 提前退出，code={proc.returncode}")
            print("  输出:", out[-600:] or "(空)")
            return 1
        for hwnd, title, rect in top_level_windows():
            if EXPECT_TITLE_PART in title:
                found = (hwnd, title, rect)
                break
        if found:
            break

    if not found:
        print("  [FAIL] 45 秒内未出现主窗口")
        dump = [t for _h, t, _r in top_level_windows()][:15]
        print("  当前可见窗口:", dump)
        proc.terminate()
        return 1

    hwnd, title, rect = found
    print(f"  [PASS] 主窗口已显示: {title!r}")
    print(f"         位置 {rect[0]},{rect[1]}  尺寸 {rect[2]}x{rect[3]}")

    ok = True
    if rect[2] < 400 or rect[3] < 300:
        print(f"  [WARN] 窗口尺寸偏小: {rect[2]}x{rect[3]}")
    if not user32.IsWindowVisible(hwnd):
        print("  [FAIL] 窗口存在但不可见")
        ok = False

    # 前台置顶检查
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.5)
    fg = user32.GetForegroundWindow()
    print(f"  [{'PASS' if fg == hwnd else 'INFO'}] 可置于前台 (foreground={fg == hwnd})")

    # 截图存证
    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QGuiApplication
        app = QApplication.instance() or QApplication([])
        screen = QGuiApplication.primaryScreen()
        pm = screen.grabWindow(hwnd)
        out = os.path.join(os.path.expanduser("~"), "AppData", "Local", "Temp",
                           "hgd_run_bat_window.png")
        pm.save(out)
        print(f"  [PASS] 窗口截图: {out}")
    except Exception as e:
        print(f"  [INFO] 截图跳过: {e}")

    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
    print("\n结论:", "run.bat 双击可正常启动 GUI" if ok else "存在问题")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
