---
name: gis-plot-editor
description: 在 Python GIS 制图、已有 Matplotlib Figure 接入、标题/字体/图例/色标/图框微调、首图持续编辑、点选拖拽、撤销恢复与 PNG/PDF/SVG 导出时使用。支持 GeoJSON、Shapefile、GeoPackage、GeoTIFF，本地 Tk 编辑窗口与同一 Agent 的文件式命令接口。保留指标结果，不用于从 PNG 恢复原始指标或自动完成地图发布审核。
---

# GIS Plot Editor

把首次制图当作创建可编辑场景，而不是只生成图片。使用同一个 Agent 调度本地运行时；持续 Python 进程不是第二个 Agent。只在创建/恢复时调用数据构建器，样式修改直接作用于同一 Figure。

## 先读与边界

- 所有调用先读 [references/api.md](references/api.md)。GIS 文件制图再读 [references/gis-contract.md](references/gis-contract.md)；接入已有项目再读 [references/figure-adapter.md](references/figure-adapter.md)。最终导出前读 [references/verification.md](references/verification.md)。
- 用户只给 PNG/JPG 时，先索取数据/绘图源码或已有场景，不假装恢复 Figure/Artist。不加载 pickle，不对命令使用 eval/exec。
- 数据值、几何坐标、分析方法和样本量不属于样式补丁；默认只读。修改分类边界、投影、视图范围、栅格重采样属于制图语义变化，先解释影响，并用显式新配置创建新会话。
- 不自动猜 CRS；不能靠把坐标重新标记为另一个 CRS 来代替转换。无法读取完整输入就请求补齐。
- 不硬套 EasyPlot 的 R 默认、中国地图边界源或个别项目配色。这里使用 Python；只借鉴结果/表现分离、显式投影、字体与导出检查。来源见 [references/source-adoption.md](references/source-adoption.md)。
- 不宣称投影标签、审计日志或自动检查等于地图合规认证、科学有效性或期刊认可。

## 环境与安装

1. 先在用户的 Python 环境运行 `python scripts/gis_editor.py doctor`。命令中的 `scripts/` 路径均相对本 Skill 目录，不相对项目。
2. 本机隔离环境已验证时，优先使用 `%LOCALAPPDATA%\GisPlotEditor\desktop-runtime\Scripts\python.exe`，不改全局 Python、PATH 或现有项目依赖。
3. 新机器先运行 `python scripts/setup_runtime.py` 只读探测。确认安装后运行 `python scripts/setup_runtime.py --install`，在用户本地数据目录创建独立 venv。需要无脚本参数的 Windows 桌面入口时，加 `--build-app --output-dir <用户项目目录>`；按请求加 `--register-app`。详见 [references/installation.md](references/installation.md)。不改全局 PATH/Anaconda，不静默切换语言或仅提供静态图片代替编辑器。
4. Tk 必须可用且会话有桌面。`Agg` 只用于测试/无界面导出；编辑器嵌入 `FigureCanvasTkAgg`，不在模块导入时强制切换全局 backend。
5. GUI 的打开和操作遵守宿主的电脑操作工具协议；已注册的“GIS 出图编辑器”应用可通过应用发现/启动接口打开。命令行 `gui` 是普通人工终端入口；宿主禁止 shell 启动 GUI 时不要绕过。

## 执行流程

### 1. 探查与数据契约

确认输入完整、图层/字段、源 CRS、显示 CRS、指标单位、连续/分类编码、范围、目标图幅与格式。需要真实数据时不可拿合成演示替代。已有分析结果不为制图重新拟合。

先检查是否已有 `session.json`；编辑老图沿用该会话。保留原文件，输出与会话放用户项目目录。不要在 Skill 目录保存用户研究数据。

### 2. 创建场景

**GIS 文件**：按 API 编写完整 `scene.json`，图层路径解析为绝对本地路径。连续值使用色标，分类值要求显式类别顺序和完整配色。栅格必须明确 band、重采样及 NoData。自定义复杂地图使用 Figure 工厂。

**已有 Figure**：在已审查的本地 `.py` 中定义 `build_scene(params)`，返回 `EditableScene` 或 Figure；优先注册语义对象 ID。写 factory 配置。工厂是受信任代码，启动/恢复前审查后显式使用 `--trusted-factory`；不得执行来历不明代码。

第一次构建后保存 `session.json`、原始输入哈希、对象属性、基线与历史。保存的是可复现配置，不是跨版本 Figure pickle。

