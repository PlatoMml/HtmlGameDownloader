"""HTTP 客户端。

踩坑记录（针对参考仓库缺陷的修正）：
1. **绝不自动解压**：Unity WebGL 常用 .br/.gz 资源，且由页面 JS 自行解压。
   若 requests 帮我们解压了，文件内容变化但文件名仍带 .br，Unity loader 会二次解压失败。
   因此统一发送 Accept-Encoding: identity。
2. **检查状态码**：参考仓库从不检查 status_code，404 页面被当内容写盘。
3. **带指数退避重试**：参考仓库完全没有重试。
4. **Content-Type 感知的 404 判定**：只对 text/html 做 404 特征检测，避免误删二进制。
"""
from __future__ import annotations

import os
import random
import time
from typing import Dict, Optional, Tuple

import requests

from ..config import USER_AGENT, config


class HttpError(Exception):
    pass


def build_proxies() -> Optional[Dict[str, str]]:
    """代理优先级：显式配置 > 环境变量。"""
    p = (config.get("proxy") or "").strip()
    if p:
        if "://" not in p:
            p = "http://" + p
        return {"http": p, "https": p}
    if config.get("use_env_proxy"):
        env = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") \
            or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
        if env:
            return {"http": env, "https": env}
    return None


def default_headers(referer: str = "") -> Dict[str, str]:
    h = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        # 关键：禁止任何传输层压缩，保持字节原样
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
    }
    if referer:
        h["Referer"] = referer
    return h


def get_bytes(
    url: str,
    referer: str = "",
    timeout: Optional[int] = None,
    retries: Optional[int] = None,
    headers: Optional[Dict[str, str]] = None,
    session: Optional[requests.Session] = None,
) -> Tuple[int, bytes, Dict[str, str]]:
    """GET 返回 (status, content, headers)。非 2xx 抛 HttpError。"""
    timeout = timeout or int(config.get("timeout"))
    retries = retries if retries is not None else int(config.get("retries"))
    h = default_headers(referer)
    if headers:
        h.update(headers)

    last_err: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            req = session.get if session else requests.get
            r = req(url, headers=h, timeout=(10, timeout),
                    proxies=build_proxies(), stream=False)
            if r.status_code == 200:
                return 200, r.content, dict(r.headers)
            # 404/403 等不必重试，直接抛
            if 400 <= r.status_code < 500:
                raise HttpError(f"HTTP {r.status_code} for {url}")
            last_err = HttpError(f"HTTP {r.status_code} for {url}")
        except HttpError:
            raise
        except Exception as e:  # 网络类错误才重试
            last_err = e
        if attempt < retries:
            time.sleep(min(2 ** attempt, 5) + random.random() * 0.4)
    raise HttpError(f"请求失败 {url}: {last_err}")


def get_text(url: str, referer: str = "", **kw) -> str:
    """取文本，按 meta/header/常见中文编码智能解码。"""
    status, raw, headers = get_bytes(url, referer=referer, **kw)
    return decode_bytes(raw, headers)


def decode_bytes(raw: bytes, headers: Optional[Dict[str, str]] = None) -> str:
    """编码探测顺序：BOM > meta charset > Content-Type > utf-8 > gb18030。

    参考仓库只试 utf-8/gbk/gb2312 且顺序硬编码，这里改成 gb18030（gbk 超集）。
    """
    if raw[:3] == b"\xef\xbb\xbf":
        return raw.decode("utf-8-sig", "ignore")
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", "ignore")

    head = raw[:4096].decode("ascii", "ignore")
    charset = ""
    if headers:
        ct = headers.get("Content-Type", "") or headers.get("content-type", "")
        i = ct.lower().find("charset=")
        if i >= 0:
            charset = ct[i + 8:].strip().strip('"\'')
    if not charset:
        import re
        m = None
        for pat in (r'charset\s*=\s*["\']?\s*([\w\-]+)', r'encoding\s*=\s*["\']?\s*([\w\-]+)'):
            m = re.search(pat, head, re.I)
            if m:
                break
        if m:
            charset = m.group(1)

    for enc in ([charset] if charset else []) + ["utf-8", "gb18030", "latin-1"]:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "ignore")


def looks_like_404(raw: bytes, content_type: str = "") -> bool:
    """只对文本类做 404 判定，避免误判二进制。

    参考仓库的 bug：对 swf/图片也 decode 后搜 "404 Not Found"，误删正常文件。
    """
    ct = (content_type or "").lower()
    if ct and not (ct.startswith("text/") or "html" in ct or "xml" in ct or "json" in ct):
        return False
    if len(raw) > 512 * 1024:
        return False
    text = raw[:4096].decode("utf-8", "ignore").lower()
    for key in ("404 not found", "404 -", "not found", "页面不存在", "您访问的页面"):
        if key in text:
            return True
    return False
