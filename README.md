# Retrostation

Linux 掌机**双屏游戏前端**，兼容单屏。基于 [tiny-scraper](../tiny-scraper) 的 SDL2 + Pillow 渲染栈，
适配 Anbernic **RG DS**（Buildroot / Weston / 双 640×480 DSI 屏）。

- 上屏（DSI-1）交互主控，下屏（DSI-2）上下文联动
- 游戏页 **三种视图**：列表 / 网格 / 封面轮播（`X` 键循环）
- 媒体三类：**封面** `Imgs/` · **视频** `video/` · **Logo** `logo/`；下屏媒体区**视频优先，缺则封面**
- 元数据用 **`gamelist.xml`（ES-DE 兼容）**，收藏/游玩次数/上次游玩写回 gamelist，与 ES-DE / Batocera 互通
- **数据源可插拔**（§6.8）：`data/sources/` 插件层，当前 ES-DE，预留 **Pegasus**；新增格式只加一个文件，UI 与扫描器不动
- 只检测到 1 个显示输出时自动降级为单屏布局，功能零缺失
- 冷启动索引缓存 + 缩略图缓存；游戏退出后由守护脚本恢复到原位
- **副屏视频**（M4）：ffmpeg 管道软件解码，无视频/解码失败自动回退封面；实测 4.5% 单核
- **跨平台预留**（§17）：只有 `platform/` 允许平台代码，尺寸全走 theme token，为后续 Android App 做准备

## 文档

| 文档 | 内容 |
|---|---|
| [docs/DESIGN.md](docs/DESIGN.md) | 详细设计：真机环境实测、架构、双屏方案、渲染管线、输入系统、数据层与**数据源插件架构**、启动器、视觉规范、**跨平台/Android 预留**、风险与里程碑 |
| [docs/PROTOTYPE.md](docs/PROTOTYPE.md) | UI 原型说明：页面状态机、键位、布局数值、原型→真机映射 |

## 部署到掌机

一键部署（Windows / macOS / Linux 通用）：

```bash
# 任何带 Python + ssh/scp 的环境都行
python scripts/deploy.py root@192.168.31.205            # 实际部署（走 ssh/scp，需要 key）
python scripts/deploy.py --dry-run root@192.168.31.205  # 仅打印，不动手
python scripts/deploy.py --reset   root@192.168.31.205  # 顺手清掉 /tmp/retrostation_*

# 设备只有密码（出厂 root/root）时加 --password，会自动改用 paramiko
python scripts/deploy.py root@192.168.0.55 --password root
# 或：export RETROSTATION_SSH_PASSWORD=root
```

> 需要 `pip install paramiko`（仅密码登录时需要）。Windows 的 `ssh` 无法非交互传密码，
> 所以密码模式下 `deploy.py` 走 `scripts/remote.py`（paramiko 的 SSH/SFTP）。
> `scripts/remote.py` 也可单独用来跑命令 / 传文件：
> `python scripts/remote.py root@192.168.0.55 "tail -20 /mnt/mmc/Roms/APPS/Retrostation/log.txt"`

或者直接 PowerShell / bash 包装：

```powershell
.\scripts\deploy.ps1 root@192.168.31.205
```

```bash
./scripts/deploy.sh root@192.168.31.205
```

部署会推 4 类文件到设备：

| 源 | 目标 | 说明 |
|---|---|---|
| `src/` | `/mnt/mmc/Roms/APPS/Retrostation/src/` | 应用代码 |
| `retrostation.sh` | `/mnt/mmc/Roms/APPS/Retrostation/retrostation.sh` | 启动器（含崩溃保护 + 日志轮转） |
| `packaging/APPS/Retrostation.sh` | **`/mnt/mmc/Roms/APPS/Retrostation.sh`** | APPS 菜单入口（必须在 APPS **根目录**，原厂菜单不扫子目录） |
| `packaging/APPS/Imgs/Retrostation.png` | `/mnt/mmc/Roms/APPS/Imgs/Retrostation.png` | APPS 菜单封面 240×180 |

部署完成后：

1. 在设备上打开 **APPS** 菜单（首页 → APPS）
2. 选择 **Retrostation.sh** → 启动
3. 上屏进入游戏库，下屏显示当前游戏详情（视频/封面 + 元数据）

> **原厂前端持屏，SSH 启动会失败。** 必须从 APPS 菜单进入，原厂 UI 才会把屏幕让出来。
> 启动时所有输出落在 `/mnt/mmc/Roms/APPS/Retrostation/log.txt`，启动失败查这里。