### 3. 维持编辑会话

让本地窗口持续运行，所有图对象修改在 GUI 主线程执行。Agent 调用 `send` 投递白名单 JSON 命令，不在每次修改时启动重绘脚本。

每次修改前：
1. 发送 `list`，定位真实对象 ID、坐标空间、允许属性与当前 revision。
2. 把用户指令转换为明确补丁，例如标题字体、图例锚点、图框位置。身份不明确先追问，禁止根据序号猜另一个面板。
3. 批量补丁附 `expected_revision`，避免覆盖用户刚刚拖动的改动。冲突时重新 list，不强行写入。
4. 发送 patch，检查 `ok` 和实际对象属性/审计提示；在窗口或最终渲染中检查改动。操作失败不能当完成。
5. 用户继续调整时沿用同一会话。`undo` / `redo` 与 UI 共用历史；自动保存修改。

窗口支持对象列表、属性面板、标题/普通注释/图例/图框拖拽。带箭头注释、真北与比例尺是复合约束对象，不支持拆开拖拽；复杂复合对象需独立适配器。

### 4. 关闭与恢复

发送 close 或正常关闭窗口，保存状态并释放本会话锁。恢复时重新执行构建器，再应用保存的样式与撤销/重做历史，检查每个输入/工厂哈希及对象 ID。文件变化时阻止恢复，创建新会话，不悄悄将旧样式套在新结果上。

命令超时不等于取消：读取返回的 `response_path`，确认是否已执行，禁止盲目重发。心跳过期则恢复会话；不杀未知进程，不修改用户文件解决锁问题。

### 5. 导出与交付

先 audit，再检查实际渲染。导出显式图幅、DPI、格式和来源记录；不默认使用 `bbox_inches='tight'` 改变尺寸。Agent 导出限定在会话目录，已有文件需明确 overwrite；UI 使用文件对话框确认替换。

检查图例与数据/分级一致、对象裁切、字体、地图证据遮挡、投影与图层范围、栅格分辨率、色标单位。审计只检测注册文字/图例裁切与互相重叠，不声称自动检测全部地物遮挡。

成图附 manifest，保存会话与原数据引用。按宿主交付协议展示最新成图及可复现配置；说明检查覆盖和未验证项。

## 能力范围

- 直接读取：GeoJSON、Shapefile（必需 .shx/.dbf，缺 .prj 需确认）、GeoPackage、GeoTIFF。
- 直接矢量：单值层、点线面连续值与分类编码；不将站点擅自聚合成行政区指标。
- 直接栅格：单波段显示，nearest/bilinear 显式重采样，保留 NoData；预览最大边限制记录在 manifest，增加导出 DPI 不恢复原始栅格细节。
- 普通标题/图例/注释、色标位置/单位标签、图框位置/边线、图层透明度/色带可编辑；色带修改联动连续色标。
- 真北与比例尺：按请求生成；比例尺仅适用投影坐标并进行局部测地长度误差检查，不对经纬度平面添加恒定米比例尺。
- 复杂布局/插图/多面板：通过可信 factory 接入现有 Figure，注册各 Axes。内置 GIS 文件入口不自动推断插图区域或多幅地图含义。
- 单个连续主题色标；多个连续主题需 factory 显式布局。跨日界线几何必须先显式拆分，不能静默包装。
- 不含完整地图编辑软件的地物编辑、拓扑修复、统计分析、地图审查、投影变形优化或任意 Matplotlib 对象适配。

## 资源

- `scripts/scene.py`：对象注册、白名单属性、版本校验、撤销/重做、导出、状态持久化。
- `scripts/gis.py`：GIS 文件出图与可信 Figure 工厂、输入哈希验证、CRS 与图层预检。
- `scripts/transport.py`：本地文件式命令/响应、心跳、幂等处理；不开网络端口。
- `scripts/gis_editor.py`：Tk 编辑器与 `gui/send/render/doctor` CLI。
- `scripts/test_gis_editor.py`：可复现合成数据回归测试。
- `assets/demo_factory.py` / `assets/demo.json`：明确标注的合成演示，不是真实地理分析。
- `scripts/requirements.txt`：隔离环境依赖。
- `scripts/setup_runtime.py`：可移植环境探测/显式安装/Windows 应用构建与注册。
- `scripts/launch_gui.py`：无参数桌面入口及错误日志。
