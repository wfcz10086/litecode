"""会话记忆 Question Log 写入含 Windows 路径的内容不报错 (跑分发现: re.sub 把用户内容当替换模板 → bad escape \\U)。"""
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / d) for d in ("", "core", "lib")]


def test_question_log_with_windows_path(tmp_path):
    from core.memory.manager import MemoryManager
    m = MemoryManager(workspace=tmp_path, session_id="web-bs", vllm_url="http://127.0.0.1:1/v1", model_id="x")
    for msg in (r"打开 C:\Users\Public\a.txt", r"再看 C:\Users\litecode\new\b.txt 和 \d+ 正则"):
        m.save("Question Log", msg)
    text = m.l1_file.read_text()
    assert r"C:\Users\litecode\new\b.txt" in text and r"\d+" in text
