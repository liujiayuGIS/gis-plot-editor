# 命令与场景接口

## 路径和执行入口

将 `<python>` 替换为已验证解释器，将 `<skill>` 替换为本 Skill 的绝对目录。Windows 用 PowerShell 调用包含空格的路径：

```powershell
& '<python>' '<skill>\scripts\gis_editor.py' doctor
& '<python>' '<skill>\scripts\gis_editor.py' gui --spec '<project>\scene.json' --session '<project>\出图会话'
& '<python>' '<skill>\scripts\gis_editor.py' gui --session '<project>\出图会话'
& '<python>' '<skill>\scripts\gis_editor.py' send --session '<project>\出图会话' --command '<project>\修改指令.json'
```

`gui` 是人工命令行启动入口；Agent 的 GUI 启动遵守宿主工具协议。factory 创建/恢复加 `--trusted-factory`，仅在检查代码后使用。无界面独立导出使用 `render --spec ... --session ... --output ...`；编辑器占用会话时必须用 send 导出，不要并发恢复。

## 矢量场景

示例字段与路径仅说明格式，必须替换为已读取、已确认的用户材料。CRS 示例不是全国/任意区域的默认投影。

```json
{
  "mode": "gis",
  "display_crs": "EPSG:32650",
  "title": "已确认的研究图题",
  "size_mm": [180, 130],
  "preview_dpi": 110,
  "font": "Microsoft YaHei",
  "layers": [{
    "id": "sites",
    "path": "已确认的本地文件.geojson",
    "field": "已确认的指标字段",
    "encoding": "continuous",
    "units": "已确认单位",
    "cmap": "viridis",
    "point_size": 20,
    "alpha": 1,
    "source": "数据来源及版本"
  }]
}
```

- 图层 `id` 唯一，使用 ASCII 标识。
- 源文件无 CRS 时，加 `source_crs` 与 `crs_confirmed:true`；已带 CRS 的文件声明冲突会报错。
- 分类值：`encoding:'categorical'`，给定 `categories:['A','B']` 与 `colors:{'A':'#367ca4','B':'#a87535'}`，类别及字典键使用字符串并完整覆盖。
- 无 `field` 时使用单色层，可给 `color` 与 `label`。
- GeoPackage 非默认层给 `layer`，其他格式不用。
- 范围可给 `extent:[xmin,ymin,xmax,ymax]`，严格按 display_crs；裁切触发提示。
- 比例尺给 `scale_bar_m`；指北针给 `north_arrow:true`，按请求生成，不能把网格北当真北。

## 栅格场景

```json
{
  "mode": "gis",
  "display_crs": "已确认的 CRS",
  "layers": [{
    "id": "surface",
    "path": "已确认的本地栅格.tif",
    "band": 1,
    "resampling": "nearest",
    "preview_max_edge": 1600,
    "units": "已确认单位",
    "cmap": "viridis"
  }]
}
```

band 从 1 开始。nearest 保留类别，bilinear 仅适用于连续变量。预览会读取重投影显示网格、保留 NoData；不是修改原栅格。分类栅格应通过可信 factory 使用离散 Normalize 和显式图例，不能当连续数值色标。

## 可信 Figure 工厂场景

```json
{
  "mode": "factory",
  "factory": "绘图适配器.py",
  "function": "build_scene",
  "data_files": ["完整结果文件路径"],
  "params": {}
}
```

`params` 为已确认绘图函数参数，不是可执行代码。列出所有需要跟踪的输入文件，包括函数内部依赖；内置哈希不能自动推断工厂暗中读取的其他文件或数据库。

## Agent 命令

只接受本地 JSON。stdout 成功形态 `{"ok":true,"result":...}`；`ok:false` 表示未成功或超时，需要检查 error。

列对象与版本：`{"op":"list"}`。

批量补丁：

```json
{
  "op": "patch",
  "expected_revision": 0,
  "patches": [
    {"target":"title","properties":{"text":"用户确认的新标题","fontsize":14}},
    {"target":"legend","properties":{"position":[0.78,0.20]}}
  ]
}
```

