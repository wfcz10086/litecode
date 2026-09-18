"""
lib/thinking_adapter.py — 统一推理参数适配器
════════════════════════════════════════════════════
不同后端的 thinking/reasoning 参数格式完全不同:
  vllm (Qwen3)     → chat_template_kwargs: {"enable_thinking": bool}
  openai (GLM-5, MiniMax等)  → 不支持 thinking，跳过
  anthropic         → thinking: {"type": "enabled", "budget_tokens": N}
  ollama (0.9+)     → think: true
  deepseek          → 模型自带推理，无需参数

核心问题: vLLM 的 thinking tokens 和 content tokens 共享 max_tokens。
当 thinking 消耗过多 token 时，tool_call JSON 被截断 → write_file 丢失 filepath。
解决: 对 vLLM 后端，max_tokens += thinking_budget。
"""

import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("openclaw")

# 已知的"服务栈" —— 即 inject_params 里真正分支到的那些值。deployment 字段只有落在
# 这个集合里才被当作服务栈采信 (见 ThinkingConfig.stack)。新增分支时记得同步这里,
# 否则那个 deployment 会被静默忽略、退回 backend_type。
_KNOWN_STACKS = frozenset({"vllm", "anthropic", "ollama", "openai", "openai-chat", "deepseek"})


@dataclass
class ThinkingConfig:
    enabled: bool = False
    budget: int = 8192
    backend_type: str = "vllm"
    # [thinking-route 2026-08-26] 见下方 stack() —— backend_type 只说线路协议,
    # deployment 说服务栈, 判 thinking 参数格式必须看后者。
    deployment: str = ""

    def stack(self) -> str:
        """真正决定 thinking 参数格式的"服务栈"。

        backend_type 混淆了两件事: vLLM 说的是 OpenAI 协议 (backend_type="openai" 没错),
        但它同时**是** vLLM, 需要 chat_template_kwargs (要走 vllm 分支)。两个都对, 而
        inject_params 只能按一个字段分支 —— 实测 Qwen3.6-35B 就是这么漏的:
        deployment="vllm" 但 backend_type="openai" → 落进 openai 分支空转返回,
        chat_template_kwargs 没发, 关键的 max_tokens += budget 补偿也跳过
        (该补偿注释写明: 不加大会导致 thinking 吃掉预算 → tool_call JSON 截断 →
        write_file 丢失 filepath)。

        但 deployment 字段里**不全是服务栈** —— 实测生产 config 里它有 3 种取值:
        `vllm` (服务栈) / `gateway` / `official` (后两个是**接入拓扑**标签, 说的是"怎么连
        上去", 不是"对面跑的什么")。所以只有当 deployment 确实命名了一个已知服务栈时才
        采信它, 否则一律退回 backend_type。
        (最初版本无条件优先 deployment, 被本模块的单测抓出回归: deepseek-v4-pro 的
         deployment="gateway" 匹配不上任何分支 → reasoning_effort 再也发不出去。)
        """
        dep = (self.deployment or "").lower()
        if dep in _KNOWN_STACKS:
            return dep
        return (self.backend_type or "vllm").lower()


_active: Optional[ThinkingConfig] = None


def _get_loaded_live():
    """ 只在 lib.config 已加载时返回 _LIVE, 不主动触发它的加载.
    防止: 我们的 init() 触发 lib.config 首次 import,
    后者的 _load_thinking_adapter 反过来再调 init(_MDL),
    把用户的入参覆盖回 config.json 默认值."""
    import sys
    pkg = __name__.rsplit(".", 1)[0]  # "lib"
    cfg_mod = sys.modules.get(pkg + ".config")
    if cfg_mod is None:
        return None
    return getattr(cfg_mod, "_LIVE", None)


def init(model_cfg: dict):
    global _active
    _active = ThinkingConfig(
        enabled=model_cfg.get("enable_thinking", False),
        budget=model_cfg.get("thinking_budget", 8192),
        backend_type=model_cfg.get("backend_type", "vllm"),
        deployment=str(model_cfg.get("deployment", "") or "").strip().lower(),
    )
    log.debug(f"[thinking] init: type={_active.backend_type} "
              f"enabled={_active.enabled} budget={_active.budget}")
    #  同时把值同步到 lib.config._LIVE (单一真源),
    # 但只在 _LIVE 已存在时同步, 不主动触发 import 防止循环初始化.
    live = _get_loaded_live()
    if live is not None:
        live["enable_thinking"] = bool(_active.enabled)
        live["thinking_budget"] = int(_active.budget)
        live["backend_type"]    = str(_active.backend_type)
        live["deployment"]      = str(_active.deployment)


def update(model_cfg: dict):
    init(model_cfg)


