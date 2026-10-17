"""模型热切换后, 拆到 lib/agent/ 的主循环模块要读当前模型的 CONTEXT_WINDOW 等 (不是启动时的快照)。

2026-10-16 拆分梳理: 这些模块原先 `from lib.config import CONTEXT_WINDOW` 导入时拷快照,
切到窗口不同的模型后预算仍按旧窗口算; 现改为读 lib.config 模块属性 (reload_model 更新它)。
"""
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]
from lib import config
from lib.agent import loop_budget


def _ctx(n_tokens_each, count=20):
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    msgs += [{"role": "tool", "content": "x" * (n_tokens_each * config.CHARS_PER_TOKEN)} for _ in range(count)]
    return SimpleNamespace(messages=msgs)


def _trimmed(ctx):
    return any("[budget-trimmed]" in str(m.get("content")) for m in ctx.messages)


def test_budget_follows_reload_model():
    saved = dict(config._LIVE)
    target = {"id": saved.get("model_id", "m"), "backend_url": saved.get("backend_url", "http://x"),
              "api_key": saved.get("api_key", ""), "context_window": 8000}
    try:
        config.reload_model(dict(target))                     # 小窗口: 20*1000 tok 远超 8000*0.75
        small = _ctx(1000)
        loop_budget._loop_context_budget(small, fixed_overhead=0)
        config.reload_model(dict(target, context_window=200000))   # 切到大窗口: 不该再裁
        big = _ctx(1000)
        loop_budget._loop_context_budget(big, fixed_overhead=0)
    finally:
        config.reload_model({"id": saved.get("model_id"), "backend_url": saved.get("backend_url", ""),
                             "api_key": saved.get("api_key", ""), "context_window": saved.get("context_window"),
                             "max_tokens": saved.get("max_tokens"), "enable_thinking": saved.get("enable_thinking"),
                             "thinking_budget": saved.get("thinking_budget"), "backend_type": saved.get("backend_type"),
                             "deployment": saved.get("deployment", ""), "served_model_name": saved.get("served_model_name", ""),
                             "supports_vision": saved.get("supports_vision", False)})
    assert _trimmed(small)
    assert not _trimmed(big)


def test_no_snapshot_imports_left_in_agent_modules():
    """lib/agent 下不得"从 config 导入模型相关名字后直接读" (导入即快照)。"""
    import ast
    live = {'CONTEXT_WINDOW', 'MODEL_ID', 'BACKEND_URL', 'API_KEY', 'MAX_TOKENS',
            'ENABLE_THINKING', 'THINKING_BUDGET', 'BACKEND_TYPE'}
    for f in (ROOT / 'lib' / 'agent').glob('*.py'):
        tree = ast.parse(f.read_text())
        snap = {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                and (n.module or '').endswith('config') for a in n.names} & live
        read = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        assert not (snap & read), f'{f.name} 用导入快照读 {sorted(snap & read)}'
