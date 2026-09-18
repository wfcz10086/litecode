#!/usr/bin/env python3
"""tests/test_thinking_route.py — thinking 参数下发路由 + CLI 思考流显示

覆盖 2026-08-26 实测发现的两个断链:

1. **参数下发**: `thinking_adapter.inject_params` 原来只按 `backend_type` 分支, 而
   `backend_type` 混淆了"线路协议"和"服务栈" —— vLLM 说 OpenAI 协议 (backend_type
   ="openai" 没错) 但它**是** vLLM (需要 chat_template_kwargs)。实测 Qwen3.6-35B 就是
   `deployment="vllm"` + `backend_type="openai"`, 落进 openai 分支空转返回:
   chat_template_kwargs 没发, 关键的 `max_tokens += thinking_budget` 补偿也跳过
   (该补偿防的是 "thinking 吃掉预算 → tool_call JSON 截断 → write_file 丢 filepath")。

2. **CLI 丢思考流**: `litecli.py` 两处 SSE 消费点都只读 `delta.content`,
   `delta.reasoning` 整段静默丢弃 —— Web 有折叠块、CLI 什么都看不到, 三端不一致。
"""
import json
import re
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "litecodeext"
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "lib"))

import lib.thinking_adapter as TA  # noqa: E402

_fails: list[str] = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  [{'OK' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        _fails.append(name)


def _inject(model_cfg: dict, payload: dict | None = None) -> dict:
    """用给定模型配置跑一次 inject_params, 隔离 _LIVE 干扰。"""
    TA._active = None
    TA.init(model_cfg)
    # init() 会同步进 lib.config._LIVE (若已加载), _live_cfg 优先读 _LIVE, 所以
    # 这里必须让 _LIVE 也带上本次的值, 否则读到的是进程启动时的真实生产配置。
    live = TA._get_loaded_live()
    if live is not None:
        live.update({
            "enable_thinking": model_cfg.get("enable_thinking", False),
            "thinking_budget": model_cfg.get("thinking_budget", 8192),
            "backend_type":    model_cfg.get("backend_type", "vllm"),
            "deployment":      str(model_cfg.get("deployment", "") or "").lower(),
        })
    p = dict(payload or {"model": model_cfg["id"], "max_tokens": 8192, "temperature": 0.7})
    return TA.inject_params(p)


# ── 1. 回归本次实际踩到的配置 ────────────────────────────────────────
print("\n=== 1. vLLM 部署但 backend_type=openai (生产实际配置, 本次 bug 现场) ===")
QWEN = {"id": "Qwen3.6-35B", "deployment": "vllm", "backend_type": "openai",
        "enable_thinking": True, "thinking_budget": 4096, "max_tokens": 8192}

out = _inject(QWEN)
check("发出了 chat_template_kwargs (修复前整段跳过)",
      "chat_template_kwargs" in out, f"got keys={sorted(out)}")
check("chat_template_kwargs.enable_thinking == True",
      out.get("chat_template_kwargs", {}).get("enable_thinking") is True,
      f"got={out.get('chat_template_kwargs')}")
check("Qwen3.6 额外带 preserve_thinking",
      out.get("chat_template_kwargs", {}).get("preserve_thinking") is True)
check("max_tokens 加上了 thinking_budget (8192+4096=12288, 防 tool_call 截断)",
      out.get("max_tokens") == 12288, f"got={out.get('max_tokens')}")
check("开思考时移除 temperature (vLLM 要求)", "temperature" not in out)

print("\n=== 2. 同配置但关思考 ===")
out = _inject({**QWEN, "enable_thinking": False})
check("仍显式发 enable_thinking=False (不能靠模板默认, 默认是开的)",
      out.get("chat_template_kwargs") == {"enable_thinking": False},
      f"got={out.get('chat_template_kwargs')}")
check("关思考时 max_tokens 不加 budget", out.get("max_tokens") == 8192,
      f"got={out.get('max_tokens')}")


# ── 2. 不能误伤其他后端 ──────────────────────────────────────────────
print("\n=== 3. 其他服务栈不受影响 ===")
out = _inject({"id": "deepseek-v4-pro", "deployment": "gateway", "backend_type": "openai",
               "enable_thinking": True, "thinking_budget": 4096, "max_tokens": 8192})
check("deepseek-v4 走 openai 分支, 发 reasoning_effort",
      out.get("reasoning_effort") == "high", f"got={out.get('reasoning_effort')}")
check("deepseek-v4 不发 chat_template_kwargs", "chat_template_kwargs" not in out)

out = _inject({"id": "glm-5", "deployment": "gateway", "backend_type": "openai",
               "enable_thinking": True, "thinking_budget": 4096, "max_tokens": 8192})
check("GLM-5 (不支持 thinking) 什么都不加",
      "chat_template_kwargs" not in out and "reasoning_effort" not in out
      and "thinking" not in out, f"got keys={sorted(out)}")

out = _inject({"id": "qwen3-local", "deployment": "ollama", "backend_type": "openai",
               "enable_thinking": True, "thinking_budget": 4096, "max_tokens": 8192})
check("ollama 部署发 think=true", out.get("think") is True, f"got={out.get('think')}")

print("\n=== 3b. deployment 是拓扑标签而非服务栈时必须退回 backend_type ===")
# 生产 config 里 deployment 只有 3 种取值: vllm(服务栈) / gateway / official(接入拓扑).
# 初版 stack() 无条件优先 deployment, 导致 gateway/official 匹配不上任何分支 →
# deepseek 的 reasoning_effort 静默消失。这条钉死该回归。
for dep in ("gateway", "official", "", "某个没见过的值"):
    o = _inject({"id": "deepseek-v4-pro", "deployment": dep, "backend_type": "openai",
                 "enable_thinking": True, "thinking_budget": 4096, "max_tokens": 8192})
    check(f"deployment={dep!r} → 退回 backend_type=openai, reasoning_effort 仍发出",
          o.get("reasoning_effort") == "high", f"got={o.get('reasoning_effort')}")

print("\n=== 4. 没配 deployment 时退回 backend_type (老配置不回归) ===")
out = _inject({"id": "old-vllm", "backend_type": "vllm",
               "enable_thinking": True, "thinking_budget": 4096, "max_tokens": 8192})
check("缺 deployment → 用 backend_type=vllm, 行为不变",
      out.get("chat_template_kwargs", {}).get("enable_thinking") is True
      and out.get("max_tokens") == 12288)

print("\n=== 5. 切模型时 deployment 必须跟着换 (不能沿用旧值) ===")
_inject(QWEN)                                     # 先切到 vllm
out = _inject({"id": "deepseek-v4-pro", "deployment": "gateway", "backend_type": "openai",
               "enable_thinking": True, "thinking_budget": 4096, "max_tokens": 8192})
check("从 vllm 切到 gateway 后不再发 chat_template_kwargs",
      "chat_template_kwargs" not in out, f"got keys={sorted(out)}")
check("从 vllm 切走后 max_tokens 不再被错误加大",
      out.get("max_tokens") == 8192, f"got={out.get('max_tokens')}")


# ── 3. CLI 思考流渲染 (真跑子进程, 不是读代码) ──────────────────────
print("\n=== 6. CLI 真实渲染思考流 (子进程跑 litecli 的 SSE 解析) ===")

_CLI_HARNESS = r'''
import sys, json, types
sys.path.insert(0, %r)
sys.argv = ["litecli"]
import litecli

# 造一段真实形状的 SSE: 先思考再正文
frames = [
    {"choices":[{"delta":{"reasoning":"先看看题目"}}]},
    {"choices":[{"delta":{"reasoning":"再算一下"}}]},
    {"choices":[{"delta":{"content":"答案是 12"}}]},
]
lines = [f"data: {json.dumps(f, ensure_ascii=False)}".encode() for f in frames]
lines.append(b"data: [DONE]")

class R:
    status_code = 200
    text = ""
    def iter_lines(self, decode_unicode=False):
        for l in lines:
            yield l

litecli._req = lambda *a, **kw: R()
litecli._new_session = lambda *a, **kw: "sid-test"
try:
    litecli.chat.callback(message="q", sid="sid-test", new=False,
                          no_stream=False, hide_reasoning=%s)
except SystemExit:
    pass
'''

def run_cli(hide: bool) -> str:
    src = _CLI_HARNESS % (str(BASE), "True" if hide else "False")
    r = subprocess.run([sys.executable, "-c", src], capture_output=True,
                       text=True, timeout=90, cwd=str(BASE))
    return r.stdout + r.stderr

out_show = run_cli(hide=False)
check("默认显示思考流 (含 💭 抬头)", "💭" in out_show, f"got={out_show!r}")
check("思考文本出现", "先看看题目" in out_show and "再算一下" in out_show,
      f"got={out_show!r}")
check("正文出现", "答案是 12" in out_show, f"got={out_show!r}")
check("思考与正文之间有断行 (不糊成一行)",
      re.search(r"再算一下.*\n.*答案是 12", out_show, re.S) is not None,
      f"got={out_show!r}")

out_hide = run_cli(hide=True)
check("--hide-reasoning 时不显示思考", "先看看题目" not in out_hide, f"got={out_hide!r}")
check("--hide-reasoning 时正文仍显示", "答案是 12" in out_hide, f"got={out_hide!r}")


# ── 结果 ─────────────────────────────────────────────────────────────
total = 28
print(f"\n{'='*54}")
if _fails:
    print(f"FAIL {len(_fails)}/{total}: " + "; ".join(_fails))
    sys.exit(1)
print(f"PASS {total}/{total}")

# ── 4. served_model_name: 显示 id 与上游线路名解耦 ──────────────────
# config 的 `id` 原来同时当显示名和上游 model 字段用, 导致"自建 + 中转"两条同名线路
# 无法共存 (自建 vLLM 的 --served-model-name 固定, 换个名字直接 404)。
print("\n=== 7. served_model_name 解耦 ===")
import importlib
CFGM = importlib.import_module("lib.config")

_orig_live = dict(CFGM._LIVE)
_orig_cfg_models = CFGM.CFG.get("models")
try:
    CFGM.CFG["models"] = [
        {"id": "deepseek-v4-flash",       "backend_url": "https://relay/v1"},
        {"id": "deepseek-v4-flash-local", "served_model_name": "deepseek-v4-flash"},
        {"id": "qwen-vision-fb",          "served_model_name": "Qwen3.6-VL"},
        {"id": "plain-model"},
    ]
    CFGM._LIVE.update({"model_id": "deepseek-v4-flash-local",
                       "served_model_name": "deepseek-v4-flash"})

    check("激活模型: 显示 id → 线路名",
          CFGM.wire_model_name("deepseek-v4-flash-local") == "deepseek-v4-flash",
          f"got={CFGM.wire_model_name('deepseek-v4-flash-local')}")
    check("非激活模型 (vision-fallback 路由后) 也能翻译",
          CFGM.wire_model_name("qwen-vision-fb") == "Qwen3.6-VL",
          f"got={CFGM.wire_model_name('qwen-vision-fb')}")
    check("没配 served_model_name → 原样返回 (老配置零变化)",
          CFGM.wire_model_name("plain-model") == "plain-model")
    check("中转那条 (同名但没配 served) 不受影响",
          CFGM.wire_model_name("deepseek-v4-flash") == "deepseek-v4-flash")
    check("未知 id 原样返回, 不抛异常",
          CFGM.wire_model_name("从没见过的") == "从没见过的")
    check("空 id 不炸", CFGM.wire_model_name("") == "")
finally:
    CFGM._LIVE.clear(); CFGM._LIVE.update(_orig_live)
    if _orig_cfg_models is not None: CFGM.CFG["models"] = _orig_cfg_models
