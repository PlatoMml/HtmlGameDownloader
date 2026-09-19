"""一键全量验收：按需求逐条检查。

覆盖用户提出的 7 项功能，每项都有可验证的判据。
运行：python tools/acceptance.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 关键：把数据目录指向临时位置，避免验收测试污染用户的真实游戏库。
# 必须在本项目任何模块 import config 之前设置。
_SANDBOX = tempfile.mkdtemp(prefix="hgd_acceptance_")
os.environ["HGD_DATA_DIR"] = _SANDBOX

os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    "--no-sandbox --enable-unsafe-swiftshader --use-gl=angle --use-angle=swiftshader "
    "--autoplay-policy=no-user-gesture-required --log-level=3"
)

RESULTS = []


def sec(title: str):
    print(f"\n{'='*64}\n{title}\n{'='*64}")


def rec(req: str, name: str, ok: bool, detail: str = ""):
    RESULTS.append((req, name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


# ---------------------------------------------------------------- 1. 网址识别

def test_identify():
    sec("需求 1：识别用户提供的网址")
    from hgd.core.identifier import identify_url
    cases = [
        ("https://www.4399.com/flash/262895_3.htm", "数据之翼"),
        ("https://www.4399.com/flash/262782_3.htm", "滑雪冒险"),
        ("https://www.4399.com/flash/206501.htm", "救援棕色大象"),
    ]
    for url, expect in cases:
        r = identify_url(url)
        rec("1", f"识别 {url.rsplit('/',1)[-1]}",
            r.ok and r.name == expect,
            f"name={r.name!r} engine={r.engine} entry={(r.entry_url or '')[-28:]}")

    # 尺寸识别
    r = identify_url("https://www.4399.com/flash/262782_3.htm")
    rec("1", "识别画布尺寸", r.width == 860 and r.height == 540, f"{r.width}x{r.height}")

    # 非法输入
    r = identify_url("")
    rec("1", "空网址优雅报错", not r.ok and "空" in r.message, r.message)
    r = identify_url("https://this-domain-should-not-exist-xyz123.com/a")
    rec("1", "无效网址优雅报错", not r.ok, r.message[:44])


# ---------------------------------------------------------------- 2. 游戏名可改

def test_name_editable():
    sec("需求 2：识别游戏名（可手动修改）")
    from hgd.core.site_plugins import clean_name
    ok = clean_name("数据之翼_数据之翼html5游戏在线玩_4399h5游戏-4399在线在线玩") == "数据之翼"
    rec("2", "自动清洗站点后缀", ok)

    from hgd.models import Game
    from hgd.core import db
    tmp = tempfile.mkdtemp(prefix="acc_db_")
    from pathlib import Path
    old = db.DB_PATH
    db.DB_PATH = Path(tmp) / "t.db"
    db.init_db()
    gid = db.add_game(Game(name="识别出的名字", local_path=os.path.join(tmp, "g")))
    db.update_game(gid, name="用户改的名字")
    rec("2", "手动改名生效", db.get_game(gid).name == "用户改的名字")
    db.DB_PATH = old
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 3. 自选目录

def test_custom_dir():
    sec("需求 3：下载到用户自选目录")
    tmp = tempfile.mkdtemp(prefix="acc_dir_")
    sub = os.path.join(tmp, "我的游戏", "2026")
    os.makedirs(sub, exist_ok=True)

    from hgd.core.downloader import GameDownloader
    g = GameDownloader().download(
        "https://www.4399.com/flash/262782_3.htm", dest_dir=sub)
    ok = bool(g) and os.path.abspath(g.local_path).startswith(os.path.abspath(sub))
    rec("3", "下载到指定嵌套目录", ok, g.local_path if g else "失败")
    if g:
        rec("3", "产物含可播放入口", os.path.exists(os.path.join(g.local_path, "index.html")))
        rec("3", "产物含元数据", os.path.exists(os.path.join(g.local_path, "game_meta.json")))
        rec("3", "资源已落盘", g.size > 1_000_000, f"{g.size/1024/1024:.1f} MB")
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 4. 收藏夹

def test_favorites():
    sec("需求 4：收藏夹 / 分类 / 重命名")
    tmp = tempfile.mkdtemp(prefix="acc_fav_")
    from pathlib import Path
    from hgd.core import db
    old = db.DB_PATH
    db.DB_PATH = Path(tmp) / "t.db"
    db.init_db()
    from hgd.models import Game

    a = db.add_game(Game(name="游戏A", local_path=os.path.join(tmp, "a"), category="动作"))
    b = db.add_game(Game(name="游戏B", local_path=os.path.join(tmp, "b"), category="益智"))
    db.add_game(Game(name="游戏C", local_path=os.path.join(tmp, "c"), category="动作"))

    db.update_game(a, favorite=1); db.update_game(b, favorite=1)
    rec("4", "收藏游戏", len(db.list_games(favorite_only=True)) == 2)
    rec("4", "取消收藏", (db.update_game(a, favorite=0),
                        len(db.list_games(favorite_only=True)) == 1)[1])

    rec("4", "分类列表", sorted(db.categories()) == ["动作", "益智"], str(db.categories()))
    db.rename_category("动作", "竞技")
    rec("4", "分类重命名", "竞技" in db.categories() and "动作" not in db.categories())

    db.update_game(b, name="游戏B改名")
    rec("4", "游戏重命名", db.get_game(b).name == "游戏B改名")

    db.update_game(b, category="竞技")
    rec("4", "移动到其它分类", db.get_game(b).category == "竞技")
    rec("4", "按分类过滤", len(db.list_games(favorite_only=True, category="竞技")) == 1)

    db.delete_game(b)
    rec("4", "删除记录", db.get_game(b) is None)

    db.DB_PATH = old
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 5. 目录扫描

def test_scan():
    sec("需求 5：扫描目录识别多个游戏")
    from hgd.core.identifier import scan_directory
    tmp = tempfile.mkdtemp(prefix="acc_scan_")
    layout = {
        "Unity游戏": {"index.html": '<canvas id="unity-canvas"></canvas><script>createUnityInstance(c,{})</script>',
                      "Build/x.loader.js": "a", "Build/x.data": "b"},
        "Flash游戏": {"play.swf": "FWS1234567890"},
        "HTML5游戏": {"index.html": "<canvas></canvas>", "game.js": "console.log(1)"},
        "不是游戏": {"说明.txt": "nothing"},
    }
    for nm, files in layout.items():
        for rel, c in files.items():
            p = os.path.join(tmp, nm, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w", encoding="utf-8").write(c)

    items = scan_directory(tmp)
    names = sorted(i["name"] for i in items)
    rec("5", "识别出多个游戏", len(items) == 3, str(names))
    rec("5", "识别 Unity", any(i["engine"] == "unity" for i in items))
    rec("5", "识别 Flash", any(i["engine"] == "flash" for i in items))
    rec("5", "识别 HTML5", any(i["engine"] == "html" for i in items))
    rec("5", "排除非游戏", "不是游戏" not in names)
    rec("5", "识别入口文件", all(i["entry"] for i in items),
        str({i["name"]: i["entry"] for i in items}))

    # 单文件 swf
    tmp2 = tempfile.mkdtemp(prefix="acc_scan2_")
    open(os.path.join(tmp2, "single.swf"), "wb").write(b"FWS" + b"0" * 100)
    items2 = scan_directory(tmp2)
    rec("5", "识别单个 swf 文件", len(items2) == 1 and items2[0]["engine"] == "flash")

    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(tmp2, ignore_errors=True)


# ---------------------------------------------------------------- 6. 播放器

def test_player():
    sec("需求 6：打开游戏 / 音量 / 静音 / 两种全屏")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent
    app = QApplication.instance() or QApplication(sys.argv)

    tmp = tempfile.mkdtemp(prefix="acc_play_")
    os.makedirs(tmp, exist_ok=True)
    open(os.path.join(tmp, "index.html"), "w").write(
        "<html><body><canvas width=640 height=480></canvas></body></html>")

    from hgd.ui.player import GamePlayer
    from hgd.models import Game
    p = GamePlayer(Game(id=1, name="测试", local_path=tmp, entry_file="index.html",
                        engine="html"))
    p.show()
    app.processEvents()
    rec("6", "打开游戏窗口", p.isVisible())

    # 音量
    from hgd.config import config
    rec("6", "默认音量 30%", p.slider.value() == 30, f"{p.slider.value()}%")
    p.slider.setValue(88); app.processEvents()
    rec("6", "拖动调节音量", p._volume == 88)
    rec("6", "音量范围 0-100", p.slider.minimum() == 0 and p.slider.maximum() == 100)

    # 静音按钮（在音量条左边）
    layout = p.toolbar.layout()
    mute_idx = layout.indexOf(p.btn_mute)
    slider_idx = layout.indexOf(p.slider)
    rec("6", "喇叭在音量条左侧", mute_idx < slider_idx, f"mute#{mute_idx} < slider#{slider_idx}")
    rec("6", "喇叭有矢量图标", not p.btn_mute.icon().isNull())

    p._toggle_mute(); app.processEvents()
    rec("6", "点击喇叭静音", p._muted and "静音" in p.lbl_vol.text(), p.lbl_vol.text())
    p._toggle_mute(); app.processEvents()
    rec("6", "再点取消静音", not p._muted, p.lbl_vol.text())

    # 网页全屏
    p._toggle_web_fullscreen(); app.processEvents()
    rec("6", "网页全屏生效", p._web_fullscreen and p.isMaximized())
    rec("6", "网页全屏保留工具条", p.toolbar.isVisible())
    p._toggle_web_fullscreen(); app.processEvents()
    rec("6", "退出网页全屏", not p._web_fullscreen)

    # 全屏
    p._enter_fullscreen(); app.processEvents()
    rec("6", "全屏铺满显示器", p.isFullScreen())
    rec("6", "全屏隐藏工具条", not p.toolbar.isVisible())
    rec("6", "无菜单栏遮挡", p.menuBar() is None or not p.menuBar().isVisible())
    rec("6", "禁用右键菜单(顶部不弹遮挡)",
        p.contextMenuPolicy() == Qt.NoContextMenu)
    p._exit_fullscreen(); app.processEvents()
    rec("6", "退出全屏恢复工具条", p.toolbar.isVisible() and not p._fullscreen)

    # ESC 退出
    p._enter_fullscreen(); app.processEvents()
    p.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    app.processEvents()
    rec("6", "ESC 退出全屏", not p._fullscreen)

    # 比例铺满：用真实下载产物验证（含 #frame 包装层），临时 HTML 没有包装层
    ready = [False]
    p.view.loadFinished.connect(lambda ok: ready.__setitem__(0, True))
    p.close()

    real = r"G:\traetool\temp\hgd-work\final_test\滑雪冒险"
    if os.path.isdir(real) and os.path.exists(os.path.join(real, "index.html")):
        p2 = GamePlayer(Game(id=2, name="滑雪冒险", local_path=real,
                             entry_file="index.html", engine="unity"))
        p2.resize(1024, 700)
        p2.show()
        got2 = [False]
        p2.view.loadFinished.connect(lambda ok: got2.__setitem__(0, True))
        for _ in range(60):
            app.processEvents(); time.sleep(0.1)
            if got2[0]:
                break
        time.sleep(1.2)
        res = {}
        p2.view.page().runJavaScript(
            "(function(){var f=document.getElementById('frame');"
            "if(!f) return 'noframe';"
            "var r=f.getBoundingClientRect();"
            "return Math.round(r.width)+'x'+Math.round(r.height);})()",
            lambda v: res.setdefault("v", v))
        for _ in range(25):
            app.processEvents(); time.sleep(0.1)
        v = str(res.get("v") or "")
        ok = "x" in v and v != "noframe"
        ratio_ok = False
        if ok:
            try:
                w, h = (int(x) for x in v.split("x"))
                ratio_ok = abs(w / h - 16 / 9) < 0.06
            except Exception:
                pass
        rec("6", "游戏保持比例铺满窗口", ok and ratio_ok,
            f"frame={v} (16:9 期望)")
        p2.close()
    else:
        rec("6", "游戏保持比例铺满窗口", False, "缺少真实游戏产物，先运行下载")

    p.close()
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 7. MCP

def test_mcp():
    sec("需求 7：MCP 功能（端口可自定义）")
    import requests
    from hgd.mcp.server import McpServer

    srv = McpServer(port=8801)
    port = srv.start()
    rec("7", "MCP 启动", port > 0, f"port={port}")

    base = f"http://127.0.0.1:{port}"

    def call(method, params=None):
        return requests.post(base, json={"jsonrpc": "2.0", "id": 1,
                                        "method": method, "params": params or {}},
                             timeout=90).json()

    r = call("initialize")
    rec("7", "initialize 握手", r["result"]["protocolVersion"].startswith("2024"))
    tools = call("tools/list")["result"]["tools"]
    rec("7", "工具列表", len(tools) == 7, f"{len(tools)} 个")
    rec("7", "ping", call("ping")["result"] == {})

    d = json.loads(call("tools/call", {"name": "identify_url",
                                       "arguments": {"url": "https://www.4399.com/flash/206501.htm"}}
                        )["result"]["content"][0]["text"])
    rec("7", "工具 identify_url", d["ok"] and d["engine"] == "flash", d["name"])

    d = json.loads(call("tools/call", {"name": "extract_refs",
                                       "arguments": {"url": "https://sda.4399.com/4399swf/upload_swf/ftp28/gamehwq/20190606/13.swf",
                                                     "keyword": ""}})["result"]["content"][0]["text"])
    rec("7", "工具 extract_refs", "count" in d, f"count={d.get('count')}")

    d = json.loads(call("tools/call", {"name": "list_games", "arguments": {}})
                   ["result"]["content"][0]["text"])
    rec("7", "工具 list_games", "count" in d, f"{d.get('count')} 个")

    # 错误处理（此时 8801 仍在监听）
    e = call("tools/call", {"name": "no_such_tool"})
    rec("7", "未知工具报错", "error" in e)
    r = requests.post(base, data=b"{bad", timeout=10)
    rec("7", "非法 JSON 报错", r.status_code == 400)

    # 端口自定义（先停掉再换端口）
    srv.stop()
    srv2 = McpServer(port=8877)
    p2 = srv2.start()
    rec("7", "自定义端口", p2 == 8877, f"{p2}")
    srv2.stop()

    # 内置 skill.md
    from hgd.config import SKILL_DIR
    skill = os.path.join(SKILL_DIR, "SKILL.md")
    ok = os.path.exists(skill) and os.path.getsize(skill) > 3000
    rec("7", "内置 SKILL.md 技能文档", ok,
        f"{os.path.getsize(skill)} bytes" if ok else "缺失")
    if ok:
        txt = open(skill, encoding="utf-8").read()
        rec("7", "技能含分引擎方案", all(k in txt for k in ("Unity", "Flash", "Ruffle", "懒补漏")))
        rec("7", "技能含排查流程", "排查流程" in txt)


# ---------------------------------------------------------------- 附加

def test_saves_and_restart():
    sec("需求 8：游戏存档与重玩")
    from hgd.core import saves
    from hgd.models import Game

    tmp = tempfile.mkdtemp(prefix="acc_save_")
    g = Game(id=8001, name="存档测试", local_path=os.path.join(tmp, "g"))

    # 存档基础能力
    check_ok = saves.current_gen(g) == 1
    rec("8", "初始存档代际为 1", check_ok, f"gen={saves.current_gen(g)}")
    rec("8", "无存档时判定正确", not saves.save_exists(g))

    d = saves.save_dir(g)
    d.mkdir(parents=True, exist_ok=True)
    (d / "data.db").write_bytes(b"x" * 2048)
    rec("8", "有存档时判定正确", saves.save_exists(g))
    rec("8", "统计存档占用", saves.save_size(g) > 0, f"{saves.save_size(g)} B")

    # 重玩 = 切换到全新代际
    ok, msg = saves.clear_save(g)
    rec("8", "重玩重置存档成功", ok, msg)
    rec("8", "重置后进入新代际", saves.current_gen(g) == 2,
        f"gen={saves.current_gen(g)}")
    rec("8", "新代际为空白（全新存档）", not saves.save_exists(g))

    # 端口稳定（存档能读回的前提）
    p1 = saves.port_for(g)
    p2 = saves.port_for(Game(id=8001, name="存档测试", local_path=os.path.join(tmp, "g")))
    rec("8", "端口按游戏固定（存档可续接）", p1 == p2, f"{p1} == {p2}")
    other = saves.port_for(Game(id=8002, name="另一个", local_path=os.path.join(tmp, "h")))
    rec("8", "不同游戏端口互不冲突", other != p1, f"{p1} vs {other}")

    # 重玩按钮与二次确认（UI 层）
    try:
        from PySide6.QtWidgets import QApplication, QDialog, QCheckBox, QPushButton, QLabel
        app = QApplication.instance() or QApplication(sys.argv)
        from hgd.ui.player import GamePlayer
        gdir = os.path.join(tmp, "playable")
        os.makedirs(gdir, exist_ok=True)
        with open(os.path.join(gdir, "index.html"), "w", encoding="utf-8") as f:
            f.write("<html><body>t</body></html>")
        gp = Game(id=8003, name="按钮测试", local_path=gdir,
                  entry_file="index.html", engine="html")
        pl = GamePlayer(gp)
        pl.show()
        app.processEvents()
        rec("8", "播放器提供重玩按钮", hasattr(pl, "btn_restart")
            and pl.btn_restart.text() == "重玩", pl.btn_restart.text() if hasattr(pl, "btn_restart") else "")

        holder = {}
        real_exec = QDialog.exec

        def cap(self):
            holder["dlg"] = self
            return QDialog.Rejected

        QDialog.exec = cap
        try:
            pl._confirm_restart()
        finally:
            QDialog.exec = real_exec

        dlg = holder.get("dlg")
        if dlg:
            labels = " ".join(w.text() for w in dlg.findChildren(QLabel))
            btns = [b.text() for b in dlg.findChildren(QPushButton)]
            chks = dlg.findChildren(QCheckBox)
            rec("8", "重玩需二次确认（勾选）", len(chks) == 1, str([c.text() for c in chks]))
            rec("8", "确认框说明不可恢复",
                ("无法恢复" in labels or "永久丢失" in labels))
            ok_btns = [b for b in dlg.findChildren(QPushButton) if "清除存档" in b.text()]
            if ok_btns and chks:
                rec("8", "未勾选时禁止执行", not ok_btns[0].isEnabled())
            else:
                rec("8", "未勾选时禁止执行", False, "未找到按钮或勾选框")
        else:
            rec("8", "重玩弹出确认对话框", False, "未捕获到对话框")
        pl.close()
        app.processEvents()
    except Exception as e:
        rec("8", "重玩 UI 检查", False, f"{type(e).__name__}: {e}")

    shutil.rmtree(tmp, ignore_errors=True)


def test_extra():
    sec("附加：健壮性与边界")
    from hgd.core.mirror import safe_filename, url_to_local_rel

    rec("附加", "Windows 非法字符过滤",
        not any(c in safe_filename('a<b>c:d|e?f*g"h') for c in '<>:|?*"'))
    rec("附加", "超长文件名截断", len(safe_filename("x" * 500)) <= 130)

    a = url_to_local_rel("https://x.com/s.js?v=1")
    b = url_to_local_rel("https://x.com/s.js?v=2")
    rec("附加", "带 query 资源不互相覆盖", a != b)

    # 二进制安全（真实事故回归）
    import zlib
    from hgd.core.downloader import _is_textual
    body = b"\x78\x00\x07\xd0" + b"z" * 200
    swf = b"CWS" + bytes([34]) + b"\x00" * 4 + zlib.compress(body)
    tmp = tempfile.mkdtemp()
    fp = os.path.join(tmp, "g.swf")
    open(fp, "wb").write(swf)
    before = os.path.getsize(fp)
    if _is_textual(fp):
        txt = open(fp, encoding="utf-8", errors="ignore").read()
        open(fp, "w", encoding="utf-8").write(txt)
    rec("附加", "二进制文件不被文本改写损坏", os.path.getsize(fp) == before,
        f"{before} -> {os.path.getsize(fp)}")
    shutil.rmtree(tmp, ignore_errors=True)

    # URL 往返映射
    from hgd.core.mirror_server import MirrorServer
    s = MirrorServer(root=tempfile.gettempdir())
    for u in ("https://a.com/Build/x.js", "https://a.com/hm.js?x=1",
              "http://cdn.b.com:8080/p.png?q=2"):
        rel = url_to_local_rel(u)
        s.downloader.url_map[rel.replace("\\", "/")] = u
        back = s.reverse_map(os.path.join(s.root, rel))
        rec("附加", f"URL 精确往返 {u.split('//')[1][:26]}", back == u, f"-> {back[:44]}")

    # 内置 Ruffle
    from hgd.config import RUFFLE_DIR
    has_js = (RUFFLE_DIR / "ruffle.js").exists()
    wasms = list(RUFFLE_DIR.glob("*.wasm"))
    rec("附加", "内置 Ruffle 运行时", has_js and len(wasms) >= 1,
        f"ruffle.js + {len(wasms)} wasm" if has_js else "缺失")


def main() -> int:
    print("网页游戏下载器 — 全量验收")
    print(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"数据沙箱: {_SANDBOX}  (不会影响你的真实游戏库)")

    for fn in (test_identify, test_name_editable, test_custom_dir, test_favorites,
               test_scan, test_player, test_saves_and_restart, test_mcp, test_extra):
        try:
            fn()
        except Exception as e:
            import traceback
            print(f"  [ERROR] {fn.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
            RESULTS.append(("?", fn.__name__, False, f"异常 {e}"))

    # 清理沙箱（含验收过程中下载的游戏文件）
    try:
        shutil.rmtree(_SANDBOX, ignore_errors=True)
    except Exception:
        pass

    sec("汇总")
    passed = sum(1 for r in RESULTS if r[2])
    total = len(RESULTS)
    by_req = {}
    for req, name, ok, _d in RESULTS:
        by_req.setdefault(req, [0, 0])
        by_req[req][1] += 1
        if ok:
            by_req[req][0] += 1
    for req in sorted(by_req):
        p, t = by_req[req]
        print(f"  需求 {req}: {p}/{t} {'✓' if p == t else '✗'}")
    print(f"\n  总计: {passed}/{total}")
    if passed != total:
        print("\n  失败项：")
        for req, name, ok, detail in RESULTS:
            if not ok:
                print(f"    ✗ [{req}] {name}  {detail}")
    print("\n" + ("全部通过 ✓" if passed == total else "存在失败项 ✗"))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
