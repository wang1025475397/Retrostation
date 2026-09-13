# Retrostation Android 版 详细设计

> 目标：把已在 Linux 双屏掌机（RG DS）与 TrimUI Smart Pro 上跑通的前端，移植到 **Android**
> （手机 / 平板 / 安卓掌机 / 折叠屏），**复用内核而不是重写**。
> 本文是 `docs/DESIGN.md` §17「跨平台与 Android 移植」的落地展开版，前者定的是约束，本文定的是实现。
> 文档版本：v1.0 ｜ 基于对当前代码库（v0.5.0）的逐层核查，不是纸面推演。

---

## 0. 一句话结论

预留的架构**基本兑现**：`platform/` 抽象、语义输入、比例化 metrics、数据层解耦都已到位，
并且已经用 `platform/desktop/`（tkinter）证明了"换一个平台不用动 UI"。
Android 的真正工作量集中在五项**平台无关的前置重构**（§5）和一层 **Kotlin 宿主**（§6），
预估 **约 8 周**到可发布（A0~A6），**内联模拟器再加约 6 周**（B0~B4，§8.6）。

**游戏执行走双轨制，核心定义只有一份**：外部（RetroArch App / 独立模拟器 Intent）与
内联（本 App 内自己 dlopen libretro 核心）**共用 `SystemDef.core`** ——
同一个核心名的两种执行位置，靠 `launch_mode` 三级覆盖与 `auto` 决策链选择（§8.1 / §8.2）。

**战略重点是双屏安卓掌机**（AYN Thor、Anbernic RG DS 的 Android 系统，且品类在扩张）：
Retrostation 是为双屏而生的前端，而 Android 上**目前没有原生双屏前端**；
更关键的是这条路径的 **UI 代码量为零** —— `ui/app.py` 的 dual 分支在 RG DS 上每天在跑，
Android 侧只需让 `init_display()` 返回两个 Canvas（§6.4）。

---

## 1. 目标与范围

### 1.1 目标形态

| 形态 | 典型设备 | 优先级 | 布局 |
|---|---|---|---|
| **双屏安卓掌机** | **AYN Thor**（上 1920×1080 / 下 1240×1080）、**Anbernic RG DS 的 Android 系统**（双 640×480）、AYANEO Pocket DS，后续会更多 | **P0 ★ 战略重点** | **真双屏 `DUAL`**，与掌机 Linux 版同一套布局 |
| 单屏安卓掌机 | Retroid Pocket 5/6、AYN Odin 2、RP Flip 2 | **P0** | 横屏 16:9，等价 `COMPACT`，实体键 |
| **手机** | 任意 Android 10+ | P0 | 竖屏分区 `PORTRAIT`，触摸 + 可选手柄 |
| 平板 / 折叠屏展开 | Fold 类、平板 | P1 | 横屏左右分栏 `WIDE`（DESIGN §11 方案 B） |
| 电视 / 投屏 | Android TV、外接 HDMI | P2 | 横屏 + 纯手柄导航，无触摸 |

> **为什么双屏安卓掌机是战略重点**：这是本项目**唯一无可替代的差异化优势**。
> Retrostation 从第一行代码起就是"上屏交互 + 下屏联动"的双屏原生前端，
> 而 Android 双屏机上**目前没有原生双屏前端**——实测资料显示 Thor 用户在用
> EmulationStation 整合包 + 手动逐个模拟器配双屏布局，AYN 自己也承认
> "作为双屏机器安卓原生适配非常不足"，只能靠自家 TCC（控制中心）打补丁。
>
> 更关键的是：**这条路径的 UI 代码量是零**。`ui/app.py` 的 dual 分支在 RG DS 上
> 每天在跑，`init_display()` 返回 2 个 Canvas 即可。Android 双屏的全部工作
> 都在平台层的 `present(1)`（§6.4）。
>
> **为什么其次是单屏安卓掌机**：交互模型与现有实现完全一致（实体方向键 + A/B/X/Y + L/R），
> UI 侧同样零改动。手机的触摸交互才是新增能力（§10.3），风险最高，放最后。

### 1.2 不在本期范围

- **纯 Linux 端的内联模拟器**（Android 走 B 系列内联，但掌机继续用 `RA_launch.sh`——已够好）
- 刮削（继续由 tiny-scraper / Skraper 在 PC 上产出媒体，Android 只读）
- 折叠屏的"跨折痕双区"（用 `WIDE` 左右分栏近似，不做真双 Canvas）
- 副屏跑第三方内容（TCC 那种"上屏游戏 / 下屏 B 站"的双应用场景不是前端的职责）
- Google Play 上架（权限政策见 §7.5，首发走侧载）

### 1.3 必须保持的东西

| 不变量 | 理由 |
|---|---|
| **媒体与元数据格式完全一致** | 同一张 SD 卡插掌机、插手机、接 PC，`gamelist.xml` + `media/` 都认；这是本项目的核心资产 |
| **i18n key 稳定** | `assets/lang/*.json` 直接复用，不转 `strings.xml`（Python 侧仍是权威） |
| **Linux 端零回归** | §5 的重构必须先在掌机 + TrimUI + desktop 三处跑通再合入 |

---

## 2. 现状核查：预留的架构兑现了多少

逐项实测（读代码 + grep，不是推测）：

| 预留项 | 状态 | 证据 |
|---|---|---|
| `platform/base.py` 抽象完备 | ✅ | `Platform`(28 方法) / `Canvas`(12 方法) / `VideoPipe` / `AudioPipe` / `FileEntry` / `InputEvent` 全部就位，且带"为什么"的注释 |
| 输入已是语义事件 | ✅ | `InputAction` 枚举 18 项，UI 不认识 evdev code |
| `data/` 不碰绝对路径 | ✅ | `library.py` 9 处、`media.py` 14 处、`scanner.py` 6 处均走 `platform.list_dir` / `rom_root` |
| 尺寸 token 化（E2） | ✅ **已完成** | `core/theme.py` 的 `Metrics.u()` / `scale` / `grid_cols` 自适应；README 里 E2「待开始」**已过时** |
| 抽象真的可换实现 | ✅ | `platform/desktop/`（tkinter + 键盘）已是第二个实现，跑同一套 UI |
| 设备差异靠配置而非代码 | ✅ | TrimUI 移植只加了 `packaging/trimui/data-config.json` + 一份键映射 + 一个媒体源 |
| `FileEntry` 为 SAF 预留（无 path 字段） | ✅ | `base.py` 注释明确写了"Android SAF 返回不透明 URI" |
| 常驻/让位两条启动路径 | ✅ | `can_stay_resident()` + `suspend_display()` 已存在，Android 走常驻路径天然合适 |

### 2.1 必须先补的裂缝（这是真实工作量）

| # | 泄漏点 | 位置 | 为什么必须修 |
|---|---|---|---|
| L1 | 直接操作 `canvas.pil_image` | `ui/app.py` 5 处（586 / 943 / 950 / 955 / 1252） | 增量重绘缓存绕过了 `Canvas`，任何非 PIL 后端立刻崩 |
| L2 | `from PIL import Image, ImageChops, ImageDraw` | `ui/screens/games.py:9` | 圆角遮罩 + backdrop 合成，违反"`ui/` 不许 import PIL" |
| L3 | PIL 图像处理散落 | `data/media.py` 6 处（占位图 / 解码 / 缩放 / 写盘 / fit / cover） | 缩略图管线是平台能力，不是数据逻辑 |
| L4 | 启动只能产出 `argv` | `launcher/launch.py` 全文 + `LAUNCH_CMD_PATH` | Android 是 `Intent`，没有 argv 也没有 `/tmp` 命令文件 |
| L5 | `subprocess.run` 直接起游戏 | `ui/app.py:1532` | 常驻模式的启动路径没走 `platform.launch_game()` |
| L6 | 输入无触摸语义 | `platform/base.py` `InputAction` | `InputEvent` 有 `x/y` 字段、注释提到 `InputAction.TAP`，但**枚举里没有** —— 预留了一半 |
| L7 | 单屏详情条比例写死 | `core/theme.py` `strip_h = u(118)` | 竖屏 20:9 手机上 118 参考像素的详情条只占屏高 5%，需要按形态给分区比例 |

> **关键判断**：L1~L3 是 PIL 泄漏，L4~L5 是进程模型泄漏，L6~L7 是形态适配缺口。
> 三类都**与 Android 无关地有价值**：修完 Linux 端也更干净（desktop 平台同样受益），
> 所以它们不是"为 Android 付的税"，而是**本来就该收的债**。

---

## 3. 技术选型

### 3.1 方案对比

| 方案 | 内核复用 | 渲染性能 | 包体 | 生态风险 | 结论 |
|---|---|---|---|---|---|
| **Chaquopy（Kotlin 宿主 + Python 内核）** | ★★★★★ `core`/`data`/`ui`/`launcher` 几乎原样 | 取决于 §4 的渲染路线 | +12~18 MB | 商业许可（非开源项目需付费）、Pillow wheel 存疑 | ✅ **首选** |
| python-for-android + SDL2 | ★★★★☆ 可复用现有 SDL ctypes 层 | 中 | ~15 MB | 存储 / Intent / 生命周期全走 pyjnius，胶水更脏；SDL Activity 已初始化，ctypes 再 `SDL_Init` 冲突 | ❌ |
| 纯 Kotlin 重写 | ★☆☆☆☆ | ★★★★★ | 最小 | 数据层规则要重写一遍（`gamelist.xml` / Pegasus / 媒体探测 / 索引） | ❌ 首发不做 |
| Flutter / RN | ☆ | ★★★★☆ | 大 | 完全另一套栈 | ❌ |

### 3.2 Chaquopy 的三个硬前提（**A0 必须先验证**）

| # | 前提 | 已知情况（2026-09 查证） | 不满足时的退路 |
|---|---|---|---|
| P1 | **Pillow 有 Android wheel** | Chaquopy 包索引 `chaquo.com/pypi-13.1/pillow/` **存在**，目录最后更新 2024-10-16 | 走 §4 的 **R2（Skia Canvas）**，彻底不要 Pillow |
| P2 | **16 KB page size 设备可加载** | Chaquopy 17.0.0 起支持 16 KB 页设备，但**2024 年 10 月前构建的 Android wheel 仍会加载失败** —— pillow 目录正好卡在 2024-10-16 这个临界点上 | 同上；或自建 wheel |
| P3 | **Python 版本** | Chaquopy 17 支持 3.10.19 / 3.11.14 / 3.12.12 / 3.13.9 / 3.14.0，**已放弃 3.8/3.9**；16 KB 兼容性建议用 3.13+ | 本项目 `requires-python = ">=3.9"`，实际代码用 `from __future__ import annotations`，3.13 无障碍 |

> **A0 的验收动作**：建一个空 Chaquopy 工程，`pip install Pillow`，在**一台 Android 15+ / 16 KB 页真机**上
> `import PIL; PIL.Image.new(...)`。这一条实验决定 §4 走 R1 还是 R2，**必须先做，不要边写边赌**。

### 3.3 许可提示

Chaquopy 对**闭源商用**需要付费许可；开源（GPL 兼容）项目免费。本项目若保持开源分发，无成本。
若将来要闭源上架，选型需回到"纯 Kotlin 重写数据层"，届时 §6.8 的字段映射表就是规格书。

---

## 4. 渲染方案（最关键的决策）

### 4.1 不能做的事：在原生分辨率上做软渲染

掌机上的实测基线（DESIGN §9.4）：`draw_top` 32 ms + `draw_bottom` 19 ms @ 640×480（0.31 MP），A55。

| 目标分辨率 | 像素量 | 相对掌机 | 现代手机 SoC 单核约 3× A55 | 预估帧耗时 | 可用性 |
|---|---|---|---|---|---|
| 1080×2400（竖屏原生） | 2.59 MP | 8.4× | ÷3 | **~90 ms** | ❌ 11 fps |
| 1920×1080（掌机横屏原生） | 2.07 MP | 6.7× | ÷3 | ~71 ms | ❌ |
| **720×1600（逻辑竖屏）** | 1.15 MP | 3.7× | ÷3 | **~40 ms** | ⚠️ 25 fps |
| **540×1200（逻辑竖屏）** | 0.65 MP | 2.1× | ÷3 | **~22 ms** | ✅ 45 fps |
| **960×540（逻辑横屏）** | 0.52 MP | 1.7× | ÷3 | ~18 ms | ✅ 55 fps |

**结论**：Python 侧永远渲染到**逻辑画布**，由 GPU 免费上采样到物理屏。
这不是妥协——`desktop` 平台已经在做同一件事的反向操作（`_RENDER_SCALE = 3.0` 超采样后 LANCZOS 缩小）。

