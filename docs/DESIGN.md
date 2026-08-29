# Retrostation — Linux 双屏游戏前端 详细设计

> 目标机型：Anbernic **RG DS**（Linux / Buildroot），兼容单屏设备（HDMI 单显、普通单屏掌机）
> 基础项目：`D:\code\tiny-scraper`（复用其 SDL2+PIL 渲染层、双屏初始化、平台表、多语言、刮削能力）
> 文档版本：v1.1 ｜ 所有环境参数均来自对真机 `root@192.168.31.205` 的实测
>
> **v1.1 变更**：新增 §6.8 **数据源插件架构**（ES-DE + 预留 Pegasus）、§17 **跨平台与 Android 移植**；
> 列表左图改用 Logo；下屏元数据改为评分 / 发布时间 / 多行简介，移除所有媒体类型标记

---

## 1. 项目目标与范围

### 1.1 目标

做一个**常驻的游戏选择前端（Frontend / Launcher）**，替代或并行于原厂 `dmenu_ln`：

- **双屏原生**：上屏交互、下屏展示，下屏不是"壁纸"而是**随选中项实时联动的上下文面板**
- **单屏兼容**：单显示器时自动降级为"上下分区"布局，功能零缺失
- **秒开**：ROM 索引缓存 + 缩略图缓存，冷启动 < 3s，切平台 < 300ms
- **可接管**：游戏启动/退出后能回到原界面位置

### 1.2 不在本期范围

- 内建模拟器（全部通过外部脚本拉起）
- NDS 双屏游戏内显示（由 `ndsCtrl.dge` + `subscreen.dge` 负责，前端启动后即让位）
- ROM 管理（增删改），仅做只读浏览 + 收藏

---

## 2. 运行环境实测结论（设备档案）

### 2.1 硬件与系统

| 项目 | 实测值 |
|---|---|
| 设备 | `RGds`（`/mnt/vendor/oem/board.ini`） |
| 系统 | Buildroot 2024.02，Linux 6.1.141，aarch64 |
| CPU / RAM | 4 核 Cortex-A55 / 2961 MB（可用 ~2573 MB） |
| 显示 | `card0-DSI-1`、`card0-DSI-2`，**均 640×480，均 connected+enabled** |
| 合成器 | **Weston**（`backend=drm-backend.so`，双 output，scale=1） |
| 触摸 | `gt9xx-0` → `/dev/input/event1`（下屏触摸） |
| 按键 | `ANBERNIC-rk3568-keys` → `/dev/input/event4` + `js0` |
| 背光 | `/sys/class/backlight/backlight`、`backlight1`，0–255（各屏独立） |
| 电量 | `/sys/class/power_supply/battery/{capacity,status}` |
| 温度 | `/sys/class/thermal/thermal_zone0/temp`（毫度，实测 56666 = 56.7℃） |

### 2.2 软件栈

| 项目 | 实测值 |
|---|---|
| Python | **3.11.8** |
| Pillow | **10.2.0** ✅ |
| SDL2 | `/usr/lib/libSDL2-2.0.so.0`（2.0.32），含 `SDL2_ttf / image / mixer / gfx` |
| 其他 Python 包 | **无 `evdev`、无 `requests`** → 输入自读 `/dev/input`，网络用 `urllib` |
| 中文字体 | `/usr/share/fonts/source-han-sans-cn/`（思源黑体）；回退 `/usr/share/fonts/dejavu/` |

### 2.3 ROM 与媒体布局

| 路径 | 说明 | 实测 |
|---|---|---|
| `/mnt/mmc/Roms/<SYS>/` | ROM（SD1/TF1，49 个平台目录 + APPS + PORTS） | FC 515、SFC 410、GBA 302、NDS 102、PS 23 |
| `/mnt/mmc/Roms/<SYS>/Imgs/<游戏名>.png` | 封面/截图（**tiny-scraper 已产出**） | FC/Imgs 1763 张 |
| `/mnt/sdcard/Roms/` | SD2（当前为空，需按 tiny-scraper 逻辑自动探测） | 空 |
| `gamelist.xml` | **不存在** → 由本前端按 ES-DE 格式创建（见 §6.4） | — |
| `video/` `logo/` | **尚不存在** → 由本设计定义的新目录（见 §6.3） | 待建 |

**视频解码实测**（新增）

| 项目 | 结果 |
|---|---|
| `ffmpeg` / `ffprobe` / `ffplay` | ✅ 4.4.4（GPL+nonfree，含 libx264/libx265/alsa/libdrm） |
| 硬件解码 | ❌ 无 `/dev/video*`，ffmpeg 未编入 rkmpp → **仅软件解码** |
| SW 解码 288×216@15fps | 24s 素材 **4.5s**（≈5.3× 实时，≈**19% 单核**）→ **可用** |

### 2.4 游戏启动入口（已全部实测确认）

```bash
# ① RetroArch（主力，覆盖绝大多数平台）
/mnt/mod/ctrl/RA_launch.sh  <core.so>  <rom绝对路径>  [auto]
#   内部展开为：/mnt/vendor/deep/retro/retroarch -c $RACONFIG -L /mnt/vendor/deep/retro/cores/<core.so> "<rom>"

# ② 原厂等价路径（无魔改时）
cd /oem/retro && ./retroarch -c /.config/retroarch/retroarch.cfg -L /oem/retro/cores/<core.so> "<rom>"

# ③ 独立模拟器
NDS     : /mnt/vendor/ctrl/setNDS64.sh run "<rom>"        # drastic + ndsCtrl.dge（双屏）
PSP     : /mnt/vendor/deep/ppsspp/run_gles.sh "<rom>"
SATURN  : /mnt/vendor/deep/saturn/launch.sh HLE "<rom>"
DC/NAOMI: /mnt/vendor/deep/flycast/launch.sh "<rom>"
OPENBOR : /mnt/vendor/deep/openBOR/scripts/openbor.sh "<rom>"
PICO8   : /mnt/vendor/deep/pico-8/launch.sh "<rom>"
PORTS   : 直接执行 /mnt/mmc/Roms/PORTS/<name>.sh
```

### 2.5 副屏（下屏）官方机制

```bash
/mnt/vendor/subscreen/launch.sh <资源目录> <资源名> <ForceLcdPos>
#   → echo 1 > /sys/class/anbernic_misc/tpctrl
#   → echo 1 > /sys/class/anbernic_misc/runapp
#   → ./subscreen.dge <dir> <name> <pos>
# 资源目录：/mnt/vendor/subscreen/{default,game,retro,apk}
#   retro/ = 每平台一张 png（a2600.png…）  game/nds.jpg  default/bk.jpg
```
**结论**：原厂副屏是**独立进程直写 DSI-2**（不走 Weston）。我们有两种选择，见 §4.3。

---

## 3. 总体架构

```
┌───────────────────────────────────────────────────────────────┐
│                        retrostation.sh                        │
│                   （APPS 入口 · 环境探测 · 守护重启）              │
└───────────────────────────┬───────────────────────────────────┘
                            │  启动 python3 -u main.py
┌───────────────────────────▼───────────────────────────────────┐
│                     src/retrostation/main.py                  │
│   初始化 → 恢复现场 → 后台扫描 → 主循环(input→state→draw) → 退出   │
└───┬──────────────┬──────────────┬──────────────┬──────────────┘
    │              │              │              │
┌───▼───┐     ┌────▼────┐   ┌─────▼─────┐  ┌─────▼─────┐
│ core/ │     │  data/  │   │    ui/    │  │ launcher/ │
├───────┤     ├─────────┤   ├───────────┤  ├───────────┤
│display│     │ systems │   │   app.py  │  │  launch   │
│ input │     │ scanner │   │  widgets  │  │ subscreen │
│config │     │  media  │   │ screens/  │  └───────────┘
│ i18n  │     │metadata │   │  theme    │
│ theme │     │favorites│   └───────────┘
└───────┘     └─────────┘
   SDL2 ctypes + PIL 离屏绘制（与 tiny-scraper 同构，已验证稳定）
```

### 3.1 目录结构

```
d:/code/Retrostation/
├── retrostation.sh              # 设备端启动脚本（放入 /mnt/mmc/Roms/APPS/）
├── README.md
├── docs/
│   ├── DESIGN.md                # 本文档
│   └── PROTOTYPE.md             # UI 原型说明 + 交互稿
├── prototype/                   # 浏览器可交互原型（先于真机验证交互与视觉）
│   ├── index.html
│   ├── css/style.css
│   └── js/{data.js,app.js}
└── src/retrostation/
    ├── main.py
    ├── core/{display,input,config,i18n,theme,hw}.py
    ├── core/
    │   ├── model.py             # ★ 统一 Game 模型（与数据源无关，Android 直接复用）
    │   └── {display,input,config,i18n,theme,hw}.py
    ├── data/
    │   ├── systems.py           # 平台表：目录/扩展名/核心/媒体目录/中文名
    │   ├── scanner.py           # ROM 扫描 + index.json 索引缓存（遍历走 platform.list_dir）
    │   ├── sources/             # ★★ 数据源插件层
    │   │   ├── base.py          #    MetadataSource 抽象 + 合并/写回策略
    │   │   ├── esde.py          #    gamelist.xml（可写，主写源）
    │   │   └── pegasus.py       #    metadata.pegasus.txt（只读，预留）
    │   ├── media.py             # ★ 封面/视频/Logo 探测 + 缩略图缓存 + LRU
    │   └── video.py             # ★ ffmpeg 管道解码 + 解码线程 + 帧队列
    ├── ui/{app,widgets,theme}.py
    ├── ui/screens/
    │   ├── home.py              # 平台页（卡片轮播）
    │   ├── game_list.py         # 列表 + 网格视图
    │   ├── game_carousel.py     # ★ 封面轮播视图（第三视图）
    │   ├── bottom.py            # ★ 副屏 MediaView（视频/封面/Logo）+ 元数据
    │   └── menu.py              # 系统菜单 / 设置
    ├── launcher/{launch,subscreen}.py
    └── platform/                # ★★ 平台适配层（唯一含平台代码的地方，见 §17）
        ├── base.py              #    Platform / Canvas 抽象接口
        └── linux/               #    SDL2 + PIL + evdev + ffmpeg
```

