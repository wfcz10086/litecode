"""
blackboard.py — 共享黑板 (Shared Blackboard)
==============================================
多代理编排的核心状态共享机制。

设计理念:
  - 替代 multi_agent.py 中 previous_output 的纯文本截断传递
  - 子 Agent 通过 update_global_context() 写入结构化数据
  - Supervisor 通过 query() 精确检索，零信息衰减
  - 线程安全，支持并行写入

架构位置:
  Supervisor → Blackboard ← SubAgent_1
                           ← SubAgent_2
                           ← SubAgent_N

v1.0 新增模块，不影响 SSE 流式层。
"""

import json
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class EntryType(str, Enum):
    """黑板条目类型"""
    FILE_PATH   = "file_path"      # 代码文件路径
    API_DEF     = "api_def"        # API 接口定义
    DB_SCHEMA   = "db_schema"      # 数据库 schema
    CONFIG      = "config"         # 配置信息
    ERROR       = "error"          # 错误记录
    RESULT      = "result"         # 执行结果
    DEPENDENCY  = "dependency"     # 依赖关系
    ARTIFACT    = "artifact"       # 产出物（报告/文档/图片）
    DECISION    = "decision"       # 架构/技术决策
    CONTEXT     = "context"        # 通用上下文


@dataclass
class BlackboardEntry:
    """单条黑板记录"""
    key: str
    value: Any
    entry_type: EntryType = EntryType.CONTEXT
    source_agent: str = ""           # 写入者标识
    timestamp: float = field(default_factory=time.time)
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "value": self.value,
            "type": self.entry_type.value,
            "source": self.source_agent,
            "ts": self.timestamp,
            "meta": self.metadata,
        }


