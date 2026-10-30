# colorlab

影视调色的**色彩分析**、**Look 设计**和 **3D LUT 生成**工具。

拿一张参考图 + 一张自己的静帧，算出两者的影调和色彩差异，把差异写成一个可复现的
`Look` 参数文件，最后烘焙成 DaVinci Resolve 能直接导入的 `.cube`。

三种用法：

- **命令行**：`colorlab analyze / compare / grade / bake / apply / audit / sheet`
- **Python**：`from colorlab import Look, bake`
- **MCP**：`colorlab-mcp`，让 AI agent 直接调用（推荐）

依赖只有 numpy + Pillow（MCP 模式额外需要 `mcp`）。

---

## 安装

```bash
cd colorlab
python3 -m pip install -e ".[mcp,dev]"
```

不装也能用，在项目目录里 `python3 -m colorlab ...` 即可。

---

## 快速开始

```bash
# 1. 看片：分析参考图和素材的色彩统计
python3 -m colorlab analyze ref.png src.jpg

# 2. 对比：输出"参考 - 原片"的影调差和色相差
python3 -m colorlab compare src.jpg ref.png --ref-crop 0.02,0.08,0.98,0.75

# 3. 预演：用 Look 调一张图看看效果
python3 -m colorlab grade src.jpg --look looks/lotus_sunset_dusk.json -o out/after.jpg

# 4. 出片：烘焙 65³ LUT，并用素材验证烘焙误差
python3 -m colorlab bake -o out/look.cube --look looks/lotus_sunset_dusk.json \
    --size 65 --verify src.jpg

# 5. 对比图：参考 / 原片 / 调色 三联
python3 -m colorlab sheet src.jpg --ref ref.png --look looks/lotus_sunset_dusk.json -o out/compare.jpg
```

| 命令 | 作用 |
|---|---|
| `analyze IMG...` | 亮度分位、分区统计、OKLCh 色相剖面 |
| `compare SRC REF` | 影调差 + 每个色相的亮度/色度差异 |
| `derive SRC REF -o look.json` | 从两张图自动拟合**初稿**（见下文「两条路」） |
| `grade SRC --look L -o OUT` | 预演调色（可 `--lut` 同时出 LUT） |
| `bake --look L -o OUT.cube` | 烘焙 LUT（`--size 33/65`，`--verify IMG` 验证） |
| `apply IMG --lut X.cube -o OUT` | 套现成 LUT |
| `audit X.cube` | 检查值域、折返幅度、二阶差分 |
| `sheet SRC --ref REF -o OUT` | 生成对比图 |

所有命令都支持 `--width`（处理宽度，默认 1200，调色预览够用且快）。

上面是**方案 B（手工定调）**的完整链路。如果你只是要把同场景的两条素材拉齐，直接用
`derive` 一条命令就够，见下一节。

---

## 两条路：自动对齐（A）还是手工定调（B）

先回答三个问题，答案决定走哪条：

1. **两张图是不是同一个场景、同一个机位？**（同场景 / 不同场景）
2. **你要的是"两张图看起来是同一台机器拍的"，还是"这张图变成那个调子"？**
3. **差别主要在曝光、白平衡、饱和度，还是在光位、明暗结构、局部光？**

| 情况 | 走哪条 | 你会怎么说（触发话术） |
|---|---|---|
| 同场景、同机位，只是曝光/白平衡/饱和度不一致 | **A（derive）** | "这两条同机位的素材颜色对不齐，帮我统一"<br>"A/B 机拍同一个景，色温偏了，拉齐一下"<br>"这一批图的曝光和白平衡不统一，做个统一的 LUT" |
| 不同场景，想学参考图的调子 | **B（手工定调）** | "照着这张参考图给我调色"<br>"把背景压暗、花提亮，做成参考图那种落日暗调"<br>"给我做一版这种风格的 LUT" |
| 差别在光位、明暗结构、局部光（逆光、暗角、轮廓光） | **两条都不够**，见下文「做不到」 | "参考图那种背景很暗、主体被光打亮的感觉" |

判定口径很简单：**差别能被"一个颜色映射"描述就走 A，不能被描述就走 B，而 B 也表达不了的部分
只能回 DaVinci Resolve 用手工节点。**

---

### 方案 A：自动对齐（`derive`）

```bash
python3 -m colorlab derive src.jpg ref.png -o looks/draft.json \
    --ref-crop 0.02,0.08,0.98,0.75 --preview out/draft.jpg
```