---

## 4. 双屏模型（核心设计）

### 4.1 屏幕角色

| 屏幕 | 角色 | 内容 |
|---|---|---|
| **主屏 DSI-1（上）** | 交互主控 | 平台轮播 → 游戏**列表 / 网格 / 封面轮播** → 设置菜单 → 确认弹窗 |
| **副屏 DSI-2（下）** | 上下文展示 | **媒体区（有视频播视频，无则封面）** + Logo 条 + 元数据卡（评分/发布/简介）+ 情境按键提示 |

**设计原则**：副屏**永远跟随主屏的"选中项"**，而不是跟随"当前页面"。
即：在平台页 → 副屏展示该平台；在游戏列表 → 副屏展示当前高亮游戏；滚动时副屏**节流刷新**（见 §9.2）。

### 4.2 三种屏幕模式（`config.screen_mode`）

| 模式 | 行为 |
|---|---|
| `auto`（默认） | 启动调 `SDL_GetNumVideoDisplays()`，≥2 → dual；否则 single |
| `dual` | 强制双窗口（双屏设备） |
| `single` | 只建 1 个窗口，副屏内容以**下分区**形式并入主屏 |

HDMI 接入时 Weston 可能只暴露 1 个 output → 自动降级 single，无需用户干预。

### 4.3 副屏实现方案对比与选择

| 方案 | 做法 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| **A. SDL 双全屏窗口**（tiny-scraper 方案） | 上屏 window@display0 + 下屏 window@display1 | 已验证可用；纯用户态；可画任意内容；可接触摸 | 依赖 Weston 暴露 2 output | ✅ **采用** |
| B. subscreen.dge 托管 | 调原厂二进制贴静态图 | 最稳、不吃 CPU | 只能显示**预置静态图**，无法画动态元数据 | ❌ 仅作兜底 |
| C. KMS/DRM 直写 | 自己开 DRM plane | 不依赖合成器 | 与 Weston 抢 CRTC，易黑屏 | ❌ 不用 |

### 4.4 双屏初始化（关键踩坑，全部来自真机）

```python
# core/display.py（要点）
def init(hw_info, mode="auto"):
    # ① 一次性加载 SDL，且 **绝不创建-销毁-重建窗口** —— Wayland 下会崩
    _bare_init_sdl()                       # 只 SDL_Init + TTF_Init

    SDL_SetHint(b"SDL_VIDEO_MINIMIZE_ON_FOCUS_LOSS", b"0")

    n = libSDL2.SDL_GetNumVideoDisplays()  # 判定双屏
    dual = (mode == "dual") or (mode == "auto" and n >= 2)

    # ② 上屏：FULLSCREEN_DESKTOP，居中于 display 0
    window = SDL_CreateWindow(b"Retrostation", CENTERED(0), CENTERED(0),
                              W, H, SDL_WINDOW_FULLSCREEN_DESKTOP)
    renderer = SDL_CreateRenderer(window, -1, SDL_RENDERER_SOFTWARE)

    # ③ **延迟 500ms 再建下屏**（tiny-scraper 实测：立即建会失败/遮挡）
    if dual:
        SDL_Delay(500)
        window2 = SDL_CreateWindow(b"Retrostation Bottom", CENTERED(1), CENTERED(1),
                                   W, H, SDL_WINDOW_FULLSCREEN_DESKTOP)
        renderer2 = SDL_CreateRenderer(window2, -1, SDL_RENDERER_SOFTWARE)
```

**必须遵守的 4 条规则**

1. **不在进程内重建窗口**：游戏退出后不试图 `SDL_Quit()` 再 `SDL_Init()`（Wayland 下崩溃/黑屏）。改用 **§8.2 的"守护重启"**。
2. **渲染后必须 `SDL_DestroyTexture`**：否则每次 paint 泄漏一张 640×480 纹理，几分钟就 OOM。
3. **渲染器固定 `SDL_RENDERER_SOFTWARE`**：rk3568 上 GPU 合成与 Weston 冲突，软件渲染 640×480 足够（tiny-scraper 已验证）。
4. **下屏窗口创建失败要能静默降级**为 single，不能抛异常。

### 4.5 渲染管线

```
PIL 离屏画布（RGBA 640×480）
   ↓   widgets 绘制（draw_text / 圆角矩形 / 贴图）
activeImage / bottomImage
   ↓   tobytes() → SDL_CreateRGBSurfaceFrom → CreateTextureFromSurface
   ↓   RenderClear → RenderCopy → RenderPresent → DestroyTexture
上屏 / 下屏
```

- 掩码固定 `rmask=0x000000FF, gmask=0x0000FF00, bmask=0x00FF0000, amask=0xFF000000`（**RGBA 字节序**，与 PNG 直读一致，实测正确）
- 每帧只 paint **发生变化**的屏幕（脏标记 `dirty_top` / `dirty_bottom`）

---

## 5. 输入系统

### 5.1 为什么不用 tiny-scraper 的 `input.py`

现有实现是 `while True: read(24)` **阻塞读到第一个事件就 return**，问题：

- ❌ 阻塞 → 无法做动画/进度条/时钟刷新
- ❌ 无按键重复 → 500 个游戏翻页要按 500 次
- ❌ 无长按语义
- ❌ 只认 `mapping` 里的硬编码 code，换机型要改代码

### 5.2 新设计

```
/dev/input/event4 (evdev)  ──线程──▶  struct 解析 ─▶ 语义映射 ─▶ queue.Queue
/dev/input/event1 (触摸)   ──可选──▶                              │
                                                                 ▼
                               主循环非阻塞 get_nowait() ──▶ UI 消费
```

**事件语义**

| 语义 | 触发 |
|---|---|
| `PRESS` | key/btn value==1 |
| `RELEASE` | value==0 |
| `REPEAT` | 按住 400ms 后，每 **80ms** 一次（列表滚动） |
| `LONG_PRESS` | 按住 **800ms** 且不移动（如 START 长按 = 快捷菜单） |

**键位映射（RG DS）**

| 键 | 平台页 | 游戏列表 | 网格 | 设置 |
|---|---|---|---|---|
| 上 / 下 | 切平台（渲染为纵向时） | 上/下一个（支持 REPEAT） | 上/下一行 | 上/下一项 |
| 左 / 右 | 上/下一个平台 | 翻页 ±10 | 左/右一列 | 调值 |
| L1 / R1 | 上/下一个平台 | 翻页 ±10 | 翻页 ±9 | — |
| L2 / R2 | 跳到首/末平台 | 跳到首/末游戏 | 同上 | — |
| **A** | 进入平台 | **启动游戏** | 启动 | 确认/进入 |
| **B** | — | 返回平台页 | 返回 | 返回/取消 |
| **Y** | 切换排序 | **收藏/取消收藏** | 收藏 | 重置默认 |
| **X** | — | **列表 → 网格 → 轮播**（三态循环） | 同左 | — |
| **SELECT** | 打开"全部/收藏/最近"筛选 | 同左 | 同左 | — |
| **START** | 系统菜单 | 系统菜单 | 系统菜单 | 保存返回 |
| **MENUF** | 退出确认（长按 1s 直接退出） | 同 | 同 | 同 |

> **说明**：RG DS 只有 A/B/X/Y/L1/R1/L2/R2/START/SELECT/MENU/十字键，**没有多余的按键**给"副屏媒体源切换"。
>
> **设计取舍 —— 界面不展示媒体类型**：
> - ❌ 不在列表/网格/轮播上加 ▶（有视频）/◆（有 Logo）之类的**媒体类型标记**
> - ❌ 不在下屏放"自动 / 视频 / 封面 / Logo"**媒体源页签**
> - ❌ 不在元数据区列"封面 ✓ / 视频 ✗ / Logo ✓"这类**状态勾选**
> - ✅ 下屏媒体区就一条规则：**有视频播视频，没视频放封面**，用户不需要知道媒体类型
> - ✅ 唯一入口是设置菜单里的 **`副屏视频` 开关**（想省电可关，关了就一直显示封面）

**触摸（下屏）**：`event1` 的 EV_ABS → 坐标映射到 640×480；用于：
- 点击副屏媒体区 / Logo 条 = 启动游戏
- 上下滑动 = 列表滚动
- 点击副屏底部快捷条（收藏 / 视图 / 返回）

> 触摸为**增强项**，全部功能都必须能用纯按键完成。

---

## 6. 数据层

### 6.1 平台表 `data/systems.py`

完全复用 tiny-scraper 的 `systems.py`（49 平台 + 扩展名 + screenscraper id），**新增字段**：

```python
{
  "name": "FC",                       # 目录名（不变）
  "label": {"zh_CN": "红白机", "en_US": "NES / Famicom"},
  "extensions": ["nes", "zip"],
  "core": "fceumm_libretro.so",       # 默认核心
  "alt_cores": ["nestopia_libretro.so", "quicknes_libretro.so"],  # 用户可切
  "standalone": None,                 # 或 {"cmd": ".../setNDS64.sh", "args": ["run", "{rom}"]}
  "media_dir": "Imgs",
  "aspect": "4:3",                    # 封面展示比例
  "art": "/mnt/vendor/subscreen/retro/fc.png",   # 复用原厂副屏素材
  "order": 16,
}
```

**核心映射（对照真机 `/oem/retro/cores/` 实测清单）**

