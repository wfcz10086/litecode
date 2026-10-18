"""lib/agent/subagent_evidence.py — 子代理交付必须带证据。

问题: 子代理 (以及采信它的主代理) 会把未经验证的结论当事实上报, 例如把表格合计 1964 报成 4163
并标"已验证"。这里不信任模型自述, 只从子代理自己的工具调用与结果里机器采集客观证据:
  - 测试/构建/运行类 execute_shell: 命令分类 + 退出码 (从结果前缀推断) + 通过/失败计数
    (pytest/unittest/jest/vitest/mocha/go test/cargo test/node --test 等; 解析不了保留尾部 20 行)
  - 写入/修改的文件 (写类工具 + shell 重定向/tee/sed -i), 写后是否被 read_file / shell 读回
  - windows_system inspect_file / list_dir 回读摘要
  - 工作树隔离结果 (提交 hash、合并状态, 由 subagent_worktree 经 note_worktree 送来)
  - operator (远程操作员) 的界面改动与确认: windows_desktop 文件类改动 (与 readback_gate.is_file_change 一致:
    保存/另存/粘贴/剪切/删除/重命名/拖拽) 及 windows_system upload, 其后是否有回读
    (inspect_file/list_dir/run_powershell/download); 其它界面动作之后是否有 screenshot/ocr 确认

判定 (verdict):
  - 写类角色 (与 subagent_worktree.is_write_role 一致) 且有代码改动: 至少一次测试/构建/运行类命令
    退出码 0 (且解析出的失败数为 0), 或工具写入的改动文件全部回读过, 才算 verified。
  - operator 属写类: 改过文件 → 最后一次文件改动之后必须有成功回读 (截图/OCR 不算, 数字以文件为准);
    没改文件但做过界面动作 → 最后一次界面动作之后有 screenshot/ocr 或回读确认才算 verified。
  - 其余 (只读角色, 或写类角色但没改动): 至少一次成功的取证类工具调用才算 verified;
    零取证 = 结论纯靠模型自述 = 未验证。
  - 写类子代理准备结束时未验证: 循环内注入一次 [SYSTEM-EVIDENCE] 补证提示 (每个子代理最多一次,
    且必须还剩迭代次数), 之后照常结束。

接入点 (都很薄):
  - subagent._run_subagent: start_collector / observe / nudge 三处调用
  - spawn_agent 两条派发路径: EvidencePlan.wrap 包住 (工作树 wrap 之外), 结束 decorate 结果
    —— 结果开头加 [已验证] / [未验证: 原因], 末尾附「证据」小节; 多个子代理时列出每个的判定。
  - 不经 spawn_agent 的 DAG 运行时 (dag 工具 / DAG 编辑器) 只采集+补证+记日志, 不改步骤输出文本,
    以免破坏 when 条件与 JSON 传值。
开关: config agent.subagent_evidence (默认 true), 关闭时以上全部不生效。
"""
from __future__ import annotations

import contextvars
import os
import re
import shlex
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

SECTION_MAX = 1500
NUDGE_PREFIX = "[SYSTEM-EVIDENCE]"
_WRITE_TOOLS = {"write_file", "patch_file", "apply_blocks", "edit_symbol", "create_file"}
_NON_EVIDENCE_TOOLS = {"update_global_context", "load_skill", "list_skills", "update_map",
                       "save_memory", "update_profile", "spawn_agent"}
_WIN_READBACK_ACTIONS = {"inspect_file", "list_dir"}
# operator 判定用 (与 readback_gate.READBACK_ACTIONS 一致)
_OP_READBACK = {"inspect_file", "list_dir", "run_powershell", "download"}
_OP_SYS_CONFIRM = {"processes", "system_info", "services"}
_OP_CONFIRM = {"screenshot", "ocr"}
_OP_NEUTRAL = {"windows", "clipboard", "wechat_status", "initialize"}   # 纯查看, 不改界面也不算确认
# operator 任务里点名的文件 (生产实测 2026-10-18: 操作员被桌面旧文件带偏, 回读了别的文件却判 verified)
_TARGET_RE = re.compile(r'([^\s\\/:*?"<>|，。、；：“”‘’（）()「」【】\[\]]{1,80}?\.'
                        r'(?:xlsx|xlsm|xls|csv|docx|doc|pptx|ppt|txt|md|pdf|json|wps|et|dps))(?![A-Za-z0-9])',
                        re.I)
_TARGET_MAX = 5


def _target_match(target: str, name: str) -> bool:
    """任务里抽出的名字可能带前置中文 ("在桌面新建op.txt"), 观察到的真实文件名是它的后缀即算同一个。"""
    name = name.strip().strip("'\"`").lower()
    return bool(name) and (name == target or (len(name) >= 5 and target.endswith(name)
                                               and not target[-len(name) - 1].isascii()))


