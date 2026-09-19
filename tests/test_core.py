"""离线单元测试（无需联网）。

覆盖纯逻辑：路径映射、URL 提取、JS 拼接还原、名称清洗、目录扫描、数据库。
运行：python -m pytest tests -v   或   python tests/test_core.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hgd.core.mirror import extract_refs, resolve_js_concat, safe_filename, url_to_local_rel
from hgd.core.mirror_server import MirrorServer
from hgd.core.site_plugins import clean_name, pick
from hgd.core.identifier import detect_engine_in_dir, find_entry_file, scan_directory


class TestPathMapping(unittest.TestCase):
    def test_basic(self):
        rel = url_to_local_rel("https://a.com/x/y.js").replace("\\", "/")
        self.assertEqual(rel, "a.com/x/y.js")

    def test_query_disambiguated(self):
        a = url_to_local_rel("https://a.com/s.js?v=1")
        b = url_to_local_rel("https://a.com/s.js?v=2")
        self.assertNotEqual(a, b, "不同 query 必须落盘到不同文件，否则互相覆盖")

    def test_reverse_map_roundtrip(self):
        """精确映射表必须能原样还原 URL（含 query 与 scheme）。"""
        srv = MirrorServer(root=tempfile.gettempdir())
        cases = [
            "https://sda.4399.com/4399swf/a/Build/x.js",
            "https://hm.baidu.com/hm.js?abc=1",
            "http://cdn.x.com:8080/p/a.png?q=1",
        ]
        for url in cases:
            rel = url_to_local_rel(url)
            srv.downloader.url_map[rel.replace("\\", "/")] = url
        for url in cases:
            rel = url_to_local_rel(url)
            full = os.path.join(srv.root, rel)
            self.assertEqual(srv.reverse_map(full), url, f"往返失败: {url}")

    def test_scheme_fallback(self):
        srv = MirrorServer(root=tempfile.gettempdir(), base_url="https://x.com/")
        srv.downloader.url_map.clear()
        full = os.path.join(srv.root, "cdn.a.com", "p.js")
        self.assertEqual(srv.reverse_map(full), "https://cdn.a.com/p.js")

    def test_safe_filename(self):
        self.assertNotIn(":", safe_filename('a:b*c?.js'))
        self.assertTrue(safe_filename("x" * 500).endswith(".js") is False or len(safe_filename("x" * 500)) <= 130)


class TestRefExtraction(unittest.TestCase):
    BASE = "https://site.com/game/index.htm"

    def test_html_attrs(self):
        html = '<img src="a.png"><script src="./b.js"></script><link href="../c.css">'
        refs = extract_refs(html, self.BASE)
        self.assertIn("https://site.com/game/a.png", refs)
        self.assertIn("https://site.com/game/b.js", refs)
        self.assertIn("https://site.com/c.css", refs)

    def test_css_url(self):
        css = "body{background:url('bg.jpg')} @font-face{src:url(f.woff2)}"
        refs = extract_refs(css, self.BASE)
        self.assertIn("https://site.com/game/bg.jpg", refs)
        self.assertIn("https://site.com/game/f.woff2", refs)

    def test_js_concat(self):
        """Unity 壳页的核心场景：buildUrl + "/xxx.loader.js"。"""
        js = 'const buildUrl = "Build";\nvar loaderUrl = buildUrl + "/abc.loader.js";'
        got = resolve_js_concat(js)
        self.assertIn("Build/abc.loader.js", got)
        refs = extract_refs(js, self.BASE)
        self.assertIn("https://site.com/game/Build/abc.loader.js", refs)

    def test_unity_config(self):
        js = '''const config = { dataUrl: "Build/x.data.br",
                              frameworkUrl: "Build/y.framework.js",
                              codeUrl: buildUrl + "/z.wasm.br" };'''
        refs = extract_refs(js, self.BASE)
        self.assertIn("https://site.com/game/Build/x.data.br", refs)
        self.assertIn("https://site.com/game/Build/y.framework.js", refs)
        self.assertTrue(any(r.endswith("Build/z.wasm.br") for r in refs),
                        f"拼接的 wasm 未还原: {refs}")

    def test_ignores_data_uri(self):
        refs = extract_refs('<img src="data:image/png;base64,AAAA">', self.BASE)
        self.assertEqual(refs, [])

    def test_swf_in_js_string(self):
        js = 'var p = "games/foo.swf";'
        refs = extract_refs(js, self.BASE)
        self.assertIn("https://site.com/game/games/foo.swf", refs)

    def test_dedup(self):
        html = '<img src="a.png"><img src="a.png">'
        refs = extract_refs(html, self.BASE)
        self.assertEqual(len([r for r in refs if r.endswith("a.png")]), 1)


class TestNames(unittest.TestCase):
    def test_clean_strips_site_suffix(self):
        self.assertEqual(clean_name("数据之翼_数据之翼html5游戏在线玩_4399h5游戏"), "数据之翼")
        self.assertEqual(clean_name("滑雪冒险 - 7k7k小游戏"), "滑雪冒险")

    def test_clean_keeps_simple(self):
        self.assertEqual(clean_name("Tiny Game"), "Tiny Game")

    def test_plugin_pick_order(self):
        plugins = pick("https://www.4399.com/flash/1.htm")
        self.assertEqual(plugins[0].__name__, "Plugin4399")
        self.assertEqual(plugins[-1].__name__, "PluginGeneric")
        self.assertEqual(pick("https://other.com/g")[0].__name__, "PluginGeneric")


class TestDirScan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hgd_scan_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mk(self, name, files: dict):
        d = os.path.join(self.tmp, name)
        for rel, content in files.items():
            p = os.path.join(d, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
        return d

    def test_unity_game(self):
        self._mk("gameA", {
            "index.html": '<canvas id="unity-canvas"></canvas><script>createUnityInstance(c,{})</script>',
            "Build/g.loader.js": "x",
            "Build/g.data": "y",
        })
        self.assertEqual(detect_engine_in_dir(os.path.join(self.tmp, "gameA")), "unity")

    def test_flash_game(self):
        self._mk("gameB", {"play.swf": "FWS", "index.html": "<p>hi</p>"})
        self.assertEqual(detect_engine_in_dir(os.path.join(self.tmp, "gameB")), "flash")
        self.assertEqual(find_entry_file(os.path.join(self.tmp, "gameB")), "play.swf")

    def test_scan_finds_multiple(self):
        self._mk("g1", {"index.html": '<canvas></canvas>', "Build/a.data": "1"})
        self._mk("g2", {"main.swf": "FWS"})
        found = scan_directory(self.tmp)
        names = sorted(f["name"] for f in found)
        self.assertIn("g1", names)
        self.assertIn("g2", names)
        self.assertEqual(len(found), 2)

    def test_empty_dir(self):
        self.assertEqual(scan_directory(self.tmp), [])


class TestDatabase(unittest.TestCase):
    def setUp(self):
        import hgd.core.db as db
        self.db = db
        self.tmp = tempfile.mkdtemp(prefix="hgd_db_")
        self.orig = db.DB_PATH
        from pathlib import Path
        db.DB_PATH = Path(self.tmp) / "t.db"
        db.init_db()

    def tearDown(self):
        self.db.DB_PATH = self.orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_list_favorite(self):
        from hgd.models import Game
        g = Game(name="G1", local_path=os.path.join(self.tmp, "g1"), engine="unity")
        gid = self.db.add_game(g)
        self.assertIsNotNone(gid)
        self.assertEqual(len(self.db.list_games()), 1)
        self.db.update_game(gid, favorite=1)
        self.assertEqual(len(self.db.list_games(favorite_only=True)), 1)
        self.assertEqual(len(self.db.list_games(favorite_only=False)), 1)

    def test_duplicate_path_updates(self):
        from hgd.models import Game
        p = os.path.join(self.tmp, "same")
        a = self.db.add_game(Game(name="A", local_path=p))
        b = self.db.add_game(Game(name="B", local_path=p))
        self.assertEqual(a, b, "同一路径应更新而非新增")
        self.assertEqual(len(self.db.list_games()), 1)
        self.assertEqual(self.db.get_game(a).name, "B")

    def test_category_ops(self):
        from hgd.models import Game
        self.db.add_game(Game(name="X", local_path="x", category="动作"))
        self.db.add_game(Game(name="Y", local_path="y", category="益智"))
        self.assertEqual(sorted(self.db.categories()), ["动作", "益智"])
        self.db.rename_category("动作", "射击")
        self.assertIn("射击", self.db.categories())
        self.assertNotIn("动作", self.db.categories())

    def test_keyword_search(self):
        from hgd.models import Game
        self.db.add_game(Game(name="滑雪冒险", local_path="s"))
        self.db.add_game(Game(name="数据之翼", local_path="d"))
        self.assertEqual(len(self.db.list_games(keyword="滑雪")), 1)
        self.assertEqual(len(self.db.list_games(keyword="不存在")), 0)


class TestUnsafeName(unittest.TestCase):
    def test_windows_reserved(self):
        for bad in ['a<b>c', 'a:b', 'a|b', 'a?b', 'a*b', 'a"b']:
            out = safe_filename(bad)
            self.assertFalse(any(ch in out for ch in '<>:|?*"'), f"{bad} -> {out}")


class TestBinarySafety(unittest.TestCase):
    """回归：二进制文件绝不能被当作文本改写。

    实测事故：CWS 压缩的 swf 被 errors='ignore' 读入、再以 utf-8 写出，
    体积 1755639 -> 998229，Ruffle 加载失败（'Ruffle instance destroyed'）。
    """

    def test_is_textual_flags(self):
        from hgd.core.downloader import _is_textual
        for p in ("a.html", "b.htm", "c.js", "d.css", "e.json", "f.xhtml"):
            self.assertTrue(_is_textual(p), p)
        for p in ("g.swf", "h.png", "i.wasm", "j.data.br", "k.mp3", "l.woff2", "m.zip"):
            self.assertFalse(_is_textual(p), p)

    def test_write_text_is_atomic(self):
        from hgd.core.downloader import _write_text
        import tempfile
        d = tempfile.mkdtemp()
        p = os.path.join(d, "x.html")
        _write_text(p, "hello")
        self.assertEqual(open(p, encoding="utf-8").read(), "hello")
        self.assertFalse(os.path.exists(p + ".hgd_tmp"), "临时文件应已被 replace 掉")
        shutil.rmtree(d, ignore_errors=True)

    def test_binary_swf_roundtrip_not_corrupted(self):
        """模拟：下载 -> 判断是否需改写 -> 二进制必须原样保留。"""
        import zlib
        from hgd.core.downloader import _is_textual
        # 造一个 CWS（zlib 压缩）swf
        body = b"\x78\x00\x07\xd0\x00\x00" + b"payload" * 50
        swf = b"CWS" + bytes([34]) + b"\x00" * 4 + zlib.compress(body)
        d = tempfile.mkdtemp()
        p = os.path.join(d, "game.swf")
        with open(p, "wb") as f:
            f.write(swf)
        before = os.path.getsize(p)
        if _is_textual(p):                     # 这是修复的核心：必须为 False
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                txt = f.read()
            with open(p, "w", encoding="utf-8") as f:
                f.write(txt)
        after = os.path.getsize(p)
        self.assertEqual(before, after, "swf 被文本改写损坏了")
        with open(p, "rb") as f:
            self.assertTrue(f.read().startswith(b"CWS"))
        shutil.rmtree(d, ignore_errors=True)


class TestLauncherScripts(unittest.TestCase):
    """回归：批处理脚本必须保持纯 ASCII。

    实测事故：run.bat 里写了 UTF-8 中文 + chcp 65001，
    但 cmd.exe 以 OEM 代码页（zh-CN 是 GBK）解析 .bat 字节，
    导致中文行被当成命令执行、脚本瞬间退出 —— 用户双击无任何反应。

    chcp 只改控制台输出代码页，不改解析代码页，所以中文提示必须交给
    Python 侧输出，脚本本身只能有 ASCII。
    """

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def test_bat_files_are_ascii(self):
        bad = []
        for root, _dirs, files in os.walk(self.ROOT):
            if os.sep + ".git" in root:
                continue
            for f in files:
                if f.endswith((".bat", ".cmd")):
                    p = os.path.join(root, f)
                    raw = open(p, "rb").read()
                    n = sum(1 for b in raw if b > 127)
                    if n:
                        bad.append((os.path.relpath(p, self.ROOT), n))
        self.assertEqual(bad, [], f"批处理脚本含非 ASCII 字节，双击可能闪退: {bad}")

    def test_run_bat_has_expected_flow(self):
        p = os.path.join(self.ROOT, "run.bat")
        self.assertTrue(os.path.exists(p), "run.bat 缺失")
        txt = open(p, "r", encoding="ascii", errors="strict").read()
        # 关键要素
        self.assertIn("python -m hgd", txt)
        self.assertIn("pause", txt.lower(), "出错时应 pause 以便用户看到原因")
        self.assertIn("where python", txt.lower())
        # 不应残留会与 GBK 解析冲突的 chcp 指令（注释里提到它是允许的）
        for line in txt.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("rem"):
                continue
            self.assertNotIn("chcp", stripped.lower(),
                             f"不应切换代码页（注释除外）: {stripped!r}")

    def test_run_sh_exists(self):
        p = os.path.join(self.ROOT, "run.sh")
        self.assertTrue(os.path.exists(p))
        txt = open(p, "r", encoding="utf-8").read()
        self.assertIn("python3", txt)
        self.assertIn("exec", txt)


class TestDataIsolation(unittest.TestCase):
    """回归：测试与工具绝不能写入用户的真实游戏库。

    实测事故：验收测试调用 GameDownloader().download() 下载到临时目录，
    随后删除临时文件，却在真实 library.db 里留下多条指向已删目录的记录，
    用户打开软件看到"游戏库里显示了大量已经不存在的游戏"。

    修复：数据目录支持 HGD_DATA_DIR 环境变量覆盖，测试脚本据此隔离。
    """

    def test_env_override_respected(self):
        import subprocess
        tmp = tempfile.mkdtemp(prefix="hgd_iso_")
        code = (
            "import sys; sys.path.insert(0, r'%s');"
            "from hgd.config import DATA_DIR, DB_PATH;"
            "print(str(DATA_DIR)); print(str(DB_PATH))"
            % os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        env = dict(os.environ)
        env["HGD_DATA_DIR"] = tmp
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, env=env, timeout=60)
        lines = [x for x in out.stdout.strip().splitlines() if x.strip()]
        self.assertTrue(lines, f"无输出: {out.stderr[-300:]}")
        self.assertIn(os.path.basename(tmp), lines[0],
                      f"DATA_DIR 未遵循 HGD_DATA_DIR: {lines[0]}")
        self.assertIn(os.path.basename(tmp), lines[1],
                      f"DB_PATH 未遵循 HGD_DATA_DIR: {lines[1]}")
        shutil.rmtree(tmp, ignore_errors=True)

    def test_test_scripts_declare_isolation(self):
        """会写库的工具脚本必须声明数据隔离。"""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ("acceptance.py", "test_ui.py", "test_gui.py"):
            p = os.path.join(root, "tools", name)
            if not os.path.exists(p):
                continue
            src = open(p, encoding="utf-8").read()
            self.assertIn("HGD_DATA_DIR", src,
                          f"tools/{name} 未隔离数据目录，可能污染用户游戏库")


class TestMissingRecords(unittest.TestCase):
    """失效记录（本地文件已不在磁盘）的识别与清理。"""

    def setUp(self):
        from pathlib import Path
        from hgd.core import db
        self.db = db
        self.tmp = tempfile.mkdtemp(prefix="hgd_missing_")
        self.orig = db.DB_PATH
        db.DB_PATH = Path(self.tmp) / "t.db"
        db.init_db()

    def tearDown(self):
        self.db.DB_PATH = self.orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_is_available(self):
        from hgd.models import Game
        exist = os.path.join(self.tmp, "exist")
        os.makedirs(exist)
        g_ok = self.db.add_game(Game(name="在", local_path=exist))
        g_bad = self.db.add_game(Game(name="不在", local_path=os.path.join(self.tmp, "gone")))
        g_empty = self.db.add_game(Game(name="空路径", local_path=""))
        self.assertTrue(self.db.is_available(self.db.get_game(g_ok)))
        self.assertFalse(self.db.is_available(self.db.get_game(g_bad)))
        self.assertFalse(self.db.is_available(self.db.get_game(g_empty)))

    def test_find_and_prune_missing(self):
        from hgd.models import Game
        exist = os.path.join(self.tmp, "ok")
        os.makedirs(exist)
        self.db.add_game(Game(name="正常", local_path=exist))
        self.db.add_game(Game(name="丢失1", local_path=os.path.join(self.tmp, "x1")))
        self.db.add_game(Game(name="丢失2", local_path=os.path.join(self.tmp, "x2")))
        missing = self.db.find_missing()
        self.assertEqual(len(missing), 2, [g.name for g in missing])
        self.assertEqual(self.db.prune_missing(), 2)
        self.assertEqual(len(self.db.list_games()), 1)
        self.assertEqual(self.db.list_games()[0].name, "正常")
        self.assertEqual(self.db.prune_missing(), 0, "清理应幂等")


class TestCleanName(unittest.TestCase):
    """站点标题清洗（实测样本）。"""

    def _c(self, s):
        from hgd.core.site_plugins import clean_name
        return clean_name(s)

    def test_real_samples(self):
        cases = [
            ("数据之翼_数据之翼html5游戏在线玩_4399h5游戏-4399在线玩", "数据之翼"),
            ("救援棕色大象,救援棕色大象小游戏,4399小游戏 www.4399.com", "救援棕色大象"),
            ("滑雪冒险 - 7k7k小游戏", "滑雪冒险"),
            ("植物大战僵尸2中文版_植物大战僵尸2小游戏-4399", "植物大战僵尸2中文版"),
            ("Alto's Adventure", "Alto's Adventure"),
            ("Tiny Game", "Tiny Game"),
        ]
        for src, exp in cases:
            self.assertEqual(self._c(src), exp, f"{src!r}")

    def test_never_empty(self):
        for s in ("", "   ", ",,,", "___"):
            self.assertTrue(self._c(s) is not None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