**逻辑分辨率选取规则**（`platform/android/display.py`）：

```
target_px  = 0.7 MP                       # 预算：留出 30 fps 余量
ratio      = 物理宽 / 物理高
logical_h  = round(sqrt(target_px / ratio))
logical_w  = round(logical_h * ratio)
# 对齐到 4 的倍数（Bitmap stride 友好），并夹在 [480, 1280] 之间
```

1080×2400 → **约 561×1247 → 对齐后 560×1248**，`Metrics.scale = min(560/640, 1248/480) = 0.875`。
`Metrics` 现有的 `min()` 逻辑在竖屏下由**宽度**决定 scale，高度富余转化为更多列表行（36 行），
这正是想要的行为——**不需要改 `Metrics.scale`**。

> 上采样倍率约 1.93×，文字边缘会略软。可接受度：掌机屏 640×480 本就是 1:1，
> 手机上 1.93× 双线性放大后中文在 5 寸屏上仍清晰（等效 ~14 sp）。若不接受，走 R2。

### 4.2 两条渲染路线

| | **R1 · PIL 渲染 + Bitmap 上传** | **R2 · Skia Canvas 实现** |
|---|---|---|
| 做法 | 复用 `PilCanvas` 全部代码；`present()` 把 RGBA bytes 交给 Kotlin，`Bitmap.copyPixelsFromBuffer` → `Canvas.drawBitmap`（带缩放矩阵，硬件加速） | 新写 `AndroidCanvas(Canvas)`，12 个方法各自映射到 `android.graphics`（`Paint` / `Path` / `LinearGradient` / `drawText` / `measureText` / `drawBitmap`） |
| 代码量 | 平台层 ~200 行 | 平台层 ~600 行（Kotlin）+ Python 侧桥 ~250 行 |
| 依赖 Pillow | **是**（P1/P2 风险全在这） | **否** |
| 性能 | 逻辑分辨率 + 上采样，25~45 fps | 原生分辨率硬件加速，60 fps |
| 每帧跨 JNI | 1 次，传 0.65 MP × 4 B ≈ **2.6 MB**（direct ByteBuffer，~0.5 ms） | 每帧数百次 draw call（Chaquopy 单次调用约 1~10 μs，300 次 ≈ 1~3 ms） |
| 文本渲染 | PIL + 思源黑体，与掌机**逐像素一致** | Skia，字形不同（更好但不一致，截图对比测试要重做基线） |
| 增量重绘（L1） | 直接受益于现有 `_top_cache` 机制 | 需要 `saveLayer` / 离屏 Bitmap 等价物 |

### 4.3 决策：R1 先行，R2 作为性能升级路径

理由：

1. **R1 让内核在两周内跑起来**，最快拿到"架构到底能不能复用"的答案；
2. 只要 §5 的重构（把 `pil_image` 关进 `Canvas`）做掉，**R1 → R2 是换一个 `Canvas` 实现**，
   UI 与数据层零改动 —— 这正是抽象的价值，不必现在赌；
3. R2 的收益（60 fps）在**列表滚动**上才明显，而掌机端至今也只有 ~14 fps 且体验被接受，
   R1 的 25~45 fps 已是改善。

**若 A0 判定 P1/P2 不成立**（Pillow 在 16 KB 设备上加载失败），直接跳 R2 —— 此时 §5 的重构
从"应该做"变成"必须做"，工期 +2 周。

### 4.4 帧上传管线（R1）

```
Python 主循环（后台线程）
  ui/app.py 画完 → platform.present(0)
    ↓  PilCanvas.pil_image.tobytes() ——— 一次 memcpy，0.65 MP 约 0.4 ms
  FrameBridge.push(index, bytes)             # Chaquopy: bytes → byte[] 自动转换
    ↓  JNI
Kotlin: ByteBuffer.wrap(bytes) → Bitmap.copyPixelsFromBuffer
    ↓  标记脏，postInvalidateOnAnimation()
RetroSurfaceView.onDraw: canvas.drawBitmap(bmp, srcRect, dstRect, paint)   # GPU 缩放
```

**优化余量（首发不做，留给 A5）**：改成 Kotlin 侧分配 direct `ByteBuffer`，
Python 侧用 `PilCanvas` 的 `frombuffer` 直接写进去，省掉 `tobytes()` 的那次拷贝。
需要 `Canvas` 支持"外部缓冲区构造"，接口上是 `PilCanvas(width, height, buffer=...)`。

### 4.5 双画布在 Android 上的归属

`init_display(mode)` 按**探测结果**返回 1 或 2 个 Canvas，与掌机同一套语义
（`auto` / `dual` / `single`）：

| 设备 | 返回 | UI 路径 |
|---|---|---|
| 双屏安卓掌机（Thor / RG DS Android） | **2 个 Canvas** | `DUAL` —— `ui/app.py` 的双屏分支，**代码零改动** |
| 单屏掌机 / 手机 / 平板 | 1 个 Canvas | `COMPACT` / `PORTRAIT` / `WIDE`，副屏内容折叠为详情区 |

探测与实现见 **§6.4（Android 双屏）**。

**逐屏逻辑分辨率**：两块屏的物理分辨率可以完全不同（Thor 上屏 1920×1080 16:9、
下屏 1240×1080 约 3.44:3），所以 §4.1 的公式**对每块屏独立计算**：

| Thor | 物理 | 逻辑（0.7 MP 预算） | `Metrics.scale` |
|---|---|---|---|
| 上屏 | 1920×1080（16:9） | 约 1116×628 → 对齐 **1116×628** | `min(1116/640, 628/480) = 1.31` |
| 下屏 | 1240×1080（3.44:3） | 约 896×780 → 对齐 **896×780** | `min(896/640, 780/480) = 1.40` |

> **这一点已经被架构支持，不需要改代码**：`ui/app.py:299` 是
> `Painter(canvas, metrics_for(*canvas.size), ...)` —— **每个 canvas 各自算 metrics**；
> 视频尺寸取 `bottom.media_inner_size(metrics_for(*canvases[1].size))`（下屏的）；
> `session.attach_metrics()` 只吃上屏 metrics，而它只用于网格列数/行数（在上屏）。
>
> 但 RG DS 上两屏都是 640×480，**这条异构路径从未被真实验证过**。
> 它列在 §15 的待验证清单里，是 A2 的第一个测试用例。

---

## 5. 前置重构（平台无关，对 Linux 端零功能变化）

**原则**：每一项都能独立合入、独立验收，合入后掌机 / TrimUI / desktop 三处行为不变。
`tests/` 已有 33 个测试文件，重构以它们为回归网。

### 5.1 R-A：把增量重绘关进 Canvas（修 L1）

现状（`ui/app.py`）：

```python
self._top_cache = painter.canvas.pil_image.copy()     # 943
painter.canvas.pil_image.paste(self._top_cache)       # 950 / 955 / 586
cache.paste(painter.canvas.pil_image.crop(...), ...)  # 1252
```

新增到 `Canvas`（`platform/base.py`）：

```python
class Canvas(abc.ABC):
    @abc.abstractmethod
    def snapshot(self, box: Sequence[float] | None = None) -> object:
        """Opaque copy of the surface (or of ``box``), for incremental repaint.

        The app caches a painted panel and restores it instead of repainting the
        whole screen while only the selection moves (DESIGN §9.4).  What the
        handle *is* belongs to the platform: a PIL image today, a Bitmap on
        Android.  Callers may only pass it back to :meth:`restore`.
        """

    @abc.abstractmethod
    def restore(self, snapshot: object, at: Sequence[float] | None = None) -> None:
        """Blit a :meth:`snapshot` back, optionally at ``(x, y)``."""
```

`PilCanvas` 实现 = `pil_image.copy()` / `paste()`，**逐行等价**，无行为变化。
`AndroidCanvas`（R2）实现 = `Bitmap.createBitmap` / `drawBitmap`。

改动：`ui/app.py` 5 处；`_top_cache: object`（去掉 PIL 类型标注）。

### 5.2 R-B：图像效果收进 Canvas / ImageOps（修 L2、L3）

`ui/screens/games.py` 需要的两件事：**圆角裁切**、**backdrop 压平合成**。
`data/media.py` 需要的：**占位图、解码、等比 fit、cover 裁切、写盘**。

统一收到一个新的平台服务 `Platform.images`（返回 `ImageOps`），而不是继续在 `data/` 里 `from PIL import`：

```python
# platform/base.py
class ImageOps(abc.ABC):
    """Bitmap manipulation that is not drawing.  ``data/`` and ``ui/`` use this
    instead of importing an imaging library, so a platform can back it with
    Pillow, Skia or MediaCodec-produced bitmaps."""

    @abc.abstractmethod
    def decode(self, path: Path, *, max_size: tuple[int, int] | None = None) -> object: ...
    @abc.abstractmethod
    def encode(self, bitmap: object, path: Path, *, quality: int = 85) -> None: ...
    @abc.abstractmethod
    def size(self, bitmap: object) -> tuple[int, int]: ...
    @abc.abstractmethod
    def fit(self, bitmap: object, width: int, height: int) -> object:
        """Scale to fit inside (contain), never stretch."""
    @abc.abstractmethod
    def cover(self, bitmap: object, width: int, height: int) -> object:
        """Scale to fill and centre-crop."""
    @abc.abstractmethod
    def rounded(self, bitmap: object, radius: int) -> object: ...
    @abc.abstractmethod
    def dim(self, bitmap: object, opacity: int) -> object: ...
    @abc.abstractmethod
    def gradient_placeholder(self, seed: str, width: int, height: int,
                             label: str, font: object) -> object: ...
    @abc.abstractmethod
    def flatten(self, bitmap: object, background: Sequence[int]) -> object:
        """Composite onto an opaque colour and drop alpha (backdrop path)."""
    @abc.abstractmethod
    def is_opaque(self, bitmap: object) -> bool: ...

class Platform(abc.ABC):
    @property
    @abc.abstractmethod
    def images(self) -> ImageOps: ...
```

改动清单：

| 文件 | 现在 | 之后 |
|---|---|---|
| `platform/linux/images.py` | — | **新增** `PilImageOps`，把 `data/media.py` 里的 PIL 代码整体搬进来 |
| `data/media.py` | 6 处 `from PIL import` | 全部改调 `platform.images.*`；`ThumbnailCache` 已持有 `platform`，无需改签名 |
| `ui/screens/games.py` | `Image/ImageChops/ImageDraw` | `painter.platform.images.rounded(...)` / `.flatten(...)` |
| `Canvas.dim` | 已存在 | 保留（绘制期用），实现委托给 `ImageOps.dim` |

> **注意**：`ImageOps` 有 11 个方法，比 `Canvas` 还大，是这次重构的主要体量。
> 但它 100% 是"把现有代码搬个位置"，没有新逻辑，风险低；`tests/` 里针对
> 缩略图与占位图的测试是天然回归网。

### 5.3 R-C：启动目标泛化（修 L4、L5）

现状：`LaunchPlan.argv: tuple[str, ...]`，交接靠 `/tmp/retrostation_launch.cmd` + 退出码 42。
Android 既没有 argv 也没有那个文件，但**"由平台决定怎么交接"这件事接口上已经对了**
（`Platform.launch_game()` + `can_stay_resident()`）。只需把"命令"这个概念抽象掉：

```python
# launcher/target.py（新增）
@dataclass(frozen=True)
class ArgvTarget:
    """A command line (Linux handhelds)."""
    argv: tuple[str, ...]

@dataclass(frozen=True)
class IntentTarget:
    """An Android activity to start."""
    package: str
    activity: str = ""                       # 空 = 让系统按 action 解析
    action: str = "android.intent.action.MAIN"
    data_uri: str = ""                       # ACTION_VIEW 走这里
    mime: str = ""
    extras: tuple[tuple[str, str], ...] = () # 有序，便于日志与测试
    fallbacks: tuple["IntentTarget", ...] = ()   # 见 §8.2 的核心路径回归

LaunchTarget = ArgvTarget | IntentTarget

@dataclass(frozen=True)
class LaunchPlan:
    target: LaunchTarget
    core_label: str
```

- `Platform.launch_game(target: LaunchTarget)`（签名从 `Sequence[str]` 改为 `LaunchTarget`）
- `LinuxPlatform.launch_game`：只接受 `ArgvTarget`，其余不变
- `ui/app.py:1532` 的 `subprocess.run(plan.argv)` → `self.platform.run_foreground(plan.target)`
  （新增 `Platform.run_foreground`，默认实现 = "不支持，抛异常"；Linux 实现 = 现在这段 `subprocess.run`；
  Android 实现 = `startActivityForResult` + 等 `onActivityResult`）