def get() -> ThinkingConfig:
    return _active or ThinkingConfig()


def _live_cfg(force_disable: bool = False) -> ThinkingConfig:
    """
     优先从 lib.config._LIVE 现读, 避免:
      1. 启动时 _MDL 与 models[] 里激活模型不一致 → 缓存值错误
      2. /v1/model/switch 之后 _LIVE 已更新但 _active 未同步 → 仍走旧值
    _LIVE 是 reload_model 维护的运行时单一真源, 直接读它最稳。
    若 _LIVE 尚未存在 (例: 单测里只 import 了 thinking_adapter),
    退回 _active 缓存。
    [v12.8-fix] _active 为 None 时用 _LIVE 回填, 保证首请求不走旧缓存。
    """
    global _active
    live = _get_loaded_live()
    if live is not None:
        if _active is None:
            _active = ThinkingConfig(
                enabled=bool(live.get("enable_thinking", False)),
                budget=int(live.get("thinking_budget", 8192)),
                backend_type=str(live.get("backend_type", "vllm")),
                deployment=str(live.get("deployment", "") or ""),
            )
        return ThinkingConfig(
            enabled=bool(live.get("enable_thinking", False)) and not force_disable,
            budget=int(live.get("thinking_budget", 8192)),
            backend_type=str(live.get("backend_type", "vllm")),
            deployment=str(live.get("deployment", "") or ""),
        )
    cfg = get()
    return ThinkingConfig(
        enabled=cfg.enabled and not force_disable,
        budget=cfg.budget,
        backend_type=cfg.backend_type,
        deployment=cfg.deployment,
    )


def inject_params(payload: dict, *, force_disable: bool = False) -> dict:
    """
    往 API payload 注入正确格式的 thinking 参数。
    force_disable=True 时强制关闭（用于 memory 压缩等场景）。
     直接读 _LIVE, 不再依赖 _active 缓存。
    """
    cfg = _live_cfg(force_disable=force_disable)
    enabled = cfg.enabled
    _model_id = (payload.get("model") or "").lower()

    _stack = cfg.stack()
    if _stack == "vllm":
        # vLLM/Qwen3: 必须用 chat_template_kwargs 显式控制
        ctk = {"enable_thinking": enabled}
        # [2026-05-25] Qwen3.6 新增 preserve_thinking — 保留历史推理链, agent
        # 决策一致性显著提升 (Qwen3.5 没有此参数, 设了也被忽略, 安全).
        if enabled and "qwen3.6" in _model_id:
            ctk["preserve_thinking"] = True
        payload["chat_template_kwargs"] = ctk
        if enabled:
            # ⚡ thinking tokens 共享 max_tokens，必须加大以防 tool_call 截断
            base_max = payload.get("max_tokens", 8192)
            payload["max_tokens"] = base_max + cfg.budget
            payload.pop("temperature", None)
            # [effort-compat 2026-09-03] 同时发官方 reasoning_effort (low/medium/high)。
            # 各模型对思考强度的控制方式不一, 保留兼容: 真控制仍靠上面的 budget
            # (实测本自建 qwen3.8 收 reasoning_effort 但**忽略**它, HTTP 200 而思考量无差别,
            #  none/low/high 量乱序 — 见 commit body); 但认 effort 的上游 (标准 vLLM 新版)
            #  会据此自动分级。budget→effort 映射, 与前端 🧠 chip 档位对齐:
            #    ≤2048=low(前端"低") / ≤8000=medium(前端"中") / >8000=high(前端"高")。
            # 若调用方已显式传 reasoning_effort, 尊重它不覆盖。
            if "reasoning_effort" not in payload:
                _b = cfg.budget
                payload["reasoning_effort"] = ("low" if _b <= 2048
                                               else "medium" if _b <= 8000
                                               else "high")

    elif _stack == "anthropic":
        if enabled:
            payload["thinking"] = {"type": "enabled", "budget_tokens": cfg.budget}
            payload.pop("temperature", None)
        else:
            payload.pop("thinking", None)

    elif _stack == "ollama":
        if enabled:
            payload["think"] = True
        else:
            payload.pop("think", None)

    elif _stack in ("openai", "openai-chat"):
        # [2026-05-25] 大多数 OpenAI 兼容 API (GLM-5/MiniMax) 不支持 thinking 参数, 跳过.
        # 但 DeepSeek V4 系列 (deepseek-v4-pro / deepseek-v4-flash / deepseek-chat 等)
        # 走 OpenAI 协议且**强制要求**第一轮请求声明 reasoning 支持, 否则上游
        # 流回 reasoning_content 但 client 没回传 → 第二轮必爆 400:
        #   "The reasoning_content in the thinking mode must be passed back to the API"
        # 官方契约 (api-docs.deepseek.com/guides/thinking_mode):
        #   - reasoning_effort: 只接受 "high" 或 "max", low/medium 自动映射 high
        #   - thinking: {"type": "enabled" | "disabled"}, 走 extra_body 平铺到 body
        # 不影响 deepseek-reasoner (R1 老版) — R1 文档明确说 reasoning_content 不能
        # 入 input, 这里也不主动加 thinking 字段, R1 模型识别后会走 reasoning_model 路径.
        if "deepseek-v4" in _model_id or "deepseek-chat" in _model_id or "deepseek-flash" in _model_id:
            if enabled:
                payload["reasoning_effort"] = "high"
                payload["thinking"] = {"type": "enabled"}
            else:
                payload["thinking"] = {"type": "disabled"}
                payload.pop("reasoning_effort", None)

    elif _stack == "deepseek":
        if enabled:
            base_max = payload.get("max_tokens", 8192)
            payload["max_tokens"] = base_max + cfg.budget

    return payload


