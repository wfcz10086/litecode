"""litecodeext/core/ — 核心业务模块 (agent 编排 / 工具 / 搜索 / 记忆 / 基础设施)

顶层 entrypoint (litecode_server.py / web_ui.py / wechat_bridge.py / litecode.py / cli.py)
通过在 import 前把本目录加入 sys.path, 让本目录下的模块仍可以用扁平名称互相 import
(`from blackboard import X`), 最小化改动面。
"""
