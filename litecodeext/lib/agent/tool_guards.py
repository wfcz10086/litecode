"""工具调用前的守卫家族与 update_map 工具实现。

从 litecode_server.py 原样搬出 (拆分大文件), 函数体未改动; litecode_server 仍 re-export 这些名字。
"""
from lib.agent_helpers import _auto_map_build_skeleton
from lib.agent_helpers import _um_find_table_end
from lib.config import log


# ── [AGENT_STREAM_REFACTOR M7b 2026-09-18] 工具调用前守卫家族, 从主循环整体迁出
#    (原 ~242 行, 5 站点: BLOCK-MAP 强制建图+AUTO-MAP / BLOCK1.5 强制填图 /
#    BLOCK2 强制测试 / BLOCK-OVERWRITE 防覆盖 / ROUTE 长文强制 spawn_agent)。
#    纯同步。返回 None=放行(可能已执行 AUTO-MAP 副作用); 返回 str=拦截理由。
#    共享尾巴 (tool_results.append + tracer + continue) 收拢到调用点写一次; 站点专属
#    log 副作用保留在本函数内。tool_call_id 统一取
#    tc.get("id","") 的容错形态 (原 4 站点 tc["id"] / 1 站点 tc.get, 语义超集)。──
def _guard_tool_call(fn_name: str, fn_args: dict, pm_state, test_state,
                     misc_state, force_writer_mode: bool):
    # ── [BLOCK] PROJECT_MAP 强制: 写第3个源文件前必须有 PROJECT_MAP ──
    if fn_name == "write_file" and pm_state.project_root:
        import os as _bos
        _bfp = fn_args.get("filepath", "").replace("\\", "/")
        _bfn = _bfp.split("/")[-1]
        _bext = "." + _bfn.rsplit(".", 1)[-1] if "." in _bfn else ""
        _BSRC = {".py", ".js", ".ts", ".sh", ".go"}
        _BSKIP = ("test_", "_test.", ".test.", ".spec.", "__init__",
                  "config", "settings", "constants", "conftest",
                  "requirements", "PROJECT_MAP", ".gitkeep")
        _is_src = _bext in _BSRC and not any(s in _bfn for s in _BSKIP)
        _is_test = any(p in _bfn for p in ("test_", "_test.", ".test.", ".spec."))
        _map_path_b = pm_state.project_root + "/PROJECT_MAP.md"

        # BLOCK 1: 写第6个源文件时 PROJECT_MAP 必须存在 → 自动创建
        if (_is_src and pm_state.py_files_written >= 5
                and not pm_state.project_map_written
                and not _bos.path.exists(_map_path_b)):
            # ── [AUTO-MAP] 用 project_map_watcher 深度 AST 分析 ──
            try:
                from project_map_watcher import get_global_watcher
                _watcher = get_global_watcher()
                _watcher.force_update(pm_state.project_root)
                pm_state.project_map_written = True
                pm_state.py_at_map_write = pm_state.py_files_written
                log.info(f"  [AUTO-MAP] AST-analyzed {_map_path_b} via project_map_watcher")
            except ImportError:
                # fallback: 用内联骨架
                _bm_content = _auto_map_build_skeleton(test_state.src_files_written, test_state.test_files_written, pm_state.project_root)
                with open(_map_path_b, "w", encoding="utf-8") as _bmfp:
                    _bmfp.write(_bm_content)
                pm_state.project_map_written = True
                pm_state.py_at_map_write = pm_state.py_files_written
                log.info(f"  [AUTO-MAP] skeleton-created {_map_path_b} (fallback, no AST)")
            except Exception as _bme:
                log.warning(f"  [AUTO-MAP] auto-create failed: {_bme}, falling back to block")
                _reject_result = (
                    f"ERROR[BLOCK-MAP]: 已有 {pm_state.py_files_written} 个源文件但 PROJECT_MAP.md 不存在。\n"
                    f"必须先创建 {_map_path_b}，然后才能继续写源文件。\n"
                    f"本次 write_file({_bfn}) 已被拦截，创建 PROJECT_MAP.md 后重试。"
                )
                log.warning(f"  [BLOCK-MAP] rejected write_file({_bfn}): no PROJECT_MAP")
                return _reject_result

        # BLOCK 1.5: 源文件过多未调 update_map → 拦截并强制填充 PROJECT_MAP
        if (_is_src and pm_state.files_since_map_update >= 5
                and pm_state.project_map_written and pm_state.project_root):
            import os as _bmo
            _map_check = pm_state.project_root + "/PROJECT_MAP.md"
            _map_empty = True
            if _bmo.path.exists(_map_check):
                _mc = open(_map_check, "r", encoding="utf-8").read()
                # 检查 API合约 和 函数索引 表是否有内容行
                _has_api = False
                _has_func = False
                _in_api = False
                _in_func = False
                for _ml in _mc.splitlines():
                    if "## API" in _ml: _in_api = True; _in_func = False; continue
                    if "## 函数索引" in _ml: _in_func = True; _in_api = False; continue
                    if _ml.startswith("## "): _in_api = False; _in_func = False; continue
                    if _in_api and _ml.startswith("|") and not _ml.startswith("|--") and "Method" not in _ml:
                        _has_api = True
                    if _in_func and _ml.startswith("|") and not _ml.startswith("|--") and "文件" not in _ml:
                        _has_func = True
                _map_empty = not (_has_api or _has_func)
            if _map_empty:
                _src_written_list = [f.split("/")[-1] for f in test_state.src_files_written[:10]]
                _reject_result = (
                    f"ERROR[BLOCK-MAP-UPDATE]: 已写入 {pm_state.files_since_map_update} 个源文件未调用 update_map，PROJECT_MAP.md 关键表仍为空。\n"
                    f"本次 write_file({_bfn}) 被拦截。每写5个源文件必须调用一次 update_map。\n"
                    f"步骤: 对每个已写的源文件依次执行:\n"
                    f"  1. read_file(<源文件>)  ← 获取真实签名\n"
                    f"  2. update_map(\n"
                    f"       filepath=<相对路径>,\n"
                    f"       routes=[{{method,path,handler,params含类型,returns响应格式}}],\n"
                    f"       functions=[{{name,params含类型,returns类型,calls调用链,called_by被调用}}],\n"
                    f"       classes=[{{name,bases,methods}}],\n"
                    f"       deps={{imports,db_tables,ext_apis,used_by}},\n"
                    f"       config=[{{key,default,required}}],\n"
                    f"       progress={{status:done/partial/todo,done,todo,issues}},\n"
                    f"       tests=[{{test_file,test_func,status:pass|fail|pending,cmd,covers}}],\n"
                    f"       templates=[{{path,type,purpose}}],  ← 关联html/sql/yaml/prompt资产\n"
                    f"     )\n"
                    f"待注册文件: {', '.join(_src_written_list)}"
                )
                log.warning(f"  [BLOCK-MAP-UPDATE] rejected write_file({_bfn}): {pm_state.files_since_map_update} files without update_map")
                return _reject_result

        # BLOCK 2: 每3个源文件必须有至少1个对应测试，否则拦截
        if _is_src and pm_state.py_files_written >= 2:
            import os as _tos
            # 构建已测试模块集合（基于文件名+内容扫描）
            _tested_modules = set()
            for _tf_path in test_state.test_files_written:
                _tf_basename = _tf_path.split("/")[-1].rsplit(".", 1)[0]
                # 从文件名提取: test_ssh_client → ssh_client
                if _tf_basename.startswith("test_"):
                    _tested_modules.add(_tf_basename[5:])
                # 扫描文件内容找 def test_xxx / import xxx
                try:
                    _tf_content = open(_tf_path).read(5000)
                    import re as _re
                    for _m in _re.findall(r'def test_(\w+)', _tf_content):
                        _tested_modules.add(_m)
                    for _m in _re.findall(r'from\s+\S+\.(\w+)\s+import', _tf_content):
                        _tested_modules.add(_m)
                except Exception:
                    pass
            # 同样扫描磁盘上 tests/ 目录
            if pm_state.project_root:
                _tests_dir = pm_state.project_root + "/tests"
                if _tos.path.isdir(_tests_dir):
                    for _tdf in _tos.listdir(_tests_dir):
                        if _tdf.startswith("test_") and _tdf.endswith((".py",".js",".ts",".sh",".go")):
                            _tested_modules.add(_tdf.split("test_",1)[1].rsplit(".",1)[0])
                            try:
                                _tdc = open(_tos.path.join(_tests_dir, _tdf)).read(5000)
                                for _m in _re.findall(r'def test_(\w+)', _tdc):
                                    _tested_modules.add(_m)
                                for _m in _re.findall(r'from\s+\S+\.(\w+)\s+import', _tdc):
                                    _tested_modules.add(_m)
                            except Exception:
                                pass

            _untested_b = []
            for _tsf in test_state.src_files_written:
                _tsfname = _tsf.split("/")[-1].rsplit(".", 1)[0]
                _tsfext  = "." + _tsf.rsplit(".", 1)[-1] if "." in _tsf else ".py"
                _tsfdir  = _tsf.rsplit("/", 1)[0]
                _thas = (
                    _tsfname in _tested_modules
                    or _tos.path.exists(f"{_tsfdir}/test_{_tsfname}{_tsfext}")
                    or _tos.path.exists(f"{pm_state.project_root}/tests/test_{_tsfname}{_tsfext}")
                )
                if not _thas:
                    _untested_b.append(_tsf.split("/")[-1])
            # 规则: 累计无测试的源文件超过5个则拦截
            if len(_untested_b) >= 5:
                _ext_tpl = {
                    ".py": f"tests/test_{_untested_b[0].rsplit('.',1)[0]}.py",
                    ".js": f"{_untested_b[0].rsplit('.',1)[0]}.test.js",
                    ".sh": f"tests/test_{_untested_b[0].rsplit('.',1)[0]}.sh",
                    ".go": f"{_untested_b[0].rsplit('.',1)[0]}_test.go",
                }
                _tpl_file = _ext_tpl.get(_bext, f"tests/test_{_untested_b[0].rsplit('.',1)[0]}.py")
                _tpl_body = {
                    ".py": (
                        "import pytest\n"
                        f"# from app.services.{_untested_b[0].rsplit('.',1)[0]} import ...\n"
                        "def test_happy():\n    assert True  # 替换为真实断言\n"
                        "def test_error():\n    with pytest.raises(Exception): pass"
                    ),
                    ".js": (
                        "const assert = require('assert');\n"
                        f"// const mod = require('./{_untested_b[0].rsplit('.',1)[0]}');\n"
                        "assert.ok(true, 'basic test');\nconsole.log('PASS');"
                    ),
                    ".sh": (
                        "#!/usr/bin/env bash\nset -euo pipefail\n"
                        "assert_eq() { [ \"$1\" = \"$2\" ] || { echo \"FAIL: $1 != $2\"; exit 1; }; }\n"
                        f"# source ../app/services/{_untested_b[0]}\n"
                        "assert_eq \"expected\" \"expected\"\necho ALL PASS"
                    ),
                }.get(_bext, "import pytest\ndef test_happy(): assert True")
                _reject_result = (
                    f"ERROR[BLOCK-TEST]: {len(_untested_b)} 个源文件没有测试，"
                    f"超过限额(5)，本次 write_file({_bfn}) 被拦截。\n"
                    f"必须先为以下模块写测试才能继续写源文件:\n"
                    + "\n".join(f"  - {f}" for f in _untested_b[:3])
                    + f"\n\n立即创建: {_tpl_file}\n"
                    f"模板:\n{_tpl_body}\n\n"
                    f"写完测试后必须立即执行验证（不能跳过）。每累计5个无测试模块会再次拦截。"
                )
                log.warning(f"  [BLOCK-TEST] rejected write_file({_bfn}): {len(_untested_b)} untested")
                return _reject_result

    # ── [BLOCK] PROJECT_MAP patch_file 保护: 检测到 write_file 覆盖 PROJECT_MAP ──
    if (fn_name == "write_file"
            and "PROJECT_MAP" in fn_args.get("filepath", "")
            and pm_state.project_map_written):
        # 已经有 PROJECT_MAP 了，用 write_file 会覆盖，拦截并要求 patch_file
        _reject_result = (
            "ERROR[BLOCK-OVERWRITE]: PROJECT_MAP.md 已存在，不能用 write_file 覆盖（会丢失已有内容）。\n"
            "必须用 patch_file 追加新行到对应表格。\n"
            "示例: patch_file(filepath='PROJECT_MAP.md', old_str='| 最后一行 |', new_str='| 最后一行 |\n| 新模块:行 | 函数(参数) | 返回 |')"
        )
        log.warning(f"  [BLOCK-OVERWRITE] rejected write_file(PROJECT_MAP): use patch_file")
        return _reject_result

    # [v1.0] 架构级强制路由: 长文 write_file 拦截转 spawn_agent
    # 当写作任务模式下, LLM 试图直接 write_file 写 >5000c 的 .md 文件时,
    # 拒绝执行并返回错误, 强制 LLM 改用 spawn_agent(writer)
    if (fn_name == "write_file" and force_writer_mode
            and fn_args.get("filepath", "").endswith(".md")):
        _content = fn_args.get("content") or ""
        if len(_content) > 5000:
            log.warning(f"  [ROUTE] write_file intercepted: {len(_content)}c .md in writer mode -> forcing spawn_agent")
            _reject_result = (
                f"ERROR[ROUTE]: 写作任务中禁止在主循环直接 write_file 超过 5000 字的 .md 文件 "
                f"(当前 {len(_content)} 字)。\n"
                "你必须使用 spawn_agent(agent_type='writer', task='写第N章...') 来分批写作。\n"
                "每次 spawn_agent 只写 1-3 章, 每章一个独立文件。\n"
                "已拦截本次 write_file, 请立即改用 spawn_agent。"
            )
            return _reject_result

    return None