class Blackboard:
    """
    线程安全的共享黑板。

    使用方式:
        bb = Blackboard()
        # 子 Agent 写入
        bb.put("user_table_sql", "CREATE TABLE ...", EntryType.DB_SCHEMA, source="db-agent")
        # Supervisor / 其他 Agent 读取
        schema = bb.get("user_table_sql")
        all_files = bb.query_by_type(EntryType.FILE_PATH)
        # 导出为 context 字符串注入 prompt
        ctx = bb.to_context_string(max_chars=4000)
    """

    def __init__(self):
        self._store: Dict[str, BlackboardEntry] = {}
        self._history: List[BlackboardEntry] = []  # 全部写入历史（含覆盖）
        self._lock = threading.Lock()

    # ── 写入 ──────────────────────────────────────────────

    def put(self, key: str, value: Any,
            entry_type: EntryType = EntryType.CONTEXT,
            source: str = "", metadata: dict = None) -> None:
        """写入/覆盖一条记录。"""
        entry = BlackboardEntry(
            key=key, value=value, entry_type=entry_type,
            source_agent=source, metadata=metadata or {},
        )
        with self._lock:
            self._store[key] = entry
            self._history.append(entry)

    def put_file(self, filepath: str, description: str = "", source: str = "") -> None:
        """快捷方式：记录文件路径。"""
        self.put(f"file:{filepath}", {"path": filepath, "desc": description},
                 EntryType.FILE_PATH, source=source)

    def put_error(self, error_key: str, error_msg: str,
                  root_cause: str = "", source: str = "") -> None:
        """快捷方式：记录错误。"""
        self.put(f"error:{error_key}", {
            "message": error_msg[:500], "root_cause": root_cause,
        }, EntryType.ERROR, source=source)

    def put_api(self, endpoint: str, method: str = "GET",
                params: dict = None, source: str = "") -> None:
        """快捷方式：记录 API 定义。"""
        self.put(f"api:{method}:{endpoint}", {
            "endpoint": endpoint, "method": method, "params": params or {},
        }, EntryType.API_DEF, source=source)

    # ── 读取 ──────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        """精确获取。"""
        with self._lock:
            entry = self._store.get(key)
        return entry.value if entry else default

    def get_entry(self, key: str) -> Optional[BlackboardEntry]:
        """获取完整条目（含元数据）。"""
        with self._lock:
            return self._store.get(key)

    def query_by_type(self, entry_type: EntryType) -> List[BlackboardEntry]:
        """按类型查询所有条目。"""
        with self._lock:
            return [e for e in self._store.values() if e.entry_type == entry_type]

    def query_by_source(self, source: str) -> List[BlackboardEntry]:
        """按来源 Agent 查询。"""
        with self._lock:
            return [e for e in self._store.values() if e.source_agent == source]

    def keys(self) -> List[str]:
        with self._lock:
            return list(self._store.keys())

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._store

    # ── 删除 ──────────────────────────────────────────────

    def remove(self, key: str) -> bool:
        with self._lock:
            return self._store.pop(key, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    # ── 导出 ──────────────────────────────────────────────

    def to_context_string(self, max_chars: int = 6000,
                          types: List[EntryType] = None) -> str:
        """
        导出为自然语言上下文字符串，用于注入 Agent prompt。
        按类型分组，结构清晰，LLM 可直接理解。
        """
        with self._lock:
            entries = list(self._store.values())

        if types:
            entries = [e for e in entries if e.entry_type in types]

        if not entries:
            return ""

        # 按类型分组
        groups: Dict[str, List[BlackboardEntry]] = {}
        for e in entries:
            groups.setdefault(e.entry_type.value, []).append(e)

        parts = ["## Shared Context (Blackboard)\n"]
        total = 0
        for type_name, group in groups.items():
            section = f"### {type_name}\n"
            for e in group:
                val_str = json.dumps(e.value, ensure_ascii=False, default=str) \
                          if not isinstance(e.value, str) else e.value
                line = f"- [{e.source_agent}] {e.key}: {val_str}\n"
                if total + len(line) > max_chars:
                    section += "- ... (truncated)\n"
                    break
                section += line
                total += len(line)
            parts.append(section)

        return "\n".join(parts)

    def query_since(self, ts: float, types: Optional[List[EntryType]] = None) -> List[BlackboardEntry]:
        """返回 timestamp >= ts 的所有 entry (从 _history, 含覆盖记录).
        用于子代理订阅黑板增量更新, 避免重复处理已知数据.
        """
        with self._lock:
            entries = [e for e in self._history if e.timestamp >= ts]
        if types:
            entries = [e for e in entries if e.entry_type in types]
        return entries

    def latest_ts(self) -> float:
        """返回当前最新条目的 timestamp (供调用方下次 since_ts 用)."""
        with self._lock:
            if not self._history:
                return 0.0
            return self._history[-1].timestamp

    def to_context_string_since(self, ts: float, max_chars: int = 4000,
                                types: Optional[List[EntryType]] = None) -> str:
        """增量版 to_context_string: 只输出 ts 之后的新增/覆盖.
        返回空字符串表示无更新, 调用方可跳过 prompt 注入.
        """
        entries = self.query_since(ts, types)
        if not entries:
            return ""

        groups: Dict[str, List[BlackboardEntry]] = {}
        for e in entries:
            groups.setdefault(e.entry_type.value, []).append(e)

        parts = [f"## Shared Context (incremental, {len(entries)} new)\n"]
        total = 0
        for type_name, group in groups.items():
            section = f"### {type_name}\n"
            for e in group:
                val_str = json.dumps(e.value, ensure_ascii=False, default=str) \
                          if not isinstance(e.value, str) else e.value
                line = f"- [{e.source_agent}] {e.key}: {val_str}\n"
                if total + len(line) > max_chars:
                    section += "- ... (truncated)\n"
                    break
                section += line
                total += len(line)
            parts.append(section)
        return "\n".join(parts)

    def to_dict(self) -> dict:
        """导出为完整 JSON（用于 checkpoint）。"""
        with self._lock:
            return {k: v.to_dict() for k, v in self._store.items()}

    @classmethod
    def from_dict(cls, data: dict) -> 'Blackboard':
        """从 checkpoint 恢复。"""
        bb = cls()
        for key, d in data.items():
            bb.put(
                key=d["key"], value=d["value"],
                entry_type=EntryType(d.get("type", "context")),
                source=d.get("source", ""),
                metadata=d.get("meta", {}),
            )
        return bb

    def snapshot(self) -> dict:
        """生成快照（用于 State Checkpointing）。"""
        return {
            "entries": self.to_dict(),
            "entry_count": len(self),
            "timestamp": time.time(),
        }


# ── 工具定义：供子 Agent 调用 ───────────────────────────────

BLACKBOARD_TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "update_global_context",
        "description": (
            "向共享黑板写入结构化数据，供其他 Agent 读取。"
            "用于共享：文件路径、API 定义、数据库 schema、配置、错误记录等。"
            "key 应具备描述性（如 'api:POST:/users'、'file:src/main.py'）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "key":   {"type": "string", "description": "唯一标识（如 api:GET:/users）"},
                "value": {"type": "string", "description": "数据内容"},
                "type":  {
                    "type": "string",
                    "enum": [t.value for t in EntryType],
                    "description": "数据类型",
                },
            },
            "required": ["key", "value"],
        },
    },
}
