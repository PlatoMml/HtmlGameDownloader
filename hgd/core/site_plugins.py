"""站点插件规则。

设计：通用启发式识别为主，站点特例为插件。
每个插件声明自己能处理哪些域名，并提供 (可选) 入口抽取 / 名称抽取 / 页面改写。
站点改版只需改这里，不动通用引擎。

插件接口（全部可选）：
    match(url) -> bool
    extract(html, url) -> dict  可含 entry_url / name / width / height / engine
    rewrite(html, url) -> str   下载前对页面做改写（去广告、去 document.domain 限制等）
"""
from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional
from urllib.parse import urljoin


# ---------------------------------------------------------------- utils

def _find1(pattern: str, text: str, flags=0) -> str:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else ""


def clean_name(name: str) -> str:
    """游戏名清洗：去站点后缀、去空白、限长。

    站点标题格式很杂，实测样本：
        "数据之翼_数据之翼html5游戏在线玩_4399h5游戏-4399在线玩"  -> 数据之翼
        "救援棕色大象,救援棕色大象小游戏,4399小游戏 www.4399.com"  -> 救援棕色大象
        "滑雪冒险 - 7k7k小游戏"                                  -> 滑雪冒险
    """
    if not name:
        return ""
    name = name.strip()

    # 先按站点常用分隔符切分，取第一段（游戏名总在最前）
    for sep in (",", "，", "_", "|", "—", "-", "－", "·"):
        if sep in name:
            first = name.split(sep)[0].strip()
            if 1 < len(first) <= 40:
                name = first
                break

    # 去掉残留的域名 / 站点名
    name = re.sub(r"\bwww\.[\w\-\.]+", "", name, flags=re.I)
    name = re.sub(r"\b[\w\-]+\.(?:com|cn|net|org|cc)\b", "", name, flags=re.I)
    for junk in ("在线玩", "小游戏", "单机游戏", "网页游戏", "游戏大全", "h5游戏", "html5游戏"):
        name = name.replace(junk, "")
    name = re.sub(r"\s+", " ", name).strip(" -_·—,，|")

    # 全被清空时退回原始标题的前一段，避免得到空名
    if not name:
        name = re.split(r"[,，_|—\-]", name or "")[0].strip() or "未命名游戏"
    return name[:60]


def title_name(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    return clean_name(m.group(1)) if m else ""


def og_name(html: str) -> str:
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', html, re.I)
    if not m:
        m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']', html, re.I)
    return clean_name(m.group(1)) if m else ""


# ---------------------------------------------------------------- 4399

class Plugin4399:
    """4399：flash 老页 / h5 新页 / unity。

    老页里有 _strGamePath="/upload_swf/..." 或 "xxx.swf"，需拼 CDN 前缀；
    h5/unity 页里 _strGamePath 指向一个 index.htm（新页面），需二次请求。
    """

    CDN = "https://sda.4399.com/4399swf"
    DOMAINS = ("4399.com",)

    @classmethod
    def match(cls, url: str) -> bool:
        return any(d in url for d in cls.DOMAINS)

    @classmethod
    def extract(cls, html: str, url: str) -> Dict:
        out: Dict = {}
        path = _find1(r'_strGamePath\s*=\s*["\']([^"\']+)["\']', html)
        if path:
            if path.lower().endswith(".swf"):
                out["engine"] = "flash"
                if path.startswith("http"):
                    out["entry_url"] = path
                else:
                    out["entry_url"] = cls.CDN + (path if path.startswith("/") else "/" + path)
            else:
                # h5 / unity 新页面：路径是站内相对路径，需补前缀
                cand = path if path.startswith("http") else cls.CDN + (
                    path if path.startswith("/") else "/" + path)
                out["entry_url"] = cand
                out["engine"] = "unknown"   # 由通用引擎二次探测
        # 兜底：老式 embed
        if not out.get("entry_url"):
            swf = _find1(r'<embed[^>]+src=["\']([^"\']+\.swf[^"\']*)["\']', html, re.I)
            if not swf:
                swf = _find1(r'src=["\']([^"\']+\.swf[^"\']*)["\']', html, re.I)
            if swf:
                out["engine"] = "flash"
                out["entry_url"] = swf if swf.startswith("http") else cls.CDN + "/" + swf.lstrip("/")
        # 尺寸
        w = _find1(r'width\s*[:=]\s*["\']?(\d{2,5})', html)
        h = _find1(r'height\s*[:=]\s*["\']?(\d{2,5})', html)
        if w.isdigit():
            out["width"] = int(w)
        if h.isdigit():
            out["height"] = int(h)
        # 名称：优先 og/title
        nm = og_name(html) or title_name(html)
        if nm:
            out["name"] = nm
        return out

    @classmethod
    def rewrite(cls, html: str, url: str) -> str:
        """离线化必要改写。

        - 去掉 document.domain 限制（本地 file/127.0.0.1 下该语句会抛错，阻断后续 JS）
        - 移除跨域广告脚本，避免离线时卡加载
        """
        html = re.sub(r"document\.domain\s*=\s*['\"][^'\"]*['\"]\s*;?", "", html)
        html = re.sub(r'<script[^>]+src=["\']https?://[^"\']*(?:ads|ad\.|advert|google)[^"\']*["\'][^>]*>\s*</script>',
                      "", html, flags=re.I)
        return html


