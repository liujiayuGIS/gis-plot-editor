# 接入已有 Matplotlib Figure

## 最小接入

保持原有结果读取与绘图流程，拆成可复现的函数。不要把统计/空间分析过程放在样式更新函数中，不在导出后 close 掉 Figure。已有结果只加载和绘制，不自动重新拟合。

```python
from scene import EditableScene

def build_scene(params):
    fig, axes, artists = existing_plot_function(params)
    return EditableScene.from_figure(
        fig,
        objects={
            "map.main": axes["main"],
            "map.inset": axes["inset"],
            "title": artists["title"],
            "legend": artists["legend"],
        },
        provenance={
            "display_crs_wkt": params["display_crs_wkt"],
            "source_crs": params["source_crs"],
            "analysis_results": params["results_path"],
        },
    )
```

以上 `existing_plot_function` 是对真实已读取项目的示意，不能当成现成可执行函数。本 Skill 提供可运行的 `assets/demo_factory.py` 供回归；其值明确标为合成。

`scripts/` 内包含 scene 模块；通过 gis_editor 工厂加载时已在 sys.path。如果从项目直接 import，用明确绝对 Skill scripts 路径插入，或按项目已建立的包管理方式导入。不硬改全局 PYTHONPATH。

## 对象注册

- ID 稳定、唯一、有语义：title、legend、map.main、map.inset、colorbar.main；禁止依赖每次不同的 Artist 索引。
- 不注册未知对象类型。首版 Text、Legend、Axes、AxesImage、Collection、Line2D、Patch 有白名单。
- figure 必须是相同 Figure 的对象，注册外部 Figure 的对象会报错。
- 不传 objects 时保守发现 axes、titles、axis labels、texts、legends；后续 list 确认真实 ID，不能猜测。
- 连续色标需要 `colorbars={"layer.metric": colorbar}` 与对应 mappable 注册，支持改 cmap/clim 后同步色标。
- 分类图例往往是 proxy handles，不与数据对象自动联动。用 `scene.locked_properties["layer.category"]={"facecolor","edgecolor","cmap","clim"}` 锁住会造成图例失配的独立属性；修改分类映射应建立新会话。
- 带箭头 Annotation 的 position/rotation 禁止独立修改，以免箭头和文字关系失效。真北/比例尺应作为约束复合对象，不注册为可以随意拆分的普通 Text。

## 恢复与版本

factory 配置必须完整列出 `data_files` 和依赖的参数。恢复时工厂会再次执行，因此它必须从已缓存结果重建场景，不应触发训练、网络下载、写文件、随机新结果或关闭窗口。随机布局固定种子。

输入哈希或工厂文件变化时禁止原地恢复；明确创建新会话。其他第三方库/外部环境版本变化可能影响布局，保存软件版本并重新验证，不能承诺跨版本像素一致。

## 和 EasyPlot 配合

EasyPlot 的 `easyplot_py.py` 可生成 Figure，但它在导入时强制 Agg。不要据此承诺打开原版 EasyPlot 就能交互。先创建静态 Figure，再由本编辑器用 `FigureCanvasTkAgg` 嵌入。既定配色、物理图幅、显式不确定性与来源记录应保留；只读取已有结果，不在样式补丁里调用 EasyPlot 分析函数。

不默认复制 EasyPlot 全库或安装其 R 依赖。可复用具体函数时先检查版本、许可和完整数据契约，再补实际测试。
