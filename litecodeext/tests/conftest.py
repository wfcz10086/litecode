"""litecodeext/tests 收集配置。

下列文件名叫 test_*.py, 实为手动跑的独立脚本: 模块顶层就连生产服务 (18789/18790)、
用写死的密码登录、起浏览器发真实指令。被 pytest 收集即等于对生产跑一遍, 必须排除。
需要时按各文件头部的"用法"直接 python3 运行。
"""
collect_ignore = [
    "test_e2e_desktop_chat.py",   # 顶层 sync_playwright 登录 18790 并让 AI 操作桌面
    "test_e2e_playwright.py",     # 完整 E2E 脚本 (argparse), 依赖在线 Web UI
    "test_v123_fixes.py",         # 命令行传入服务器地址/令牌, 测在线网关
]

# [sec 2026-10-19] Windows 控制端密钥已挪出工作区 (lib/key_store): 测试统一落到临时目录,
# 不污染宿主 ~/.litecode/keys; 每个临时工作区按路径摘要各有一套, 互不串用。
import os as _os
import tempfile as _tempfile
_os.environ.setdefault("LITECODE_KEY_DIR", _tempfile.mkdtemp(prefix="lc_test_keys_"))
