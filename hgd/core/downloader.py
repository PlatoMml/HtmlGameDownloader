"""游戏下载器：识别 -> 镜像 -> 封装成本地可直接玩的包。

产物结构（每个游戏一个文件夹）：
    <下载目录>/<游戏名>/
        index.html          <- 生成的播放入口（负责 Unity 尺寸适配、Flash 走 Ruffle）
        game_meta.json      <- 元数据（来源/引擎/尺寸）
        assets/...          <- 镜像下来的原始资源（保持网络目录结构）

为什么保留 index.html 包装层而不是直接打开原始入口：
1. Unity 壳页常写死 canvas 尺寸，不铺满窗口；包装层接管"保持比例铺满"逻辑
2. Flash 需要 Ruffle 运行时，包装层负责加载本地 ruffle.js
3. 统一屏蔽广告/统计脚本，避免离线时卡加载
"""
from __future__ import annotations

import json
import os
import shutil
import time
from typing import Callable, Optional
from urllib.parse import urlparse

from ..config import RUFFLE_DIR, config
from ..models import DownloadProgress, Game, IdentifyResult
from .db import add_game
from .http_client import HttpError, decode_bytes, get_bytes
from .identifier import identify_url
from .mirror import Downloader, safe_dirname, safe_filename, url_to_local_rel
from .site_plugins import pick

ProgressCB = Callable[[DownloadProgress], None]


# ---------------------------------------------------------------- 包装层模板