> 这一项顺手消掉了一个既有的架构瑕疵：**常驻模式的启动路径此前绕过了平台层**，
> 只有"退出让位"那条路走了 `launch_game()`。

### 5.4 R-D：触摸语义事件（修 L6）

`InputEvent` 已有 `x/y` 字段，只需补齐动作与手势语义：

```python
class InputAction(str, enum.Enum):
    ...
    #: A tap at (x, y) in *logical canvas* coordinates.  The platform maps
    #: physical touch pixels into the canvas' coordinate space, so the UI never
    #: learns the device's real resolution.
    TAP = "tap"
    #: Vertical/horizontal drag.  Carries the delta in ``dx``/``dy``.
    DRAG = "drag"
    #: Inertial scroll continuation after the finger lifts.
    FLING = "fling"

@dataclass(frozen=True)
class InputEvent:
    ...
    dx: int = 0
    dy: int = 0
```

**UI 侧的命中测试**放在各 screen（`ui/screens/*.py`）里，形式是新增
`hit_test(x, y) -> Hit | None`，与已有的布局计算共用同一份 metrics —— 不允许重复布局知识。
Linux 平台不产出这些事件，UI 分支自然不触发，**掌机零影响**。

### 5.5 R-E：形态化布局分区（修 L7）

`Metrics` 增加"形态"概念，而不是继续用单一 `strip_h`：

```python
class Form(str, enum.Enum):
    DUAL = "dual"          # 双屏：上屏 + 下屏。掌机 Linux 版现状，
                           # Android 双屏机（Thor / RG DS Android）直接复用，
                           # 两块屏允许分辨率与比例都不同（§4.5 / §11.1）
    COMPACT = "compact"    # 掌机/TrimUI 单屏 4:3~16:9：列表 + 118u 详情条（现状）
    PORTRAIT = "portrait"  # 手机竖屏：列表 60% + 详情 40%
    WIDE = "wide"          # 平板/折叠屏横屏：左列表 65% + 右详情 35%（DESIGN §11 方案 B）

@dataclass(frozen=True)
class Metrics:
    width: int
    height: int
    form: Form = Form.DUAL
```

- `strip_h` 改为按 form 取值：`COMPACT → u(118)`；`PORTRAIT → round(height * 0.40)`；
  `WIDE → 0`（详情走右栏）
- 新增 `detail_box() -> Rect`：四种形态统一出口，`ui/app.py` 的 `_draw_detail_strip`
  只认这个矩形，不再自己算
- `form` 由平台推断：`AndroidPlatform` 依据 `ratio` 与 `isTablet` 给出；Linux 给 `DUAL`/`COMPACT`

> **DESIGN §11 的伏笔在这里兑现**：文档里说方案 B（左右分栏）是占位字段没实现——
> Android 平板/折叠屏正是它的第一个真实用户，做 `WIDE` 等于把这笔债一并还掉。

### 5.6 重构顺序与验收

| 序 | 项 | 依赖 | 验收（三平台跑通） |
|---|---|---|---|
| 1 | R-A snapshot/restore | — | `pytest`；掌机滚动帧率不回退（对比 §9.4 基线） |
| 2 | R-B ImageOps | — | `--check FC` 的媒体覆盖率数字与重构前完全一致；缩略图字节级相同 |
| 3 | R-C LaunchTarget | — | 掌机启动 → 玩 → 退出回原位；TrimUI 直调 RA 正常 |
| 4 | R-D 触摸语义 | — | 掌机无行为变化（不产事件）；desktop 可用鼠标点击验证 |
| 5 | R-E 形态布局 | R-A | 掌机 `dual` / 强制 `single` 两种模式截图与重构前一致 |

**R-A ~ R-E 全部完成后，Android 平台层才开始写。** 这个顺序不能倒——
边写平台边改抽象会让"到底是抽象不对还是平台没写好"变得无法判断。

---

## 6. Android 平台实现设计

### 6.1 工程结构

```
android/                                  # 新增：独立 Gradle 工程（不进 src/）
├── settings.gradle.kts
├── build.gradle.kts                      # Chaquopy plugin
├── app/
│   ├── build.gradle.kts                  # minSdk 26 / targetSdk 36 / abi arm64-v8a
│   └── src/main/
│       ├── AndroidManifest.xml
│       ├── kotlin/ai/retrostation/
│       │   ├── MainActivity.kt           # 生命周期 + 权限 + 全屏沉浸
│       │   ├── RetroSurfaceView.kt        # 帧显示（R1: drawBitmap）
│       │   ├── FrameBridge.kt             # Python → Bitmap
│       │   ├── InputBridge.kt             # KeyEvent / MotionEvent → 语义事件
│       │   ├── StorageBridge.kt           # 权限、目录遍历、Tree URI 解析
│       │   ├── LaunchBridge.kt            # Intent 组装 + startActivityForResult
│       │   ├── MediaBridge.kt             # ExoPlayer 预览 + SoundPool 音效
│       │   ├── HwBridge.kt                # 电量 / 温度 / 亮度
│       │   └── PyRuntime.kt               # Chaquopy 启动、Python 线程托管
│       ├── python/                        # Chaquopy sourceSets：软链/复制 ../../src
│       └── res/                           # 图标、主题（仅启动闪屏，UI 全在画布里）
└── gradle/
```

Python 侧：

```
src/retrostation/platform/android/
├── __init__.py
├── platform.py      # AndroidPlatform(Platform)
├── canvas.py        # R1: 复用 PilCanvas；R2: AndroidCanvas 走 JNI
├── images.py        # R1: 复用 PilImageOps；R2: SkiaImageOps
├── input.py         # 从 InputBridge 取事件、按键映射表
├── storage.py       # list_dir / rom_root / config_dir（All-files 或 SAF）
├── video.py         # SurfaceVideoPipe（external=True，见 §9.2）
├── audio.py         # ExoPlayer 音轨 + SoundPool 音效
├── hw.py            # 电量 / 温度 / 亮度
└── launch.py        # IntentTarget 落地 + 启动器表加载
```

> **`src/` 不动结构**：Android 工程通过 Chaquopy 的 `sourceSets`
> 指向 `../../src`，Python 内核仍然是同一份代码、同一个包、同一套测试。
> **绝不复制一份 Python 代码到 android/ 里**——那是移植失败最常见的死法。

### 6.2 线程模型（ANR 的生死线）

```
┌─ Android 主线程（UI）─────────────────────────────┐
│  MainActivity 生命周期 / 权限回调 / Intent 结果      │
│  RetroSurfaceView.onDraw ← postInvalidateOnAnimation │
│  KeyEvent / MotionEvent → InputBridge（入队，立即返回）│
└──────────────────────────────────────────────────┘
              ▲ push(bytes)        │ 取事件（线程安全队列）
              │                    ▼
┌─ "retrostation-core" 线程（Python）────────────────┐
│  ui/app.py 的 run() 主循环，原样不改：              │
│    poll_events → 状态机 → 绘制 → present            │
└──────────────────────────────────────────────────┘
```

要点：

1. **Python 主循环绝不跑在 Android 主线程**。`App.run()` 是一个自带节奏的
   `while` 循环（`_FRAME_BUDGET` 睡眠 + 分片轮询），放主线程必然 ANR。
2. `present()` 只做"拷贝 + 标脏 + postInvalidate"，**不等待 vsync**——
   等待会把 Python 线程的节奏绑到 Choreographer 上，与它自己的帧预算打架。
3. 帧率天然由 Python 侧的 `_FRAME_BUDGET` 控制（掌机上已调好），Kotlin 侧
   `onDraw` 拿到什么画什么，掉帧只表现为重复画同一帧，不会撕裂。
4. 输入队列用 `java.util.concurrent.ConcurrentLinkedQueue`，
   `poll_events(timeout)` 在 Python 侧做短睡轮询（与 desktop 平台同构）。

### 6.3 生命周期映射

| Android 回调 | 动作 | 复用的既有能力 |
|---|---|---|
| `onCreate` | 启动 Chaquopy → 起 core 线程 → `main(argv)` | — |
| `onPause` | `platform.suspend_display()`；`VideoPlayer.stop()`；释放音频 | ✅ 已存在（掌机让位副屏用同一路径） |
| `onResume` | `platform.resume_display()`；标记全屏脏 | ✅ 已存在 |
| `onActivityResult`（游戏退出） | `platform.on_resume()`；重扫 `lastplayed` | ✅ 已存在 |
| `onDestroy` / `onTrimMemory(CRITICAL)` | `ThumbnailCache.clear_memory()`；落盘 config/state | ✅ 已存在 |
| `onConfigurationChanged`（旋转 / 折叠） | **重建 metrics** → `Form` 切换 | ⚠️ 见下 |

> **旋转的处理**：掌机上 `init_display` 明确要求"每进程只调一次"（Wayland 重建会崩），
> 所以 `App.run()` 在开头一次性 `metrics_for(*canvases[0].size)`。
> Android 上旋转要换逻辑分辨率，两个选择：
> - **首发（A1~A4）**：`AndroidManifest` 锁定方向（手机竖屏 / 掌机横屏），**不处理旋转**；
> - **A5**：走已有的 `EXIT_RESTART_UI = 43` 契约 —— 前端自己重启一次 UI，
>   这正是"切换 screen_mode"已经在用的机制，Android 上把"重启进程"换成"重启 core 线程"即可。
>
> 后者几乎零新增设计，是这套退出码契约的意外红利。

### 6.4 Android 双屏（P0 · 战略重点）

#### 6.4.1 目标设备与已知规格

| 设备 | 上屏 | 下屏 | 备注 |
|---|---|---|---|
| **Anbernic RG DS（Android 系统）** | 640×480 | 640×480 | **同尺寸**，与本项目 Linux 版硬件完全相同 → **第一个测试目标**（手上就有） |
| **AYN Thor**（Lite/Base/Pro/Max） | 6" 1920×1080 16:9 120 Hz OLED | 3.92" 1240×1080（≈3.44:3）60 Hz OLED | **异构双屏**；骁龙 865 / 8 Gen2；系统提供"双屏异显 / 仅上屏 / 仅下屏"三种模式 |
| AYANEO Pocket DS 等后续机型 | — | — | 品类在扩张，探测逻辑必须**通用**，不做机型硬编码 |

#### 6.4.2 实现：一个 Activity + 一个 Presentation

Android 的多显示器模型与本项目"**单进程 · 双画布 · 一个主循环**"的架构天然同构：

```
                    ┌─ Python core 线程（唯一主循环）──┐
                    │  present(0)          present(1)  │
                    └────┬───────────────────┬─────────┘
                         │ JNI               │ JNI
        ┌────────────────▼──────┐   ┌────────▼──────────────────┐
        │ MainActivity          │   │ BottomPresentation        │
        │  (Display 0 · 主屏)    │   │  (Display 1 · 副屏)        │
        │  RetroSurfaceView     │   │  RetroSurfaceView          │
        └───────────────────────┘   └───────────────────────────┘
```

`Presentation` 是 `Dialog` 的子类，绑定到指定 `Display`，**由同一个 Activity 持有**——
不需要第二个 Activity、不需要 `FEATURE_ACTIVITY_ON_SECONDARY_DISPLAY`、
不需要多 Activity 同时 resumed 的设备支持。这是双屏方案里最省事也最稳的一条。

```kotlin
// DisplayBridge.kt
fun probeDisplays(): List<Point> {
    val dm = getSystemService(DISPLAY_SERVICE) as DisplayManager
    // 不用 DISPLAY_CATEGORY_PRESENTATION：部分厂商不给副屏打这个标记，
    // 会漏掉真实存在的第二块屏。改为取全部 Display 再剔除虚拟/无效的。
    return dm.displays
        .filter { it.isValid && it.displayId != Display.DEFAULT_DISPLAY }
        .filterNot { it.flags and Display.FLAG_PRESENTATION == 0 && it.name.contains("Overlay") }
        .map { Point(it.mode.physicalWidth, it.mode.physicalHeight) }
}
```

`AndroidPlatform.init_display(mode)`：

```
mode == "single"           → 只建主屏 Canvas
mode == "dual"             → 强制两个（副屏探测失败则记警告并降级，不抛异常)
mode == "auto"（默认）      → 探测到 ≥2 个有效 Display 就 dual，否则 single
```

> **降级必须静默**：这条规则来自 DESIGN §4.4 的第 4 条（"下屏窗口创建失败要能静默降级"）。
> Android 上更需要它——用户可能在 TCC 里把机器切成了"仅上屏"。

#### 6.4.3 三个必须处理的坑

**坑 1 · 输入焦点跟随（最扎手，实测已确认存在）**

Thor 用户实测记录：

> "因为安卓系统的原因，如果使用下屏触控会导致**焦点变到下屏，这时候手柄可能无法正常使用**。"

