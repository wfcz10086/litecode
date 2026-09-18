#!/usr/bin/env python3
"""
swe-bench.py — SWE-bench Verified 精简评测脚本
=================================================
针对 LiteCode agent 的 SWE-bench Verified (500 题) 评测runner。

## 设计

每个 instance 的执行流程:
  1. clone 目标 repo 到 workspace/swe-bench/{instance_id}/
  2. git checkout base_commit
  3. 把 problem_statement 送给 LiteCode (POST /v1/chat/completions)
  4. 等 agent 跑完 → 在 repo 里 `git diff` 得到 agent 产生的 patch
  5. 叠加 golden test_patch (加入测试用例)
  6. 运行:
     - FAIL_TO_PASS: 之前 FAIL 的测试, 补丁后应 PASS
     - PASS_TO_PASS: 之前 PASS 的测试, 补丁后仍应 PASS
  7. 判定 resolved (两类全过) / partial / failed / error

## 运行方式

```bash
# 先装 dataset 支持
pip install datasets --break-system-packages

# 烟雾测 (5 题)
python3 swe-bench.py --smoke

# 用 HuggingFace 的 Verified 子集
python3 swe-bench.py --subset verified --limit 20

# 给 session_id 前缀, 便于 log 区分
python3 swe-bench.py --smoke --session-prefix swebench-run1

# 用 docker 模式 (推荐, 避免环境污染)
python3 swe-bench.py --smoke --mode docker

# 离线 JSONL 模式 (dataset 不可下载时)
python3 swe-bench.py --input ./swebench_verified.jsonl --limit 10
```

## 限制 (诚实声明)

本脚本是**精简版**, 与官方 SWE-bench harness 的区别:
  - 不起 per-instance Docker 容器 (local 模式直接 venv/pip install)
  - 不做严格的 test command 推断 (仅支持 pytest)
  - 复杂构建 (如 requires_specific_python_version) 可能失败
  - 不做 ground_truth 对比 (只看 FAIL_TO_PASS/PASS_TO_PASS)

要得到可对外发布的 SWE-bench 分数, 仍需用官方 harness:
  https://github.com/princeton-nlp/SWE-bench

本脚本的价值: 快速迭代调试 agent, 定位它在真实 issue 上的失败模式。
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterable, Optional

try:
    import requests
except ImportError:
    requests = None

# ═══════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════
# [layout] 测试文件位于 litecodeext/tests/, 源码在父目录 litecodeext/
BASE = Path(__file__).resolve().parent.parent
_CFG = json.loads((BASE / "config.json").read_text()) if (BASE / "config.json").exists() else {}
SERVER_URL = f"http://127.0.0.1:{_CFG.get('server', {}).get('port', 18789)}"
SERVER_TOKEN = _CFG.get("server", {}).get("token", "CHANGE_ME_TOKEN")
MODEL = _CFG.get("model", {}).get("id", "openclaw")
WORKSPACE = Path(_CFG.get("paths", {}).get("workspace_base", "/tmp/openclaw_workspace"))

# ── [host/container split fix 2026-08-26] ─────────────────────────────────────
# 本脚本跑在**宿主机**, 而 agent 跑在 **litecode 容器**里。容器把宿主的
# /opt/litecode/workspace 挂到自己的 /tmp/litecode_workspace —— 于是
# "/tmp/litecode_workspace/swe-bench/<id>" 这个**路径字符串在两边指向不同目录**。
#
# 修复前的后果 (实测): harness 在宿主 clone → prompt 告诉 agent 去
# /tmp/litecode_workspace/swe-bench/<id> → agent 在容器里进这个路径发现是空的
# → 自己重新 clone 一份到子目录 → 改的是自己那份 → harness 回头 diff 宿主那份
# (从没被碰过) → patch_lines 恒为 0。**跑了 5 轮全是无效测量。**
#
# 修法: harness 把 clone 放进"容器可见"的宿主侧目录, prompt 里换算成容器侧路径。
# 两侧看的是同一份文件, diff 才有意义。
def _detect_ws_mount() -> tuple[Path, str]:
    """返回 (宿主侧 workspace 根, 容器侧 workspace 根)。
    优先 env 覆盖, 其次 docker inspect 自动探测, 都失败则退回原行为 (不做映射)。"""
    env_host = os.environ.get("SWEBENCH_HOST_WS")
    env_cont = os.environ.get("SWEBENCH_CONTAINER_WS")
    if env_host and env_cont:
        return Path(env_host), env_cont.rstrip("/")
    container = os.environ.get("LITECODE_CONTAINER", "litecode")
    try:
        out = subprocess.run(
            ["docker", "inspect", container,
             "--format", "{{range .Mounts}}{{.Source}}|{{.Destination}}\n{{end}}"],
            capture_output=True, text=True, timeout=20,
        ).stdout
        for line in out.splitlines():
            if "|" not in line:
                continue
            src, dst = line.split("|", 1)
            src, dst = src.strip(), dst.strip().rstrip("/")
            # 只认 workspace 根挂载, 排除 sessions 等子目录挂载
            if dst and str(WORKSPACE).rstrip("/") == dst:
                return Path(src), dst
    except Exception:
        pass
    # 探测不到 → 退回原行为 (单机无容器场景仍然可用)
    return WORKSPACE, str(WORKSPACE).rstrip("/")


_HOST_WS, _CONTAINER_WS = _detect_ws_mount()
SWE_WS = _HOST_WS / "swe-bench"          # harness 侧: clone / diff / 跑测试都在这
SWE_WS.mkdir(parents=True, exist_ok=True)


def _agent_path(p: Path) -> str:
    """把宿主侧路径换算成 agent (容器内) 能 cd 进去的路径。
    没有映射 (宿主==容器) 时原样返回。"""
    s = str(p)
    hs = str(_HOST_WS).rstrip("/")
    if hs and s.startswith(hs):
        return _CONTAINER_WS + s[len(hs):]
    return s
REPORT_DIR = WORKSPACE / "logs"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# HuggingFace 数据集名
DATASET_ID = "princeton-nlp/SWE-bench_Verified"

# ANSI 色
_T = sys.stderr.isatty()
G = "\033[32m" if _T else ""
R = "\033[31m" if _T else ""
Y = "\033[33m" if _T else ""
C = "\033[36m" if _T else ""
B = "\033[1m" if _T else ""
D = "\033[2m" if _T else ""
NC = "\033[0m" if _T else ""


@dataclass
class Instance:
    """SWE-bench Verified 单条数据结构。"""
    instance_id: str
    repo: str            # "django/django"
    base_commit: str     # SHA
    problem_statement: str
    test_patch: str      # 加测试用例的 diff
    hints_text: str = ""
    FAIL_TO_PASS: list = field(default_factory=list)
    PASS_TO_PASS: list = field(default_factory=list)
    patch: str = ""      # golden fix (仅做对比用, 不喂给 agent)
    version: str = ""


@dataclass
class InstanceResult:
    instance_id: str
    repo: str
    status: str          # resolved / partial / failed / error / skipped
    fail_to_pass_passed: int = 0
    fail_to_pass_total: int = 0
    pass_to_pass_passed: int = 0
    pass_to_pass_total: int = 0
    agent_elapsed: float = 0
    total_elapsed: float = 0
    agent_patch_lines: int = 0
    agent_text: str = ""        # agent 最后一段文字 (截断)
    error: str = ""
    # ── [diff-grade 2026-08-26] 不依赖跑测试的评分维度 ──────────────────────
    # 起因: local 模式的 pytest 环境不隔离 (系统装着新版 astropy, 题目是旧版源码且
    # C 扩展未编译), 实测在 `import astropy` 阶段就崩 → P2P=0/N (那是"改之前就该过"
    # 的测试, 全 0 说明根本没跑起来) → resolved 恒为 0, 与 agent 能力无关。
    # 直接把 agent 的 diff 和 golden patch 比对, 是不依赖运行时环境的能力信号。
    diff_grade: str = ""        # exact / partial / wrong_file / empty
    golden_files: list = field(default_factory=list)
    agent_files: list = field(default_factory=list)
    files_hit: list = field(default_factory=list)   # 改对了的文件 (与 golden 交集)
    files_extra: list = field(default_factory=list) # golden 没动而 agent 动了的
    golden_line_recall: float = 0.0   # golden 的改动行里, agent 复现了多少比例
    touched_test_files: list = field(default_factory=list)  # 违反"别动测试"的证据


# ═══════════════════════════════════════════════════════
# 数据集加载
# ═══════════════════════════════════════════════════════
def _load_from_hf(limit: Optional[int]) -> Iterable[Instance]:
    """通过 huggingface datasets 拉取 Verified 子集。"""
    try:
        from datasets import load_dataset
    except ImportError:
        print(f"{R}ERROR: datasets 未安装{NC}. 运行: "
              "pip install datasets --break-system-packages")
        return
    print(f"{C}加载 {DATASET_ID} (test split)...{NC}")
    ds = load_dataset(DATASET_ID, split="test")
    count = 0
    for row in ds:
        # FAIL_TO_PASS / PASS_TO_PASS 可能是 JSON string 需 parse
        f2p = row.get("FAIL_TO_PASS", "[]")
        p2p = row.get("PASS_TO_PASS", "[]")
        if isinstance(f2p, str):
            try: f2p = json.loads(f2p)
            except: f2p = []
        if isinstance(p2p, str):
            try: p2p = json.loads(p2p)
            except: p2p = []
        yield Instance(
            instance_id=row["instance_id"],
            repo=row["repo"],
            base_commit=row["base_commit"],
            problem_statement=row["problem_statement"],
            test_patch=row.get("test_patch", ""),
            hints_text=row.get("hints_text", ""),
            FAIL_TO_PASS=f2p, PASS_TO_PASS=p2p,
            patch=row.get("patch", ""),
            version=row.get("version", ""),
        )
        count += 1
        if limit and count >= limit:
            return


def _load_from_jsonl(path: Path, limit: Optional[int]) -> Iterable[Instance]:
    with path.open() as f:
        for i, line in enumerate(f):
            if not line.strip():
                continue
            if limit and i >= limit:
                return
            d = json.loads(line)
            f2p = d.get("FAIL_TO_PASS", [])
            p2p = d.get("PASS_TO_PASS", [])
            if isinstance(f2p, str):
                try: f2p = json.loads(f2p)
                except: f2p = []
            if isinstance(p2p, str):
                try: p2p = json.loads(p2p)
                except: p2p = []
            yield Instance(
                instance_id=d["instance_id"],
                repo=d["repo"],
                base_commit=d["base_commit"],
                problem_statement=d["problem_statement"],
                test_patch=d.get("test_patch", ""),
                hints_text=d.get("hints_text", ""),
                FAIL_TO_PASS=f2p, PASS_TO_PASS=p2p,
                patch=d.get("patch", ""),
                version=d.get("version", ""),
            )


# ═══════════════════════════════════════════════════════
# Git / shell 辅助
# ═══════════════════════════════════════════════════════
def _run(cmd: list, cwd: Optional[Path] = None, timeout: int = 180) -> tuple[int, str]:
    """同步执行, 返回 (exit_code, merged_output)."""
    try:
        env = None
        if cmd and cmd[0] == "git":
            # [host/container split fix 2026-08-26] 容器里 agent 以 root 身份写这些文件,
            # 宿主上跑 git 会触发 dubious-ownership 保护, 直接报
            # "fatal: detected dubious ownership" / "not a git repository" 并返回非 0。
            # 实测这会让 _git_diff 拿到空输出 → patch_lines 恒为 0, 且报错很容易被
            # 上层的 2>/dev/null 之类吞掉, 表现成"agent 什么都没改"的假象。
            # 用 GIT_CONFIG_* 环境变量注入 safe.directory=*, 不落任何全局配置文件。
            env = {
                **os.environ,
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "safe.directory",
                "GIT_CONFIG_VALUE_0": "*",
            }
        r = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True,
            timeout=timeout, errors="replace", env=env,
        )
        return r.returncode, (r.stdout + r.stderr)
    except subprocess.TimeoutExpired:
        return -1, f"[timeout after {timeout}s]"
    except Exception as e:
        return -1, f"[exec err] {e}"


def _clone_repo(instance: Instance, dst: Path) -> bool:
    """clone 到 dst 并 checkout 到 base_commit."""
    if dst.exists():
        # [cleanup fix 2026-08-28] 原来是 shutil.rmtree(dst, ignore_errors=True) ——
        # ignore_errors 会把权限失败**静默吞掉**, 然后 git clone 报一个看起来毫不相干的
        # "destination path already exists and is not an empty directory", 极难定位。
        # 实测成因: agent 在容器里以 root 跑 pip build, 留下 root 属主的 .so 编译产物,
        # 宿主上以普通用户跑的 harness 删不动。
        shutil.rmtree(dst, ignore_errors=True)
        if dst.exists():
            # 退回容器内以 root 删 (harness 在宿主, agent 在容器, 见 _detect_ws_mount)
            container = os.environ.get("LITECODE_CONTAINER", "litecode")
            in_container = _agent_path(dst)
            subprocess.run(["docker", "exec", container, "rm", "-rf", in_container],
                           capture_output=True, timeout=60)
        if dst.exists():
            # 还删不掉就明确报出来, 不要让下游 git clone 抛一个误导性的错
            print(f"{R}  清理失败: {dst} 仍存在 (可能有 root 属主文件, "
                  f"试 `docker exec {os.environ.get('LITECODE_CONTAINER','litecode')} "
                  f"rm -rf {_agent_path(dst)}`){NC}")
            return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/{instance.repo}.git"
    rc, out = _run(["git", "clone", "--quiet", url, str(dst)], timeout=300)
    if rc != 0:
        print(f"{R}  clone 失败: {out[:200]}{NC}")
        return False
    rc, out = _run(["git", "checkout", "--quiet", instance.base_commit], cwd=dst, timeout=60)
    if rc != 0:
        print(f"{R}  checkout {instance.base_commit[:8]} 失败: {out[:200]}{NC}")
        return False
    return True


def _apply_test_patch(repo_dir: Path, test_patch: str) -> bool:
    """把 golden test_patch 应用到 repo 里 (添加新测试用例)."""
    if not test_patch.strip():
        return True
    patch_file = repo_dir / ".swe_test.patch"
    patch_file.write_text(test_patch)
    rc, out = _run(["git", "apply", "--allow-empty", str(patch_file)],
                   cwd=repo_dir, timeout=30)
    if rc != 0:
        # 3-way 再试
        rc, out = _run(["git", "apply", "--3way", str(patch_file)],
                       cwd=repo_dir, timeout=30)
    patch_file.unlink(missing_ok=True)
    return rc == 0


# ═══════════════════════════════════════════════════════
# [diff-grade 2026-08-26] agent diff vs golden patch 比对
# ═══════════════════════════════════════════════════════
def _parse_patch(text: str) -> dict:
    """把 unified diff 解析成 {文件路径: (新增行集合, 删除行集合)}。

    只取真正的内容行 (+/-), 丢掉 diff header / index / @@ hunk 头 ——
    因为 hunk 的行号偏移会随上下文变化, 但改动内容本身是稳定的。
    行尾空白统一 strip, 避免纯格式差异造成假阴性。
    """
    files: dict = {}
    cur = None
    for line in (text or "").splitlines():
        if line.startswith("diff --git "):
            # "diff --git a/x b/x" → 取 b/ 侧
            parts = line.split()
            cur = parts[-1][2:] if len(parts) >= 4 and parts[-1].startswith("b/") else None
            if cur:
                files.setdefault(cur, (set(), set()))
            continue
        if cur is None or line.startswith(("index ", "--- ", "+++ ", "@@", "new file", "deleted file",
                                           "similarity index", "rename ", "old mode", "new mode")):
            continue
        if line.startswith("+"):
            v = line[1:].strip()
            if v:
                files[cur][0].add(v)
        elif line.startswith("-"):
            v = line[1:].strip()
            if v:
                files[cur][1].add(v)
    return files


def _is_test_file(path: str) -> bool:
    p = path.replace("\\", "/")
    return ("/tests/" in p or "/test/" in p
            or p.rsplit("/", 1)[-1].startswith("test_")
            or p.endswith("_test.py"))


def _grade_diff(agent_patch: str, golden_patch: str) -> dict:
    """对比 agent 的改动与标准答案, 返回可读的评分维度。

    grade 语义:
      exact      — golden 涉及的文件全部命中, 且 golden 的每一行改动都被复现
      partial    — 改对了文件, 但内容只对上一部分
      wrong_file — 有改动, 但一个 golden 文件都没碰到
      empty      — 没有任何改动
    刻意**不**因为 agent 多改了别的文件就降级 —— 多改的记在 files_extra 里单独看,
    因为真实修复里顺手改相关文件是合理的, 由人判断。
    """
    a = _parse_patch(agent_patch)
    g = _parse_patch(golden_patch)
    g_files = sorted(g.keys())
    # agent 侧排除测试文件参与"是否改对源码"的判定, 但单独记录 (prompt 明确禁止动测试)
    a_test = sorted(f for f in a if _is_test_file(f))
    a_files = sorted(a.keys())
    hit = sorted(set(a_files) & set(g_files))
    extra = sorted(set(a_files) - set(g_files) - set(a_test))

    # golden 改动行的召回率: golden 的每个 +/- 行, agent 有没有做出同样的改动
    g_lines = set()
    a_lines = set()
    for f, (add, rem) in g.items():
        g_lines |= {(f, "+", x) for x in add} | {(f, "-", x) for x in rem}
    for f, (add, rem) in a.items():
        a_lines |= {(f, "+", x) for x in add} | {(f, "-", x) for x in rem}
    recall = (len(g_lines & a_lines) / len(g_lines)) if g_lines else 0.0

    if not a_files:
        grade = "empty"
    elif not hit:
        grade = "wrong_file"
    elif recall >= 0.999:
        grade = "exact"
    else:
        grade = "partial"
    return {
        "diff_grade": grade,
        "golden_files": g_files,
        "agent_files": a_files,
        "files_hit": hit,
        "files_extra": extra,
        "golden_line_recall": round(recall, 3),
        "touched_test_files": a_test,
    }


def _git_diff(repo_dir: Path) -> str:
    """获取 agent 的 patch (workspace 变更)."""
    rc, out = _run(["git", "diff", "--no-color"], cwd=repo_dir, timeout=30)
    return out if rc == 0 else ""


def _reset_repo(repo_dir: Path, base_commit: str):
    """还原 repo 到 base_commit, 扔掉所有未提交变更."""
    _run(["git", "reset", "--hard", base_commit], cwd=repo_dir, timeout=30)
    _run(["git", "clean", "-fdx"], cwd=repo_dir, timeout=30)


# ═══════════════════════════════════════════════════════
# 测试运行 (简化版, 只认 pytest)
# ═══════════════════════════════════════════════════════
def _run_tests(repo_dir: Path, test_ids: list, timeout: int = 180) -> dict:
    """
    跑 test_ids (通常是 'tests.test_X::test_foo' 格式) 返回 {tid: pass/fail/err}.
    """
    if not test_ids:
        return {}
    # 统一用 pytest, 单独跑每个以获取精确结果
    results = {}
    for tid in test_ids:
        cmd = ["python3", "-m", "pytest", "-x", "--no-header",
               "--tb=no", "-q", tid]
        rc, out = _run(cmd, cwd=repo_dir, timeout=timeout)
        if rc == 0:
            results[tid] = "pass"
        else:
            # "err" vs "fail": assertion = fail; 其他 = err
            if "AssertionError" in out or "FAILED" in out:
                results[tid] = "fail"
            else:
                results[tid] = "err"
    return results


# ═══════════════════════════════════════════════════════
# 调 LiteCode agent
# ═══════════════════════════════════════════════════════
def _call_agent(problem_statement: str, repo_dir: Path,
                session_id: str, timeout: int = 1800) -> tuple[str, float]:
    """
    通过 /v1/chat/completions 让 agent 在 repo_dir 里解 issue.
    返回 (agent_last_text, elapsed_s).
    """
    if requests is None:
        raise RuntimeError("requests 未安装")

    # [host/container split fix 2026-08-26] agent 在容器里跑, 必须给它容器侧路径。
    # 宿主侧路径 (repo_dir) 只用于本脚本自己 clone/diff/跑测试。
    _agent_dir = _agent_path(repo_dir)
    prompt = (
        f"你现在在 {_agent_dir} 目录下处理一个开源项目的 issue。请按四段式流程:\n"
        f"1. 分析: 先 cd {_agent_dir} + get_tree + read_file 理解项目结构和相关代码\n"
        f"   (该目录已经 clone 好并 checkout 到指定 commit, **不要自己重新 clone**)\n"
        f"2. 计划: 列出修复这个 issue 要改哪些文件和步骤\n"
        f"3. 执行: 用 patch_file/write_file 改代码, 不要动测试文件 (测试是评审方加的)\n"
        f"4. 验证: 运行相关测试 (pytest) 确认你的修复生效\n\n"
        f"## Issue\n{problem_statement[:6000]}\n\n"
        f"要求: 只改源码, 不写新的测试文件。改完后运行 pytest 验证通过。"
    )
    t0 = time.time()
    try:
        r = requests.post(
            f"{SERVER_URL}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {SERVER_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "stream": True,
                "user": session_id,
            },
            timeout=timeout, stream=True,
        )
        full = ""
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
                delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
                full += delta.get("content") or ""
            except Exception:
                continue
        return full, time.time() - t0
    except Exception as e:
        return f"[agent error] {e}", time.time() - t0


# ═══════════════════════════════════════════════════════
# 单个 instance 评估
# ═══════════════════════════════════════════════════════
def evaluate_instance(instance: Instance, session_prefix: str,
                      mode: str = "local", agent_timeout: int = 1800,
                      test_timeout: int = 180) -> InstanceResult:
    """评估单个 SWE-bench instance."""
    res = InstanceResult(
        instance_id=instance.instance_id,
        repo=instance.repo,
        status="error",
        fail_to_pass_total=len(instance.FAIL_TO_PASS),
        pass_to_pass_total=len(instance.PASS_TO_PASS),
    )
    t_all = time.time()
    repo_dir = SWE_WS / instance.instance_id

    try:
        # 1. Clone + checkout
        print(f"{D}  [1/6] cloning {instance.repo} @ {instance.base_commit[:8]}...{NC}")
        if not _clone_repo(instance, repo_dir):
            res.error = "clone/checkout failed"
            return res

        # 2. 跑 agent
        session_id = f"{session_prefix}-{instance.instance_id[:20]}-{uuid.uuid4().hex[:4]}"
        print(f"{D}  [2/6] agent working (session={session_id})...{NC}")
        agent_text, agent_elapsed = _call_agent(
            instance.problem_statement, repo_dir, session_id, timeout=agent_timeout,
        )
        res.agent_elapsed = agent_elapsed
        res.agent_text = agent_text[-1000:] if agent_text else ""

        # 3. 取 agent 生成的 patch
        agent_patch = _git_diff(repo_dir)
        res.agent_patch_lines = len(agent_patch.splitlines())
        print(f"{D}  [3/6] agent patch: {res.agent_patch_lines} lines diff{NC}")

        # [diff-grade 2026-08-26] 就在这一刻评分 —— 后面 harness 会 stash/commit/
        # apply test_patch 反复折腾仓库, 事后再取磁盘状态会看到 harness 自己写进去的
        # 东西 (实测把 golden test_patch 改的 test_sampled.py 误判成"agent 改了测试文件")。
        # 这里的 agent_patch 是 agent 结束后、harness 动手前捕获的, 是唯一干净的快照。
        try:
            _g = _grade_diff(agent_patch, instance.patch)
            res.diff_grade = _g["diff_grade"]
            res.golden_files = _g["golden_files"]
            res.agent_files = _g["agent_files"]
            res.files_hit = _g["files_hit"]
            res.files_extra = _g["files_extra"]
            res.golden_line_recall = _g["golden_line_recall"]
            res.touched_test_files = _g["touched_test_files"]
            _gc = {"exact": G, "partial": Y, "wrong_file": R, "empty": R}.get(res.diff_grade, NC)
            _msg = (f"  [3/6] diff-grade: {_gc}{res.diff_grade}{NC}"
                    f"  golden行召回={res.golden_line_recall:.0%}"
                    f"  命中文件={len(res.files_hit)}/{len(res.golden_files)}")
            if res.files_extra:
                _msg += f"  额外改了{len(res.files_extra)}个"
            if res.touched_test_files:
                _msg += f"  {Y}⚠动了测试文件{len(res.touched_test_files)}个{NC}"
            print(_msg)
        except Exception as _ge:
            print(f"{D}  [3/6] diff-grade 失败: {_ge}{NC}")

        if not agent_patch.strip():
            res.status = "failed"
            res.error = "agent produced empty patch"
            return res

        # 暂存 agent patch 到 git stash, 以便叠加 test_patch
        _run(["git", "stash"], cwd=repo_dir, timeout=30)
        _run(["git", "stash", "pop"], cwd=repo_dir, timeout=30)
        # 其实更安全的做法: 先 commit agent 的 patch, 再 apply test_patch
        _run(["git", "add", "-A"], cwd=repo_dir, timeout=30)
        _run(["git", "-c", "user.name=swebench", "-c", "user.email=s@s.com",
              "commit", "-m", "agent patch", "--allow-empty", "--quiet"],
             cwd=repo_dir, timeout=30)

        # 4. 叠加 golden test_patch (加入评审方的测试用例)
        print(f"{D}  [4/6] applying golden test_patch...{NC}")
        if instance.test_patch.strip() and not _apply_test_patch(repo_dir, instance.test_patch):
            res.error = "golden test_patch 应用失败"
            res.status = "error"
            return res

        # 5. 跑 FAIL_TO_PASS (应 PASS)
        print(f"{D}  [5/6] running {len(instance.FAIL_TO_PASS)} FAIL_TO_PASS tests...{NC}")
        f2p_results = _run_tests(repo_dir, instance.FAIL_TO_PASS, timeout=test_timeout)
        res.fail_to_pass_passed = sum(1 for v in f2p_results.values() if v == "pass")

        # 6. 跑 PASS_TO_PASS (应仍然 PASS, 不能被我们的 patch 破坏)
        print(f"{D}  [6/6] running {len(instance.PASS_TO_PASS)} PASS_TO_PASS tests...{NC}")
        p2p_results = _run_tests(repo_dir, instance.PASS_TO_PASS, timeout=test_timeout)
        res.pass_to_pass_passed = sum(1 for v in p2p_results.values() if v == "pass")

        # 判定
        all_f2p_ok = (res.fail_to_pass_passed == res.fail_to_pass_total)
        all_p2p_ok = (res.pass_to_pass_passed == res.pass_to_pass_total)
        if all_f2p_ok and all_p2p_ok:
            res.status = "resolved"
        elif all_f2p_ok and not all_p2p_ok:
            res.status = "partial"       # 修好了但破坏了其他测试
        elif res.fail_to_pass_passed > 0:
            res.status = "partial"       # 修好了一部分
        else:
            res.status = "failed"

    except Exception as e:
        res.error = f"{type(e).__name__}: {e}"
        res.status = "error"
    finally:
        res.total_elapsed = time.time() - t_all
    return res


# ═══════════════════════════════════════════════════════
# CLI 主入口
# ═══════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(
        description="SWE-bench Verified 精简 runner (针对 LiteCode agent)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--subset", default="verified",
                     help="HF 子集名 (默认 verified)")
    src.add_argument("--input", type=str, help="本地 JSONL 文件路径")
    ap.add_argument("--limit", type=int, default=None,
                    help="只跑前 N 个 instance")
    ap.add_argument("--smoke", action="store_true",
                    help="烟雾测试: 只跑 3 个 instance")
    ap.add_argument("--mode", choices=["local", "docker"], default="local",
                    help="执行模式 (local=裸 pip, docker=TODO)")
    ap.add_argument("--session-prefix", default="swebench",
                    help="session_id 前缀, 便于 log 区分")
    ap.add_argument("--agent-timeout", type=int, default=1800,
                    help="单 instance agent 超时 (秒, 默认 1800)")
    ap.add_argument("--test-timeout", type=int, default=180,
                    help="单 test 超时 (秒, 默认 180)")
    ap.add_argument("--filter", type=str,
                    help="只跑 instance_id 匹配此子串的 (如 'django')")
    ap.add_argument("--report", type=str,
                    default=str(REPORT_DIR / "swe_bench_report.md"),
                    help="报告输出路径")
    args = ap.parse_args()

    if args.mode == "docker":
        print(f"{Y}WARN: docker 模式未实现, 退回 local. 真想要 docker 请用官方 harness: "
              f"https://github.com/princeton-nlp/SWE-bench{NC}")
        args.mode = "local"

    if requests is None:
        print(f"{R}ERROR: requests 未装{NC}. pip install requests")
        sys.exit(1)

    # 连通性检查
    try:
        h = requests.get(f"{SERVER_URL}/health",
                         headers={"Authorization": f"Bearer {SERVER_TOKEN}"},
                         timeout=5)
        if h.status_code != 200:
            print(f"{R}ERROR: server {SERVER_URL} 不健康 ({h.status_code}){NC}")
            sys.exit(1)
        print(f"{C}Server: {SERVER_URL}  Model: {h.json().get('model','?')}{NC}")
    except Exception as e:
        print(f"{R}ERROR: 连不上 server {SERVER_URL}: {e}{NC}")
        sys.exit(1)

    # 加载数据集
    limit = 3 if args.smoke else args.limit
    if args.input:
        p = Path(args.input)
        if not p.exists():
            print(f"{R}ERROR: {p} 不存在{NC}")
            sys.exit(1)
        instances = list(_load_from_jsonl(p, limit if not args.filter else None))
    else:
        # [v1.10 BUG-FIX] filter 模式下加载全部 500 题, 否则 limit 取头几题再 filter 必为 0
        instances = list(_load_from_hf(limit if not args.filter else None))

    if args.filter:
        instances = [i for i in instances if args.filter in i.instance_id]
        # filter 后再 apply limit
        if limit and len(instances) > limit:
            instances = instances[:limit]

    if not instances:
        print(f"{R}无 instance 可跑{NC}")
        sys.exit(1)

    print(f"\n{B}SWE-bench Verified 精简评测{NC}")
    print(f"{D}Instances: {len(instances)}  Mode: {args.mode}  "
          f"Session prefix: {args.session_prefix}{NC}")
    print(f"{D}Workspace: {SWE_WS}  Report: {args.report}{NC}\n")

    results: list[InstanceResult] = []
    t_total = time.time()
    for idx, inst in enumerate(instances, 1):
        print(f"\n{'='*60}")
        print(f"{B}[{idx}/{len(instances)}] {inst.instance_id}{NC}")
        print(f"{D}  repo: {inst.repo}  commit: {inst.base_commit[:8]}{NC}")
        print(f"{D}  issue: {inst.problem_statement[:150]}...{NC}")
        res = evaluate_instance(
            inst, args.session_prefix,
            mode=args.mode,
            agent_timeout=args.agent_timeout,
            test_timeout=args.test_timeout,
        )
        results.append(res)

        icon = {"resolved": "✅", "partial": "⚠️ ", "failed": "❌",
                "error": "⛔", "skipped": "⏭ "}.get(res.status, "?")
        color = {"resolved": G, "partial": Y, "failed": R,
                 "error": R, "skipped": D}.get(res.status, NC)
        print(f"{color}  {icon} {res.status.upper()}{NC}  "
              f"F2P={res.fail_to_pass_passed}/{res.fail_to_pass_total}  "
              f"P2P={res.pass_to_pass_passed}/{res.pass_to_pass_total}  "
              f"patch_lines={res.agent_patch_lines}  "
              f"elapsed={res.total_elapsed:.1f}s")
        if res.error:
            print(f"{R}    err: {res.error}{NC}")

    # ── 汇总 ──
    total = len(results)
    resolved = sum(1 for r in results if r.status == "resolved")
    partial = sum(1 for r in results if r.status == "partial")
    failed = sum(1 for r in results if r.status == "failed")
    errored = sum(1 for r in results if r.status == "error")
    total_elapsed = time.time() - t_total

    print(f"\n\n{B}{'='*60}{NC}")
    print(f"{B}SWE-bench 精简评测结果{NC}")
    print(f"{B}{'='*60}{NC}")
    print(f"  Total:    {total}")
    print(f"  {G}Resolved: {resolved} ({resolved/max(total,1)*100:.1f}%){NC}")
    print(f"  {Y}Partial:  {partial}{NC}")
    print(f"  {R}Failed:   {failed}{NC}")
    print(f"  {R}Error:    {errored}{NC}")
    print(f"  {D}Elapsed:  {total_elapsed:.1f}s "
          f"(avg {total_elapsed/max(total,1):.1f}s/instance){NC}")

    # ── [diff-grade 2026-08-26] 不依赖测试环境的能力维度 ──────────────────────
    # resolved 依赖 pytest 能跑起来; 当环境不隔离 (系统装了新版依赖 / C 扩展未编译)
    # 时它恒为 0, 淹没了 agent 的真实表现。这一段直接看"改对了没有"。
    _graded = [r for r in results if r.diff_grade]
    if _graded:
        _c = {k: sum(1 for r in _graded if r.diff_grade == k)
              for k in ("exact", "partial", "wrong_file", "empty")}
        _recall = sum(r.golden_line_recall for r in _graded) / len(_graded)
        _tests = [r for r in _graded if r.touched_test_files]
        print(f"\n{B}  ── diff vs golden (不依赖测试环境) ──{NC}")
        print(f"  {G}Exact:    {_c['exact']}{NC}  "
              f"{Y}Partial: {_c['partial']}{NC}  "
              f"{R}WrongFile: {_c['wrong_file']}  Empty: {_c['empty']}{NC}")
        print(f"  {D}golden 改动行平均召回: {_recall:.1%}{NC}")
        if _tests:
            print(f"  {Y}⚠ {len(_tests)} 题动了测试文件 (prompt 明确禁止){NC}")

    # 写报告
    lines = [
        "# SWE-bench Verified 精简评测报告",
        "",
        f"- 时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Model: {MODEL}",
        f"- Server: {SERVER_URL}",
        f"- Instances: {total}",
        f"- **Resolved: {resolved}/{total} ({resolved/max(total,1)*100:.1f}%)**",
        f"- Partial: {partial}  Failed: {failed}  Error: {errored}",
        f"- Total elapsed: {total_elapsed:.1f}s",
        "",
        "| # | Instance | Repo | Status | F2P | P2P | Patch | Time |",
        "|---|----------|------|--------|-----|-----|-------|------|",
    ]
    for i, r in enumerate(results, 1):
        lines.append(
            f"| {i} | `{r.instance_id}` | {r.repo} | **{r.status}** | "
            f"{r.fail_to_pass_passed}/{r.fail_to_pass_total} | "
            f"{r.pass_to_pass_passed}/{r.pass_to_pass_total} | "
            f"{r.agent_patch_lines}L | {r.total_elapsed:.1f}s |"
        )
    # 错误详情
    err_items = [r for r in results if r.error]
    if err_items:
        lines.extend(["", "## 错误详情", ""])
        for r in err_items:
            lines.append(f"- **{r.instance_id}** ({r.status}): {r.error}")

    # JSON 原始数据
    json_path = Path(args.report).with_suffix(".json")
    json_path.write_text(json.dumps([asdict(r) for r in results],
                                     ensure_ascii=False, indent=2))
    Path(args.report).write_text("\n".join(lines))
    print(f"\n  Report MD:  {args.report}")
    print(f"  Report JSON: {json_path}")

    sys.exit(0 if resolved == total else 1)


if __name__ == "__main__":
    main()
