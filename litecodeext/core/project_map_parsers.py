"""project_map 数据结构 / 解析器 (Python AST、多语言正则、写作、配置) / 工具函数 — 从 project_map_watcher.py 拆出"""

import ast
import hashlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── 支持语言 ──────────────────────────────────────────────────
_CODE_EXTS = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java",
              ".rb", ".php", ".c", ".cpp", ".h", ".vue", ".svelte", ".sh"}
_WRITING_EXTS = {".md", ".txt", ".rst", ".adoc"}
_CONFIG_EXTS = {".json", ".yaml", ".yml", ".toml"}
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
              "dist", "build", ".next", "target", "vendor", ".mypy_cache",
              ".pytest_cache", ".openclaw_checkpoints"}
_SKIP_FILES = {"PROJECT_MAP.md", "PROJECT_MAP_BRIEF.md", "CALL_GRAPH.json",
               ".manifest_hashes.json", "MEMORY.md", "SYMBOL_INDEX.json"}


# ─────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────

@dataclass
class RouteInfo:
    method: str
    path: str
    handler: str
    params: str = ""
    doc: str = ""

@dataclass
class FuncInfo:
    name: str
    line_no: int
    params: str
    returns: str = ""
    doc: str = ""
    is_async: bool = False
    decorators: List[str] = field(default_factory=list)

@dataclass
class ClassInfo:
    name: str
    line_no: int
    bases: str = ""
    methods: List[str] = field(default_factory=list)
    doc: str = ""

@dataclass
class FileInfo:
    path: str           # 相对于 project_root
    lines: int
    hash: str
    language: str
    routes: List[RouteInfo] = field(default_factory=list)
    funcs: List[FuncInfo] = field(default_factory=list)
    classes: List[ClassInfo] = field(default_factory=list)
    constants: List[str] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    # 写作模式
    word_count: int = 0
    headings: List[Tuple[int, str]] = field(default_factory=list)  # (level, title)


# ─────────────────────────────────────────────────────────────
# Python AST 解析器（最精确）
# ─────────────────────────────────────────────────────────────

