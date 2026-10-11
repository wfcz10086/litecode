"""core/memory/recall + turn_sources.memory_recall: 相关度挑条目 / 常驻段 / 已可见跳过 / 字数封顶。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "litecodeext"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "litecodeext" / "core"))

from core.memory import recall as R  # noqa: E402

MD = """# Session Memory — L1
## Vault (Never Compress)
<!-- 永久保留 -->
- 部署端口 = 6100
## User Intent
- 画一只鹈鹕
## Key References
- 数据库: postgres://db.internal:5432/app
- 前端用 vite 构建, 入口 src/main.ts
- 鹈鹕页面在 /tmp/pelican/index.html
## Errors & Corrections
- npm install 超时, 改用国内镜像 registry.npmmirror.com
## Question Log
- Q1: 部署端口是多少
"""


def _entries():
    return R.parse_entries(MD)


def test_parse_skips_comments_intent_and_log():
    texts = [e.text for e in _entries()]
    assert "部署端口 = 6100" in texts and len(texts) == 5
    assert not any("画一只鹈鹕" in t or "Q1" in t or "永久保留" in t for t in texts)


def test_tokens_cjk_bigrams_and_words():
    assert R.tokens("鹈鹕页面 vite") == ["vite", "鹈鹕", "鹕页", "页面"]


def test_relevant_entries_ranked_first():
    picked = R.select("npm 安装依赖老是超时怎么办", _entries(), k=1, pinned=())
    assert [e.text for e in picked] == ["npm install 超时, 改用国内镜像 registry.npmmirror.com"]


def test_irrelevant_query_gets_only_pinned():
    picked = R.select("今天天气", _entries(), k=3, pinned=("Vault (Never Compress)",))
    assert [e.section for e in picked] == ["Vault (Never Compress)"]


def test_entries_visible_in_history_skipped():
    picked = R.select("鹈鹕页面", _entries(), k=3, pinned=(), seen="文件在 /tmp/pelican/index.html")
    assert all("pelican" not in e.text for e in picked)


def test_char_budget():
    picked = R.select("数据库 vite 鹈鹕 npm", _entries(), k=5, pinned=(), max_chars=60)
    assert sum(len(e.text) for e in picked) <= 60 and picked


def test_render():
    out = R.render([R.Entry("Vault", "部署端口 = 6100")])
    assert out.startswith("## 相关记忆") and "- [Vault] 部署端口 = 6100" in out
    assert R.render([]) == ""


def test_load_entries_includes_l2(tmp_path):
    (tmp_path / "MEMORY.md").write_text(MD)
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "deploy.md").write_text("# deploy\n## 端口\n6100\n_曾为_: 5173\n")
    es = R.load_entries(tmp_path)
    assert R.Entry("端口", "6100", "L2:deploy") in es
    assert not any(e.text.startswith("_曾为_") for e in es)


def test_turn_source_memory_recall(tmp_path, monkeypatch):
    import lib.turn_context as tc
    import lib.turn_sources as ts
    sd = tmp_path / "sessions" / "s1"
    sd.mkdir(parents=True)
    (sd / "MEMORY.md").write_text(MD)
    monkeypatch.setattr(ts, "WORKSPACE", tmp_path)
    monkeypatch.setattr(ts, "CFG", {"agent": {"memory_pinned_sections": ["Vault (Never Compress)"]}})
    out = ts.memory_recall(tc.TurnInput("npm 超时了", "s1", []))
    assert "部署端口 = 6100" in out and "npmmirror" in out
    # 上一轮已注入过的不再重复
    hist = [{"role": "user", "content": "q", tc.KEY: out}]
    again = ts.memory_recall(tc.TurnInput("npm 超时了", "s1", hist))
    assert "部署端口" not in again and "npmmirror" not in again
    monkeypatch.setattr(ts, "CFG", {"agent": {"memory_recall": False}})
    assert ts.memory_recall(tc.TurnInput("npm 超时了", "s1", [])) == ""
