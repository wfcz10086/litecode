"""
test_memory_longrange.py — 长程 / 跨 session 记忆召回真测
=========================================================
两条测试:
  M-2: 同 session 100 轮后, 第 1 轮塞 ssh 配置, 第 100 轮还能召回 (L1 不被 sliding window 裁)
  M-3: sessionA 写偏好 → 关掉 → sessionB 新开自动召回 (L3 FTS5 跨 session)

不依赖 LLM (不调远程 backend), 只验证:
  - rule_capture_user_facts 真把 ssh/host/偏好 写到 L1 + L3
  - L1 永远存在 (MEMORY.md), 任意轮数后还在
  - L3 SQLite FTS5 跨 session 能查到老偏好
"""
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "core"))


def _make_mgr(ws: Path, sid: str):
    from memory import MemoryManager
    return MemoryManager(
        workspace=ws, session_id=sid,
        vllm_url="http://localhost:99", model_id="test-stub",
    )


def test_m2_same_session_100_round_recall():
    """M-2: 第 1 轮塞 ssh, 100 轮无关消息, 最终 L1 仍含原始 ssh 命令."""
    from memory import rule_capture_user_facts
    ws = Path(tempfile.mkdtemp(prefix="lc_m2_"))
    mgr = _make_mgr(ws, "long-session-1")

    # Round 1: 塞关键 ssh 配置
    msgs = [
        {"role": "user", "content": "我的服务器是 ssh long@10.0.0.5 -p 22 -i ~/.ssh/work"},
        {"role": "assistant", "content": "OK 记下"},
    ]
    rule_capture_user_facts(mgr, msgs)

    # Round 2 ~ 99: 灌一堆无关短消息 (模拟用户聊别的)
    for i in range(2, 100):
        msgs.append({"role": "user", "content": f"轮 {i} 的随机问题: 今天天气如何?"})
        msgs.append({"role": "assistant", "content": f"轮 {i} 的回答, 跟 ssh 无关"})
        rule_capture_user_facts(mgr, msgs)  # 每轮都跑一次, 模拟真实 server 调用

    # Round 100: 用户终于要用 ssh
    msgs.append({"role": "user", "content": "帮我 ssh 上去看下 nginx 日志"})

    # 断言: L1 MEMORY.md 永远在 (sliding window 不裁 system prompt 部分)
    l1_text = mgr.l1_file.read_text()
    assert "ssh long@10.0.0.5" in l1_text, f"L1 丢了 ssh 配置! 全文:\n{l1_text}"
    assert "-p 22" in l1_text, "L1 丢了 port"
    assert "~/.ssh/work" in l1_text, "L1 丢了 key path"
    print(f"✓ M-2 通过: 100 轮后 L1 仍含完整 ssh 配置 (l1 size={len(l1_text)} chars)")
    return True


def test_m3_cross_session_recall():
    """M-3: sessionA 写偏好 → sessionB 新开 → L3 FTS5 能召回."""
    from memory import rule_capture_user_facts
    # 同一个 workspace, 不同 session_id (跨 session 共享 .memory_index.db)
    ws = Path(tempfile.mkdtemp(prefix="lc_m3_"))

    # ── Session A: 用户写下偏好 ──
    mgr_a = _make_mgr(ws, "session-A-yesterday")
    msgs_a = [
        {"role": "user", "content": "记住, 看加密合约一律走 tradingview, 不要 binance.com"},
        {"role": "user", "content": "我的 ssh 是 ssh ada@gpu-a40.local -i ~/.ssh/gpu"},
        {"role": "user", "content": "我希望部署到 production 前必须先跑 e2e"},
    ]
    n_a = rule_capture_user_facts(mgr_a, msgs_a)
    assert n_a >= 3, f"sessionA 应该捕获 >= 3 条事实, 实际 {n_a}"

    # ── 模拟 session A 结束 (关掉, 不留任何 in-memory 状态) ──
    del mgr_a

    # ── Session B: 全新开 ──
    mgr_b = _make_mgr(ws, "session-B-today")
    # sessionB 的 L1 应该是空模板, 不含 sessionA 的事实 (L1 是 session 级)
    l1_b = mgr_b.l1_file.read_text()
    assert "tradingview" not in l1_b, "L1 是 session 级, 不应该跨 session 共享"

    # ── L3 跨 session 检索: 用 sessionB 的视角查 ──
    try:
        from memory_index import MemoryIndex
    except ImportError:
        from core.memory_index import MemoryIndex

    idx = MemoryIndex(ws / ".memory_index.db")

    # 测 1: 用 "tradingview" 关键词召回
    r1 = idx.search("tradingview", limit=3)
    assert len(r1) >= 1, f"L3 应该召回 tradingview, 实际返回 {r1}"
    assert any("tradingview" in (x.get("content") or "") for x in r1)
    print(f"✓ L3 召回 tradingview: {r1[0].get('content','')[:80]}")

    # 测 2: 用 "ssh" 召回
    r2 = idx.search("ssh", limit=3)
    assert len(r2) >= 1, "L3 应该召回 ssh 配置"
    assert any("ada@gpu-a40" in (x.get("content") or "") for x in r2), \
        f"应该召回 sessionA 写的 ssh, 实际:\n{r2}"
    print(f"✓ L3 召回 ssh: {r2[0].get('content','')[:80]}")

    # 测 3: 用 search_as_context (这是真实 server 启动新会话时调用的入口)
    ctx = idx.search_as_context("我要 ssh 上 gpu 跑 e2e", limit=3, max_chars=1500)
    assert ctx, f"search_as_context 应返回非空, 实际: {ctx!r}"
    assert ("ada@gpu-a40" in ctx) or ("e2e" in ctx) or ("ssh" in ctx), \
        f"context 应含 sessionA 的事实, 实际:\n{ctx}"
    print(f"✓ search_as_context 返回 {len(ctx)} chars")

    print("✓ M-3 通过: sessionA 写, sessionB 召回 L3 FTS5")
    return True


def main():
    failures = []
    for name, fn in [
        ("M-2 同 session 100 轮远召回", test_m2_same_session_100_round_recall),
        ("M-3 跨 session L3 召回",      test_m3_cross_session_recall),
    ]:
        try:
            fn()
        except Exception as e:
            print(f"✗ {name} FAIL: {e}")
            import traceback; traceback.print_exc()
            failures.append(name)
    if failures:
        print(f"\n失败: {failures}")
        sys.exit(1)
    print("\n全部通过 ✓")


if __name__ == "__main__":
    main()