| 平台 | 核心 |
|---|---|
| FC / FDS | `fceumm_libretro.so`（备 `nestopia` `quicknes`） |
| SFC | `snes9x2005_plus_libretro.so`（备 `snes9x2010` `snes9x`） |
| MD / SEGA32X | `picodrive_libretro.so`（备 `genesis_plus_gx`） |
| GB / GBC | `gambatte_libretro.so`（备 `sameboy` `gearboy`） |
| GBA | `mgba_libretro.so`（备 `vba_next` `meteor` 无） |
| NDS | **独立**：`setNDS64.sh run`（drastic） |
| PS | `pcsx_rearmed_libretro.so` |
| PSP | **独立**：`ppsspp/run_gles.sh` |
| N64 | `mupen64plus_next_libretro.so`（备 `parallel_n64`） |
| SATURN | **独立**：`saturn/launch.sh HLE` |
| DC / NAOMI / ATOMISWAVE | `flycast_libretro.so`（DC 亦可独立 `flycast/launch.sh`） |
| CPS1/2/3 / NEOGEO / FBNEO | `fbalpha2012_*` / `fbneo_libretro.so`（走 `ARCADE.csv` 自动核心） |
| MAME / HBMAME | `mame2003_plus_libretro.so` |
| PCE / PCECD | `mednafen_pce_fast` / `mednafen_pce` |
| WS / NGP / VB / LYNX / GG / SMS | `mednafen_wswan` / `mednafen_ngp` / `mednafen_vb` / `handy` / `gearsystem` / `smsplus` |
| DOS / SCUMMVM / EASYRPG | `dosbox_pure` / `scummvm` / `easyrpg` |
| PICO / PORTS / OPENBOR | 独立脚本 |

### 6.2 ROM 扫描与索引

```
首次启动： 后台线程全量扫描 → 写 index.json
后续启动： 读 index.json，仅对 mtime/size 变化的目录增量重扫
启动 0.3s 内先渲染"骨架屏 + 已缓存列表"，扫描完增量刷新
```

`index.json` 结构（按平台分文件，避免大文件重写）：

```json
{
  "version": 1,
  "root": "/mnt/mmc/Roms",
  "updated": 1780000000,
  "systems": {
    "FC": {"count": 515, "has_media": 498,
           "roms": [{"n":"超级马力欧兄弟","f":"超级马力欧兄弟.nes","s":40976,"m":1780000000,"c":1}]}
  }
}
```
（`c:1` = 有封面，避免启动时 stat 上千个 png）

### 6.3 媒体模型：封面 / 视频 / Logo

三类媒体**同级存放**，与 tiny-scraper 已产出的 `Imgs/` 完全共存：

```
/mnt/mmc/Roms/FC/
├── 超级马力欧兄弟.nes
├── Imgs/超级马力欧兄弟.png       # 封面  cover  （tiny-scraper 产出，路径不变）
├── video/超级马力欧兄弟.mp4      # 视频  video  （新增）
├── logo/超级马力欧兄弟.png       # Logo  logo   （新增，建议 RGBA 透明通道）
└── gamelist.xml                 # 元数据（新增，ES-DE 兼容，见 §6.4）
```

| 媒体 | 目录 | gamelist 元素 | 形态 | **用在哪** |
|---|---|---|---|---|
| 封面 `cover` | `Imgs/` | `<cover>`（读时兼容 `<image>` / `<thumbnail>`） | png/jpg，任意比例，等比 contain | **网格卡、轮播卡、副屏媒体区**（无视频时） |
| 视频 `video` | `video/` | `<video>` | mp4 / webm / mkv，优先 H.264；**静音循环**，288×216@15fps 解码 | **副屏媒体区**（有则播） |
| Logo `logo` | `logo/` | `<marquee>`（读时兼容 `<wheel>`） | 建议 RGBA png，宽幅（约 4:1）的 clear logo | **列表行左图**（84×30）、**轮播卡底部**、副屏 Logo 条 |

> **列表为什么用 Logo**：列表行是"横条 + 文字"，宽幅 Logo（约 4:1）比竖版封面更贴合 84×30 的槽位，
> 且同一屏能快速扫读品牌标识。无 Logo 时**静默回退封面**，不做任何提示。

**探测顺序**（三级，任一命中即停）

1. `gamelist.xml` 里的显式路径（相对 ROM 目录解析，兼容 `./` 前缀与绝对路径）
2. 目录约定默认路径：`<媒体目录>/<ROM 主文件名>.{png,jpg,mp4,webm,mkv}`
3. 都没有 → 生成程序化占位图（平台色渐变 + 首字），**不报错**

> 媒体类型别名可配（`config.media_dirs`），默认 `{cover:"Imgs", video:"video", logo:"logo"}`。

### 6.4 元数据：gamelist.xml（ES-DE 兼容）

**当前唯一「可写」的元数据载体就是 `gamelist.xml`**，不另建 `metadata.json` —— 收藏、游玩次数、上次游玩全部写回 gamelist，与 ES-DE / Batocera / Retrobat 互通。

> 本节是 **§6.8 数据源插件层里「ES-DE 这一个源」的具体实现**。
> 未来接入 Pegasus 等只读源时，`ui/`、扫描器、启动器都不用改，
> 只需新增一个 `data/sources/<name>.py` 并在注册表里加一行。

**查找优先级**

1. `<rom_root>/<SYS>/gamelist.xml`　← **写入目标**（ROM 目录内，多数前端的写法）
2. `<rom_root>/<SYS>/media/gamelist.xml`（ES-DE 变体，只读）
3. `~/.emulationstation/gamelists/<SYS>/gamelist.xml`（ES-DE 默认位置，只读兜底）
4. 都不存在 → 用文件名生成内存条目，**首次退出时创建** ①

**字段支持表**

| 元素 | 类型 | 说明 |
|---|---|---|
| `path` | str | ROM 相对路径（`./xxx.nes`），**作为主键** |
| `name` / `sortname` | str | 显示名 / 排序名（缺省 = 文件名去扩展名、去 `[...]` 标签） |
| `desc` | str | 简介 |
| `rating` | 0–1 float | 星级（显示时 ×5 四舍五入） |
| `releasedate` | `YYYYMMDDThhmmss` | 兼容纯日期 `1985-09-13` |
| `developer` / `publisher` | str | 开发 / 发行 |
| `genre` | str | 类型 |
| `players` | str | 人数（`1-2`） |
| `playcount` | int | 游玩次数（**前端维护**） |
| `lastplayed` | `YYYYMMDDThhmmss` | 上次游玩（**前端维护**） |
| `favorite` / `completed` / `hidden` / `kidgame` / `broken` | bool | `true` / `false`（`favorite` **前端维护**；`hidden` 参与过滤） |
| `cover` / `image` / `thumbnail` | str | 封面路径 |
| `video` | str | 视频路径 |
| `marquee` / `wheel` | str | Logo 路径 |
| `screenshot` / `titleshot` / `fanart` / `box` / `3dbox` / `miximage` / `manual` / `cartridge` | str | **解析并保留，暂不主动展示**（为后续扩展留位） |
| `id` / `source` / `hash` / `region` / `lang` | str | 刮削来源，原样透传不改写 |

**写入策略（关键，避免破坏用户既有数据）**

- **未知元素原样保留**：解析每个 `<game>` 时缓存未识别的子元素与属性，写回时拼回去 → 不会丢 ES-DE / Skraper 写入的字段
- **只增量更新**：仅当 `favorite` / `playcount` / `lastplayed` / 媒体路径**发生变化**才写回
- **延迟合并落盘**：改动先在内存累积，在「退出 / 切换平台 / 空闲 10s / 启动游戏前」统一写；避免连按收藏反复重写上千条
- **原子写 + 备份**：写 `gamelist.xml.tmp` → `os.replace()`；写前 `cp` 一份 `gamelist.xml.bak`（只保留一份）
- **并发安全**：`fcntl.flock` 独占锁；单进程写
- **编码格式**：UTF-8，`<?xml version="1.0"?>` + `<gameList>` 根，2 空格缩进，与 ES-DE 输出一致

**前端自动生成的最小条目**

```xml
<?xml version="1.0"?>
<gameList>
  <game>
    <path>./超级马力欧兄弟.nes</path>
    <name>超级马力欧兄弟</name>
    <cover>./Imgs/超级马力欧兄弟.png</cover>
    <marquee>./logo/超级马力欧兄弟.png</marquee>
    <video>./video/超级马力欧兄弟.mp4</video>
    <playcount>23</playcount>
    <lastplayed>20260820T193000</lastplayed>
    <favorite>true</favorite>
  </game>
</gameList>
```

**不内建刮削**：封面由 tiny-scraper 产出，本前端只做 **读取 · 跳转 · 写回游玩状态**
（设置页提供「跳转到 Tiny Scraper 补封面 / 补视频」入口，退出后由守护脚本拉起）。

### 6.5 视频播放子系统（真机实测可行）

**实测数据**（2026-08-28，rk3568，ffmpeg 4.4.4）

| 项目 | 结果 |
|---|---|
| `ffmpeg` / `ffplay` / `ffprobe` | ✅ 均存在，4.4.4（GPL+nonfree，含 libx264/libx265/alsa/libdrm） |
| 硬件解码 | ❌ **不可用** —— 无 `/dev/video*`（V4L2 m2m 无设备），ffmpeg 未编入 `rkmpp` |
| SW 解码 → 288×216 @15fps | 24s 素材耗时 **4.5s**（≈ **5.3× 实时**）→ 约 **19% 单核** |
| SW 解码 → 240×180 @12fps | 24s 素材耗时 **3.6s**（≈ **6.6× 实时**）→ 约 **15% 单核** |

**结论：软件解码完全够用**，副屏 288×216@15fps 视频可长期播放。

**管道设计**

```python
CMD = ["ffmpeg", "-hide_banner", "-loglevel", "error",
       "-stream_loop", "-1",                                  # 无限循环
       "-i", str(video_path),
       "-an",                                                  # 丢弃音频
       "-vf", "scale=288:216:flags=fast_bilinear,fps=15",
       "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
proc = subprocess.Popen(CMD, stdout=PIPE, stderr=DEVNULL, bufsize=FRAME * 4)
```

