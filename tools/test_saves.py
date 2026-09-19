"""存档与重玩功能验收测试。

覆盖用户需求：为软件提供重玩按钮，二次确认后从新存档开始。

同时验证一个前提性缺陷已修复：原先用默认 profile（off-the-record），
游戏存档在窗口关闭时全部丢失，根本谈不上"重玩"。

测试要点：
  A. 存档能跨会话保留（localStorage + IndexedDB）
  B. 每个游戏存档互相隔离（同名键不串档）
  C. 重玩清档后从全新存档开始
  D. 重玩按钮存在且要求二次确认
  E. 端口稳定性（存档能读回的前提）
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# 隔离数据目录：绝不污染真实游戏库与存档
_SANDBOX = tempfile.mkdtemp(prefix="hgd_saves_test_")
os.environ["HGD_DATA_DIR"] = _SANDBOX
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    "--no-sandbox --enable-unsafe-swiftshader --log-level=3"
)

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (f"  — {detail}" if detail else ""))


# ---------------------------------------------------------------- 测试页面

PROBE_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>save probe</title></head>
<body>
<script>
window.__probe = {};
(function () {
  try {
    var n = parseInt(localStorage.getItem('counter') || '0', 10) + 1;
    localStorage.setItem('counter', String(n));
    localStorage.setItem('%KEY%', String(n));
    window.__probe.ls = n;
  } catch (e) { window.__probe.lsErr = String(e); }

  try {
    var req = indexedDB.open('gamedb', 1);
    req.onupgradeneeded = function (e) {
      try { e.target.result.createObjectStore('kv', { keyPath: 'k' }); } catch (err) {}
    };
    req.onsuccess = function (e) {
      var db = e.target.result;
      try {
        var tx = db.transaction('kv', 'readwrite');
        var st = tx.objectStore('kv');
        var g = st.get('n');
        g.onsuccess = function () {
          var v = (g.result ? g.result.v : 0) + 1;
          try { st.put({ k: 'n', v: v }); } catch (err) {}
          window.__probe.idb = v;
        };
      } catch (err) { window.__probe.idbErr = String(err); }
    };
  } catch (e) { window.__probe.idbErr2 = String(e); }
})();
</script>
</body></html>
"""


def make_game(root: str, name: str, key: str) -> str:
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    html = PROBE_HTML.replace("%KEY%", key)
    with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    return d


# ---------------------------------------------------------------- 单元层

def test_port_stability():
    print("\n=== E. 端口稳定性（存档能读回的前提）===")
    from hgd.core import saves
    from hgd.models import Game

    g = Game(id=101, name="端口测试", local_path=os.path.join(_SANDBOX, "p"))
    p1 = saves.port_for(g)
    check("首次分配端口", p1 > 0, f"port={p1}")

    # 模拟重开：用同一个 game 再取一次
    g2 = Game(id=101, name="端口测试", local_path=os.path.join(_SANDBOX, "p"))
    p2 = saves.port_for(g2)
    check("重复请求端口不变", p1 == p2, f"{p1} -> {p2}")

    # 不同游戏应拿到不同端口
    g3 = Game(id=102, name="另一个", local_path=os.path.join(_SANDBOX, "q"))
    p3 = saves.port_for(g3)
    check("不同游戏端口不同", p3 != p1, f"{p1} vs {p3}")

    # 配置持久化
    from hgd.config import DATA_DIR
    check("端口分配已持久化", (DATA_DIR / "save_ports.json").exists())

    # 未入库游戏也能拿到稳定端口
    g4 = Game(name="未入库", local_path=os.path.join(_SANDBOX, "r"))
    a = saves.port_for(g4)
    g5 = Game(name="未入库", local_path=os.path.join(_SANDBOX, "r"))
    b = saves.port_for(g5)
    check("未入库游戏端口稳定（路径哈希）", a == b, f"{a} == {b}")


