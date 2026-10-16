# 安装与其他 Agent 使用

## Skill 安装

此仓库根目录含 SKILL.md，scripts、references、assets 放在同级；可以作为一个 Agent Skill 安装。不要只复制 SKILL.md，否则运行脚本与引用会丢失。

在支持 skills CLI 的环境：

```sh
npx skills add liujiayuGIS/gis-plot-editor --skill gis-plot-editor
```

在使用 AgentBuddy 的环境，按其环境要求安装对应完整仓库：

```powershell
$env:npm_config_registry='https://bnpm.byted.org'
npx -y agentbuddy@latest skill add liujiayuGIS/gis-plot-editor@gis-plot-editor -g -y
```

公开仓库可读取；安装 CLI/宿主权限与网络策略仍以当前 Agent 环境为准。Skill 文件安装不等于 Python 依赖已经可用。

## Python 依赖与本地窗口

需要 Python 3.11+，Windows 本地桌面及可用 Tk。先在安装后的 Skill 目录执行：

```sh
python scripts/setup_runtime.py
```

该命令只探测，不安装、不启动 GUI。明确批准依赖安装后：

```sh
python scripts/setup_runtime.py --install
```

默认独立环境在 Windows 的 `%LOCALAPPDATA%/GisPlotEditor/desktop-runtime`；不会更改全局 Python/Anaconda/PATH。可指定 `--runtime`。用该环境的解释器执行 `scripts/gis_editor.py doctor` 和测试。

人工终端打开编辑器：

```sh
<隔离环境python> scripts/gis_editor.py gui
```

宿主只支持发现/启动应用而不能传脚本参数时，明确批准后：

```sh
python scripts/setup_runtime.py --install --build-app --output-dir <用户选择的应用输出目录>
```

需要注册到 Windows 开始菜单时加 `--register-app`。脚本不会启动 GUI、上传数据、监听网络或覆盖已有应用入口。启动真实应用必须按宿主 GUI 工具协议操作。打包应用、依赖环境、用户会话留在本机，不推到仓库。

## 环境限制

无桌面环境可使用 render 做确定性导出，但不能声称实现了拖拽窗口。Linux/macOS 的 Python 核心原则可复用，当前交付只验证 Windows；其他系统需安装 Tk 并补 GUI 验收。

原生 Figure 工厂要用它自己的库依赖；默认 runtime 不会替用户安装所有可能 GIS/科学库。确认依赖后安装到隔离环境，不执行未知工厂。

## 其他 Agent 的最小流程

1. 加载本 Skill 与按场景选择的 reference。
2. 验证环境，读取用户完整输入，确认 CRS、字段、单位、显示方式。
3. 创建/恢复本地窗口，保留活跃 Figure。
4. 每次自然语言修改都 list → 校验 revision → patch → 查看响应/画面。
5. 检查导出，交付图与来源/恢复记录；不把示例当研究数据。

自然语言由调用本 Skill 的 Agent 理解，不由运行时内置 LLM 理解；无需额外模型 Key、外部服务或第二个 Agent。