- 解码线程循环 `proc.stdout.read(FRAME)`（FRAME = 288×216×3），`Image.frombytes("RGB", (288,216))` 后放入 `queue(maxsize=3)`
- 主循环每视频帧从队列**只取最新一帧**（丢弃积压），避免延迟累积
- 视频固定 **15fps**，与主屏渲染解耦（主屏仍走脏标记）
- **切换选中项**：`proc.terminate()` → 起新进程；带 **250ms 防抖**，快速滚动时不反复重启 ffmpeg
- **失败静默降级**：视频不存在 / 解码报错 / ffmpeg 缺失 → 直接显示封面，不弹错误

设置项 **`bottom_video`**（布尔，默认 `true`）：

| 取值 | 行为 |
|---|---|
| `true`（默认） | 有视频则播，无则显示封面 |
| `false` | 跳过视频，始终显示封面（省电 / 省 CPU；单屏模式强制此项） |

### 6.6 媒体加载与缓存

| 层 | 策略 |
|---|---|
| 磁盘缩略图 | 首次生成，子目录以 `.` 开头不被 ROM 扫描命中：<br>· `Imgs/.cache/<name>.jpg` → 网格 132×108 / 轮播 196×272 / 副屏 336×264<br>· `logo/.cache/<name>.png` → 列表左图 80×26（**保留 alpha 通道**，png 格式）<br>· `video/.cache/<name>.jpg` → 视频首帧，解码启动前作占位（消除切项黑屏） |
| 视频首帧 | `<SYS>/video/.cache/<name>.jpg`，作为视频解码启动前的**占位帧**（消除切项黑屏） |
| 内存 LRU | 主屏 ~30 张缩略图 + 副屏 3 张大图 + Logo 10 张；`OrderedDict` 实现 |
| 预取 | 选中项变化时，预取**前后各 5 项**的缩略图 + **下一项**的视频首帧（工作线程） |
| 缺媒体 | 程序化占位图（平台色渐变 + 首字），不报错 |

### 6.7 收藏 / 最近游玩 / 现场恢复

- **收藏 / 游玩次数 / 上次游玩** 属于"前端状态"，写回**主写数据源**（默认 ES-DE，见 §6.8）
- **最近游玩**：按 `<lastplayed>` 排序取前 30，不额外存文件
- **现场恢复**：`~/.retrostation/state.json`（**只存界面状态**，不存业务数据）

```json
{
  "resume": {"view":"games","system":"FC","index":137,"layout":"carousel"},
  "last_system": "FC"
}
```

> `state.json` 只负责"回到哪儿"。当且仅当**没有任何可写数据源**时，才回退到 sidecar
> `<SYS>/.retrostation/state.json` 保存收藏与游玩数据（见 §6.8 写回策略）。

---

### 6.8 数据源插件架构（可插拔，当前 ES-DE，预留 Pegasus）

> §6.4 描述的是 **ES-DE 这一个数据源的实现**。本节定义**它之上的抽象层**，
> 让新增 Pegasus / 其他格式时**只加一个文件，不动 UI 与扫描器**。

### 6.8.1 为什么需要抽象

| 现状 | 风险 |
|---|---|
| 元数据直接读写 `gamelist.xml` | 换 Pegasus 要改 data/ 与 ui/ 多处 |
| 字段语义散落（rating 0–1、日期格式） | 换源就要全链路改一遍 |
| 没有统一模型 | Android 端重写时无法对齐 |

**目标**：UI 只认**内部统一模型 `Game`**，永远不知道底层是 XML 还是 `metadata.pegasus.txt`。

### 6.8.2 内部统一模型（source-agnostic）

```python
# core/model.py —— 纯 dataclass，无任何平台/格式依赖
@dataclass
class Game:
    key: str                      # 稳定主键 f"{system}/{rom_filename}"
    path: Path                    # ROM 绝对路径
    name: str
    sortname: str | None = None
    summary: str = ""             # 单行短简介
    description: str = ""         # 长描述（多行）
    rating: float | None = None   # ★ 归一化到 0.0–1.0
    release: date | None = None   # 支持 YYYY / YYYY-MM / YYYY-MM-DD
    developer: str | None = None
    publisher: str | None = None
    genres: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    players: str | None = None

    # —— 前端维护的状态 ——
    favorite: bool = False
    play_count: int = 0
    last_played: datetime | None = None
    completed: bool = False
    hidden: bool = False

    # —— 媒体：统一 key，值为绝对路径或 None ——
    assets: dict[str, Path | None] = field(default_factory=dict)
    # 可用 key: cover | logo | screenshot | video | fanart | manual | ...

    # —— 溯源与透传 ——
    sources: dict[str, str] = field(default_factory=dict)  # {"esde": ..., "pegasus": ...}
    extra: dict = field(default_factory=dict)              # 未识别字段，原样保留不丢
```

> `extra` 是保命设计：任何源里我们还不认识的字段都塞进去，写回时原样拼回，
> **不会因为换前端就丢失用户用 Skraper / Pegasus 刮削的数据**。

### 6.8.3 数据源插件接口

```python
# data/sources/base.py
class MetadataSource(abc.ABC):
    name: str          # "esde" | "pegasus" | ...
    display_name: str  # 设置页显示名
    writable: bool     # 是否支持写回
    priority: int      # 小者优先（默认 esde=10, pegasus=20）

    @abc.abstractmethod
    def detect(self, system_dir: Path) -> bool:
        """该平台目录是否存在此源的元数据文件"""

    @abc.abstractmethod
    def load(self, system_dir: Path, roms: list[Path]) -> dict[str, RawEntry]:
        """返回 {rom_key: RawEntry}；RawEntry 为该源的原始字段字典"""

    def save(self, system_dir: Path, entries: dict[str, RawEntry]) -> None:
        """写回。writable=False 的源直接抛 UnsupportedWrite"""
        raise UnsupportedWrite(self.name)

    @abc.abstractmethod
    def to_model(self, raw: RawEntry) -> Game:
        """原始字段 → 内部模型（含 rating/日期/多值 的归一化）"""

    @abc.abstractmethod
    def from_model(self, g: Game, prev: RawEntry | None) -> RawEntry:
        """内部模型 → 原始字段（写回用，未变更字段原样保留）"""
```

```python
# data/sources/__init__.py —— 注册表，新增源只改这一行
SOURCES: list[MetadataSource] = [
    ESDESource(),       # priority 10，writable=True
    PegasusSource(),    # priority 20，writable=False
]
```

### 6.8.4 合并策略（多源共存时）

```
① 按 priority 升序依次 load()
② 字段级合并：first-wins（先加载的源优先），空值才被后者填补
     → ES-DE 有的字段用 ES-DE，没有的用 Pegasus 补
③ 例外：favorite / play_count / last_played 这类"前端状态"用
     last-modified-wins（比各源文件的 mtime + 条目内时间戳）
④ assets 独立合并：每个 asset key 单独走一遍上面的规则
⑤ 目录约定探测（§6.3）作为最后兜底，优先级最低
```

**写回策略**

| 场景 | 行为 |
|---|---|
| 只有 ES-DE | 写 `gamelist.xml` |
| ES-DE + Pegasus 共存 | 写 **ES-DE**（主写源）；Pegasus 只读，其独有字段经 `extra` 保住 |
| 只有 Pegasus | Pegasus 不支持收藏/游玩数据 → 写 sidecar `<SYS>/.retrostation/state.json` |
| 一个都没有 | 首次退出时**按配置的主写源格式创建**（默认 ES-DE） |

> 主写源可通过 `config.metadata.primary_write_source` 指定。

### 6.8.5 Pegasus 格式要点与映射

**文件**：`metadata.pegasus.txt`（collection 目录内，或任意被扫描到的位置）

**语法要点**（实现时必须处理的 3 个坑）

1. 键值小写、大小写不敏感：`game:` / `Game:` 等价
2. **多行值**：续行必须以**空格或 TAB 开头**；单独一个 `.` 表示在多行值中插入空行
3. `rating: 80%` 是**百分比**，`release: 1985-09-13` 是 **ISO 日期**（可只写 `1985`）

```pegasus
collection: NES
extension: nes
directory: /mnt/mmc/Roms/FC

game: 超级马力欧兄弟
file: 超级马力欧兄弟.nes
developer: 任天堂
publisher: 任天堂
genre: 平台跳跃
release: 1985-09-13
players: 1-2
rating: 80%
summary: 横版卷轴平台跳跃的开山之作。
description: 由宫本茂设计的经典作品，
 定义了横版平台跳跃这一游戏类型。
 .
 至今仍是游戏设计教材级范例。
assets.boxFront: Imgs/超级马力欧兄弟.png
assets.marquee: logo/超级马力欧兄弟.png
assets.video: video/超级马力欧兄弟.mp4
```

**字段映射表**

| 内部模型 | ES-DE（XML） | Pegasus（txt） | 备注 |
|---|---|---|---|
| `name` | `<name>` | `game` | |
| `sortname` | `<sortname>` | — | Pegasus 无 |
| `summary` | `<desc>` 首行 | `summary` | |
| `description` | `<desc>` | `description` | 多行，注意续行规则 |
| `rating` | `<rating>` `0–1` | `rating` `0–100%` | **归一化到 0–1** |
| `release` | `<releasedate>` `19850913T000000` | `release` `1985-09-13` | 都允许不完整 |
| `developer` | `<developer>` | `developer` | |
| `publisher` | `<publisher>` | `publisher` | |
| `genres` | `<genre>`（单值） | `genre`（`,` 分隔多值） | 统一成 `list[str]` |
| `tags` | — | `tags`（`,` 分隔） | ES-DE 无 |
| `players` | `<players>` | `players` | |
| `favorite` | `<favorite>` | ❌ **不支持** | 只能写 ES-DE / sidecar |
| `play_count` | `<playcount>` | ❌ **不支持** | 同上 |
| `last_played` | `<lastplayed>` | ❌ **不支持** | 同上 |
| `completed` / `hidden` | `<completed>` / `<hidden>` | ❌ | |
| `assets.cover` | `<cover>` / `<image>` / `<thumbnail>` | `assets.boxFront` | |
| `assets.logo` | `<marquee>` / `<wheel>` | `assets.marquee` / `assets.wheel` | |
| `assets.screenshot` | `<screenshot>` / `<titleshot>` | `assets.screenshot` / `assets.titlescreen` | |
| `assets.video` | `<video>` | `assets.video` | |
| `assets.fanart` | `<fanart>` | `assets.fanart` | |
| `command`（启动覆盖） | — | `command` | 可选，用于覆盖平台默认启动命令 |

