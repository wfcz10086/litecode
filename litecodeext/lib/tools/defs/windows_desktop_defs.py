"""Windows 桌面与微信登录工具定义。"""
from lib.windows_desktop_actions import HOTKEYS as _HOTKEYS

HOTKEY_NAMES = tuple(_HOTKEYS)   # 快捷键枚举直接取 Windows 端白名单, 两边不会再对不上
WINDOWS_DESKTOP_DEFS = [{
    'type': 'function', 'function': {
        'name': 'windows_desktop',
        'description': '通用 Windows 交互桌面操作。initialize 安装依赖；screenshot 截图；ocr 返回文字和词级边界框；click_text 按本次 OCR 完整文字唯一匹配点击，text 必填；open_url 打开网址；hotkey 执行 keys 快捷键；type_text 把 text 粘贴至当前焦点；windows 列出窗口；focus_window/close_window 按 title 完整标题唯一定位，不强制丢弃未保存内容；wechat_login 打开个人微信（排除企业微信，已在托盘运行时唤出窗口）供扫码或操作；wechat_status 检查状态。clipboard 读取剪贴板文字；复制整条消息优先 right_click 消息再点菜单里的“复制”，或选中后 hotkey ctrl+c，再 clipboard 读取，不要靠截图转写。right_click 按图片坐标右键打开菜单；hover 把鼠标停在图片坐标上，唤出悬浮按钮或提示。在聊天输入框发送消息优先 hotkey enter，不要点小的发送按钮。未传 reference_image 时自动使用该电脑本会话最近一张截图。代用户发消息/提交前：搜索出多个可能的对象（同名、备注相近）时不要自己挑，停下列出候选问用户；输入框里原有用户草稿时不要清空或覆盖，先问用户。scroll 在指定图片坐标区域按整格滚动，翻聊天记录等长列表时优先用它；drag 按图片坐标拖动滚动条滑块或窗口；click 点击没有文字的图标。三者坐标均按 image_width/image_height 映射，定位须用最新 reference_image。先判断多层遮挡和滚动容器；每步返回实际截图，执行过动作不等于任务完成，应继续查看。launch_app 按 name 启动应用（内置常用程序，未知名称走开始菜单搜索，结果靠截图验收）；window_state 对 title/window_handle 定位的窗口最大化/最小化/还原。compress=true 可让返回截图按长边≤1600缩放转JPEG以提速，坐标换算不受影响。二维码回传当前来源会话，unknown 不能当作成功。本工具是视觉/OCR 通道：需要看界面或操作 GUI 应用才用；查文件/进程/系统信息、装软件、读写配置、执行命令等能用命令行完成的，优先用 windows_system 工具（对回连节点更快更稳）。新机器可用 windows_nodes 生成主动回连接入命令，已有 SSH 别名也可使用。',
        'parameters': {'type': 'object', 'properties': {
            'alias': {'type': 'string', 'description': 'Windows 主机别名，如 win11'},
            'elevated': {'type': 'boolean', 'default': False, 'description': '已授权时用当前账户可用最高权限执行交互任务，处理高权限窗口；不更改UAC或账户权限'},
            'window_handle': {'type': 'integer', 'minimum': 1, 'description': 'focus_window/close_window可用：最新windows列表中的句柄，区分同名窗口；同时传title时也会核对标题'},
            'action': {'type': 'string', 'enum': ['initialize', 'screenshot', 'wechat_login', 'wechat_status', 'ocr', 'click_text', 'open_url', 'hotkey', 'type_text', 'type_secret', 'windows', 'focus_window', 'close_window', 'scroll', 'click', 'drag', 'clipboard', 'right_click', 'double_click', 'hover', 'launch_app', 'window_state']},
            'name': {'type': 'string', 'description': 'launch_app 必填：应用名称，内置 notepad/explorer/calc/mspaint/edge/chrome，其余名称走开始菜单搜索；cmd/powershell 属 shell 能力不在此启动'},
            'window_state_mode': {'type': 'string', 'enum': ['maximize', 'minimize', 'restore'], 'description': 'window_state 必填：最大化/最小化/还原目标窗口'},
            'compress': {'type': 'boolean', 'default': False, 'description': '截图/OCR 返回图按长边≤1600缩放并转JPEG压缩以提速；坐标换算仍以原始屏幕为准，点击定位不受影响'},
            'reference_image': {'type': 'string', 'description': 'click/double_click/right_click/hover/drag 或指定滚动位置必填：最新完整截图路径；旧图超时或屏幕尺寸变化时拒绝操作'},
            'image_width': {'type': 'integer', 'minimum': 1, 'description': '模型当前所见完整图片的宽度，可能小于原始截图'},
            'image_height': {'type': 'integer', 'minimum': 1, 'description': '模型当前所见完整图片的高度'},
            'x': {'type': 'number', 'minimum': 0, 'description': '图片内横坐标；click点击、drag起点、scroll鼠标所在滚动区域'},
            'y': {'type': 'number', 'minimum': 0, 'description': '图片内纵坐标'},
            'end_x': {'type': 'number', 'minimum': 0, 'description': 'drag终点横坐标，可拖动滚动条滑块或窗口'},
            'end_y': {'type': 'number', 'minimum': 0, 'description': 'drag终点纵坐标'},
            'amount': {'type': 'integer', 'minimum': -20, 'maximum': 20, 'description': 'scroll 滚动刻度：负数向下，正数向上，默认 -5'},
            'credential': {'type': 'string', 'description': 'type_secret 必填：仓库外凭据引用名，先聚焦正确输入框，避免把密码写入对话'},
            'input_method': {'type': 'string', 'enum': ['clipboard', 'unicode'], 'default': 'clipboard', 'description': 'type_text/type_secret 的输入通道；剪贴板粘贴无效或输入法干扰时选 unicode 直接发送文字，动作后需截图验收，不应编写临时键盘脚本'},
            'field': {'type': 'string', 'enum': ['username', 'password'], 'description': 'type_secret 要输入的字段'},
            'text': {'type': 'string', 'description': 'click_text 必填：最新 OCR 返回的目标完整文字'},
            'url': {'type': 'string', 'description': 'open_url 必填：http/https 网址'},
            'keys': {'type': 'string', 'enum': sorted(HOTKEY_NAMES)},
            'title': {'type': 'string', 'description': 'focus_window/close_window 必填：windows 返回的完整标题'},
            'timeout': {'type': 'integer', 'minimum': 10, 'maximum': 120, 'default': 35},
        }, 'required': ['alias', 'action']},
    },
}]
