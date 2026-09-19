"""资源镜像下载器 + 懒补漏（镜像服务器）。

## 设计思路（继承并修正参考仓库）

参考仓库最有价值的一点：**不写静态递归 HTML 解析器，而是起本地 HTTP 服务，
让浏览器运行时驱动资源下载（漏什么下什么）**。这样天然覆盖 JS 动态拼接的 URL、
CSS url()、懒加载资源——静态解析做不到。

本实现把它做成"两级"：
- **第一级（离线镜像）**：静态递归解析 + 并发镜像下载，游戏应当开箱即玩（不依赖联网）。
- **第二级（懒补漏）**：仍起本地镜像服务器，任何静态解析漏掉的资源，在浏览器实际请求时
  即时回源抓取并落盘。既是兜底，也让第一次解析不完美也能跑。

相比参考仓库修掉的坑：
- 状态码校验 + 重试退避（原来 404 页面会被当资源写盘）
- 统一 URL 解码口径（原来 %20/中文路径每轮重复下载）
- 禁止传输压缩（原来 .br 文件被解压导致 Unity 二次解压失败）
- content-type 感知的 404 判定（原来会误删二进制）
- 钩子生命周期由 MirrorServer 自己管理（原来挂到全局永不解绑）
"""
from __future__ import annotations

import html as html_lib
import json
import os
import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import unquote, urljoin, urlparse

from .http_client import HttpError, get_bytes, looks_like_404
from ..models import DownloadProgress

ProgressCB = Callable[[DownloadProgress], None]


# ---------------------------------------------------------------- URL -> 本地路径

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name: str, maxlen: int = 120, default: str = "index") -> str:
    name = unquote(name or "")
    name = _UNSAFE.sub("_", name).strip(" .")
    name = name or default
    if len(name) > maxlen:
        stem, dot, ext = name.rpartition(".")
        if dot and len(ext) <= 8:
            name = stem[: maxlen - len(ext) - 1] + dot + ext
        else:
            name = name[:maxlen]
    return name


def safe_dirname(name: str) -> str:
    return safe_filename(name, 80, "game")


def url_to_local_rel(url: str) -> str:
    """把绝对 URL 映射为本地相对路径（保持目录镜像结构）。

    这是"路径镜像"策略的核心：网络相对结构 == 本地目录结构，
    因此页面里原有的相对引用无需改写即可离线工作。
    """
    p = urlparse(url)
    host = p.netloc.replace(":", "_")
    path = unquote(p.path)
    if not path or path.endswith("/"):
        path += "_index.html"
    parts = [safe_filename(x) for x in path.split("/") if x != ""]
    rel = os.path.join(host, *parts) if parts else os.path.join(host, "_index.html")

    # 查询串会区分同名资源，但必须做安全化
    if p.query:
        q = safe_filename(p.query[:60], 60, "")
        if q:
            stem, dot, ext = rel.rpartition(".")
            rel = (f"{stem}__{q}{dot}{ext}" if dot else f"{rel}__{q}")
    return rel


def guess_extension(url: str, content_type: str) -> str:
    path = unquote(urlparse(url).path)
    stem, dot, ext = path.rpartition(".")
    ext = ext.lower()
    if ext and 1 <= len(ext) <= 6 and ext.isalnum():
        return "." + ext
    ctype = (content_type or "").split(";")[0].strip().lower()
    table = {
        "text/html": ".html", "text/css": ".css", "application/javascript": ".js",
        "text/javascript": ".js", "application/json": ".json", "image/png": ".png",
        "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp",
        "image/svg+xml": ".svg", "audio/mpeg": ".mp3", "audio/wav": ".wav",
        "audio/ogg": ".ogg", "video/mp4": ".mp4", "video/webm": ".webm",
        "application/octet-stream": ".bin", "font/woff2": ".woff2", "font/woff": ".woff",
        "application/x-font-woff2": ".woff2", "application/wasm": ".wasm",
    }
    return table.get(ctype, ".bin")


# ---------------------------------------------------------------- 资源引用提取

# 使用枚举型的属性/模式，尽量覆盖装饰/脚本/内联样式
_REF_ATTRS = (
    "src", "href", "srcset", "data-src", "data-url", "data-src2", "poster", "data-bg",
)

