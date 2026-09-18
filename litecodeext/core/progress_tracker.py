"""progress_tracker.py — 框架级章节/编号文件进度自动维护

设计目标:
- write_file / patch_file 成功后自动调用 auto_update_state(filepath)
- 检测文件名是否为"按编号"模式 (ch01.md / chapter_03.md / 第十章.md / 01_intro.md)
- 在文件所在目录或父目录寻找 state.json / story_state.json / progress.json
- 把 current_chapter 字段更新为 max(current, 本文件编号)
- LLM 不再需要自己维护 state.json, 解决"反复重写已写章节"死循环

不抛异常, 全 try/except — 进度追踪失败不能影响 write_file 本身.
"""
import json
import re
import time
from pathlib import Path
from typing import Optional


# 章节编号识别正则 (按优先级)
_RX_CH_EN = re.compile(r"^(?:ch|chap|chapter|c)[_\-]?(\d+)\b", re.IGNORECASE)
_RX_NUM_PREFIX = re.compile(r"^(\d{1,4})[_\-]")
_RX_NUM_ONLY = re.compile(r"^(\d{1,4})$")
_RX_CN_CHAPTER = re.compile(r"第([\d一二三四五六七八九十百千]+)章")

_CN_NUM = {"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10}

_STATE_FILE_CANDIDATES = ("story_state.json", "state.json", "progress.json", "novel_state.json")


def _parse_cn_number(s: str) -> Optional[int]:
    """十/二十/三十五 -> int. 简易实现, 只支持 1-99."""
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    if len(s) == 1:
        return _CN_NUM.get(s)
    # 两位组合: 十X, X十, X十Y
    if s.startswith("十") and len(s) == 2:
        return 10 + _CN_NUM.get(s[1], 0)
    if s.endswith("十") and len(s) == 2:
        d = _CN_NUM.get(s[0])
        return d * 10 if d else None
    if len(s) == 3 and s[1] == "十":
        a, b = _CN_NUM.get(s[0]), _CN_NUM.get(s[2])
        if a and b:
            return a * 10 + b
    return None


def detect_chapter_num(filepath: str) -> Optional[int]:
    """从文件名提取章节编号. 失败返回 None."""
    name = Path(filepath).stem
    if not name:
        return None
    # 1. ch01 / chapter_3 / chap-12
    m = _RX_CH_EN.match(name)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    # 2. 第三章 / 第10章
    m = _RX_CN_CHAPTER.search(name)
    if m:
        n = _parse_cn_number(m.group(1))
        if n is not None:
            return n
    # 3. 01_intro / 003-foo
    m = _RX_NUM_PREFIX.match(name)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    # 4. 纯数字 01.md / 003.txt
    m = _RX_NUM_ONLY.match(name)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return None


def _find_state_file(start: Path) -> Optional[Path]:
    """从 start 目录开始向上找 state.json, 最多 3 级父目录."""
    cur = start if start.is_dir() else start.parent
    for _ in range(4):
        for name in _STATE_FILE_CANDIDATES:
            p = cur / name
            if p.is_file():
                return p
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def auto_update_state(filepath: str, op: str = "write") -> Optional[dict]:
    """成功 write_file / patch_file 后调用. 不抛异常.
    返回 {state_path, chapter, prev, new} 给上层 log; 没动作返回 None.
    """
    try:
        ch = detect_chapter_num(filepath)
        if ch is None:
            return None
        fp = Path(filepath)
        sp = _find_state_file(fp)
        if sp is None:
            return None
        try:
            state = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(state, dict):
            return None
        prev = state.get("current_chapter", 0)
        try:
            prev_int = int(prev) if not isinstance(prev, int) else prev
        except Exception:
            prev_int = 0
        if ch <= prev_int:
            return {"state_path": str(sp), "chapter": ch, "prev": prev_int,
                    "new": prev_int, "action": "skip-already-recorded"}
        state["current_chapter"] = ch
        state["last_chapter_file"] = str(fp.name)
        state["last_updated_by"] = "framework_hook"
        state["last_updated_at"] = time.time()
        state["last_op"] = op
        sp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"state_path": str(sp), "chapter": ch, "prev": prev_int,
                "new": ch, "action": "updated"}
    except Exception:
        return None


def ensure_state_file(dir_path: str, init: Optional[dict] = None) -> Path:
    """主代理可调用: 在指定目录创建 story_state.json 初始结构. 已存在不动."""
    d = Path(dir_path)
    d.mkdir(parents=True, exist_ok=True)
    sp = d / "story_state.json"
    if not sp.exists():
        sp.write_text(json.dumps(init or {
            "current_chapter": 0,
            "target_chapters": 0,
            "characters": {},
            "plot_events": [],
            "world_rules": {},
            "last_updated_by": "framework_hook",
            "last_updated_at": time.time(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    return sp