# ---------------------------------------------------------------- 7k7k

class Plugin7k7k:
    DOMAINS = ("7k7k.com",)

    @classmethod
    def match(cls, url: str) -> bool:
        return any(d in url for d in cls.DOMAINS)

    @classmethod
    def extract(cls, html: str, url: str) -> Dict:
        out: Dict = {}
        p = _find1(r'gamePath\s*:\s*["\']([^"\']+)["\']', html)
        if not p:
            p = _find1(r"_src_\s*=\s*['\"]([^'\"]+\.swf[^'\"]*)['\"]", html)
        if p:
            out["engine"] = "flash"
            # 7k7k 有 flash/ 与 swf/ 混用的历史问题
            p = p.replace("/flash/", "/swf/")
            out["entry_url"] = p if p.startswith("http") else urljoin(url, p)
        nm = og_name(html) or title_name(html)
        if nm:
            out["name"] = nm
        return out

    @classmethod
    def rewrite(cls, html: str, url: str) -> str:
        return re.sub(r"document\.domain\s*=\s*['\"][^'\"]*['\"]\s*;?", "", html)


# ---------------------------------------------------------------- 通用 Unity / Flash

class PluginGeneric:
    """兜底插件：任何站点都尝试的通用启发式。"""

    @classmethod
    def match(cls, url: str) -> bool:
        return True

    @classmethod
    def extract(cls, html: str, url: str) -> Dict:
        out: Dict = {}
        low = html.lower()

        # Unity WebGL 特征
        if ("createunityinstance" in low or ".unityweb" in low
                or re.search(r'["\']Build/[^"\']+\.loader\.js', html)):
            out["engine"] = "unity"
            m = re.search(r'dataUrl\s*:\s*[^"\']*["\']([^"\']+\.data(?:\.br|\.gz)?)["\']', html)
            if m:
                out["raw_build"] = m.group(1)
        elif ".swf" in low:
            out["engine"] = "flash"

        # 名称
        nm = og_name(html) or title_name(html)
        if not nm:
            m = re.search(r'productName\s*:\s*["\']([^"\']+)["\']', html)
            if m:
                nm = clean_name(m.group(1))
        if nm:
            out["name"] = nm

        # Unity canvas 尺寸
        m = re.search(r'<canvas[^>]+width=["\']?(\d+)[^>]*height=["\']?(\d+)', html, re.I)
        if m:
            out["width"] = int(m.group(1))
            out["height"] = int(m.group(2))
        return out

    @classmethod
    def rewrite(cls, html: str, url: str) -> str:
        # 通用：去掉 document.domain（本地服务下会因跨域限制报错）
        return re.sub(r"document\.domain\s*=\s*['\"][^'\"]*['\"]\s*;?", "", html)


PLUGINS = [Plugin4399, Plugin7k7k, PluginGeneric]


def pick(url: str) -> List:
    """返回适用于该 URL 的插件（站点在前，通用兜底在后）。"""
    matched = [p for p in PLUGINS if p is not PluginGeneric and p.match(url)]
    return matched + [PluginGeneric]
