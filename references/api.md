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
- `{"op":"undo"}` / `{"op":"redo"}`
- `{"op":"audit"}`
- `{"op":"save"}`
- `{"op":"export","path":"final.png","dpi":300,"overwrite":false}`
- `{"op":"close"}`

Agent export 相对 path 落在会话 `exports/`，绝对 path 必须在会话目录下。超时会返回 response_path，原请求可能仍执行，先查响应再决定重试。

## 属性白名单

以 list 返回的 allowed 为权威。文本：text/fontsize/fontfamily/fontweight/color/position/ha/va/rotation。图例：fontsize/fontfamily/position/title/framealpha/edgecolor/facecolor。图框：position/framecolor/framelinewidth。连续主题：cmap/clim/alpha，联动关联色标。其他图层：alpha/edgecolor/facecolor/linewidth（按对象类型）。通用：visible/zorder。

不接受 xdata/ydata、指标数组、几何坐标、投影属性、任意方法名、代码字符串。不要把分类 Collection 的 facecolor 设置成同色导致图例失配；分类配色变更应更改场景映射并建立新会话，或通过已验证的复合适配器同步图例。

## 会话文件

`session.json` 是持久状态：spec、输入哈希、对象状态、revision、baseline、undo/redo、修改日志。`runtime.json` 是运行心跳，不是数据权威。`editor.lock` 防止同会话多窗口。commands/responses 保存幂等请求证据。关闭后通过 spec 重建 Figure 再恢复样式，不跨版本反序列化 Figure。
