"""网址 / 本地目录 游戏识别。

职责：
- 识别用户提供的网址 -> 游戏名 + 引擎类型 + 真实入口地址
- 扫描本地目录 -> 识别其中的多个游戏（需求 5）
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

from ..config import ENGINE_EXT, UNITY_MARKERS
from .http_client import HttpError, decode_bytes, get_bytes
from .mirror import safe_dirname
from ..models import IdentifyResult
from .site_plugins import clean_name, pick


# ---------------------------------------------------------------- 网址识别

def identify_url(url: str) -> IdentifyResult:
    """识别网址，自动推断游戏名与入口。"""
    url = (url or "").strip()
    if not url:
        return IdentifyResult(False, message="网址为空")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url.lstrip("/")

    parsed = urlparse(url)
    domain = parsed.netloc
    referer = f"{parsed.scheme}://{parsed.netloc}/"

    try:
        status, raw, headers = get_bytes(url, referer=referer)
    except HttpError as e:
        return IdentifyResult(False, url=url, message=f"无法访问：{e}")

    html = decode_bytes(raw, headers)
    plugins = pick(url)

    merged: Dict = {}
    for p in plugins:
        part = p.extract(html, url) or {}
        for k, v in part.items():
            if v and (k not in merged or not merged.get(k)):
                merged[k] = v

    entry = merged.get("entry_url") or ""
    name = merged.get("name") or clean_name(_name_from_url(url)) or "未命名游戏"

    # 入口是二级页面时，二次探测真实引擎
    # 入口是二级页面时，二次探测真实引擎与画布尺寸
    engine = merged.get("engine", "unknown")
    w = int(merged.get("width", 0) or 0)
    h = int(merged.get("height", 0) or 0)
    ratio = 0.0
    if entry and entry != url:
        engine, entry, w2, h2, ratio = _probe_entry(entry, referer, engine)
        w, h = w2 or w, h2 or h
    if not ratio:
        ratio = _declared_aspect(html)

    if not entry:
        # 页面本身就是游戏（Unity/HTML5 直接内嵌）
        if "createunityinstance" in html.lower() or ".unityweb" in html.lower():
            engine = "unity"
        entry = url

    # 详情页自身也可能带 canvas（单页内嵌型游戏）
    cw, ch = _canvas_size(html)
    if cw and ch:
        w, h = cw, ch
    if h and (h < 120 or w < 120):
        w = h = 0   # 明显是 UI 元素而非画布，丢弃

    # 页面声明的比例优先（有注入自适应脚本时，canvas 属性尺寸会失真）
    if ratio and not (0.3 < ratio < 3.5):
        ratio = 0.0
    if not ratio and w and h:
        ratio = w / h

    return IdentifyResult(
        ok=True, url=url, name=name, engine=engine, entry_url=entry,
        page_url=url, width=w, height=h,
        message="识别成功",
        raw={**merged, "ratio": ratio},
    )


def _probe_entry(entry: str, referer: str, current: str) -> tuple:
    """请求入口页，探测真实引擎、画布尺寸与页面声明的宽高比。"""
    try:
        status, raw, headers = get_bytes(entry, referer=referer)
    except HttpError:
        return current, entry, 0, 0, 0.0
    text = decode_bytes(raw, headers)
    low = text.lower()
    cw, ch = _canvas_size(text)
    ratio = _declared_aspect(text)
    if "createunityinstance" in low or ".unityweb" in low or "/build/" in low:
        return "unity", entry, cw, ch, ratio
    if ".swf" in low:
        return "flash", entry, cw, ch, ratio
    # Unity 标题特征
    if "unity webgl player" in low:
        return "unity", entry, cw, ch, ratio
    return ("html" if current == "unknown" else current), entry, cw, ch, ratio


def _canvas_size(html: str) -> tuple:
    """从 <canvas width= height=> 或 Unity 配置里取真实画布尺寸。"""
    m = re.search(r"<canvas[^>]*\bwidth=[\"']?(\d+)[^>]*\bheight=[\"']?(\d+)", html, re.I)
    if not m:
        m = re.search(r"<canvas[^>]*\bheight=[\"']?(\d+)[^>]*\bwidth=[\"']?(\d+)", html, re.I)
        if m:
            return int(m.group(2)), int(m.group(1))
    if m:
        return int(m.group(1)), int(m.group(2))
    return 0, 0


def _declared_aspect(html: str) -> float:
    """读取页面自身声明的目标宽高比。

    很多站点（如 4399）会给游戏壳页注入尺寸自适应脚本，形如：
        const targetAspect = 16 / 9;
    这是页面作者认定该游戏应呈现的比例，比 canvas 属性更权威
    （实测：Alto's Adventure 的 canvas 属性是 860x540=1.593，
     但注入脚本强制 16:9，此时应以 16:9 为准）。
    """
    m = re.search(r"targetAspect\s*=\s*([0-9.]+)\s*/\s*([0-9.]+)", html)
    if m:
        try:
            a, b = float(m.group(1)), float(m.group(2))
            if b and 0.3 < a / b < 3.5:
                return a / b
        except ValueError:
            pass
    m = re.search(r"targetAspect\s*=\s*([0-9.]+)\s*[,;]", html)
    if m:
        try:
            v = float(m.group(1))
            if 0.3 < v < 3.5:
                return v
        except ValueError:
            pass
    # 形如 aspectRatio: "16:9" / "aspect-ratio: 16 / 9"
    m = re.search(r'aspect[_-]?[Rr]atio["\']?\s*[:=]\s*["\']?\s*(\d+)\s*[:/]\s*(\d+)', html)
    if m:
        try:
            a, b = int(m.group(1)), int(m.group(2))
            if b and 0.3 < a / b < 3.5:
                return a / b
        except ValueError:
            pass
    return 0.0


def _name_from_url(url: str) -> str:
    """从 URL 兜底猜名字。"""
    p = urlparse(url).path
    stem = os.path.splitext(os.path.basename(p))[0]
    stem = re.sub(r"_\d+$", "", stem)
    return clean_name(stem.replace("-", " ").replace("_", " "))


# ---------------------------------------------------------------- 本地目录识别

_SCORE_RULES = {
    # 文件名关键词 -> (加分, 引擎)
    "index": (30, ""), "main": (20, ""), "play": (25, ""), "game": (25, ""),
    "start": (15, ""), "loader": (10, ""), "default": (10, ""),
}


def detect_engine_in_dir(path: str) -> str:
    """根据目录内容判定引擎。"""
    markers = {"unity": 0, "flash": 0, "html": 0}
    for root, _dirs, files in os.walk(path):
        depth = root[len(path):].count(os.sep)
        if depth > 3:
            continue
        for f in files:
            low = f.lower()
            if low.endswith((".unityweb", ".data", ".wasm")) or "streamingassets" in root.lower():
                markers["unity"] += 3
            elif low.endswith(".swf"):
                markers["flash"] += 3
            elif low.endswith((".html", ".htm")):
                markers["html"] += 1
            if low.endswith(".js") and "unity" in low:
                markers["unity"] += 2
        # Build 目录是 Unity 强特征
        if os.path.basename(root).lower() == "build":
            markers["unity"] += 5
    best = max(markers, key=markers.get)
    return best if markers[best] > 0 else "unknown"


def find_entry_file(path: str, max_depth: int = 3) -> Optional[str]:
    """在目录里找最可能的入口 HTML/SWF。

    评分综合考虑：目录深度（越浅越可能是入口）、文件名关键词、页面特征。
    """
    candidates: List[tuple] = []
    for root, _dirs, files in os.walk(path):
        depth = root[len(path):].count(os.sep)
        if depth > max_depth:
            continue
        for f in files:
            low = f.lower()
            if not low.endswith((".html", ".htm", ".swf")):
                continue
            score = 100 - depth * 12
            stem = os.path.splitext(low)[0]
            for kw, (add, _e) in _SCORE_RULES.items():
                if kw in stem:
                    score += add
            full = os.path.join(root, f)
            if low.endswith((".html", ".htm")):
                try:
                    with open(full, "rb") as fh:
                        head = fh.read(8192).decode("utf-8", "ignore").lower()
                    if "unity-container" in head or "createunityinstance" in head:
                        score += 60
                    if "<canvas" in head:
                        score += 10
                    if "swf" in head:
                        score += 5
                except OSError:
                    pass
            else:
                score += 20   # swf 直接可玩
            candidates.append((score, os.path.relpath(full, path)))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (-x[0], x[1]))
    return candidates[0][1]


def _entry_is_direct(d: str, entry: str) -> bool:
    """入口是否位于该目录的直接子层（而非深处的子游戏目录）。

    用于 scan_directory：避免把"包含多个游戏的父目录"也登记成一个游戏。
    """
    if not entry:
        return False
    return os.sep not in entry and "/" not in entry


def scan_directory(root_dir: str, max_games: int = 200) -> List[Dict]:
    """扫描目录，识别其中的多个游戏（需求 5）。

    判定策略：
    - 直接含入口文件的目录 => 一个游戏
    - 只含子目录的目录 => 逐个子目录判定
    - 单文件 swf/html => 一个游戏
    """
    root_dir = os.path.abspath(root_dir)
    if not os.path.isdir(root_dir):
        return []

    found: List[Dict] = []

    def consider_dir(d: str, require_direct_entry: bool = False):
        if len(found) >= max_games:
            return
        engine = detect_engine_in_dir(d)
        entry = find_entry_file(d)
        if not entry or engine == "unknown":
            return
        # 根目录只在"入口位于顶层"时才算一个游戏，
        # 否则会把装满游戏的父目录整个登记成一个游戏
        if require_direct_entry and not _entry_is_direct(d, entry):
            return
        found.append({
            "name": os.path.basename(d) or "未命名",
            "path": d,
            "entry": entry,
            "engine": engine,
            "size": dir_size(d),
        })

    # 1) 根目录自身是否就是游戏（要求入口在顶层）
    root_is_game = False
    before = len(found)
    consider_dir(root_dir, require_direct_entry=True)
    root_is_game = len(found) > before

    # 2) 单层子目录
    for name in sorted(os.listdir(root_dir)):
        sub = os.path.join(root_dir, name)
        if os.path.isdir(sub):
            consider_dir(sub)
        elif name.lower().endswith((".swf", ".html", ".htm")) and len(found) < max_games:
            ext = os.path.splitext(name)[1].lower()
            found.append({
                "name": os.path.splitext(name)[0],
                "path": sub,
                "entry": "",
                "engine": ENGINE_EXT.get(ext, "html"),
                "size": os.path.getsize(sub),
            })

    # 3) 去重：
    #    - 若父目录已命中，去掉其子目录结果（避免同一游戏登记两次）
    #    - 若某文件的入口已被祖先目录登记，去掉该文件结果
    dir_paths = {f["path"] for f in found if os.path.isdir(f["path"])}
    entry_abs = {os.path.normcase(os.path.join(f["path"], f["entry"]))
                 for f in found if f["entry"]}
    result = []
    for f in found:
        p = os.path.normcase(f["path"])
        if os.path.isdir(f["path"]):
            # 子目录：父目录已命中则丢弃
            if os.path.dirname(p) in {os.path.normcase(d) for d in dir_paths}:
                continue
        else:
            # 单文件：已被某个目录当作入口登记则丢弃
            if p in entry_abs:
                continue
        result.append(f)
    return result


def dir_size(path: str) -> int:
    total = 0
    if os.path.isfile(path):
        return os.path.getsize(path)
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total