**媒体路径解析优先级**（新增源后统一为 4 级）

```
① 数据源里的显式路径（ES-DE <cover> / Pegasus assets.boxFront）
② 本前端目录约定：Imgs/ · video/ · logo/        ← 配置 media_dirs
③ 该格式的默认目录（ES-DE: media/covers 等；Pegasus: assets/box_front 等）
④ 程序化占位图
```

### 6.8.6 新增一个数据源要改哪些文件

```
新增 data/sources/pegasus.py         ← 实现 MetadataSource 的 5 个方法
修改 data/sources/__init__.py        ← SOURCES 列表加一行
修改 config.json 默认值（可选）       ← metadata.sources 顺序
─────────────────────────────────
ui/ 、core/ 、launcher/ 全部不用动     ← 这就是抽象的价值
```

---

## 7. UI 信息架构

```
┌── 平台页 Home ────────────┐
│  横向卡片轮播               │──A──▶┌── 游戏页 Games ─────────────────┐
│  · 全部游戏                │      │  X 键三态循环：                  │
│  · 收藏                    │      │   ① 列表  ② 网格  ③ 封面轮播    │──A──▶ 启动
│  · 最近游玩                │      │  排序：名称 / 最近 / 最多玩      │
│  · FC · SFC · GBA · …      │◀──B──│  筛选：全部 / 有封面 / 缺封面    │
└───────────────────────────┘      └─────────────────────────────────┘
              START ──▶ 系统菜单（视图 / 副屏媒体 / 刮削 / 主题 / 语言 / 关于 / 退出）
```

### 7.1 主屏布局规范（640×480）

```
┌──────────────────────────────────────────────────┐ 0
│ 状态栏 28px   12:04   WiFi  🔋87%   56℃   TF1     │
├──────────────────────────────────────────────────┤ 28
│ 页头 44px     ▍红白机 (FC)  515 个 · 轮播         │
├──────────────────────────────────────────────────┤ 72
│                                                  │
│ 内容区 378px（单屏模式 260px）                     │
│                                                  │
│ ① 列表：行高 34 × 9 行，左图 84×30 显示 Logo       │
│    ┌──────────────────────────────────────┐      │
│    │ [Logo 84×30] 超级马力欧兄弟  ★ 1/515 │ ←选中 │
│    └──────────────────────────────────────┘      │
│                                                  │
│ ② 网格：4 列 × 3 行（单屏 4×2），卡片间距 8        │
│                                                  │
│ ③ 封面轮播：中间卡 196×272（scale 1.0）            │
│    左右卡 scale 0.75 / opacity .42，可见 ±3 张     │
│    下方 56px：Logo（有则用 Logo 图，否则文字标题）  │
│                                                  │
├──────────────────────────────────────────────────┤ 450
│ 按钮条 30px  Ⓐ开始 Ⓑ返回 Ⓨ收藏 Ⓧ视图 Ⓢ筛选 Ⓜ菜单 │
└──────────────────────────────────────────────────┘ 480
```

**三种视图对比**

| | 列表 | 网格 | 封面轮播 |
|---|---|---|---|
| 一屏可见 | 9 个 | 12 个（单屏 8） | 1 主 + 4 侧 |
| 左图 / 卡片 | **Logo 84×30**（无则封面） | 封面 132×108 | 封面 196×272 + 底部 Logo |
| 导航 | ↑↓ ±1 | ↑↓ ±4 / ←→ ±1 | ←→ ±1 |
| 翻页 | ←→ ±10 / L1R1 ±10 | L1R1 ±12 | ↑↓ ±10 / L1R1 ±10 |
| 适用 | 快速扫描、认 Logo 认得快 | 视觉浏览、封面为主 | 沉浸挑选、看封面细节 |
| 副屏 | 同一套（视频优先） | 同一套 | 同一套 |

> 三视图**共用同一份选中索引 `gameIdx`**，切换视图时保持当前游戏不变。

### 7.2 副屏布局规范（640×480）

```
┌──────────────────────────────────────────────────────────────────────┐
│ 副屏标题 32px   [FC] 游戏详情                                         │
├──────────────────────────────────────────────────────────────────────┤
│ ┌────────────────────────────┐  ┌────────────────────────────────────┐│
│ │ 媒体区 336×264              │  │ 元数据区 264px                      ││
│ │                            │  │  红白机 FC · 任天堂                 ││
│ │   有视频 → 播视频           │  │  ★★★★☆  4.0                        ││
│ │   288×216@15fps 静音循环    │  │  ────────────────────────────────  ││
│ │   底部 3px 进度条           │  │  类型    平台跳跃                   ││
│ │                            │  │  人数    1-2 人                     ││
│ │   无视频 → 显示封面         │  │  发布    1985-09-13                ││
│ │   （contain 居中，不裁切）   │  │  核心    fceumm_libretro.so         ││
│ │                            │  │  ────────────────────────────────  ││
│ │  ✗ 不叠 Logo                │  │  简介                               ││
│ │  ✗ 不显示媒体类型徽标        │  │  经典名作，手感扎实，至今仍值得一   ││
│ │  ✗ 无媒体源切换页签          │  │  玩。（最多 4 行，超长自动省略）     ││
│ └────────────────────────────┘  │  ────────────────────────────────  ││
│ ┌────────────────────────────┐  │  [已玩 23 次]   [上次 3 天前]        ││
│ │ Logo 条 336×72              │  │  元数据来源：gamelist.xml（ES-DE）   ││
│ │ 有 Logo → Logo 图           │  └────────────────────────────────────┘│
│ │ 无 Logo → 游戏名            │                                        │
│ └────────────────────────────┘                                        │
├──────────────────────────────────────────────────────────────────────┤
│ 情境提示 44px  Ⓐ 开始游戏  Ⓑ 返回  Ⓨ 收藏  Ⓧ 切换视图                  │
└──────────────────────────────────────────────────────────────────────┘
```

**媒体区降级链**（无手动切换入口，无类型标记）

```
bottom_video = true（默认）
   ├─ video 存在 ──▶ 播放视频（解码失败则下一级）
   ├─ cover 存在 ──▶ 显示封面
   ├─ logo  存在 ──▶ 显示 Logo
   └─ 都没有    ──▶ 程序化占位图（平台色渐变 + 首字）

bottom_video = false  →  跳过视频，直接从封面开始（省电 / 省 CPU）
```

**元数据区字段说明**

| 区块 | 内容 | 来源 |
|---|---|---|
| 抬头 | 平台中文名 · 发行商 | 平台表 + `<publisher>` |
| **评分** | 五星 + 数值（`<rating>` × 5，保留 1 位小数） | `<rating>` |
| **发布时间** | `1985-09-13`（`<releasedate>` 前 8 位；只有年份则显示 `1985`） | `<releasedate>` |
| kv | 类型 / 人数 / 核心 | `<genre>` `<players>` 平台表 |
| **简介** | **多行，最多 4 行**，超长末行加 `…` | `<desc>` |
| 统计 | 已玩次数 / 上次游玩 | `<playcount>` `<lastplayed>` |
| 脚注 | 元数据来源 | — |

> **`desc` 多行折行的真机实现要点**：中文没有空格，不能按空格分词。
> 用 `font.getlength()` **逐字累加宽度**，超过列宽（264 − 内边距）就断行，逐行累加高度，
> 超过 4 行就在末尾追加 `…` 并截断。伪代码：
>
> ```python
> def wrap_text(draw, text, font, max_w, max_lines):
>     lines, cur = [], ""
>     for ch in text:
>         if draw.textlength(cur + ch, font=font) > max_w:
>             lines.append(cur); cur = ch
>             if len(lines) == max_lines:
>                 lines[-1] = lines[-1][:-1] + "…"
>                 return lines
>         else:
>             cur += ch
>     if cur: lines.append(cur)
>     return lines[:max_lines]
> ```

### 7.3 副屏内容矩阵

| 主屏状态 | 副屏媒体区 | 副屏元数据区 |
|---|---|---|
| 平台页 · 具体平台 | 平台艺术图（复用 `/mnt/vendor/subscreen/retro/*.png`） | 平台名 + 目录 / 核心 / 媒体目录 / ROM 数 / 封面覆盖率 / 简介 |
| 平台页 · 聚合（全部/收藏/最近） | 分类大图 + Top 6 封面拼贴 | 统计卡（总数 / 已有封面 / 收藏 / 平台数） |
| 游戏页 · 列表 / 网格 / 轮播 | **有视频播视频，无则显示封面**（不叠 Logo、无类型标记） | 平台·发行 / **评分** / kv（类型·人数·**发布**·核心）/ **简介 4 行** / 统计 / gamelist 来源 |
| 设置菜单 | 保持当前上下文（不打断） | 当前项说明 + 取值预览 |
| 确认弹窗 | 保持当前上下文 | 大号警示图标 + 说明 |
| 启动中 | 封面（**停视频**，省 CPU 让位模拟器） | 启动器 / 核心 / 路径 + "守护脚本恢复现场"说明 |
| 扫描中 | 封面拼贴 | 进度条 + 实时发现的平台数 |

**媒体在三处的分工**（一处媒体只出现在最合适的位置，不重复占位）

