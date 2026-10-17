"""project_map PROJECT_MAP.md 渲染器 (MapRenderer) — 从 project_map_watcher.py 拆出"""

from pathlib import Path
from typing import Dict, List

try:
    from .project_map_parsers import FileInfo, _build_tree_str, _now
except ImportError:  # core/ 扁平导入 或 脚本直跑
    from project_map_parsers import FileInfo, _build_tree_str, _now


# ─────────────────────────────────────────────────────────────
# PROJECT_MAP.md 渲染器
# ─────────────────────────────────────────────────────────────

class MapRenderer:
    """把 FileInfo 列表渲染成 PROJECT_MAP.md 内容"""

    def render_full(self, project_root: Path,
                    file_infos: Dict[str, FileInfo],
                    existing_map: str = "") -> str:
        """完整渲染或增量更新"""
        proj_name = project_root.name
        lines = [f"# {proj_name} — Project Map",
                 f"> 自动生成 · 最后更新: {_now()} · LLM无关\n"]

        # 目录结构
        lines.append("## 目录结构\n```")
        lines.append(_build_tree_str(project_root))
        lines.append("```\n")

        # 模块依赖（汇总所有文件的 imports）
        lines.append("## 模块依赖\n```")
        for relpath, fi in sorted(file_infos.items()):
            if fi.imports and fi.language != "writing":
                lines.append(f"{relpath}  ←  {', '.join(fi.imports[:8])}")
        lines.append("```\n")

        # 核心流程占位（保留手动填写内容）
        if "## 核心流程" in existing_map:
            # 提取已有核心流程段
            start = existing_map.find("## 核心流程")
            end = existing_map.find("\n---\n", start + 5)
            if end > start:
                lines.append(existing_map[start:end])
        else:
            lines.append("## 核心流程\n```\n(首次运行后手动填写，或由 LLM 补充)\n```\n")

        lines.append("---")

        # 按文件分 section
        code_files = {k: v for k, v in file_infos.items() if v.language != "writing"}
        writing_files = {k: v for k, v in file_infos.items() if v.language == "writing"}

        for relpath, fi in sorted(code_files.items()):
            lines.extend(self._render_code_section(relpath, fi, existing_map))

        # 写作模式：章节大纲
        if writing_files:
            lines.append("\n---\n## 章节大纲\n")
            lines.append("| 序号 | 文件 | 标题 | 字数 | 状态 |")
            lines.append("|------|------|------|------|------|")
            i = 1
            for relpath, fi in sorted(writing_files.items()):
                status = "✅done" if fi.word_count > 1000 else "✏️draft"
                lines.append(f"| {i} | {relpath} | {fi.headings[0][1] if fi.headings else '-'} | {fi.word_count} | {status} |")
                i += 1
            lines.append("")

            # 小说角色（从标题中猜）
            all_h2 = [h[1] for fi in writing_files.values() for h in fi.headings if h[0] == 2]
            if all_h2:
                lines.append("### 子章节列表\n")
                for t in all_h2[:30]:
                    lines.append(f"- {t}")
                lines.append("")

        # 测试覆盖
        lines.append("\n---\n## 测试覆盖\n")
        lines.append("| 源文件 | 测试文件 | 状态 |")
        lines.append("|--------|--------|------|")
        for relpath, fi in sorted(code_files.items()):
            fn = Path(relpath).name
            mn = fn.rsplit(".", 1)[0]
            ext = Path(relpath).suffix
            test_name = f"test_{mn}{ext}"
            has_test = any(test_name in k or f"{mn}_test{ext}" in k
                           for k in file_infos.keys())
            icon = "✅" if has_test else "⏳"
            lines.append(f"| {fn} | {test_name} | {icon} |")

        # 变更日志（保留已有 + 追加头部）
        lines.append("\n---\n## 变更日志\n")
        lines.append("| 时间 | 文件 | 类型 | 说明 |")
        lines.append("|------|------|------|------|")
        if "## 变更日志" in existing_map:
            # 提取已有日志行
            idx = existing_map.find("## 变更日志")
            old_log = existing_map[idx:].split("\n\n", 2)
            if len(old_log) > 1:
                for log_line in old_log[1].splitlines()[2:22]:  # 保留最近20条
                    if log_line.startswith("|") and "---" not in log_line:
                        lines.append(log_line)

        return "\n".join(lines) + "\n"

    def render_section(self, relpath: str, fi: FileInfo) -> str:
        """渲染单个文件的 section（用于增量更新）"""
        sec_lines = self._render_code_section(relpath, fi, "")
        return "\n".join(sec_lines)

    def _render_code_section(self, relpath: str, fi: FileInfo,
                              existing_map: str) -> List[str]:
        fn = Path(relpath).name
        lines = [f"\n---\n## {fn}", f"**{relpath}** · {fi.lines}L · `{fi.language}`\n"]

        # Routes
        if fi.routes:
            lines.append("### Routes\n")
            lines.append("| Method | Path | Handler | Params | Doc |")
            lines.append("|--------|------|---------|--------|-----|")
            for r in fi.routes[:20]:
                lines.append(f"| `{r.method}` | `{r.path}` | `{r.handler}` | `{r.params[:50]}` | {r.doc} |")
            lines.append("")

        # Classes
        if fi.classes:
            lines.append("### Classes\n")
            lines.append("| L# | Class | Base | Methods | Doc |")
            lines.append("|----|-------|------|---------|-----|")
            for c in fi.classes[:15]:
                methods_str = ", ".join(f"`{m}`" for m in c.methods[:6])
                if len(c.methods) > 6:
                    methods_str += f" +{len(c.methods)-6}"
                lines.append(f"| {c.line_no} | `{c.name}` | `{c.bases or '-'}` | {methods_str} | {c.doc[:50]} |")
            lines.append("")

        # Functions
        if fi.funcs:
            lines.append("### Functions\n")
            lines.append("| L# | Function | Params | Return | Doc |")
            lines.append("|----|----------|--------|--------|-----|")
            for f in fi.funcs[:30]:
                async_tag = "⚡" if f.is_async else ""
                dec_tag = f" `@{f.decorators[0].split('(')[0]}`" if f.decorators else ""
                lines.append(
                    f"| {f.line_no} | `{async_tag}{f.name}`{dec_tag} "
                    f"| `{f.params[:60]}` | `{f.returns or '-'}` | {f.doc[:50]} |"
                )
            lines.append("")

        # Constants
        if fi.constants:
            lines.append(f"**Constants**: `{'`, `'.join(fi.constants[:15])}`\n")

        # Imports
        if fi.imports:
            lines.append(f"**Imports**: {', '.join(fi.imports[:10])}\n")

        return lines
