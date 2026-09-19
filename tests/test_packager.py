"""打包功能与管理类 MCP 工具的测试。"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# 隔离数据目录
_SANDBOX = tempfile.mkdtemp(prefix="hgd_pkg_test_")
os.environ["HGD_DATA_DIR"] = _SANDBOX

from hgd.core import packager
from hgd.models import Game


def _make_game_dir(root: str, name: str = "测试游戏") -> str:
    d = os.path.join(root, name)
    os.makedirs(os.path.join(d, "Build"), exist_ok=True)
    with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as f:
        f.write('<html><body><canvas id="unity-canvas"></canvas></body></html>')
    with open(os.path.join(d, "Build", "a.data"), "wb") as f:
        f.write(b"\x00" * 20000)
    with open(os.path.join(d, "game_meta.json"), "w", encoding="utf-8") as f:
        f.write('{"name":"测试游戏","engine":"unity"}')
    with open(os.path.join(d, ".hgd_urlmap.json"), "w", encoding="utf-8") as f:
        f.write('{"a":"b"}')
    return d


class TestPackager(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hgd_pkg_")
        self.gdir = _make_game_dir(self.tmp)
        self.out = os.path.join(self.tmp, "out")
        self.game = Game(id=1, name="测试游戏", local_path=self.gdir,
                         entry_file="index.html", engine="unity",
                         source_url="https://example.com/g")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_find_7z(self):
        p = packager.find_7z()
        self.assertTrue(p, "找不到 7z 工具")
        self.assertTrue(os.path.exists(p), p)

    def test_package_creates_archive(self):
        ok, msg, path = packager.package_game(self.game, out_dir=self.out, level=1)
        self.assertTrue(ok, msg)
        self.assertTrue(os.path.exists(path), path)
        self.assertTrue(path.endswith(".7z"), path)
        self.assertGreater(os.path.getsize(path), 0)

    def test_only_packages_the_selected_game(self):
        """建两个游戏，打包其中一个，不应把另一个也包进去。"""
        _make_game_dir(self.tmp, "另一个游戏")
        ok, msg, path = packager.package_game(self.game, out_dir=self.out, level=1)
        self.assertTrue(ok, msg)
        ex = os.path.join(self.tmp, "ex_only")
        rc, out = packager._run([packager.find_7z(), "x", path, f"-o{ex}", "-y"])
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.exists(os.path.join(ex, "index.html")))
        self.assertFalse(os.path.exists(os.path.join(ex, "另一个游戏")),
                         "不该把其它游戏打进包里")

    def test_archive_contains_launcher_and_readme(self):
        """验证包内包含启动脚本与说明。

        不用 `7z l` 的输出做断言：7zr 的控制台输出走 OEM 代码页，
        中文文件名会显示成乱码（实际文件名是正确的）。
        解压后检查文件系统才可靠。
        """
        ok, msg, path = packager.package_game(self.game, out_dir=self.out, level=1)
        self.assertTrue(ok, msg)
        ex = os.path.join(self.tmp, "ex_launcher")
        rc, out = packager._run([packager.find_7z(), "x", path, f"-o{ex}", "-y"])
        self.assertEqual(rc, 0, out)
        names = set(os.listdir(ex))
        for expected in ("启动游戏.bat", "使用说明.txt", "start-game.sh",
                         "_serve.py", "index.html"):
            self.assertIn(expected, names, f"包内缺少 {expected}：{sorted(names)}")
        # README 内容应含使用与部署说明
        readme = open(os.path.join(ex, "使用说明.txt"), encoding="utf-8").read()
        self.assertIn("启动游戏.bat", readme)
        self.assertIn("部署到网站", readme)

    def test_internal_files_excluded(self):
        """内部文件不该出现在分发包里。"""
        ok, msg, path = packager.package_game(self.game, out_dir=self.out, level=1)
        ex = os.path.join(self.tmp, "ex_internal")
        packager._run([packager.find_7z(), "x", path, f"-o{ex}", "-y"])
        names = set(os.listdir(ex))
        self.assertNotIn(".hgd_urlmap.json", names)
        self.assertNotIn("game_meta.json", names)

    def test_extract_and_verify(self):
        ok, msg, path = packager.package_game(self.game, out_dir=self.out, level=1)
        ex = os.path.join(self.tmp, "ex")
        rc, out = packager._run([packager.find_7z(), "x", path, f"-o{ex}", "-y"])
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.exists(os.path.join(ex, "index.html")))
        self.assertTrue(os.path.exists(os.path.join(ex, "Build", "a.data")))
        self.assertTrue(os.path.exists(os.path.join(ex, "启动游戏.bat")))
        # 数据完整性
        self.assertEqual(
            os.path.getsize(os.path.join(ex, "Build", "a.data")), 20000)

    def test_launcher_bat_is_ascii(self):
        """启动脚本必须纯 ASCII —— 否则 Windows 双击会闪退。"""
        ok, msg, path = packager.package_game(self.game, out_dir=self.out, level=1)
        ex = os.path.join(self.tmp, "ex2")
        packager._run([packager.find_7z(), "x", path, f"-o{ex}", "-y"])
        raw = open(os.path.join(ex, "启动游戏.bat"), "rb").read()
        bad = [b for b in raw if b > 127]
        self.assertEqual(bad, [], "启动脚本含非 ASCII 字节，双击会闪退")

    def test_serve_py_is_valid_python(self):
        ok, msg, path = packager.package_game(self.game, out_dir=self.out, level=1)
        ex = os.path.join(self.tmp, "ex3")
        packager._run([packager.find_7z(), "x", path, f"-o{ex}", "-y"])
        src = open(os.path.join(ex, "_serve.py"), encoding="utf-8").read()
        compile(src, "_serve.py", "exec")   # 语法必须合法

    def test_missing_dir_fails_cleanly(self):
        bad = Game(id=2, name="不存在", local_path=os.path.join(self.tmp, "nope"))
        ok, msg, path = packager.package_game(bad, out_dir=self.out)
        self.assertFalse(ok)
        self.assertIsNone(path)

    def test_estimate_size(self):
        self.assertGreater(packager.estimate_size(self.game), 20000)

    def test_staging_cleaned(self):
        packager.package_game(self.game, out_dir=self.out, level=1)
        leftovers = [d for d in os.listdir(self.out) if d.startswith(".staging_")]
        self.assertEqual(leftovers, [], f"暂存目录未清理: {leftovers}")


class TestMCPManagement(unittest.TestCase):
    """MCP 管理类工具。"""

    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        from hgd.config import DATA_DIR
        from hgd.core import db
        db.init_db()
        cls.db = db
        cls.tmp = tempfile.mkdtemp(prefix="hgd_mcpmgmt_")
        cls.gdir = _make_game_dir(cls.tmp)
        cls.gid = db.add_game(Game(name="管理测试", local_path=cls.gdir,
                                   engine="unity", category="未分类"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _call(self, name, args):
        from hgd.mcp.server import HANDLERS
        return HANDLERS[name](args)

    def test_get_game(self):
        r = self._call("get_game", {"id": self.gid})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["game"]["name"], "管理测试")
        self.assertTrue(r["game"]["exists"])
        self.assertIn("save_bytes", r["game"])

    def test_get_game_by_name(self):
        r = self._call("get_game", {"name": "管理测试"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["game"]["id"], self.gid)

    def test_get_game_not_found(self):
        r = self._call("get_game", {"id": 999999})
        self.assertFalse(r["ok"])
        self.assertIn("找不到", r["error"])

    def test_update_game(self):
        r = self._call("update_game", {
            "id": self.gid, "new_name": "改名后", "category": "动作",
            "favorite": True, "note": "agent 写入的备注"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["game"]["name"], "改名后")
        self.assertEqual(r["game"]["category"], "动作")
        self.assertTrue(r["game"]["favorite"])
        self.assertEqual(r["game"]["note"], "agent 写入的备注")

    def test_update_game_no_fields(self):
        r = self._call("update_game", {"id": self.gid})
        self.assertFalse(r["ok"])
        self.assertIn("没有要修改", r["error"])

    def test_clean_ads_dry_run_and_apply(self):
        # 造一个带广告的页面
        ad_page = os.path.join(self.gdir, "ad.html")
        with open(ad_page, "w", encoding="utf-8") as f:
            f.write('<html><head>'
                    '<script src="https://pagead2.googlesyndication.com/x.js"></script>'
                    '</head><body><div id="ad_slot">a</div>'
                    '<canvas id="unity-canvas"></canvas></body></html>')

        r = self._call("clean_ads", {"id": self.gid, "dry_run": True})
        self.assertTrue(r["ok"], r)
        self.assertGreaterEqual(r["found"], 1, r)

        r2 = self._call("clean_ads", {"id": self.gid})
        self.assertTrue(r2["ok"], r2)
        self.assertGreaterEqual(r2["cleaned_files"], 1, r2)
        self.assertGreaterEqual(r2["removed_scripts"], 1, r2)
        self.assertEqual(r2["residue_after"], 0, r2)

        txt = open(ad_page, encoding="utf-8").read()
        self.assertIn("unity-canvas", txt, "游戏画布被误删")
        self.assertNotIn("googlesyndication", txt)

    def test_package_via_mcp(self):
        out = os.path.join(self.tmp, "mcp_out")
        r = self._call("package_game", {"id": self.gid, "out_dir": out, "level": 1})
        self.assertTrue(r["ok"], r)
        self.assertTrue(os.path.exists(r["archive"]), r)
        self.assertGreater(r["size_bytes"], 0)

    def test_list_pending_saves(self):
        r = self._call("list_pending_saves", {})
        self.assertTrue(r["ok"], r)
        self.assertIn("total_bytes", r)

    def test_delete_game_record_only(self):
        from hgd.models import Game as G
        d = _make_game_dir(self.tmp, "待删除")
        gid = self.db.add_game(G(name="待删除", local_path=d))
        r = self._call("delete_game", {"id": gid, "delete_files": False})
        self.assertTrue(r["ok"], r)
        self.assertIsNone(self.db.get_game(gid))
        self.assertTrue(os.path.isdir(d), "只删记录时不应动文件")

    def test_delete_game_with_files(self):
        from hgd.models import Game as G
        d = _make_game_dir(self.tmp, "连文件删除")
        gid = self.db.add_game(G(name="连文件删除", local_path=d))
        r = self._call("delete_game", {"id": gid, "delete_files": True})
        self.assertTrue(r["ok"], r)
        self.assertFalse(os.path.exists(d), "应删除磁盘文件")

    def test_all_tools_have_handlers(self):
        from hgd.mcp.server import TOOLS, HANDLERS
        for t in TOOLS:
            self.assertIn(t["name"], HANDLERS, f"{t['name']} 未实现")


class TestMCPProtocol(unittest.TestCase):
    """HTTP 层端到端。"""

    def test_tools_list_and_call(self):
        import requests
        from hgd.mcp.server import McpServer, TOOLS
        srv = McpServer(port=8842)
        try:
            srv.start()
            base = "http://127.0.0.1:8842"
            j = requests.post(base, json={
                "jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                timeout=20).json()
            names = [t["name"] for t in j["result"]["tools"]]
            self.assertGreaterEqual(len(names), 13, names)
            for expected in ("get_game", "update_game", "delete_game",
                             "clean_ads", "package_game", "list_pending_saves"):
                self.assertIn(expected, names)

            j2 = requests.post(base, json={
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "list_games", "arguments": {}}}, timeout=30).json()
            self.assertIn("content", j2["result"])
        finally:
            srv.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
