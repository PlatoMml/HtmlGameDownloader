"""广告与统计脚本清理。

## 为什么需要

下载网页游戏时，页面里往往夹带着站点自己的广告与统计代码：

    - Google AdSense / DoubleClick（`adsbygoogle`、`googlesyndication`）
    - 百度统计 / CNZZ / 51.la 等中文统计
    - 站点自建的广告位脚本、弹窗、悬浮层
    - 4399 的 `h.api.4399.com` 广告接口

离线运行时这些请求要么失败（拖慢加载、控制台报错），
要么在本地服务下反复重试，影响观感。打包分发给别人时更不合适。

## 清理策略（分级）

1. **移除整个 <script> 标签**：src 或内容命中广告/统计域名时整体删除
2. **移除广告容器元素**：常见广告位 div / iframe（按 id、class 特征）
3. **中和内联广告初始化**：`adsbygoogle.push(...)`、`adConfig`、`adBreak`
   等调用替换为空操作，避免 `adsbygoogle is not defined` 报错
4. **清理统计埋点**：`hm.baidu.com`、`cnzz`、`google-analytics` 等

## 安全边界

只清理**明确的广告/统计特征**，不动游戏自身逻辑：
    - 不删除含 `ad` 的普通单词（如 `load`、`shadow`、`header`）
    - 域名匹配要求完整域名或带点号前缀
    - 只改文本文件（HTML/JS/CSS），二进制一律不动
"""
from __future__ import annotations

import os
import re
from typing import List, Tuple

# ---------------------------------------------------------------- 特征库

# 广告/统计域名（完整域名或带点前缀，避免误伤 load/adult 之类）
_AD_DOMAINS = (
    "googlesyndication.com",
    "googleadservices.com",
    "doubleclick.net",
    "google-analytics.com",
    "googletagmanager.com",
    "googletagservices.com",
    "adservice.google.com",
    "pagead2.googlesyndication.com",
    "adsbygoogle",
    "hm.baidu.com",
    "cnzz.com",
    "51.la",
    "51yes.com",
    "vamaker.com",
    "tanx.com",
    "alimama.com",
    "baidustatic.com/hm",
    "h.api.4399.com",
    "api.4399.com/h5mini",
    "union.4399.com",
    "ads.4399.com",
)

# 广告位元素特征（用于删 <div id=...> / <iframe>）
_AD_ELEMENT_HINTS = (
    "adsbygoogle", "google_ads", "google-ads", "ad_slot", "ad-slot",
    "advert", "ad_banner", "ad-banner", "adsense", "ad_container",
    "ad-container", "gg_ad", "gg-ad", "bd_ad", "union_ad", "ad_float",
    "ad-popup", "ad_popup", "popup-ad",
)

# 内联广告初始化调用
_AD_CALL_PATTERNS = (
    # (adsbygoogle = window.adsbygoogle || []).push({...})
    re.compile(r"\(\s*adsbygoogle\s*=\s*window\.adsbygoogle\s*\|\|\s*\[\]\s*\)"
               r"\s*\.\s*push\s*\([^;]*\)\s*;?", re.S),
    # adsbygoogle.push({...})  /  adsbygoogle.push(o)
    re.compile(r"\badsbygoogle\s*\.\s*push\s*\([^;]*\)\s*;?", re.S),
    # var adBreak = (adConfig = function (o) { ... });
    re.compile(r"\bvar\s+adBreak\s*=\s*\(?\s*adConfig\s*=\s*function\s*\([^)]*\)\s*\{.*?\}\s*\)?\s*;?", re.S),
    # adBreak({...}) / adConfig({...}) 调用
    re.compile(r"\b(?:adBreak|adConfig)\s*\([^;]*\)\s*;?", re.S),
)


# ---------------------------------------------------------------- 工具

def _domain_matches(text: str) -> bool:
    low = text.lower()
    return any(d in low for d in _AD_DOMAINS)


_SCRIPT_TAG = re.compile(r"<script\b[^>]*>.*?</script>", re.S | re.I)
_SCRIPT_SELFCLOSE = re.compile(r"<script\b[^>]*/>", re.I)


def _script_is_ad(tag: str) -> bool:
    """判断一个 <script> 标签是否属于广告/统计。"""
    return _domain_matches(tag)


