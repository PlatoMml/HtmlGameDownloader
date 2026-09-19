"""本地镜像 HTTP 服务 + 运行时懒补漏。

这是从参考仓库继承的最有价值设计：漏掉的资源由浏览器运行时代为"点单"。
本实现把它做成可选的兜底层（默认开启），而不是唯一依赖。

相比参考仓库的修正：
- 钩子由本类持有，close() 后彻底解绑（原来挂全局，关闭后仍写文件）
- URL 解码口径统一（unquote 后一致），避免含 %20/中文的资源每轮重复下载
- 只在未命中时回源，并做极简超时，避免卡死浏览器请求
"""
from __future__ import annotations

import os
import socket
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import unquote, urlparse

from .http_client import HttpError, get_bytes, looks_like_404
from .mirror import url_to_local_rel, Downloader


class _GameHandler(SimpleHTTPRequestHandler):
    """静态服务 + 未命中回源。

    注意：不能用 self.server.xxx 传递自定义属性——BaseHTTPRequestHandler
    的 .server 是 socketserver 实例，动态挂属性在不同 Python 版本上表现不一致。
    改用模块级注册表按端口查找，稳定可靠。
    """

    def log_message(self, fmt, *args):  # 静音，避免刷屏
        pass

    @property
    def mirror(self) -> "MirrorServer":
        """按端口查找所属服务器。

        关闭瞬间可能有在途请求，此时注册表项已删除 —— 返回一个"惰性空服务器"
        而不是抛 KeyError，避免控制台刷栈。
        """
        ms = _REGISTRY.get(self.server.server_address[1])
        return ms if ms is not None else _DEAD

    def translate_path(self, path: str) -> str:
        """统一在此处 unquote，保证与 Downloader 落盘口径一致。"""
        p = unquote(urlparse(path).path)
        if p.startswith("/"):
            p = p[1:]
        return os.path.join(self.mirror.root, p)

    def do_GET(self):
        local = self.translate_path(self.path)
        if not os.path.exists(local) or os.path.isdir(local):
            self._lazy_fetch(local)
        SimpleHTTPRequestHandler.do_GET(self)

    def do_HEAD(self):
        local = self.translate_path(self.path)
        if not os.path.exists(local):
            self._lazy_fetch(local)
        SimpleHTTPRequestHandler.do_HEAD(self)

    def _lazy_fetch(self, local: str) -> None:
        """静态解析漏掉的资源 -> 即时回源抓取。"""
        ms = self.mirror
        if not ms.lazy_enabled or ms.downloader is None:
            return
        url = ms.reverse_map(local)
        if not url:
            return
        key = os.path.normcase(os.path.abspath(local))
        with ms.lock:
            if key in ms.inflight or key in ms.tried:
                return
            ms.inflight.add(key)
        try:
            ms.downloader.fetch_one(url, referer=ms.referer)
        except Exception:
            pass
        finally:
            with ms.lock:
                ms.inflight.discard(key)
                ms.tried.add(key)
                ms.lazy_count += 1


_REGISTRY: dict = {}


class _DeadServer:
    """服务已停止时的占位对象，让在途请求安全地失败而不是崩溃。"""
    root = ""
    base_url = ""
    scheme = "https"
    referer = ""
    lazy_enabled = False
    port = 0
    lazy_count = 0

    def __init__(self):
        import threading
        self.lock = threading.Lock()
        self.inflight: set = set()
        self.tried: set = set()
        self.downloader = None

    def reverse_map(self, _p: str) -> str:
        return ""


_DEAD = _DeadServer()


class MirrorServer:
    """管理本地镜像服务生命周期。"""

    def __init__(self, root: str, base_url: str = "", referer: str = "",
                 lazy: bool = True, downloader: Optional[Downloader] = None):
        self.root = os.path.abspath(root)
        self.base_url = base_url.rstrip("/")
        self.scheme = urlparse(base_url).scheme or "https"
        self.referer = referer or self.base_url
        self.lazy_enabled = lazy
        self.downloader = downloader or Downloader(self.root, self.referer)
        self.lock = threading.Lock()
        self.inflight: set = set()
        self.tried: set = set()
        self.lazy_count = 0
        self.port = 0
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # ---------- 生命周期

    def start(self, preferred: int = 0) -> int:
        if self._httpd:
            return self.port
        port = preferred or _free_port()
        for attempt in range(50):
            try:
                httpd = ThreadingHTTPServer(("127.0.0.1", port), _GameHandler)
                break
            except OSError:
                port = _free_port()
        else:
            raise RuntimeError("找不到可用端口")
        self._httpd = httpd
        self.port = httpd.server_address[1]
        _REGISTRY[self.port] = self
        self._thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        if self._httpd:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None
        _REGISTRY.pop(self.port, None)
        self._thread = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *a):
        self.stop()

    # ---------- 映射

    def url_for(self, rel_local: str) -> str:
        """本地相对路径 -> http://127.0.0.1:port/..."""
        rel = rel_local.replace("\\", "/").lstrip("/")
        from urllib.parse import quote
        return f"http://127.0.0.1:{self.port}/" + quote(rel)

    def reverse_map(self, local_path: str) -> str:
        """本地路径 -> 原始网络 URL（懒补漏用）。

        优先查下载时记录的精确映射表（Downloader.url_map），
        它保留 query 与原始 scheme，不会有反推误差。
        查不到时退回结构化推断（第一段=netloc），仅作兜底。
        """
        rel = os.path.relpath(os.path.abspath(local_path), self.root).replace("\\", "/")
        if rel.startswith(".."):
            return ""
        # 1) 精确映射
        mapped = self.downloader.url_map.get(rel)
        if mapped:
            return mapped
        # 2) 兜底：结构化推断
        if "/" not in rel:
            return ""
        host, _, rest = rel.partition("/")
        if not host or not rest:
            return ""
        netloc = host.replace("_", ":") if "_" in host and ":" not in host else host
        from urllib.parse import quote
        return f"{self.scheme}://{netloc}/{quote(rest)}"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
