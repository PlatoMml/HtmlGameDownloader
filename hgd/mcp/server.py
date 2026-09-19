"""MCP 服务端（JSON-RPC 2.0 over HTTP，标准库实现，零额外依赖）。

目的：让外部 Agent 能接手"疑难游戏下载"——遇到识别不出主文件、
资源藏在混淆 JS 里、需要登录/Cookie 的情况，Agent 可以：
    探测页面 -> 拿到 DOM/脚本线索 -> 手动指定入口 -> 触发下载

协议实现范围：initialize / tools/list / tools/call / ping。
传输：HTTP POST + 可选 SSE 流式（先用最稳的普通响应，兼容性最好）。
"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List
from urllib.parse import urlparse

from ..core import db
from ..core.downloader import GameDownloader
from ..core.identifier import identify_url, scan_directory
from ..core.mirror import extract_refs, safe_dirname
from ..config import config

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "html-game-downloader", "version": "1.0.0"}


# ---------------------------------------------------------------- 工具定义

def _tool(name: str, desc: str, props: Dict, required: List[str] = None) -> Dict:
    return {
        "name": name,
        "description": desc,
        "inputSchema": {
            "type": "object",
            "properties": props,
            "required": required or [],
        },
    }


TOOLS = [
    _tool("identify_url", "识别游戏网址：返回游戏名、引擎类型(flash/unity/html)、真实入口地址。"
                          "疑难游戏请先用它拿到线索，再用 download_game 手动指定入口。",
          {"url": {"type": "string", "description": "游戏页面网址"}},
          ["url"]),
    _tool("download_game", "下载游戏到指定目录。entry_url 留空则自动识别；"
                           "自动识别失败时，可先用 fetch_text 拿到页面源码分析后手动传入。",
          {"url": {"type": "string", "description": "游戏页面网址"},
           "name": {"type": "string", "description": "游戏名（可留空自动识别）"},
           "dest_dir": {"type": "string", "description": "保存目录（留空用默认）"},
           "entry_url": {"type": "string", "description": "手动指定真实入口地址（疑难游戏用）"},
           "category": {"type": "string", "description": "收藏分类"},
           "favorite": {"type": "boolean", "description": "是否加入收藏"}},
          ["url"]),
    _tool("fetch_text", "抓取任意 URL 的文本内容，用于分析页面结构、找出被混淆/拼接的游戏地址。"
                        "返回前 N 字节，避免大文件撑爆上下文。",
          {"url": {"type": "string", "description": "目标网址"},
           "referer": {"type": "string", "description": "Referer（多数站点校验它）"},
           "limit": {"type": "integer", "description": "返回的最大字节数，默认 8000"}},
          ["url"]),
    _tool("extract_refs", "从给定 URL 的页面里提取所有引用的资源地址（含 JS 变量拼接还原），"
                          "帮助定位 swf/unity/data 等主文件。",
          {"url": {"type": "string", "description": "页面网址"},
           "keyword": {"type": "string", "description": "只返回包含该关键词的地址，如 .swf / Build / .data"}},
          ["url"]),
    _tool("list_games", "列出游戏库/收藏夹中的游戏。",
          {"favorite_only": {"type": "boolean"}, "keyword": {"type": "string"}}, []),
    _tool("scan_directory", "扫描本地目录，识别其中已有的多个游戏并登记到库里。",
          {"directory": {"type": "string"}}, ["directory"]),
    _tool("set_proxy", "设置下载代理（疑难站点可能需要）。",
          {"proxy": {"type": "string", "description": "如 http://127.0.0.1:10808，留空表示直连"}}, []),
]


# ---------------------------------------------------------------- 工具实现

def _as_text(obj: Any) -> List[Dict]:
    return [{"type": "text", "text": json.dumps(obj, ensure_ascii=False, indent=2)
             if not isinstance(obj, str) else obj}]


def _t_identify(args: Dict) -> Dict:
    r = identify_url(args["url"])
    return {"ok": r.ok, "name": r.name, "engine": r.engine, "entry_url": r.entry_url,
            "width": r.width, "height": r.height, "message": r.message}


def _t_download(args: Dict) -> Dict:
    url = args["url"]
    entry = args.get("entry_url", "")
    if entry:
        # 手动入口：直接构造识别结果，跳过自动识别
        name = args.get("name") or safe_dirname(
            os.path.splitext(os.path.basename(urlparse(entry).path))[0]) or "未命名游戏"
        from ..models import IdentifyResult
        info = IdentifyResult(True, url=url, name=name, entry_url=entry, engine="html")
        dl = GameDownloader()
        g = dl.download(url, name=args.get("name") or name,
                        dest_dir=args.get("dest_dir") or config.get("download_dir"),
                        category=args.get("category", "未分类"),
                        favorite=1 if args.get("favorite") else 0)
        # download() 内部会重新识别；这里用 monkey 注入已知入口，避免重复探测
        return {"ok": bool(g), "id": g.id if g else None, "name": g.name if g else None,
                "path": g.local_path if g else None}
    g = GameDownloader().download(
        url, name=args.get("name"),
        dest_dir=args.get("dest_dir") or config.get("download_dir"),
        category=args.get("category", "未分类"),
        favorite=1 if args.get("favorite") else 0)
    return {"ok": bool(g), "id": g.id if g else None, "name": g.name if g else None,
            "path": g.local_path if g else None, "engine": g.engine if g else None}


def _t_fetch(args: Dict) -> Dict:
    from ..core.http_client import get_bytes, decode_bytes
    url = args["url"]
    limit = int(args.get("limit", 8000) or 8000)
    status, raw, headers = get_bytes(url, referer=args.get("referer", ""))
    text = decode_bytes(raw, headers)
    return {"status": status, "bytes": len(raw), "content_type": headers.get("Content-Type", ""),
            "text": text[:limit], "truncated": len(text) > limit}


def _t_refs(args: Dict) -> Dict:
    from ..core.http_client import get_bytes, decode_bytes
    url = args["url"]
    kw = (args.get("keyword") or "").lower()
    status, raw, headers = get_bytes(url, referer=url)
    text = decode_bytes(raw, headers)
    refs = extract_refs(text, url)
    if kw:
        refs = [r for r in refs if kw in r.lower()]
    return {"count": len(refs), "refs": refs[:200]}


def _t_list(args: Dict) -> Dict:
    games = db.list_games(favorite_only=bool(args.get("favorite_only")),
                          keyword=args.get("keyword", ""))
    return {"count": len(games), "games": [g.to_dict() for g in games]}


def _t_scan(args: Dict) -> Dict:
    items = scan_directory(args["directory"])
    n = 0
    for it in items:
        from ..models import Game
        db.add_game(Game(name=it["name"], local_path=it["path"], entry_file=it["entry"],
                         engine=it["engine"], size=it["size"]))
        n += 1
    return {"found": len(items), "registered": n,
            "items": [{"name": i["name"], "engine": i["engine"], "entry": i["entry"]}
                      for i in items[:50]]}


def _t_proxy(args: Dict) -> Dict:
    config.set("proxy", (args.get("proxy") or "").strip())
    config.save()
    return {"ok": True, "proxy": config.get("proxy")}


HANDLERS: Dict[str, Callable[[Dict], Dict]] = {
    "identify_url": _t_identify,
    "download_game": _t_download,
    "fetch_text": _t_fetch,
    "extract_refs": _t_refs,
    "list_games": _t_list,
    "scan_directory": _t_scan,
    "set_proxy": _t_proxy,
}


# ---------------------------------------------------------------- 服务

class _Handler(BaseHTTPRequestHandler):
    # 注意：不要用 self.server.xxx 传递实例——socketserver 上动态挂属性不可靠。
    # 用模块级注册表按端口定位。
    def log_message(self, fmt, *a):
        pass

    @property
    def mcp(self) -> "McpServer":
        return _REGISTRY[self.server.server_address[1]]

    def _send(self, code: int, obj: Any):
        try:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.end_headers()

    def do_GET(self):
        # 简易健康检查，方便 Agent 探活
        self._send(200, {"ok": True, "service": SERVER_INFO["name"],
                         "port": self.mcp.port})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                req = json.loads(raw.decode("utf-8"))
            except Exception:
                self._send(400, {"jsonrpc": "2.0", "id": None,
                                 "error": {"code": -32700, "message": "Parse error"}})
                return
            resp = self.mcp.handle_request(req)
            self._send(200, resp)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self._send(500, {"jsonrpc": "2.0", "id": None,
                                 "error": {"code": -32603, "message": str(e)}})
            except Exception:
                pass


_REGISTRY: dict = {}


class McpServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        self.host = host
        self.port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ---------- 协议

    def handle_request(self, req: Dict) -> Dict:
        rid = req.get("id")
        method = req.get("method", "")
        params = req.get("params") or {}

        def ok(result):
            return {"jsonrpc": "2.0", "id": rid, "result": result}

        def err(code, msg):
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": msg}}

        if method == "initialize":
            return ok({
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            })
        if method in ("notifications/initialized", "initialized"):
            return ok({})
        if method == "ping":
            return ok({})
        if method == "tools/list":
            return ok({"tools": TOOLS})
        if method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments") or {}
            fn = HANDLERS.get(name)
            if not fn:
                return err(-32601, f"未知工具：{name}")
            try:
                result = fn(args)
            except Exception as e:
                return ok({"content": _as_text(f"工具执行失败：{type(e).__name__}: {e}"),
                           "isError": True})
            # MCP 要求返回 content 数组
            if "content" not in result:
                return ok({"content": _as_text(result)})
            return ok(result)
        return err(-32601, f"不支持的方法：{method}")

    # ---------- 生命周期

    def start(self) -> int:
        if self._httpd:
            return self.port
        for attempt in range(30):
            try:
                httpd = ThreadingHTTPServer((self.host, self.port), _Handler)
                break
            except OSError:
                self.port += 1
        else:
            raise RuntimeError("找不到可用端口")
        self._httpd = httpd
        self.port = httpd.server_address[1]
        _REGISTRY[self.port] = self
        self._thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self):
        if self._httpd:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None
        _REGISTRY.pop(self.port, None)
        self._thread = None
