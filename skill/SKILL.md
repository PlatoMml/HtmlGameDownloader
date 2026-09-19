# 网页游戏下载技能（SKILL）

供外部 Agent / 模型在下载网页游戏碰壁时使用的排查与解决手册。

工具自身通过 MCP 暴露能力（默认 `http://127.0.0.1:8765`）。
本文档说明**思路和手法**，MCP 工具只负责执行。

---

## 0. 心智模型

网页游戏离线化 = 三件事，按可靠性排序：

| 层次 | 手法 | 能覆盖 | 局限 |
|---|---|---|---|
| L1 静态解析 | 正则扫 HTML/CSS/JS 里的资源引用 | 90% 的普通页面 | 拿不到运行时拼接的 URL、二次加载的资源 |
| L2 路径镜像 | 网络目录结构 == 本地目录结构 | 让相对引用离线可用 | 需要路径规划一致 |
| L3 运行时懒补漏 | 本地起 HTTP 服务，浏览器请求什么就回源抓什么 | 覆盖 L1 的全部盲区 | 需要真正跑一遍游戏 |

**核心结论：L3 是必需品，不是可选项。**
只做 L1 一定失败；L1+L2 能跑通大部分；加上 L3 才能对付动态加载。

本工具三层都实现了。当自动下载失败时，按下面的顺序排查。

---

## 1. 先确认引擎类型

不同引擎的坑完全不同，别一上来就猜。

```
identify_url(url)  ->  {engine: flash|unity|html, entry_url, name}
```

引擎判据：
- **Unity WebGL**：页面含 `createUnityInstance` / `.unityweb` / `Build/*.loader.js` / `Build/*.data`
- **Flash**：`.swf` 文件，或 `<embed type="application/x-shockwave-flash">`
- **HTML5**：其余（Phaser / Cocos / PixiJS / 自研 canvas）

---

## 2. 分引擎排查手册

### 2.1 Unity WebGL

Unity 的资源清单固定为这五类，缺一不可：

```
Build/<hash>.loader.js          # 引导脚本（最先加载）
Build/<hash>.framework.js       # 引擎胶水层
Build/<hash>.wasm               # 引擎本体（可带 .br/.gz）
Build/<hash>.data               # 游戏资源（可带 .br/.gz）
Build/<hash>.jpg|png            # 加载背景图（可选）
StreamingAssets/***             # 若游戏用了则必需
TemplateData/style.css          # 加载界面样式
```

**坑 1：地址在 JS 变量拼接里**
```js
const buildUrl = "Build";
const loaderUrl = buildUrl + "/7a2d....loader.js";   // 纯正则拿不到 "Build" 前缀
```
解决：先抽 `const/var/let NAME = "..."` 常量表，再解析 `NAME + "/tail"`。
本工具 `resolve_js_concat()` 已实现；变量声明在别的文件时，会按 `Build/`、`TemplateData/`、`StreamingAssets/` 三个约定目录做候选。

**坑 2：`.br` / `.gz` 后缀不等于真压缩**
有些 CDN 存的是**已解压**文件却保留 `.br` 后缀（响应头没有 `Content-Encoding`）。
判定方法：
```python
import brotli, gzip
raw = open(f, "rb").read()
try: brotli.decompress(raw); real = "brotli"
except Exception:
    try: gzip.decompress(raw); real = "gzip"
    except Exception: real = "plain"
```
- 真压缩 + Unity 自称 `.br`：本地服务需回 `Content-Encoding: br`，Unity 才会解压
- 假压缩（实际明文）：**绝不能**加 `Content-Encoding`，否则 Unity 解压失败

**坑 3：下载时被中间层自动解压**
如果下载库（如 requests）自动 gunzip/brotli 解压，文件内容变了但名字仍是 `.br`，
Unity 再解压一次就崩。**必须发送 `Accept-Encoding: identity`**。

**坑 4：Unity 加载卡在 Loading**
- 检查 `.wasm` 的 `Content-Type`：应为 `application/wasm`
- 检查是否缺 `framework.js`
- 用 `--enable-unsafe-swiftshader` 让无 GPU 环境也能拿到 WebGL 上下文
- 打开控制台看是否有 `Unable to decode audio data`：说明 `.data` 内容损坏或被截断

