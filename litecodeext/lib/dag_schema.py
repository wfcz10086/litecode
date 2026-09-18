"""
dag_schema.py — DAG 计划 JSON Schema 校验 (P5-a)
================================================
现有 orchestrator.py 已有 StepSpec/DAGPlan dataclass.
P5-a 补: JSON 序列化 + JSON Schema 校验 + 持久化 (workspace/dags/*.json)

API:
  DAG_JSON_SCHEMA      — 完整 JSON Schema (draft 7)
  validate_dag_json(d) — 校验 dict 是否合法 DAG, 返回 (ok, errors)
  dag_to_dict(plan)    — DAGPlan → dict
  dag_from_dict(d)     — dict → DAGPlan (校验)
  save_dag(workspace, name, plan)
  load_dag(workspace, name)
  list_dags(workspace)
"""
import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple


DAG_JSON_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["steps"],
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "auto_critic": {"type": "boolean"},
        "critic_checklist": {"type": "string"},
        "total_token_budget": {"type": "integer", "minimum": 0},
        "steps": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["id", "task"],
                "properties": {
                    "id": {"type": "string", "pattern": "^[a-zA-Z0-9_-]+$"},
                    "task": {"type": "string", "minLength": 1},
                    "agent_type": {
                        "type": "string",
                        "enum": ["coder","explorer","researcher","analyst","tester","shell","writer","critic","vision","dag"]
                    },
                    "label": {"type": "string"},
                    "depends_on": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "timeout": {"type": "integer", "minimum": 1},
                    "max_retries": {"type": "integer", "minimum": 0},
                    "context": {"type": "string"},
                    "critical": {"type": "boolean"},
                    "retry_strategy": {"type": "string"},
                    "breakpoint": {"type": "boolean"},
                    # [v2 2026-05] 多条件判断 — DSL 见 core/_when_eval.py
                    "when": {
                        "description": (
                            "条件 (顶层 list = AND). 支持:\n"
                            "  - v1.3 旧字符串: success:<sid> / failure:<sid> / contains:<sid>:<kw> / !取反\n"
                            "  - v2 中缀: \"$step.fetch.status eq success\" / \"$step.x.tool_count gt 3\"\n"
                            "  - v2 dict: {\"eq\": [\"$step.x.status\", \"success\"]}\n"
                            "  - 逻辑: {\"any\":[...]} (OR) / {\"all\":[...]} (AND) / {\"not\": cond}\n"
                            "取值: $step.<sid>.{status,output,json.<jq-path>,tool_count,duration_ms} / "
                            "$env.<NAME> / $cfg.<a.b> / $time.now / 字面量"
                        ),
                    },
                }
            }
        }
    }
}


def validate_dag_json(d) -> Tuple[bool, List[str]]:
    """简易 JSON Schema 校验 (不依赖 jsonschema 库, 手写关键检查)"""
    errors: List[str] = []
    if not isinstance(d, dict):
        return False, ["root must be dict"]
    if "steps" not in d:
        errors.append("missing 'steps'")
    elif not isinstance(d["steps"], list) or not d["steps"]:
        errors.append("'steps' must be non-empty array")
    else:
        ids = set()
        for i, s in enumerate(d["steps"]):
            if not isinstance(s, dict):
                errors.append(f"step[{i}] not dict")
                continue
            sid = s.get("id")
            task = s.get("task")
            if not sid or not isinstance(sid, str):
                errors.append(f"step[{i}] missing id")
            elif sid in ids:
                errors.append(f"step[{i}] duplicate id={sid}")
            else:
                ids.add(sid)
            if not task or not isinstance(task, str):
                errors.append(f"step[{i}] missing task")
            ag = s.get("agent_type", "coder")
            if ag not in {"coder","explorer","researcher","analyst","tester","shell","writer","critic","vision","tool","dag"}:
                errors.append(f"step[{i}] bad agent_type={ag}")
            # [Round 4 · 2026-07-24] agent_type=tool 时校验必带 tool_name
            if ag == "tool" and not s.get("tool_name"):
                errors.append(f"step[{i}] agent_type=tool 必须给 tool_name (registered plugin name)")
            # [#2 2026-09-05] agent_type=dag 时必带 dag_name (要跑的子 DAG 名)
            if ag == "dag" and not s.get("dag_name"):
                errors.append(f"step[{i}] agent_type=dag 必须给 dag_name (要运行的子 DAG 名)")
            deps = s.get("depends_on", []) or []
            if not isinstance(deps, list):
                errors.append(f"step[{i}] depends_on not list")
            if "breakpoint" in s and not isinstance(s["breakpoint"], bool):
                errors.append(f"step[{i}] breakpoint must be bool")
            # [v2 2026-05] when 接受 str / dict / list (含嵌套), 见 core/_when_eval.py
            # 严格 schema 留给求值器自己 try/except, 这里只挡明显错的类型
            w = s.get("when")
            if w is not None and not isinstance(w, (str, dict, list)):
                errors.append(f"step[{i}] when 必须是 str/dict/list, got {type(w).__name__}")
        # 校验 depends_on 都指向已有 step
        for s in d["steps"]:
            for dep in s.get("depends_on", []) or []:
                if dep not in ids:
                    errors.append(f"step {s.get('id')} depends_on={dep} not found")
    return (len(errors) == 0, errors)


