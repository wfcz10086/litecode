"""memory/user_facts.py — 从对话轻量提取用户信息 / 服务端点, 增量写 USER.md / TOOLS.md."""
from __future__ import annotations

import re as _re
from pathlib import Path

# 用户信息提取
_USER_INFO_PATTERNS = [
    (_re.compile(r'(?:我(?:叫|是|的名字[是叫]?))\s*([^\s，。,\.]{2,10})'), 'name'),
    (_re.compile(r'(?:我在|我们公司|公司(?:叫|是|名))\s*([^\s，。,\.]{2,20})'), 'company'),
    (_re.compile(r'(?:我(?:喜欢|偏好|习惯|想要))\s*(.{2,30}?)(?:[，。,\.]|$)'), 'preference'),
    (_re.compile(r'(?:我(?:在|的时区))\s*([\w/]+)'), 'timezone'),
]

# 服务/路径创建检测
_TOOLS_PATTERNS = [
    _re.compile(r'(?:port|端口)\s*[:=]?\s*(\d{4,5})'),
    _re.compile(r'(?:Written|created|saved|写入)\s+.*?->\s+(/\S+)'),
    _re.compile(r'http://(?:localhost|0\.0\.0\.0|127\.0\.0\.1):(\d+)(/\S*)?\s'),
]


def extract_user_info(messages: list) -> dict:
    """从对话消息中提取用户信息. 返回 {category: [values]}."""
    info: dict = {}
    for m in messages:
        if m.get("role") != "user":
            continue
        text = m.get("content") or ""
        if isinstance(text, list):
            text = " ".join(b.get("text", "") for b in text if isinstance(b, dict))
        if not isinstance(text, str):
            continue
        for pattern, category in _USER_INFO_PATTERNS:
            for match in pattern.finditer(text):
                info.setdefault(category, []).append(match.group(1).strip())
    return info


def extract_services(messages: list) -> list:
    """从工具执行结果中提取新创建的服务/路径信息."""
    services: list = []
    for m in messages:
        content = m.get("content") or ""
        if isinstance(content, list):
            content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
        if not isinstance(content, str):
            continue
        for pattern in _TOOLS_PATTERNS:
            for match in pattern.finditer(content):
                services.append(match.group(0).strip())
    return services


def auto_update_user_md(
    workspace: Path,
    session_id: str,
    sessions_dir: Path,
    template_dir: Path,
    base_dir: Path,
    messages: list,
) -> bool:
    """从对话中提取用户信息, 自动更新 USER.md. 返回是否有更新."""
    import logging
    log = logging.getLogger("openclaw")

    info = extract_user_info(messages)
    if not info:
        return False

    sess_dir = sessions_dir / session_id
    user_md_path = None
    for d in [sess_dir, template_dir, base_dir / "workspace_template"]:
        p = d / "USER.md"
        if p.exists():
            user_md_path = p
            break

    current = user_md_path.read_text(errors="replace") if user_md_path else ""

    updates = [(cat, val) for cat, vals in info.items() for val in vals if val not in current]
    if not updates:
        return False

    sess_dir.mkdir(parents=True, exist_ok=True)
    write_path = sess_dir / "USER.md"
    if not write_path.exists() and user_md_path:
        write_path.write_text(current)
    content = write_path.read_text(errors="replace") if write_path.exists() else current

    for category, val in updates:
        if category == "name":
            content = _re.sub(r'(\*\*Name:\*\*\s*)(\S+)', rf'\g<1>{val}', content)
        elif category == "company":
            if val not in content:
                content = content.rstrip() + f"\n  - 公司: {val}\n"
        elif category == "preference":
            if val not in content:
                if "## 偏好" in content:
                    content = content.replace("## 偏好", f"## 偏好\n- {val}", 1)
                else:
                    content = content.rstrip() + f"\n\n## 偏好\n- {val}\n"
        elif category == "timezone":
            content = _re.sub(r'(\*\*Timezone:\*\*\s*)(\S+)', rf'\g<1>{val}', content)

    write_path.write_text(content)
    log.info(f"  [auto-learn:{session_id[:8]}] USER.md updated: {[u[1] for u in updates]}")
    return True


def auto_update_tools_md(
    workspace: Path,
    session_id: str,
    sessions_dir: Path,
    template_dir: Path,
    base_dir: Path,
    messages: list,
) -> bool:
    """从工具执行结果中提取服务/路径信息, 自动更新 TOOLS.md."""
    import logging
    log = logging.getLogger("openclaw")

    services = extract_services(messages[-20:])
    if not services:
        return False

    sess_dir = sessions_dir / session_id
    tools_md_path = None
    for d in [sess_dir, template_dir, base_dir / "workspace_template"]:
        p = d / "TOOLS.md"
        if p.exists():
            tools_md_path = p
            break

    current = tools_md_path.read_text(errors="replace") if tools_md_path else ""
    new_entries = [s for s in services if s not in current]
    if not new_entries:
        return False

    sess_dir.mkdir(parents=True, exist_ok=True)
    write_path = sess_dir / "TOOLS.md"
    if not write_path.exists() and tools_md_path:
        write_path.write_text(current)
    content = write_path.read_text(errors="replace") if write_path.exists() else current

    import datetime
    ts = datetime.datetime.now().strftime("%H:%M")
    additions = "\n".join(f"- [{ts}] {e}" for e in new_entries[:5])

    if "## 服务地址" in content:
        content = content.replace("## 服务地址", f"## 服务地址\n{additions}", 1)
    else:
        content = content.rstrip() + f"\n\n## 本次会话服务\n{additions}\n"

    write_path.write_text(content)
    log.info(f"  [auto-learn:{session_id[:8]}] TOOLS.md updated: {new_entries[:3]}")
    return True


def register_dynamic_skill(skills_dir: Path, name: str, content: str) -> bool:
    """动态注册一个新 skill, 将 SKILL.md 写入 skills 目录."""
    import logging
    log = logging.getLogger("openclaw")
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content)
    log.info(f"  [skill] registered dynamic skill: {name}")
    return True