### 操作

| 键 | 平台页 | 游戏页 |
|---|---|---|
| 上下 | 切换平台 | 移动选择（网格按列跳） |
| 左右 | 切换平台 | 列表 ±10 / 网格、轮播 ±1 |
| **A** | 进入游戏库 | **启动游戏** |
| **B** | — | 返回平台页 |
| **X** | — | 切换 列表 / 网格 / 轮播 |
| **Y** | — | 收藏 / 取消收藏 |
| L1 / R1 | — | 上下翻页 |
| L2 / R2 | — | 跳到 首 / 尾 |
| SELECT | — | 切换筛选（全部 / 有封面 / 缺封面） |
| START | 设置菜单 | 设置菜单 |
| FUNC **长按** | 退出确认 | 退出确认 |

> FUNC 键（BTN_TL2）**长按 0.8 秒**退出。L3 / R3 也绑定了同样的动作，避免误以为退不出去。

### 排错

```bash
# SSH 进设备，无需屏幕即可诊断
cd /mnt/mmc/Roms/APPS/Retrostation
PYTHONPATH=src python3 -m retrostation.main --scan-only      # 库扫描摘要
PYTHONPATH=src python3 -m retrostation.main --check FC       # 某平台元数据/媒体覆盖率
PYTHONPATH=src python3 -X utf8 scripts/screenshot.py /tmp/shots  # 无头渲染各界面

# ---------- 按键相关 ----------
# 1) 静态自检：键位映射是否覆盖这台设备（无需按键）
python3 scripts/probe_input.py --check-keymap

# 2) 动态抓码：跑起来后按键，看内核实际发什么（按 Ctrl+C 结束）
python3 scripts/probe_input.py --watch-any --seconds 20

# 3) 列出所有输入节点及其能力位图
python3 scripts/probe_input.py

# ---------- 视频相关 ----------
# 副屏视频自检：找一段真实视频，测帧率 / CPU / 首帧延迟（需 ffmpeg）
PYTHONPATH=src python3 -X utf8 scripts/video_selftest.py
# 给某个平台的第一个 ROM 合成一段 10s 测试视频（设备无素材时最快的上手方式）
PYTHONPATH=src python3 -X utf8 scripts/video_selftest.py --make-demo FC
# 无头跑真实 UI，把上下屏存成 PNG（验证「副屏真的在播」）
PYTHONPATH=src python3 -X utf8 scripts/video_selftest.py --system FC --ui /tmp/shots

# 如果下屏被原厂 overlay 遮挡（subscreen.dge 等残留），在 APPS 入口里：
RETROSTATION_KILL_OVERLAY=1  # 临时环境变量，启动脚本会杀掉残留进程
```

**键位映射的真实来源**（`src/retrostation/platform/linux/input.py`）：

```
方向键  ABS_HAT0X/Y (16/17)，值 -1/0/+1 —— 符号决定方向，不是"非零即按下"
A=BTN_SOUTH(304)  B=BTN_EAST(305)  Y=BTN_C(306)  X=BTN_NORTH(307)
L1=BTN_WEST(308)  R1=BTN_Z(309)    L2=BTN_SELECT(314)  R2=BTN_START(315)
SELECT=BTN_TL(310)  START=BTN_TR(311)  FUNC=BTN_TL2(312) / L3(313) / R3(316)
```

> 这不是常规手柄布局。它来自厂商自己的定义 `/mnt/mod/ctrl/configs/functions`，
> 并与 `ANBERNIC-rk3568-keys` 的能力位图交叉验证过。换设备时改 `DEFAULT_KEYMAP`
> 即可，UI 与状态机不用动——先用 `--check-keymap` 确认，别靠猜。

### 关键环境变量（设备 APPS 菜单不会带）

| 变量 | 默认 | 说明 |
|---|---|---|
| `PYSDL2_DLL_PATH` | `/usr/lib` | 找 libSDL2-2.0.so.0 |
| `XDG_RUNTIME_DIR` | `/var/run` | weston socket 所在 |
| `WAYLAND_DISPLAY` | `wayland-0` | 多屏识别靠它 |
| `RETROSTATION_ROM_ROOT` | `/mnt/mmc/Roms` | ROM 库根 |
| `RETROSTATION_CONFIG_DIR` | 启动器目录 | 配置/状态写在这里 |
| `RETROSTATION_KILL_OVERLAY` | 0 | 设为 1 会 `pkill` 残留原厂进程 |
| `RETROSTATION_FONT` | 自动 | 自定义字体路径，覆盖默认 source-han-sans-cn |
| `RETROSTATION_SSH_PASSWORD` | — | 部署/远程脚本用的 SSH 密码（默认 `root`） |

