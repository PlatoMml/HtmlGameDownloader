# 网页游戏下载器

把网页游戏（HTML5 / Unity WebGL / Flash）下载到本地离线游玩。
贴一个网址 → 自动识别 → 下载 → 双击开玩。

> 参考了 [FlashGameDownloader](https://github.com/icefairy999/FlashGameDownloader) 的抓取思路，
> 重写了整个前端与抓取引擎。

---

## 快速开始

```bash
# Windows
run.bat

# Linux / macOS
chmod +x run.sh && ./run.sh
```

首次运行会自动安装依赖（PySide6 + requests）。

手动运行：

```bash
pip install -r requirements.txt
python -m hgd
```

---

## 功能

| 功能 | 说明 |
|---|---|
| **网址识别** | 贴网址即自动识别游戏名、引擎类型、真实入口地址，名字可手动修改 |
| **离线下载** | 递归镜像全部资源，下载到任意指定目录，断点续传式（已存在则跳过） |
| **收藏夹** | 收藏游戏、自定义分类、分类重命名、游戏重命名 |
| **目录识别** | 扫描本地目录，自动发现并登记其中已有的多个游戏 |
| **游戏播放** | 内置播放器，打开即网页全屏，音量拖动条（默认 30%）、一键静音、全屏（ESC 退出） |
| **存档与重玩** | 游戏存档自动持久保存、每个游戏独立隔离；「重玩」按钮二次确认后从全新存档开始 |
| **打包分发** | 一键导出 7z：含播放页、启动脚本与说明文档，可拷到别的电脑玩，也可直接部署到网站 |
| **广告清理** | 自动移除页面里的广告脚本、广告位与统计埋点（只改 HTML，不动游戏资源） |
| **MCP 接入** | 内置 MCP 服务，外部 Agent 可接手疑难游戏下载，端口可自定义 |

### 服务端渲染的抓取引擎

三层协作，越是难点越靠后：

1. **静态解析** — 扫描 HTML/CSS/JS 中的资源引用，含 JS 变量拼接还原
   （`buildUrl + "/x.loader.js"` 这类 Unity 常见写法）
2. **路径镜像** — 本地目录结构 == 网络 URL 结构，页面相对引用天然可用，无需改写
3. **运行时懒补漏** — 本地起 HTTP 服务，浏览器请求什么就回源抓什么，
   覆盖 JS 动态拼接、懒加载、二次请求等静态解析的盲区

这三层是实测打出来的：只做第 1 层时，Unity 游戏的 `Build/*.data.br` 全部抓不到。
详见 [`skill/SKILL.md`](skill/SKILL.md)。

---

## 播放器

- **音量**：拖动条调节，默认 30%，设置持久化
- **静音**：拖动条左侧喇叭按钮，点击静音 / 再点恢复
- **网页全屏**：**默认窗口状态** —— 打开游戏即为最大化的带工具条窗口，
  游戏在窗口内保持比例铺满。仍是普通窗口，可自行拖动缩放或还原
- **全屏**：保持比例铺满显示器，隐藏工具条，鼠标移到屏幕顶部不弹出任何遮挡，
  `ESC` 或按钮返回网页全屏
- **重玩**：清除本游戏存档，从头开始（需勾选确认，防误触）

## 游戏存档

游戏进度（localStorage / IndexedDB / Cookie 等）自动保存到
`data/saves/<游戏>/`，重开游戏继续上次进度。

实现上受三个约束（均为实测得出）：

1. **必须用命名 profile** —— QtWebEngine 默认 profile 是无痕模式，
   用它打开游戏存档在关窗时全部丢失
2. **每个游戏独立存储** —— 否则两个游戏用同名键（如 `level`）会串档
3. **镜像端口按游戏固定** —— 浏览器存储按 `scheme://host:port` 隔离，
   端口一变同一游戏就变成"另一个源"，读不到旧存档

**重玩**采用「代际切换」而非直接删目录：游戏运行时 leveldb 持有文件句柄，
直接删除会失败或留下半删状态导致重载异常。切换代际让游戏立刻用上全新的空目录，
旧目录在句柄释放后自动回收 —— 既保证"从新存档开始"立刻生效，也不长期占盘。

存档管理入口：**设置 → 游戏存档**（查看占用 / 打开目录 / 清空全部）。

---

## MCP 服务

在「设置」页启用，端口可自定义（默认 8765）。启用后外部 Agent 可调用：

**下载类**

| 工具 | 用途 |
|---|---|
| `identify_url` | 识别网址 → 引擎 / 名称 / 入口 |
| `download_game` | 下载（支持手动指定 `entry_url` 绕过自动识别） |
| `fetch_text` | 抓页面源码分析（支持 `referer`） |
| `extract_refs` | 提取页面所有资源地址，支持关键词过滤 |
| `set_proxy` | 设置代理 |

**管理类**（Agent 可直接管理游戏库）

| 工具 | 用途 |
|---|---|
| `list_games` / `get_game` | 列出游戏、查单个详情（含存档占用、广告残留数） |
| `update_game` | 重命名、改分类、收藏/取消、写备注、修正路径 |
| `delete_game` | 删除条目（可选连同文件） |
| `scan_directory` | 扫描本地目录批量登记 |
| `clean_ads` | 清理广告/统计代码（`dry_run` 可预览） |
| `package_game` | 打包成 7z |
| `list_pending_saves` | 查看存档占用 |

调用示例：

```bash
curl -s http://127.0.0.1:8765 \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"identify_url","arguments":{"url":"https://example.com/game"}}}'
```

只跑 MCP（不开界面）：`python -m hgd --no-gui --mcp-port 8765`

**疑难游戏排查手册：[`skill/SKILL.md`](skill/SKILL.md)** —— 分引擎的坑位清单与排查流程。

---

## 打包分发

在「游戏库」里选中游戏 → 点「打包为 7z」（或右键菜单）。
压缩包内含游戏本体、播放页、启动脚本与说明文档。

- **拷到别的电脑玩**：解压后双击 `启动游戏.bat`（Linux/macOS 用 `./start-game.sh`）。
  脚本会起一个本地 HTTP 服务再打开浏览器 —— 直接双击 `index.html` 时
  Chrome/Edge 会因安全策略拒绝加载 Unity 等引擎的资源。
- **部署到网站**：把包内容原样上传到网站目录，访问 `index.html` 即可在线游玩，
  无需任何额外配置（游戏在网站里本来就通过 HTTP 提供）。

压缩工具优先用系统已装的 7-Zip，找不到则用内置的 `assets/7z/7zr.exe`。

## 广告清理

下载完成后会自动清理页面里的广告与统计代码，也可在右键菜单手动对已有游戏执行。

清理范围：广告/统计 `<script>` 标签、明确的广告位容器、
内联广告调用（`adsbygoogle.push` / `adBreak` 等）、统计埋点。

**只改 HTML 页面**，不碰 JS / CSS / 二进制资源 —— 实测 Unity 的 `framework.js`
含 `adsbygoogle_present: !!window.adsbygoogle` 这类只读遥测，改写会破坏引擎文件。
清理后还会下发空的 `window.adsbygoogle` 兜底，避免残留代码抛 `ReferenceError`。

可在设置里关闭 `clean_ads`。

## 项目结构

```
hgd/
  config.py            路径与设置
  models.py            数据模型
  core/
    http_client.py     HTTP 客户端（禁用传输压缩、重试、编码探测）
    mirror.py          资源提取 + 递归镜像下载器
    mirror_server.py   本地镜像服务 + 运行时懒补漏
    identifier.py      网址识别 / 目录扫描识别
    site_plugins.py    站点特例插件（4399 / 7k7k / 通用）
    downloader.py      下载编排 + 播放入口生成
    adclean.py         广告/统计代码清理
    packager.py        7z 打包（含启动脚本与说明文档）
    db.py              SQLite 游戏库 + 收藏夹
  ui/
    main_window.py     主窗口（下载 / 游戏库 / 收藏夹 / 设置）
    player.py          播放器（音量 / 静音 / 两种全屏）
    bridge.py          音量控制通道注入
    theme.py           深色主题与矢量图标
  mcp/
    server.py          MCP 服务端（标准库实现，零额外依赖）
assets/ruffle/         内置 Ruffle（Flash 运行时，用户无需另行下载）
skill/SKILL.md         疑难游戏下载技能文档
tools/                 验收测试与诊断脚本
tests/                 单元测试
```

---

## 测试

```bash
# 单元测试（离线，32 项）
python tests/test_core.py

# 启动验证（模拟双击 run.bat，确认主窗口真的显示出来）
python tools/test_launch.py

# UI 端到端（41 项：收藏/分类/扫描/音量/静音/全屏/ESC/MCP/设置持久化）
python tools/test_ui.py

# 存档与重玩（40 项：跨会话保留 / 游戏间隔离 / 重玩清档 / 二次确认）
python tools/test_saves.py

# 广告清理（20 项：清理效果 + 不误伤游戏逻辑）
python tests/test_adclean.py

# 打包与 MCP 管理工具（23 项：压缩包内容 / 解压可玩 / Agent 管理能力）
python tests/test_packager.py

# 需求逐条验收（66 项，需要联网与已下载的测试游戏）
python tools/acceptance.py

# 真实游戏运行验证
python tools/verify_game.py "<游戏目录>" --seconds 90

# Flash 链路验证
python tools/test_flash.py

# GUI 截图测试
python tools/test_gui.py
```

### 关于启动脚本

`run.bat` / `run.sh` 内部**只使用 ASCII**。

原因：cmd.exe 用系统 OEM 代码页（简体中文 Windows 是 GBK）解析 `.bat` 文件的字节，
而 `chcp 65001` 只改变控制台输出代码页、不改变解析代码页。
如果批处理里写了 UTF-8 中文，中文行会被当作命令执行，脚本瞬间退出 ——
表现为**双击毫无反应**。所以中文提示一律由 Python 侧输出。

---

## 已知限制

- **需要登录 / 强服务端逻辑**的游戏无法离线化，工具会如实报错而非伪造成功
- 少数站点用混淆 JS 动态生成地址，静态解析抓不到——靠懒补漏兜底，或按 `skill/SKILL.md` 手动指定入口
- Unity 大型游戏（100MB+）首次加载需数十秒，属正常现象
- Flash 通过 Ruffle 模拟，极少数使用冷门 AVM2 特性的老游戏可能不完全兼容

## 许可

本项目代码 MIT。内置的 [Ruffle](https://ruffle.rs/) 运行时为 MIT / Apache-2.0 双许可（见 `assets/ruffle/LICENSE_*`）。
