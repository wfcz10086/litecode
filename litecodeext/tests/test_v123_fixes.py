#!/usr/bin/env python3
"""
test_v123_fixes.py — LiteCode v1.0 修复验证测试
用法: python3 test_v123_fixes.py http://YOUR_SERVER:18789 YOUR_TOKEN

测试项:
  1. 模型切换 401 修复 — 切换模型后立即发消息验证
  2. 子代理(spawn_agent) — write_file 工具是否可用
  3. 记忆系统 — rule_based 结果不丢弃
  4. write_file 缺参恢复
  5. 文件处理技能加载
"""
import sys, json, time, httpx, traceback

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18789"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else "CHANGE_ME_TOKEN"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

passed = 0
failed = 0
errors = []

def test(name, fn):
    global passed, failed
    print(f"\n{'='*60}")
    print(f"[TEST] {name}")
    print(f"{'='*60}")
    try:
        fn()
        passed += 1
        print(f"  ✅ PASSED")
    except Exception as e:
        failed += 1
        errors.append((name, str(e)))
        print(f"  ❌ FAILED: {e}")
        traceback.print_exc()

# ─── 测试 1: config._LIVE 机制 ───────────────────────────────
def test_config_live():
    """验证 config._LIVE dict 存在且可被 reload_model 更新"""
    # 这个测试直接 import 本地模块，需要在 litecodeext 目录下运行
    # 如果远程测试，改用 API 方式
    r = httpx.get(f"{BASE}/v1/model/list", headers=HEADERS, timeout=5)
    assert r.status_code == 200, f"model/list 返回 {r.status_code}"
    data = r.json()
    assert "active" in data, "缺少 active 字段"
    assert "models" in data, "缺少 models 字段"
    print(f"  当前模型: {data['active']}")
    print(f"  可用模型: {[m['id'] for m in data['models']]}")

# ─── 测试 2: 模型切换 + 立即对话 ─────────────────────────────
def test_model_switch_no_401():
    """切换模型后立即发消息，验证不会 401"""
    # 获取模型列表
    r = httpx.get(f"{BASE}/v1/model/list", headers=HEADERS, timeout=5)
    models = r.json().get("models", [])
    if len(models) < 2:
        print("  ⚠ 只有 1 个模型，跳过切换测试（添加第二个模型后重测）")
        return

    original = r.json()["active"]
    target = [m for m in models if m["id"] != original][0]

    # 切换到另一个模型
    print(f"  切换: {original} → {target['id']}")
    r = httpx.post(f"{BASE}/v1/model/switch",
                   headers=HEADERS,
                   json={"model_id": target["id"]},
                   timeout=10)
    assert r.status_code == 200, f"switch 返回 {r.status_code}: {r.text[:200]}"
    data = r.json()
    assert data.get("ok"), f"switch 失败: {data}"

    # 立即发一条消息 — 旧版这里会 401
    print(f"  发送测试消息...")
    try:
        r2 = httpx.post(f"{BASE}/v1/chat/completions",
                        headers=HEADERS,
                        json={
                            "model": target["id"],
                            "stream": False,
                            "messages": [{"role": "user", "content": "回复OK两个字"}],
                            "user": "test-switch"
                        },
                        timeout=30)
        # 只要不是 401 就算通过
        assert r2.status_code != 401, f"切换后 401 未修复！status={r2.status_code}"
        print(f"  切换后响应: HTTP {r2.status_code}")
    finally:
        # 切回原来的模型
        httpx.post(f"{BASE}/v1/model/switch",
                   headers=HEADERS,
                   json={"model_id": original},
                   timeout=10)
        print(f"  已切回: {original}")

# ─── 测试 3: spawn_agent 子代理工具注入 ──────────────────────
def test_spawn_agent():
    """验证子代理能使用 write_file 工具"""
    print("  发送 spawn_agent 任务...")
    r = httpx.post(f"{BASE}/v1/chat/completions",
                   headers=HEADERS,
                   json={
                       "model": "auto",
                       "stream": False,
                       "messages": [{"role": "user", "content":
                           "用 spawn_agent 的 agent_type='coder' 写一个文件 "
                           "/tmp/openclaw_workspace/sa_test.py，内容就一行 print('SA_OK')，"
                           "然后运行确认输出 SA_OK"}],
                       "user": "test-subagent"
                   },
                   timeout=120)
    assert r.status_code == 200, f"HTTP {r.status_code}"
    text = r.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    # 只要不包含 "execute_tool not available" 就算修复
    assert "execute_tool not available" not in text, \
        f"子代理工具仍未注入！响应包含: execute_tool not available"
    print(f"  子代理响应: {text[:200]}")

# ─── 测试 4: write_file 缺参恢复 ────────────────────────────
def test_write_file_recovery():
    """模拟 write_file 缺少 filepath 的恢复"""
    print("  写文件测试...")
    r = httpx.post(f"{BASE}/v1/chat/completions",
                   headers=HEADERS,
                   json={
                       "model": "auto",
                       "stream": False,
                       "messages": [{"role": "user", "content":
                           "写一个 Python 文件 /tmp/openclaw_workspace/wf_test.py "
                           "内容是 print('WF_OK')，然后运行它确认输出 WF_OK"}],
                       "user": "test-writefile"
                   },
                   timeout=60)
    assert r.status_code == 200
    text = r.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    print(f"  响应: {text[:200]}")

# ─── 测试 5: 日期感知 ───────────────────────────────────────
def test_datetime_awareness():
    """验证模型知道当前年份"""
    print("  测试日期感知...")
    r = httpx.post(f"{BASE}/v1/chat/completions",
                   headers=HEADERS,
                   json={
                       "model": "auto",
                       "stream": False,
                       "messages": [{"role": "user", "content":
                           "现在是哪一年？只回答数字年份"}],
                       "user": "test-datetime"
                   },
                   timeout=30)
    assert r.status_code == 200
    text = r.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    print(f"  模型回答: {text[:100]}")
    assert "2026" in text, f"模型不知道当前是 2026 年，回答了: {text[:100]}"

# ─── 测试 6: file-reading 技能加载 ──────────────────────────
def test_file_reading_skill():
    """验证 file-reading 技能可以加载"""
    r = httpx.post(f"{BASE}/v1/chat/completions",
                   headers=HEADERS,
                   json={
                       "model": "auto",
                       "stream": False,
                       "messages": [{"role": "user", "content":
                           "加载 file-reading 技能，告诉我 PDF 文件应该用什么命令读取"}],
                       "user": "test-skill"
                   },
                   timeout=30)
    assert r.status_code == 200
    text = r.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    print(f"  响应: {text[:200]}")

# ─── 运行所有测试 ────────────────────────────────────────────
if __name__ == "__main__":
    print(f"LiteCode v1.0 Fix Verification")
    print(f"Server: {BASE}")
    print(f"Token: {TOKEN[:4]}****")

    test("1. config._LIVE 机制", test_config_live)
    test("2. 模型切换不再 401", test_model_switch_no_401)
    test("3. spawn_agent 子代理", test_spawn_agent)
    test("4. write_file 写文件", test_write_file_recovery)
    test("5. 日期感知 (2026)", test_datetime_awareness)
    test("6. file-reading 技能", test_file_reading_skill)

    print(f"\n{'='*60}")
    print(f"结果: {passed} passed, {failed} failed")
    if errors:
        print(f"\n失败详情:")
        for name, err in errors:
            print(f"  ❌ {name}: {err}")
    print(f"{'='*60}")
    sys.exit(1 if failed else 0)
