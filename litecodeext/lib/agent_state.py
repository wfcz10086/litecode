"""
agent_state.py -- domain state objects for agent_stream() (AGENT_STREAM_REFACTOR M1+)

绞杀者模式迁移: 把 litecode_server.py::agent_stream() 里跨阶段共享的局部变量
按原作者的注释标签分域收进小的领域状态对象, 而不是一个大的上帝对象。
见 docs/AGENT_STREAM_REFACTOR.md。
"""
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ProjectMapState:
    """[PROJECT_MAP] 域 -- PROJECT_MAP.md 自动生成/更新提醒/写锁相关的 7 个跨阶段变量"""
    py_files_written: int = 0          # 追踪本任务写入的 .py 文件数
    project_map_written: bool = False  # 是否已创建 PROJECT_MAP.md
    py_at_map_write: int = 0           # 创建 MAP 时的 py 计数（用于更新提醒）
    project_root: str = ""             # 推断的项目根目录
    map_reminded_files: set = field(default_factory=set)     # 已提醒过的文件
    files_since_map_update: int = 0    # 自上次 update_map 调用以来写入的源文件数
    project_map_lock: threading.Lock = field(default_factory=threading.Lock)  # 写锁（防 spawn_agent 子代理并发覆盖）


@dataclass
class TestTrackState:
    """[TEST] 域 -- 写源文件/测试文件后的强制补测提醒 + 立即执行的 5 个跨阶段变量"""
    src_files_written: list = field(default_factory=list)   # 已写的源文件列表 (各语言)
    test_files_written: set = field(default_factory=set)    # 已写的测试文件集合
    iter_wrote_src: bool = False       # 本轮写了源文件
    iter_wrote_test: bool = False      # 本轮写了测试文件
    tests_run_for: set = field(default_factory=set)         # 已执行过测试的文件集合（防止重复注入）


@dataclass
class WebTestState:
    """[WEB-TEST] 域 -- 项目收尾时注入最终 Web 测试相关的 2 个跨阶段变量"""
    web_test_injected: bool = False    # 是否已注入最终Web测试
    readme_written: bool = False       # README 已写


@dataclass
class BatchWriteState:
    """[BATCH] 域 -- 连续单文件写入检测 + 批量写入提醒的 2 个跨阶段变量
    + [READONLY] 只读空转检测 (探索不收敛) 的 2 个跨阶段变量
    + [PLAN-GATE] 复杂任务强制先拆解 (Plan-First Gate, 修复轮 #2 改为轨迹触发, 不再判文本)
    的 3 个跨阶段变量, 同域: 都是"根据本轮工具调用模式决定要不要注入一条提醒"这类判断,
    语义上比塞进 MiscTurnState 更近。"""
    consecutive_single_writes: int = 0  # 连续单文件写入计数
    batch_reminder_sent: bool = False   # 是否已发送批量写入提醒
    readonly_streak: int = 0            # [READONLY] 连续零写入轮数, 一遇写入立即清零
    readonly_reminder_count: int = 0    # [READONLY] 已发送的推进提醒次数 (有上限, 防刷屏)
    plan_gate_tool_call_iters: int = 0  # [PLAN-GATE] 本轮累计发生过工具调用的迭代轮数 (非单个 call 数)
    plan_gate_task_created: bool = False  # [PLAN-GATE] 本轮是否调用过 task_create (调过永久豁免)
    plan_gate_injected: bool = False    # [PLAN-GATE] 本轮是否已注入过拆解提醒 (注入过永久不再注入)
    exec_gate_write_calls: int = 0      # [EXEC-GATE] 本轮累计*成功*写类工具调用次数 (名单见 litecode_server._WRITE_TOOL_NAMES)
    exec_gate_injected: bool = False    # [EXEC-GATE] 本轮是否已注入过收尾提醒 (最多注入 1 次, 之后永久放行)

    def readonly_tick(self, iter_has_write: bool, threshold: int = 10, max_reminders: int = 3) -> bool:
        """记录本轮是否调用了写类工具, 返回本轮是否应该注入"探索不收敛"推进提醒。
        达阈值即重置 streak (避免每轮都提醒), reminder_count 达上限后永久不再提醒
        (保护纯调研类任务不被反复催)。"""
        if iter_has_write:
            self.readonly_streak = 0
            return False
        self.readonly_streak += 1
        if self.readonly_streak >= threshold and self.readonly_reminder_count < max_reminders:
            self.readonly_streak = 0
            self.readonly_reminder_count += 1
            return True
        return False

    def plan_gate_tick(self, task_created_now: bool, threshold: int = 5) -> bool:
        """记录"本轮又发生了一次带工具调用的迭代"(调用方只在确认有 tool_call 的分支里调这个
        方法, 所以每次调用天然等价于 +1 轮, 不是数单个 tool_call 个数), 返回本轮是否应该注入
        Plan-Gate 拆解提醒。task_created_now=True 一旦发生就永久豁免 (本轮循环内不重置);
        injected 一旦为真也永久不再返回 True (最多注入 1 次, 不重复提醒, 不死锁)。"""
        self.plan_gate_tool_call_iters += 1
        if task_created_now:
            self.plan_gate_task_created = True
        if (not self.plan_gate_injected
                and not self.plan_gate_task_created
                and self.plan_gate_tool_call_iters >= threshold):
            self.plan_gate_injected = True
            return True
        return False

    def exec_gate_tick(self, tool_call_iters: int, min_tool_iters: int = 4) -> bool:
        """在"本轮模型这一次没有 tool_call、即将给出最终回复并结束回合"的那一刻调用一次。
        `tool_call_iters` 是本轮累计"带工具调用的迭代轮数"(复用 plan_gate_tool_call_iters
        这同一个计数器传入, 语义相同、不重复维护第二份)。若已达最小工具轮数门槛
        (排除纯问答/闲聊这类几乎不调工具就正常结束的场景), 且本轮*成功*写类工具调用次数
        (`exec_gate_write_calls`, 由调用方在每批工具调用执行完、确认结果非 ERROR 后才
        累加——判不出是否成功一律不计入, 宁可多提醒也不哑火) 仍为 0,
        且此前从未注入过 → 返回 True 并把 injected 永久置位 (最多触发一次, 不重复
        注入、不死锁; 调用方拿到 True 后应该"多给一轮机会"而不是硬阻断结束)。"""
        if self.exec_gate_injected:
            return False
        if tool_call_iters >= min_tool_iters and self.exec_gate_write_calls == 0:
            self.exec_gate_injected = True
            return True
        return False

    def exec_gate_task_done_needs_warning(self, iter_has_write: bool) -> bool:
        """`task_done` 所在这一批工具调用**全部执行完**后调用一次 (不是 task_done 那一刻
        —— 同批次可能还有排在它后面、尚未执行完的写工具, 必须等全批跑完才知道成不成功)。
        `iter_has_write` 是"这一批(含 task_done 本身)是否含*成功*的写类工具调用", 与
        `exec_gate_write_calls`(此前各批累计的成功次数) 合起来才是"本回合全程"。返回
        True 表示应该在 task_done 的工具返回结果里附一句证据缺失提醒 (不拒绝执行, 纯提示)。"""
        return self.exec_gate_write_calls == 0 and not iter_has_write