**它做什么**：比较两张图的全局统计量 —— 亮度分位数、按 OKLCh 色相分桶的亮度中位/色度中位/
色相均值、暗部与高光区的平均 RGB 偏移 —— 反解出 `tone / luma / sat / rot / split` 五组曲线。

**它擅长**：同场景的两条素材，曝光差半档以内、白平衡偏一点、饱和度差一截 —— 这种"线性偏差"
它能拉平。同一个场景有多条素材时，挑一条当参考、逐条 `derive` 即可（没有批量命令，需要自己循环）。

**什么算成功**：`derive` 打印的对照行（亮度中位、平均色度）调色后明显更接近目标，
且 `grade` 出来的图没有断层、没有偏色。**达不到就别硬用，转 B。**

**已知会失败的情况**（都是实测过的）：

- 参考图的好看来自光位和明暗结构 —— `derive` 会把整体统计拉近，但画面气质基本不变
- 参考图里有大面积黑边、水印、大块纯色 —— 统计被污染，需要 `--ref-crop` 先裁干净
- 两张图构图完全不同（特写 vs 全景）—— 各亮度区间占比不一样，分位匹配会拉歪
- 花朵/天空这类"主体色"在图里占比差异大 —— 按色相分桶的中位数会被占比带偏

---

### 方案 B：手工定调（`compare` → 改曲线 → `grade` → `bake`）

```bash
# 1. 看差异：影调差 + 每个色相的 dL / dC
python3 -m colorlab compare src.jpg ref.png --ref-crop 0.02,0.08,0.98,0.75

# 2. 改 looks/xxx.json 的 tone / luma / sat / rot / split
#    改法见 skill 的 references/tuning.md，或直接读 compare 的输出

# 3. 小图预演，迭代到你满意
python3 -m colorlab grade src.jpg --look looks/xxx.json -o out/preview.jpg --width 800

# 4. 出片：65³ + 验证误差
python3 -m colorlab bake -o out/xxx.cube --look looks/xxx.json --size 65 --verify src.jpg
```

**为什么慢**：`compare` 只给数字，不给判断。"绿色该压到 0.74 还是 0.78"、"花该往红推 15°
还是 20°"，要看着预览图一轮轮试。这一步是审美，不是算术。

**什么时候算完成**：三联对比图上，背景的明暗关系、主体的色相和饱和度、高光的走向都接近参考，
同时**没有引入断层、偏色、噪点放大**。`audit` 的 `max_fold` 和 `p99.9` 误差只是及格线，
不是"好看"的证明。

---

### 两条路的区别

| | A 自动对齐 | B 手工定调 |
|---|---|---|
| 输入 | 同场景两张图 | 素材 + 任意参考图 |
| 产出 | 一份能跑的 Look（初稿） | 一份能交付的 Look |
| 耗时 | 一条命令 | 反复迭代，看素材复杂度 |
| 谁做判断 | 统计量 | 人 / agent 看预览 |
| 适合 | 多机位对齐、同场景统一 | 风格化、成片交付 |
| 风险 | 拉平统计但气质没变 | 过度调整、过拟合单张图 |

A 的产物可以直接被 B 接管：`derive` 出来的 JSON 就是一份普通 Look，接着 `grade` + 手改曲线就行。

---

### colorlab 能做 / 不能做

**能做**

- 把一张图的色彩特征量化：亮度分位、分区统计、按 OKLCh 色相的亮度/色度/色相剖面
- 对比两张图，给出每个色相的 dL / dC 差异（差异怎么落到曲线上是判断，见方案 B）
- 按五组曲线（影调 / 亮度 / 饱和度 / 色相旋转 / 分离调色）描述一套调色，并存成可 diff 的 JSON
- 烘焙 33³ / 65³ 的 `.cube`，给出烘焙误差和 LUT 体检（值域、折返、二阶差分）
- 生成 参考 / 原片 / 调色 三联对比图
- 通过 MCP 让 agent 跑完整条链

**不能做（别过度期待）**

- **不能复制参考图的"光"**。暗角、轮廓光、局部提亮、逆光氛围，这些是空间信息，3D LUT 是逐像素
  映射，表达不了。要回 DaVinci Resolve 用 Power Window（窗口）+ 限定器补。
- **不能自动判断"好看"**。`derive` 只对齐统计量，不会知道哪张图更有味道；`compare` 只给数字，
  不给结论。判断始终在人（或 agent 看预览）。