AYN 的解法是在 TCC 里做"触控焦点锁定"。而对本项目，这个问题**结构性地消失**：

| 别人的困境 | 我们的情况 |
|---|---|
| 上下屏是两个独立 App（游戏 + 视频），手柄事件只能送给有焦点的那个 | 上下屏是**同一进程的两个窗口**，两边的 `onKeyDown` / `onGenericMotionEvent` **都汇入同一个 `InputBridge` 队列** |

实现要求：

```kotlin
// 两处都要装同一个监听，且都 return true 吃掉事件
MainActivity.onKeyDown(code, ev)        → InputBridge.offer(...)
BottomPresentation.onKeyDown(code, ev)  → InputBridge.offer(...)   // ★ 别漏
```

副屏 `Presentation` 还要 `setCanceledOnTouchOutside(false)`，否则点主屏会把它关掉。

**坑 2 · 副屏触摸坐标属于副屏**

副屏的 `MotionEvent` 到达 `BottomPresentation` 的 view，坐标是**副屏物理像素**。
按 §5.4 的约定，平台负责映射到**该 Canvas 的逻辑坐标**，并在事件上标注屏号：

```python
@dataclass(frozen=True)
class InputEvent:
    ...
    #: Which canvas the touch landed on (0 = top, 1 = bottom).  Key events
    #: leave it 0: a button press does not belong to a screen.
    screen: int = 0
```

这正好实现 DESIGN §5 里设计过但从未落地的下屏触摸：点媒体区/Logo 条 = 启动游戏、
上下滑 = 列表滚动、点底部快捷条。**掌机上因为没有 evdev 触摸驱动的封装而搁置，
Android 上是免费的。**

**坑 3 · 合盖 / 切换屏幕模式导致 Display 热插拔**

翻盖机合上、或用户在 TCC 里切"仅上屏"，副屏 Display 会被移除。
但 `init_display()` 的契约是"每进程只调一次"（Wayland 的教训，见 DESIGN §4.4）。

对策——**沿用已有的 `EXIT_RESTART_UI = 43` 契约**：

```
DisplayManager.DisplayListener.onDisplayAdded/Removed
   → 若增减的是我们在用的副屏
   → 通知 Python 侧 App 设 self._restart_ui = True
   → run() 返回 43 → Kotlin 侧重启 core 线程（不重启进程）
   → 新一轮 init_display() 重新探测，state.json 的 resume 恢复现场
```

这条路径**掌机上已经在用**（切换 `screen_mode` 设置项就是走它），Android 只是换了个触发源。

> **首发简化**：A2~A4 阶段可以先不监听热插拔（合盖本来就该息屏），
> A5 再接 `DisplayListener`。风险低，因为退路已经存在。

#### 6.4.4 副屏让位给模拟器

双屏机上的模拟器**自己也要用副屏**（实测：3DS 的 Azahar 默认适配双屏；
NDS 的 MelonDS 要手动配"内屏布局只留上屏 + 外显屏幕 = 下屏"；DraStic 则下屏触控不可用）。

所以启动游戏时必须**彻底交出副屏**：

```
launch 前：VideoPlayer.close() → suspend_display()
           ├─ BottomPresentation.dismiss()      ★ 必须 dismiss，不是 hide
           └─ MainActivity 转后台（startActivityForResult 自然发生）
游戏退出： onActivityResult → resume_display()
           └─ 重建 Presentation（Display 还在，重建是安全的——这不是 Wayland）
```

> `dismiss()` 而不是隐藏：Presentation 只要还活着就占着副屏窗口层，
> 模拟器的副屏输出可能被压在下面。这是必须在 A4 实测确认的一条。

#### 6.4.5 双屏带来的额外收益

| 收益 | 说明 |
|---|---|
| **亮度独立控制已就绪** | `set_brightness(value, index)` 的 `index` 参数一直存在（掌机双背光节点），Thor 的系统也支持双屏独立亮度 |
| **副屏视频预览** | ExoPlayer 的 Surface 直接摆在副屏 Presentation 里（§9.2），比掌机的 ffmpeg 软解强一个数量级：**1240×1080 硬解 60 fps 有声** |
| **NDS / 3DS 库的天然主场** | 这两个平台的封面/截图本来就是双屏拼接图，下屏整屏展示比掌机的 336×264 媒体框更合适 |
| **市场空位** | Thor 于 2025-10 开售，RG DS 支持 Android/Linux 双系统，品类在扩张，而原生双屏前端**目前空缺** |

---

## 7. 存储与权限（本移植最大的坑）

### 7.1 三种访问模式

| 模式 | 权限 | 能否用 `pathlib` | 遍历性能（3.9k ROM 参照掌机 0.7 s） | 适用 |
|---|---|---|---|---|
| **A. All files access** | `MANAGE_EXTERNAL_STORAGE` | ✅ 真实路径 | 快（与掌机同量级） | **主方案**（侧载分发） |
| **B. SAF 目录授权** | `ACTION_OPEN_DOCUMENT_TREE` + 持久化 | ❌ `content://` URI | **慢 5~20×**，`DocumentFile` 每次查询过 ContentResolver | 兜底（Play 版 / 用户拒绝 A） |
| C. App 私有目录 | 无需权限 | ✅ | 快 | 缩略图 / config / index（**始终用这个**） |

### 7.2 主方案：A + 真实路径

```
rom_root      候选顺序：
  ① config.rom_root（用户选过就固定）
  ② /storage/emulated/0/Roms、/Games、/ROMs、/RetroArch/roms
  ③ /storage/<SD 卡 UUID>/Roms 等外置卡同名目录（多卡 → available_rom_roots()）
  ④ 首启引导页让用户选（走 B 的目录选择器，但只取路径，见 §7.4）
config_dir    getExternalFilesDir(null)/config        # 无需权限、卸载即清、用户可见可备份
cache（缩略图）getExternalFilesDir(null)/thumbnails    # ★ 不写进 ROM 目录，见 §7.3
```

`available_rom_roots()` / `rom_root_label()` **已在基类实现**（掌机 TF1/TF2 双卡就是它），
Android 上返回 `[(内置, "内部"), (外置, "SD 卡")]` 即可，UI 的多卡切换零改动。

### 7.3 缩略图缓存必须换位置（重要差异）

掌机上缩略图写在 `<SYS>/media/covers/.cache/`（跟着卡走，换机不重建）。
Android 上**不能这样**：

| 原因 | 说明 |
|---|---|
| SAF 兜底模式下无法写 | `content://` 树可能只读，或写入需要逐个 `createFile` |
| 外置 SD 卡在 Android 11+ 对 App 写入受限 | 即便有 All-files，某些 ROM 厂设备仍拒写 |
| 用户预期 | Android 用户不接受 App 往自己的媒体目录里塞点文件 |

方案：`ThumbnailCache` 的 root 改为**平台给定**（`Platform.cache_dir`），
Linux 继续返回"媒体目录旁的 `.cache`"，Android 返回 `getExternalFilesDir/thumbnails/<hash>`。
`ThumbnailCache.__init__(platform, root, enabled)` **已经接受 root 参数**，
只需把调用处的 root 来源换成平台属性 —— 又一处预留生效。

> 代价：换设备要重建缩略图。以 §CHANGELOG 0.5.0 的空闲预热（单游戏 4 尺寸 103 ms）算，
> 500 个游戏约 52 s 后台完成，可接受。

### 7.4 SAF 兜底与"Tree URI → 真实路径"

首启引导即使用 `ACTION_OPEN_DOCUMENT_TREE`（用户体验最好、无需申请敏感权限），
拿到 `treeUri` 后**先尝试推导真实路径**：

```
content://com.android.externalstorage.documents/tree/primary%3ARoms
  → /storage/emulated/0/Roms
content://com.android.externalstorage.documents/tree/1A2B-3C4D%3ARoms
  → /storage/1A2B-3C4D/Roms
```

推导成功且 `os.access(path, R_OK)` 通过 → **走 A 模式的快路径**；
失败（如 Downloads provider、云盘 provider）→ 退回 B 模式，`FileEntry` 由
`DocumentFile.listFiles()` 产出，`Game.path` 存 URI 字符串。

### 7.5 B 模式下 `Game.path` 是 URI 的连带影响

这是 SAF 唯一真正扎手的地方，涉及三处：

| 影响点 | 处理 |
|---|---|
| `data/sources/esde.py` 读写 `gamelist.xml` | 用 `ContentResolver.openFileDescriptor` 拿 fd，Python 侧 `os.fdopen`；原子写（`os.replace`）在 SAF 下不可用 → 退化为"写临时 doc + 删旧 + 改名"，并保留 `.bak` |
| ROM 路径传给模拟器 | **必须传 `content://` URI 并 `grantUriPermission`**，因为第三方 App 读不到我们的 fd。RetroArch Android 支持 URI；不支持的模拟器在 B 模式下**禁用启动并提示"需要文件访问权限"** |
| 缩略图键 | 已按 §7.3 移出，无影响 |

> **设计取舍**：B 模式是**降级模式**，明确标注"部分模拟器不可用"。
> 不为了 Play 上架把 A 模式做成二等公民 —— 目标用户是掌机玩家，侧载是常态。

### 7.6 扫描性能预算

| 场景 | 3.9k ROM 首次扫描 | 缓存命中启动 |
|---|---|---|
| 掌机（基线） | 0.7 s | 1.18 s 冷启动 |
| Android A 模式 | 预估 0.4~0.8 s（闪存更快，但 `os.scandir` 过 FUSE 有开销） | ≈ 同上 |
| Android B 模式 | **预估 8~20 s** | 同上（`index.json` 兜住） |

B 模式的缓解手段（都已存在）：`library.scan(cached_only=True)` 先出首屏 +
后台线程真扫（`main.py:_scan_in_background`），所以**慢扫描不阻塞可交互**。

---

## 8. 启动游戏：外部模拟器 + 内联模拟器（双轨，核心统一）

### 8.1 三条执行路径，一个核心概念

**设计基点**：无论游戏在哪里跑，"用哪个 libretro 核心"这件事**只有一份定义** ——
`data/systems.py` 的 `SystemDef.core`（`fceumm_libretro.so`）与 `config.core_overrides`。
三条路径只是同一个核心名的三种**执行位置**：

| 路径 | 执行位置 | 核心名的用法 | 覆盖的平台 |
|---|---|---|---|
| **inline**（内联） | **本 App 内**，自己 dlopen 核心 | 加载 `<cores_dir>/fceumm_libretro_android.so` | 8/16 位、掌机、街机、PS1、N64、NDS…… |
| **external-ra** | RetroArch App | Intent extra `LIBRETRO=fceumm_libretro_android.so` | 同上（用户已有配置时） |
| **external-app** | 独立模拟器 App | 不用核心 | PS2 / GC / Wii / 3DS / PSP 等重型平台 |

命名差异必须收在一处：Linux 是 `<core>_libretro.so`，Android 是 `<core>_libretro_android.so`。

```python
# data/systems.py —— 新增，供三条路径共用
def core_basename(core: str) -> str:
    """``fceumm_libretro.so`` -> ``fceumm``.  The stable identity of a core."""

def core_filename(core: str, *, suffix: str = "") -> str:
    """``fceumm`` + suffix ``_android`` -> ``fceumm_libretro_android.so``."""
```

平台提供后缀（`LinuxPlatform.core_suffix = ""` / `AndroidPlatform.core_suffix = "_android"`），
**`SystemDef` 一个字都不用改** —— 这是"核心统一"能成立的关键。

### 8.2 `auto` 决策链

新增配置维度 `launch_mode`，三级覆盖（与既有 `core_overrides` 完全同构）：

```json
{
  "launch_mode": "auto",
  "launch_modes": { "PS2": "external-app", "FC": "inline", "SFC": "external-ra" }
}
```

单游戏覆盖走 gamelist 的 `<command>`（Pegasus 也有 `command` 字段，§6.8.5 已映射）。

`auto` 的判定顺序（实现在 `launcher/`，平台无关）：

```
① 该平台在 launchers.android.json 里标了 prefer_external（重型平台）
     且对应 App 已安装                          → external-app
② 该平台的核心已下载到 <cores_dir>              → inline        ★ 默认首选
③ RetroArch 已安装                              → external-ra
④ 核心可下载                                    → 提示"下载核心 (2.1 MB)"，确认后 inline
⑤ 都不行                                        → 提示"安装模拟器"，给商店/忽略
```

"App 是否安装 / 核心是否存在"是平台知识，通过一个查询接口回答，
决策本身留在平台无关的 `launcher/` 里：