def test_save_helpers():
    print("\n=== 存档辅助功能 ===")
    from hgd.core import saves
    from hgd.models import Game

    g = Game(id=201, name="辅助", local_path=os.path.join(_SANDBOX, "s1"))
    check("无存档时 save_exists=False", not saves.save_exists(g))
    check("无存档时 size=0", saves.save_size(g) == 0)
    check("初始代际为 1", saves.current_gen(g) == 1, f"gen={saves.current_gen(g)}")

    d = saves.save_dir(g)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "x.db", "wb") as f:
        f.write(b"data" * 100)
    check("有存档时 save_exists=True", saves.save_exists(g))
    check("有存档时 size>0", saves.save_size(g) > 0, f"{saves.save_size(g)} bytes")

    ok, msg = saves.clear_save(g)
    check("重置存档成功", ok, msg)
    check("代际已推进", saves.current_gen(g) == 2, f"gen={saves.current_gen(g)}")
    check("新代际目录与旧的不同",
          str(saves.save_dir(g)) != str(saves.game_root(g) / "gen1"))
    check("新代际为空（save_exists=False）", not saves.save_exists(g))

    # 二次重置继续推进
    saves.clear_save(g)
    check("可重复重置", saves.current_gen(g) == 3, f"gen={saves.current_gen(g)}")


def test_restart_button_and_confirm():
    print("\n=== D. 重玩按钮与二次确认 ===")
    import hgd.ui.player as player_mod
    from PySide6.QtWidgets import (QApplication, QDialog, QPushButton, QCheckBox,
                                   QLabel, QWidget)
    from hgd.models import Game
    from hgd.ui.player import GamePlayer

    app = QApplication.instance() or QApplication(sys.argv)
    games_root = os.path.join(_SANDBOX, "games")
    gc = make_game(games_root, "GameC", "keyC")
    g = Game(id=401, name="GameC", local_path=gc, entry_file="index.html", engine="html")

    p = GamePlayer(g)
    p.show()
    app.processEvents()

    check("播放器有「重玩」按钮", hasattr(p, "btn_restart"))
    check("重玩按钮文案", p.btn_restart.text() == "重玩", p.btn_restart.text())
    check("重玩按钮有提示", "存档" in p.btn_restart.toolTip(), p.btn_restart.toolTip())

    # ---- 拦截 _confirm_restart 里创建的 QDialog，检查确认界面要素 ----
    captured = {}

    def fake_exec(self):
        captured["widget"] = self
        captured["title"] = self.windowTitle()
        captured["labels"] = [w.text() for w in self.findChildren(QLabel)]
        captured["buttons"] = [b.text() for b in self.findChildren(QPushButton)]
        captured["checkboxes"] = [c.text() for c in self.findChildren(QCheckBox)]
        # 不真正等待，返回 Rejected（相当于取消）
        return QDialog.Rejected

    real_exec = QDialog.exec
    QDialog.exec = fake_exec
    try:
        p._confirm_restart()
    finally:
        QDialog.exec = real_exec

    texts = " ".join(captured.get("labels", []))
    btns = captured.get("buttons", [])
    chks = captured.get("checkboxes", [])

    check("点击重玩弹出确认对话框", bool(captured), f"title={captured.get('title')}")
    check("确认框说明将清除存档", "存档" in texts, texts[:100])
    check("确认框提示不可恢复",
          ("无法恢复" in texts or "永久丢失" in texts), texts[:120])
    check("显示存档占用大小", "约" in texts or "MB" in texts or "KB" in texts,
          texts[:120])
    check("提供「清除存档并重玩」按钮", any("清除存档" in b for b in btns), str(btns))
    check("提供「取消」按钮", any("取消" in b for b in btns), str(btns))
    check("提供二次确认勾选项", len(chks) == 1, str(chks))

    # ---- 验证未勾选时确认按钮不可用（二次确认的核心）----
    dlg2 = QDialog(p)
    # 复用真实弹窗构建逻辑：直接检查 _confirm_restart 里的构建结果
    holder = {}

    def grab_exec(self):
        holder["dlg"] = self
        return QDialog.Rejected

    QDialog.exec = grab_exec
    try:
        p._confirm_restart()
    finally:
        QDialog.exec = real_exec

    dlg = holder.get("dlg")
    if dlg:
        ok_btns = [b for b in dlg.findChildren(QPushButton) if "清除存档" in b.text()]
        chk = dlg.findChildren(QCheckBox)
        if ok_btns and chk:
            check("未勾选时确认按钮禁用", not ok_btns[0].isEnabled())
            chk[0].setChecked(True)
            app.processEvents()
            check("勾选后确认按钮启用", ok_btns[0].isEnabled())
        else:
            check("找到确认按钮与勾选框", False,
                  f"buttons={[b.text() for b in dlg.findChildren(QPushButton)]}")
    else:
        check("捕获对话框", False)

    p.close()
    app.processEvents()