| 媒体 | 上屏列表 | 上屏网格 | 上屏轮播 | 下屏媒体区 |
|---|---|---|---|---|
| Logo | ✅ **行左图 84×30** | — | ✅ 卡底部横幅 | ✅ Logo 条 336×72 |
| 封面 | 回退项（无 Logo 时） | ✅ 卡片主体 | ✅ 卡片主体 | 回退项（无视频时） |
| 视频 | — | — | — | ✅ **优先播放** |

---

## 8. 启动器与进程管理

### 8.1 启动序列

```
① 保存现场  → state.json["resume"]
② 刷新 gamelist.xml（把 favorite / playcount / lastplayed 落盘）
③ **停掉副屏视频解码进程**（ffmpeg terminate + wait，避免残留占 CPU）
④ 写 /tmp/retrostation_launch.cmd（要执行的启动命令）
⑤ 关下屏窗口 → 关上屏窗口 → SDL_Quit()   （顺序不能反）
⑥ os.execv("/bin/bash", ["bash", "/…/run_game.sh"])   ← 直接让位，不 fork
```

> 注意 ③：**必须先杀 ffmpeg**。否则它会跟着 `execv` 一起被继承，在模拟器运行期间持续占用约 19% CPU。
> 同时在 `main.py` 注册 `atexit` + `SIGTERM` 处理器做兜底。

### 8.2 守护重启（**规避 Wayland 重建崩溃的关键**）

**不在同进程内重建 SDL 窗口**。改用 shell 自举：

```bash
# run_game.sh
source /tmp/retrostation_launch.cmd
"$@"                                   # 阻塞：跑 RetroArch / 独立模拟器
sync
# 游戏结束后，把屏幕交还前端
exec /mnt/mmc/Roms/APPS/retrostation.sh      # 重新拉起，读 resume 恢复现场
```

```bash
# retrostation.sh
while true; do
    python3 -u "$DIR/src/retrostation/main.py" "$DIR/config.json"
    code=$?
    [ -f /tmp/retrostation_launch.cmd ] || break   # 无待启动游戏 = 正常退出
    bash "$DIR/run_game.sh"                        # 启动并等待，结束后回到本循环
    rm -f /tmp/retrostation_launch.cmd
done
```

好处：
- 前端进程退出 → **内存完全释放**，模拟器拿到全部资源
- 游戏退出 → 干净的全新进程建窗口，**零重建风险**
- 天然支持"退出游戏回到原位"

### 8.3 副屏让位

启动游戏前若发现 `/mnt/vendor/subscreen/subscreen.dge` 未运行，可选择拉起（NDS 必需由 `setNDS64.sh` 处理）。
**默认策略**：前端退出后不再管副屏，交给模拟器/原厂逻辑。避免抢 `tpctrl` / `runapp`。

---

## 9. 性能预算

### 9.1 目标（rk3568 · 4×A55 · 软件渲染）

| 指标 | 目标 | 手段 |
|---|---|---|
| 冷启动到可交互 | < 1.5s | 索引缓存；先渲染骨架屏，后台扫描；gamelist 按平台懒加载 |
| 列表滚动帧率 | ≥ 30 fps | 只重绘脏区；行渲染 = 1 圆角矩形 + 1 文本 + 1 贴图 |
| **轮播视图帧率** | ≥ 30 fps | 复用磁盘预生成的 196×272 缩略图，PIL 缩放一次完成 |
| 切平台 | < 300ms | 索引 + 磁盘缩略图缓存 |
| 副屏刷新（静态） | 节流 ≤ 12 fps | 见 §9.2 |
| **副屏视频播放** | 288×216 @ 15fps | ffmpeg SW 解码，实测 ≈ **19% 单核**（4 核共约 5%） |
| **视频播放时主屏** | 仍 ≥ 30 fps | 解码在独立线程；主循环只取队列最新帧，不阻塞 |
| **视频切换响应** | < 350ms | 250ms 防抖 + 视频首帧占位图（不等解码） |
| 常驻内存 | < 130 MB | LRU 限 30 张缩略图 + 3 张副屏大图 + 10 张 Logo；纹理即用即毁；视频帧队列 maxsize=3 |
| 常驻 CPU（空闲） | < 3% | 空闲时 input 用 `select(timeout=0.2)`，不做忙轮询 |
| 常驻 CPU（播视频） | < 25%（单核计） | 见上；滚动/启动游戏时暂停解码 |

### 9.2 副屏节流

快速滚动时若每个游戏都重画副屏，会卡。策略：

```python
# 选中项变化 → 标记 pending_bottom
# 主循环末尾：距上次副屏绘制 ≥ 90ms 才真正渲染
# 停止滚动 250ms 后，强制渲染一次（保证最终一致）
```

---

## 10. 视觉规范

### 10.1 配色（沿用 tiny-scraper 橙色系，保证同一设备观感统一）

| 名称 | RGB | 用途 |
|---|---|---|
| `bg` | `#141414` | 屏幕底色 |
| `panel` | `#1C1C1E` | 卡片 / 行底 |
| `panel-2` | `#242426` | 选中行底 / 输入框 |
| `border` | `#333336` | 分隔线 / 卡片描边 |
| `accent` | `#E8A33D` | 主强调（选中、进度、A 键） |
| `accent-d1` | `#B87D22` | 强调暗部（渐变尾、按钮底） |
| `text` | `#F2F2F2` | 主文本 |
| `text-dim` | `#9A9A9E` | 次要文本 / 元数据 |
| `ok` | `#4CAF50` | 成功 / 有封面 |
| `warn` | `#FFC866` | 警告 / 重试 |
| `danger` | `#E05252` | 删除 / 退出确认 |

### 10.2 字体与字号（640×480 基准）

| 用途 | 字号 | 字重 |
|---|---|---|
| 状态栏 / 按钮条 | 13 | Regular |
| 列表行 | 16 | Regular |
| 页头标题 | 20 | Bold |
| 副屏大标题 | 22 | Bold |
| 副屏元数据 | 14 | Regular |
| 弹窗正文 | 16 | Regular |

字体链：`SourceHanSansCN-Regular.otf` → `DejaVuSans.ttf` → `ImageFont.load_default()`
**必须做字体缓存**（`{size: font}`），否则每帧 `truetype()` 会明显掉帧。

### 10.3 控件

| 控件 | 规格 |
|---|---|
| `ListRow` | 600×34，圆角 6，选中时橙色渐变 + 左侧 3px 竖条；**左图 84×30 显示 Logo**（无 Logo 回退封面） |
| `GridCard` | 132×112，封面 + 24px 名称条；选中放大 1.06 + 2px 橙边；右上角 ★ 表示收藏 |
| **`CoverCard`（轮播）** | **196×272**，选中 scale 1.0 / opacity 1；相邻 scale 0.75 / opacity 0.42；更远 0.62 / 0.10；选中卡 2px 橙边 + 阴影；底部叠 `LogoBanner`（无 Logo 则游戏名） |
| **`MediaView`（副屏媒体区）** | 336×264，圆角 8；**有视频播视频**（288×216@15fps，contain 居中，底部 3px 进度条），**无视频显示封面**。**不叠 Logo、不显示媒体类型徽标、无页签** |
| **`LogoBanner`** | 副屏 336×72（无 Logo 时降级为游戏名）；轮播卡底部 cardW−20 × 28；列表行左图 80×26 |
| `MetaPanel`（副屏元数据） | 264 宽：平台·发行 → **评分（★ + 数值）** → kv（类型 / 人数 / **发布** / 核心）→ **简介多行（≤4 行）** → 统计 → gamelist 来源 |
| `ButtonBar` | 圆键 ⌀20 + 标签；矩形键 60×24（START/SELECT/MENU） |
| `Dialog` | 420×160 居中，圆角 8，遮罩 60% 黑 |
| `Toast` | 底部浮动，2s 自动消失 |
| `ProgressBar` | 高 8，圆角 4，橙渐变 |
| `ScrollBar` | 宽 4，右侧，橙 60% 透明度 |

---

## 11. 单屏兼容方案

`screen_mode == "single"` 或检测到 1 个 display 时：

**方案 A（默认）· 上下分区**

```
640×480 主屏
├─ 状态栏 24
├─ 内容区 260（列表 ≈6 行 / 网格 4×2 / 轮播 150×208）
├─ 详情条 118（缩略图 132×98 + 4 行文本，含 Logo 与媒体状态）
└─ 按钮条 30
```

**方案 B · 左右分栏**（网格视图时用）
`左 420 网格(3×3) + 右 220 详情卡`

- 单屏下 **X 键** 在 A/B 方案间切换
- 副屏模块 `ui/screens/bottom.py` 输出的是**一个 PIL 图层 + 尺寸参数**，单屏时把它 `paste` 到主屏指定区域 → **零重复代码**

**单屏下的视频处理（重要）**

| 规则 | 说明 |
|---|---|
| **强制关视频** | `bottom_video` 在 single 模式强制 `false`；详情条（132×98）只显示封面 |
| 不提供开关 | 118px 的详情条放视频没有意义，且与列表滚动抢 CPU，索性不开放 |
| 滚动时无视频负担 | 因为本来就关，省去 `SIGSTOP/CONT` 的状态管理 |

---

## 12. 配置与持久化

`config.json`（首次运行生成，缺失字段回退默认）：

```json
{
  "screen_mode": "auto",
  "single_layout": "split_v",
  "rom_root": "auto",
  "layout": "list",
  "sort": "name",
  "filter": "all",
  "media_dirs": { "cover": "Imgs", "video": "video", "logo": "logo" },
  "bottom_video": true,
  "video_fps": 15,
  "video_size": [288, 216],
  "metadata": {
     "sources": ["esde", "pegasus"],
     "primary_write_source": "esde",
     "read_only": false,
     "backup": true,
     "sidecar_fallback": true
  },
  "language": "auto",
  "theme": "amber",
  "theme_variant": "dark",
  "show_status_bar": true,
  "bottom_refresh_ms": 90,
  "thumbnail_cache": true,
  "launcher": {
     "ra_script": "/mnt/mod/ctrl/RA_launch.sh",
     "cores_dir": "/mnt/vendor/deep/retro/cores",
     "fallback_ra": "/oem/retro/retroarch",
     "fallback_cores_dir": "/oem/retro/cores"
  },
  "core_overrides": { "FC": "nestopia_libretro.so" },
  "brightness": { "top": 140, "bottom": 140 }
}
```