@dataclass
class MiscTurnState:
    """[FIX]/[VALIDATE] 散项 -- 各自单字段、字段数不足以单独成域的 2 个跨阶段变量。
    `system_injected` 写 4 读 0 (AGENT_STREAM_REFACTOR M3 已用 AST 核实), 原样迁移
    不删, 判定依据见 docs/AGENT_STREAM_REFACTOR.md 迭代回执。"""
    system_injected: bool = False      # [FIX] 系统注入标记（RUN-TEST/BLOCK-TEST后跳过enforce）
    output_artifacts: list = field(default_factory=list)  # [VALIDATE] 已写的输出产物 (.docx/.pptx/.xlsx/.pdf/.csv/.html/.json)


@dataclass
class UsageCounters:
    """用量计数与计时 -- 跨全部迭代累计的 5 个跨阶段变量 (AGENT_STREAM_REFACTOR M4)。
    `t0` 用 default_factory 在实例化那一刻取时间戳, 等价于原代码 `t0 = time.time()`
    紧跟在 try 起手第一行, 必须最先构造 (早于其余域状态对象), 否则超时计时基准会漂移。"""
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_iterations: int = 0
    t0: float = field(default_factory=time.time)
    elapsed: float = 0.0               # 原局部变量首次赋值在循环内 (L499), 非 SETUP 起手
    # [USAGE-ANCHOR 2026-09-03] 上游回报的真实 usage 锚点。
    # 估算器 (est_tokens) 永远看不见 system prompt + 工具定义 (实测一句"hi"就 15278
    # prompt tokens, 全是固定开销), 且中文/reasoning 各有系统性偏差。
    # 锚点做法: 每次上游回报 usage 就记下"真实 prompt_tokens ↔ 当时估算值"这一对,
    # 之后的预算判断用 anchor + 增量估算, 而不是纯估算。绝对值估不准没关系,
    # 每次调用都用真值重新锚定, 只估增量。
    last_real_prompt_tokens: int = 0   # 上游最近一次回报的真实 prompt_tokens (0=还没有)
    last_est_at_anchor: int = 0        # 锚定那一刻我方估算的 prompt tokens
    usage_reports: int = 0             # 收到过几次真实 usage (0 时退回纯估算)


@dataclass
class TurnContext:
    """核心域 -- 本轮对话上下文 (AGENT_STREAM_REFACTOR M5a: sid_tag/tracer/new_msgs/base_history,
    M5b: messages/session_id).
    实例化必须在 `try:` 之前构造 (见 docs/AGENT_STREAM_REFACTOR.md 3.2.2 决定 2):
    finally 依赖 new_msgs/base_history/sid_tag/session_id, 若构造放 try 内且抛异常, finally 引用会
    UnboundLocalError。`session_id` 来自函数形参, 构造时须显式传入 (见 3.2.1 坑 3), 不能靠默认值起手。
    `_lock` 永久排除不迁 (加锁顺序语义, 见 3.2.2 决定 1)。"""
    sid_tag: str = "anon"
    tracer: Any = None          # L154 才创建 (依赖 make_tracer), 构造时给不了, 默认 None
    new_msgs: list = field(default_factory=list)
    base_history: list = field(default_factory=list)
    messages: list = field(default_factory=list)      # SETUP L340 才真正赋值, 构造时给不了
    session_id: Optional[str] = None                  # 形参型字段, 构造时显式传入 (非默认值起手)