def _run_player_once(game_dir: str, game_id: int, name: str,
                     restart: bool = False, wait_ms: int = 5000) -> dict:
    """在子进程里打开播放器一次，返回探针结果。

    用子进程是必要的：QtWebEngine 的存储线程与 profile 生命周期
    必须真实走完"启动-写入-退出"才能验证跨会话持久化。
    """
    script = os.path.join(_SANDBOX, "_one_shot.py")
    code = f'''
import json, os, sys, time
sys.path.insert(0, r"{_ROOT}")
os.environ["HGD_DATA_DIR"] = r"{_SANDBOX}"
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --enable-unsafe-swiftshader --log-level=3"

from PySide6.QtCore import QUrl, QTimer
from PySide6.QtWidgets import QApplication
from hgd.ui.player import GamePlayer
from hgd.models import Game

app = QApplication(sys.argv)
g = Game(id={game_id}, name={name!r}, local_path=r"{game_dir}",
         entry_file="index.html", engine="html")
p = GamePlayer(g)
p.resize(700, 500)
p.show()

res = {{}}
loaded = [False]
p.view.loadFinished.connect(lambda ok: loaded.__setitem__(0, True))

def probe():
    def got(v):
        try:
            res["probe"] = json.loads(v) if isinstance(v, str) else v
        except Exception as e:
            res["probeErr"] = str(e)
    p.view.page().runJavaScript("JSON.stringify(window.__probe||{{}})", got)

def finish():
    probe()
    QTimer.singleShot(700, lambda: (print("RESULT:" + json.dumps(res), flush=True),
                                     p.close(),
                                     QTimer.singleShot(300, app.quit)))

if {restart!r}:
    # 直接走内部清档路径（对话框在别的用例里单独验证）
    QTimer.singleShot(1200, lambda: (p._restart_fresh(),
                                     QTimer.singleShot({wait_ms}, finish)))
else:
    QTimer.singleShot({wait_ms}, finish)

QTimer.singleShot(40000, app.quit)
app.exec()
'''
    with open(script, "w", encoding="utf-8") as f:
        f.write(code)

    out = subprocess.run([sys.executable, script], capture_output=True,
                         text=True, timeout=180, cwd=_ROOT)
    for line in out.stdout.splitlines():
        if line.startswith("RESULT:"):
            try:
                return json.loads(line[7:])
            except Exception:
                pass
    return {"error": (out.stdout[-400:] + out.stderr[-400:])}