- **不能从有损素材里救回信息**。已经烧死的高光、糊成一团的暗部，LUT 变不出来。
- **不能跨场景保证一致**。不同光位、不同镜头、不同构图的素材，LUT 挂上去观感会差很远。
- **不做色彩空间自动识别**。它不知道你的素材是 Log 还是 Rec.709，得你告诉它；LUT 是按显示参考
  （Rec.709 / Gamma 2.4）烘焙的，Log 素材必须先 CST。
- **不处理视频**。只吃静帧；要做整条片子，把 LUT 拿到 DaVinci Resolve / 剪辑软件里套。
- **只输出 `.cube`**。不生成 `.3dl`、`.vlut`、DCTL 或 DaVinci Resolve 的 `.drx` 节点树。

**关于 DaVinci Resolve 的版本**：这里说的都是 **DaVinci Resolve**（Blackmagic Design 的调色软件，
免费版就叫这个名字），没有特指付费版。生成的 `.cube` LUT 在**免费版和付费版
（DaVinci Resolve Studio）里都能导入**；文中偶尔提到的 Magic Mask 属于 Studio 版功能，
免费版用 Power Window + 限定器（Qualifier）替代即可。

一句话：**colorlab 负责"把调色参数化并算出 LUT"，不负责"替你看片做审美判断"，也不负责
"用 LUT 做 LUT 做不到的事"。**

---

## MCP：让 agent 直接调色

### 启动

```bash
colorlab-mcp                      # 装好后
python3 -m colorlab.mcp_server    # 或直接跑
```

走 stdio，不需要端口。

### 在 OpenCode 里注册

`~/.config/opencode/opencode.json`：

```json
{
  "mcp": {
    "servers": {
      "colorlab": {
        "type": "local",
        "command": ["python3", "-m", "colorlab.mcp_server"],
        "environment": {
          "PYTHONPATH": "/path/to/colorlab"
        },
        "enabled": true
      }
    }
  }
}
```

把 `/path/to/colorlab` 换成仓库所在目录；`python3` 换成装了 numpy/Pillow/mcp 的那个解释器。
（用 `PYTHONPATH` 而不是 `cwd`，这样 agent 在哪个目录都不影响。）

### 给出的工具

| 工具 | 作用 | 典型用途 |
|---|---|---|
| `look_schema` | 返回 Look 参数的字段说明、取值范围、示例 | agent 动手前先读这个 |
| `list_looks` | 列出 `looks/` 下所有 Look | 找现成风格 |
| `read_look` / `write_look` | 读写 Look JSON（会校验） | 在现有 Look 上微调 |
| `analyze_image` | 单图色彩统计 | 看片 |
| `compare_images` | 素材 vs 参考图差异 | 定调色方向 |
| `grade_image` | 用 Look 出预览图（可顺便烘焙） | 快速迭代 |
| `bake_lut` | 烘焙 `.cube`，可返回烘焙误差 | 交付 |
| `apply_lut_file` | 把 `.cube` 套到图上 | 验收 |
| `audit_lut` | LUT 体检（值域/折返/二阶差分） | 交付前检查 |
| `make_comparison_sheet` | 三联对比图 | 交对比稿 |
| `derive_look` | 从原图+目标图自动拟合一版 Look（初稿） | 同机位对齐 |

设计上让 agent 能走完整条链：

```
analyze_image(素材) → analyze_image(参考) → compare_images
    → write_look（按差异改曲线）→ grade_image（看效果，不满意回去改）
    → bake_lut（--verify 验证误差）→ make_comparison_sheet → audit_lut
```

工具全部返回 JSON，路径用绝对路径。LUT 是逐像素映射，暗角/局部光这类空间操作
不会被 LUT 表达，agent 应该提醒用户回 DaVinci Resolve 补窗口节点。

### 配套 skill

有一份配套 skill，告诉 agent 该按什么顺序走、哪些事不能做：

```
analyze_image → compare_images → write_look → grade_image → bake_lut → audit_lut → sheet
```

它不在本仓库里，而是单独放在一个 skill 仓库中（本机所有 agent 的 skills 目录都软链到那里，
所以 OpenCode / Claude Code 读到的是同一份）。改 skill 去那个仓库改。

---

## Look 文件

一个 Look 就是一份 JSON，全是人可读的曲线控制点：