### 视频相关配置（`config.json`）

| 键 | 默认 | 说明 |
|---|---|---|
| `bottom_video` | `true` | 副屏视频总开关；单屏模式强制关闭 |
| `video_size` | `[288, 216]` | ffmpeg 输出尺寸（未拿到布局时的兜底；运行中会按副屏媒体框内框实际尺寸解码） |
| `video_fps` | `15` | 解码/播放帧率，同时是解码线程的节流节奏 |
| `bottom_refresh_ms` | `90` | 副屏**静态**内容的最小重绘间隔；有视频时按帧驱动 |

### 视频文件放哪

按此顺序探测（`<系统>/` 下，`<stem>` = ROM 主文件名）：

1. `video/<stem>.mp4|.webm|.mkv|.avi`
2. `Imgs/<stem>.mp4|...` —— 实测这台设备的刮削器把 mp4 丢在封面目录里
3. ES-DE 惯例 `media/videos/<stem>.mp4|...`

`gamelist.xml` 里的 `<video>` 标签优先级最高（数据源已解析的不会被覆盖）。
覆盖率用 `python3 -m retrostation.main --check NDS` 看 `video` 那一行。

## 本地开发

```bash
python -m pytest                                   # 全部单元测试（无需掌机）
python -X utf8 scripts/screenshot.py               # 无头渲染各界面到 screenshots/
python -m retrostation.main --scan-only --headless --rom-root <目录>
```

`scripts/screenshot.py` 用真实帧循环渲染列表/网格/轮播/菜单/下屏，输出 640×480 PNG，
是布局回归的快速检查手段。

## UI 原型

```bash
cd prototype && python -m http.server 8899
# 浏览器打开 http://127.0.0.1:8899/
```