_ASSET_EXT = (r"(?:js|json|css|png|jpg|jpeg|gif|webp|svg|mp3|ogg|wav|m4a|mp4|webm|"
              r"woff2?|ttf|otf|swf|unityweb|unity3d|data|br|gz|wasm|bin|xml|txt|atlas|pak)")
# 引号包裹的绝对/相对资源路径
_PAT_ASSET = re.compile(
    r'''["']((?:https?:)?//[^"'\s<>()]{4,1024}\.''' + _ASSET_EXT + r'''(?:\.br|\.gz)?(?:\?[^"'\s<>]*)?)["']''',
    re.I,
)
_PAT_ASSET_REL = re.compile(
    r'''["']((?:/|\.{1,2}/)[^"'\s<>()]{1,1024}\.''' + _ASSET_EXT + r'''(?:\.br|\.gz)?(?:\?[^"'\s<>]*)?)["']''',
    re.I,
)

_PATTERNS = [
    re.compile(r'<[^>]+?\b(?:src|href|poster)\s*=\s*["\']([^"\']{1,2048})["\']', re.I),
    re.compile(r'<[^>]+?\bsrcset\s*=\s*["\']([^"\']{1,2048})["\']', re.I),
    re.compile(r"url\(\s*['\"]?([^'\")]{1,2048})['\"]?\s*\)", re.I),
    _PAT_ASSET,
    _PAT_ASSET_REL,
]

# Unity 特有：Build/xxx.loader.js 等写在 JS 变量里
_UNITY_BUILD = re.compile(
    r'["\']([^"\']*?(?:Build|StreamingAssets|TemplateData)/[^"\']*?(?:\.js|\.json|\.data|\.wasm|\.unityweb|\.br|\.gz|\.png|\.jpg)[^"\']*)["\']',
    re.I,
)

# JS 字符串常量声明：const/var/let NAME = "value";  或  NAME: "value"
_JS_CONST = re.compile(
    r'''(?:const|var|let)\s+([A-Za-z_$][\w$]*)\s*=\s*["']([^"']{0,512})["']\s*;?''',
)
_JS_PROP = re.compile(
    r'''["']?([A-Za-z_$][\w$]*)["']?\s*:\s*["']([^"']{0,512})["']''',
)
# JS 拼接引用：NAME + "/xxx.js"   或   NAME + '/xxx.js'
_JS_CONCAT = re.compile(
    r'''([A-Za-z_$][\w$]*)\s*\+\s*["']([^"']{1,512})["']''',
)


def js_const_map(text: str) -> Dict[str, str]:
    """抽取 JS 里的字符串常量：const buildUrl = "Build" / { dataUrl: "Build/x" }。

    用途：Unity 壳页普遍用 `buildUrl + "/xxx.loader.js"` 拼接路径，
    纯正则拿不到 "Build" 前缀，会把地址误解析到站点根目录。
    """
    consts: Dict[str, str] = {}
    for name, val in _JS_CONST.findall(text):
        if val and len(val) < 200 and not val.startswith(("http", "//")):
            consts.setdefault(name, val)
    for name, val in _JS_PROP.findall(text):
        if val and len(val) < 200 and not val.startswith(("http", "//")):
            consts.setdefault(name, val)
    return consts


# Unity 约定的资源目录名。当拼接变量声明在别的文件里（静态解析拿不到）时作兜底。
_UNITY_DIRS = ("Build", "TemplateData", "StreamingAssets")
_ASSET_TAIL = re.compile(
    r'''^[^"'\s]{1,200}?\.(?:js|json|css|png|jpg|jpeg|gif|svg|data|wasm|unityweb|br|gz|bin|mem)$''',
    re.I,
)


def resolve_js_concat(text: str) -> List[str]:
    """还原 JS 拼接出的相对路径：`buildUrl + "/7a2b...loader.js"` -> `Build/7a2b...loader.js`。

    若变量在该文本内没有声明（常见于声明在另一个 JS 文件里），
    则把该路径追加到 Unity 常见目录名下作为候选——多抓几个不存在的 URL 无害
    （会被 404 过滤），漏抓则导致游戏跑不起来。
    """
    consts = js_const_map(text)
    out: List[str] = []
    for name, tail in _JS_CONCAT.findall(text):
        if not tail or not _ASSET_TAIL.match(tail):
            continue
        head = consts.get(name)
        if head is not None:
            out.append(head.rstrip("/") + "/" + tail.lstrip("/"))
        else:
            # 未知变量：按 Unity 约定目录候选
            for d in _UNITY_DIRS:
                out.append(f"{d}/{tail.lstrip('/')}")
    return _dedup(out)


