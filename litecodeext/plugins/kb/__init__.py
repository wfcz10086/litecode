"""plugins/kb — 内置知识库 (PostgreSQL + pgvector + 本地 text2vec 向量模型)。

由一个已有的个人知识库项目改造而来 (差异见 docs/KNOWLEDGE_BASE.md)。模块:
  text.py    文档解析 (pdf/docx/pptx/xlsx/txt/md/html/图片 OCR) + 切分 (450 字, 重叠 80)
  embed.py   本地 ONNX 向量模型 (纯 Python WordPiece 分词, 进程内单例懒加载)
  store.py   PG 建表迁移 + 库/文档/块 CRUD + 混合检索 (向量 + 中文 bigram 关键词) + 上下文窗口
  jobs.py    后台入库队列 (DB 状态驱动, SKIP LOCKED 认领, 失败可重试)
  router.py  /api/kb 接口 (挂在 routers/kb_router.py)
  tool.py    kb_search 工具输出 (带出处 [n])

拔插: config.json 去掉 "kb" 段 (或 kb.enabled=false) 即停用 —— 接口回 enabled=false, 工具回"未启用",
面板显示部署说明; 不影响 LiteCode 其他功能。DSN 也可用环境变量 LITECODE_KB_DSN 覆盖。
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent.parent      # litecodeext/ (容器里 /opt/litecode)
_MODEL_NAME = "text2vec-base-chinese-paraphrase-onnx"

DEFAULTS = {
    "enabled": False,
    "dsn": "",
    "model_dir": "",
    "chunk_size": 450,
    "chunk_overlap": 80,
    "top_k": 5,
    "min_score": 0.5,         # 融合分低于此值不返回 (0.75×向量余弦 + 0.25×关键词覆盖率; 无关内容实测 0.45~0.5)
    "context_chars": 3000,    # 每处命中返回给模型的上下文窗口 (命中块前后扩展), 可调到 4000
    "total_chars": 9000,      # 一次检索返回的上下文总字数上限 (约 6k token)
    "ocr": True,              # 图片 / 扫描版 PDF 走当前模型的视觉识别
    "max_file_mb": 200,
    "model_idle_minutes": 10, # 向量模型闲置多久卸载 (释放 ~500MB 内存), 0 = 常驻
}

_cache: dict = {"mtime": None, "cfg": None}
_lock = threading.Lock()


def config_path() -> Path:
    return Path(os.environ.get("LITECODE_CONFIG", str(_BASE / "config.json")))


def cfg() -> dict:
    """读 config.json 的 kb 段 (按 mtime 缓存, 改配置即时生效)。"""
    p = config_path()
    try:
        mt = p.stat().st_mtime
    except OSError:
        mt = None
    with _lock:
        if _cache["cfg"] is None or _cache["mtime"] != mt:
            raw = {}
            try:
                raw = json.loads(p.read_text(encoding="utf-8")).get("kb") or {}
            except Exception:
                raw = {}
            c = {**DEFAULTS, **{k: v for k, v in raw.items() if v is not None}}
            if "enabled" not in raw:
                c["enabled"] = bool(raw.get("dsn"))
            env_dsn = os.environ.get("LITECODE_KB_DSN", "").strip()
            if env_dsn:
                c["dsn"], c["enabled"] = env_dsn, raw.get("enabled", True) is not False
            _cache.update(mtime=mt, cfg=c)
        return dict(_cache["cfg"])


def enabled() -> bool:
    c = cfg()
    return bool(c["enabled"] and c["dsn"])


def workspace() -> Path:
    try:
        from lib.config import WORKSPACE
        return Path(WORKSPACE)
    except Exception:
        return Path(os.environ.get("LITECODE_WORKSPACE", "/tmp/litecode_workspace"))


def data_dir() -> Path:
    """知识库原始文件根目录: WORKSPACE/kb (桌面版 WORKSPACE 在升级保留区)。"""
    return workspace() / "kb"


def model_dir() -> Path | None:
    """向量模型目录查找顺序: kb.model_dir → LITECODE_KB_MODEL_DIR → WORKSPACE/kb/models/… → /opt/litecode-models/…"""
    c = cfg()
    cands = [c.get("model_dir"), os.environ.get("LITECODE_KB_MODEL_DIR"),
             str(data_dir() / "models" / _MODEL_NAME), f"/opt/litecode-models/{_MODEL_NAME}"]
    for d in cands:
        if d and (Path(d) / "model.onnx").is_file() and (Path(d) / "vocab.txt").is_file():
            return Path(d)
    return None