def task_targets(task: str) -> List[str]:
    """任务文本里点名的文档文件名 (小写 basename, 去重保序, 最多 _TARGET_MAX 个)。"""
    out: List[str] = []
    for m in _TARGET_RE.finditer(task or ""):
        name = m.group(1).strip().strip("'\"`").lower()
        if name and not name.startswith(".") and name not in out:
            out.append(name)
    return out[:_TARGET_MAX]
_ABORT_PREFIXES = ("[子代理超时]", "[子代理已中断]", "[子代理异常]", "[子代理已取消",
                   "[子代理嵌套过深]", "[子代理错误栈]")
_RUN_KINDS = ("test", "build", "run")
_KIND_CN = {"test": "测试", "build": "构建", "run": "运行", "syntax": "语法检查"}

_SLOT: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "subagent_evidence_slot", default=None)


def enabled() -> bool:
    try:
        from ..config import CFG
        return bool((CFG.get("agent") or {}).get("subagent_evidence", True))
    except Exception:
        return True


def _is_write_role(agent_type: str) -> bool:
    from .subagent_operator import is_operator
    if is_operator(agent_type):      # 改远程文件/界面 → 写类; 但不建服务器工作树 (worktree.is_write_role 为 False)
        return True
    from .subagent_worktree import is_write_role
    return is_write_role(agent_type)


# ═══════════════════════════ 命令分类 ═══════════════════════════

_SEG_SPLIT = re.compile(r"&&|\|\||[;|\n]")
_PREFIX_WORDS = {"sudo", "time", "nice", "env", "nohup", "exec", "command", "stdbuf"}
_TEST_BINS = {"pytest", "py.test", "tox", "nox", "jest", "vitest", "mocha", "ctest", "phpunit",
              "rspec", "karma", "ava", "tap", "unittest"}
_BUILD_BINS = {"tsc", "gcc", "g++", "clang", "clang++", "javac", "rustc", "cmake", "ninja", "webpack",
               "vite", "esbuild", "rollup", "mypy", "eslint", "ruff", "flake8", "pylint"}
_RUN_BINS = {"node", "deno", "ruby", "perl", "php", "java", "rscript", "lua", "dotnet", "swift",
             "kotlin", "scala", "julia", "bash", "sh", "zsh", "ts-node", "tsx"}
_PKG_MGRS = {"npm", "pnpm", "yarn", "bun"}


def _words(seg: str) -> List[str]:
    try:
        ws = shlex.split(seg, posix=True)
    except Exception:
        ws = seg.split()
    out = []
    i = 0
    while i < len(ws):
        w = ws[i]
        if not out and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w) or w in _PREFIX_WORDS):
            i += 1
            continue
        if not out and w == "timeout":           # timeout [-s SIG] N cmd
            i += 1
            while i < len(ws) and (ws[i].startswith("-") or re.match(r"^\d+[smh]?$", ws[i])):
                i += 1
            continue
        out.append(w)
        i += 1
    return out


def _classify_segment(ws: List[str]) -> Optional[str]:
    if not ws:
        return None
    w0 = os.path.basename(ws[0]).lower()
    rest = [w.lower() for w in ws[1:]]
    args = " ".join(rest)
    if w0 in ("cd", "ls", "pwd", "echo", "cat", "head", "tail", "grep", "rg", "find", "wc", "true",
              "export", "source", ".", "mkdir", "rm", "cp", "mv", "touch", "chmod", "git", "pip",
              "pip3", "printf", "sleep", "which", "test", "["):
        return None
    if w0 in _TEST_BINS:
        return "test"
    if w0 == "npx" and rest:
        return _classify_segment(ws[1:]) or "run"
    if re.match(r"^python(\d(\.\d+)?)?$", w0) or w0 in ("py", "pypy", "pypy3", "uv", "poetry"):
        if w0 in ("uv", "poetry"):
            return _classify_segment(ws[2:]) if rest[:1] == ["run"] else None
        if "-m" in rest:
            mod = rest[rest.index("-m") + 1] if rest.index("-m") + 1 < len(rest) else ""
            if mod in ("pytest", "unittest", "nose", "nose2", "doctest", "tox"):
                return "test"
            if mod in ("py_compile", "compileall"):
                return "syntax"
            if mod in ("mypy", "build", "ruff", "flake8", "pylint"):
                return "build"
            if mod in ("pip", "venv", "http.server", "json.tool"):
                return None
            return "run"
        if "-c" in rest:
            return "run"
        script = next((w for w in rest if not w.startswith("-")), "")
        if not script:
            return None
        b = os.path.basename(script)
        if b.startswith("test_") or b.endswith("_test.py") or b in ("test.py", "tests.py", "runtests.py"):
            return "test"
        return "run"
    if w0 in _PKG_MGRS:
        sub = rest[0] if rest else ""
        if sub == "run" and len(rest) > 1:
            sub = rest[1]
        if sub in ("test", "t", "tests") or sub.startswith("test:") or sub == "jest" or sub == "vitest":
            return "test"
        if sub in ("build", "compile", "tsc", "lint", "typecheck", "check") or sub.startswith("build:"):
            return "build"
        if sub in ("start", "dev", "exec") or (rest and rest[0] == "run"):
            return "run"
        return None
    if w0 == "go":
        sub = rest[0] if rest else ""
        return {"test": "test", "build": "build", "vet": "build", "run": "run"}.get(sub)
    if w0 == "cargo":
        sub = rest[0] if rest else ""
        return {"test": "test", "nextest": "test", "build": "build", "check": "build",
                "clippy": "build", "run": "run"}.get(sub)
    if w0 in ("make", "gmake"):
        tg = [w for w in rest if not w.startswith("-") and "=" not in w]
        if any(t.startswith("test") or t in ("check", "tests") for t in tg):
            return "test"
        return "build"
    if w0 in ("mvn", "gradle", "./gradlew", "gradlew", "./mvnw"):
        return "test" if any(w in ("test", "verify", "check") for w in rest) else "build"
    if w0 == "dotnet":
        sub = rest[0] if rest else ""
        return {"test": "test", "build": "build", "run": "run"}.get(sub, "run")
    if w0 == "node" and ("--test" in rest):
        return "test"
    if w0 in _BUILD_BINS:
        return "build"
    if w0 in _RUN_BINS:
        if w0 in ("bash", "sh", "zsh") and (not rest or rest[0] in ("-c",)):
            return _classify_command(" ".join(ws[2:])) if len(ws) > 2 else None
        return "run"
    if ws[0].startswith(("./", "/")):
        return "run"
    return None