def _strip_ad_scripts(html: str) -> Tuple[str, int]:
    """移除广告/统计 <script> 标签。"""
    removed = 0

    def repl(m):
        nonlocal removed
        tag = m.group(0)
        if _script_is_ad(tag):
            removed += 1
            return ""
        return tag

    html = _SCRIPT_TAG.sub(repl, html)
    html = _SCRIPT_SELFCLOSE.sub(lambda m: "" if _script_is_ad(m.group(0)) else m.group(0), html)
    return html, removed


# 带 id/class 的容器元素（保守：只匹配明确含广告关键词的）
_CONTAINER = re.compile(
    r"""<(div|iframe|ins|section|aside)\b[^>]*?
        (?:id|class)\s*=\s*["'][^"']*?
        (?:%s)
        [^"']*?["'][^>]*?>(?:.*?</\1>)?
    """ % "|".join(re.escape(h) for h in _AD_ELEMENT_HINTS),
    re.S | re.I | re.X,
)


def _strip_ad_containers(html: str) -> Tuple[str, int]:
    """移除明确的广告位容器元素。"""
    count = 0

    def repl(m):
        nonlocal count
        count += 1
        return ""

    return _CONTAINER.sub(repl, html), count


def _neutralize_ad_calls(html: str, had_adsbygoogle: bool = False) -> Tuple[str, int]:
    """把内联广告初始化调用中和成空操作。

    直接删掉会让后续代码引用未定义变量而抛 ReferenceError，
    因此清理后统一下发一份最小 stub：
        window.adsbygoogle = window.adsbygoogle || [];
    游戏或残留代码再引用它时不会崩。

    had_adsbygoogle: 清理前原文是否用到过 adsbygoogle。
    整个 <script> 标签可能已被删掉，所以不能只看当前文本。
    """
    count = 0
    for pat in _AD_CALL_PATTERNS:
        def repl(m):
            nonlocal count
            count += 1
            return "void 0;"
        html = pat.sub(repl, html)

    # 原文出现过 adsbygoogle -> 保证它仍被定义（stub 形式）
    if (had_adsbygoogle or "adsbygoogle" in html) and "window.adsbygoogle" not in html:
        guard = "<script>window.adsbygoogle=window.adsbygoogle||[];</script>"
        if re.search(r"<head[^>]*>", html, re.I):
            html = re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + guard,
                          html, count=1, flags=re.I)
        else:
            html = guard + html
    return html, count


def _strip_tracking_refs(html: str) -> Tuple[str, int]:
    """清理统计埋点：图片/链接/内联形式的统计 URL。"""
    count = 0
    # 形如 <img src="//hm.baidu.com/xxx"> 这类透明统计图
    pat = re.compile(
        r"""<(?:img|iframe|script|link)\b[^>]*?
            (?:src|href)\s*=\s*["'][^"']*(?:%s)[^"']*["'][^>]*>
        """ % "|".join(re.escape(d) for d in _AD_DOMAINS),
        re.S | re.I | re.X,
    )

    def repl(m):
        nonlocal count
        count += 1
        return ""

    return pat.sub(repl, html), count


# ---------------------------------------------------------------- 主入口

TEXT_EXT = (".html", ".htm", ".xhtml", ".js", ".css")

# 只对 HTML 类文件做改写。
# 原因：JS 是游戏逻辑与引擎代码，正则改写风险远大于收益。
# 实测案例：Unity 的 framework.js 含 `adsbygoogle_present: !!window.adsbygoogle`
# 这类**只读遥测**，既不加载广告也不发请求，却会被误判成广告代码。
# 广告注入几乎总在 HTML 页面层（script 标签、广告位 div），
# 所以只清 HTML 就能覆盖实际需要，且不会碰坏引擎文件。
REWRITE_EXT = (".html", ".htm", ".xhtml")


