"""lib/skill_author.py — 用一句话描述让模型写 SKILL.md 草稿 (技能面板「新建技能」/ `/新建技能`)。

直接调当前默认模型的 OpenAI 兼容接口 (不走 agent 循环, 不会调用工具); 旁路模型可在
config.json side_models.skills 指定 (lib/side_model.py)。生成结果先进草稿区, 用户审核 + 安全扫描后才启用。
"""
from __future__ import annotations

import re

import httpx

from . import skill_registry as reg

PROMPT = """你是技能 (Agent Skill) 作者。根据用户的一句话需求, 写一个 SKILL.md。

要求:
1. 开头是 YAML frontmatter, 用 --- 包住, 字段:
   name: {name_hint}(小写字母/数字/连字符, 3-40 字符, 英文语义)
   title: 中文名 (2-12 字)
   description: 一句话说明"什么时候用这个技能" (中文, 60 字内, 写清触发场景)
   aliases: ["触发词1", "触发词2", ...]  (3-8 个用户可能说的中文短语)
2. 正文用中文, Markdown, 包含这些二级标题: ## 何时使用 / ## 步骤 / ## 注意事项 / ## Keywords
3. 步骤要具体可执行 (用哪些工具、先做什么后做什么、怎么验证结果), 不写空话; 全文 300-1500 字。
4. 不要包含任何删除系统文件、下载执行脚本、读取密钥、要求忽略之前指令之类的内容。
5. 只输出 SKILL.md 本身, 不要解释, 不要用 ``` 包裹。

用户需求: {desc}"""


def _endpoint(model_id: str = "") -> tuple[str, str, str]:
    from .config import CFG, find_model_entry, wire_model_name
    mid = model_id or CFG.get("default_model_id") or (CFG.get("model") or {}).get("id") or ""
    try:
        m = find_model_entry(mid) or {}
    except Exception:
        m = {}
    if not m:
        m = CFG.get("model") or {}
    url, key = str(m.get("backend_url") or "").rstrip("/"), str(m.get("api_key") or "EMPTY")
    try:
        from .side_model import resolve
        url, mid, key = resolve("skills", url, mid, key)
    except Exception:
        pass
    try:
        wire = wire_model_name(mid)
    except Exception:
        wire = mid
    return url.rstrip("/"), wire, key


def clean(text: str) -> str:
    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    m = re.search(r"```(?:markdown|md)?\s*\n(.*?)```", t, re.S)
    if m and "---" in m.group(1):
        t = m.group(1).strip()
    i = t.find("---")
    return t[i:].strip() if i > 0 else t


def normalize(md: str, name_hint: str = "") -> tuple[str, str]:
    """补齐 frontmatter name, 返回 (技能名, SKILL.md)。"""
    fm = reg.frontmatter(md)
    name = reg.slugify(name_hint or fm.get("name") or "") or f"my-skill-{abs(hash(md)) % 10000}"
    if not fm:
        md = f"---\nname: {name}\ndescription: {name}\n---\n\n{md}"
    elif fm.get("name") != name:
        md = re.sub(r"(?m)^name:.*$", f"name: {name}", md, count=1) if re.search(r"(?m)^name:", md) \
            else md.replace("---\n", f"---\nname: {name}\n", 1)
    return name, md.strip() + "\n"


async def generate(desc: str, name: str = "", timeout: float = 120, model_id: str = "") -> tuple[str, str]:
    url, model, key = _endpoint(model_id)
    if not url:
        raise RuntimeError("没有可用的模型配置 (config.json models / default_model_id)")
    payload = {"model": model, "stream": False, "temperature": 0.3, "max_tokens": 4000,
               "messages": [{"role": "user", "content": PROMPT.format(desc=desc.strip()[:800],
                                                                    name_hint=name or "自拟")}]}
    try:
        from .thinking_adapter import inject_for_compress
        inject_for_compress(payload)       # 生成草稿不需要长思考
    except Exception:
        pass
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(f"{url}/chat/completions", json=payload,
                         headers={"Authorization": f"Bearer {str(key or '').strip() or 'EMPTY'}", "Content-Type": "application/json"})
    if r.status_code != 200:
        raise RuntimeError(f"模型返回 HTTP {r.status_code}: {r.text[:200]}")
    msg = r.json()["choices"][0]["message"]
    try:
        from .thinking_adapter import extract_content
        text = extract_content(msg)
    except Exception:
        text = msg.get("content") or ""
    md = clean(text)
    if "description" not in md:
        raise RuntimeError("模型没有按格式输出 SKILL.md, 请换个说法重试")
    return normalize(md, name)
