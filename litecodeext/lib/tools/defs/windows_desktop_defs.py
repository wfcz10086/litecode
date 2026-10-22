"""Windows 桌面与微信登录工具定义。"""
from lib.windows_desktop_actions import HOTKEYS as _HOTKEYS

HOTKEY_NAMES = tuple(_HOTKEYS)   # 快捷键枚举直接取 Windows 端白名单, 两边不会再对不上
WINDOWS_DESKTOP_DEFS = [{
    'type': 'function', 'function': {
        'name': 'windows_desktop',
        'description': '通用 Windows 交互桌面操作。闭环：每步先观察（ui_tree 控件树优先，其次 screenshot/ocr）→ 只做一个动作 → 看返回的新截图/窗口列表核对结果，再决定下一步；动作返回后已自动等界面稳定（settled/settle_ms）。ui_tree 列出前台窗口（或 title/window_handle 指定窗口）的 UI Automation 控件：类型、名称、输入框当前值、屏幕矩形 rect/center，可用 text 过滤、control_type 限定类型；click_element 按 ui_tree 的控件名称（text，可加 control_type）唯一定位并点击——定位按钮/菜单/列表项/输入框优先用它，比 OCR 认字和按坐标盲点可靠，自绘界面（微信、游戏、远程画面）控件树为空时再用 ocr/click_text 或截图坐标。launch_app/open_url 会等新窗口出现并切到前台，结果 focused=false 或 state=focus_failed 时先 focus_window 再输入，免得字打进别的窗口。initialize 安装依赖；screenshot 截图；ocr 返回文字和词级边界框；click_text 按本次 OCR 完整文字唯一匹配点击，text 必填；open_url 打开网址；hotkey 执行 keys 快捷键；type_text 把 text 粘贴至当前焦点；windows 列出窗口；focus_window/close_window 按 title 完整标题唯一定位，不强制丢弃未保存内容；wechat_login 打开个人微信（排除企业微信，已在托盘运行时唤出窗口）供扫码或操作；wechat_status 检查状态。clipboard 读取剪贴板文字；复制整条消息优先 right_click 消息再点菜单里的“复制”，或选中后 hotkey ctrl+c，再 clipboard 读取，不要靠截图转写。right_click 按图片坐标右键打开菜单；hover 把鼠标停在图片坐标上，唤出悬浮按钮或提示。在聊天输入框发送消息优先 hotkey enter，不要点小的发送按钮。未传 reference_image 时自动使用该电脑本会话最近一张截图。代用户发消息/提交前：搜索出多个可能的对象（同名、备注相近）时不要自己挑，停下列出候选问用户；输入框里原有用户草稿时不要清空或覆盖，先问用户。scroll 在指定图片坐标区域按整格滚动，翻聊天记录等长列表时优先用它；drag 按图片坐标拖动滚动条滑块或窗口；click 点击没有文字的图标。三者坐标均按 image_width/image_height 映射，定位须用最新 reference_image。先判断多层遮挡和滚动容器；每步返回实际截图，执行过动作不等于任务完成，应继续查看。用界面编辑/保存过文件（WPS/Office 表格、文档、演示等）后，汇报前必须用 windows_system 的 inspect_file 回读文件真实内容核对，数字以文件为准，不以截图/OCR 为准。launch_app 按 name 启动应用（内置常用程序；节点未开启 shell 能力时只接受内置名称，开启后未知名称才走开始菜单搜索；结果靠截图验收）；hotkey win+r/ctrl+shift+esc 仅在开启 shell 能力后可用；shell 关闭时前台为命令行/VS Code/任务管理器时拒绝键盘输入，资源管理器地址栏不可输入命令行程序名；window_state 对 title/window_handle 定位的窗口最大化/最小化/还原。compress=true 可让返回截图按长边≤1600缩放转JPEG以提速，坐标换算不受影响。二维码回传当前来源会话，unknown 不能当作成功。本工具是视觉/OCR 通道：需要看界面或操作 GUI 应用才用；查文件/进程/系统信息、装软件、读写配置、执行命令等能用命令行完成的，优先用 windows_system 工具（对回连节点更快更稳）。新机器可用 windows_nodes 生成主动回连接入命令，已有 SSH 别名也可使用。',
        'parameters': {'type': 'object', 'properties': {
            'alias': {'type': 'string', 'description': 'Windows 主机别名，如 win11'},
            'elevated': {'type': 'boolean', 'default': False, 'description': '仅 SSH 别名通道且管理员已在服务端授权该别名时生效（否则忽略并在结果注明）：用当前账户可用最高权限执行交互任务；不更改UAC或账户权限'},
            'window_handle': {'type': 'integer', 'minimum': 1, 'description': 'focus_window/close_window/window_state/ui_tree/click_element可用：最新windows列表中的句柄，区分同名窗口；同时传title时也会核对标题'},
            'action': {'type': 'string', 'enum': ['initialize', 'screenshot', 'wechat_login', 'wechat_status', 'ocr', 'click_text', 'open_url', 'hotkey', 'type_text', 'type_secret', 'windows', 'focus_window', 'close_window', 'scroll', 'click', 'drag', 'clipboard', 'right_click', 'double_click', 'hover', 'launch_app', 'window_state', 'ui_tree', 'click_element']},
            'name': {'type': 'string', 'description': 'launch_app 必填：应用名称，内置 notepad/explorer/calc/mspaint/paint/edge/chrome；vscode/code（自带终端）及其余名称仅在节点开启 shell 能力后可用（其余走开始菜单搜索）；cmd/powershell 属 shell 能力不在此启动'},
            'args': {'type': 'array', 'items': {'type': 'string'}, 'description': 'launch_app 可选：传给内置程序的参数，如 vscode 打开项目文件夹 ["C:\\Users\\me\\Desktop\\hello"]、notepad 打开文件。给用户打开程序/项目/文件夹都用 launch_app（以用户普通权限打开）；不要在 windows_system run_powershell 里 Start-Process，那样窗口会带管理员权限'},
            'window_state_mode': {'type': 'string', 'enum': ['maximize', 'minimize', 'restore'], 'description': 'window_state 必填：最大化/最小化/还原目标窗口'},
            'compress': {'type': 'boolean', 'default': False, 'description': '截图/OCR 返回图按长边≤1600缩放并转JPEG压缩以提速；坐标换算仍以原始屏幕为准，点击定位不受影响'},
            'ocr_language': {'type': 'string', 'description': 'ocr/click_text 可选：auto 默认偏好中文，英文代码/数字可指定 en-US；实际可用语言由 OCR 返回的 available_languages 给出，未安装时明确报错；首次识别较慢可设 timeout=90'},
            'expected_texts': {'type': 'array', 'minItems': 1, 'maxItems': 10, 'items': {'type': 'string', 'minLength': 1, 'maxLength': 200}, 'description': 'ocr 验收时填用户要求的完整文字行；仅忽略空白，其余字符和数字严格匹配。ocr_verified 才表示对应识别通过；ocr_mismatch 明确为失败。ocr_ready 只表示识别调用完成'},
            'reference_image': {'type': 'string', 'description': 'click/double_click/right_click/hover/drag 或指定滚动位置必填：最新完整截图路径；旧图超时或屏幕尺寸变化时拒绝操作'},
            'image_width': {'type': 'integer', 'minimum': 1, 'description': '模型当前所见完整图片的宽度，可能小于原始截图'},
            'image_height': {'type': 'integer', 'minimum': 1, 'description': '模型当前所见完整图片的高度'},
            'x': {'type': 'number', 'minimum': 0, 'description': '图片内横坐标；click点击、drag起点、scroll鼠标所在滚动区域'},
            'y': {'type': 'number', 'minimum': 0, 'description': '图片内纵坐标'},
            'end_x': {'type': 'number', 'minimum': 0, 'description': 'drag终点横坐标，可拖动滚动条滑块或窗口'},
            'end_y': {'type': 'number', 'minimum': 0, 'description': 'drag终点纵坐标'},
            'amount': {'type': 'integer', 'minimum': -20, 'maximum': 20, 'description': 'scroll 滚动刻度：负数向下，正数向上，默认 -5'},
            'credential': {'type': 'string', 'description': 'type_secret 必填：仓库外凭据引用名，先聚焦正确输入框，避免把密码写入对话'},
            'input_method': {'type': 'string', 'enum': ['clipboard', 'unicode'], 'default': 'clipboard', 'description': 'type_text 的输入通道（type_secret 固定 unicode 直接键入，从不经剪贴板）；剪贴板粘贴无效或输入法干扰时选 unicode 直接发送文字，动作后需截图验收，不应编写临时键盘脚本'},
            'field': {'type': 'string', 'enum': ['username', 'password'], 'description': 'type_secret 要输入的字段'},
            'text': {'type': 'string', 'description': 'click_text 必填：最新 OCR 返回的目标完整文字；click_element 必填：ui_tree 返回的控件名称（先整名再唯一子串匹配）；ui_tree 可选：按名称/值过滤；type_text 要输入的文字'},
            'url': {'type': 'string', 'description': 'open_url 必填：http/https 网址'},
            'browser': {'type': 'string', 'enum': ['msedge', 'chrome', 'firefox'], 'description': 'open_url 可选：指定浏览器；不填用系统默认，未设默认浏览器时自动选已安装的 Edge/Chrome，避免弹出“选择应用”对话框'},
            'control_type': {'type': 'string', 'description': 'ui_tree/click_element 可选：控件类型，如 Button/Edit/ListItem/MenuItem/TreeItem/TabItem/Hyperlink/CheckBox/ComboBox'},
            'keys': {'type': 'string', 'enum': sorted(HOTKEY_NAMES)},
            'title': {'type': 'string', 'description': 'focus_window/close_window 必填（或用 window_handle）：windows 返回的完整标题（零宽字符/多余空白不影响匹配）；ui_tree/click_element 可选：目标窗口，默认前台窗口'},
            'timeout': {'type': 'integer', 'minimum': 10, 'maximum': 120, 'default': 35},
        }, 'required': ['alias', 'action']},
    },
}]