_WRAPPER_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<title>{title}</title>
<style>
  html,body{{margin:0;padding:0;width:100%;height:100%;overflow:hidden;background:#0b0b0f;}}
  /* 用固定定位四边贴齐，保证 stage 永远等于视口大小 */
  #stage{{position:fixed;left:0;top:0;right:0;bottom:0;overflow:hidden;}}
  #frame{{border:0;display:block;position:absolute;left:50%;top:50%;
          transform:translate(-50%,-50%);background:#000;}}
</style>
</head>
<body>
<div id="stage"><iframe id="frame" src="{inner}" allowfullscreen allow="autoplay;fullscreen"></iframe></div>
<script>
(function () {{
  var RATIO = {ratio};

  // 按"保持比例铺满"计算：尽量占满，同时不超出窗口
  function computeSize(W, H) {{
    if (!RATIO) return [W, H];
    var w = W, h = Math.round(W / RATIO);
    if (h > H) {{ h = H; w = Math.round(H * RATIO); }}
    return [w, h];
  }}

  function fit() {{
    var frame = document.getElementById('frame');
    if (!frame) return;
    var W = window.innerWidth, H = window.innerHeight;
    if (!W || !H) return;
    var wh = computeSize(W, H);
    frame.style.width = wh[0] + 'px';
    frame.style.height = wh[1] + 'px';
  }}

  window.addEventListener('resize', fit);
  window.addEventListener('orientationchange', fit);
  // DOM 就绪、资源加载完成、以及若干次延迟重算，覆盖各种迟到的尺寸变化
  if (document.readyState === 'loading') {{
    document.addEventListener('DOMContentLoaded', fit);
  }} else {{
    fit();
  }}
  window.addEventListener('load', fit);
  [0, 60, 200, 500, 1200, 2500].forEach(function (ms) {{ setTimeout(fit, ms); }});

  // 宿主通过 postMessage 下发音量；也转发给 iframe 内部
  window.addEventListener('message', function (e) {{
    var d = e.data || {{}};
    if (d.type !== 'hgd-volume') return;
    var v = d.muted ? 0 : (d.value / 100);
    try {{
      var fr = document.getElementById('frame');
      if (fr && fr.contentWindow) fr.contentWindow.postMessage(d, '*');
      var els = document.querySelectorAll('audio,video');
      for (var i = 0; i < els.length; i++) {{
        els[i].volume = v; els[i].muted = !!d.muted;
      }}
      window.__hgdVolume = v;
    }} catch (err) {{}}
  }});
}})();
</script>
</body>
</html>
"""

_FLASH_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  html,body{{margin:0;padding:0;width:100%;height:100%;overflow:hidden;background:#0b0b0f;}}
  #stage{{position:fixed;left:0;top:0;right:0;bottom:0;overflow:hidden;}}
  #ruffle{{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);}}
</style>
<script src="{ruffle_js}"></script>
</head>
<body>
<div id="stage"><div id="ruffle"></div></div>
<script>
(function () {{
  var RATIO = {ratio};
  var box = document.getElementById('ruffle');
  var swf = "{swf}";
  var W = 0, H = 0;
  var player = null;

  function computeSize(cw, ch) {{
    if (!RATIO) return [cw, ch];
    var w = cw, h = Math.round(cw / RATIO);
    if (h > ch) {{ h = ch; w = Math.round(ch * RATIO); }}
    return [w, h];
  }}

  function fit() {{
    var wh = computeSize(window.innerWidth, window.innerHeight);
    W = wh[0]; H = wh[1];
    if (player) {{
      player.style.width = W + 'px';
      player.style.height = H + 'px';
      box.style.width = W + 'px';
      box.style.height = H + 'px';
    }} else {{
      render();
    }}
  }}

  function render() {{
    if (!window.RufflePlayer) {{ setTimeout(render, 120); return; }}
    if (!W || !H) {{ fit(); return; }}
    var r = window.RufflePlayer.newest();
    player = r.createPlayer();
    box.appendChild(player);
    box.style.width = W + 'px'; box.style.height = H + 'px';
    player.style.width = W + 'px'; player.style.height = H + 'px';
    player.load({{ url: swf, backgroundColor: '#000000',
                  allowScriptAccess: true, quality: 'high' }}).then(function () {{
      try {{ player.volume = window.__hgdVolume != null ? window.__hgdVolume : 0.3; }} catch (e) {{}}
    }});
  }}

  window.addEventListener('resize', fit);
  window.addEventListener('orientationchange', fit);
  window.addEventListener('load', fit);
  [0, 60, 200, 500, 1200].forEach(function (ms) {{ setTimeout(fit, ms); }});

  window.addEventListener('message', function (e) {{
    var d = e.data || {{}};
    if (d.type !== 'hgd-volume') return;
    var v = d.muted ? 0 : (d.value / 100);
    window.__hgdVolume = v;
    try {{ if (player) {{ player.volume = v; player.muted = !!d.muted; }} }} catch (err) {{}}
  }});
}})();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------- 下载器

class GameDownloader:
    def __init__(self, cb: Optional[ProgressCB] = None):
        self.cb = cb
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def _emit(self, phase: str, msg: str = "", **kw) -> None:
        if self.cb:
            self.cb(DownloadProgress(phase=phase, message=msg, **kw))

    # -------------------------------------------------- 主流程

    def download(
        self,
        url: str,
        name: Optional[str] = None,
        dest_dir: Optional[str] = None,
        category: str = "未分类",
        favorite: int = 0,
        max_depth: Optional[int] = None,
        lazy: bool = True,
    ) -> Optional[Game]:
        """完整下载流程。返回入库的 Game 或 None。"""
        dest_dir = dest_dir or config.get("download_dir")
        max_depth = max_depth if max_depth is not None else int(config.get("max_depth"))
        os.makedirs(dest_dir, exist_ok=True)

        self._emit("analyse", f"识别 {url}")
        info = identify_url(url)
        if not info.ok:
            self._emit("error", info.message)
            return None

        final_name = (name or info.name or "未命名游戏").strip()
        folder = os.path.join(dest_dir, safe_dirname(final_name))
        folder = _unique_dir(folder)
        os.makedirs(folder, exist_ok=True)

        referer = f"{urlparse(info.entry_url).scheme}://{urlparse(info.entry_url).netloc}/"
        dl = Downloader(folder, referer=referer, cb=self.cb)

        # 1) 下载入口页
        self._emit("download", "下载入口页")
        entry_local = dl.fetch_one(info.entry_url)
        if not entry_local:
            self._emit("error", "入口页下载失败")
            return None
        entry_rel = os.path.relpath(entry_local, folder).replace("\\", "/")

        # 2) 站点改写（去 document.domain / 广告脚本）
        # 只对 HTML 文本做改写；.swf 等二进制一旦按文本读回写就会损坏
        # （实测：CWS 压缩 swf 被 errors='ignore' 读取再以 utf-8 写回，体积从 1.75MB 变 998KB 且无法解析）
        if _is_textual(entry_local):
            text = _read_text(entry_local)
            if text:
                for plugin in pick(url):
                    try:
                        text = plugin.rewrite(text, info.entry_url)
                    except Exception:
                        pass
                _write_text(entry_local, text)

        # 3) 递归镜像
        self._emit("mirror", "镜像资源")
        stats = dl.mirror(info.entry_url, entry_local, max_depth=max_depth)
        self._emit("mirror", f"资源 {stats['files']} 个", done=stats["files"])

        # 4) 生成播放入口
        self._emit("package", "生成播放入口")
        # 比例优先用页面声明的 targetAspect（站点注入的自适应脚本最权威），
        # 其次用 canvas 属性尺寸。实测 case：canvas 属性 860x540 但站点强制 16:9。
        ratio = float(info.raw.get("ratio") or 0)
        if not (0.3 < ratio < 3.5):
            ratio = (info.width / info.height) if (info.width and info.height) else 0
        inner = _wrapper_for(info.engine, entry_rel, folder, final_name, ratio, info)

        # 5) 元数据
        meta = {
            "name": final_name, "source_url": url, "entry_url": info.entry_url,
            "engine": info.engine, "width": info.width, "height": info.height,
            "ratio": ratio, "downloaded_at": time.time(),
            "files": stats["files"], "bytes": stats["bytes"],
            "failed": stats["failed_urls"],
        }
        with open(os.path.join(folder, "game_meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        game = Game(
            name=final_name, source_url=url, local_path=folder,
            entry_file=os.path.basename(inner), engine=info.engine,
            category=category, favorite=favorite, size=stats["bytes"],
        )
        game.id = add_game(game)
        self._emit("done", f"完成：{final_name}（{stats['files']} 个文件）",
                   done=stats["files"], total=stats["files"])
        return game


# ---------------------------------------------------------------- helpers

def _unique_dir(path: str) -> str:
    if not os.path.exists(path):
        return path
    i = 2
    while os.path.exists(f"{path}_{i}"):
        i += 1
    return f"{path}_{i}"


def _is_textual(path: str) -> bool:
    """判断是否是可以安全按文本读写的文件。

    二进制（swf/图片/音频/wasm/压缩包）绝不能被 _read_text/_write_text 处理。
    """
    ext = os.path.splitext(path)[1].lower()
    return ext in (".html", ".htm", ".xhtml", ".js", ".css", ".json", ".txt", ".xml")


def _read_text(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8", "ignore")
    except OSError:
        return ""


def _write_text(path: str, text: str) -> None:
    try:
        # 原子替换，避免写一半损坏原文件
        tmp = path + ".hgd_tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError:
        pass


def _wrapper_for(engine: str, entry_rel: str, folder: str, title: str,
                 ratio: float, info: IdentifyResult) -> str:
    """生成包装入口页，返回其文件名。"""
    if engine == "flash" and entry_rel.lower().endswith(".swf"):
        ruffle_js = _ensure_ruffle(folder)
        html = _FLASH_HTML.format(
            title=_esc(title), ruffle_js=ruffle_js, swf=_esc(entry_rel),
            ratio=ratio or 0.75,
        )
    else:
        html = _WRAPPER_HTML.format(
            title=_esc(title), inner=_esc(entry_rel), ratio=ratio or 0,
        )
    out = os.path.join(folder, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    return "index.html"


def _ensure_ruffle(folder: str) -> str:
    """把内置 Ruffle 拷到游戏目录旁（保证离线可玩，用户无需下载）。

    返回相对游戏目录的 ruffle.js 路径。
    """
    dest = os.path.join(folder, "_runtime", "ruffle")
    os.makedirs(dest, exist_ok=True)
    if not RUFFLE_DIR.exists():
        return ""
    for f in ("ruffle.js",):
        src = RUFFLE_DIR / f
        if src.exists():
            shutil.copy2(src, os.path.join(dest, f))
    # core.*.js 与 *.wasm 是 ruffle.js 运行时按需加载的同伴文件
    for f in os.listdir(RUFFLE_DIR):
        if f.endswith(".wasm") or f.startswith("core.ruffle"):
            src = RUFFLE_DIR / f
            dst = os.path.join(dest, f)
            if src.is_file() and not os.path.exists(dst):
                try:
                    shutil.copy2(src, dst)
                except OSError:
                    pass
    return "_runtime/ruffle/ruffle.js"


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
