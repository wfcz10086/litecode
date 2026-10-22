---
name: remote-desktop-control
description: >
  远程操控电脑 (Windows 桌面 / Linux 主机 / 本机) 的闭环做法: 截图或控件树观察 → 单个动作 → 核对结果 → 下一步。
  覆盖 windows_desktop 的 ui_tree/click_element/launch_app/open_url/type_text/hotkey、坐标换算、等界面稳定、
  windows_system 传文件与回读、ssh_exec 操作 Linux 和本机宿主机, 以及真机踩过的坑。
  触发: 远程操控、远程控制电脑、操作 Windows、桌面、截图、点击、打开记事本、资源管理器、浏览器、关闭窗口、传文件到 Windows。
---

## Keywords
远程操控 远程控制 操控电脑 控制电脑 Windows 桌面 截图 点击 双击 鼠标 键盘 输入 窗口 关闭窗口
记事本 notepad 资源管理器 explorer 浏览器 edge 打开网页 另存为 保存 文件 传文件 上传 下载
windows_desktop windows_system windows_nodes ui_tree click_element 控件 节点 win-hugebq 本机 local

---

# 远程操控电脑 (闭环)

## 1. 先选对机器和通道

| 要做的事 | 用什么 |
|---|---|
| 看有哪些机器 | `windows_nodes` list (Windows 回连节点) / `ssh_list_hosts` (SSH 别名) |
| Linux 主机跑命令 | `ssh_exec` + alias (5090 / gpu1 / jumphost …), 不用 sshpass |
| **本机 (LiteCode 所在服务器)** | `ssh_exec` alias=`local` (宿主机 ubuntu, 免密 sudo)。`execute_shell` 跑在容器里, 看不到宿主机进程/文件; 别在本机 `docker restart/stop litecode` (那是你自己) |
| 本机容器里的图形桌面 (Xvfb :99) | `desktop_screenshot` / `desktop_click` / `desktop_type` / `desktop_key` |
| Windows 命令、文件、进程、装软件 | `windows_system` (run_powershell 需节点开启 shell 能力; 单次 ≤300 秒) |
| Windows 图形界面 | `windows_desktop` |
| 服务端 ↔ Windows 传文件 | `windows_system` upload / download (≤1GB, 约 2MB/s, 两端都按 sha256 校验整文件) |

用户点名了界面做法 ("用记事本写""在资源管理器里找") 就走界面, 不要偷换成命令行; 但核对结果时用命令行/回读。

## 2. 闭环五步 (每一步都要做)

1. **观察**: `ui_tree` 看前台窗口控件 (类型/名称/输入框当前值/center 坐标); 自绘界面 (微信、游戏、远程画面) 控件树为空, 再用 `screenshot`/`ocr`。
2. **确认前台**: 返回里 `windows` 列表看 `foreground: true` 的是不是你的目标窗口。不是 → `focus_window` (title 或 window_handle)。
3. **只做一个动作**: 点击/输入/快捷键一次一个, 不要一口气连发。
4. **核对**: 动作返回已自动等界面稳定 (`settled`/`settle_ms`), 看新截图、窗口标题变化 (记事本标题出现 `*` = 已输入未保存)、或再 `ui_tree` 读控件值。没变化就重新判断焦点/遮挡, 不要盲目重发。
5. **收尾验真**: 改过/保存过文件 → `windows_system inspect_file` 回读真实内容, 汇报以文件为准; 打开的窗口用完就 `close_window`。

## 3. 定位优先级

1. **命令行能确定的事实** (文件在不在、内容是什么、进程) → `windows_system`。
2. **控件树** `click_element` text=控件名 (可加 `control_type`: Button/Edit/ListItem/TabItem/MenuItem/TreeItem/Hyperlink)。匹配多个会列出候选和 center, 用 control_type 或更完整名称区分。
3. **OCR** `click_text` (只适合大字、英文; 1280x800 小字中文常认错, 下划线会丢)。
4. **坐标** `click`/`double_click`/`right_click`/`drag`: x,y 取自**最新完整截图**, `image_width/image_height` 填你看到的那张图的尺寸 (ui_tree 的 center 是屏幕物理像素, 此时填 `screen_size`)。旧图超 5 分钟或分辨率变化会被拒。

## 4. 常用流程模板