def dag_to_dict(plan) -> Dict:
    """DAGPlan dataclass → dict (供 JSON 序列化)"""
    if hasattr(plan, "steps"):
        return {
            "auto_critic": getattr(plan, "auto_critic", True),
            "critic_checklist": getattr(plan, "critic_checklist", ""),
            "total_token_budget": getattr(plan, "total_token_budget", 0),
            "steps": [
                {
                    "id": s.id,
                    "task": s.task,
                    "agent_type": getattr(s, "agent_type", "coder"),
                    "label": getattr(s, "label", ""),
                    "depends_on": list(getattr(s, "depends_on", []) or []),
                    "timeout": getattr(s, "timeout", 300),
                    "max_retries": getattr(s, "max_retries", 2),
                    "context": getattr(s, "context", ""),
                    "critical": getattr(s, "critical", True),
                    "retry_strategy": getattr(s, "retry_strategy", ""),
                    "breakpoint": getattr(s, "breakpoint", False),
                    "when": list(getattr(s, "when", []) or []),
                    "tool_name": getattr(s, "tool_name", ""),
                    "tool_args": dict(getattr(s, "tool_args", {}) or {}),
                }
                for s in plan.steps
            ]
        }
    return dict(plan)


def dag_from_dict(d: Dict):
    """dict → DAGPlan (含校验). 失败抛 ValueError."""
    ok, errs = validate_dag_json(d)
    if not ok:
        raise ValueError("invalid DAG: " + "; ".join(errs))
    # lazy import 避免循环依赖
    from orchestrator import DAGPlan, StepSpec
    steps = []
    for s in d["steps"]:
        steps.append(StepSpec(
            id=s["id"],
            task=s["task"],
            agent_type=s.get("agent_type", "coder"),
            label=s.get("label", ""),
            depends_on=list(s.get("depends_on", []) or []),
            timeout=int(s.get("timeout", 300)),
            max_retries=int(s.get("max_retries", 2)),
            context=s.get("context", ""),
            critical=bool(s.get("critical", True)),
            retry_strategy=s.get("retry_strategy", ""),
            when=list(s.get("when", []) or []),
            tool_name=s.get("tool_name", ""),
            tool_args=dict(s.get("tool_args") or {}),
            dag_name=s.get("dag_name", ""),
        ))
    return DAGPlan(
        steps=steps,
        auto_critic=bool(d.get("auto_critic", True)),
        critic_checklist=d.get("critic_checklist", ""),
        total_token_budget=int(d.get("total_token_budget", 0)),
    )


def _dags_dir(workspace) -> Path:
    p = Path(workspace) / "dags"
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_dag(workspace, name: str, plan_or_dict) -> Path:
    """保存 DAG 到 workspace/dags/{name}.json"""
    if not name.replace("_", "").replace("-", "").isalnum():
        raise ValueError("name must be alnum/-/_")
    if hasattr(plan_or_dict, "steps"):
        d = dag_to_dict(plan_or_dict)
    else:
        d = dict(plan_or_dict)
    ok, errs = validate_dag_json(d)
    if not ok:
        raise ValueError("invalid DAG: " + "; ".join(errs))
    fp = _dags_dir(workspace) / f"{name}.json"
    tmp = fp.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2))
    tmp.replace(fp)
    return fp


def load_dag(workspace, name: str) -> Optional[Dict]:
    fp = _dags_dir(workspace) / f"{name}.json"
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text(errors="replace"))
    except Exception:
        return None


def list_dags(workspace) -> List[str]:
    out = []
    for fp in sorted(_dags_dir(workspace).glob("*.json")):
        out.append(fp.stem)
    return out
