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
python scripts/deploy.py root@192.168.31.205            # 实际部署
python scripts/deploy.py --dry-run root@192.168.31.205  # 仅打印，不动手
python scripts/deploy.py --reset   root@192.168.31.205  # 顺手清掉 /tmp/retrostation_*
```

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
| **M3.6** | **输入层修正（真机键位映射 + 方向键符号处理）+ `probe_input.py` 自检** | **✅ 完成（本次）** |
| M4 | 副屏视频（ffmpeg 管道解码 + 帧队列） | 待开始 |
| M5 | 游戏启动联调（真机 RA / 独立模拟器 / 守护重启） | 待开始 |
| M6 | 设置落地（背光/语言/主题）、单屏兼容打磨 | 待开始 |
| M7 | 性能打磨（帧预算、纹理释放、冷启动） | 待开始 |

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
│   ├── deploy.py                # 跨平台部署
│   ├── deploy.sh                # bash 包装
│   ├── deploy.ps1               # PowerShell 包装
│   ├── screenshot.py            # 无头渲染所有界面
│   └── device_selftest.py       # ES-DE 写回烟测
├── src/retrostation/            # 应用代码
│   ├── core/                    #   config, model, theme, i18n
│   ├── data/                    #   library, scanner, media, sources/{esde,pegasus}
│   ├── launcher/                #   游戏启动（RA / 独立模拟器）
│   ├── platform/                #   平台抽象 + linux/ 实现
│   └── ui/                      #   状态机、painter、screens、widgets
└── tests/                       # 单元测试（pytest 140+ 通过）
```