**布局与数据源说明**

- `layout` 取值：`list` / `grid` / `carousel`（**三态**，`X` 键循环）
- `bottom_video`：`true`（默认，有视频播视频）/ `false`（只显示封面）
- 单屏模式下 `bottom_video` 默认被强制为 `false`（视频在 118px 详情条里没有意义且抢 CPU），
  设置项显示"单屏已停用"
- `metadata.sources`：启用的数据源及其优先级顺序（默认 `["esde", "pegasus"]`，合并规则见 §6.8.4）
- `metadata.primary_write_source`：主写源，收藏/游玩次数写回它（默认 `esde`）
- `metadata.sidecar_fallback`：无可用写源时，是否回退到 sidecar 文件（默认 `true`）

**文件路径**

| 文件 | 位置 | 说明 |
|---|---|---|
| `config.json` | `/mnt/mmc/Roms/APPS/Retrostation/config.json` | 随程序走，换卡不丢配置 |
| `state.json` | 同上 | 只存界面现场（resume / last_system） |
| `gamelist.xml` | `<rom_root>/<SYS>/gamelist.xml` | **业务数据**，与 ES-DE 互通 |
| `gamelist.xml.bak` | 同目录 | 一份备份 |
| `metadata.pegasus.txt` | `<rom_root>/<SYS>/` | Pegasus 源（**只读**） |
| `.retrostation/state.json` | `<SYS>/.retrostation/` | sidecar 兜底（仅当无可用写源时） |
| `index.json` | `~/.retrostation/index.json` | ROM 扫描索引缓存 |
| 缩略图缓存 | `<SYS>/Imgs/.cache/`、`video/.cache/` | 可安全删除，会自动重建 |

- 设置页可导出 / 导入 `config.json`

---

## 13. 复用 tiny-scraper 的清单

| 模块 | 复用方式 |
|---|---|
| `graphic.py` | **fork 重构**为 `core/display.py`：保留 ctypes/PIL/双屏初始化/掩码；加脏标记、纹理销毁、单屏模式 |
| `input.py` | **重写**为 `core/input.py`：线程 + 队列 + 重复/长按 |
| `systems.py` | **扩展**为 `data/systems.py`：加 core / label / art / aspect |
| `language.py` + `lang/*.json` | **直接复用**，扩键（新增菜单项词条） |
| `scraper.py` / `thumbnail_matcher.py` | **不内嵌**；设置页提供跳转入口 |
| `anbernic.py` | 改写为 `core/hw.py`：ROM 根探测（mmc/sdcard）、电量、背光、温度、WiFi |
| `app.py` 主循环 | 重写为状态机 |
| — | **新增** `data/sources/`：数据源插件层（`base.py` + `esde.py` + 未来的 `pegasus.py`） |
| — | **新增** `data/media.py`：封面/视频/Logo 探测 + 缩略图缓存 + LRU |
| — | **新增** `data/video.py`：ffmpeg 管道解码 + 解码线程 + 队列 |
| — | **新增** `ui/screens/game_carousel.py`：第三视图 |
| — | **新增** `platform/`：平台适配层（Linux / 未来 Android），见 §17 |

**外链工具依赖**：`ffmpeg`（视频解码，实测 4.4.4 已内置）。启动时探测一次，缺失则 `bottom_video` 强制降级为 `false` 并在设置页提示。

---

## 14. 风险与已知坑

| 风险 | 影响 | 对策 |
|---|---|---|
| Wayland 下窗口重建崩溃 | 退出游戏后黑屏 | §8.2 守护重启，**进程内绝不重建** |
| Weston 只暴露 1 output | 下屏黑 | `auto` 降级 single；提供 `dual` 强制覆盖 |
| 纹理泄漏 | 几分钟 OOM | 每次 paint 后 `SDL_DestroyTexture` |
| `Imgs/` 命名与 ROM 名不一致（大小写/特殊字符） | 缺封面 | 匹配时做 NFC 归一 + 大小写不敏感 + 去非法字符后再匹配 |
| 中文文件名编码 | 乱码/打不开 | 全链路 `os.fsencode` / `surrogateescape`；启动命令用 `subprocess` 列表传参（**不要 shell 字符串拼接**） |
| 冷启动扫描 40+ 平台慢 | 首屏卡 | 索引缓存 + 后台线程 + 骨架屏 |
| 背光节点序号不定 | 调错屏 | 启动时探测 `/sys/class/backlight/*`，按 `max_brightness` 合法性排序，配置里固化 |
| `RA_launch.sh`（魔改）可能不存在 | 无法启动 | 回退原厂 `./retroarch -c … -L …`；设置页可手填 |
| HDMI 插入后分辨率变化 | 布局错位 | 监听 `/sys/class/extcon/hdmi/state` 或 `SDL_WINDOWEVENT`，尺寸变化触发重新布局 |
| 封面过大（原图 1000+px） | 解码慢 | 磁盘缩略图缓存，原图**只在副屏大图用一次**并降采样 |
| **无硬件解码**（实测无 `/dev/video*`、ffmpeg 无 rkmpp） | 视频吃 CPU | 固定 288×216@15fps（实测 ≈19% 单核）；滚动/启动时暂停；单屏默认关 |
| **ffmpeg 子进程残留** | 退出后僵尸进程占 CPU | `atexit` + 信号处理里 `terminate()`；启动时先 `pkill -f` 旧管道 |
| 快速滚动导致 ffmpeg 反复启停 | 卡顿、句柄泄漏 | 选中项变化 **250ms 防抖**；只保留最后一个进程，`terminate` 后 `wait(timeout=1)` |
| 视频损坏 / 奇怪编码 | ffmpeg 卡死 | 管道读取带超时（3s 无帧即判定失败并降级到封面） |
| **gamelist.xml 被前端写坏** | 用户元数据丢失 | 解析时保留未知元素；写前备份 `.bak`；临时文件 + `os.replace` 原子替换；`flock` 独占锁 |
| gamelist 很大（515 条 × 40 平台） | 解析慢 | 按平台懒加载（只解析进入的平台）；`xml.etree.ElementTree` + `iterparse` |
| 单机多份 gamelist 冲突 | 数据不同步 | 明确优先级（§6.4），**只写 ROM 目录那一份**，其余只读 |
| Logo 图不是透明通道 | 叠在视频上难看 | 叠加前检测 alpha；无 alpha 则加 1px 深色描边 + 半透明底衬 |
| 轮播视图渲染 7 张 196×272 封面 | 掉帧 | 轮播用**预生成的 196×272 磁盘缩略图**；缩放由 PIL 一次完成，不做实时重采样 |

---

## 15. 里程碑

| 阶段 | 交付物 | 验收 |
|---|---|---|
**主线（Linux 双屏掌机）**

| 阶段 | 交付物 | 验收 |
|---|---|---|
| **M0 ✅** | 详细设计（三视图 / 三类媒体 / 数据源架构 / 副屏视频 / 跨平台预留）+ HTML 交互原型 | 本文档 + `prototype/` 可在浏览器跑通全流程 |
| M1 | `platform/base.py` + `core/`（display 双/单屏、input、config、hw、i18n）+ 空壳 UI | 真机显示双屏黑底 + 按键可退出 |
| M2 | `data/`（扫描、索引、**三类媒体探测**、缩略图缓存）+ 平台页 + 游戏**列表/网格/轮播** | 真机浏览 FC 515 个 ROM，三视图切换，滚动 ≥30fps |
| M3 | **`data/sources/`**（`base.py` + `esde.py`）+ 副屏联动 + 元数据卡 | 收藏/游玩次数写入 `gamelist.xml`，用 ES-DE 打开能看到 |
| M4 | **`data/video.py`**（ffmpeg 管道）+ 副屏 `MediaView`（视频/封面自动降级） | 副屏稳定播放 288×216@15fps；无视频自动回退封面；元数据区显示评分/发布/多行简介 |
| M5 | `launcher/`：RA + 独立模拟器 + 守护重启（**启动前停视频**） | 启动 → 玩 → 退出 → 回到原位 |
| M6 | 设置菜单、主题、语言、背光、**单屏兼容** | 强制 single 模式功能零缺失 |
| M7 | 打磨：动画、缺图占位、错误提示、性能 | 冷启动 <1.5s，常驻 <120MB，视频播放时主屏仍 ≥30fps |

**扩展线（主线 M3 之后，可并行）**

| 阶段 | 交付物 | 验收 |
|---|---|---|
| **E1** | `data/sources/pegasus.py` —— **Pegasus 数据源**（只读） | 只放 `metadata.pegasus.txt` 的目录能正常出封面/简介；与 ES-DE 共存时按 §6.8.4 合并 |
| **E2** | 尺寸 token 化（`ui/theme.py`）+ 布局比例化 | 把 `prototype` 缩放到任意分辨率都不破版（**Android 前置条件**） |
| **E3** | `platform/android/`（Chaquopy 宿主） | Android 上能扫描、浏览、看封面；单屏分区布局可用 |
| **E4** | Android 视频（MediaPlayer 硬解）+ 启动 Intent | 副屏位置播放 30fps；能拉起 RetroArch |

---

## 16. 附：真机验证清单（M1 前必做）

**已完成（2026-08-28 实测）** ✅