def _classify_command(cmd: str) -> Optional[str]:
    kinds = set()
    for seg in _SEG_SPLIT.split(cmd or ""):
        k = _classify_segment(_words(seg.strip()))
        if k:
            kinds.add(k)
    for k in ("test", "build", "run", "syntax"):
        if k in kinds:
            return k
    return None


def classify_command(cmd: str) -> Optional[str]:
    """test / build / run / syntax(py_compile 一类, 不算验证) / None(观察类命令)。"""
    return _classify_command(cmd)


# ═══════════════════════════ 退出码 / 计数解析 ═══════════════════════════

def infer_exit(result: str) -> Optional[int]:
    """从 execute_shell 结果推断退出码。执行器只在失败时加前缀, 成功无标记 → 0。None=未知 (转后台)。"""
    r = (result or "").lstrip()
    m = re.match(r"ERROR\[EXIT=(-?\d+)\]", r)
    if m:
        return int(m.group(1))
    if r.startswith("ERROR[TEST_FAIL]"):
        return 1
    if r.startswith(("ERROR", "TIMEOUT", "EXEC_ERROR")):
        return -1
    m = re.fullmatch(r"\(exit (-?\d+)\)", r.strip())
    if m:
        return int(m.group(1))
    if "[AUTO-BG]" in r:
        return None
    return 0


def _n(m, g=1) -> int:
    return int(m.group(g)) if m else 0