### 2.2 Flash (.swf)

**运行时**：官方 Flash Player 已停更，用 **Ruffle**（Rust 实现的 Flash 模拟器）。
工具已内置 Ruffle 到 `assets/ruffle/`，下载时自动拷到游戏的 `_runtime/` 下，用户无需联网。

**坑 1：主文件地址藏在接口里**
某些站点（如 17yy）需要 POST 一个 ajax 接口换取真实 swf 路径。
用 `fetch_text` 看页面 JS，找到接口后手动构造请求，把结果作为 `entry_url` 传给 `download_game`。

**坑 2：防盗链**
必须带正确的 `Referer`（通常是游戏详情页，不是 CDN 域名）。
`fetch_text` 支持 `referer` 参数。

**坑 3：swf 内部还会再请求外部资源**
Ruffle 加载 swf 后，swf 里的 `loadMovie` 可能再拉别的 swf/图片。
这时靠 **L3 懒补漏**：本地服务接到请求自动回源。
若回源失败，说明原始 URL 的推导错了——检查 `.hgd_urlmap.json`。

### 2.3 HTML5

**坑 1：资源清单在 JSON 里**
Cocos / Phaser 常把资源列表放在 `assets/resources/config.json` 之类的清单文件里。
静态正则只能看到清单文件本身，看不到清单内部列的资源 → 靠 L3 兜底。

**坑 2：动态拼接路径**
```js
var p = "level" + n + ".json";
```
运行前无法枚举 → L3 兜底。

**坑 3：跨域限制**
`document.domain` 在本地 `127.0.0.1` 下执行会抛错，阻塞后续脚本。
本工具下载时自动剥离该语句（见 `site_plugins.py` 的 `rewrite`）。

---

## 3. 排查流程（碰壁时按序执行）

```
1. identify_url(url)
   └─ 失败 → fetch_text(url, limit=20000) 看是否被反爬（需要 UA/Cookie/代理）
             └─ 需要代理 → set_proxy("http://127.0.0.1:10808")

2. extract_refs(url, keyword=".swf")  或  keyword="Build"  或  keyword=".data"
   └─ 找到主文件 → download_game(url, entry_url=<手动指定>)
   └─ 找不到 → 用 fetch_text 直接读页面源码，人工找线索

3. 下载完成但跑不起来
   └─ 打开游戏目录，检查 Build/ 或资源目录是否完整
   └─ 对比页面里声明的文件名与实际落盘文件名
   └─ 缺文件 → 手工下载：用 python requests 带上正确 Referer 补到对应目录
               （目录结构必须与网络 URL 路径一一对应，L2 原则）

4. 还是不行 → 启动播放器，打开开发者工具看具体 404 的 URL
   └─ 记下 URL，确认是域名错、路径错、还是防盗链
```

---

## 4. 路径镜像规则（L2）

本地目录结构 = 网络 URL 的 `host/path`：

```
https://sda.4399.com/4399swf/upload/swf/a/Build/x.js
    -> <游戏目录>/sda.4399.com/4399swf/upload/swf/a/Build/x.js
```

这样页面里的相对引用（`Build/x.js`）无需改写就能命中。
**手动补文件时必须遵守这条**，否则页面引用不到。

URL 带 query 时（`x.js?v=2`）会落盘为 `x__v=2.js` 避免覆盖。
精确的「本地路径 → 原始 URL」映射记录在游戏目录的 `.hgd_urlmap.json`（隐藏文件）。

---

## 5. MCP 工具速查

### 下载类

| 工具 | 用途 |
|---|---|
| `identify_url` | 识别网址 → 引擎 / 名称 / 入口 |
| `download_game` | 下载（可手动指定 `entry_url` 绕过自动识别） |
| `fetch_text` | 抓页面源码分析（支持 `referer`） |
| `extract_refs` | 提取页面所有资源地址（含 JS 拼接还原），支持关键词过滤 |
| `set_proxy` | 设置代理 |

