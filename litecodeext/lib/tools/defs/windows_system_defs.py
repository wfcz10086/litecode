"""Windows 节点系统运维与文件传输工具定义（非视觉通道）。"""
WINDOWS_SYSTEM_DEFS = [{
    'type': 'function', 'function': {
        'name': 'windows_system',
        'description': (
            '主动回连 Windows 节点的系统运维与文件传输（命令行/非视觉通道）。能用命令行完成的事'
            '（查看/增删文件、看进程、装软件、读写配置、查系统信息）优先用本工具，比截图点击更快更稳；但用户明确指定了界面做法（如「用记事本」「用鼠标拖拽」「在 WPS 里改」）时必须照做，不要换成命令行；'
            '只有需要看界面或操作 GUI 应用（如用 WPS 编辑文档、处理图形界面弹窗）才改用 windows_desktop 的 '
            'ocr/click_text/点击。动作：'
            'system_info 返回系统版本/主机名/CPU/内存/磁盘/开机时长/登录用户；'
            'processes 按 cpu 或 memory 排序的前 top 个进程，可用 name 过滤；'
            'kill_process 按 pid 或精确进程名结束，拒绝结束关键/常驻系统进程与会话 0 服务进程，结束 explorer 需 force=true；'
            '按名称命中多个进程时拒绝并列出 pid，需改用 pid 或显式 all=true；'
            'services 只读列出服务及状态（代理普通用户权限，不做启停）；'
            'list_dir 列目录（名称/大小/修改时间/是否目录，最多 500 项）；'
            'download 把 Windows 上的文件（≤1GB，约 2MB/s）取回服务端工作区，返回服务端路径并已按 sha256 校验整文件；'
            'upload 把服务端工作区 uploads/ 内的文件（≤1GB）发到 Windows 的 %USERPROFILE%\\Downloads\\LiteCode\\，同名自动改名不覆盖，Windows 端按 sha256 校验；'
            'run_powershell 执行 PowerShell：默认关闭，只有用户经 Web/CLI 为该节点开启后可用，模型不能自行开启；'
            '支持 cwd 工作目录，返回 exit_code/stdout/stderr/duration_ms，输出按 UTF-8 解码（失败回退 GBK），'
            '输出各截断 8000 字符，timeout 默认 60 秒上限 300 秒。'
            'inspect_file 回读核对：把 Windows 上的文件（≤50MB，路径支持 %USERPROFILE% 等环境变量）取回服务端并解析真实内容——'
            'xlsx 返回各 sheet 单元格的地址/公式/缓存值与公式清单，docx 返回段落文本与字数，pptx 按页序返回每页文字，'
            'txt/csv/md/json/log 返回前 8000 字，其他类型只返回大小与 sha256。'
            '凡是用界面操作（windows_desktop 输入/粘贴/点击/快捷键）改过或保存过的文件，汇报结论前必须用 inspect_file 回读；'
            '汇报里的数字、文字以 inspect_file 结果为准，不以截图/OCR 为准，截图与文件不一致时以文件为准并如实说明。'
            'kill_process、upload、download、run_powershell 为有副作用动作，执行后应回读（list_dir/processes/system_info 或再截图）确认结果，不要盲目重试。'
            '仅支持已接入且代理为最新版本的回连节点；版本过低会提示等待自动更新。'),
        'parameters': {'type': 'object', 'properties': {
            'alias': {'type': 'string', 'description': '回连节点别名'},
            'action': {'type': 'string', 'enum': ['system_info', 'processes', 'kill_process', 'services',
                                                  'list_dir', 'download', 'upload', 'run_powershell', 'inspect_file']},
            'sort': {'type': 'string', 'enum': ['cpu', 'memory'], 'default': 'memory', 'description': 'processes 排序维度'},
            'top': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 15, 'description': 'processes 返回条数'},
            'name': {'type': 'string', 'description': 'processes/services 过滤关键词，或 kill_process 的精确进程名'},
            'pid': {'type': 'integer', 'minimum': 1, 'description': 'kill_process 目标进程号（与 name 二选一）'},
            'force': {'type': 'boolean', 'default': False, 'description': 'kill_process 结束 explorer 等受保护进程时必须为 true'},
            'all': {'type': 'boolean', 'default': False, 'description': 'kill_process 按名称命中多个进程时，确认全部结束须为 true'},
            'path': {'type': 'string', 'description': 'list_dir 的目录，或 download / inspect_file 的 Windows 文件路径（支持 %USERPROFILE% 等环境变量）'},
            'source': {'type': 'string', 'description': 'upload 必填：服务端工作区内的源文件路径（必须位于工作区内）'},
            'dest_name': {'type': 'string', 'description': 'upload 可选：落地到 Windows 的文件名，默认取源文件名；会清洗并防穿越'},
            'script': {'type': 'string', 'description': 'run_powershell 必填：要执行的 PowerShell 脚本'},
            'cwd': {'type': 'string', 'description': 'run_powershell 可选：工作目录（必须已存在）'},
            'timeout': {'type': 'integer', 'minimum': 1, 'maximum': 300, 'default': 60, 'description': 'run_powershell 超时秒数'},
        }, 'required': ['alias', 'action']},
    },
}]
