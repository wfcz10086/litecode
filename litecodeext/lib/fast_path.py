"""
fast_path.py — 短任务直答判定 (P22)
=====================================
判断用户输入是否属于"应直答的短问题"，跳过工具循环减少延迟和 token.

判定规则 (任意一条命中则 fast-path):
  1. 长度 < 30 字符 且 不含工具关键词 (写/创建/搜索/分析/...)
  2. 是问候/感谢/确认 (你好/谢谢/好的/明白/ok/嗯/...)
  3. 全英文 ≤ 8 单词 且 不含 ./tool 关键词
  4. 单行问号问题 (e.g. "什么是 X?")

输出: bool (True = fast-path)
"""
from typing import Tuple


_TOOL_KEYWORDS = (
    '写', '创建', '生成', '搜索', '分析', '脚本', '代码', '文件', '运行', '执行',
    '报告', '小说', '图表', '调试', '查找', '修改', '提交', '部署',
    'write', 'create', 'build', 'search', 'analyze', 'run', 'execute',
    'script', 'code', 'file', 'report', 'debug', 'find', 'modify',
    '.py', '.js', '.ts', '.go', '.md', '.csv', '.xlsx', '.pdf', '.sh',
)

_GREETINGS = (
    '你好', '您好', 'hello', 'hi', 'hey',
    '谢谢', '感谢', 'thanks', 'thank',
    '好的', '明白', '收到', '知道了', 'ok', 'okay', 'got it',
    '嗯', '行', '可以', 'sure', 'yes', 'no', '对', '不',
    'bye', '再见',
)


def is_fast_path(message: str) -> Tuple[bool, str]:
    """返回 (是否 fast-path, 原因).
    fast-path = 直答, 不进工具循环.
    """
    if not message:
        return False, "empty"
    msg = message.strip()
    msg_low = msg.lower()
    
    # Rule 2: 问候 / 确认 (优先, 比长度规则更明确)
    for g in _GREETINGS:
        if msg_low == g or msg_low.startswith(g + ' ') or msg_low.startswith(g + ','):
            if len(msg) < 50:
                return True, f"greeting:{g}"
    
    # 工具关键词 → 一定不是 fast-path
    if any(k in msg_low for k in _TOOL_KEYWORDS):
        return False, "tool_keyword"
    
    # Rule 1: 短消息无工具关键词
    if len(msg) < 30:
        return True, "short<30"
    
    # Rule 3: 全英文 ≤ 8 单词
    if msg.isascii() and len(msg) < 80:
        words = msg.split()
        if len(words) <= 8:
            return True, "short_english"
    
    # Rule 4: 单行问号问题 (中文 ?, 英文 ?, 中文 ？)
    if "\n" not in msg and len(msg) < 80 and msg.endswith(('?', '？')):
        return True, "single_question"
    
    return False, "default_full_loop"
