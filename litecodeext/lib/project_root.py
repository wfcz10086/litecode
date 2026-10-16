"""写文件追踪用的两个纯函数: 推断项目根目录 / 判断一次性脚本。

2026-10-16 实测: 脚本直接写在工作区根目录 (/ws/win11_shot2.py) 时, 旧逻辑取"工作区下第一级"
当项目目录, 把文件名当目录, 之后每轮都提醒"先创建 win11_shot2.py/PROJECT_MAP.md"。
"""
ONEOFF_PREFIXES = ("probe", "tmp", "scratch", "_")      # 同 SYSTEM-TEST 的口径


def is_oneoff(fname: str) -> bool:
    return fname.split(".")[0].lower().startswith(ONEOFF_PREFIXES)


def infer_project_root(workspace: str, filepath: str) -> str:
    """文件在工作区某个子目录里 → 返回该子目录; 直接放工作区根目录 / 不在工作区 → 返回 ""。"""
    ws = workspace.rstrip("/")
    if not filepath.startswith(ws + "/"):
        return ""
    rel = filepath[len(ws) + 1:]
    top, sep, _ = rel.partition("/")
    return f"{ws}/{top}" if top and sep else ""