```python
# platform/base.py
@dataclass(frozen=True)
class LauncherAvailability:
    """What this device can actually run right now."""
    inline_cores: frozenset[str] = frozenset()      # 已就位的核心 basename
    installed_packages: frozenset[str] = frozenset()
    can_download_cores: bool = False

class Platform(abc.ABC):
    def launcher_availability(self) -> LauncherAvailability:
        """Default: nothing inline, nothing installed -- so Linux keeps its
        script-based path untouched."""
        return LauncherAvailability()
```

> **为什么 inline 优先于 external-ra**：内联不需要用户装第二个 App、启动快、
> 存档与按键由我们统一管，而且**双屏机上只有内联能做到真双屏**（§8.9）。
> 但已经配好 RetroArch 的老用户可以在设置里把默认改成 `external-ra`，一行配置的事。

### 8.3 外部 · RetroArch（覆盖面最广）

```
package  : com.retroarch.aarch64        （32 位机型：com.retroarch）
activity : com.retroarch.browser.retroactivity.RetroActivityFuture
extras   : ROM        = <ROM 绝对路径 或 content:// URI>
           LIBRETRO   = <core.so 文件名 或 绝对路径>   ← 见下
           CONFIGFILE = /storage/emulated/0/Android/data/com.retroarch.aarch64/files/retroarch.cfg
           IME        = <可选，输入法>
           DATADIR    = <可选，/data/data/<pkg>>
```

**必须处理的兼容坑**（2025-01-17 起的 nightly 回归，后续已修但发布版本参差）：
只传 `LIBRETRO=mesen_libretro_android.so` 时部分版本报
`Frontend is built for dynamic libretro cores, but path is not set`，
必须给**全路径** `/data/data/com.retroarch.aarch64/cores/xxx.so`。

因此 `IntentTarget.fallbacks` 的设计（§5.3）在这里派上用场：

```python
IntentTarget(
    package="com.retroarch.aarch64", activity="...RetroActivityFuture",
    extras=(("ROM", rom), ("LIBRETRO", core_name), ("CONFIGFILE", cfg)),
    fallbacks=(
        # 同一个 Intent，核心换成全路径；第一次失败（Activity 立刻返回 / 3s 内无前台切换）就试它
        IntentTarget(..., extras=(("ROM", rom),
                                  ("LIBRETRO", f"/data/data/{pkg}/cores/{core_name}"),
                                  ("CONFIGFILE", cfg))),
    ),
)
```

判定"失败"的信号：`startActivityForResult` 立即回 `RESULT_CANCELED`，
或 3 s 内本 Activity 未进入 `onPause` —— 后者更可靠，因为 RetroArch 崩退时会秒回。

### 8.4 外部 · 独立模拟器表

与 `data/systems.py` 的 `standalone`（Linux 脚本）**平行**，但**不塞进 `SystemDef`**：
Android 的启动器是"设备上装了什么"决定的，属于运行环境，不属于平台定义。

新增配置文件 `launchers.android.json`（随包内置默认值，`config_dir` 下可覆盖，
与 `systems.json` 的用户覆盖机制完全同构）：

```json
{
  "NDS":   {"package": "com.dsemu.drastic",       "action": "VIEW", "mime": "*/*"},
  "3DS":   {"package": "org.citra.citra_emu",     "activity": "org.citra.citra_emu.activities.EmulationActivity",
            "extras": {"SelectedGame": "{rom}"}, "prefer_external": true},
  "PSP":   {"package": "org.ppsspp.ppsspp",       "action": "VIEW", "prefer_external": true},
  "PS2":   {"package": "xyz.aethersx2.android",   "extras": {"bootPath": "{rom}"}, "prefer_external": true},
  "WII":   {"package": "org.dolphinemu.dolphinemu","activity": "org.dolphinemu.dolphinemu.ui.main.MainActivity",
            "extras": {"AutoStartFile": "{rom}"}, "prefer_external": true},
  "PORTS": {"unsupported": "android"}
}
```

解析规则：
1. `prefer_external: true` 且 App 已安装 → 直接用它（重型平台上独立模拟器性能远超对应
   libretro 核心：AetherSX2 ≫ pcsx2 core、Azahar ≫ citra core、PPSSPP 独立版 ≫ ppsspp core）
2. 平台键命中但 App 未安装 → 回到 §8.2 决策链的 ② （内联）
3. 都没有 → 提示"未检测到模拟器"，给"打开应用商店 / 下载核心内联运行 / 忽略"
4. `"unsupported"` → 该平台在 Android 上隐藏（`PORTS` 是 Linux shell 脚本，无意义）

### 8.5 外部 · 通用回退：ACTION_VIEW

未在表内、又想让用户自己选 App 的场景：

```
Intent(ACTION_VIEW) + setDataAndType(uri, "application/octet-stream")
  + FLAG_GRANT_READ_URI_PERMISSION
  → createChooser
```

首发**不做**这条（体验差、易误选），列在此处备用。

### 8.6 内联 · libretro 宿主（Retrostation 从"前端"变成"前端 + 宿主"）

#### 8.6.1 为什么要做内联

| 收益 | 说明 |
|---|---|
| **开箱即用** | 不必装 RetroArch、不必配 `retroarch.cfg`、不必在两个 App 的设置之间来回跑 |
| **启动快** | 无跨 App 冷启动（RetroArch Android 的 APK 有 194 MB，冷启动数秒），内联是进程内加载一个 1~3 MB 的 `.so` |
| **体验统一** | 按键映射、存档管理、核心选项、退出行为全部由前端统一，退出即回原位 |
| **★ 双屏原生** | **只有内联才能把 NDS/3DS 的下屏真正送到副屏**（§8.9）。RetroArch Android 只能把双屏拼进一块屏；实测 MelonDS 要手动配"内屏只留上屏 + 外显屏幕 = 下屏"，DraStic 的下屏触控还不可用 |
| 存档缩略图 | 内联时能拿到 framebuffer → 存档槽配截图，做成"存档墙"（前端本来就是干这个的） |

#### 8.6.2 技术基座：LibretroDroid