| # | 项目 | 结果 |
|---|---|---|
| 1 | `SDL_GetNumVideoDisplays()` | 待 M1 验证（tiny-scraper 已能开双窗口 ✅） |
| 2 | 副屏窗口可见 | ✅ tiny-scraper 已验证 |
| 3 | 触摸 `event1` 坐标范围 | 待验证（gt9xx-0 存在） |
| 4 | 背光 `backlight` / `backlight1` 对应哪块屏 | 待验证（两者均 0–255，当前 140） |
| 5 | 电量 `power_supply/battery/capacity` | ✅ 可读（100 / Full） |
| 6 | `RA_launch.sh` 是否存在（魔改固件） | ✅ 存在（`/mnt/mod/ctrl/RA_launch.sh`） |
| 7 | **ffmpeg 可用性** | ✅ **4.4.4**，含 ffprobe/ffplay |
| 8 | **视频硬件解码** | ❌ **无**（无 `/dev/video*`，无 rkmpp）→ 走软件解码 |
| 9 | **SW 解码性能** | ✅ 288×216@15fps ≈ **19% 单核** |
| 10 | 现有 `video/` `logo/` 目录 | ❌ 尚不存在 → 需按 §6.3 新建 |
| 11 | 现有 `gamelist.xml` | ❌ 尚不存在 → 由前端创建 |

**M4 前需要补测**

```bash
# 视频首帧抽取耗时（用于占位图）
time ffmpeg -i <视频> -vf scale=336:264 -frames:v 1 -y /tmp/first.jpg
# 长时间播放稳定性（跑 10 分钟看内存/CPU）
# 中文文件名视频的 ffmpeg 参数传递（用列表传参，不要拼字符串）
```

---

## 17. 跨平台与 Android 移植

> 后续计划做 Android App。本章规定**现在写代码时必须遵守的约束**，
> 让 Android 端能复用内核，而不是从零重写。

### 17.1 分层：只有 `platform/` 允许出现平台代码

```
retrostation/
├── core/        # 纯 Python：模型、状态机、i18n、配置      ← 100% 可复用
├── data/        # 纯 Python + pathlib：扫描/数据源/媒体    ← 95% 可复用
├── ui/          # 布局计算 + 绘制指令，只依赖 Canvas 抽象    ← 90% 可复用
├── launcher/    # 启动命令组装（不含进程管理）               ← 可复用
└── platform/    # ★ 唯一含平台代码的地方
    ├── base.py
    ├── linux/   # SDL2 + PIL + /dev/input + ffmpeg 管道
    └── android/ # SDL2/Kivy + KeyEvent/MotionEvent + MediaPlayer
```

**硬性约束（写代码时就要守）**

| 约束 | 说明 |
|---|---|
| ❌ `ui/` 里不许 import `sdl2` / `PIL` / `ctypes` | 只能通过 `Canvas` 抽象 |
| ❌ `data/` 里不许出现 `/mnt/mmc` 之类的绝对路径 | 一律走 `platform.rom_root` |
| ❌ 不许硬编码 `640` / `480` | 一律走 `theme.py` 的 token 或比例 |
| ❌ 不许直接 `os.system()` / `subprocess` 拉游戏 | 走 `platform.launch_game()` |
| ✅ 输入只产出**语义事件**（UP/DOWN/A/B/L1/...） | UI 不认识 evdev code |
| ✅ 中文文案全走 i18n key | 复用 `lang/*.json`，Android 可直接转 `strings.xml` |

### 17.2 平台接口（`platform/base.py`）

```python
class Platform(abc.ABC):
    # —— 显示 ——
    def init_display(self, mode: str) -> list["Canvas"]:
        """返回 1 或 2 个画布（双屏 2 个，单屏/手机 1 个）"""
    def present(self, idx: int) -> None:
    def screen_size(self, idx: int) -> tuple[int, int]:

    # —— 输入 ——
    def poll_events(self) -> list[InputEvent]:
        """语义事件：PRESS/RELEASE/REPEAT/LONG_PRESS + TAP/SWIPE"""

    # —— 硬件 ——
    def battery(self) -> int
    def temperature(self) -> float | None
    def set_brightness(self, value: int, idx: int = 0)

    # —— 启动 ——
    def launch_game(self, argv: list[str]) -> None
    def on_game_exit(self) -> None      # Linux: exec 守护脚本；Android: onActivityResult

    # —— 路径 ——
    @property
    def rom_root(self) -> Path
    @property
    def config_dir(self) -> Path        # Linux: APPS 目录；Android: getExternalFilesDir()

class Canvas(abc.ABC):
    size: tuple[int, int]
    def rect(...); def rounded_rect(...); def text(...)
    def image(self, img, box, ...)      # img 为平台无关位图句柄
    def measure_text(self, s, font) -> tuple[int, int]   # ★ 多行折行必须靠它
```

### 17.3 分辨率无关（**现在就要做，否则 Android 上会全乱**）

掌机是固定的 640×480，Android 是 1080×2400 / 1440×3200 / 折叠屏……**所有布局必须比例化**。

| 现在（掌机硬编码） | 改成 |
|---|---|
| `行高 34` | `row_h = round(H * 0.0708)`（34/480） |
| `缩略图 84×30` | `thumb = (round(W*0.131), round(H*0.0625))` |
| `网格 4 列` | `cols = clamp(round(W / 160), 3, 6)` |
| `轮播卡 196×272` | `card_h = H*0.567; card_w = card_h*0.72` |
| `下屏左 336 / 右 264` | `左 = round(W*0.525) / 右 = W - 左 - gap` |
| `字号 13/16/20` | `sp = round(H / 480 * base)` |

**双屏 → Android 的映射**

| 掌机双屏 | Android 单屏 | Android 折叠/横屏 |
|---|---|---|
| 上屏 + 下屏 | **上下分区**（60% / 40%），等价 §11 方案 A | 左右分栏，等价 §11 方案 B |
| 副屏媒体区 | 下分区的媒体卡 | 右栏顶部媒体卡 |
| 副屏元数据 | 下分区右半 | 右栏下半 |

> 换句话说：**Android 的单屏布局 = 掌机的单屏兼容模式**（§11 已经设计好了）。
> 所以 §11 不是"降级方案"，它是**第二种正式形态**，必须同等打磨。

### 17.4 Android 与 Linux 的差异对照

| 能力 | Linux 掌机（RG DS） | Android | 影响 |
|---|---|---|---|
| 双屏 | 真双屏 DSI-1/2 | 基本没有；折叠屏可近似 | 走单屏分区 |
| 输入 | `/dev/input/event4` + 触摸 event1 | KeyEvent + MotionEvent + 手柄 | `platform/android/input.py` |
| 渲染 | SDL2 + PIL（**软件**） | SDL2 / Kivy(OpenGL) / 原生 Canvas | 见 §17.5 |
| **视频** | ffmpeg **软解**（19% 单核） | **MediaPlayer/ExoPlayer 硬解** | 比掌机强，可实现 30fps 有声预览 |
| 启动游戏 | `/mnt/mod/ctrl/RA_launch.sh` | `Intent` + RetroArch/Android 包名 | `launch_game()` 分平台实现 |
| 存储 | `/mnt/mmc/Roms`（root，直读） | **分区存储限制**，需 SAF 授权 | 最大坑，见下 |
| 背光/电量 | sysfs 直读 | BatteryManager / Settings.System | `platform/android/hw.py` |
| 多语言 | 自管 `lang/*.json` | 系统 Resources | i18n key 保持一致即可 |

**Android 存储（最容易翻车的点）**

- Android 11+ 分区存储：不能随便遍历 `/sdcard/Roms`
- 方案：用 **SAF（`ACTION_OPEN_DOCUMENT_TREE`）** 让用户授权 ROM 目录，
  持久化 `takePersistableUriPermission()`，之后用 `DocumentFile` 遍历
- 代价：`DocumentFile` 遍历比 `pathlib` 慢很多 → **必须在 `data/scanner.py` 里把
  "目录遍历"也抽象掉**（`platform.list_dir(uri)`），否则扫 5000 个 ROM 会卡死
- 缩略图缓存写到 `getExternalFilesDir()`（App 私有，无需权限）

### 17.5 技术选型建议

| 方案 | 复用度 | 性能 | 包体 | 我的建议 |
|---|---|---|---|---|
| **Chaquopy（Kotlin 宿主 + Python 内核）** | ★★★★★ 复用 core/data | 中 | +8~12MB | ✅ **首选**：`core/`、`data/`、`ui/` 几乎原样搬，只用 Compose 重写渲染层 |
| Kivy / KivyMD | ★★★☆☆ | 中 | ~15MB | 纯 Python 全栈，但 UI 要重写，且 Kivy 的列表性能一般 |
| 纯 Kotlin + Compose | ★☆☆☆☆（重写） | ★★★★★ | 最小 | 体验最好、能上商店；数据格式规则已固化在本文档，重写 data 层约 1~2 周 |
| BeeWare (Toga) | ★★★☆☆ | 中低 | 小 | 生态弱，大列表滚动吃力 |

**推荐路径**

```
阶段一：先做 Chaquopy 版本，验证数据层与交互能否复用（成本低、见效快）
阶段二：若性能和包体不满足，再用本文档的字段映射表（§6.8.5）重写 Kotlin 版 data 层
```

### 17.6 现在就要做、将来能省大钱的 7 件事

1. ✅ **数据源插件化**（§6.8）—— Android 上同样要读 ES-DE / Pegasus
2. ✅ **尺寸全走 `theme.py` token**，禁止魔法数字
3. **`scanner.py` 的目录遍历走 `platform.list_dir()`** —— 不然 SAF 一来要改一堆
4. **输入统一为语义事件** —— Android 的 KeyEvent 直接映射即可
5. **`ui/` 只输出绘制指令**，不碰具体渲染 API
6. **i18n key 保持稳定** —— Android 端可脚本转成 `strings.xml`
7. **原型（prototype/）就是 Android 布局的参照** —— 它已经用 CSS 验证了比例化布局，
   建议下一步把 `prototype/css` 里的关键尺寸提取成 `ui/theme.py` 的 token 表

---

*设计基于 2026-08-28 对 RG DS（Buildroot 2024.02 / Weston / 双 640×480 DSI）的实测。*
*数据源架构（§6.8）与跨平台层（§17）为扩展预留，当前实现目标仍以 ES-DE + Linux 双屏为准。*
