"""
agent_helpers.py -- zero-closure-dependency pure functions extracted from agent_stream()

Moved out of litecode_server.py (P2-5): these three functions had 0 closure
dependencies (verified via AST), so they're safe to hoist to module level.
"""
import json


def _auto_map_build_skeleton(src_files: list, test_files: set, project_root: str) -> str:
    """生成 PROJECT_MAP.md 骨架 — 高质量语义地图，适用于编码/长文/论文/分析等任何复杂任务"""
    import os as _sk_os

    # ── 扫描目录结构 ──
    def _count_lines(fpath):
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                return sum(1 for _ in f)
        except Exception:
            return 0

    def _build_tree(root, prefix="", max_depth=3, depth=0):
        """生成目录树，带行数统计"""
        tree_lines = []
        if depth >= max_depth:
            return tree_lines
        try:
            entries = sorted(_sk_os.listdir(root))
        except PermissionError:
            return tree_lines
        # 过滤隐藏文件和常见噪声目录
        skip = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', '.cache',
                '.mypy_cache', '.pytest_cache', 'dist', 'build', '.egg-info'}
        entries = [e for e in entries if e not in skip and not e.startswith('.')]
        dirs = [e for e in entries if _sk_os.path.isdir(_sk_os.path.join(root, e))]
        files = [e for e in entries if _sk_os.path.isfile(_sk_os.path.join(root, e))]
        for f in files:
            fp = _sk_os.path.join(root, f)
            lc = _count_lines(fp)
            suffix = f"  {lc} lines" if lc > 0 and f.endswith(('.py', '.js', '.ts', '.jsx', '.tsx', '.go', '.rs', '.java', '.md', '.html', '.css', '.vue', '.rb', '.php', '.c', '.cpp', '.h')) else ""
            tree_lines.append(f"  {prefix}{f}{suffix}")
        for d in dirs:
            dp = _sk_os.path.join(root, d)
            sub_files = []
            try:
                sub_files = _sk_os.listdir(dp)
            except Exception:
                pass
            n_items = len([x for x in sub_files if not x.startswith('.')])
            tree_lines.append(f"  {prefix}{d}/{'  ' + str(n_items) + ' files' if depth >= max_depth - 1 and n_items > 3 else ''}")
            tree_lines.extend(_build_tree(dp, prefix + "  ", max_depth, depth + 1))
        return tree_lines

    lines = []
    # ── 标题 ──
    proj_name = _sk_os.path.basename(project_root)
    lines.append(f"# {proj_name} - Project Map\n")

    # ── 目录结构 ──
    lines.append("\n## 目录结构\n```")
    lines.append(f"{proj_name}/")
    tree = _build_tree(project_root)
    lines.extend(tree[:80])  # 限制行数防过大
    if len(tree) > 80:
        lines.append(f"  ... ({len(tree) - 80} more entries)")
    lines.append("```\n")

    # ── 模块依赖 ──
    lines.append("\n---\n## 模块依赖\n```")
    lines.append("(update_map 填充: 每个模块的 import 关系、调用链路)")
    lines.append("```\n")

    # ── 核心流程 ──
    lines.append("\n---\n## 核心流程\n```")
    lines.append("(update_map 填充: 主链路 / 数据流 / 消息处理链路)")
    lines.append("```\n")

    # ── 按文件生成独立 section ──
    _code_exts = {'.py', '.js', '.ts', '.jsx', '.tsx', '.go', '.rs', '.java',
                  '.rb', '.php', '.c', '.cpp', '.h', '.vue', '.svelte'}
    _writing_exts = {'.md', '.txt', '.rst', '.tex', '.adoc'}

    # 分离代码文件和文档文件
    code_files = []
    doc_files = []
    for _f in src_files:
        _ext = _sk_os.path.splitext(_f)[1].lower()
        if _ext in _code_exts:
            code_files.append(_f)
        elif _ext in _writing_exts:
            doc_files.append(_f)

    # ── 每个代码文件生成独立 section (参考级格式) ──
    for _f in code_files:
        _bn = _f.split("/")[-1]
        _relpath = _f.replace(project_root + "/", "")
        _lc = _count_lines(_f)
        lines.append(f"\n---\n## {_bn}\n**{_relpath}** ({_lc} lines)\n")

        # API Endpoints 子表 (占位)
        lines.append(f"\n### API Endpoints\n")
        lines.append(f"| Route | Function | Params | Doc |")
        lines.append(f"|-------|----------|--------|-----|")
        lines.append(f"")

        # Functions 子表 (占位)
        lines.append(f"\n### Functions\n")
        lines.append(f"| L# | Function | Params | Return | Doc |")
        lines.append(f"|----|----------|--------|--------|-----|")
        lines.append(f"")

        # Classes 子表 (占位)
        lines.append(f"\n### Classes\n")
        lines.append(f"| L# | Class | Base | Key Methods | Doc |")
        lines.append(f"|----|-------|------|-------------|-----|")
        lines.append(f"")

        # Constants (占位)
        lines.append(f"\n**Constants**: (update_map 填充)\n")

    # ── 文档/章节文件 section ──
    if doc_files:
        lines.append("\n---\n## 章节大纲\n")
        lines.append("| 序号 | 文件 | 标题 | 状态 | 字数 | 备注 |")
        lines.append("|------|------|------|------|------|------|")
        for i, _f in enumerate(doc_files, 1):
            _bn = _f.split("/")[-1]
            _lc = _count_lines(_f)
            lines.append(f"| {i} | {_bn} | - | draft | ~{_lc * 5} | - |")
        lines.append("")

    # ── 论点图谱 (研究/论文用) ──
    lines.append("\n---\n## 核心论点图谱\n")
    lines.append("| 论点 | 支撑证据 | 来源 | 强度 |")
    lines.append("|------|----------|------|------|")
    lines.append("")

    # ── 场景/角色 (剧本/小说用) ──
    lines.append("\n---\n## 场景列表\n")
    lines.append("| 场景ID | 所在文件 | 标题/摘要 | 地点 | 出场角色 | 状态 | 字数 | 备注 |")
    lines.append("|--------|--------|---------|------|---------|------|------|------|")
    lines.append("")

    lines.append("\n## 角色表\n")
    lines.append("| 角色名 | 角色类型 | 成长弧线 | 首次出场 | 状态 |")
    lines.append("|--------|--------|--------|--------|------|")
    lines.append("")

    # ── 测试覆盖 ──
    lines.append("\n---\n## 测试覆盖\n")
    lines.append("| 源文件 | 测试文件 | 测试函数 | 状态 | 运行命令 | 覆盖场景 |")
    lines.append("|--------|--------|--------|------|---------|--------|")
    for _f in code_files:
        _bn = _f.split("/")[-1]
        _mn = _bn.rsplit(".", 1)[0]
        _has = any(_mn in tf for tf in test_files)
        lines.append(f"| {_bn} | tests/test_{_bn} | - | {'✅pass' if _has else '⏳pending'} | pytest tests/test_{_bn} -v | - |")
    lines.append("")

    # ── 配置项 ──
    lines.append("\n---\n## 配置项\n")
    lines.append("| 文件 | 配置Key/EnvVar | 默认值 | 必填 | 说明 |")
    lines.append("|------|--------------|-------|------|------|")
    lines.append("")

    # ── 实现进度 ──
    lines.append("\n---\n## 实现进度\n")
    lines.append("| 文件 | 状态 | 已完成 | 待完成 | 已知问题 |")
    lines.append("|------|------|-------|-------|--------|")
    for _f in code_files:
        _bn = _f.split("/")[-1]
        lines.append(f"| {_bn} | partial | - | - | - |")
    lines.append("")

    # ── 变更日志 ──
    lines.append("\n---\n## 变更日志\n")
    lines.append("| 时间 | 操作 | 文件/章节 | 说明 |")
    lines.append("|------|------|----------|------|")
    lines.append("")

    return "\n".join(lines)


def _safe_json_args(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return "{}"
    try:
        json.loads(s)
        return s
    except Exception:
        return "{}"


def _um_find_table_end(lines, header_keyword):
    _in_table = False
    _last_row = -1
    for i, l in enumerate(lines):
        if header_keyword in l:
            _in_table = True
        elif _in_table and l.startswith("|"):
            _last_row = i
        elif _in_table and not l.startswith("|") and l.strip():
            break
    return _last_row
