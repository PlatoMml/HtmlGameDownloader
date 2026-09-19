"""广告清理功能测试。

关键要求：清理必须**有效**且**不误伤**。
误伤游戏自身逻辑比残留广告更严重，因此负向用例同样重要。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hgd.core import adclean


class TestAdScriptRemoval(unittest.TestCase):
    def test_removes_adsense_script(self):
        html = ('<html><head>'
                '<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js"></script>'
                '</head><body>game</body></html>')
        out, st = adclean.clean_html(html)
        self.assertNotIn("googlesyndication", out)
        self.assertIn("game", out)
        self.assertEqual(st["scripts"], 1)

    def test_removes_baidu_tongji(self):
        html = '<script src="//hm.baidu.com/hm.js?abc123"></script><canvas></canvas>'
        out, st = adclean.clean_html(html)
        self.assertNotIn("hm.baidu.com", out)
        self.assertIn("canvas", out)

    def test_removes_4399_ad_api(self):
        html = '<script>var u="https://h.api.4399.com/h5mini-2.0/h5api-interface.php";</script><p>x</p>'
        out, _ = adclean.clean_html(html)
        self.assertNotIn("h.api.4399.com", out)
        self.assertIn("<p>x</p>", out)

    def test_removes_cnzz(self):
        html = '<script src="https://s4.cnzz.com/z_stat.php?id=1"></script><div>ok</div>'
        out, _ = adclean.clean_html(html)
        self.assertNotIn("cnzz.com", out)
        self.assertIn("ok", out)


class TestAdCallsNeutralized(unittest.TestCase):
    def test_adsbygoogle_push(self):
        html = ('<script>'
                '(adsbygoogle = window.adsbygoogle || []).push({});'
                '</script><body>g</body>')
        out, st = adclean.clean_html(html)
        self.assertNotIn(".push({})", out)
        # 应保留 adsbygoogle 变量定义，避免后续引用报错
        self.assertIn("adsbygoogle", out)
        self.assertGreaterEqual(st["calls"], 1)

    def test_adbreak_adconfig(self):
        html = ('<script>var adBreak = (adConfig = function (o) { adsbygoogle.push(o); });'
                '</script><body>g</body>')
        out, st = adclean.clean_html(html)
        self.assertGreaterEqual(st["calls"], 1)

    def test_no_uncaught_reference(self):
        """清理后不应留下会抛 ReferenceError 的裸调用。"""
        html = '<script>adsbygoogle.push({slot:"1"});</script><p>g</p>'
        out, _ = adclean.clean_html(html)
        self.assertNotIn("adsbygoogle.push", out)


class TestNoFalsePositives(unittest.TestCase):
    """负向：绝不能误伤游戏自身代码。"""

    def test_keeps_normal_words_with_ad_substring(self):
        html = ('<script>function load(){ var shadow=1; var header=2; '
                'var gradient=3; var adapter=4; return load+shadow+header+gradient+adapter; }'
                '</script><canvas id="unity-canvas"></canvas>')
        out, st = adclean.clean_html(html)
        for word in ("function load", "shadow", "header", "gradient", "adapter"):
            self.assertIn(word, out, f"误删了 {word}")
        self.assertEqual(st["scripts"] + st["containers"] + st["calls"], 0)

    def test_js_files_never_rewritten(self):
        """JS 属于游戏逻辑，绝不能做广告改写。

        实测事故：Unity 的 framework.js 含
        `adsbygoogle_present: !!window.adsbygoogle` 这类只读遥测，
        既不加载广告也不发请求，正则改写会破坏引擎文件。
        """
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "framework.js")
        content = ('var buildUrl="Build";'
                   'var probe={adsbygoogle_present:!!window.adsbygoogle,'
                   'doubleclick_seen:signal.indexOf("doubleclick")!==-1};')
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        st = adclean.clean_text_file(p)
        self.assertEqual(st, {}, "JS 文件不应被清理")
        self.assertEqual(open(p, encoding="utf-8").read(), content)
        shutil.rmtree(tmp, ignore_errors=True)

    def test_keeps_unity_loader_in_html(self):
        """HTML 里内联的 Unity 加载器逻辑必须原样保留。"""
        html = ('<script>const buildUrl = "Build";'
                'const loaderUrl = buildUrl + "/7a2d.loader.js";'
                'const config = { dataUrl: buildUrl + "/x.data.br" };</script>'
                '<canvas id="unity-canvas"></canvas>')
        out, _ = adclean.clean_html(html)
        self.assertIn("buildUrl", out)
        self.assertIn("loader.js", out)
        self.assertIn("x.data.br", out)
        self.assertIn("unity-canvas", out)

    def test_keeps_game_scripts(self):
        html = ('<script src="game.js"></script>'
                '<script src="sda.4399.com/4399swf/a/Build/loader.js"></script>'
                '<link rel="stylesheet" href="TemplateData/style.css">')
        out, st = adclean.clean_html(html)
        self.assertIn("game.js", out)
        self.assertIn("Build/loader.js", out)
        self.assertIn("style.css", out)
        self.assertEqual(st["scripts"], 0)

    def test_does_not_touch_binary(self):
        tmp = tempfile.mkdtemp()
        swf = os.path.join(tmp, "g.swf")
        payload = b"CWS" + bytes([34]) + b"\x00" * 4 + b"googlesyndication.com" * 10
        with open(swf, "wb") as f:
            f.write(payload)
        before = os.path.getsize(swf)
        st = adclean.clean_text_file(swf)
        self.assertEqual(st, {}, "二进制文件不应被清理")
        self.assertEqual(os.path.getsize(swf), before)
        shutil.rmtree(tmp, ignore_errors=True)

    def test_does_not_touch_wasm_or_data(self):
        tmp = tempfile.mkdtemp()
        for name in ("a.wasm", "b.data.br", "c.png", "d.mp3"):
            p = os.path.join(tmp, name)
            with open(p, "wb") as f:
                f.write(b"adsbygoogle googlesyndication" * 20)
            self.assertEqual(adclean.clean_text_file(p), {}, name)
        shutil.rmtree(tmp, ignore_errors=True)


class TestAdContainers(unittest.TestCase):
    def test_removes_ad_div(self):
        html = ('<body><div id="ad_slot_1" class="advert">ad</div>'
                '<canvas id="game"></canvas></body>')
        out, st = adclean.clean_html(html)
        self.assertNotIn("ad_slot_1", out)
        self.assertIn('id="game"', out)
        self.assertGreaterEqual(st["containers"], 1)

    def test_keeps_normal_div(self):
        html = '<div id="game-container" class="main-wrapper">x</div>'
        out, st = adclean.clean_html(html)
        self.assertIn("game-container", out)
        self.assertEqual(st["containers"], 0)


class TestTrackingStrip(unittest.TestCase):
    def test_removes_tracking_pixel(self):
        html = ('<img src="//hm.baidu.com/hm.gif?x=1" width="1" height="1">'
                '<img src="logo.png">')
        out, st = adclean.clean_html(html)
        self.assertNotIn("hm.baidu.com", out)
        self.assertIn("logo.png", out)
        self.assertGreaterEqual(st["tracking"], 1)


class TestGameDirClean(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hgd_adclean_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_clean_dir_reports_counts(self):
        # 一个含广告的入口页 + 一个游戏脚本 + 一个二进制
        with open(os.path.join(self.tmp, "index.html"), "w", encoding="utf-8") as f:
            f.write('<html><head>'
                    '<script src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js"></script>'
                    '<script src="game.js"></script>'
                    '</head><body><div id="ad_banner">ad</div>'
                    '<canvas id="unity-canvas"></canvas></body></html>')
        with open(os.path.join(self.tmp, "game.js"), "w", encoding="utf-8") as f:
            f.write("function load(){return 1} var shadow=2;")
        with open(os.path.join(self.tmp, "g.swf"), "wb") as f:
            f.write(b"FWS" + b"googlesyndication" * 20)

        total = adclean.clean_game_dir(self.tmp)
        self.assertEqual(total["files"], 1, f"应只清理 index.html: {total}")
        self.assertGreaterEqual(total["scripts"] + total["containers"], 2)

        idx = open(os.path.join(self.tmp, "index.html"), encoding="utf-8").read()
        self.assertNotIn("googlesyndication", idx)
        self.assertNotIn("ad_banner", idx)
        self.assertIn("game.js", idx)
        self.assertIn("unity-canvas", idx)

        # 游戏脚本与二进制不受影响
        self.assertIn("shadow", open(os.path.join(self.tmp, "game.js"), encoding="utf-8").read())
        self.assertIn(b"googlesyndication", open(os.path.join(self.tmp, "g.swf"), "rb").read())

    def test_scan_residue(self):
        with open(os.path.join(self.tmp, "index.html"), "w", encoding="utf-8") as f:
            f.write('<script src="//hm.baidu.com/hm.js"></script><div id="ad_slot">x</div>')
        with open(os.path.join(self.tmp, "clean.js"), "w", encoding="utf-8") as f:
            f.write("var a=1;")
        hits = adclean.scan_ad_residue(self.tmp)
        names = [h["file"] for h in hits]
        self.assertIn("index.html", names)
        self.assertNotIn("clean.js", names)

    def test_clean_is_idempotent(self):
        p = os.path.join(self.tmp, "a.html")
        with open(p, "w", encoding="utf-8") as f:
            f.write('<script src="//hm.baidu.com/hm.js"></script><div>g</div>')
        first = adclean.clean_text_file(p)
        self.assertTrue(first)
        second = adclean.clean_text_file(p)
        self.assertEqual(second, {}, "第二次清理应无变化")


class TestEncodingSafety(unittest.TestCase):
    def test_gbk_file_preserved(self):
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "a.html")
        content = '<html><head><script src="//hm.baidu.com/hm.js"></script></head>' \
                  '<body>中文内容测试</body></html>'
        with open(p, "wb") as f:
            f.write(content.encode("gb18030"))
        st = adclean.clean_text_file(p)
        self.assertTrue(st)
        raw = open(p, "rb").read()
        # 应仍能以 gb18030 解码且中文完好
        text = raw.decode("gb18030")
        self.assertIn("中文内容测试", text)
        self.assertNotIn("hm.baidu.com", text)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