| 项 | 结论 |
|---|---|
| 库 | [`Swordfish90/LibretroDroid`](https://github.com/Swordfish90/LibretroDroid) —— C++ libretro 宿主 + `GLRetroView` |
| 生产验证 | 驱动 **Lemuroid**（知名开源 Android 全能模拟器） |
| 依赖方式 | JitPack：`implementation 'com.github.swordfish90:libretrodroid:<version>'` |
| **许可** | **GPL-3.0** → 见 §8.10 的许可影响，**这是必须先定的事** |
| 已具备 | 2D 核心、GL 核心、音频、手柄事件、**游戏状态存读档**、**SaveRAM 存读**、CRT/LCD 滤镜、**触摸屏**、多碟切换、**核心变量（core options）** |
| 已验证核心 | Stella / Gambatte / mGBA / Mupen64Plus / Snes9x / QuickNES / fceumm / nestopia / PPSSPP / fbneo / picodrive / Genesis Plus GX / **DeSmuME** / PCSXReARMed |
| **缺** | **没有多 Surface / 双屏输出** → §8.9 需要 fork |

> **不自己写 libretro 宿主**：libretro API 的 environment callback 有几十个分支，
> 音视频同步、GL 上下文管理、存档格式都是坑。LibretroDroid 已经把这些做完并被
> 生产验证，269 次提交。自己写等于把 3 个月花在与本项目价值无关的地方。

#### 8.6.3 Python 侧抽象

`LaunchTarget` 增加第三种（承接 §5.3 的泛化）：

```python
@dataclass(frozen=True)
class InlineTarget:
    """Run the game inside this app, on a libretro core we host ourselves."""
    core: str                                  # basename: "fceumm"
    rom: str
    system: str
    #: Where SaveRAM / states / BIOS live.  Resolved by the platform so the
    #: UI never learns Android's directory layout.
    save_slot: str = ""
    #: Split the core's framebuffer across two screens (NDS/3DS on a dual-screen
    #: handheld).  A request, not a demand: the platform ignores it when it has
    #: only one screen or the core is single-screen (§8.9).
    dual_screen: bool = False
    #: libretro core variables, e.g. (("desmume_screens_layout", "top/bottom"),)
    options: tuple[tuple[str, str], ...] = ()

LaunchTarget = ArgvTarget | IntentTarget | InlineTarget
```

`Platform.launch_game(target)` 分派；`LinuxPlatform` 只认 `ArgvTarget`（**掌机零影响**）。

> 顺带一提：Linux 掌机将来也可以内联（`/oem/retro/cores/*.so` 就在那里），
> 但没必要 —— `RA_launch.sh` 已经很好用。抽象允许，路线上不做。

#### 8.6.4 运行时架构

```
┌ GameActivity（内联专用 Activity）──────────────────────┐
│  ┌ GLRetroView（LibretroDroid，GL 渲染，下层）──────┐   │
│  │            游戏画面 · 60 fps · C++ 线程          │   │
│  └────────────────────────────────────────────┘   │
│  ┌ OverlayView（普通 View，上层，平时完全透明）──────┐   │
│  │   Python 绘制的游戏内菜单 / 提示 / 存档槽          │   │
│  └────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────┘
        ▲ 输入：InputBridge 先给 Python，菜单未开则转发给 GLRetroView
```

要点：

1. **独立 `GameActivity`**，不塞进 `MainActivity`：生命周期独立、核心崩溃不带崩前端、
   `startActivityForResult` 回来天然是"游戏退出"信号 —— 与 §8.11 的外部路径**共用同一条返回逻辑**。
2. **Python 前端不退出，只让位**：`suspend_display()` 已有；游戏运行时 Python 主循环
   降到低频（菜单未开时不绘制），CPU 几乎为零。
3. **游戏内菜单是 Python 的一个新 screen**（`ui/screens/ingame.py`），复用现有
   widgets 与主题 —— **不用 Kotlin 再写一套 UI**。这是"让 Python 活着"的最大理由。
4. 输入优先级：`InputBridge` 拿到事件先问 Python "菜单开着吗"；
   开着 → Python 消费；没开 → 转发给 `GLRetroView`。
   热键（如 `START` 长按 / `MENU`）永远由 Python 先截。

#### 8.6.5 核心与 BIOS 管理

**核心来源**：libretro 官方 buildbot（RetroArch 自己也从这里下载，仍在日更）：

```
https://buildbot.libretro.com/nightly/android/latest/arm64-v8a/<core>_libretro_android.so.zip
```

体积很小（实测该目录：`wasm4` 59 KB、`x1` 162 KB，重型的 fbneo/mame 几 MB）。

| 决策 | 理由 |
|---|---|
| **不把核心打进 APK** | ① 体积（几十个核心 = 上百 MB）；② 许可（核心各自的 GPL/非商业条款，随包分发要逐个核对）；③ 更新（核心比前端更新快） |
| **App 内做核心管理器** | 设置页新增"核心管理"：按平台列出所需核心 → 下载 / 更新 / 删除 / 查看版本；首次进入某平台时按需提示 |
| 存放位置 | `getExternalFilesDir()/cores/arm64-v8a/`（App 私有，无需权限，卸载即清） |
| 离线可用 | 允许用户从 PC 手动拷 `.so` 进该目录，管理器扫描到就认 |
| **不复用 RetroArch 的核心** | `/data/data/com.retroarch.aarch64/cores/` 无 root **不可读**，这条路走不通 |

**BIOS**：内联绕不开（PS1 / Saturn / 部分街机 / NDS 的 DSi 模式）。

```
<config_dir>/system/            ← libretro 的 system dir，交给核心
```

维护一张"平台 → 必需/可选 BIOS + 校验和"清单（随包，可覆盖），
在游戏详情与启动前给出**明确缺失提示**（"缺 scph5501.bin"），
而不是让核心静默黑屏 —— 这正是前端该干的活。

#### 8.7 存档设计

| 类型 | 位置 | 说明 |
|---|---|---|
| SaveRAM（电池存档 `.srm`） | `<config_dir>/saves/<SYSTEM>/<rom>.srm` | LibretroDroid 提供序列化接口；退出与切后台时落盘 |
| 状态快照 | `<config_dir>/states/<SYSTEM>/<rom>.state<N>` | N 个槽位（默认 4）+ 每槽一张 **PNG 截图**与时间戳 |
| 自动存档 | `.state0` 视为 auto 槽 | `onPause` 自动存，下次启动提示"继续上次" |

> **无法与 RetroArch 共享存档**：Android 11+ 禁止跨 App 访问
> `Android/data/<pkg>/`，所以 RA 的 `saves/` 我们读不到，反之亦然。
> 设计上**不假装能共享**，而是提供"导出存档到 ROM 目录旁"的显式动作
> （写到 `<SYS>/saves/`，用户可自行拷给 RA）。含糊其辞比缺功能更糟。

#### 8.8 游戏内菜单（`ui/screens/ingame.py`）

由 Python 绘制在 OverlayView 上，复用现有 widgets：

```
START 长按 / MENU  →  半透明覆盖层
  ┌──────────────────────────┐
  │  继续游戏                  │
  │  存档 ▸  [4 个槽 + 截图]    │
  │  读档 ▸                    │
  │  快进    ×1 / ×2 / ×4      │
  │  核心选项 ▸（core variables）│
  │  重置                      │
  │  退出到前端                 │
  └──────────────────────────┘
```

- **核心选项**直接读 LibretroDroid 暴露的 core variables，动态生成菜单项 ——
  不需要为每个核心手写 UI，与 `systems.json` 的"配置驱动"哲学一致
- 双屏机上菜单画在**副屏**，游戏画面完全不被遮挡（§8.9）

#### 8.9 ★ 内联 + 双屏 = 无可替代的差异化

这是内联最有价值的部分，也是本项目在 Android 上**唯一别人做不到的事**。

**NDS / 3DS 核心的 framebuffer 本来就是上下拼接的**（DeSmuME 输出 256×384 = 两个
256×192；citra 类似）。RetroArch Android 只能把它整块塞进一块屏，
所以双屏机用户得手动折腾"只显示上屏 + 外接屏显示下屏"。

我们的做法：

```
core framebuffer 256×384
    ├─ 上半 256×192  ──texture UV 裁剪──▶  上屏 GLRetroView (Display 0)
    └─ 下半 256×192  ──texture UV 裁剪──▶  副屏 GLRetroView (Display 1)
                                              ↑ 副屏触摸 → 注入 core 的触摸输入
```

实现路径：**fork LibretroDroid**，在其 GL 渲染层增加"第二个 EGLSurface + 可配置 UV 区域"。
它本身已经是 C++/OpenGL 渲染同一个 texture，加第二个输出目标属于**中等工作量的扩展**，
不是重写。

| 平台类型 | 上屏 | 副屏 |
|---|---|---|
| **双屏核心**（NDS、3DS） | 游戏上屏 | **游戏下屏 + 触摸**（真 NDS 体验） |
| 单屏核心（FC/SFC/GBA/PS1…） | 游戏画面 | **前端信息面板**：封面 / 存档槽 / 核心信息 / 按键提示（Python 绘制） |

> 第二行同样有价值：玩 FC 时下屏放着这个游戏的封面与存档槽，
> 这是掌机 Linux 版做不到的（游戏一起来前端就退出了），Android 内联反而更强。

**副屏触摸注入**：LibretroDroid 已支持 `Touchscreen`，把副屏的 `MotionEvent`
按 UV 映射回 core 坐标即可 —— 这正好解决实测里"DraStic 下屏触控不可用"的痛点。

#### 8.10 许可影响（**必须先决策**）

| 组件 | 许可 | 影响 |
|---|---|---|
| LibretroDroid | **GPL-3.0** | 集成后 **Retrostation 整体必须 GPL-3.0 兼容** |
| libretro 核心 | 各自不同（多为 GPL，个别禁止再分发） | **不随包分发**（§8.6.5 的下载器方案在许可上也是最稳的） |
| Chaquopy | 开源项目免费；**闭源商用需付费** | 与 GPL 路线一致：必须开源分发 |

**结论与行动项**：

1. 项目需要**明确添加 `LICENSE`（建议 GPL-3.0）** —— 当前仓库根目录没有许可文件，
   而集成 LibretroDroid 后这不再是可以往后放的事；
2. 若将来想闭源上架，内联路径必须换成自研宿主（工作量 +3 个月），
   或**只保留外部路径**（Intent 调用不构成衍生作品）；
3. 因此建议：**内联与外部两条路径在代码上保持可分离**（`InlineTarget` 的实现全在
   `platform/android/inline/` 与 Kotlin 的 `GameActivity` 里），
   万一许可路线变更，砍掉内联不影响其余部分。

### 8.11 返回前端

**三条路径共用同一条返回逻辑**（内联的 `GameActivity` 也是 `startActivityForResult` 拉起的）：

| 机制 | 说明 |
|---|---|
| `startActivityForResult(intent, REQ_GAME)` | 拿到 `onActivityResult` 就知道游戏退出了；内联与外部**完全同构** |
| 不依赖 result code | 外部模拟器基本都返回 `RESULT_CANCELED`，只当"回来了"的信号用；内联可以额外回传游玩时长 |
| 现场恢复 | 复用 `state.json["resume"]`（**Android 不需要它**——进程没死、UI 状态还在内存里；但仍写，便于被系统杀后恢复） |
| 游玩统计 | `on_resume()` 里 `play_count += 1`、`last_played = now`，写回 gamelist —— 与掌机同一段代码 |

> **Android 天然走"常驻"路径**（`can_stay_resident() → True`），
> 不需要掌机那套"退出码 42 + shell 自举"。这是 Android 端反而更简单的地方。

---

## 9. 视频预览与音频

### 9.1 不要把视频解码进画布

掌机上是 `ffmpeg → rawvideo 管道 → PIL 帧 → 画进画布`，因为那台机器没有硬解、也没有
合成器可用的视频面板。Android 上照搬是**双重浪费**：硬解出来的帧要拷回 CPU、缩放、再随整帧上传。

### 9.2 方案：媒体窗口（Surface 叠层）

```
┌ RetroSurfaceView（UI 帧，R1 的 Bitmap）───────────────┐
│  ┌ 媒体框（Python 告知的矩形）───────┐                │
│  │  ← 这里画布留空（透明洞）          │                │
│  └──────────────────────────────┘                │
└──────────────────────────────────────────────────┘
      ▲ 下方叠一个 PlayerView（ExoPlayer），按媒体框矩形定位
```

Python 侧新增 `VideoPipe.external`：

```python
class VideoPipe(abc.ABC):
    #: True when frames are composited by the platform directly onto the screen
    #: (Android's ExoPlayer surface) instead of being handed to the UI.
    #: ``read_frame()`` then returns None forever and the UI leaves the media
    #: box empty instead of drawing a cover over the video.
    external: bool = False
```

**`data/video.py` 的 `VideoPlayer` 完全复用**——250 ms 防抖、fps 节流、3 s 无帧降级、
黑名单、切换时后台收尾、启动游戏前 `close()`，这些逻辑与解码器实现无关，
只是 `frame()` 恒返回 `None` 而 `is_playing()` 仍为真。

`AndroidPlatform.open_video_pipe()` 返回的 `SurfaceVideoPipe`：
- 构造时把 `(path, 媒体框矩形)` 交给 `MediaBridge`，ExoPlayer `prepare + play`（静音由 `set_volume` 控制）
- `external = True`，`read_frame()` 返回 `None`
- `duration` 由 ExoPlayer 给（比掌机的 `ffprobe` 更便宜）
- `close()` → `player.stop()`；幂等

UI 侧唯一改动：`ui/screens/bottom.py`（及单屏详情条）在
`video.is_playing() and pipe.external` 时**跳过媒体区绘制**，只画边框与进度条。

### 9.3 备选：MediaCodec 抽帧（Plan B）

若"透明洞 + Surface 定位"在某些 ROM/厂商上翻车（SurfaceView 层级问题常见），
退回 `MediaCodec → ImageReader → Bitmap → JNI → PIL 帧` 的 `VideoPipe`，
此时 `external = False`，走与掌机完全相同的路径。性能差但**必定能画出来**。
> 该退路的存在是保留 `VideoPipe` 抽象的正当理由，不是过度设计。

### 9.4 音频

| 用途 | Android 实现 | 复用 |
|---|---|---|
| 预览音轨 | ExoPlayer 自带音轨 + `setVolume` | `AudioPipe` 接口现成，`set_volume` 已在基类 |
| 按键音效 | `SoundPool`（三个短音，Kotlin 侧用 `AudioTrack` 合成或打包 3 个 wav） | `play_sfx(kind)` / `configure_sfx` 已在基类 |
| 让位 | `onPause` 里 `release_sfx()` + ExoPlayer `stop` | 已在基类且掌机已用 |

> 掌机上音效是 ALSA 现场合成（不带音频文件）；Android 上可以直接内置 3 个几 KB 的 wav，
> 更省事。这属于平台实现细节，接口不变。

---

## 10. 输入与交互

### 10.1 三类输入源

| 源 | 事件 | 目标形态 |
|---|---|---|
| 实体按键（安卓掌机 / 蓝牙手柄 / USB 手柄） | `KeyEvent` + `MotionEvent`(AXIS_HAT/AXIS_X) | 掌机 P0、手机可选 |
| 触摸 | `MotionEvent` → `GestureDetector` | 手机 P0 |
| 屏幕虚拟键 | 画在画布里的半透明按键 → 命中测试转语义事件 | 手机 P1（无手柄时的兜底） |

### 10.2 KeyEvent → InputAction 映射

```python
# platform/android/input.py
KEYMAP = {
    KEYCODE_DPAD_UP: UP, KEYCODE_DPAD_DOWN: DOWN,
    KEYCODE_DPAD_LEFT: LEFT, KEYCODE_DPAD_RIGHT: RIGHT,
    KEYCODE_BUTTON_A: A, KEYCODE_DPAD_CENTER: A, KEYCODE_ENTER: A,
    KEYCODE_BUTTON_B: B, KEYCODE_BACK: B,          # ★ 见下
    KEYCODE_BUTTON_X: X, KEYCODE_BUTTON_Y: Y,
    KEYCODE_BUTTON_L1: L1, KEYCODE_BUTTON_R1: R1,
    KEYCODE_BUTTON_L2: L2, KEYCODE_BUTTON_R2: R2,
    KEYCODE_BUTTON_START: START, KEYCODE_MENU: START,
    KEYCODE_BUTTON_SELECT: SEARCH,
    KEYCODE_VOLUME_UP: VOLUME_UP, KEYCODE_VOLUME_DOWN: VOLUME_DOWN,
    KEYCODE_BUTTON_MODE: MENU,                      # 长按 = 退出
}
```

坑与对策：

| 坑 | 对策 |
|---|---|
| **双屏机上手柄事件只到有焦点的那块屏** | 主屏 Activity 与副屏 Presentation **都装 `onKeyDown` / `onGenericMotionEvent`，都汇入同一队列**（§6.4.3 坑 1）。这是双屏机上最容易漏、又最致命的一条 |
| **`KEYCODE_BACK` 语义冲突**：系统返回键 = B（返回上一层），但在平台页按 B 应该退出 App | 平台页收到 B 且无处可退 → 交还系统（`super.onKeyDown`），符合 Android 习惯；不要把"长按退出"当唯一出口 |
| 音量键被系统吞掉 | Activity 里拦截 `KEYCODE_VOLUME_*` 并 `return true`（掌机上前端也是这么接管的） |
| 手柄摇杆当方向键 | `MotionEvent` 的 `AXIS_HAT_X/Y` + `AXIS_X/Y` 做死区（0.5）与边沿检测，产出 PRESS/RELEASE |
| **自动重复** | **在 Kotlin 侧不做**，让 Python 侧统一合成——掌机与 desktop 各有一份合成逻辑（`_REPEAT_DELAY=0.40 / _REPEAT_RATE=0.08 / _LONG_PRESS=0.80`），Android 抄 desktop 那份即可，节奏一致 |

### 10.3 触摸手势 → 语义

| 手势 | 事件 | UI 行为 |
|---|---|---|
| 单击列表行 / 网格卡 / 轮播卡 | `TAP(x, y, screen=0)` | 未选中 → 选中；已选中 → 启动（"二次确认"式，避免误触启动） |
| **双屏机：点副屏媒体区 / Logo 条** | `TAP(screen=1)` | **启动当前游戏** —— DESIGN §5 设计过但掌机上从未落地的下屏触摸，Android 上免费拿到 |
| **双屏机：副屏上下滑** | `DRAG(dy, screen=1)` | 滚动上屏列表（下屏当触摸板用） |
| 单击详情区媒体框（单屏） | `TAP(screen=0)` | 启动当前游戏 |
| 垂直拖动 | `DRAG(dy)` | 列表滚动（按 `row_step` 换算索引） |
| 快速滑动 | `FLING(dy)` | 惯性滚动（Kotlin 侧算速度，Python 侧按衰减推进） |
| 水平滑动（轮播 / 平台页） | `DRAG(dx)` | 切卡 |
| 长按 | `TAP` + `LONG_PRESS` | 打开上下文菜单（收藏 / 隐藏 / 详情） |
| 双指 / 边缘返回 | 系统手势 | 交还系统 |

> **原则不变**：全部功能必须能纯按键完成（DESIGN §5 已确立），触摸是增强。
> 反过来在手机上则要求：**全部功能必须能纯触摸完成** —— 这条新要求落在
> "屏幕虚拟键"（P1）和"上下文菜单"上，其中菜单入口已存在（START → 设置菜单）。

### 10.4 首发的键位提示

掌机上有 `ButtonBar` 画 Ⓐ开始 Ⓑ返回…… 手机上没有实体键，`ButtonBar` 要改成
**触摸按钮条**（同一个矩形，加命中测试即可，不必换视觉）。这是 R-D 的直接受益。

---

## 11. UI 布局适配

### 11.1 四种形态的分区

**PORTRAIT（手机竖屏，逻辑约 560×1248）**

```
┌──────────────────────────────┐ 0
│ 状态栏 u(28)   12:04 🔋87% 内部 │
├──────────────────────────────┤
│ 页头 u(44)  ▍红白机 515 个·列表 │
├──────────────────────────────┤
│                              │
│ 内容区 ≈ 60%（列表 ~13 行 /    │
│ 网格 3×5 / 轮播）              │
│                              │
├──────────────────────────────┤ ← detail_box() 上沿
│ 详情区 ≈ 40%                  │
│  ┌────────┐ 名称 / ★4.0      │
│  │ 媒体框  │ 类型 · 人数        │
│  │ 视频/封面│ 发布 · 核心        │
│  └────────┘ 简介（跑马灯）      │
├──────────────────────────────┤
│ 触摸按钮条 u(30)                │
└──────────────────────────────┘
```

**WIDE（平板 / 折叠屏展开，逻辑约 1120×630）** = DESIGN §11 方案 B：
左 65% 内容（网格 5×3）+ 右 35% 详情卡（上媒体框、下元数据），
状态栏与按钮条通栏。

**COMPACT（单屏安卓掌机横屏 16:9）**：与 TrimUI 现状一致（列表 + `u(118)` 详情条），
**零新增布局代码**。

**DUAL（双屏安卓掌机）**：与 RG DS Linux 版**完全同一套布局**——上屏平台轮播/列表/网格/轮播，
下屏媒体区 + Logo 条 + 元数据卡 + 情境提示（DESIGN §7.2）。**零新增布局代码**。

唯一需要注意的是**异构双屏下的比例差异**：

| | RG DS（Linux / Android） | Thor 上屏 | Thor 下屏 |
|---|---|---|---|
| 物理 | 640×480（4:3） | 1920×1080（16:9） | 1240×1080（≈3.44:3） |
| 逻辑参考空间（÷ scale） | 640×480 | 640×**480**（16:9 → 高度富余被 `min()` 吃掉，实际 852×480 的参考空间） | 640×**557** |

- **上屏 16:9**：`scale` 由高度决定，宽度富余 → 列表行更宽、网格自动变 5~6 列
  （`grid_cols` 已按 `spare` 自适应，`_clamp(round(4 * spare), 3, 6)`），**无需干预**
- **下屏 3.44:3**：比 4:3 更方，垂直富余 33 px 参考像素 → 媒体框与元数据卡各自变高一点，
  `media_h = u(264)` 与 `bottom_body_h()` 都是派生值，**自动吸收**

> 结论：**异构双屏不需要新布局分支**，只需要在 A2 用截图核对一遍不破版。
> 这是 `Metrics` 全比例化（E2）带来的直接回报。

### 11.2 密度与可读性

| 项 | 掌机 640×480 | 手机竖屏 560×1248 | 说明 |
|---|---|---|---|
| `scale` | 1.0 | `min(560/640, 1248/480) = 0.875` | 由**宽度**决定，符合直觉 |
| 行高 | 34 px | 30 px 逻辑 → **物理 58 px ≈ 15 dp** | 略小于 Material 建议的 48 dp 触摸目标 |
| 触摸目标 | — | **需要放大**：`PORTRAIT` 下 `row_h` 用 `u(34) * 1.5` | 见下 |
| 字号（列表行 16） | 16 px | 14 逻辑 → 27 物理 ≈ **14 sp** | 可读 |

**结论**：`Metrics` 需要一个 `touch: bool`（或直接看 `form`），
在 `PORTRAIT` / `WIDE` 下把 `row_h` / `bar_h` / 按钮命中框放大到 ≥ 48 dp 等效值。
这是 R-E 的一部分，**不新增布局分支，只调 token 系数**。

### 11.3 平台艺术与媒体比例

无需改动：平台背景 1024×1024、Logo 820×330 的资源已随包（`assets/platforms/`），
`platform_art` / `platform_logo_h` 都是 `u()` 派生的，竖屏下自动变小。
封面缩略图尺寸档位（轮播 4 档 + 网格 + 列表 + 详情条 + 2 档 Logo，共 8 张）
在 Android 上会算出不同像素值 → **缓存 key 已含尺寸**（`_entry_digest(source, w, h, mtime)`），
不会与掌机的缓存冲突。

---

## 12. 打包与分发

### 12.1 Gradle / Chaquopy 配置要点

```kotlin
// app/build.gradle.kts
android {
    compileSdk = 36
    defaultConfig {
        minSdk = 26                     // Android 8.0：覆盖几乎所有安卓掌机
        targetSdk = 36
        ndk { abiFilters += listOf("arm64-v8a") }   // 只出 64 位，包体减半
    }
}
chaquopy {
    defaultConfig {
        version = "3.13"                // 16 KB page 兼容性建议 3.13+
        pip { install("Pillow") }       // R1 需要；R2 可去掉
        pyc { src = false }             // 与掌机分发策略一致：不预编译字节码
        extractPackages("retrostation") // 需要按真实路径读 assets/lang、assets/platforms
    }
    sourceSets { getByName("main").srcDir("../../src") }   // ★ 复用同一份内核
}
```

### 12.2 包体估算

| 组成 | 大小 |
|---|---|
| Chaquopy runtime + Python 3.13（arm64） | ~10 MB |
| Pillow（arm64 wheel，R1） | ~3 MB |
| 内核 Python 源码 | ~1 MB |
| `assets/platforms`（55 png + 51 webp） | ~6 MB |
| 思源黑体子集（**必须自带**，见下） | ~5 MB（子集化后） |
| ExoPlayer（media3） | ~2 MB |
| **合计（仅外部路径）** | **约 27~30 MB** |
| + LibretroDroid native（内联，B 系列） | ~2~3 MB |
| + libretro 核心 | **0 MB** —— 不打进 APK，按需下载到私有目录（§8.6.5） |
| **合计（含内联）** | **约 30~33 MB** |

> **字体必须自带**：掌机上用系统 `/usr/share/fonts/source-han-sans-cn/`，
> Android 没有可直接给 PIL 用的 TTF 路径（系统字体是 `NotoSansCJK.ttc`，
> 位于 `/system/fonts/`，可读但 `.ttc` 索引在 Pillow 上有兼容性问题）。
> 稳妥做法：打包思源黑体 CN 的**常用字子集**（GB2312 + 常见符号，约 5 MB）
> 到 assets，`FontBook` 的候选目录加一条 Android 分支 —— `FontBook(font_dirs)`
> **已接受目录参数**（TrimUI 移植时就是这么补的），零接口改动。

### 12.3 分发

| 渠道 | 可行性 |
|---|---|
| GitHub Release（APK 侧载） | ✅ 首发唯一渠道，与掌机 zip 包同一套发布流程 |
| F-Droid | ⚠️ 需可复现构建；Chaquopy 的 pip 阶段联网，较麻烦。**但 GPL-3.0（内联路线）与 F-Droid 天然契合**，值得后续投入 |
| Google Play | ❌ 首发不做：`MANAGE_EXTERNAL_STORAGE` 需要审批（"文件管理器"类目才可能过），ROM 场景敏感，且 GPL-3.0 与 Play 的分发条款有已知摩擦 |

新增打包脚本 `scripts/package_android.py`（与 `package_trimui.py` 同构）：
清 `__pycache__` → 同步 `src/` → `gradlew assembleRelease` → 产出
`dist/Retrostation-<版本>-android-arm64.apk`。

---

## 13. 里程碑

| 阶段 | 交付物 | 验收标准 | 预估 |
|---|---|---|---|
| **A0** | Chaquopy 可行性实验（§3.2） | 空工程在 Android 15+/16 KB 真机上 `import PIL` 成功并能 `Image.new`；否则判定走 R2 | 2 天 |
| **A1** | §5 前置重构 R-A ~ R-E | 掌机 / TrimUI / desktop 三处行为与截图不变；`pytest` 全绿；掌机帧率不回退 | 1.5 周 |
| **A2** | Kotlin 宿主骨架 + `AndroidPlatform` 最小可跑 + **双屏探测（§6.4.2）** | 真机启动 → 扫到 ROM 库 → 平台页出卡片 + 背景图 → 手柄可导航；**RG DS Android 上双屏同时出画面**；Thor 上异构双屏不破版（截图核对） | 1.5 周 |
| **A3** | 存储与权限（§7 A 模式 + SAF 兜底 + 引导页） | 内置存储与 SD 卡两处 ROM 库都能扫；拒绝权限时走 SAF 且明确降级提示 | 1 周 |
| **A4** | 启动 Intent（§8 RetroArch + 4 个独立模拟器 + fallback）+ **副屏让位（§6.4.4）** | 启动 FC/GBA/PSP/NDS 各一款，退出回到原位且 `playcount` +1；**双屏机上模拟器能正常占用副屏**（MelonDS 双屏 / Azahar 双屏实测） | 1 周 |
| **A5** | 视频预览（§9 Surface 叠层）+ 音频 + 音效 + **双屏输入焦点与热插拔（§6.4.3）** | 副屏 30 fps 有声预览；**点副屏任意位置后手柄仍可用**；合盖/切"仅上屏"能走 `EXIT_RESTART_UI` 优雅重来 | 1.5 周 |
| **A6** | 触摸交互（§10.3，含**副屏触摸**）+ PORTRAIT/WIDE 布局打磨 + 打包脚本 | 纯触摸完成"选平台 → 选游戏 → 启动"全流程；双屏机上点下屏媒体框即启动；APK ≤ 35 MB | 1.5 周 |
| A7（可选） | R2 Skia Canvas | 原生分辨率 60 fps；与 R1 可用配置切换 | 2 周 |
| A8（可选） | 旋转 / 折叠动态适配（走 `EXIT_RESTART_UI`） | 旋转后布局正确、选中项不丢 | 3 天 |

**B 系列 · 内联模拟器**（依赖 A2；可与 A5/A6 并行，但**必须先定许可**，见 §8.10）

| 阶段 | 交付物 | 验收标准 | 预估 |
|---|---|---|---|
| **B0** | 许可决策 + LibretroDroid 集成实验 | 仓库确定并加入 `LICENSE`；demo 用 fceumm 跑起一个 FC ROM，有声有画能操作 | 3 天 |
| **B1** | 核心管理器（§8.6.5）+ `InlineTarget` + `GameActivity` 打通 | 从前端选 FC/GBA/SFC 游戏 → 按需下载核心 → 内联启动 → 退出回原位、`playcount` +1；SaveRAM 正确保存 | 1.5 周 |
| **B2** | 游戏内菜单（§8.8，Python overlay）+ 状态存读档 + 核心选项 | 4 个存档槽带截图；核心选项由 core variables 动态生成；快进可用 | 1.5 周 |
| **B3** | ★ **内联双屏**（§8.9，fork LibretroDroid 加第二 EGLSurface + UV 裁剪）+ 副屏触摸 | **NDS 游戏上屏显上屏、副屏显下屏且副屏可触控**；单屏核心时副屏显示前端信息面板 | 2 周 |
| **B4** | BIOS 管理与缺失提示 + `auto` 决策链 + 设置 UI（§8.2） | 缺 BIOS 时给出明确文件名提示；每平台/每游戏可选执行路径；三条路径可自由切换 | 1 周 |

**B 系列合计约 6 周**。与 A 系列的关系：

```
A0 → A1 → A2 ─┬─→ A3 → A4 → A5 → A6        （前端可用，只走外部模拟器）
               └─→ B0 → B1 → B2 → B3 → B4   （内联，把"开箱即用 + 真双屏"补上）
```

> **建议节奏**：先把 A 线做到 A4（此时已是可用前端，靠外部模拟器），
> 再启动 B 线。原因是 B 线的价值（开箱即用、真双屏）建立在"前端本身已经好用"之上，
> 而且 B0 的许可决策一旦定了 GPL 就不可逆。

**关键路径**：A0 → A1 → A2 → A3 → A4（此时已是"可用的前端"）→ A5/A6（体验完整）。
合计 **约 8 周**（不含 A7/A8）。

**建议的设备推进顺序**（风险递增）：

```
① RG DS 刷 Android          ← 双屏同尺寸 640×480，与 Linux 版硬件一致，
                              可与 Linux 版逐帧对比，最容易定位问题
② AYN Thor                  ← 异构双屏 + 高分辨率 + 强 SoC，验证 §4.1 的逻辑分辨率与 §6.4 的异构布局
③ 单屏安卓掌机 / 手机         ← COMPACT / PORTRAIT，触摸与竖屏布局
```

> 第 ① 步是这次移植最大的运气：**同一台机器能跑 Linux 版和 Android 版**，
> 出问题时可以直接对照"同样的代码在 Linux 上是对的"，把平台层的锅和内核的锅分开。
> 没有这个对照组，双屏 + 新宿主 + 新存储模型同时上，排障会非常痛苦。

---

## 14. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| **Pillow wheel 在 16 KB page 设备加载失败** | R1 路线不成立 | A0 先验；退路 R2（§4.2），工期 +2 周。**这是唯一的架构级风险** |
| Chaquopy Python→Java 调用开销超预期 | R2 的 draw call 方案变慢 | A0 顺带压测：空循环 10 万次 JNI 调用计时；若 > 20 μs/次，R2 改为"批量指令缓冲"（Python 侧攒一帧 draw list，一次传过去解释执行） |
| 软渲染帧率不达标 | 滚动卡 | 降逻辑分辨率（§4.1 已给公式）；开启现有增量重绘（`_top_cache`）；最终走 R2 |
| `MANAGE_EXTERNAL_STORAGE` 被用户拒绝 | 扫不到库 | SAF 兜底（§7.4）+ 引导页把"为什么需要"讲清楚 |
| SAF 模式下第三方模拟器读不到 ROM | 启动失败 | `grantUriPermission` + 传 URI；不支持的模拟器**禁用并提示**，不静默失败 |
| SurfaceView 层级（透明洞）在部分 ROM 上失效 | 视频区黑块或盖住 UI | Plan B：MediaCodec 抽帧（§9.3），配置项可切 |
| **副屏 Display 不被 `DisplayManager` 暴露**（厂商私有通道） | 双屏功能不成立 | A2 第一件事就是在 RG DS Android + Thor 上跑 `dm.displays` 打印；若确实不暴露，退路是厂商 SDK（Thor 有 TCC，说明存在控制通道）或**降级 single**（功能不缺失，只是没有下屏） |
| **点副屏后手柄失灵**（Android 焦点跟随，实测存在） | 双屏机上不可操作 | 主屏 Activity 与副屏 Presentation **都接按键**并汇入同一队列（§6.4.3）；A5 专项验收 |
| 模拟器与我们抢副屏 | 游戏里下屏黑或被前端盖住 | 启动前 `Presentation.dismiss()`（§6.4.4），不是 hide；A4 用 MelonDS 双屏配置实测 |
| 合盖 / TCC 切"仅上屏"导致 Display 移除 | 崩溃或黑屏 | `DisplayListener` → `EXIT_RESTART_UI = 43` 重来（§6.4.3 坑 3），复用掌机既有契约 |
| **异构双屏布局破版** | Thor 上难看 | `Metrics` 全比例化已能吸收（§11.1）；A2 用截图逐屏核对，这是异构路径的**首次真实验证** |
| **LibretroDroid 的 GPL-3.0 传染** | 项目许可被锁定，闭源路线关闭 | **B0 先决策**（§8.10）；内联实现全部隔离在 `platform/android/inline/` + `GameActivity`，必要时可整块砍掉 |
| **内联双屏需要 fork LibretroDroid** | B3 变成维护一个 fork | 改动限定在"第二个 EGLSurface + UV 区域"，尽量向上游提 PR；fork 失败则退回"上屏拼接显示"（功能不缺，只是不如真双屏） |
| libretro 核心质量参差 | 某些平台内联能跑但效果差 | `prefer_external` 表把重型平台交给独立模拟器（§8.4）；内联只做它擅长的 8/16 位与轻量平台 |
| 核心下载依赖 buildbot 可用性 | 无网络时装不上核心 | 允许手动放 `.so`；已下载的核心不会因为断网失效；核心管理器显示"已就位/可更新/需下载" |
| BIOS 缺失导致核心黑屏 | 用户以为程序坏了 | 维护平台→BIOS 清单 + 校验和，启动前明确提示缺哪个文件（§8.6.5） |
| 内联存档与 RetroArch 存档不通 | 老用户迁移困难 | 明确说明不可共享（Android 11+ 限制），提供"导出到 ROM 目录旁"（§8.7），不含糊其辞 |
| 模拟器 Intent 协议变动（如 RetroArch 那次核心路径回归） | 启动失败 | `fallbacks` 链 + 失败信号判定（§8.1）；启动器表可用户覆盖（`launchers.android.json`） |
| 系统在后台杀掉 App | 回来后状态丢失 | `state.json["resume"]` 照常写（§8.4），与掌机同一段代码 |
| 中文字体缺失 → 方块 | 不可用 | 打包子集字体（§12.2），`FontBook` 加 Android 候选目录 |
| 缩略图占用 App 私有空间过大 | 用户投诉 | 已有 `prune_thumbnails()` + 设置项"清理缓存"；Android 上额外接入系统"清除缓存" |
| 内核重构引入 Linux 回归 | 掌机变砖 | R-A~R-E 每项独立合入 + 三平台验收（§5.6）；`scripts/screenshot.py` 做像素级对比 |

---

## 15. 待验证清单（拿到设备/环境立刻做）

```bash
# ① A0 核心实验：Chaquopy + Pillow + 16KB page
#    在 Android 15+ 真机（如 Pixel 8 / 一加 12）上跑
adb shell getconf PAGE_SIZE            # 16384 = 16 KB 页设备
# 空 Chaquopy 工程里：
#   import PIL, PIL.Image; PIL.Image.new("RGBA", (640, 480)).tobytes()

# ② JNI 调用开销压测（决定 R2 是否可行）
#   Python 侧循环 100000 次调用一个空 Kotlin 方法，计时

# ③ 软渲染帧耗时实测（决定逻辑分辨率）
#   把 scripts/screenshot.py 的无头渲染搬到 Android，量 draw_top/draw_bottom

# ④ 存储遍历性能对比
#   同一张卡：All-files 的 os.scandir vs DocumentFile.listFiles()，3.9k ROM 计时

# ⑤ RetroArch Intent 实测（当前发布版行为）
adb shell am start -n com.retroarch.aarch64/com.retroarch.browser.retroactivity.RetroActivityFuture \
  -e ROM /storage/emulated/0/Roms/FC/xxx.nes \
  -e LIBRETRO fceumm_libretro_android.so \
  -e CONFIGFILE /storage/emulated/0/Android/data/com.retroarch.aarch64/files/retroarch.cfg
#   失败则改传核心全路径，记录哪种可用 → 决定 fallbacks 顺序

# ⑥ 系统字体能否直接给 PIL 用
#   ImageFont.truetype("/system/fonts/NotoSansCJK-Regular.ttc", 16)  # 成功则省 5 MB

# ⑦ SurfaceView 透明洞 + ExoPlayer 定位
#   最小 demo：一个 SurfaceView 画半透明 Bitmap，下面叠 PlayerView

# ⑧ ★ 双屏探测（双屏机上第一件事，A2 的入口）
adb shell dumpsys display | grep -E "mDisplayId|mBaseDisplayInfo|unique"
#   在 RG DS(Android) 与 Thor 上各跑一次，确认：
#     · 副屏是否作为独立 Display 暴露、displayId 是多少
#     · 是否带 FLAG_PRESENTATION、是否 FLAG_PRIVATE
#     · 两屏各自的 physicalWidth/Height（核对 1920x1080 / 1240x1080）

# ⑨ ★ Presentation 可用性 + 输入焦点
#   最小 demo：Activity(主屏) + Presentation(副屏) 各画一个色块，
#   两边都装 onKeyDown 打日志。然后：
#     · 点副屏 → 按手柄 → 确认 Activity 或 Presentation 至少一处收到事件
#     · 确认点主屏不会 dismiss 掉 Presentation
#   这一条决定 §6.4.3 坑 1 的处理是否够用

# ⑩ ★ 副屏让位
#   demo 里 dismiss() Presentation → am start 启动 MelonDS（配好双屏）
#   → 确认 MelonDS 的下屏输出正常显示，不被我们的窗口层压住
#   → 回到 demo → 重建 Presentation → 确认副屏恢复

# ⑪ ★ 异构双屏布局核对（Thor）
#   把 scripts/screenshot.py 的无头渲染按 1116x628 与 896x780 两组尺寸各跑一遍，
#   人工核对：上屏网格列数、下屏媒体框/元数据卡/简介行数是否合理不溢出

# ⑫ ★ 内联可行性（B0，与许可决策同期）
#   ① JitPack 拉 libretrodroid，最小 Activity + GLRetroView 跑 fceumm + 一个 FC ROM
curl -O https://buildbot.libretro.com/nightly/android/latest/arm64-v8a/fceumm_libretro_android.so.zip
#   ② 确认能拿到 core variables 列表（决定 §8.8 的核心选项菜单能否动态生成）
#   ③ 确认 state / SaveRAM 序列化接口可用
#   ④ 量一下内联运行时 Python 侧的 CPU 占用（应接近 0，因为菜单未开不绘制）

# ⑬ ★ 内联双屏可行性（B3 的前置，决定要不要 fork）
#   在 LibretroDroid 的 C++ 渲染层确认：
#     · framebuffer 是否以单个 GL texture 存在（是 → UV 裁剪方案成立）
#     · 能否创建第二个 EGLSurface 绑到副屏的 SurfaceView
#   用 DeSmuME 核心跑一个 NDS ROM，确认输出确实是 256x384 上下拼接

# ⑭ 层级验证：GLSurfaceView（下）+ 半透明普通 View（上）
#   这是 §8.6.4 的游戏内菜单方案，与 ⑦ 的透明洞是相反方向，需要各自验证
```

---

## 16. 对现有文档的影响

| 文档 | 需要的修改 |
|---|---|
| **仓库根目录** | **新增 `LICENSE`（建议 GPL-3.0）** —— B0 的前置，见 §8.10 |
| `docs/DESIGN.md` §1.2 | "不在本期范围"里的"内建模拟器"需要注明：**Android 端通过 B 系列内联实现**，Linux 端仍不做 |
| `docs/DESIGN.md` §17 | 保留（它是约束层），加一行指向本文；§17.3 的"现在就要做的 7 件事"里，第 1、2 项已完成，第 3~7 项在本文 §5 具体化 |
| `docs/DESIGN.md` §15 扩展线 | E2 标记为 ✅（`Metrics` 已比例化）；E3/E4 拆成本文 A0~A8 |
| `README.md` 未来计划 | E2 状态从"待开始"改为"✅ 完成"；E3/E4 指向本文 |
| `docs/USAGE.md` | A3 之后补 Android 侧的权限与目录说明 |

---

*本设计基于 2026-09-10 对代码库 v0.5.0 的逐层核查：`platform/base.py`（28+12 方法抽象）、*
*`core/theme.py`（`Metrics.u()` 比例化）、`data/`（`platform.list_dir` 全覆盖）、*
*`platform/desktop/`（第二个平台实现的存在性证明）、*
*`ui/app.py:292-315`（每个 canvas 各自 `metrics_for` → 异构双屏已被支持）。*
*外部技术前提（Chaquopy 17 的 Python 版本与 16 KB 页限制、Chaquopy 包索引中的 Pillow、*
*RetroArch Android 的 Intent 协议与 2025-01 核心路径回归、AYN Thor 的双屏规格与*
*输入焦点跟随问题、Anbernic RG DS 的 Android/Linux 双系统支持）于同日查证。*

*双屏设备资料来源：AYN Thor 上手实测（上屏 6" 1920×1080 120 Hz、下屏 3.92" 1240×1080、*
*TCC 的双屏异显 / 触控焦点锁定 / 双屏独立亮度与音量、MelonDS 与 Azahar 的双屏配置方式）；*
*Anbernic RG DS 官方页（4 英寸双屏翻盖，支持 Android/Linux 双系统，双 640×480，RK3568）。*

*内联模拟器技术前提：LibretroDroid（GPL-3.0，JitPack `com.github.swordfish90:libretrodroid`，*
*269 次提交，驱动 Lemuroid；已支持 2D/GL 核心、音频、手柄、状态与 SaveRAM 序列化、*
*触摸屏、多碟、核心变量；已验证 fceumm / mGBA / Snes9x / DeSmuME / PCSXReARMed 等 14 个核心；*
*未提供多 Surface 输出，双屏拆分需扩展）；libretro buildbot*
*`nightly/android/latest/arm64-v8a/<core>_libretro_android.so.zip`（日更，单核心 59 KB 起）。*