### 管理类

| 工具 | 用途 |
|---|---|
| `list_games` | 列出游戏库 / 收藏夹 |
| `get_game` | 查单个游戏详情（路径、引擎、分类、存档占用、广告残留数） |
| `update_game` | 重命名、改分类、收藏/取消、写备注、修正路径 |
| `delete_game` | 删除条目（可选连同文件） |
| `scan_directory` | 扫描本地目录批量登记游戏 |
| `clean_ads` | 清理页面里的广告 / 统计代码（支持 `dry_run` 预览） |
| `package_game` | 打包成 7z（便于拷贝分发或部署到网站） |
| `list_pending_saves` | 查看各游戏存档占用与待清理的旧代际 |

### 广告清理说明

`clean_ads` 只改写 **HTML 页面**，不动 JS / CSS / 二进制：

- **移除**：广告与统计的 `<script>` 标签（AdSense、百度统计、CNZZ、4399 广告接口等）
- **移除**：明确的广告位容器（`id`/`class` 含 `ad_slot`、`advert`、`adsense` 等）
- **中和**：内联广告调用（`adsbygoogle.push`、`adBreak`、`adConfig`），
  并下发 `window.adsbygoogle = window.adsbygoogle || []` 空数组兜底，
  避免残留代码抛 `ReferenceError`
- **移除**：统计埋点（透明 gif、埋点 script/link）

**为什么不动 JS**：实测 Unity 的 `framework.js` 里有
`adsbygoogle_present: !!window.adsbygoogle` 这类**只读遥测**，
既不加广告也不发请求；正则改写 JS 只会破坏引擎文件。
广告注入几乎总在 HTML 层，清 HTML 就够。

下载流程默认已自动清理（设置里可关 `clean_ads`）。

### 打包说明

`package_game` 产出的 7z 内含：

- 游戏本体（保持原目录结构）
- `index.html` 播放页
- `启动游戏.bat` / `start-game.sh` + `_serve.py` —— 起本地 HTTP 服务再打开，
  避开浏览器对 `file://` 的 CORS 限制
- `使用说明.txt` —— 游玩方法与**网站部署**说明

站点部署：把包内容原样上传到网站目录即可（游戏在网站里本就通过 HTTP 提供）。

压缩工具优先用系统 7-Zip，找不到则用内置 `assets/7z/7zr.exe`。

调用示例（JSON-RPC over HTTP）：

```bash
curl -s http://127.0.0.1:8765 \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"identify_url","arguments":{"url":"https://example.com/game"}}}'
```

---

## 6. 常见错误信息对照

| 现象 | 原因 | 处理 |
|---|---|---|
| `Unable to decode audio data` | `.data` 文件损坏/截断 | 重新下载该文件，检查是否被中间层解压 |
| Unity 一直 Loading，无报错 | 缺 `.wasm` / `framework.js` | 检查 Build 目录完整性 |
| `wasm streaming compile failed` | Content-Type 不是 `application/wasm` | 本地服务补 MIME |
| 白屏，控制台 `document.domain` 报错 | 跨域限制 | 剥离 `document.domain` |
| Flash 显示 "Ruffle 无法加载" | swf 路径错或 ruffle 未就位 | 检查 `_runtime/ruffle/` 与 swf 路径 |
| 404 一堆但游戏能跑 | 统计/广告脚本 | 忽略，不影响 |
| 下载 403 | 防盗链 | 补 `Referer` |

---

## 7. 给 Agent 的建议

- **先探测再下载**，别盲目重试。`identify_url` + `extract_refs` 成本极低。
- **手动指定 entry_url 是最有效的兜底**。自动识别做的是通用启发式，站点特殊改版时必然失效。
- **下载后一定要验证**：用 `tools/verify_game.py` 检查引擎是否真正初始化，
  而不是看文件数量够不够。
- **临时文件放临时目录**，不要污染用户目录。
- 碰不到的：需要登录的、强加密（如某些商用引擎自定义格式）、依赖服务端逻辑的游戏，
  如实告知用户不可离线化，不要伪造成功。