`expected_revision` 必须来自刚取得的 list，不把示例 0 硬编码。位置：标题/普通注释沿 Artist transform；内置 title 为 Figure 比例坐标。图例为 Figure 比例坐标 `[x,y]`（左下锚点）。Axes 为 Figure 比例坐标 `[x,y,width,height]`。左下为原点，不能将屏幕像素直接传入。

操作：
- `undo` / `redo`：携带 list 返回的整数 `expected_revision`，例如 `{"op":"undo","expected_revision":1}`
- `{"op":"audit"}`
- `{"op":"save"}`
- `{"op":"export","path":"final.png","dpi":300,"overwrite":false}`
- `{"op":"close"}`

Agent export 相对 path 落在会话 `exports/`，绝对 path 必须在会话目录下。超时会返回 response_path，原请求可能仍执行，先查响应再决定重试。

`patch` / `undo` / `redo` / `style_apply` 必须携带刚从 list 得到的整数 `expected_revision`，缺失或冲突拒绝修改。

### 多格式导出

扩展名不区分大小写：`.tif/.tiff`、`.png`、`.jpg/.jpeg`、`.pdf`、`.svg`。格式按扩展名选择，固定物理图幅，DPI 范围 36–1200。

```json
{"op":"export","path":"figure.tiff","dpi":600,"tiff_compression":"tiff_lzw","transparent":false,"overwrite":false}
```

- TIFF 压缩：`tiff_lzw`（默认）、`tiff_adobe_deflate`、`raw`，均无损。输出为排版图片，不是带地理配准的 GeoTIFF。
- JPEG：`jpeg_quality` 为 1–100 整数，默认 95，白底；`transparent:true` 报错。
- 其他格式可选 `transparent:true`，默认 false；JPEG 不支持透明。
- render CLI 对应 `--jpeg-quality`、`--tiff-compression`、`--transparent`。
- manifest 保存格式、图幅、DPI、选项、输入哈希、色标截断数量和最终物理尺寸下的 audit。

### 样式模板

发送 `{"op":"style_get"}`，将返回的 result 保存为 JSON 模板。可加 `include_color_scale:true` 包含色标上下限。应用示例：

```json
{"op":"style_apply","expected_revision":0,"style":{"version":1,"size_mm":[180,130],"objects":{"title":{"kind":"text","properties":{"fontsize":14}}}},"include_color_scale":false}
```

0 只是示例，替换为 list 返回的版本。复用布局、字体、色带和图幅；禁止在模板中注入标题、单位、几何、指标、投影、范围或隐藏图层等语义变化。对象 ID 和类型必须匹配；分类/单色图层的锁定属性仍受保护。应用色标范围须再次显式 `include_color_scale:true`，并确认两图指标与单位一致。一次应用只占一个撤销步骤。

`audit` 返回 `color_scales`（真实/显示范围、有限样本数、上下界外数量）及 `checklist`（图幅、CRS、哈希、图层记录与人工复核项）。栅格统计针对已读取的显示网格，不声称是全分辨率总体统计。

## 属性白名单

以 list 返回的 allowed 为权威。文本：text/fontsize/fontfamily/fontweight/color/position/ha/va/rotation。图例：fontsize/fontfamily/position/title/framealpha/edgecolor/facecolor。图框：position/framecolor/framelinewidth。连续主题：cmap/clim/alpha，联动关联色标。其他图层：alpha/edgecolor/facecolor/linewidth（按对象类型）。通用：visible/zorder。

不接受 xdata/ydata、指标数组、几何坐标、投影属性、任意方法名、代码字符串。不要把分类 Collection 的 facecolor 设置成同色导致图例失配；分类配色变更应更改场景映射并建立新会话，或通过已验证的复合适配器同步图例。

## 会话文件

`session.json` 是持久状态：spec、输入哈希、对象状态、revision、baseline、undo/redo、修改日志和已提交命令回执。`runtime.json` 是运行心跳，不是数据权威。`editor.lock` 由操作系统持锁，文件保留，进程退出自动释放；不能因心跳过期就删除锁。GUI 和 render 均持锁。commands/responses 保存请求证据；状态和回执一起原子保存，崩溃后补写响应，避免重放已提交修改。导出文件属于外部副作用，进程在图片写入后、提交回执前退出时，需检查图片/manifest，不能保证跨文件事务。关闭后通过 spec 重建 Figure 再恢复样式，不跨版本反序列化 Figure。