**记事本写中文并保存到指定路径**
1. `launch_app` name=notepad → 看 `new_window`/`focused`; Win11 记事本会**恢复上次打开的标签页**, 标题若不是 Untitled/无标题, 先 `hotkey ctrl+n` 新建标签页 (或 click_element 空白标签)。
2. `type_text` 中文 (默认剪贴板粘贴, 不受输入法影响) → 标题出现 `*`。
3. `hotkey ctrl+s` → `ui_tree control_type=Edit` 找 "文件名:" → `click_element text=文件名: control_type=Edit` → `hotkey ctrl+a` → `type_text` 完整路径 → `click_element text=保存(S)` (英文系统是 Save)。
4. 目录不存在会弹 "路径不存在" 框: 先 `windows_system run_powershell New-Item -ItemType Directory -Force` 建目录再保存。已存在同名文件会弹 "确认另存为/替换" 框, 用 ui_tree 看清再点。
5. `inspect_file` 回读逐字核对 → `close_window`。

**浏览器打开网页并截图**: `open_url` url=… (可选 browser=msedge/chrome)。工具会等新窗口并切到前台; 没设默认浏览器时直接起 Edge/Chrome, 系统弹"选择一个应用"框会自动关掉改为直接启动。网页内容第一次 ui_tree 可能只有浏览器外壳 (Chromium 懒加载), 工具已自动重取一次。

**资源管理器找文件**: `launch_app explorer` → `click_element text=地址栏 control_type=Edit` → `type_text` 完整路径 → `hotkey enter` (按键一律用 hotkey, 没有 press 动作) → `ui_tree text=文件名片段` 看 ListItem, 名称精确就是文件名; 选中用 `click_element text=文件名 control_type=ListItem`, 打开用 `double_click` 它的 center。地址栏输入时冒出的"主机弹出窗口"是自动补全下拉框, 不用管, 回车即可。修改时间等列在"详细信息"视图里, ui_tree 的 Edit 子项 (名称/修改日期/类型/大小) 的 value 就是各列值, 不必开属性窗口。核实存在与否用 `windows_system list_dir` 更准。

**传大文件**: 源文件必须在服务端工作区 `uploads/`; 落地 `%USERPROFILE%\Downloads\LiteCode\` (同名自动改名)。结果里 `sha256`、`verified` 就是校验证据; 取回的文件在 `uploads/Node-Download-*`。

## 5. 真机踩过的坑 (2026-10-21 win-hugebq)

- **新开的程序落在别的窗口后面**: 后台代理起的进程拿不到前台。工具已自动切前台; 结果 `focused=false` / `state=focus_failed` 时必须先 `focus_window`, 否则字会打进用户的窗口。
- **桌面上的 LiteCode 窗口是用户的**: 别关、别往里打字。输入前确认前台窗口。
- **窗口标题含零宽字符** (Edge: `Microsoft​ Edge`): 已做归一, 照显示的标题写即可; 同名窗口用 `window_handle`。
- **"桌面锁定或未登录"**: 看 `session_state`。`disconnected` = 远程桌面断开 (不是锁屏), 有 shell 能力时可用 run_powershell 注册一次性 SYSTEM 计划任务执行 `tscon <session_id> /dest:console` 接回控制台 (不注销、不需密码), 跑完删除该计划任务; 真锁屏只能请用户解锁, 不要猜密码。
- **节点同一时刻只跑一个任务**: 别的会话占用时工具自动排队最多 45 秒, 报错会写明占用的动作和已运行时长; 不要自己 sleep 轮询, 先做不依赖它的步骤或告诉用户稍后再试。
- **说了下一步就要真做**: 回复以"让我…/下一步…/Let me…"结尾却不调工具, 回合会被系统拦回来续跑; 汇报前对照用户原话逐项确认 (打开/输入/保存/读回/关闭), 漏项系统也会提醒补做。
- **GUI 程序别用 run_powershell 直接起**: 那是会话 0 / 无界面通道, 窗口看不见。用 `launch_app`; 实在要用命令行起 GUI, 用 `schtasks /IT` 在交互会话里起。长任务用 `Invoke-CimMethod Win32_Process Create` 脱离, 单次 run_powershell ≤300 秒。
- **OCR 不可信**: 文件名、数字、验收结论以 ui_tree 的值或 inspect_file 为准。`expected_texts` 严格逐字匹配, 失败不等于界面错。
- **多显示器**: 截图只截主屏; ui_tree 里 `offscreen: true` 的控件在副屏, `click_element` 仍能点到。高 DPI 已按物理像素换算 (进程 per-monitor DPI aware), 缩放的截图按 image_width/height 自动换算, 不要自己乘缩放比。

## 6. 汇报

列出: 在哪台机器 (alias)、做了哪些动作、证据 (inspect_file 内容 / sha256 / 截图路径 / 窗口标题), 失败的步骤和原因如实写, 不把"动作执行了"当成"任务完成了"。