class PythonASTParser:
    """使用 ast 模块精确提取 Python 符号"""

    # FastAPI/Flask/Django 路由装饰器模式
    _ROUTE_DECORATORS = re.compile(
        r"(app|router|bp|blueprint|api)\.(get|post|put|delete|patch|head|options|route|websocket)\s*\(\s*['\"]([^'\"]+)['\"]",
        re.I
    )

    def parse(self, source: str, filepath: str) -> FileInfo:
        lines = source.splitlines()
        line_count = len(lines)
        info = FileInfo(
            path=filepath, lines=line_count,
            hash=_md5(source), language="python"
        )
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return info

        info.constants = self._extract_constants(tree)
        info.imports = self._extract_imports(tree)
        info.routes = self._extract_routes(tree, source)
        info.classes = self._extract_classes(tree)
        info.funcs = self._extract_funcs(tree)
        return info

    def _extract_constants(self, tree: ast.AST) -> List[str]:
        consts = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        consts.append(target.id)
            elif isinstance(node, (ast.AnnAssign,)) and isinstance(node.target, ast.Name):
                if node.target.id.isupper():
                    consts.append(node.target.id)
        return consts[:20]

    def _extract_imports(self, tree: ast.AST) -> List[str]:
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module.split(".")[0])
        # 去重，保留前10
        seen = set()
        result = []
        for imp in imports:
            if imp not in seen:
                seen.add(imp)
                result.append(imp)
            if len(result) >= 10:
                break
        return result

    def _extract_routes(self, tree: ast.AST, source: str) -> List[RouteInfo]:
        routes = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                route = self._parse_route_decorator(dec, node)
                if route:
                    routes.append(route)
        return routes

    def _parse_route_decorator(self, dec: ast.expr, func: ast.FunctionDef) -> Optional[RouteInfo]:
        # @app.get("/path") or @router.post("/path")
        if not isinstance(dec, ast.Call):
            return None
        func_node = dec.func
        if not isinstance(func_node, ast.Attribute):
            return None
        method = func_node.attr.upper()
        if method not in {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD",
                           "OPTIONS", "ROUTE", "WEBSOCKET"}:
            return None
        if not dec.args:
            return None
        path_node = dec.args[0]
        if not isinstance(path_node, ast.Constant):
            return None
        path = str(path_node.value)
        # 提取参数
        params = ", ".join(
            f"{arg.arg}: {ast.unparse(arg.annotation) if arg.annotation else '?'}"
            for arg in func.args.args
            if arg.arg not in ("self", "cls", "request", "req", "db", "background_tasks")
        )
        doc = ast.get_docstring(func) or ""
        return RouteInfo(
            method=method, path=path,
            handler=func.name,
            params=params[:80],
            doc=doc.split("\n")[0][:60] if doc else ""
        )

    def _extract_classes(self, tree: ast.AST) -> List[ClassInfo]:
        classes = []
        for node in ast.iter_child_nodes(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = ", ".join(
                ast.unparse(b) for b in node.bases
            )[:60]
            methods = []
            for item in ast.iter_child_nodes(node):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(item.name)
            doc = ast.get_docstring(node) or ""
            classes.append(ClassInfo(
                name=node.name,
                line_no=node.lineno,
                bases=bases,
                methods=methods[:12],
                doc=doc.split("\n")[0][:80] if doc else ""
            ))
        return classes

    def _extract_funcs(self, tree: ast.AST) -> List[FuncInfo]:
        funcs = []
        # 只提取顶层函数（不含类方法）
        for node in ast.iter_child_nodes(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            params = self._fmt_args(node.args)
            returns = ""
            if node.returns:
                try:
                    returns = ast.unparse(node.returns)[:40]
                except Exception:
                    pass
            doc = ast.get_docstring(node) or ""
            decs = []
            for d in node.decorator_list:
                try:
                    decs.append(ast.unparse(d)[:40])
                except Exception:
                    pass
            funcs.append(FuncInfo(
                name=node.name,
                line_no=node.lineno,
                params=params[:100],
                returns=returns,
                doc=doc.split("\n")[0][:80] if doc else "",
                is_async=isinstance(node, ast.AsyncFunctionDef),
                decorators=decs[:3]
            ))
        return funcs

    def _fmt_args(self, args: ast.arguments) -> str:
        parts = []
        # positional
        for arg in args.args:
            if arg.arg in ("self", "cls"):
                continue
            ann = f": {ast.unparse(arg.annotation)}" if arg.annotation else ""
            try:
                parts.append(f"{arg.arg}{ann}")
            except Exception:
                parts.append(arg.arg)
        # *args
        if args.vararg:
            parts.append(f"*{args.vararg.arg}")
        # **kwargs
        if args.kwarg:
            parts.append(f"**{args.kwarg.arg}")
        return ", ".join(parts)


# ─────────────────────────────────────────────────────────────
# 多语言正则解析器
# ─────────────────────────────────────────────────────────────

class RegexParser:
    """JS/TS/Go/Rust/Java/Shell 等的正则解析"""

    _PATTERNS: Dict[str, List[Tuple[str, str]]] = {
        ".js":  [
            (r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)", "func"),
            (r"^(?:export\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?", "class"),
            (r"^const\s+(\w+)\s*=\s*(?:async\s+)?\(([^)]*)\)\s*=>", "func"),
            (r"^(?:app|router)\.(get|post|put|delete|patch)\s*\(\s*['\"`]([^'\"` ]+)['\"`]", "route"),
        ],
        ".go":  [
            (r"^func\s+(?:\(\w+\s+\*?(\w+)\)\s+)?(\w+)\s*\(([^)]*)\)", "method"),
            (r"^type\s+(\w+)\s+struct", "struct"),
            (r"^func\s+(\w+)\s*\(([^)]*)\)(?:\s*\(([^)]*)\)|\s+(\w+))?", "func"),
        ],
        ".rs":  [
            (r"^(?:pub(?:\(\w+\))?\s+)?(?:async\s+)?fn\s+(\w+)\s*(?:<[^>]*>)?\(([^)]*)\)(?:\s*->\s*(.+?))?(?:\s*\{|\s*where)", "func"),
            (r"^(?:pub\s+)?struct\s+(\w+)", "struct"),
            (r"^(?:pub\s+)?enum\s+(\w+)", "enum"),
            (r"^(?:pub\s+)?trait\s+(\w+)", "trait"),
        ],
        ".java":[
            (r"(?:public|private|protected|static|\s)+[\w<>\[\]]+\s+(\w+)\s*\(([^)]*)\)\s*(?:throws\s+[\w,\s]+)?\s*\{", "func"),
            (r"(?:public|private|protected|\s)*(?:abstract\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?(?:\s+implements\s+([^{]+))?", "class"),
        ],
        ".rb":  [
            (r"^\s*def\s+(\w+)(?:\s*\(([^)]*)\))?", "func"),
            (r"^class\s+(\w+)(?:\s*<\s*(\w+))?", "class"),
        ],
        ".sh":  [
            (r"^(?:function\s+)?(\w+)\s*\(\s*\)\s*\{", "func"),
        ],
    }
    # TypeScript 复用 JS 模式并追加
    _PATTERNS[".ts"] = _PATTERNS[".js"] + [
        (r"^(?:export\s+)?(?:abstract\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?", "class"),
        (r"^(?:export\s+)?interface\s+(\w+)", "interface"),
    ]
    _PATTERNS[".tsx"] = _PATTERNS[".ts"]
    _PATTERNS[".jsx"] = _PATTERNS[".js"]

    def parse(self, source: str, filepath: str, ext: str) -> FileInfo:
        lines = source.splitlines()
        info = FileInfo(
            path=filepath, lines=len(lines),
            hash=_md5(source), language=ext.lstrip(".")
        )
        patterns = self._PATTERNS.get(ext, [])
        for lineno, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("//") or stripped.startswith("#"):
                # 提取 import
                if any(stripped.startswith(kw) for kw in ("import ", "from ", "require(")):
                    mod = re.match(r"(?:import|from)\s+['\"]?(\S+?)['\"]?[\s;]", stripped)
                    if mod:
                        info.imports.append(mod.group(1).split("/")[0][:30])
                continue
            for pattern, kind in patterns:
                m = re.search(pattern, stripped)
                if not m:
                    continue
                g = m.groups()
                if kind == "func":
                    name = g[0] if g else "?"
                    params = g[1] if len(g) > 1 else ""
                    info.funcs.append(FuncInfo(name=name, line_no=lineno,
                                               params=(params or "")[:80]))
                elif kind == "class" or kind == "struct":
                    name = g[0] if g else "?"
                    base = g[1] if len(g) > 1 and g[1] else ""
                    info.classes.append(ClassInfo(name=name, line_no=lineno,
                                                   bases=base or ""))
                elif kind == "route":
                    method, path = g[0].upper(), g[1]
                    info.routes.append(RouteInfo(method=method, path=path, handler="?"))
                elif kind == "method":
                    receiver = g[0] if g[0] else ""
                    name = g[1] if len(g) > 1 else (g[0] or "?")
                    params = g[2] if len(g) > 2 else ""
                    info.funcs.append(FuncInfo(name=name, line_no=lineno,
                                               params=(params or "")[:80]))
                break  # 每行匹配一次

        # 去重 imports
        seen = set()
        deduped = []
        for imp in info.imports:
            if imp and imp not in seen:
                seen.add(imp)
                deduped.append(imp)
        info.imports = deduped[:10]
        return info


# ─────────────────────────────────────────────────────────────
# 写作文档解析器（小说/长文）
# ─────────────────────────────────────────────────────────────

class WritingParser:
    """Markdown/txt 文档 → 章节统计，字数统计"""

    def parse(self, source: str, filepath: str) -> FileInfo:
        lines = source.splitlines()
        info = FileInfo(
            path=filepath, lines=len(lines),
            hash=_md5(source), language="writing"
        )
        # 字数（中文按字符，英文按单词）
        chinese_chars = len(re.findall(r"[\u4e00-\u9fa5]", source))
        english_words = len(re.findall(r"\b[a-zA-Z]+\b", source))
        info.word_count = chinese_chars + english_words

        # 章节标题
        for lineno, line in enumerate(lines, 1):
            m = re.match(r"^(#{1,4})\s+(.+)$", line)
            if m:
                level = len(m.group(1))
                title = m.group(2).strip()[:80]
                info.headings.append((level, title))
        return info


# ─────────────────────────────────────────────────────────────
# 配置文件解析器
# ─────────────────────────────────────────────────────────────

class ConfigParser:
    """JSON/YAML/TOML 配置文件 → 顶层 key 列表"""

    def parse(self, source: str, filepath: str) -> FileInfo:
        ext = Path(filepath).suffix.lower()
        info = FileInfo(
            path=filepath, lines=source.count("\n"),
            hash=_md5(source), language="config"
        )
        keys: List[str] = []
        try:
            if ext == ".json":
                import json as _json
                data = _json.loads(source)
                if isinstance(data, dict):
                    keys = list(data.keys())[:20]
            elif ext in (".yaml", ".yml"):
                try:
                    import yaml as _yaml
                    data = _yaml.safe_load(source)
                    if isinstance(data, dict):
                        keys = list(data.keys())[:20]
                except ImportError:
                    pass
            elif ext == ".toml":
                try:
                    import tomllib as _toml
                    data = _toml.loads(source)
                    keys = list(data.keys())[:20]
                except ImportError:
                    try:
                        import tomli as _toml
                        data = _toml.loads(source)
                        keys = list(data.keys())[:20]
                    except ImportError:
                        pass
        except Exception:
            pass
        info.constants = keys
        return info


# ─────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────

def _md5(text: str) -> str:
    return hashlib.md5(text.encode(errors="replace")).hexdigest()[:12]

def _now() -> str:
    return time.strftime("%m-%d %H:%M")

def _count_lines(path: Path) -> int:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0

def _build_tree_str(root: Path, max_depth: int = 3) -> str:
    """生成目录树字符串"""
    lines = [str(root)]

    def _walk(p: Path, prefix: str, depth: int):
        if depth > max_depth:
            return
        try:
            entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        except PermissionError:
            return
        visible = [e for e in entries
                   if e.name not in _SKIP_DIRS
                   and not e.name.startswith(".")
                   and e.name not in _SKIP_FILES]
        for i, item in enumerate(visible):
            is_last = (i == len(visible) - 1)
            connector = "`-- " if is_last else "|-- "
            suffix = "/" if item.is_dir() else ""
            if item.is_file() and item.suffix in _CODE_EXTS | _WRITING_EXTS | _CONFIG_EXTS:
                lc = _count_lines(item)
                suffix = f"  ({lc}L)" if lc else ""
            lines.append(f"{prefix}{connector}{item.name}{suffix}")
            if item.is_dir():
                ext = "    " if is_last else "|   "
                _walk(item, prefix + ext, depth + 1)

    _walk(root, "", 1)
    return "\n".join(lines)