def parse_test_counts(output: str) -> Optional[Dict[str, object]]:
    """从测试输出解析 passed/failed/errors/skipped; 解析不了返回 None。"""
    out = output or ""
    # pytest: "=== 3 passed, 1 failed, 2 skipped in 0.12s ===" / "-q": "3 passed, 1 warning in 0.5s"
    pyl = [ln for ln in out.splitlines()
           if re.search(r"\d+ (passed|failed|errors?|skipped|xfailed|xpassed|deselected)\b", ln)
           and re.search(r"\bin [\d.]+s\b", ln)]
    if pyl:
        ln = pyl[-1]
        g = lambda k: _n(re.search(r"(\d+) " + k + r"\b", ln))
        return {"framework": "pytest", "passed": g("passed"), "failed": g("failed"),
                "errors": g("errors?"), "skipped": g("skipped")}
    # unittest: "Ran 5 tests in 0.001s" + "OK" / "FAILED (failures=1, errors=2)"
    m = re.search(r"Ran (\d+) tests? in [\d.]+s", out)
    if m:
        total = int(m.group(1))
        fm = re.search(r"FAILED \(([^)]*)\)", out)
        failed = _n(re.search(r"failures=(\d+)", fm.group(1))) if fm else 0
        errors = _n(re.search(r"errors=(\d+)", fm.group(1))) if fm else 0
        skipped = _n(re.search(r"skipped=(\d+)", out))
        return {"framework": "unittest", "passed": max(total - failed - errors - skipped, 0),
                "failed": failed, "errors": errors, "skipped": skipped}
    # jest: "Tests:       1 failed, 4 passed, 5 total"
    m = re.search(r"Tests:\s+([^\n]*\d+ total)", out)
    if m:
        ln = m.group(1)
        return {"framework": "jest", "passed": _n(re.search(r"(\d+) passed", ln)),
                "failed": _n(re.search(r"(\d+) failed", ln)), "errors": 0,
                "skipped": _n(re.search(r"(\d+) skipped", ln))}
    # vitest: "Tests  1 failed | 4 passed (5)"
    m = re.search(r"Tests\s+([^\n]*\(\d+\))", out)
    if m and re.search(r"\d+ (passed|failed)", m.group(1)):
        ln = m.group(1)
        return {"framework": "vitest", "passed": _n(re.search(r"(\d+) passed", ln)),
                "failed": _n(re.search(r"(\d+) failed", ln)), "errors": 0,
                "skipped": _n(re.search(r"(\d+) skipped", ln))}
    # cargo test: "test result: ok. 5 passed; 0 failed; 0 ignored" (多个 crate 求和)
    cr = re.findall(r"test result: \w+\. (\d+) passed; (\d+) failed; (\d+) ignored", out)
    if cr:
        return {"framework": "cargo", "passed": sum(int(a) for a, _, _ in cr),
                "failed": sum(int(b) for _, b, _ in cr), "errors": 0,
                "skipped": sum(int(c) for _, _, c in cr)}
    # go test: "--- PASS/FAIL: TestX" (-v) 或包级 "ok  pkg 0.01s" / "FAIL pkg 0.01s"
    tp, tf = len(re.findall(r"^\s*--- PASS:", out, re.M)), len(re.findall(r"^\s*--- FAIL:", out, re.M))
    pk_ok = len(re.findall(r"^ok\s+\S+", out, re.M))
    pk_fail = len(re.findall(r"^FAIL\s+\S+", out, re.M))
    if tp or tf or pk_ok or pk_fail:
        if tp or tf:
            return {"framework": "go", "passed": tp, "failed": tf, "errors": 0, "skipped": 0}
        return {"framework": "go", "unit": "包", "passed": pk_ok, "failed": pk_fail, "errors": 0, "skipped": 0}
    # mocha: "4 passing" / "1 failing"
    mp, mf = re.search(r"(\d+) passing\b", out), re.search(r"(\d+) failing\b", out)
    if mp or mf:
        return {"framework": "mocha", "passed": _n(mp), "failed": _n(mf), "errors": 0,
                "skipped": _n(re.search(r"(\d+) pending\b", out))}
    # node --test / TAP: "# pass 3" / "# fail 0"
    tpm, tfm = re.search(r"^# pass (\d+)", out, re.M), re.search(r"^# fail (\d+)", out, re.M)
    if tpm or tfm:
        return {"framework": "tap", "passed": _n(tpm), "failed": _n(tfm), "errors": 0, "skipped": 0}
    return None


def _tail(text: str, n: int = 20) -> List[str]:
    lines = [ln.rstrip()[:160] for ln in (text or "").strip().splitlines() if ln.strip()]
    return lines[-n:]


# ═══════════════════════════ 采集器 ═══════════════════════════

_BLOCK_PATH_RE = re.compile(r"^([^\n]+)\n<{7}\s*SEARCH\n", re.MULTILINE)
_REDIR_RE = re.compile(r"(?<![0-9&<>])>>?\s*([^\s;&|<>()]+)")
_TEE_RE = re.compile(r"\btee\s+(?:-a\s+)?([^\s;&|<>()]+)")
_SED_I_RE = re.compile(r"\bsed\s+(?:-\w*\s+)*-i\S*\s+(?:'[^']*'|\"[^\"]*\"|\S+)\s+([^\s;&|<>()]+)")


def _norm(p: str) -> str:
    return os.path.normpath(os.path.expanduser(str(p).strip().strip("'\"")))


def _mentions(cmd: str, path: str) -> bool:
    b = os.path.basename(path)
    if not b:
        return False
    return re.search(r"(?<![\w.-])" + re.escape(b) + r"(?![\w-]|\.\w)", cmd) is not None


def _shell_written(cmd: str) -> List[str]:
    out = []
    for rx in (_REDIR_RE, _TEE_RE, _SED_I_RE):
        for m in rx.finditer(cmd or ""):
            t = m.group(1).strip("'\"")
            if t and not t.startswith(("/dev/", "&")) and not t.isdigit():
                out.append(t)
    return out