def extract_refs(text: str, base_url: str) -> List[str]:
    """从 HTML/CSS/JS 文本中提取引用的资源 URL（已转为绝对）。"""
    found: Set[str] = set()
    for pat in _PATTERNS:
        for raw in pat.findall(text):
            if raw.startswith("srcset"):
                continue
            for piece in _split_srcset(raw):
                piece = piece.strip()
                if piece:
                    found.add(piece)
    for raw in _UNITY_BUILD.findall(text):
        found.add(raw.strip())
    # JS 常量拼接（Unity Build 目录的关键路径来源）
    for raw in resolve_js_concat(text):
        found.add(raw)
    for raw in re.findall(r'''<param\s+name=["']movie["'][^>]*value=["']([^"']+)["']''', text, re.I):
        found.add(raw.strip())
    for raw in re.findall(r'''["']([^"']+\.swf(?:\?[^"']*)?)["']''', text, re.I):
        found.add(raw.strip())

    out: List[str] = []
    for item in found:
        item = html_lib.unescape(item.strip().strip("'\""))
        if not item or item.startswith(("data:", "javascript:", "mailto:", "#", "blob:", "about:")):
            continue
        # 丢弃误伤：以 / 开头会被引擎当作站点根路径，而 Unity 的 Build/x.js 从不这样写。
        # 这类条目来自 `[^"'\s]*\.ext` 贪心匹配，把 "../x.js" 吞成了 "/x.js"。
        if item.startswith("/") and "/Build/" not in item:
            continue
        try:
            abs_url = urljoin(base_url, item)
        except Exception:
            continue
        if abs_url.startswith(("http://", "https://")):
            out.append(abs_url)
    return _dedup(out)


def _split_srcset(raw: str) -> List[str]:
    """拆分 srcset。

    注意：data: URI 内部含逗号，不能简单 split(',')——
    否则 data:image/png;base64,AAAA 会被切成两段并把 base64 当路径。
    """
    if raw.strip().lower().startswith("data:"):
        return [raw.strip()]
    out = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        # 懒加载常写成 "url 1x, url2 2x"，取空格前的 URL 部分
        out.append(part.split(" ")[0])
    return out


def _dedup(items: List[str]) -> List[str]:
    seen: Set[str] = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ---------------------------------------------------------------- 下载器