def inject_for_compress(payload: dict) -> dict:
    """
    Memory 压缩/smart-memory 专用.
    [P54+] 用户拍板: 记忆压缩一律关 thinking (提速 + 避免 reasoning 浪费 token)。
    仅当 vLLM 部署强制 enable_thinking=True 才退回开启 (避免 400 报错)。
    extract_content 已能剥离 <think> 标签, 所以开启也不污染 content。
    """
    cfg = _live_cfg()
    _stack = cfg.stack()
    if _stack == "vllm":
        # [compress-thinking fix 2026-08-28] 原来是"跟随激活模型的 enable_thinking"
        # (顾虑: 部分老 vLLM 部署 Qwen3.5 强制 parser, 关了会 400)。
        # 实测该顾虑对当前部署不成立, 而跟随配置会造成**记忆压缩全面失效**:
        #   压缩 max_tokens 只有 4096, 开着思考时模型把 4096 全烧在思考上
        #   (finish_reason=length), content 产出 0 字符 → extract_content 按设计
        #   回退把"思考文本"当答案 → 思考是自由散文没有 ## 分节 → 质检
        #   "no ## sections found" 判 FAIL → 整份新记忆被丢弃。
        # 真实复现 (wx-b3607 会话, 18798 字符 prompt):
        #   开思考: 思考 13298c / content 0c / tokens=4096 / finish=length → FAIL
        #   关思考: 思考     0c / content 4624c / tokens=1317 / finish=stop  → OK
        # 该会话连续 4 次压缩失败, 记忆退化到只剩 444 字节。
        # 故按本函数 docstring 的原意**强制关闭**。
        # 若将来某个后端关了真的 400, 在这里加针对性豁免, 不要改回全局跟随。
        payload["chat_template_kwargs"] = {"enable_thinking": False}
        # 关思考后 temperature 可以保留 (压缩要确定性, manager 传的是 0.1)
    elif _stack == "ollama":
        payload["think"] = False
    elif _stack == "anthropic":
        payload.pop("thinking", None)
    # openai/deepseek 等不支持 thinking 参数的后端自然不需要处理
    return payload


def extract_content(message: dict) -> str:
    """
     统一提取 LLM 响应的实际内容, 屏蔽不同后端的差异:
    - vLLM 有 parser: message.content = 纯答案 (reasoning 在 reasoning_content)
    - vLLM 无 parser: message.content = "<think>...</think>答案" (要剥离)
    - Ollama /v1/: 同上两种都可能
    - 极端情况: content 为空, 实际答案在 reasoning_content (此时把 reasoning 当答案)

    优先级: 剥离后的 content > reasoning_content(fallback) > ""
    """
    import re
    # 1) 尝试从 content 读
    content = (message.get("content") or "").strip()
    # 2) 不管哪个后端, 一律剥离 <think>...</think>（防 parser 没配好）
    if "<think>" in content or "</think>" in content:
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        # 处理只有 </think> 没有 <think> 的情况（某些 Qwen3-Thinking 模板）
        if "</think>" in content:
            content = content.split("</think>", 1)[-1].strip()
    # 3) content 空 → fallback 到 reasoning_content / reasoning / thinking
    if not content:
        for fkey in ("reasoning_content", "reasoning", "thinking"):
            fv = (message.get(fkey) or "").strip()
            if fv:
                # reasoning 作为答案时也剥离思考标签
                fv = re.sub(r"<think>.*?</think>", "", fv, flags=re.DOTALL).strip()
                if fv:
                    return fv
    # 4) anthropic 多块格式
    if not content:
        c = message.get("content", [])
        if isinstance(c, list):
            content = "\n".join(
                b.get("text", "") for b in c
                if isinstance(b, dict) and b.get("type") == "text"
            ).strip()
    return content