# ── [AGENT_STREAM_REFACTOR M7a 2026-09-18] update_map 内部工具实现, 从主循环
#    工具分派段整体迁出 (原 ~295 行内联块)。纯同步、无 yield/await、无 _ctx 依赖,
#    接口只收 fn_args + ProjectMapState。行为与迁移前逐行一致 (dedent + pm_state 重命名)。──
def _tool_update_map(fn_args: dict, pm_state) -> str:
    """update_map: LLM 传结构化数据, 系统写入 PROJECT_MAP.md (per-file sections)。
    返回 result 字符串 (OK:.../ERROR:...); 调用方负责 diff=None 与后续分派。"""
    try:
        import os as _um_os
        import re as _um_re
        pm_state.files_since_map_update = 0  # 重置计数
        _um_fp = fn_args.get("filepath", "")
        _um_routes = fn_args.get("routes", [])
        _um_funcs = fn_args.get("functions", [])
        _um_classes = fn_args.get("classes", [])
        _um_file_desc = fn_args.get("file_description", "")
        _um_constants = fn_args.get("constants", "")
        _um_deps_graph = fn_args.get("deps_graph", "")
        _um_core_flow = fn_args.get("core_flow", "")
        _um_map = (pm_state.project_root + "/PROJECT_MAP.md") if pm_state.project_root else ""

        if not _um_map or not _um_os.path.exists(_um_map):
            result = "ERROR: PROJECT_MAP.md not found"
        else:
            _um_content = open(_um_map, "r", encoding="utf-8").read()
            _um_bn = _um_fp.split("/")[-1]
            _inserted = []

            # ── 构建该文件的完整 section 内容 ──
            _sec_lines = []

            # 文件描述
            _lc = 0
            try:
                _full_path = _um_os.path.join(pm_state.project_root, _um_fp)
                if _um_os.path.exists(_full_path):
                    with open(_full_path, "r", encoding="utf-8", errors="ignore") as _lcf:
                        _lc = sum(1 for _ in _lcf)
            except Exception:
                pass
            _desc_str = f"**{_um_file_desc}**\n" if _um_file_desc else ""
            _sec_lines.append(f"\n---\n## {_um_bn}\n{_desc_str}")

            # API Endpoints 子表
            if _um_routes:
                _sec_lines.append(f"\n### API Endpoints\n")
                _sec_lines.append(f"| Route | Function | Params | Doc |")
                _sec_lines.append(f"|-------|----------|--------|-----|")
                for _r in _um_routes:
                    _route_str = f"`{_r.get('method','?')}` `{_r.get('path','?')}`"
                    _sec_lines.append(
                        f"| {_route_str} | `{_r.get('handler','-')}` "
                        f"| `{_r.get('params','-')}` | {_r.get('note','-')} |"
                    )
                _inserted.append(f"{len(_um_routes)} routes")

            # Classes 子表 (每个 class 独立小节)
            if _um_classes:
                for _c in _um_classes:
                    _c_ln = _c.get("line_no", "")
                    _c_name = _c.get("name", "")
                    _c_bases = _c.get("bases", "")
                    _c_bases_str = f"({_c_bases})" if _c_bases else ""
                    _sec_lines.append(f"\n### class {_c_name}{_c_bases_str}\n")
                    # 如果有 methods 字符串，解析成表
                    _methods_str = _c.get("methods", "")
                    if _methods_str:
                        _sec_lines.append(f"| L# | Method | Params | Return | Doc |")
                        _sec_lines.append(f"|----|--------|--------|--------|-----|")
                        for _m in _methods_str.split(","):
                            _m = _m.strip()
                            if _m:
                                _sec_lines.append(f"| - | `{_m}` | - | - | - |")
                    _note = _c.get("note", "")
                    if _note:
                        _sec_lines.append(f"\n{_note}\n")
                _inserted.append(f"{len(_um_classes)} classes")

            # Functions 子表
            if _um_funcs:
                _sec_lines.append(f"\n### Functions\n")
                _sec_lines.append(f"| L# | Function | Params | Return | Doc |")
                _sec_lines.append(f"|----|----------|--------|--------|-----|")
                for _f in _um_funcs:
                    _ln = _f.get("line_no", "-")
                    _sec_lines.append(
                        f"| {_ln} | `{_f.get('name','-')}` "
                        f"| `{_f.get('params','-')}` | `{_f.get('returns','-')}` "
                        f"| {_f.get('note','-')} |"
                    )
                _inserted.append(f"{len(_um_funcs)} functions")

            # Constants
            if _um_constants:
                _sec_lines.append(f"\n**Constants**: `{_um_constants}`\n")
                _inserted.append("constants")

            # ── 替换或插入该文件的 section ──
            # 查找已有的 ## {_um_bn} section 并替换
            _sec_pattern = f"\n---\n## {_um_re.escape(_um_bn)}\n"
            _sec_start = _um_content.find(f"\n---\n## {_um_bn}\n")
            if _sec_start == -1:
                _sec_start = _um_content.find(f"## {_um_bn}\n")
            
            if _sec_start >= 0:
                # 找到 section 结束位置 (下一个 \n---\n## 或文件末尾)
                _rest = _um_content[_sec_start + 1:]
                # 跳过当前 section header 行
                _next_sec = _rest.find("\n---\n## ", 5)
                if _next_sec > 0:
                    _sec_end = _sec_start + 1 + _next_sec
                else:
                    # 检查是否有其他顶级 ## section (测试覆盖、配置项等)
                    _next_top = -1
                    for _top_sec in ["\n---\n## 测试覆盖", "\n---\n## 配置项", "\n---\n## 实现进度",
                                     "\n---\n## 变更日志", "\n---\n## 核心论点", "\n---\n## 场景列表",
                                     "\n---\n## 角色表", "\n---\n## 章节大纲"]:
                        _pos = _um_content.find(_top_sec, _sec_start + 5)
                        if _pos > _sec_start and (_next_top == -1 or _pos < _next_top):
                            _next_top = _pos
                    _sec_end = _next_top if _next_top > 0 else len(_um_content)
                # 替换
                _um_content = _um_content[:_sec_start] + "\n".join(_sec_lines) + "\n" + _um_content[_sec_end:]
            else:
                # 没有找到，在 测试覆盖 或 配置项 之前插入
                _insert_before = None
                for _anchor in ["\n---\n## 测试覆盖", "\n---\n## 配置项", "\n---\n## 实现进度",
                                "\n---\n## 变更日志", "\n---\n## 核心论点", "\n---\n## 场景列表"]:
                    _pos = _um_content.find(_anchor)
                    if _pos > 0:
                        _insert_before = _pos
                        break
                if _insert_before:
                    _um_content = _um_content[:_insert_before] + "\n".join(_sec_lines) + "\n" + _um_content[_insert_before:]
                else:
                    _um_content += "\n" + "\n".join(_sec_lines) + "\n"

            # ── 更新全局 sections (deps_graph, core_flow) ──
            if _um_deps_graph:
                _dep_marker = "## 模块依赖\n```"
                _dep_pos = _um_content.find(_dep_marker)
                if _dep_pos >= 0:
                    _dep_end = _um_content.find("```\n", _dep_pos + len(_dep_marker))
                    if _dep_end >= 0:
                        _um_content = (_um_content[:_dep_pos] +
                                       f"## 模块依赖\n```\n{_um_deps_graph}\n```\n" +
                                       _um_content[_dep_end + 4:])
                        _inserted.append("deps_graph")

            if _um_core_flow:
                _flow_marker = "## 核心流程\n```"
                _flow_pos = _um_content.find(_flow_marker)
                if _flow_pos >= 0:
                    _flow_end = _um_content.find("```\n", _flow_pos + len(_flow_marker))
                    if _flow_end >= 0:
                        _um_content = (_um_content[:_flow_pos] +
                                       f"## 核心流程\n```\n{_um_core_flow}\n```\n" +
                                       _um_content[_flow_end + 4:])
                        _inserted.append("core_flow")

            # ── 更新扁平表 (测试覆盖, 配置项, 实现进度, 章节, 论点, 场景, 角色, 变更日志) ──
            _um_lines = _um_content.split("\n")

            # 依赖关系表
            _um_deps = fn_args.get("deps", {})
            if _um_deps:
                # 清除旧条目
                _um_lines = [l for l in _um_lines
                             if not (l.startswith("|") and
                                     len([c.strip() for c in l.split("|")]) > 1 and
                                     [c.strip() for c in l.split("|")][1] == _um_fp)]

            # 测试覆盖
            _um_tests = fn_args.get("tests", [])
            if _um_tests:
                # 清除该文件旧测试行
                _um_lines = [l for l in _um_lines
                             if not (l.startswith("|") and _um_bn in l and "test" in l.lower())]
                _pos_tst = _um_find_table_end(_um_lines, "## 测试覆盖")
                if _pos_tst > 0:
                    for _t in reversed(_um_tests):
                        _tst_icon = {"pass": "✅", "fail": "❌", "pending": "⏳", "skip": "⏭"}.get(_t.get("status", "pending"), "⏳")
                        _row = (f"| {_um_bn} | {_t.get('test_file','-')} | "
                                f"{_t.get('test_func','-')} | {_tst_icon}{_t.get('status','pending')} | "
                                f"{_t.get('cmd','-')} | {_t.get('covers','-')} |")
                        _um_lines.insert(_pos_tst + 1, _row)
                    _inserted.append(f"{len(_um_tests)} tests")

            # 配置项
            _um_config = fn_args.get("config", [])
            if _um_config:
                _um_lines = [l for l in _um_lines
                             if not (l.startswith("|") and
                                     len([c.strip() for c in l.split("|")]) > 1 and
                                     [c.strip() for c in l.split("|")][1] == _um_fp)]
                _pos_cfg = _um_find_table_end(_um_lines, "## 配置项")
                if _pos_cfg > 0:
                    for _cfg in reversed(_um_config):
                        _row = (f"| {_um_fp} | {_cfg.get('key','')} | "
                                f"{_cfg.get('default','-')} | {'是' if _cfg.get('required') else '否'} | "
                                f"{_cfg.get('note','')} |")
                        _um_lines.insert(_pos_cfg + 1, _row)
                    _inserted.append(f"{len(_um_config)} configs")

            # 实现进度
            _um_prog = fn_args.get("progress", {})
            if _um_prog:
                _um_lines = [l for l in _um_lines
                             if not (l.startswith("|") and
                                     len([c.strip() for c in l.split("|")]) > 1 and
                                     [c.strip() for c in l.split("|")][1] in (_um_fp, _um_bn))]
                _pos_prg = _um_find_table_end(_um_lines, "## 实现进度")
                if _pos_prg > 0:
                    _row = (f"| {_um_bn} | {_um_prog.get('status','-')} | "
                            f"{_um_prog.get('done','-')} | {_um_prog.get('todo','-')} | "
                            f"{_um_prog.get('issues','-')} |")
                    _um_lines.insert(_pos_prg + 1, _row)
                    _inserted.append("1 progress")

            # 章节大纲
            _um_chapters = fn_args.get("chapters", [])
            if _um_chapters:
                _pos_ch = _um_find_table_end(_um_lines, "## 章节大纲")
                if _pos_ch > 0:
                    for _ch in reversed(_um_chapters):
                        _row = f"| {_ch.get('order','')} | {_ch.get('title','')} | {_ch.get('status','')} | {_ch.get('words','')} | {_ch.get('note','')} |"
                        _um_lines.insert(_pos_ch + 1, _row)
                    _inserted.append(f"{len(_um_chapters)} chapters")

            # 论点
            _um_args = fn_args.get("arguments", [])
            if _um_args:
                _pos_ar = _um_find_table_end(_um_lines, "## 核心论点")
                if _pos_ar > 0:
                    for _ag in reversed(_um_args):
                        _row = f"| {_ag.get('claim','')} | {_ag.get('evidence','')} | {_ag.get('source','')} | {_ag.get('strength','')} |"
                        _um_lines.insert(_pos_ar + 1, _row)
                    _inserted.append(f"{len(_um_args)} arguments")

            # 场景
            _um_scenes = fn_args.get("scenes", [])
            if _um_scenes:
                _pos_sc = _um_find_table_end(_um_lines, "## 场景列表")
                if _pos_sc > 0:
                    for _sc in reversed(_um_scenes):
                        _sc_icon = {"done": "✅", "draft": "✏️", "review": "🔍"}.get(_sc.get("status",""), "⏳")
                        _row = (f"| {_sc.get('scene_id','-')} | {_um_fp} | "
                                f"{_sc.get('title','-')} | {_sc.get('location','-')} | "
                                f"{_sc.get('characters','-')} | {_sc_icon}{_sc.get('status','-')} | "
                                f"{_sc.get('words','-')} | {_sc.get('note','')} |")
                        _um_lines.insert(_pos_sc + 1, _row)
                    _inserted.append(f"{len(_um_scenes)} scenes")

            # 角色
            _um_chars = fn_args.get("characters", [])
            if _um_chars:
                _pos_chr = _um_find_table_end(_um_lines, "## 角色表")
                if _pos_chr > 0:
                    for _ch in reversed(_um_chars):
                        _chr_name = _ch.get("name","")
                        _um_lines = [l for l in _um_lines
                                     if not (l.startswith("|") and
                                             [c.strip() for c in l.split("|")][1:2] == [_chr_name])]
                        _pos_chr = _um_find_table_end(_um_lines, "## 角色表")
                        _row = (f"| {_chr_name} | {_ch.get('role','-')} | "
                                f"{_ch.get('arc','-')} | {_ch.get('first_appearance',_um_fp)} | "
                                f"{_ch.get('status','active')} |")
                        _um_lines.insert(_pos_chr + 1, _row)
                    _inserted.append(f"{len(_um_chars)} characters")

            # 变更日志
            _um_clog = fn_args.get("changelog", "")
            if _um_clog:
                import datetime as _clog_dt
                _now = _clog_dt.datetime.now().strftime("%m-%d %H:%M")
                _pos_cl = _um_find_table_end(_um_lines, "## 变更日志")
                if _pos_cl > 0:
                    _row = f"| {_now} | update | {_um_fp} | {_um_clog} |"
                    _um_lines.insert(_pos_cl + 1, _row)
                    _inserted.append("1 changelog")

            # 模板资产
            _um_tmpls = fn_args.get("templates", [])
            if _um_tmpls:
                # 追加到文件 section 末尾（已在上面 _sec_lines 处理）
                pass

            # 写回
            _final_content = "\n".join(_um_lines)
            with pm_state.project_map_lock:
                with open(_um_map, "w", encoding="utf-8") as _umw:
                    _umw.write(_final_content)

            _ins_str = ", ".join(_inserted) if _inserted else "section updated"
            result = f"OK: updated PROJECT_MAP for {_um_fp} ({_ins_str})"
            log.info(f"  [update_map] {_um_fp}: {_ins_str}")
    except Exception as _ume:
        result = f"ERROR: update_map failed: {_ume}"
        log.warning(f"  [update_map] failed: {_ume}")
    return result