def clean_html(html: str) -> Tuple[str, dict]:
    """清理 HTML 中的广告与统计代码。返回 (新内容, 统计信息)。

    统计口径：`calls` 计的是**被消除的广告调用总数**，
    无论它是随整个 script 标签一起删掉、还是单独被中和。
    这样用户看到的"清理了多少处广告"与手段无关，更好理解。
    """
    stats = {"scripts": 0, "containers": 0, "calls": 0, "tracking": 0}

    # 清理前先数一遍内联广告调用，作为 calls 的基线
    baseline_calls = 0
    for pat in _AD_CALL_PATTERNS:
        baseline_calls += len(pat.findall(html))

    had_adsbygoogle = "adsbygoogle" in html

    html, n = _strip_ad_scripts(html);      stats["scripts"] = n
    html, n = _strip_ad_containers(html);   stats["containers"] = n
    html, n = _neutralize_ad_calls(html, had_adsbygoogle)
    # 剩下的（未被 script 删除带走的）调用数
    stats["calls"] = max(baseline_calls, n)
    html, n = _strip_tracking_refs(html);   stats["tracking"] = n

    return html, stats


def clean_text_file(path: str) -> dict:
    """清理单个 HTML 文件里的广告与统计代码。原地写回。

    只处理 HTML 类文件 —— JS/CSS 属于游戏逻辑与样式，正则改写风险高。
    详见 REWRITE_EXT 的说明。
    """
    ext = os.path.splitext(path)[1].lower()
    if ext not in REWRITE_EXT:
        return {}

    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return {}

    # 编码：优先 utf-8，退回 gb18030
    enc = "utf-8"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("gb18030")
            enc = "gb18030"
        except UnicodeDecodeError:
            return {}

    low = text.lower()
    if not any(d in low for d in _AD_DOMAINS) and not any(
            h in low for h in _AD_ELEMENT_HINTS):
        return {}

    new_text, stats = clean_html(text)
    if new_text == text:
        return {}

    try:
        tmp = path + ".hgd_tmp"
        with open(tmp, "w", encoding=enc, newline="") as f:
            f.write(new_text)
        os.replace(tmp, path)
    except OSError:
        return {}

    stats["file"] = os.path.basename(path)
    return stats


def clean_game_dir(root: str, max_files: int = 400) -> dict:
    """清理整个游戏目录里的广告与统计代码。

    只处理 HTML 类文件；JS/CSS/二进制一律不动。
    """
    root = os.path.abspath(root)
    total = {"files": 0, "scripts": 0, "containers": 0, "calls": 0, "tracking": 0,
             "scanned": 0}

    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("_runtime", "_cache")]
        for f in files:
            if total["scanned"] >= max_files:
                break
            if os.path.splitext(f)[1].lower() not in REWRITE_EXT:
                continue
            total["scanned"] += 1
            st = clean_text_file(os.path.join(dirpath, f))
            if st:
                total["files"] += 1
                for k in ("scripts", "containers", "calls", "tracking"):
                    total[k] += st.get(k, 0)

    return total


def scan_ad_residue(root: str) -> List[dict]:
    """扫描游戏目录，列出仍残留广告/统计特征的文件（不修改）。

    用于打包前的提示与人工确认。

    只扫 HTML 类文件 —— 与清理口径一致。JS 里出现的广告域名多为
    只读遥测或变量名（实测：Unity framework.js 的 adsbygoogle_present
    只是判断该变量是否存在），不构成广告，报出来只会误导用户。
    """
    root = os.path.abspath(root)
    result = []

    # stub 形态（无副作用）不应报为残留
    stub_pat = re.compile(
        r"window\.adsbygoogle\s*=\s*window\.adsbygoogle\s*\|\|\s*\[\s*\]\s*;?")

    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("_cache",)]
        for f in files:
            p = os.path.join(dirpath, f)
            if os.path.splitext(f)[1].lower() not in REWRITE_EXT:
                continue
            try:
                with open(p, "rb") as fh:
                    raw = fh.read(2 * 1024 * 1024)
            except OSError:
                continue
            text = raw.decode("utf-8", "ignore")

            # 先摘掉有意保留的 stub，再判断是否还有真实广告特征
            probe = stub_pat.sub("", text).lower()

            hits = [d for d in _AD_DOMAINS if d in probe]
            hints = [h for h in _AD_ELEMENT_HINTS if h in probe]

            if hits or hints:
                result.append({
                    "file": os.path.relpath(p, root),
                    "domains": hits[:5],
                    "elements": hints[:5],
                })
    return result
