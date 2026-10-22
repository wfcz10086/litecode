"""kb_router.py — 内置知识库 /api/kb (实现在 plugins/kb/router.py)。

拔插: 删掉本文件即卸下全部接口; 或 config.json 去掉 kb 段, 接口仍在但只回 enabled=false。
"""
from plugins.kb.router import router  # noqa: F401

ORDER = 60