@dataclass
class EvidenceCollector:
    agent_type: str
    write_role: bool = False
    commands: List[dict] = field(default_factory=list)
    writes: Dict[str, dict] = field(default_factory=dict)      # norm path -> {path, via, readback}
    readbacks: List[str] = field(default_factory=list)
    win: List[str] = field(default_factory=list)
    tool_ok: Counter = field(default_factory=Counter)
    tool_err: int = 0
    nudged: bool = False
    worktree: Optional[dict] = None
    _seq: int = 0
    # operator: 远程界面改动与确认状态
    op_file_changes: int = 0
    op_file_pending: bool = False     # 最后一次文件改动之后尚无成功回读
    op_actions: int = 0
    op_confirm_pending: bool = False  # 最后一次界面动作之后尚无截图/OCR/回读确认
    op_last_readback: str = ""
    op_targets: List[str] = field(default_factory=list)       # 任务点名的文件 (小写 basename)
    op_target_seen: set = field(default_factory=set)          # 最后一次文件改动之后已确认的点名文件

    # ── 观察一次工具调用 ──
    def observe(self, name: str, args: dict, result) -> None:
        try:
            self._observe(name, args if isinstance(args, dict) else {}, str(result or ""))
        except Exception:
            pass       # 证据采集绝不影响子代理执行

    def _observe(self, name: str, args: dict, result: str) -> None:
        self._seq += 1
        ok = not result.lstrip().startswith(("ERROR", "TIMEOUT", "EXEC_ERROR"))
        if not ok:
            self.tool_err += 1
        if name in _WRITE_TOOLS:
            if ok:
                if name == "apply_blocks":
                    paths = [m.group(1).strip() for m in _BLOCK_PATH_RE.finditer(args.get("blocks") or "")]
                else:
                    paths = [args.get("filepath") or args.get("path") or args.get("file") or ""]
                for p in paths:
                    if p:
                        self._add_write(p, name)
            return
        if name == "read_file":
            if ok:
                self.tool_ok[name] += 1
                rp = _norm(args.get("filepath") or args.get("path") or "")
                for k, w in self.writes.items():
                    if k == rp or (os.path.basename(k) == os.path.basename(rp)
                                   and (not os.path.isabs(k) or not os.path.isabs(rp))):
                        if not w["readback"]:
                            w["readback"] = True
                            self.readbacks.append(f"read_file {w['path']}")
            return
        if name == "execute_shell":
            self._observe_shell(str(args.get("command") or ""), result, ok)
            return
        if name == "windows_system":
            act = str(args.get("action") or "")
            if ok:
                self.tool_ok[f"windows_system.{act}"] += 1
                if act in _WIN_READBACK_ACTIONS or (self._operator() and act in _OP_READBACK):
                    brief = " ".join(result.split())[:150]
                    self.win.append(f"{act} {args.get('path', '') or args.get('script', '')[:40]}: {brief}")
                if self._operator():
                    self._note_targets(act, args, result)
                    if act in _OP_READBACK:
                        self.op_file_pending = self.op_confirm_pending = False
                        self.op_last_readback = f"windows_system {act} {args.get('path', '')}".strip()
                    elif act == "upload":
                        self.op_file_changes += 1
                        self.op_file_pending = self.op_confirm_pending = True
                        self.op_target_seen.clear()
                    elif act == "kill_process":
                        self.op_actions += 1
                        self.op_confirm_pending = True
                    elif act in _OP_SYS_CONFIRM:
                        self.op_confirm_pending = False
            return
        if name == "windows_desktop":
            act = str(args.get("action") or "")
            if ok:
                self.tool_ok[f"windows_desktop.{act}"] += 1
                if self._operator():
                    from .readback_gate import is_file_change
                    if is_file_change(act, args):
                        self.op_file_changes += 1
                        self.op_file_pending = self.op_confirm_pending = True
                        self.op_target_seen.clear()
                    elif act in _OP_CONFIRM:
                        self.op_confirm_pending = False
                    elif act not in _OP_NEUTRAL:
                        self.op_actions += 1
                        self.op_confirm_pending = True
            return
        if ok and name not in _NON_EVIDENCE_TOOLS:
            self.tool_ok[name] += 1

    def _operator(self) -> bool:
        from .subagent_operator import is_operator
        return is_operator(self.agent_type)

    def _note_targets(self, act: str, args: dict, result: str) -> None:
        """成功的 inspect_file/download (路径即该文件)、list_dir/run_powershell (输出里出现该文件名) 算确认了点名文件。"""
        if not self.op_targets or act not in _OP_READBACK:
            return
        if act in ("inspect_file", "download"):
            names = [str(args.get("path") or "").replace("\\", "/").rsplit("/", 1)[-1].lower()]
        else:
            names = [m.group(1).lower() for m in _TARGET_RE.finditer(result)]
        for t in self.op_targets:
            if any(_target_match(t, n) for n in names):
                self.op_target_seen.add(t)

    def _operator_verdict(self) -> Optional[Tuple[bool, str]]:
        missing = [t for t in self.op_targets if t not in self.op_target_seen]
        if missing and (self.op_file_changes or self.op_actions):
            return False, (f"任务点名的文件 {', '.join(missing)} 在最后一次改动之后没有被 inspect_file/list_dir 确认 "
                           "(可能没生成、存错位置或改的是别的文件)")
        if self.op_file_changes:
            if self.op_file_pending:
                return False, (f"界面/上传改过文件 {self.op_file_changes} 次, 但最后一次改动之后没有用 "
                               "inspect_file/list_dir/run_powershell 回读文件真值 (截图/OCR 不算)")
            return True, f"改过文件 {self.op_file_changes} 次, 改动后已回读: {self.op_last_readback}"
        if self.op_actions:
            if self.op_confirm_pending:
                return False, f"执行了 {self.op_actions} 次界面操作, 但最后一次操作之后没有截图/OCR 或回读确认"
            return True, f"执行了 {self.op_actions} 次界面操作, 最后已用截图/OCR 或回读确认"
        return None

    def _add_write(self, path: str, via: str) -> None:
        k = _norm(path)
        self.writes[k] = {"path": path, "via": via, "readback": False}   # 重写会清掉旧的回读标记

    def _observe_shell(self, cmd: str, result: str, ok: bool) -> None:
        kind = classify_command(cmd)
        code = infer_exit(result)
        written = set(_norm(p) for p in _shell_written(cmd))
        # 回读: 命令成功且提到了之前写过的文件 (本条命令自己写的不算); git diff/show 视为全部回读
        if code == 0:
            whole = re.search(r"\bgit\s+(diff|show)\b", cmd) is not None
            for k, w in self.writes.items():
                if k in written or w["readback"]:
                    continue
                if whole or _mentions(cmd, w["path"]):
                    w["readback"] = True
                    self.readbacks.append(f"shell `{cmd.strip()[:60]}` 读到 {w['path']}")
        for p in _shell_written(cmd):
            if code == 0:
                self._add_write(p, "execute_shell")
        # 非零退出的测试/命令输出同样是客观证据 (如 tester 报"测试失败"); 只有执行器级错误不算
        if ok or re.match(r"\s*ERROR\[(EXIT=|TEST_FAIL)", result):
            self.tool_ok["execute_shell"] += 1
        if kind:
            counts = parse_test_counts(result) if kind == "test" else None
            rec = {"kind": kind, "cmd": " ".join(cmd.split())[:120], "exit": code, "counts": counts}
            if kind == "test" and counts is None:
                rec["tail"] = _tail(result, 20)
            elif code not in (0, None):
                rec["tail"] = _tail(result, 5)
            self.commands.append(rec)

    # ── 判定 ──
    def changed_files(self) -> List[str]:
        files = [w["path"] for w in self.writes.values()]
        wt = self.worktree or {}
        for f in wt.get("files") or []:
            if not any(os.path.basename(f) == os.path.basename(x) for x in files):
                files.append(f)
        return files

    @staticmethod
    def _cmd_passed(c: dict) -> bool:
        if c["kind"] not in _RUN_KINDS or c["exit"] != 0:
            return False
        cn = c.get("counts")
        return not (cn and (cn.get("failed") or cn.get("errors")))

    def verdict(self) -> Tuple[bool, str]:
        if self._operator():
            ov = self._operator_verdict()
            if ov is not None:
                return ov
        changed = self.changed_files()
        if self.write_role and changed:
            passed = [c for c in self.commands if self._cmd_passed(c)]
            if passed:
                c = passed[-1]
                return True, (f"改动 {len(changed)} 个文件, {_KIND_CN[c['kind']]}命令通过 "
                              f"(exit 0): {c['cmd'][:60]}")
            tool_writes = list(self.writes.values())
            unread = [w["path"] for w in tool_writes if not w["readback"]]
            if tool_writes and not unread:
                return True, f"改动 {len(changed)} 个文件, 均已回读核对"
            failing = [c for c in self.commands if c["kind"] in _RUN_KINDS and not self._cmd_passed(c)]
            if failing:
                c = failing[-1]
                cn = c.get("counts") or {}
                extra = f", failed={cn.get('failed', 0)}" if cn else ""
                why = f"{_KIND_CN[c['kind']]}未通过: {c['cmd'][:60]} (exit {c['exit']}{extra})"
            else:
                why = "没有运行任何测试/构建/运行命令"
            if unread:
                why += f", 改动文件未回读: {', '.join(os.path.basename(p) for p in unread[:5])}"
            return False, f"改动 {len(changed)} 个文件但{why}"
        n = sum(self.tool_ok.values())
        if n:
            top = ", ".join(f"{k}×{v}" for k, v in self.tool_ok.most_common(4))
            pre = ("无界面改动; " if self._operator() else "无代码改动; ") if self.write_role else ""
            return True, f"{pre}有工具取证: {top}"
        if self.tool_err:
            return False, f"{self.tool_err} 次工具调用全部失败, 结论没有工具结果支撑"
        return False, "没有任何工具取证, 结论仅为模型自述"

    # ── 一次补证机会 ──
    def nudge(self, it: int, max_iter: int) -> Optional[str]:
        if self.nudged or not self.write_role or it >= max_iter - 1:
            return None
        ok, why = self.verdict()
        if ok:
            return None
        self.nudged = True
        if self._operator():
            missing = [t for t in self.op_targets if t not in self.op_target_seen]
            if missing:
                # 生产实测: 文件名少打一个字符后另存成别的名字, 通用补证提示不会让它回头改名
                return (
                    f"{NUDGE_PREFIX} 你准备结束, 但任务点名的文件没有被确认: {', '.join(missing)}。\n"
                    "先用 windows_system list_dir 查看任务要求的那个目录, 逐字核对文件名 (包括数字和扩展名)。"
                    "如果文件不在、名字不一致或存到了别处: 回到程序用「另存为」按任务给的原文件名、原位置重新保存 "
                    "(遇到同名覆盖提示时, 目标就是任务点名的文件则确认覆盖), 不能改用别的名字代替; "
                    "然后 inspect_file 回读这个点名文件核对内容。做不到就在总结里明确写'未验证'及原因。"
                    "这是唯一一次补证机会, 之后无论如何都会结束并按实际证据判定。"
                )
            return (
                f"{NUDGE_PREFIX} 你准备结束, 但框架没有采集到能证明结论的客观证据: {why}。\n"
                "结束前请补证: 改过文件的, 用 windows_system inspect_file (或 list_dir / run_powershell) "
                "回读文件真实内容逐项核对, 数字以文件为准; 只做了界面操作的, 用 windows_desktop screenshot 或 ocr "
                "确认最终画面。然后基于回读/截图的真实结果重新总结, 没能验证的部分明确写'未验证'。"
                "这是唯一一次补证机会, 之后无论如何都会结束并按实际证据判定。"
            )
        files = [os.path.basename(p) for p in self.changed_files()][:6]
        fl = f" ({', '.join(files)})" if files else ""
        return (
            f"{NUDGE_PREFIX} 你准备结束, 但框架从你的工具调用里没有采集到能证明结论的客观证据: {why}。\n"
            "结束前请补证 (二选一即可):\n"
            "  1) 用 execute_shell 运行相关测试 / 构建 / 实际运行改动后的代码, 看到退出码 0 的真实输出;\n"
            f"  2) 用 read_file 回读你改动的文件{fl}, 逐项核对内容。\n"
            "然后基于工具的真实输出重新总结: 数字与结论以工具输出为准, 没能验证的部分明确写'未验证'。"
            "这是唯一一次补证机会, 之后无论如何都会结束并按实际证据判定。"
        )

    # ── 渲染「证据」小节 ──
    def render(self, verified: bool, reason: str, limit: int = SECTION_MAX) -> str:
        lines = ["【证据】(框架从工具调用机器采集, 不采信模型自述)",
                 f"判定: verified={'true' if verified else 'false'} — {reason}"]
        if self.commands:
            lines.append("测试/构建/运行:")
            for c in self.commands[-6:]:
                cn = c.get("counts")
                cs = ""
                if cn:
                    u = cn.get("unit", "")
                    cs = (f", {cn.get('framework')}: {cn.get('passed', 0)}{u} passed / "
                          f"{cn.get('failed', 0)}{u} failed" + (f" / {cn['errors']} errors" if cn.get("errors") else ""))
                ex = "未知(后台)" if c["exit"] is None else c["exit"]
                lines.append(f"  - [{_KIND_CN.get(c['kind'], c['kind'])}] {c['cmd'][:80]} → exit {ex}{cs}")
            last_tail = next((c for c in reversed(self.commands) if c.get("tail")), None)
            if last_tail:
                lines.append(f"  尾部输出 ({last_tail['cmd'][:40]}):")
                lines += [f"    | {t}" for t in last_tail["tail"]]
        if self.writes:
            ws = [f"{w['path']}({'已回读' if w['readback'] else '未回读'})" for w in self.writes.values()]
            lines.append(f"写入文件 {len(ws)} 个: " + ", ".join(ws[:10]) + (" ..." if len(ws) > 10 else ""))
        if self.readbacks:
            lines.append("回读: " + "; ".join(self.readbacks[:4]))
        if self.op_file_changes or self.op_actions:
            lines.append(f"远程界面: 文件改动 {self.op_file_changes} 次"
                         f"({'未回读' if self.op_file_pending else '已回读'}), 其它界面操作 {self.op_actions} 次"
                         f"({'未确认' if self.op_confirm_pending else '已确认'})")
        for w in self.win[-3:]:
            lines.append(f"Windows 回读: {w}")
        if self.tool_ok:
            lines.append("工具取证: " + ", ".join(f"{k}×{v}" for k, v in self.tool_ok.most_common(8))
                         + (f"; 失败调用 {self.tool_err} 次" if self.tool_err else ""))
        wt = self.worktree
        if wt:
            from .subagent_worktree import _STATUS_CN
            st = wt.get("status", "?")
            s = (f"工作树 {wt.get('id')}: 分支 {wt.get('branch')} 提交 {(wt.get('commit') or '-')[:12]} "
                 f"{_STATUS_CN.get(st, st)}")
            if wt.get("merge_commit"):
                s += f" (合并提交 {wt['merge_commit'][:12]})"
            if wt.get("conflicts"):
                s += f" 冲突文件: {', '.join(wt['conflicts'][:5])}"
            if wt.get("diffstat"):
                s += f"; {wt['diffstat']}"
            lines.append(s)
        if self.nudged:
            lines.append("补证提示: 已注入 1 次 [SYSTEM-EVIDENCE]")
        text = "\n".join(lines)
        if len(text) > limit:
            text = text[:limit - 12].rstrip() + "\n...[截断]"
        return text


