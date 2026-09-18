#!/usr/bin/env python3
# [修复轮 #1 问题 B] web_ui.py 白名单原来逐个硬编码 "[SYSTEM-XXX]" 前缀元组, startswith
# 匹配不上新加的 "[SYSTEM-PLAN-GATE]"/既有的 "[SYSTEM-READONLY]" -> 自注入伪 user 消息
# 泄漏到网页 UI。修复成正则覆盖整个 "[SYSTEM...]" 家族 (_is_sys_inject_text), 本测试
# 覆盖: 新旧前缀都被过滤 + 正文中间出现 "[SYSTEM" 不受影响。
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "litecodeext"))
os.environ.setdefault("LITECODE_TEST_OFFLINE", "1")


def _is_sys_inject_text(text):
    # 懒加载: web_ui.py import 时会在模块顶层构建真实 FastAPI app + 注册全部 routers
    # (含 routers.artifacts_v2_router), 其 Depends(require_auth) 在那一刻就绑死成真实鉴权。
    # 若在 pytest collection 阶段(而非测试真正执行时)提前 import, 会抢在
    # test_artifacts_router_smoke.py 自己的 monkeypatch 之前把 auth 依赖钉死, 导致它的
    # 用例全部 401 (同一类问题 test_route_auth.py 也用懒加载方式绕开过)。
    import web_ui
    return web_ui._is_sys_inject_text(text)


def test_plan_gate_prefix_filtered():
    assert _is_sys_inject_text("[SYSTEM-PLAN-GATE] 这是一个复杂任务...") is True
    assert _is_sys_inject_text("[SYSTEM-PLAN-GATE ×2] 你已经调用了其他工具...") is True
    print("[OK] [SYSTEM-PLAN-GATE] / [SYSTEM-PLAN-GATE ×2] 被过滤")


def test_readonly_prefix_filtered():
    # 既有漏网前缀 (commit 937b176 引入), 本次同一改动天然覆盖
    assert _is_sys_inject_text("[SYSTEM-READONLY] 你已经连续几轮只在查看...") is True
    print("[OK] [SYSTEM-READONLY] 被过滤")


def test_legacy_prefixes_still_filtered():
    assert _is_sys_inject_text("[SYSTEM-TEST] xxx") is True
    assert _is_sys_inject_text("[SYSTEM-FORCE-ACTION] xxx") is True
    assert _is_sys_inject_text("[SYSTEM-EMERGENCY] xxx") is True
    assert _is_sys_inject_text("[SYSTEM] xxx") is True
    print("[OK] 老前缀 (TEST/FORCE-ACTION/EMERGENCY/裸 SYSTEM) 依然被过滤")


def test_unknown_future_prefix_also_filtered():
    # 根治点: 以后随便加一个新前缀, 不用回来改白名单
    assert _is_sys_inject_text("[SYSTEM-WHATEVER-NEW-FEATURE] xxx") is True
    print("[OK] 未来任意新 [SYSTEM-xxx] 前缀无需改代码即被覆盖")


def test_leading_whitespace_still_matched():
    assert _is_sys_inject_text("   [SYSTEM-READONLY] xxx") is True
    print("[OK] 前导空白不影响 lstrip 后的前缀匹配")


def test_normal_user_message_not_filtered():
    assert _is_sys_inject_text("帮我看看这段代码") is False
    assert _is_sys_inject_text("今天天气怎么样") is False
    print("[OK] 正常用户消息不被误过滤")


def test_system_substring_in_middle_not_filtered():
    # 正文中间出现 "[SYSTEM" (非开头) 不该被过滤, 维持既有 lstrip().startswith 语义
    assert _is_sys_inject_text("我看日志里有一行写着 [SYSTEM] 出错了, 这是什么意思") is False
    print("[OK] 正文中间(非开头)出现 [SYSTEM 不误伤")


def test_non_string_and_empty_safe():
    assert _is_sys_inject_text(None) is False
    assert _is_sys_inject_text(123) is False
    assert _is_sys_inject_text("") is False
    print("[OK] 非字符串/空值安全 (不抛异常, 一律不命中)")


def test_systematic_word_not_falsely_matched():
    # "[SYSTEMATIC]" 之类紧跟字母的词不应误命中 (正则要求 SYSTEM 后紧跟 '-' 或 ']')
    assert _is_sys_inject_text("[SYSTEMATIC] review needed") is False
    print("[OK] [SYSTEMATIC] 这类非 SYSTEM 家族前缀不误伤")


if __name__ == "__main__":
    test_plan_gate_prefix_filtered()
    test_readonly_prefix_filtered()
    test_legacy_prefixes_still_filtered()
    test_unknown_future_prefix_also_filtered()
    test_leading_whitespace_still_matched()
    test_normal_user_message_not_filtered()
    test_system_substring_in_middle_not_filtered()
    test_non_string_and_empty_safe()
    test_systematic_word_not_falsely_matched()
    print("\n[PASS] web_ui sys-inject-filter suite")