或用任意静态服务器打开 `prototype/index.html`。键盘映射见 [docs/PROTOTYPE.md](docs/PROTOTYPE.md#3-键盘--手柄映射)。

## 目标机环境（实测摘要）

| 项 | 值 |
|---|---|
| 设备 / 系统 | `RGds` / Buildroot 2024.02, Linux 6.1.141, aarch64, 4 核 / 3 GB |
| 显示 | `card0-DSI-1` + `card0-DSI-2`，均 640×480；合成器 Weston(DRM) |
| 运行时 | Python 3.11.8 · Pillow 10.2.0 · SDL2 2.0.32（**无 evdev / requests**） |
| **视频解码** | **ffmpeg 4.4.4** 可用；**无硬解**（无 `/dev/video*`）→ SW 解码 288×216@15fps ≈ **19% 单核** |
| ROM | `/mnt/mmc/Roms/<SYS>/`；封面 `Imgs/` · 视频 `video/` · Logo `logo/` · `gamelist.xml` |
| 启动 | `/mnt/mod/ctrl/RA_launch.sh <core.so> <rom>`；NDS/PSP/SATURN/DC 走独立模拟器 |
| 输入 | 手柄 `/dev/input/event4`，触摸 `/dev/input/event1` |

完整清单见 [docs/DESIGN.md §2](docs/DESIGN.md#2-运行环境实测结论设备档案)。

## 状态

**主线（Linux 双屏掌机）**

| 阶段 | 内容 | 状态 |
|---|---|---|
| M0 | 详细设计（三视图 / 三类媒体 / 数据源架构 / 副屏视频 / 跨平台预留）+ 交互原型 | ✅ 完成 |
| M1 | `platform/` 抽象 + Linux 实现（SDL2 双屏、evdev、sysfs）+ `core/` | ✅ 完成 |
| M2 | `data/`（扫描、索引、媒体缓存）+ `ui/`（三视图、副屏联动、设置菜单） | ✅ 完成 |
| M3 | `data/sources/`（ES-DE 读写 + Pegasus 只读） | ✅ 完成 |
| **M3.5** | **APPS 菜单入口 + 一键部署 + 启动器崩溃保护** | ✅ 完成 |
| **M3.6** | **输入层修正（真机键位映射 + 方向键符号处理）+ `probe_input.py` 自检** | ✅ 完成 |
| **M4** | **副屏视频（`data/video.py` 防抖/节流/降级 + `platform/linux/video.py` ffmpeg 管道）+ 媒体框视频/封面自动降级** | **✅ 完成（本次）** |
| **M5** | **游戏启动联调（命令文件交接 + 守护重启跑游戏 + 退出后回到原位）** | **✅ 完成（本次）** |
| **M6** | **设置落地（主题/明暗/语言/背光/状态栏 + 即时落盘）、单屏兼容** | **✅ 完成（本次）** |
| **M7** | **性能打磨（冷启动、常驻内存、视频播放时的主屏帧率）** | **✅ 完成（本次）** |

### M4 真机实测（RG DS · rk3568 · ffmpeg 4.4.4 · 软件解码）

| 指标 | 目标 | 实测 |
|---|---|---|
| 副屏视频帧率 | 15 fps | **14.2 fps** |
| 解码 CPU（单核） | < 25% | **3.8% @288×216 · 4.5% @328×256** |
| 首帧延迟 | < 350ms | **348ms**（250ms 防抖 + ffmpeg 启动） |
| 无视频 / 损坏视频 | 静默回退封面 | ✅ 会话黑名单，不重试 |
| 启动游戏后残留 ffmpeg | 0 | ✅ `close()` + 启动脚本 `pkill` 兜底 |
| 切换游戏时主线程阻塞 | < 50 ms | **2 ms**（修复前 **1009 ms**） |

> 切换卡顿的成因：ffmpeg 在 SD 卡上读到一半时基本不响应 SIGTERM，而
> `VideoPlayer` 原先在主线程同步等它退出（最坏 2 秒）。改成后台线程收尾后主线程不再
> 阻塞；启动游戏前的收尾仍同步等待，不会残留进程。

### M5 真机实测（RG DS · 魔改固件 · `RA_launch.sh` 在位）

"退出游戏后回到原位"没法靠肉眼确认，所以用一个 headless 脚本在真机上跑完整
链路（合成按键 → 检查交接物 → 再起一个进程模拟守护重启）：

| 环节 | 结果 |
|---|---|
| 启动交接 | ✅ 退出码 **42**，命令文件 `/tmp/retrostation_launch.cmd` 内容完整 |
| 中文 + 空格 ROM 名 | ✅ `set -- … 'B 中文.nes'`，`source` + `"$@"` 还原无误 |
| 守护脚本 | ✅ `bash -n` 通过；跑完游戏删除命令文件并循环回前端 |
| **回到原位** | ✅ 重启后回到同一系统、同一游戏 |

### M6 真机实测（RG DS · 192.168.0.55）

| 项 | 结果 |
|---|---|
| 主题 | ✅ 琥珀 / 冰蓝 / 青柠三套 accent + 明暗两套中性色，切换立即生效 |
| 语言 | ✅ 中英切换即时生效（`auto` 在本机解析为英文，见下） |
| 背光 | ✅ 真实写入 sysfs：`backlight` / `backlight1` 均为 140 → 150 → 恢复 |
| 单屏 | ✅ 强制 single：单画布、三视图正常、设置 / 启动 / 退出均可用 |
| 单屏详情条 | ✅ 副屏要点折叠到列表下方（封面/视频 + 4 行信息）；此前这块区域预留了空间却从未绘制 |
| 单屏视频 | ✅ 在详情条内播放，不再强制关闭；24 fps 实测 **23.5**（设备可跑满 30） |
| 设置落盘 | ✅ 改完即写 `config.json`，不必等到启动游戏 |

#### M6 后续修复（真机暴露的问题）

| 问题 | 根因 | 修复 |
|---|---|---|
| 菜单右侧只剩一个字 | 画布右对齐文本渲染到暂存图时在锚点左侧越界，被裁剪丢弃 | 按锚点的水平分量偏移绘制原点 |
| 切单屏后切不回双屏 | 配置落盘了但界面不重建 | 新增退出码 43，守护脚本重启前端（不跑游戏） |
| 详情条在视频与封面间闪烁 | 详情条烤进主屏缓存，但缓存只在整屏重绘时更新，30fps 对 15fps 视频正好逐帧交替 | 详情条重绘后同步回缓存 |
| 单屏切换明显比双屏卡 | `ellipsize` 逐字重测整串，是 O(n²)：426 字的简介单次要几秒 | 改二分查找 + 结果缓存 |
| 视频像慢动作 | ffmpeg 的 `-r` 放在 `-i` 前是重打时间戳而非抽帧，30fps 源慢 2 倍、60fps 慢 4 倍 | 去掉输入 `-r`，改由 `fps` 滤镜抽帧（放在缩放之前） |

> 关于 `auto` 语言：这台设备的 `/etc/profile` 不设 `LANG`，也没有 `/etc/default/locale`，
> 所以检测不到系统语言时会回退英文。想用中文就在菜单里把「语言」切到 `zh_CN`，之后会记住。

### M7 真机实测（RG DS · 26 个系统 / 3859 个 ROM）

| 指标 | 目标 | 优化前 | 实测 |
|---|---|---|---|
| 冷启动到首帧 | < 1.5 s | 2.13 s | **1.18 s** |
| 常驻内存 | < 120 MB | 40.1 MB | **39.7 MB** |
| 视频播放时主屏 | ≥ 30 fps | 29.1 fps | **30.2 fps** |

冷启动那 2.13 s 花在哪（优化前逐段计时）：

| 阶段 | 耗时 |
|---|---|
| 模块导入 | 0.80 s |
| 目录扫描（3873 次 `stat()`） | 0.71 s |
| 首帧绘制 | 0.62 s |
| platform 初始化 | 0.26 s |

四步优化：

1. **索引命中不再重建对象** —— `scan_library` 先列目录建好 `Rom`，再用索引反序列化一份**完全相同**的来覆盖它。索引只需回答"变没变"，那一遍重建纯属浪费（0.22 s）。
2. **目录扫描四线程** —— `stat()` 是 I/O 密集，SD 卡能同时应付几个目录（0.73 s → 0.60 s）。
3. **首帧不再等扫描** —— 启动时直接读索引（`cached_only`），真正的列目录放后台，界面一起来就是满的而不是空列表。
4. **后台扫描等首帧画完再启动** —— 原先两者抢 I/O，首帧从 0.57 s 涨到 1.34 s；改到首帧回调里启动后回到 0.57 s。

帧率那条不是性能不够，是调度过冲：为了及时接视频帧，每帧要分片 sleep/poll 若干次，每次都睡过头一点，33.3 ms 的预算出来变成 29.3 fps。给预算预留 1 ms 后回到 30.2 fps。

顺带修掉一个相关缺陷：后台扫描完成后没有任何通知，而列表缓存只在按键时失效——新扫出来的内容要等用户按一次键才出现。现在扫描结束会主动触发重绘。

**扩展线（M3 之后可并行）**

| 阶段 | 内容 | 状态 |
|---|---|---|
| E1 | `data/sources/pegasus.py` → **Pegasus 数据源**（只读） | ✅ 完成 |
| E2 | 尺寸 token 化 + 布局比例化（**Android 前置条件**） | 待开始 |
| E3 | `platform/android/`（Chaquopy 宿主） | 待开始 |
| E4 | Android 视频（硬解）+ 启动 Intent | 待开始 |

### 仓库目录

```
D:/code/Retrostation/
├── README.md
├── retrostation.sh              # 启动器（崩溃保护 + 日志轮转）
├── pyproject.toml
├── docs/
│   ├── DESIGN.md                # 详细设计
│   └── PROTOTYPE.md             # 原型说明
├── packaging/
│   └── APPS/                    # 部署到设备 APPS 根目录的文件
│       ├── Retrostation.sh      #   APPS 菜单入口
│       └── Imgs/Retrostation.png#   菜单封面 240×180
├── scripts/
│   ├── deploy.py                # 跨平台部署（key 或密码）
│   ├── deploy.sh                # bash 包装
│   ├── deploy.ps1               # PowerShell 包装
│   ├── remote.py                # paramiko 版 ssh/scp（密码登录用）
│   ├── screenshot.py            # 无头渲染所有界面
│   ├── device_selftest.py       # ES-DE 写回烟测
│   └── video_selftest.py        # 视频管线真机自检（帧率/CPU/首帧/无头截图）
├── src/retrostation/            # 应用代码
│   ├── core/                    #   config, model, theme, i18n
│   ├── data/                    #   library, scanner, media, sources/{esde,pegasus}
│   │   └── video.py             #     ★ 副屏视频：防抖 / 节流 / 降级
│   ├── launcher/                #   游戏启动（RA / 独立模拟器）
│   ├── platform/                #   平台抽象 + linux/ 实现
│   │   └── linux/video.py       #     ★ ffmpeg 管道解码
│   └── ui/                      #   状态机、painter、screens、widgets
└── tests/                       # 单元测试（pytest 225 通过）
```