# ═══════════════════════════ 子代理侧接入 ═══════════════════════════

def start_collector(agent_type: str, task: str = "") -> Optional[EvidenceCollector]:
    """_run_subagent 开头调用; 开关关闭返回 None (调用方全部短路)。"""
    if not enabled():
        return None
    try:
        col = EvidenceCollector(agent_type=agent_type, write_role=_is_write_role(agent_type))
        if col._operator():
            col.op_targets = task_targets(task)
    except Exception:
        return None
    slot = _SLOT.get()
    if slot is not None:
        slot["collector"] = col
    return col


def note_worktree(report: dict) -> None:
    """subagent_worktree 收尾后调用, 把提交/合并结果交给外层证据包装器。"""
    slot = _SLOT.get()
    if slot is not None and isinstance(report, dict):
        slot["worktree"] = dict(report)


# ═══════════════════════════ 派发侧 (spawn_agent) ═══════════════════════════

def marker(verified: bool, reason: str) -> str:
    return "[已验证]" if verified else f"[未验证: {reason}]"


@dataclass
class EvidencePlan:
    enabled: bool = True
    records: List[dict] = field(default_factory=list)
    _count: int = 0

    def wrap(self, run_fn: Callable) -> Callable:
        if not self.enabled:
            return run_fn
        plan = self

        async def _with_evidence(task, agent_type="coder", context="", sse_emit=None, *a, **kw):
            idx = plan._count
            plan._count += 1
            slot: dict = {}
            token = _SLOT.set(slot)
            try:
                out = await run_fn(task, agent_type, context, sse_emit, *a, **kw)
            except BaseException:
                plan.records.append(_record(idx, task, agent_type, slot, None, "子代理被取消/异常, 没有交付"))
                raise
            finally:
                _SLOT.reset(token)
            plan.records.append(_record(idx, task, agent_type, slot, out))
            return out
        return _with_evidence

    def decorate(self, text: str) -> str:
        """主代理收到的工具结果: 开头醒目标记 + 末尾证据小节 (多个子代理时列出每个的判定)。"""
        if not self.enabled or not self.records or not isinstance(text, str):
            return text
        recs = sorted(self.records, key=lambda r: r["idx"])
        if len(recs) == 1:
            r = recs[0]
            return f"{marker(r['verified'], r['reason'])}\n{text}\n\n{r['section']}"
        bad = [r for r in recs if not r["verified"]]
        head = (f"[已验证] 全部 {len(recs)} 个子代理均有机器证据" if not bad else
                f"[未验证: {len(bad)}/{len(recs)} 个子代理未验证 — 其结论只能当线索, 汇报前须自行核实]")
        budget = max(300, min(SECTION_MAX, 6000 // len(recs)))
        lines = ["", "--- 子代理证据判定 ---"]
        for r in recs:
            lines.append(f"### {marker(r['verified'], r['reason'])} {r['label']}")
            lines.append(r["section"] if len(r["section"]) <= budget else
                         r["section"][:budget - 12].rstrip() + "\n...[截断]")
        return f"{head}\n{text}\n" + "\n".join(lines)


def _record(idx, task, agent_type, slot, out, abort_reason: str = "") -> dict:
    col: Optional[EvidenceCollector] = slot.get("collector")
    ran = col is not None
    if not ran:
        col = EvidenceCollector(agent_type=agent_type, write_role=_safe_write_role(agent_type))
    col.worktree = slot.get("worktree") or col.worktree   # 工作树改动文件也参与判定
    label = f"{agent_type}「{' '.join(str(task).split())[:30]}」"
    if abort_reason:
        ok, why = False, abort_reason
    elif not ran:
        ok, why = False, "子代理未实际运行 (命中缓存/嵌套过深/被拒), 本次没有采集到证据"
    else:
        ok, why = col.verdict()
        if isinstance(out, str) and out.lstrip().startswith(_ABORT_PREFIXES):
            ok, why = False, f"子代理未正常结束 {out.strip()[:10]}; 已采集: {why}"
    return {"idx": idx, "label": label, "agent_type": agent_type, "verified": ok, "reason": why,
            "section": col.render(ok, why)}


def _safe_write_role(agent_type: str) -> bool:
    try:
        return _is_write_role(agent_type)
    except Exception:
        return True


def plan_evidence() -> EvidencePlan:
    return EvidencePlan(enabled=enabled())