class Downloader:
    """带线程安全的磁盘写入、去重、进度回调的资源下载器。"""

    def __init__(self, root: str, referer: str = "", cb: Optional[ProgressCB] = None):
        self.root = os.path.abspath(root)
        self.referer = referer
        self.cb = cb
        self.seen_local: Set[str] = set()
        self.lock = threading.Lock()
        self.ok_count = 0
        self.fail_count = 0
        self.total_bytes = 0
        self._failed: List[str] = []
        # 本地相对路径 -> 原始 URL。懒补漏靠它精确回源（反推会被 query/scheme 坑到）
        self.url_map: Dict[str, str] = {}
        self._load_url_map()

    # ---------- 映射持久化

    URLMAP_NAME = ".hgd_urlmap.json"

    def _map_path(self) -> str:
        return os.path.join(self.root, self.URLMAP_NAME)

    def _load_url_map(self) -> None:
        p = self._map_path()
        if not os.path.exists(p):
            return
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self.url_map.update(data)
        except Exception:
            pass

    def save_url_map(self) -> None:
        try:
            os.makedirs(self.root, exist_ok=True)
            with open(self._map_path(), "w", encoding="utf-8") as f:
                json.dump(self.url_map, f, ensure_ascii=False)
            if os.name == "nt":
                import ctypes
                ctypes.windll.kernel32.SetFileAttributesW(self._map_path(), 2)  # FILE_ATTRIBUTE_HIDDEN
        except Exception:
            pass

    # ---------- 路径

    def local_path_for(self, url: str) -> str:
        rel = url_to_local_rel(url)
        return os.path.join(self.root, rel)

    def exists(self, url: str) -> bool:
        return os.path.exists(self.local_path_for(url))

    # ---------- 单文件

    def fetch_one(self, url: str, referer: str = "",
                  allow_404_skip: bool = True) -> Optional[str]:
        """下载一个资源，返回本地绝对路径；失败返回 None。"""
        local = self.local_path_for(url)
        rel = os.path.relpath(local, self.root).replace("\\", "/")
        with self.lock:
            # 记录映射：即使本次跳过也要保证懒补漏能查到原始 URL
            self.url_map[rel] = url
            if local in self.seen_local:
                return local
            self.seen_local.add(local)
        # 已存在则跳过（断点续传式）
        if os.path.exists(local) and os.path.getsize(local) > 0:
            return local

        try:
            status, raw, headers = get_bytes(url, referer=referer or self.referer)
        except HttpError:
            with self.lock:
                self.fail_count += 1
                self._failed.append(url)
            self._emit(fetch_one=0)
            return None

        ctype = headers.get("Content-Type", "") or headers.get("content-type", "")
        max_size = 512 * 1024 * 1024
        if allow_404_skip and looks_like_404(raw, ctype):
            with self.lock:
                self.fail_count += 1
            return None
        if len(raw) > max_size:
            with self.lock:
                self.fail_count += 1
            return None

        # 无扩展名（或扩展名异常长）时按 content-type 补一个
        final = local
        head, ext = os.path.splitext(local)
        if (not ext or len(ext) > 7) and not local.lower().endswith((".br", ".gz")):
            final = head + guess_extension(url, ctype)

        os.makedirs(os.path.dirname(final), exist_ok=True)
        tmp = final + ".part"
        try:
            with open(tmp, "wb") as f:
                f.write(raw)
            os.replace(tmp, final)   # 原子落盘，避免半截文件被当作已存在
        except OSError:
            return None

        with self.lock:
            self.ok_count += 1
            self.total_bytes += len(raw)
        self._emit(current=url)
        return final

    def _emit(self, current: str = "", **kw) -> None:
        if not self.cb:
            return
        with self.lock:
            done = self.ok_count + self.fail_count
        self.cb(DownloadProgress(
            phase="mirror", done=done, bytes=self.total_bytes,
            current=current, ok=self.fail_count == 0,
        ))

    # ---------- 递归镜像

    def mirror(self, entry_url: str, entry_local: str, max_depth: int = 3,
               max_files: int = 2000, text_ext=(".html", ".htm", ".js", ".css", ".json")) -> Dict:
        """以 entry_url 为根递归镜像。

        只对文本类继续提取子引用，二进制（图片/音频/wasm）直接下载不递归。
        """
        pending: queue.Queue = queue.Queue()
        pending.put((entry_url, entry_local, 0))
        visited: Set[str] = set()
        file_count = 0

        def worker():
            nonlocal file_count
            while True:
                try:
                    url, local, depth = pending.get_nowait()
                except queue.Empty:
                    return
                if url in visited or depth > max_depth:
                    continue
                visited.add(url)
                if file_count >= max_files:
                    continue
                file_count += 1

                res = self.fetch_one(url)
                if not res or depth >= max_depth:
                    continue
                # 文本类继续解析
                low = res.lower()
                if low.endswith(text_ext):
                    try:
                        with open(res, "rb") as f:
                            blob = f.read()
                        text = blob.decode("utf-8", "ignore") or blob.decode("gb18030", "ignore")
                    except OSError:
                        continue
                    for sub in extract_refs(text, url):
                        if file_count >= max_files:
                            break
                        pending.put((sub, self.local_path_for(sub), depth + 1))

        # 用线程池驱动 queue，控制并发
        n = 8
        threads = [threading.Thread(target=worker, daemon=True) for _ in range(n)]
        for t in threads:
            t.start()
        # 等待全部处理完
        while any(t.is_alive() for t in threads):
            if pending.empty():
                # 让 worker 有机会退出
                time.sleep(0.05)
            else:
                time.sleep(0.02)
        for t in threads:
            t.join(timeout=5)

        self.save_url_map()
        return {
            "files": self.ok_count,
            "failed": self.fail_count,
            "bytes": self.total_bytes,
            "failed_urls": self._failed[:50],
        }