```json
{
  "name": "lotus_sunset_dusk",
  "tone":  [[0.0, 0.0], [0.12, 0.112], [0.5, 0.5], [1.0, 1.0]],
  "luma":  [[0, 1.03], [130, 0.74], [345, 0.99]],
  "sat":   [[0, 1.40], [130, 0.70], [345, 1.50]],
  "rot":   [[0, 6], [150, 9], [340, 20]],
  "split": [[0.0, [-0.008, 0.012, 0.014]], [1.0, [0.028, 0.012, -0.014]]],
  "global_sat": 1.02,
  "shadow_sat": 0.12,
  "strength": 1.0
}
```

| 字段 | 含义 |
|---|---|
| `tone` | 影调曲线：OKLab 的 L → L，单调三次插值，不会过冲 |
| `luma` | 按色相（0–360°）给的亮度系数，用来"把绿色压暗"这类操作 |
| `sat` | 按色相给的饱和度系数 |
| `rot` | 按色相给的旋转角度（度），用来"洋红粉转暖红粉" |
| `split` | 分离调色：按亮度加 R/G/B 偏移，阴影加青绿、高光加暖 |
| `global_sat` | 全局饱和度 |
| `shadow_sat` | 暗部额外饱和（让阴影的青绿更实） |
| `chroma_floor` / `chroma_full` | 色度低于 floor 完全不做色相操作，到 full 时全量生效 |
| `strength` | 整体强度，1.0 为全量；命令行 `--strength 0.7` 也行 |

色相参考（OKLCh）：0 红、40 橙、90 黄、140 绿、200 青、260 蓝、330 品红。

**`looks/*.json` 是唯一真源。** LUT 永远是它的下游产物：想改风格就改 JSON 重烘焙，
不要手改 `.cube`，也不要维护两份参数 —— 否则半年后没人说得清那个立方体是怎么来的。

两条设计约定（踩过坑）：

1. **中性轴保护**：灰色不参与 `luma` / `sat` / `rot`，否则灰卡会偏色、暗部出噪点。
2. **近纯白保护**：亮度接近 1 时色相操作也淡出，否则"压暗绿色"会把带一点点绿的
   白高光压成灰。

---

## 案例：荷花落日暗调（`looks/lotus_sunset_dusk.json`）

这是项目的第一份 Look，来自一次真实需求：把一张平淡的荷花静帧，往一张落日逆光的
参考图靠。

| 维度 | 原片 | 参考图 | Look 里怎么处理 |
|---|---|---|---|
| 影调 | 中位 0.293，峰值 0.794 | 中位 0.259，峰值 0.997 | S 曲线，暗部沉、高光肩抬起 |
| 绿色 | 71% 画面，亮部 L≈0.60 / C≈0.15 | L≈0.31–0.39 / C≈0.03–0.07 | 120–160° 压暗到 0.74–0.82，饱和降到 0.70–0.72 |
| 花朵 | 340°（洋红偏紫）C≈0.07 | 0–30°（暖红粉）C≈0.115 | 330–350° 旋转 +18–20°，饱和 ×1.5 |
| 分离调色 | — | 青绿阴影 + 暖高光 | `split` 表 |

产物是 Rec.709 / Gamma 2.4 显示参考空间下使用的 LUT。LUT 和预览图是
`bake` / `grade` 的产物，本仓库不存放二进制文件。

---

## 目录

```
colorlab/
├── colorlab/
│   ├── colorspace.py   sRGB <-> 线性 <-> OKLab / OKLCh
│   ├── curves.py       单调三次插值、色相周期表、分离调色表
│   ├── look.py         Look 参数 + 逐像素映射
│   ├── lut.py          烘焙 / .cube 读写 / 三线性应用 / 体检
│   ├── analyze.py      亮度分位、分区统计、OKLCh 色相剖面、对比
│   ├── io.py           图片读写、参考图裁剪、对比图拼接
│   ├── cli.py          命令行
│   └── mcp_server.py   MCP 服务（stdio）
├── looks/              Look 参数（JSON，进仓库）
├── tests/              单元测试
└── out/                生成的 LUT 和预览图（不进仓库）
```

## 测试

```bash
python3 -m pytest -q
```

覆盖：色彩空间往返、曲线单调性、色相表周期性、Look 的 JSON 往返与强度混合、
灰阶中性、.cube 读写往返、烘焙误差、LUT 折返与二阶差分，以及 MCP 工具的
注册、调色、烘焙、体检、对比图。