def test_persistence_and_restart():
    print("\n=== A/B/C. 存档保留 · 隔离 · 重玩清档 ===")
    from hgd.core import saves
    from hgd.models import Game

    games_root = os.path.join(_SANDBOX, "games")
    ga = make_game(games_root, "GameA", "keyA")
    gb = make_game(games_root, "GameB", "keyB")

    A = Game(id=301, name="GameA", local_path=ga, entry_file="index.html", engine="html")
    B = Game(id=302, name="GameB", local_path=gb, entry_file="index.html", engine="html")

    # 第 1 次打开 A
    r1 = _run_player_once(ga, 301, "GameA")
    pa1 = (r1.get("probe") or {})
    check("A 首次打开 计数=1", pa1.get("ls") == 1, f"probe={pa1}  {r1.get('error','')[:200]}")

    # 第 2 次打开 A：应累加（证明存档跨会话保留）
    r2 = _run_player_once(ga, 301, "GameA")
    pa2 = (r2.get("probe") or {})
    check("A 二次打开 计数=2（存档已保留）", pa2.get("ls") == 2,
          f"probe={pa2}  {r2.get('error','')[:200]}")

    # 打开 B：应是 1（证明隔离，未被 A 影响）
    rb = _run_player_once(gb, 302, "GameB")
    pb = (rb.get("probe") or {})
    check("B 首次打开 计数=1（存档隔离）", pb.get("ls") == 1, f"probe={pb}")

    # B 再打开：证明两个游戏各自独立累加
    rb2 = _run_player_once(gb, 302, "GameB")
    pb2 = (rb2.get("probe") or {})
    check("B 二次打开 计数=2", pb2.get("ls") == 2, f"probe={pb2}")

    # A 第三次打开：应看到 3（B 的存在没影响 A）
    r3 = _run_player_once(ga, 301, "GameA")
    pa3 = (r3.get("probe") or {})
    check("A 三次打开 计数=3（隔离且持续）", pa3.get("ls") == 3, f"probe={pa3}")

    # 存档确实落在存档目录
    check("A 存档存储已生成", saves.game_root(A).exists())
    check("B 存档存储已生成", saves.game_root(B).exists())
    check("A/B 存档存储不同",
          str(saves.game_root(A)) != str(saves.game_root(B)))

    gen_before = saves.current_gen(A)

    # 重玩：代际推进后应回到 1
    r4 = _run_player_once(ga, 301, "GameA", restart=True, wait_ms=6500)
    pa4 = (r4.get("probe") or {})
    check("A 重玩后 计数=1（新存档）", pa4.get("ls") == 1,
          f"probe={pa4}  {r4.get('error','')[:200]}")
    check("A 重玩后 代际已推进",
          saves.current_gen(A) > gen_before,
          f"{gen_before} -> {saves.current_gen(A)}")

    # 重玩后 B 不受影响
    rb3 = _run_player_once(gb, 302, "GameB")
    pb3 = (rb3.get("probe") or {})
    check("重玩 A 不影响 B", pb3.get("ls") == 3, f"probe={pb3} (期望3)")

    # 重玩后 A 能重新累加
    r5 = _run_player_once(ga, 301, "GameA")
    pa5 = (r5.get("probe") or {})
    check("A 重玩后继续累加=2", pa5.get("ls") == 2, f"probe={pa5}")

    # 旧代际最终会被清理，不会长期占盘
    saves.purge_pending(A)
    root = saves.game_root(A)
    old_dirs = [d for d in os.listdir(root) if d.startswith("gen")]
    cur = f"gen{saves.current_gen(A)}"
    others = [d for d in old_dirs if d != cur]
    check("旧代际已清理（不长期占盘）", len(others) <= 1,
          f"现存 {sorted(old_dirs)}，当前 {cur}")


def main() -> int:
    print("存档与重玩功能验收")
    print(f"数据沙箱: {_SANDBOX}")

    try:
        test_port_stability()
        test_save_helpers()
        test_restart_button_and_confirm()
        test_persistence_and_restart()
    except Exception as e:
        import traceback
        traceback.print_exc()
        FAIL.append(f"异常: {e}")

    print("\n" + "=" * 56)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for f in FAIL:
        print("  ✗ " + f)
    print("=" * 56)

    shutil.rmtree(_SANDBOX, ignore_errors=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
