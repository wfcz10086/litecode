"""core/memory/upsert + MemoryManager.save: 同 key 覆盖 / 冲突留曾为 / 单槽段 / L2 同名段替换。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "litecodeext"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "litecodeext" / "core"))

from core.memory.upsert import split_kv, upsert_entry, upsert_topic_section  # noqa: E402
from core.memory.manager import MemoryManager  # noqa: E402

DOC = "# L1\n\n## Key References\n<!-- refs -->\n\n## Vault\n- 部署端口 = 5173\n"


def test_split_kv():
    assert split_kv("- 部署端口 = 5173（曾为: 80）") == ("部署端口", "5173")
    assert split_kv("- URL: http://x/a?b=1") == (None, "URL: http://x/a?b=1")
    assert split_kv("- 命令: `export A=1`") == (None, "命令: `export A=1`")


def test_new_entry_added_under_heading():
    text, act = upsert_entry(DOC, "## Key References", "文件: /a.py")
    assert act == "added" and "## Key References\n- 文件: /a.py\n<!-- refs -->" in text


def test_same_key_new_value_replaces_with_was():
    text, act = upsert_entry(DOC, "## Vault", "部署端口 = 6100")
    assert act == "updated"
    assert "- 部署端口 = 6100（曾为: 5173）" in text and text.count("部署端口") == 1
    text2, act2 = upsert_entry(text, "## Vault", "部署端口 = 7000")
    assert "- 部署端口 = 7000（曾为: 6100）" in text2 and "5173" not in text2   # 只留最近一处曾为


def test_same_value_unchanged():
    assert upsert_entry(DOC, "## Vault", "部署端口 = 5173") == (DOC, "unchanged")


def test_explicit_key():
    text, act = upsert_entry(DOC, "## Vault", "6100", key="部署端口")
    assert act == "updated" and "- 部署端口 = 6100（曾为: 5173）" in text
    t2, _ = upsert_entry(text, "## Vault", "http://a/b", key="api/base")
    t3, act3 = upsert_entry(t2, "## Vault", "http://c/d", key="api/base")
    assert act3 == "updated" and t3.count("api/base") == 1


def test_keyless_dedupe_and_multiline_flattened():
    t, _ = upsert_entry(DOC, "## Key References", "第一行\n第二行")
    assert "- 第一行 第二行" in t
    assert upsert_entry(t, "## Key References", "第一行 第二行")[1] == "unchanged"


def test_single_slot_section_keeps_latest_only():
    doc = "## User Intent\n- 旧意图\n<!-- what -->\n\n## Vault\n"
    t, act = upsert_entry(doc, "## User Intent", "新意图")
    assert act == "updated" and "旧意图" not in t and "<!-- what -->" in t and "## Vault" in t


def test_missing_section_appended():
    t, act = upsert_entry("# L1\n", "## User Preferences", "简短回答")
    assert act == "added" and t.endswith("## User Preferences\n- 简短回答\n")


def test_topic_section_replaced_not_duplicated():
    t, _ = upsert_topic_section("# deploy\n", "端口", "5173")
    t, act = upsert_topic_section(t, "端口", "6100")
    assert act == "updated" and t.count("## 端口") == 1 and "6100\n_曾为_: 5173" in t
    assert upsert_topic_section(t, "端口", "6100")[1] == "unchanged"


def _mgr(tmp_path):
    return MemoryManager(workspace=tmp_path, session_id="s1", vllm_url="http://x", model_id="m")


def test_manager_save_upserts(tmp_path):
    m = _mgr(tmp_path)
    assert m.save("Vault", "部署端口 = 5173") == "added"
    assert m.save("Vault", "部署端口 = 5173") == "unchanged"
    assert m.save("Vault", "部署端口 = 6100") == "updated"
    body = m.l1_file.read_text()
    assert body.count("部署端口") == 1 and "（曾为: 5173）" in body


def test_manager_save_topic(tmp_path):
    m = _mgr(tmp_path)
    m.save("端口", "5173", topic="deploy")
    m.save("端口", "6100", topic="deploy")
    assert (m.l2_dir / "deploy.md").read_text().count("## 端口") == 1


def test_rule_memory_skips_system_injected_intent(tmp_path):
    from core.memory.auto import rule_based_memory_update
    m = _mgr(tmp_path)
    msgs = [{"role": "user", "content": "帮我把鹈鹕页面部署到 48888 端口并验证可以访问"},
            {"role": "assistant", "content": "好"},
            {"role": "user", "content": "[SYSTEM-READONLY] 你已经连续 10 轮只在查看和验证, 没有做出任何实际修改"}]
    rule_based_memory_update(m, msgs)
    body = m.l1_file.read_text()
    assert "SYSTEM-READONLY" not in body.split("## Question Log")[0]
    assert "鹈鹕页面部署" in body


def test_rule_capture_ignores_questions(tmp_path):
    from core.memory.auto import rule_capture_user_facts
    m = _mgr(tmp_path)
    rule_capture_user_facts(m, [{"role": "user", "content": "我的项目代号和地址分别是什么? 一句话回答"},
                                {"role": "user", "content": "我的部署端口是 6100 这个要记牢"}])
    body = m.l1_file.read_text()
    assert "什么" not in body and "6100" in body
