"""agent_stream 工具批次之后的守卫与提醒注入段 (拆分大文件, 从主循环原样提取)。"""
import json
from lib import project_root as _pr
from lib.agent_helpers import _auto_map_build_skeleton
from lib.config import WORKSPACE
from lib.config import log
from lib.agent.tool_names import _WRITE_TOOL_NAMES_STR
from lib.config import CFG
_AGT = CFG.get("agent", {})


def _after_tool_batch(_ctx, _guard_stats, _iteration, _iter_write_success, _misc_state, _pm_state, _test_state, _webtest_state, _batch_state, tool_call_list, tool_results):
    """一批工具调用执行完之后: 写入追踪、PROJECT_MAP/测试/收尾验证提醒、批量写入/只读/规划闸。
    原 agent_stream 主循环内联段 (无 yield/await/段外跳转), 状态经各 state 对象原地改写。"""
    # ── [PROJECT_MAP + TEST] 追踪写入文件 ──
    _SRC_EXTS = {".py", ".js", ".ts", ".sh", ".bash", ".go", ".rs", ".java"}
    _SKIP_NAMES = {"__init__", "config", "settings", "constants", "conftest",
                   "setup", "requirements", ".gitkeep", "PROJECT_MAP"}
    _TEST_PATTERNS = ("test_", "_test.", ".test.", ".spec.", "_spec.")
    for _tc in tool_call_list:
        if _tc["function"]["name"] == "write_file":
            try:
                import os as _os
                _args = json.loads(_tc["function"].get("arguments") or "{}")
                _fp = _args.get("filepath", "").replace("\\", "/")
                _fname = _fp.split("/")[-1]
                _ext = "." + _fname.rsplit(".", 1)[-1] if "." in _fname else ""
                _is_test = any(p in _fname for p in _TEST_PATTERNS)
                _is_skip = any(s in _fname for s in _SKIP_NAMES)
                if _pr.is_oneoff(_fname):        # 一次性脚本不算项目文件
                    _is_skip = True

                if _is_test:
                    _test_state.test_files_written.add(_fp)
                    _test_state.iter_wrote_test = True
                elif _ext in _SRC_EXTS and not _is_skip:
                    _test_state.src_files_written.append(_fp)
                    _test_state.iter_wrote_src = True
                    # 推断项目根目录: WORKSPACE 下第一级子目录; 单文件脚本不算项目 (lib/project_root.py)
                    _root = _pr.infer_project_root(str(WORKSPACE), _fp)
                    if _root:
                        _pm_state.project_root = _root
                    # py 计数 (PROJECT_MAP 阈值用)
                    if _ext == ".py":
                        _pm_state.py_files_written += 1
                        _pm_state.files_since_map_update += 1

                if "PROJECT_MAP" in _fp:
                    _pm_state.project_map_written = True
                    _pm_state.py_at_map_write = _pm_state.py_files_written
                if _fname.lower() == "readme.md":
                    _webtest_state.readme_written = True
                # 追踪输出产物文件
                _ARTIFACT_EXTS = {".docx", ".pptx", ".xlsx", ".pdf", ".csv", ".html", ".json", ".xml", ".svg"}
                if _ext.lower() in _ARTIFACT_EXTS:
                    _misc_state.output_artifacts.append(_fp)
            except Exception:
                pass

        # ── [SHELL-BYPASS] 检测 execute_shell 中的 cat > / echo > 文件写入 ──
        if _tc["function"]["name"] == "execute_shell":
            try:
                import re as _re_sh
                _sh_args = json.loads(_tc["function"].get("arguments") or "{}")
                _sh_cmd = _sh_args.get("command", "")
                # 检测 cat > filepath, echo > filepath, tee filepath 等模式
                _sh_writes = _re_sh.findall(
                    r'(?:cat|tee|echo|printf)\s*>+\s*([^\s<|&;]+\.(?:py|js|ts|go|sh|rs|java))',
                    _sh_cmd
                )
                for _sh_fp in _sh_writes:
                    _sh_fname = _sh_fp.split("/")[-1]
                    _sh_ext = "." + _sh_fname.rsplit(".", 1)[-1] if "." in _sh_fname else ""
                    _sh_is_test = any(p in _sh_fname for p in _TEST_PATTERNS)
                    _sh_is_skip = any(s in _sh_fname for s in _SKIP_NAMES)
                    if _sh_is_test:
                        _test_state.test_files_written.add(_sh_fp)
                        _test_state.iter_wrote_test = True
                        log.info(f"  [SHELL-TRACK] detected test write via shell: {_sh_fname}")
                    elif _sh_ext in _SRC_EXTS and not _sh_is_skip:
                        _test_state.src_files_written.append(_sh_fp)
                        _test_state.iter_wrote_src = True
                        if _sh_ext == ".py":
                            _pm_state.py_files_written += 1
                            _pm_state.files_since_map_update += 1
                        log.info(f"  [SHELL-TRACK] detected src write via shell: {_sh_fname}")
            except Exception:
                pass

    if (_pm_state.py_files_written >= 3 and not _pm_state.project_map_written and _pm_state.project_root):
        import os as _os
        _map_path = _pm_state.project_root + "/PROJECT_MAP.md"
        _map_exists = _os.path.exists(_map_path)
        if not _map_exists:
            # ── [AUTO-MAP] 自动创建 PROJECT_MAP.md，不依赖 LLM ──
            try:
                _auto_content = _auto_map_build_skeleton(_test_state.src_files_written, _test_state.test_files_written, _pm_state.project_root)
                with open(_map_path, "w", encoding="utf-8") as _amfp:
                    _amfp.write(_auto_content)
                _pm_state.project_map_written = True
                _pm_state.py_at_map_write = _pm_state.py_files_written
                # 通知 LLM
                _pm_hint = f"\n\n[SYSTEM-AUTO-MAP] 已自动创建 {_map_path} 骨架（含测试覆盖+测试策略）。请为已写入的每个源文件调用 update_map 工具填充路由/函数/参数/类型信息（先 read_file 再 update_map）。"
                if tool_results:
                    tool_results[-1]["content"] += _pm_hint
                    if _ctx.messages and _ctx.messages[-1].get("role") == "tool":
                        _ctx.messages[-1]["content"] += _pm_hint
                log.info(f"  [AUTO-MAP] created {_map_path} with {len(_test_state.src_files_written)} modules")
            except Exception as _ame:
                log.warning(f"  [AUTO-MAP] failed to create: {_ame}")
                # 降级: 仍然提醒 LLM 创建
                _pm_hint = (
                    f"\n\n[SYSTEM: 你已写入 {_pm_state.py_files_written} 个Python模块但尚未创建 PROJECT_MAP.md。"
                    f"\n下一步必须先创建 {_map_path}，包含: API合约表、函数索引、测试覆盖状态。]"
                )
                if tool_results:
                    tool_results[-1]["content"] += _pm_hint
                    if _ctx.messages and _ctx.messages[-1].get("role") == "tool":
                        _ctx.messages[-1]["content"] += _pm_hint

    # ── [AUTO-MAP] 每写源文件 → 自动追加测试覆盖行 + 注入提示让 LLM 调用 update_map ──
    if _pm_state.project_map_written and _pm_state.project_root and _test_state.iter_wrote_src:
        import os as _os4
        _map_path2 = _pm_state.project_root + "/PROJECT_MAP.md"
        if _os4.path.exists(_map_path2):
            _new_this_iter = [
                f for f in _test_state.src_files_written[-3:]
                if f not in _pm_state.map_reminded_files
            ]
            _um_files_to_register = []
            for _new_fn in _new_this_iter:
                _new_basename = _new_fn.split("/")[-1]
                _new_relpath = _new_fn.replace(_pm_state.project_root + "/", "")
                _new_modname = _new_basename.rsplit(".", 1)[0]
                _test_status = "有" if any(
                    _new_modname in tf for tf in _test_state.test_files_written
                ) else "缺"

                # 只自动追加测试覆盖行（简单确定性操作）
                try:
                    _map_content = open(_map_path2, "r", encoding="utf-8").read()
                    if _new_relpath not in _map_content and _new_basename not in _map_content:
                        if "## 测试覆盖" in _map_content:
                            _map_lines2 = _map_content.split("\n")
                            _in_test_table = False
                            _insert_pos2 = -1
                            for _li2, _ll2 in enumerate(_map_lines2):
                                if "## 测试覆盖" in _ll2:
                                    _in_test_table = True
                                elif _in_test_table and _ll2.startswith("|"):
                                    _insert_pos2 = _li2 + 1
                                elif _in_test_table and not _ll2.startswith("|") and _ll2.strip():
                                    _in_test_table = False
                            if _insert_pos2 > 0:
                                _map_lines2.insert(_insert_pos2, f"| {_new_basename} | tests/test_{_new_basename} | {_test_status} |")
                                with open(_map_path2, "w", encoding="utf-8") as _mwf:
                                    _mwf.write("\n".join(_map_lines2))
                        _um_files_to_register.append(_new_relpath)
                        log.info(f"  [AUTO-MAP] appended test coverage for {_new_basename}, pending update_map")
                except Exception as _mae:
                    log.warning(f"  [AUTO-MAP] test-coverage append failed for {_new_basename}: {_mae}")

                _pm_state.map_reminded_files.add(_new_fn)

            # 注入提示让 LLM 调用 update_map 填充路由/函数/参数信息
            if _um_files_to_register:
                _um_file_list = ", ".join(_um_files_to_register)
                if _pm_state.files_since_map_update >= 2:
                    # 强制模式: 必须先 update_map
                    _um_hint = (
                        f"\n\n[SYSTEM-BLOCK-MAP] ⚠️ 已写入 {_pm_state.files_since_map_update} 个源文件但 PROJECT_MAP.md 仍为空，下次 write_file 将被拦截！"
                        f"立即对每个已写文件执行: read_file → update_map。"
                        f"update_map 质量标准(参考级): "
                        f"file_description(文件一句话用途) + "
                        f"functions(每个函数: line_no行号/name/params含类型/returns/note用途, 不漏任何函数) + "
                        f"classes(line_no/name/bases/methods逗号分隔) + "
                        f"constants(所有模块级常量逗号分隔) + "
                        f"routes(method/path/handler/params/note) + "
                        f"deps(imports/db_tables/ext_apis/used_by) + "
                        f"deps_graph(ASCII依赖图) + core_flow(ASCII流程图) + "
                        f"config + progress + tests。"
                        f"剧本/小说加: scenes + characters。"
                        f"待注册: {_um_file_list}"
                    )
                else:
                    _um_hint = (
                        f"\n\n[SYSTEM-MAP-UPDATE] 新源文件已写入: {_um_file_list}。"
                        f"请 read_file 后调用 update_map，填写参考级详情: "
                        f"file_description + functions(每个函数: line_no/name/params含类型/returns/note, 不可漏) + "
                        f"classes(line_no/name/bases/methods) + constants(所有常量) + "
                        f"deps(imports/used_by) + deps_graph(ASCII依赖图) + progress。"
                    )
                if tool_results:
                    tool_results[-1]["content"] += _um_hint
                    if _ctx.messages and _ctx.messages[-1].get("role") == "tool":
                        _ctx.messages[-1]["content"] += _um_hint

    # ── [TEST] 强制注入: 本轮写了源文件但没写测试 → 独立 user 消息 ──
    if _test_state.iter_wrote_src and not _test_state.iter_wrote_test:
        import os as _os3
        _all_untested = []
        # 构建已测试模块集合
        _tested_mods2 = set()
        for _tf2 in _test_state.test_files_written:
            _tfbn2 = _tf2.split("/")[-1].rsplit(".", 1)[0]
            if _tfbn2.startswith("test_"):
                _tested_mods2.add(_tfbn2[5:])
            try:
                import re as _re2
                _tfc2 = open(_tf2).read(5000)
                for _m2 in _re2.findall(r'def test_(\w+)', _tfc2):
                    _tested_mods2.add(_m2)
                for _m2 in _re2.findall(r'from\s+\S+\.(\w+)\s+import', _tfc2):
                    _tested_mods2.add(_m2)
            except Exception:
                pass
        if _pm_state.project_root:
            _tdir2 = _pm_state.project_root + "/tests"
            if _os3.path.isdir(_tdir2):
                for _tdf2 in _os3.listdir(_tdir2):
                    if _tdf2.startswith("test_") and _tdf2.endswith((".py",".js",".ts",".sh",".go")):
                        _tested_mods2.add(_tdf2.split("test_",1)[1].rsplit(".",1)[0])

        for _sf in _test_state.src_files_written:
            _sfname = _sf.split("/")[-1].rsplit(".", 1)[0]
            _sfext  = "." + _sf.rsplit(".", 1)[-1] if "." in _sf else ".py"
            _sfdir  = _sf.rsplit("/", 1)[0]
            # [FIX 2026-08-31] 三个缺陷:
            #   1) 只认 test_ 前缀 / .test. 中缀, 不认 Go 原生的 {name}_test.go 后缀
            #      → Go 项目写了测试也永远判定"无测试", 每轮注入一条假警告带偏 agent
            #   2) 测试文件自身被当成"待测源文件", 要求给测试写测试
            #   3) probe/tmp/scratch 这类一次性调研脚本被计入
            if (_sfname.endswith("_test") or _sfname.startswith("test_")
                    or ".test" in _sfname or ".spec" in _sfname
                    or _sfname.endswith("Test")):
                continue                       # 本身就是测试, 跳过
            if _sfname.split(".")[0].lower().startswith(("probe", "tmp", "scratch", "_")):
                continue                       # 一次性调研脚本, 不要求测试
            _has_test = (
                _sfname in _tested_mods2
                or _os3.path.exists(f"{_sfdir}/{_sfname}_test{_sfext}")      # Go / Python 后缀式
                or _os3.path.exists(f"{_sfdir}/test_{_sfname}{_sfext}")      # Python / shell 前缀式
                or _os3.path.exists(f"{_sfdir}/{_sfname}.test{_sfext}")      # JS
                or _os3.path.exists(f"{_sfdir}/{_sfname}.spec{_sfext}")      # TS
                or _os3.path.exists(f"{_sfdir}/{_sfname.capitalize()}Test.java")  # Java
                or _os3.path.exists(f"{_pm_state.project_root}/tests/test_{_sfname}{_sfext}")
                or _os3.path.exists(f"{_pm_state.project_root}/tests/{_sfname}_test{_sfext}")
            )
            if not _has_test:
                _all_untested.append((_sf, _sfext))
        # [FIX 2026-10-16] 同一批没测试的文件每写一个新文件就整单重报一次, 纯噪音;
        # 每个文件只提醒一次 (模型选择不测时尊重它, 收尾还有端到端验证兜底)
        _all_untested = [u for u in _all_untested if u[0] not in _test_state.test_reminded]
        _test_state.test_reminded.update(u[0] for u in _all_untested)
        if _all_untested:
            _ext_guide = {
                ".py":  ("tests/test_{name}.py",
                         "import pytest\ndef test_happy(): ...\ndef test_error(): ..."),
                ".js":  ("{name}.test.js",
                         "const assert=require('assert');\nassert.strictEqual(func(), expected);"),
                ".ts":  ("{name}.spec.ts",
                         "describe('{name}',()=>{{ it('works',()=>{{ expect(...).toBe(...); }}); }});"),
                ".sh":  ("tests/test_{name}.sh",
                         "#!/usr/bin/env bash\nset -euo pipefail\nassert_eq(){{[ \"$1\"=\"$2\" ]||{{echo FAIL;exit 1;}}}}"),
                ".go":  ("{name}_test.go",
                         "func TestHappy(t *testing.T){{\n  got:=Func(input)\n  if got!=want{{t.Errorf(...)}}\n}}"),
            }
            # [FIX 2026-08-31] 原先模板取 _all_untested[-1] 的扩展名, 文件名取 [0] 的名字,
            # 两个不同文件拼一起 → 出现 "tests/test_trigram.sh" (shell 模板 + Go 文件名)。
            # 现统一取同一项。
            _recent_sf, _recent_ext = _all_untested[0]
            _tpl_name, _tpl_body = _ext_guide.get(_recent_ext, ("tests/test_{name}.py","import pytest"))
            _untested_names = [sf.split("/")[-1] for sf,_ in _all_untested[:5]]
            _untested_list = "\n".join(f"  - {n}" for n in _untested_names)
            _test_dirs = list({sf.rsplit("/",1)[0] for sf,_ in _all_untested[:3]})
            # 模板不含 "/" 说明该语言测试与源码同目录 (Go/JS/TS/Java), 不该建 tests/ 根目录
            if "/" in _tpl_name:
                _tests_root = (_pm_state.project_root + "/tests") if _pm_state.project_root else _test_dirs[0]
            else:
                _tests_root = _recent_sf.rsplit("/", 1)[0]
            _guard_stats.hit("SYSTEM-TEST")
            _test_inject = (
                f"[SYSTEM-TEST] ⚠ 已写 {len(_test_state.src_files_written)} 个源文件，但 {len(_all_untested)} 个没有测试:\n"
                f"{_untested_list}\n\n"
                f"下一步写测试:\n"
                f"  mkdir -p {_tests_root}\n"
                f"  文件: {_tpl_name.replace('{name}', _recent_sf.split('/')[-1].rsplit('.',1)[0])}\n"
                f"  模板:\n    {_tpl_body}\n\n"
                f"写完测试后必须立即执行验证（不能先写下一个文件）。\n"
                f"所有源文件写完后，最终做端到端验证（Web项目curl路由/文档生成验证产物/数据处理验证输出）。"
            )
            # 追加到最后一个 tool result（agent 紧接着就能看到）
            if tool_results:
                tool_results[-1]["content"] += "\n\n" + _test_inject
                _last = _ctx.messages[-1] if _ctx.messages else None
                # tool_results 里的 dict 往往就是 messages 末尾那个对象, 别追加两遍
                if _last is not None and _last is not tool_results[-1] and _last.get("role") == "tool":
                    _last["content"] += "\n\n" + _test_inject
            else:   # 没有 tool result 可挂时才单独发一条 (原先两处都发, 同一提醒占双份上下文)
                _ctx.messages.append({"role": "user", "content": _test_inject})
                _ctx.new_msgs.append({"role": "user", "content": _test_inject})
            log.info(f"  [TEST] injected: {_untested_names[:3]}")

    # ── [RUN-TEST] 本轮写了测试/验证文件 → 立即执行 ──
    if _test_state.iter_wrote_test and _pm_state.project_root:
        _new_tests = [
            tf for tf in _test_state.test_files_written
            if tf not in _test_state.tests_run_for
            and any(tf in str(tr.get("content","")) for tr in tool_results)
        ]
        if not _new_tests:
            _new_tests = [tf for tf in sorted(_test_state.test_files_written) if tf not in _test_state.tests_run_for][-1:]
        if _new_tests:
            _test_file = _new_tests[-1]
            _test_rel = _test_file.replace(_pm_state.project_root + "/", "")
            _test_ext = "." + _test_file.rsplit(".", 1)[-1] if "." in _test_file else ".py"
            _test_state.tests_run_for.add(_test_file)

            # 根据文件扩展名生成对应的执行命令
            _run_cmds = {
                ".py":   f"cd {_pm_state.project_root} && python3 -m pytest {_test_rel} -v --tb=short 2>&1 | head -80",
                ".js":   f"cd {_pm_state.project_root} && node {_test_rel} 2>&1 | head -80 || npx jest {_test_rel} --no-coverage 2>&1 | head -80",
                ".ts":   f"cd {_pm_state.project_root} && npx jest {_test_rel} --no-coverage 2>&1 | head -80 || npx ts-node {_test_rel} 2>&1 | head -80",
                ".go":   f"cd {_pm_state.project_root}/{'/'.join(_test_rel.split('/')[:-1]) or '.'} && go test -v -run . 2>&1 | head -80",
                ".sh":   f"cd {_pm_state.project_root} && bash {_test_rel} 2>&1 | head -80",
                ".bash": f"cd {_pm_state.project_root} && bash {_test_rel} 2>&1 | head -80",
                ".rs":   f"cd {_pm_state.project_root} && cargo test 2>&1 | head -80",
                ".java": f"cd {_pm_state.project_root} && javac {_test_rel} && java -cp . {_test_rel.replace('.java','')} 2>&1 | head -80",
            }
            _run_cmd = _run_cmds.get(_test_ext, f"cd {_pm_state.project_root} && python3 -m pytest {_test_rel} -v --tb=short 2>&1 | head -80")

            _run_inject = (
                f"[SYSTEM-RUN-TEST] ⚠ 刚写完测试 {_test_rel}，必须立即执行验证:\n"
                f"  {_run_cmd}\n\n"
                f"执行后根据结果:\n"
                f"  - 全部 PASSED → 继续下一步\n"
                f"  - 有 FAILED/ERROR → 分析原因，修复代码或测试，重跑直到通过\n"
                f"⚠ 禁止跳过测试执行直接写下一个文件。"
            )
            if tool_results:
                tool_results[-1]["content"] += "\n\n" + _run_inject
                if _ctx.messages and _ctx.messages[-1].get("role") == "tool":
                    _ctx.messages[-1]["content"] += "\n\n" + _run_inject
            log.info(f"  [RUN-TEST] injected run for {_test_rel}")

    # ── [AUTO-MAP-COVERAGE] 写完测试后自动更新 PROJECT_MAP 测试覆盖状态 ──
    if _test_state.iter_wrote_test and _pm_state.project_map_written and _pm_state.project_root:
        _cov_map_path = _pm_state.project_root + "/PROJECT_MAP.md"
        try:
            import os as _osc, re as _rec
            if _osc.path.exists(_cov_map_path):
                _cov_content = open(_cov_map_path, "r", encoding="utf-8").read()
                if "## 测试覆盖" in _cov_content and "| 缺 |" in _cov_content:
                    # 重新扫描所有测试文件确定覆盖的模块
                    _covered = set()
                    for _ctf in _test_state.test_files_written:
                        _ctbn = _ctf.split("/")[-1].rsplit(".", 1)[0]
                        if _ctbn.startswith("test_"):
                            _covered.add(_ctbn[5:])
                        try:
                            _ctc = open(_ctf).read(5000)
                            for _cm in _rec.findall(r'def test_(\w+)', _ctc):
                                _covered.add(_cm)
                            for _cm in _rec.findall(r'from\s+\S+\.(\w+)\s+import', _ctc):
                                _covered.add(_cm)
                        except Exception:
                            pass
                    # 也扫描磁盘 tests/ 目录
                    _ctd = _pm_state.project_root + "/tests"
                    if _osc.path.isdir(_ctd):
                        for _ctdf in _osc.listdir(_ctd):
                            if _ctdf.startswith("test_"):
                                _covered.add(_ctdf.split("test_", 1)[1].rsplit(".", 1)[0])

                    # 替换: 对每个已覆盖的模块，将 "| 缺 |" 改为 "| 有 |"
                    _updated = False
                    for _cmod in _covered:
                        _old_pat = f"| {_cmod}.py |"
                        if _old_pat in _cov_content:
                            # 只替换该行中的 "| 缺 |" 为 "| 有 |"
                            _lines_c = _cov_content.split("\n")
                            for _ci, _cl in enumerate(_lines_c):
                                if _old_pat in _cl and "| 缺 |" in _cl:
                                    _lines_c[_ci] = _cl.replace("| 缺 |", "| 有 |")
                                    _updated = True
                            _cov_content = "\n".join(_lines_c)

                    if _updated:
                        with open(_cov_map_path, "w", encoding="utf-8") as _cwf:
                            _cwf.write(_cov_content)
                        log.info(f"  [AUTO-MAP-COVERAGE] updated test coverage for: {_covered & set(f.split('/')[-1].rsplit('.',1)[0] for f in _test_state.src_files_written)}")
        except Exception as _cove:
            log.warning(f"  [AUTO-MAP-COVERAGE] failed: {_cove}")

    # ── [FINAL-VALIDATE] 项目收尾: 检测项目类型 → 生成对应最终验证 ──
    # 触发条件: README已写 OR (>=8个源文件且70%有测试 且迭代>=30 且有入口文件)
    _has_entry = _pm_state.project_root and any(
        __import__('os').path.exists(f"{_pm_state.project_root}/{e}")
        for e in ("run.py", "main.py", "app.py", "manage.py", "server.py", "index.js", "main.go")
    )
    _should_final_validate = (
        _webtest_state.readme_written
        or (len(_test_state.src_files_written) >= 8
            and _iteration >= 30
            and len(_test_state.test_files_written) >= len(_test_state.src_files_written) * 0.7
            and _has_entry)
    )
    if _should_final_validate and not _webtest_state.web_test_injected and _pm_state.project_root:
        import os as _os5
        # 检查是否还有大量未测试文件
        _all_ut = []
        for _sf2 in _test_state.src_files_written:
            _sn2 = _sf2.split("/")[-1].rsplit(".", 1)[0]
            if not any(_sn2 in tf or f"test_{_sn2}" in tf for tf in _test_state.test_files_written):
                _all_ut.append(_sf2)
        if len(_all_ut) <= 2:
            _webtest_state.web_test_injected = True

            # ── 检测项目类型 ──
            _all_files_str = " ".join(_test_state.src_files_written).lower()
            _all_exts = set(f.rsplit(".",1)[-1].lower() for f in _test_state.src_files_written if "." in f)
            _artifact_exts = set(f.rsplit(".",1)[-1].lower() for f in _misc_state.output_artifacts if "." in f)
            # 读取入口文件的前几行来检测框架
            _framework_hints = ""
            for _ef in ["run.py", "main.py", "app.py", "manage.py", "server.py", "index.js", "app.js", "main.go", "main.rs"]:
                _ef_path = _os5.path.join(_pm_state.project_root, _ef)
                if _os5.path.exists(_ef_path):
                    try:
                        _framework_hints += open(_ef_path).read(2000)
                    except Exception:
                        pass
            # 也扫描 __init__.py
            for _root_d, _dirs, _fnames in _os5.walk(_pm_state.project_root):
                for _fn in _fnames:
                    if _fn in ("__init__.py", "app.py") and _root_d.count("/") - _pm_state.project_root.count("/") <= 2:
                        try:
                            _framework_hints += open(_os5.path.join(_root_d, _fn)).read(1500)
                        except Exception:
                            pass
                break  # 只扫1层

            _is_web = any(kw in _framework_hints.lower() for kw in
                ["flask", "fastapi", "django", "express", "koa", "gin", "actix", "rocket",
                 "http.server", "uvicorn", "gunicorn", "app.run", "createserver", "listen("])
            _is_docgen = bool(_artifact_exts & {"docx", "pptx", "xlsx", "pdf"}) or \
                any(kw in _all_files_str for kw in ["docx", "pptx", "xlsx", "openpyxl", "python-docx", "python-pptx"])
            _is_data = bool(_artifact_exts & {"csv", "json", "xml", "parquet"}) or \
                any(kw in _all_files_str for kw in ["pandas", "csv", "etl", "pipeline", "transform", "scraper", "crawler"])

            # ── 生成对应验证步骤 ──
            _validate_steps = []

            # 通用: 先跑全量测试
            if "py" in _all_exts:
                _validate_steps.append(f"cd {_pm_state.project_root} && python3 -m pytest tests/ -v --tb=short 2>&1 | tail -40")
            elif "js" in _all_exts or "ts" in _all_exts:
                _validate_steps.append(f"cd {_pm_state.project_root} && npx jest --no-coverage 2>&1 | tail -40 || npm test 2>&1 | tail -40")
            elif "go" in _all_exts:
                _validate_steps.append(f"cd {_pm_state.project_root} && go test ./... -v 2>&1 | tail -40")
            elif "sh" in _all_exts or "bash" in _all_exts:
                _validate_steps.append(f"cd {_pm_state.project_root} && bash tests/*.sh 2>&1 | tail -40")

            if _is_web:
                # Web 项目: 启动服务 + curl
                _entry = "run.py"
                for _candidate in ["run.py", "main.py", "app.py", "manage.py", "server.py"]:
                    if _os5.path.exists(_os5.path.join(_pm_state.project_root, _candidate)):
                        _entry = _candidate
                        break
                _validate_steps.extend([
                    f"# 启动服务 (后台, 15秒超时)",
                    f"cd {_pm_state.project_root} && timeout 15 python3 {_entry} &",
                    f"sleep 3",
                    f"# curl 测试每个关键路由 (根据 PROJECT_MAP 中的 API 合约):",
                    f"curl -sS http://127.0.0.1:5000/ -o /dev/null -w '%{{http_code}}\\n'",
                    f"curl -sS http://127.0.0.1:5000/login -o /dev/null -w '%{{http_code}}\\n'",
                    f"# 补充更多路由...",
                    f"kill %1 2>/dev/null || true",
                ])

            if _is_docgen:
                # 文档生成: 验证输出文件存在且可读
                _validate_steps.extend([
                    f"# 文档生成验证:",
                    f"cd {_pm_state.project_root}",
                    f"# 运行生成脚本 (如果有入口):",
                    f"# python3 main.py 或 python3 generate.py",
                    f"# 验证输出文件:",
                ])
                if _artifact_exts & {"docx"}:
                    _validate_steps.append(
                        f"python3 -c \"from docx import Document; d=Document('输出文件.docx'); "
                        f"print(f'段落数: {{len(d.paragraphs)}}, 表格数: {{len(d.tables)}}'); "
                        f"assert len(d.paragraphs)>0, '文档为空'\""
                    )
                if _artifact_exts & {"pptx"}:
                    _validate_steps.append(
                        f"python3 -c \"from pptx import Presentation; p=Presentation('输出文件.pptx'); "
                        f"print(f'幻灯片数: {{len(p.slides)}}'); "
                        f"assert len(p.slides)>0, '演示文稿为空'\""
                    )
                if _artifact_exts & {"xlsx"}:
                    _validate_steps.append(
                        f"python3 -c \"import openpyxl; wb=openpyxl.load_workbook('输出文件.xlsx'); "
                        f"print(f'工作表: {{wb.sheetnames}}, 行数: {{wb.active.max_row}}'); "
                        f"assert wb.active.max_row>0, '表格为空'\""
                    )
                if _artifact_exts & {"pdf"}:
                    _validate_steps.append(
                        f"python3 -c \"import subprocess; r=subprocess.run(['pdfinfo','输出文件.pdf'],capture_output=True,text=True); "
                        f"print(r.stdout); assert 'Pages' in r.stdout, 'PDF无效'\" "
                        f"|| python3 -c \"import fitz; d=fitz.open('输出文件.pdf'); print(f'页数: {{d.page_count}}'); assert d.page_count>0\""
                    )
                _validate_steps.append(
                    f"# ⚠ 将上面的 '输出文件.xxx' 替换为实际文件路径"
                )

            if _is_data:
                # 数据处理: 验证输出数据
                _validate_steps.extend([
                    f"# 数据验证:",
                    f"cd {_pm_state.project_root}",
                    f"# 运行数据处理脚本:",
                    f"# python3 main.py",
                ])
                if _artifact_exts & {"csv"}:
                    _validate_steps.append(
                        f"python3 -c \"import csv; r=list(csv.reader(open('输出.csv'))); "
                        f"print(f'行数: {{len(r)}}, 列数: {{len(r[0]) if r else 0}}'); assert len(r)>1, '数据为空'\""
                    )
                if _artifact_exts & {"json"}:
                    _validate_steps.append(
                        f"python3 -c \"import json; d=json.load(open('输出.json')); "
                        f"print(f'类型: {{type(d).__name__}}, 条目: {{len(d) if isinstance(d,(list,dict)) else 1}}'); "
                        f"assert d, '数据为空'\""
                    )
                _validate_steps.append(
                    f"# ⚠ 将上面的 '输出.xxx' 替换为实际文件路径"
                )

            if not _is_web and not _is_docgen and not _is_data:
                # 通用 CLI/库项目: 运行入口验证
                _validate_steps.extend([
                    f"# 运行入口验证:",
                    f"cd {_pm_state.project_root} && python3 run.py --help 2>&1 | head -20 || python3 main.py --help 2>&1 | head -20 || true",
                ])

            _steps_text = "\n  ".join(_validate_steps)
            _project_type = ("Web应用" if _is_web else
                            "文档生成" if _is_docgen else
                            "数据处理" if _is_data else
                            "通用项目")
            _web_inject = (
                f"[SYSTEM-FINAL-VALIDATE] ✅ 代码和测试基本完成。检测到项目类型: {_project_type}\n"
                f"最后一步: 端到端验证。必须按顺序执行:\n"
                f"  {_steps_text}\n\n"
                f"执行后根据结果:\n"
                f"  - 全部通过 → 输出最终总结\n"
                f"  - 发现问题 → 修复 → 重新验证，直到通过\n\n"
                f"⚠ 禁止跳过最终验证直接输出总结。"
            )
            if tool_results:
                tool_results[-1]["content"] += "\n\n" + _web_inject
                if _ctx.messages and _ctx.messages[-1].get("role") == "tool":
                    _ctx.messages[-1]["content"] += "\n\n" + _web_inject
            _ctx.messages.append({"role": "user", "content": _web_inject})
            _ctx.new_msgs.append({"role": "user", "content": _web_inject})
            log.info(f"  [FINAL-VALIDATE] injected {_project_type} validation")

    # ── [BATCH] 检测连续单文件写入，注入批量写入提醒 ──
    _write_count_this_iter = sum(
        1 for _tc2 in tool_call_list
        if _tc2["function"]["name"] == "write_file"
    )
    if _write_count_this_iter == 1 and len(tool_call_list) == 1:
        _batch_state.consecutive_single_writes += 1
    else:
        _batch_state.consecutive_single_writes = 0

    if _batch_state.consecutive_single_writes >= 3 and not _batch_state.batch_reminder_sent:
        _batch_state.batch_reminder_sent = True
        _guard_stats.hit("SYSTEM-BATCH")
        _batch_inject = (
            "[SYSTEM-BATCH] ⚠ 检测到连续 3 轮每轮只写 1 个文件，效率极低。\n"
            "下一轮必须批量写入所有待写文件（一轮调用多个 write_file）。\n"
            "独立的文件写入必须在同一轮批量完成，不要一个一个写。\n"
            "示例: 一轮内同时 write_file(a.py) + write_file(b.py) + write_file(c.py)"
        )
        if tool_results:
            tool_results[-1]["content"] += "\n\n" + _batch_inject
            if _ctx.messages and _ctx.messages[-1].get("role") == "tool":
                _ctx.messages[-1]["content"] += "\n\n" + _batch_inject
        log.info(f"  [BATCH] injected batch write reminder after {_batch_state.consecutive_single_writes} consecutive single writes")
        # 重置计数但保留标记，下次连续 5 轮再提醒
        _batch_state.consecutive_single_writes = 0
        _batch_state.batch_reminder_sent = False  # 允许重复提醒

    # ── [READONLY] 连续只读轮检测: N 轮只调只读工具零写入 → 注入推进提醒 (不熔断) ──
    # 信号源改为按执行结果算的 _iter_write_success (循环体里已算好, 与
    # [EXEC-GATE] 共用同一个信号, 见 L1778-1779), 只换信号来源, 阈值/提醒
    # 次数等既有行为不变。
    _readonly_threshold = int(_AGT.get("readonly_streak_threshold", 10))
    _readonly_reminder_max = int(_AGT.get("readonly_reminder_max", 3))
    if _batch_state.readonly_tick(_iter_write_success or _batch_state.action_tool_calls > 0, _readonly_threshold, _readonly_reminder_max):
        _guard_stats.hit("SYSTEM-READONLY")
        _readonly_inject = (
            f"[SYSTEM-READONLY] 你已经连续 {_readonly_threshold} 轮只在查看和验证，没有做出任何实际修改。\n"
            f"如果资料已经足够: 现在就用 {_WRITE_TOOL_NAMES_STR} 修改目标文件"
            "（注意改的是任务要求的那个目录/仓库里的文件，不是 /tmp 下的临时脚本）。\n"
            "如果确实还缺关键信息: 直接说明还缺什么，不要继续漫无目的地翻找。\n"
            "如果这个任务本来就只需要查阅和分析、不需要改任何代码"
            "（例如纯调研/架构梳理/答疑）: 那就直接给出结论，不要为了迎合本提醒而做多余的修改。"
        )
        _ctx.messages.append({"role": "user", "content": _readonly_inject})
        _ctx.new_msgs.append({"role": "user", "content": _readonly_inject})
        log.info(
            f"  [READONLY] injected readonly-streak reminder "
            f"#{_batch_state.readonly_reminder_count}/{_readonly_reminder_max} "
            f"(threshold={_readonly_threshold})"
        )

    # ── [EXEC-GATE] 累计本轮*成功*写类工具调用次数, 供收尾闸 (exec_gate_tick) 判断 ──
    if _iter_write_success:
        _batch_state.exec_gate_write_calls += 1

    # ── [PLAN-GATE] 轨迹触发 (修复轮 #2: 不判文本, 观察行为) ──
    # 本轮(有工具调用的这一轮, 见 plan_gate_tick docstring)已累计的"带工具调用的
    # 迭代轮数"达到阈值、且全程从未调过 task_create → 说明它就是在做一个需要多步
    # 的任务却没有拆解, 注入一次拆解提醒然后永久放行 (plan_gate_tick 内部保证
    # 最多返回 True 一次, 不重复提醒, 不死锁)。
    _plan_gate_task_created_now = any(
        _tc6["function"]["name"] == "task_create" for _tc6 in tool_call_list
    )
    if _batch_state.plan_gate_tick(
        _plan_gate_task_created_now, threshold=int(_AGT.get("plan_gate_tool_iters", 5))
    ):
        _guard_stats.hit("SYSTEM-PLAN-GATE")
        _plan_gate_inject = (
            f"[SYSTEM-PLAN-GATE] 你已经连续 {_batch_state.plan_gate_tool_call_iters} 轮调用工具还没完成这个任务。\n"
            "先用 task_create 把剩下的工作拆成 3-8 个具体步骤, 每步要有明确产出物或可验证的完成标志, "
            "单步一两次工具调用就能完成。拆完按步骤逐个推进, 每完成一步用 task_done 标记。"
        )
        _ctx.messages.append({"role": "user", "content": _plan_gate_inject})
        _ctx.new_msgs.append({"role": "user", "content": _plan_gate_inject})
        log.info(
            f"  \033[35m[PLAN-GATE]\033[0m [{_ctx.sid_tag}] 连续 "
            f"{_batch_state.plan_gate_tool_call_iters} 轮工具调用未见 task_create, "
            f"注入拆解提醒 (iter={_iteration})"
        )
